"""Regressions for complete registration metering and delayed publication."""
from dataclasses import replace

import pytest

from g6.distill import acceptance_gate as g
from g6.distill import records as r
from g6.distill.ledger import Stores
from g6.distill.library import Library
from . import test_acceptance_repair as repair
from . import test_acceptance_repair2 as repair2
from .test_replacement_gate import replacement_sample


@pytest.fixture
def sample():
    return repair.sample.__wrapped__()


def test_add_load_failure_before_reservation_is_charged_on_replacement_retry(sample, monkeypatch):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    calls = [0]
    def load(*args):
        calls[0] += 1
        if calls[0] == 1:
            clock[0] += 20000.
            raise ValueError("add load failed before reservation")
        return frozen
    monkeypatch.setattr(g, "_load", load)
    assert g.decide(candidate, Library(), frozen.registration_sha256).verdict == "block"
    answer = g.decide_replacement(candidate, Library(), frozen.registration_sha256)
    assert answer.verdict == "refuse", answer.reasons
    assert "cumulative cost" in " ".join(answer.reasons)
    assert g._validation_total(frozen.registration_sha256, None) >= 20000.


def test_assessment_completion_append_is_charged(sample, monkeypatch, tmp_path):
    candidate, frozen, clock = repair2.persisted_rig(sample, monkeypatch, tmp_path)
    append = g._append
    def delayed(journal, kind, body):
        result = append(journal, kind, body)
        if kind == "replacement_attempt":
            clock[0] += 20000.
        return result
    monkeypatch.setattr(g, "_append", delayed)
    answer = g.decide_replacement(candidate, Library(), frozen.registration_sha256)
    assert answer.verdict in {"refuse", "block"}, answer.reasons
    assert not g._active(Library().list())
    assert g._validation_total(frozen.registration_sha256, None) >= 20000.


def test_guarded_serving_cost_uses_whole_execution(sample, monkeypatch):
    contract = r.MethodContract(**(sample[1].contract.model_dump(exclude={"content_id"}) |
        {"cost_bound": {"wall_s": 60, "probe_runs": 100, "model_calls": 0}}))
    candidate = r.VerifierCandidate(**(sample[1].model_dump(exclude={"candidate_id"}) | {"contract": contract}))
    sample = (sample[0], candidate, sample[2], sample[3])
    candidate, frozen = replacement_sample(sample, monkeypatch, task_saving_bounds=(-30., 12.))
    acceptance = frozen.acceptance[:30] + frozen.acceptance[300:330]
    monitor = frozen.monitor[:30] + frozen.monitor[300:330]
    rows = [row.model_copy(update={"teacher_verdict": "no_concern" if row.teacher_verdict == "concern"
                                  else "concern"}) for row in frozen.replacement.rows]
    frozen = replace(frozen, acceptance=acceptance, monitor=monitor,
                     replacement=frozen.replacement.model_copy(update={"rows": rows}))
    clock = [0.]
    monkeypatch.setattr(g.time, "monotonic", lambda: clock[0])
    scope = g._case_in_scope
    def guard(*args):
        clock[0] += 20.
        return scope(*args)
    monkeypatch.setattr(g, "_case_in_scope", guard)
    cost = g._replacement_cost
    def cost_with_guard(fixed, savings, **kwargs):
        assert all(value.value < -10. for value in savings)
        return cost(fixed, savings, **kwargs)
    monkeypatch.setattr(g, "_replacement_cost", cost_with_guard)
    with pytest.raises(ValueError, match="cumulative cost"):
        g._assess_replacement(candidate, [], frozen, 1)


def test_delayed_rename_never_exposes_authority_to_readers(sample, monkeypatch, tmp_path):
    candidate, frozen, clock = repair2.persisted_rig(sample, monkeypatch, tmp_path)
    rename = g.os.replace
    observed = []
    def delayed(*args):
        rename(*args)
        clock[0] += 20000.
        entries = Library().list()
        observed.extend(entries)
        assert not g._active(entries)
        assert all(e.authority == "A0" and e.replacement == "none" for e in entries)
        assert Library().by_status("probation") == []
        assert Library().by_status("admitted") == []
        assert all(e.authority == "A0" for e in Stores().for_model(r.LibraryEntry).list())
    monkeypatch.setattr(g.os, "replace", delayed)
    answer = g.decide_replacement(candidate, Library(), frozen.registration_sha256)
    assert observed
    assert answer.verdict in {"refuse", "block"}, answer.reasons
    assert not g._active(Library().list())


def test_n1_generated_helper_does_not_capture_free_call():
    before = ("def helper(): return 2\ndef test_a():\n"
              " if answer(1)==0:\n  assert helper_0()==2\n assert helper()==2\n")
    after = before.replace("def helper()", "def helper_0()").replace("assert helper()", "assert helper_0()")
    assert not g._normal_equivalent(before, after)


def test_one_outer_meter_covers_load_and_pending_commit(sample, monkeypatch, tmp_path):
    candidate, frozen, clock = repair2.persisted_rig(sample, monkeypatch, tmp_path)
    load = g._load
    def timed_load(*args):
        clock[0] += 2.
        return load(*args)
    monkeypatch.setattr(g, "_load", timed_load)
    rename = g.os.replace
    def timed_rename(*args):
        clock[0] += 3.
        return rename(*args)
    monkeypatch.setattr(g.os, "replace", timed_rename)
    answer = g.decide_replacement(candidate, Library(), frozen.registration_sha256)
    assert answer.verdict == "admit_probation", answer.reasons
    rows = g._journal().entries()
    assert sum(row["kind"] == "replacement_attempt_start" for row in rows) == 1
    assert g._validation_total(frozen.registration_sha256, None) >= 5.
    entry = Library().get(answer.entry_id)
    assert entry.replacement == "R" and entry.authority == "A1"
    receipts = [row["body"] for row in g._journal().entries() if row["kind"] == "replacement_state"]
    assert receipts[-1]["state"] == "active"
    assert g._replacement_authorized(entry)


def test_add_unexpected_load_exception_closes_outer_meter(sample, monkeypatch):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    def load(*args):
        clock[0] += 7.
        raise RuntimeError("interrupted add load")
    monkeypatch.setattr(g, "_load", load)
    with pytest.raises(RuntimeError, match="interrupted add"):
        g.decide(candidate, Library(), frozen.registration_sha256)
    assert g._validation_total(frozen.registration_sha256, None) >= 7.

@pytest.mark.parametrize("delay", ["completion", "rename"])
def test_post_commit_cost_refuses_before_activation(sample, monkeypatch, delay):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    if delay == "completion":
        append = g._append
        def delayed(journal, kind, body):
            result = append(journal, kind, body)
            if kind == "replacement_attempt":
                clock[0] += 20000.
            return result
        monkeypatch.setattr(g, "_append", delayed)
    else:
        def publish(*args):
            clock[0] += 20000.
        monkeypatch.setattr(g, "_publish_replacement", publish)
    answer = g.decide_replacement(candidate, Library(), frozen.registration_sha256)
    assert answer.verdict in {"refuse", "block"}, answer.reasons
    assert g._validation_total(frozen.registration_sha256, None) >= 20000.


def test_failed_seal_append_requires_recovery_even_after_row_was_written(sample, monkeypatch):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    append = g._append
    def interrupted(journal, kind, body):
        result = append(journal, kind, body)
        if kind == "registration_meter_closed":
            clock[0] += 20000.
            raise ValueError("seal failed after writing")
        return result
    monkeypatch.setattr(g, "_append", interrupted)
    assert g.decide_replacement(candidate, Library(), frozen.registration_sha256).verdict == "block"
    with pytest.raises(ValueError, match="cost recovery"):
        g._validation_total(frozen.registration_sha256, None)


def test_replacement_batch_refuses_until_joint_activation_is_supported(sample, monkeypatch):
    candidate, frozen, _ = repair.meter_rig(sample, monkeypatch)
    contract = r.MethodContract(**(candidate.contract.model_dump(exclude={"content_id"}) |
                                  {"assumptions": ["distinct proposal"]}))
    other = r.VerifierCandidate(**(candidate.model_dump(exclude={"candidate_id"}) | {"contract": contract}))
    answers = g.decide_replacement_batch((candidate, other), Library(), frozen.registration_sha256)
    assert all(answer.verdict == "block" for answer in answers)
    assert all("joint activation" in " ".join(answer.reasons) for answer in answers)


def test_pending_receipt_blocks_direct_gate_readers(sample, monkeypatch):
    from types import SimpleNamespace
    candidate, frozen, _ = repair.meter_rig(sample, monkeypatch)
    entry = SimpleNamespace(entry_id="f" * 64, candidate_id=candidate.candidate_id,
        noninferiority="e" * 64, replacement="R", replacement_scope=[], authority="A1", status="probation")
    assert g._active([entry]) == []
    verdicts, costs = g._measure_entry(entry, frozen.acceptance[:1], [entry])
    assert verdicts == ["abstain"]
    assert costs == [0., 0., 0.]
    monkeypatch.setattr(Library, "get", lambda self, ident: entry)
    with pytest.raises(ValueError, match="pending"):
        g._snapshot(Library(), entry.entry_id)


def test_activation_flip_overrun_stays_pending_for_readers(sample, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    locked = [False]
    @contextmanager
    def writer():
        locked[0] = True
        try:
            yield {"id": "writer-lease", "retain": False}
        finally:
            locked[0] = False
    monkeypatch.setattr(g, "_writer", writer)
    class Lock:
        def exists(self):
            return locked[0]

        def read_text(self, **kwargs):
            return "123\nwriter-lease\n"
    monkeypatch.setattr(g, "state_path", lambda *args: Lock())
    entry = None
    def persist(c, answer, evidence, own, frozen, data, library, **kwargs):
        nonlocal entry
        entry = SimpleNamespace(entry_id="f" * 64, candidate_id=candidate.candidate_id,
                                noninferiority=g.digest(data["replacement"]))
        return replace(answer, entry_id=entry.entry_id)
    monkeypatch.setattr(g, "_persist", persist)
    append = g._append
    def delayed(journal, kind, body):
        result = append(journal, kind, body)
        if kind == "replacement_state" and body["state"] == "active":
            clock[0] += 20000.
            assert not g._replacement_authorized(entry)
        return result
    monkeypatch.setattr(g, "_append", delayed)
    answer = g.decide_replacement(candidate, Library(), frozen.registration_sha256)
    assert answer.verdict == "refuse", answer.reasons
    assert not g._replacement_authorized(entry)
    with pytest.raises(ValueError, match="cost recovery"):
        g._validation_total(frozen.registration_sha256, None)


def test_missing_reader_issuance_cannot_activate(sample, monkeypatch):
    candidate, frozen, _ = repair.meter_rig(sample, monkeypatch)
    monkeypatch.delattr(Library, "replacement_state_checked", raising=False)
    answer = g.decide_replacement(candidate, Library(), frozen.registration_sha256)
    assert answer.verdict == "refuse"
    assert "reader issuance" in " ".join(answer.reasons)
    assert not any(row["kind"] == "replacement_state" and row["body"]["state"] == "active"
                   for row in g._journal().entries())


def test_legacy_closed_meter_without_recovery_blocks_new_admission(sample, monkeypatch):
    candidate, frozen, _ = repair.meter_rig(sample, monkeypatch)
    body = {"registration": frozen.registration_sha256, "candidate_id": candidate.candidate_id,
            "phase": "assessment"}
    meter = g._append(g._journal(), "replacement_attempt_start", body)
    g._append(g._journal(), "replacement_attempt", body | {"attempt": meter, "validation_s": 1.})
    answer = g.decide_replacement(candidate, Library(), frozen.registration_sha256)
    assert answer.verdict == "refuse", answer.reasons
    assert "cost recovery" in " ".join(answer.reasons)


def active_entry(sample, monkeypatch):
    from types import SimpleNamespace
    candidate, frozen, _ = repair.meter_rig(sample, monkeypatch)
    monkeypatch.setattr(g, "_persist", lambda c, answer, *args, **kwargs: replace(answer, entry_id="f" * 64))
    assert g.decide_replacement(candidate, Library(), frozen.registration_sha256).verdict == "admit_probation"
    receipt = [row["body"] for row in g._journal().entries() if row["kind"] == "replacement_state"][-1]
    entry = SimpleNamespace(entry_id="f" * 64, candidate_id=candidate.candidate_id,
        noninferiority=receipt["pending_receipt"], replacement="R", replacement_scope=[],
        teacher=receipt["report"]["replacement"]["teacher"], authority="A1", status="probation")
    return entry


def test_active_receipt_survives_authority_only_version_change(sample, monkeypatch):
    entry = active_entry(sample, monkeypatch)
    entry.entry_id = "9" * 64
    entry.authority, entry.status = "A2", "admitted"
    assert g._replacement_authorized(entry)


@pytest.mark.parametrize("changed", ["teacher", "scope"])
def test_activation_receipt_cannot_authorize_different_teacher_or_scope(sample, monkeypatch, changed):
    entry = active_entry(sample, monkeypatch)
    if changed == "teacher":
        entry.teacher = "9" * 64
    else:
        entry.replacement_scope = [r.FeaturePredicate(feature="assertions", op="eq", value=0)]
    assert not g._replacement_authorized(entry)


def test_closed_total_reserves_activation_tail_for_the_next_attempt(sample, monkeypatch):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    frozen = replace(frozen, replacement=frozen.replacement.model_copy(update={"activation_allowance_s": 3.}))
    monkeypatch.setattr(g, "_load", lambda *args: frozen)
    append = g._append
    def delayed(journal, kind, body):
        result = append(journal, kind, body)
        if kind == "replacement_state" and body["state"] == "active":
            clock[0] += 2.
        return result
    monkeypatch.setattr(g, "_append", delayed)
    answer = g.decide_replacement(candidate, Library(), frozen.registration_sha256)
    assert answer.verdict == "admit_probation", answer.reasons
    assert g._validation_total(frozen.registration_sha256, None) >= 2.


def test_slow_refusal_tail_cannot_be_forgotten_by_retry(sample, monkeypatch):
    candidate, frozen, clock = repair.meter_rig(sample, monkeypatch)
    append = g._append
    def delayed(journal, kind, body):
        result = append(journal, kind, body)
        if kind == "replacement_attempt" or (kind == "replacement_state" and body["state"] == "pending"):
            clock[0] += 20000.
        return result
    monkeypatch.setattr(g, "_append", delayed)
    assert g.decide_replacement(candidate, Library(), frozen.registration_sha256).verdict == "refuse"
    with pytest.raises(ValueError, match="cost recovery"):
        g._validation_total(frozen.registration_sha256, None)
