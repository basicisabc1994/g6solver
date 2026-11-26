# SPDX-License-Identifier: Apache-2.0
"""Intake consumes supplied source records; no source reader invokes a tool."""
import importlib
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from g6.core.ledger import LedgerError, digest
from g6.distill.ledger import Stores
from g6.distill.records import FailureRecord


@pytest.fixture
def local_home(monkeypatch):
    # Ordinary directories in the platform temp folder; no ACLs, links or tmp_path mode changes.
    home = Path(tempfile.gettempdir()) / f"rectify-{uuid4().hex}"
    home.mkdir()
    monkeypatch.setenv("G6_HOME", str(home))
    return home


def context(event="event-1", **changes):
    module = importlib.import_module("g6.distill.intake")
    return module.EventContext(**(dict(event_id=event, family="g6_self", loop="loop-a", round=1,
        finding_class="boundary_case", g6_commit="abc", observed_at="2026-11-18T00:00:00Z") | changes))


def event(intake, source, ctx):
    if source == "refusal":
        return intake.refusal({"ok": False, "error": "[G6_E_RESOURCE_LIMIT] limit"}, ctx, well_formed=True)
    if source == "crash":
        return intake.crash({"job_id": ctx.event_id, "status": "failed", "ok": False,
                             "error": "[G6_E_JOB_FAILED] RuntimeError: broken"}, ctx)
    if source == "k_loop":
        return intake.k_finding({"folder": "review-rp1", "finding": 1, "branch": "loop-a", "round": 1,
                                 "rule": "bounds", "test": "tests/test_bounds.py"}, ctx)
    if source == "ci":
        return intake.ci({"id": 17, "status": "completed", "conclusion": "failure", "head_sha": "abc"},
                         ctx, branch="main")
    if source == "discrepancy":
        from g6.self_model.model import Field
        from g6.self_model.monitor import Expectation, Observation, compare
        def field(value):
            return Field.measured(value, source="ci", observed_at=10., ttl_s=100.)
        row, = compare(Observation(observation_id=ctx.event_id, signal="ci", category="evaluate",
                                  measurement=field(5)),
                       Expectation(signal="ci", lower=field(0), upper=field(1)), now=10.)
        return intake.discrepancy(row, ctx)
    raise AssertionError(source)


@pytest.mark.parametrize(("source", "kind"), [("refusal", "g6_refusal"), ("crash", "g6_crash"),
    ("k_loop", "missed_defect"), ("ci", "regression"), ("discrepancy", "self_model_discrepancy")])
def test_each_source_is_deduplicated_across_restart(local_home, source, kind):
    module = importlib.import_module("g6.distill.intake")
    first = event(module.FailureIntake(), source, context())
    again = event(module.FailureIntake(), source, context())
    assert first == again and first.kind == kind
    assert first.observed_by == "self"  # independence is never inferred from a tool reply
    assert first.evidence and first.split == "monitor"
    assert Stores().for_model(FailureRecord).list() == [first]


def test_same_round_is_not_earlier_and_other_loops_are(local_home):
    module = importlib.import_module("g6.distill.intake")
    intake = module.FailureIntake()
    a = event(intake, "refusal", context("a"))
    peer = event(intake, "refusal", context("peer"))
    b = event(intake, "refusal", context("b", round=2))
    c = event(intake, "refusal", context("c", loop="loop-b"))
    assert a.met_before == peer.met_before == []
    assert b.met_before == sorted([a.record_id, peer.record_id])
    assert c.met_before == sorted([a.record_id, peer.record_id, b.record_id])


def test_same_event_with_different_content_or_label_is_refused(local_home):
    module = importlib.import_module("g6.distill.intake")
    intake = module.FailureIntake()
    event(intake, "refusal", context())
    with pytest.raises(LedgerError, match="conflict"):
        intake.refusal({"ok": False, "error": "[G6_E_RESOURCE_LIMIT] different"}, context(), well_formed=True)
    with pytest.raises(LedgerError, match="conflict"):
        event(intake, "refusal", context(finding_class="state_not_durable"))
    assert len(Stores().for_model(FailureRecord).list()) == 1


def test_nonfailures_and_malformed_source_records_do_not_become_failures(local_home):
    intake = importlib.import_module("g6.distill.intake").FailureIntake()
    assert intake.refusal({"ok": True}, context(), well_formed=True) is None
    assert intake.refusal({"ok": False, "error": "[G6_E_INPUT_VALIDATION] bad"}, context(),
                          well_formed=False) is None
    assert intake.crash({"job_id": "j", "status": "pending"}, context()) is None
    assert intake.ci({"status": "completed", "conclusion": "success"}, context(), branch="main") is None
    assert intake.ci({"status": "completed", "conclusion": "failure"}, context(), branch="feature") is None
    with pytest.raises(ValueError):
        intake.refusal({"ok": False, "error": "unrecognised"}, context(), well_formed=True)
    with pytest.raises(ValueError):
        intake.k_finding({"rule": "not reproduced"}, context())
    assert Stores().for_model(FailureRecord).list() == []


def test_named_other_classes_remain_distinct(local_home):
    intake = importlib.import_module("g6.distill.intake").FailureIntake()
    with pytest.raises(ValueError):
        context(finding_class="other")
    a = event(intake, "refusal", context("a", finding_class="other", other="lost counter"))
    b = event(intake, "refusal", context("b", finding_class="other", other="lost clock", round=2))
    c = event(intake, "refusal", context("c", finding_class="other", other="lost counter", round=2))
    assert b.met_before == [] and c.met_before == [a.record_id]


def test_existing_failure_record_is_replayed_once(local_home):
    intake = importlib.import_module("g6.distill.intake").FailureIntake()
    raw = FailureRecord(kind="prediction_failed", family="g6_self", source="self_repair", observed_by="self",
        case_ref=digest("case"), split="monitor", evidence=[], finding_class="boundary_case", met_before=[],
        g6_commit="abc", observed_at="2026-11-18", provenance={"receipt": "repair"})
    first = intake.record(raw, context())
    assert intake.record(raw, context()) == first
    assert first.kind == "prediction_failed" and len(Stores().for_model(FailureRecord).list()) == 1


@pytest.mark.parametrize("source", ["crash", "ci", "discrepancy", "k_loop"])
def test_native_source_identity_cannot_be_duplicated_with_a_new_poll_id(local_home, source):
    module = importlib.import_module("g6.distill.intake")
    intake = module.FailureIntake()
    ctx = context()
    if source in {"crash", "discrepancy"}:
        # Hold producer id fixed while changing only intake context.
        if source == "crash":
            raw = dict(job_id="j1", status="failed", error="RuntimeError: bad")
            def call(c):
                return intake.crash(raw, c)
        else:
            from g6.self_model.model import Field
            from g6.self_model.monitor import FailureRecord as MonitorFailure
            raw = MonitorFailure(case_id="observation-1", cause="self_model_discrepancy",
                kind="self_model_discrepancy", category="evaluate", note="bad", observed_at=10,
                evidence=(Field.measured(5, source="ci", observed_at=10, ttl_s=20),), case_ref=digest("obs"))
            def call(c):
                return intake.discrepancy(raw, c)
    else:
        def call(c):
            return event(intake, source, c)
    call(ctx)
    with pytest.raises(LedgerError, match="conflict"):
        call(context("different-poll"))
    assert len(Stores().for_model(FailureRecord).list()) == 1


def test_failed_job_reply_is_one_crash_even_when_seen_at_both_readers(local_home):
    intake = importlib.import_module("g6.distill.intake").FailureIntake()
    raw = dict(job_id="failed-job", status="failed", ok=False, error="[G6_E_JOB_FAILED] RuntimeError: bad")
    first = intake.refusal(raw, context(), well_formed=True)
    second = intake.crash(raw, context())
    assert first == second and first.kind == "g6_crash"
    assert len(Stores().for_model(FailureRecord).list()) == 1
