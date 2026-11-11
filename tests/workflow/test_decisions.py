# SPDX-License-Identifier: Apache-2.0
"""Durable decisions about suggestions (design 17.3; local contract 3 of 17.5; AT-007): a dismissal survives calls,
retries, restarts, paraphrase, new receipts and a new order version; a reoffer states one of the declared reasons;
a mandatory finding is never hidden; an unreadable store offers nothing."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from g6.harness.workflow import diagnose as dg
from g6.harness.workflow import obstacle as ob
from g6.harness.workflow import policy, view
from g6.harness.workflow import record as rec
from g6.harness.workflow import suggest as sg
from g6.harness.workflow.decisions import REASONS, Decisions
from tests.workflow.kit import OTHER_TASK, PROJECT, T0, TASK, at, event, sha, started

OUTCOME = ob.Outcome(sha("goal"), "add returns the sum", "attributed_label")
ONE = sg.Intervention("run_reproduction", "R1", "task")
NOW = T0 + timedelta(hours=1)


def _assessment(report="REPORT", integrity=False):
    return {"report": report, "reasons": [{"stage": "integrity", "code": "x", "detail": "d"}] if integrity else [],
            "requirements": [{"id": "R1", "status": "failed", "provenance": "stated",
                              "checks": [{"id": "tests/test_calc.py::test_add", "verdict": "failed", "provenance": "stated"}]}]}


def _shown(task=TASK, *, assessment=None, job="job-1", now=NOW, outcome=OUTCOME):
    events = [event("a" * 32, "run_started", data={"origin_kind": "loop_session", "origin_sha256": sha("a")})]
    diagnosis = dg.diagnose(events, assessment=assessment or _assessment(), outcome=outcome, job_id=job)
    found = sg.candidates(diagnosis, assessment or _assessment())
    shown, again = sg.ranked(found, Decisions(PROJECT), task=task, now=now, outcome_sha256=outcome.text_sha256)
    return [one.intervention.id for one in shown], list(again)


def _dismiss(scope="task", **more):
    return Decisions(PROJECT).record(ONE.id, "dismissed", task=TASK, scope=scope, by="A person", at=at(0), **more)


def test_decision_is_on_disk_before_the_reply(home):
    ident = _dismiss()
    files = sorted(p.name for p in (home / "workflow" / PROJECT / "tasks" / TASK).glob("decisions.*") if not p.name.endswith(".lock"))
    assert files == ["decisions.head", "decisions.jsonl"] and len(ident) == 64
    (body,) = Decisions(PROJECT).held(task=TASK)                          # a fresh object: it persisted
    assert body["decision"] == "dismissed" and body["intervention"] == ONE.id and body["policy_version"] == policy.POLICY_VERSION
    assert Decisions(PROJECT).standing(ONE.id, task=TASK, now=NOW, outcome_sha256="") == (
        "suppressed", "you dismissed it for this task")


@pytest.mark.parametrize("case", ["retry", "restart", "paraphrase", "new receipt", "order version"])
def test_dismissal_survives_retry_restart_paraphrase_new_receipt_and_order_version_change(home, monkeypatch, case):
    assert ONE.id in _shown()[0]
    _dismiss()
    if case == "paraphrase":
        monkeypatch.setitem(sg.WORDS, "run_reproduction", "reproduce the failure with the stated check")
    if case == "order version":
        monkeypatch.setattr(sg, "ORDER_VERSION", "suggestion-order.v2")
    assessment = _assessment(report="A NEW REPORT") if case == "new receipt" else None
    job = "job-2" if case in ("new receipt", "retry") else "job-1"
    for _ in range(2 if case == "retry" else 1):
        shown, again = _shown(assessment=assessment, job=job)
        assert ONE.id not in shown and again == []
    assert shown                                                          # the other suggestions are still offered


def test_task_dismissal_does_not_suppress_in_another_task(home):
    _dismiss()
    assert ONE.id not in _shown(TASK)[0] and ONE.id in _shown(OTHER_TASK)[0]


def test_project_dismissal_survives_a_reworded_task(home):
    _dismiss(scope="project")
    reworded = sha("the same task, reworded between sessions")           # A's identity makes it another task
    for task in (TASK, OTHER_TASK, reworded):
        assert ONE.id not in _shown(task)[0]
    assert not (home / "workflow" / PROJECT / "tasks" / reworded).exists()     # reading wrote nothing


def test_reopen_states_its_reason(home):
    _dismiss()
    Decisions(PROJECT).record(ONE.id, "reopened", task=TASK, by="A person", at=at(60))
    shown, again = _shown()
    assert ONE.id in shown and again == [{"intervention_id": ONE.id, "reason": REASONS["reopened"]}]
    rows = [one.to_dict() for one in sg.order([sg.Suggestion(ONE, 1, "r", "unknown", rec.Ref("review", "j", sha("r")))])]
    assert "shown again because you reopened it" in sg.lines(rows, again, 3)[0]
    Decisions(PROJECT).record(ONE.id, "dismissed", task=TASK, by="A person", at=at(120))        # dismissed once more
    assert ONE.id not in _shown()[0]


def test_person_chosen_expiry_reoffers_after_it_and_not_before(home):
    _dismiss(expires_at=at(days=7))
    assert ONE.id not in _shown(now=T0 + timedelta(days=6, hours=23))[0]
    shown, again = _shown(now=T0 + timedelta(days=7, seconds=1))
    assert ONE.id in shown and again[0]["reason"] == REASONS["expired"]
    with pytest.raises(rec.EventRefused):
        _dismiss(expires_at=at(-5))                                       # an expiry before the decision


def test_a_declared_rule_reopens_and_an_undeclared_change_does_not(home):
    _dismiss(reopen_rule="outcome_superseded", outcome_sha256=OUTCOME.text_sha256)
    assert ONE.id not in _shown()[0]
    replaced = ob.Outcome(sha("another goal"), "add never overflows", "person_confirmed")
    shown, again = _shown(outcome=replaced)
    assert ONE.id in shown and again[0]["reason"] == REASONS["outcome_superseded"]
    other = sg.Intervention("rerun_at_base", "R1", "task")
    Decisions(PROJECT).record(other.id, "dismissed", task=TASK, by="A person", at=at(0))          # no rule declared
    assert other.id not in _shown(outcome=replaced)[0]
    days = policy.RETENTION_DAYS["decisions"]
    assert other.id not in _shown(now=T0 + timedelta(days=days - 1))[0]
    late, again = _shown(now=T0 + timedelta(days=days + 1))
    assert other.id in late and {row["reason"] for row in again} == {REASONS["record_expired"]}


def test_new_mandatory_finding_is_still_shown_after_a_dismissal(home, monkeypatch):
    _dismiss(scope="project")
    Decisions(PROJECT).record(sg.Intervention("rerun_at_base", "R1", "task").id, "dismissed", task=TASK, scope="project",
                              by="A person", at=at(0))
    monkeypatch.setattr(view, "ADMITTED_LEVEL", "suggestions")
    monkeypatch.setattr(view, "shared", lambda project, provider: True)
    report = "# Review\n- **Integrity finding:** the patch changed tests/test_calc.py."
    built = view.build(PROJECT, TASK, assessment=_assessment(report, integrity=True), intent={"goal": "add returns the sum"},
                       provider="openai:codex", job_id="job-9")
    assert built["suggestions"] == [] or all(s["intervention_id"] != ONE.id for s in built["suggestions"])
    assert [o["kind"] for o in built["obstacles"]] == ["integrity_block", "failing_accepted_check"]    # still findings
    result = view.with_block(report, built)
    assert result.startswith(report) and "integrity problem" in result
    assert not any(kind in sg.KINDS for kind in ("integrity_block", "finding"))                        # no id to dismiss


def test_unreadable_decision_store_offers_no_suggestion(home, monkeypatch):
    _dismiss()
    path = home / "workflow" / PROJECT / "tasks" / TASK / "decisions.jsonl"
    path.write_bytes(path.read_bytes()[:-25])
    with pytest.raises(rec.RecordUnreadable):
        Decisions(PROJECT).standing(ONE.id, task=TASK, now=NOW, outcome_sha256="")
    with pytest.raises(rec.RecordUnreadable):
        _shown()
    monkeypatch.setattr(view, "ADMITTED_LEVEL", "suggestions")
    monkeypatch.setattr(view, "shared", lambda project, provider: True)
    built = view.build(PROJECT, TASK, assessment=_assessment(), intent={"goal": "g"}, provider="openai:codex")
    assert built["suggestions"] == [] and "cannot be read" in built["suggestions_note"]              # fail closed
    assert view.with_block("REPORT", built).startswith("REPORT\n\n" + view.HEADING)                   # the report is unaffected
    assert "- **Suggestions:** none." in view.block(built)


def test_truncated_store_is_unreadable_against_its_head(home):
    _dismiss()
    Decisions(PROJECT).record(ONE.id, "reopened", task=TASK, by="A person", at=at(10))
    folder = home / "workflow" / PROJECT / "tasks" / TASK
    lines = (folder / "decisions.jsonl").read_bytes().splitlines(keepends=True)
    (folder / "decisions.jsonl").write_bytes(lines[0])                    # a valid earlier prefix: the reopening is cut off
    with pytest.raises(rec.RecordUnreadable):
        Decisions(PROJECT).held(task=TASK)
    (folder / "decisions.jsonl").write_bytes(b"")                         # emptied: never read as "nothing dismissed"
    with pytest.raises(rec.RecordUnreadable):
        Decisions(PROJECT).standing(ONE.id, task=TASK, now=NOW, outcome_sha256="")
    (folder / "decisions.jsonl").unlink()
    with pytest.raises(rec.RecordUnreadable):
        Decisions(PROJECT).held(task=TASK)                                # a head with no store


def test_two_writers_record_one_decision_each_in_order(home):
    def write(number):
        return Decisions(PROJECT).record(sg.Intervention("run_reproduction", f"R{number}", "task").id, "dismissed",
                                         task=TASK, by="A person", at=at(number))
    with ThreadPoolExecutor(6) as pool:
        idents = list(pool.map(write, range(12)))
    held = Decisions(PROJECT).held(task=TASK)
    assert len(set(idents)) == 12 == len(held) and [body["at"] for body in held] == sorted(body["at"] for body in held)


def test_a_decision_copied_from_another_project_or_task_is_refused(home):
    _dismiss()
    source = home / "workflow" / PROJECT / "tasks" / TASK
    for target in (home / "workflow" / PROJECT / "tasks" / OTHER_TASK, home / "workflow" / sha("another project") / "tasks" / TASK):
        target.mkdir(parents=True)
        for name in ("decisions.jsonl", "decisions.head"):
            (target / name).write_bytes((source / name).read_bytes())
    with pytest.raises(rec.RecordUnreadable):
        Decisions(PROJECT).held(task=OTHER_TASK)
    with pytest.raises(rec.RecordUnreadable):
        Decisions(sha("another project")).held(task=TASK)
    for bad in ({"decision": "ignored"}, {"scope": "world"}, {"by": " "}, {"reopen_rule": "whenever"}):
        with pytest.raises(rec.EventRefused):
            Decisions(PROJECT).record(ONE.id, **{"decision": "dismissed", "task": TASK, "by": "P", "at": at(0), **bad})


def test_eligible_and_displayed_are_recorded_apart_and_a_dismissed_one_is_not_displayed(home, monkeypatch):
    record = rec.ProjectRecord(PROJECT)
    started(record)
    monkeypatch.setattr(view, "ADMITTED_LEVEL", "suggestions")
    monkeypatch.setattr(view, "shared", lambda project, provider: True)
    _dismiss()
    built = view.build(PROJECT, TASK, assessment=_assessment(), intent={"goal": "add returns the sum"},
                       provider="openai:codex", job_id="job-1")
    kinds = [(e.kind, e.data["kind"]) for e in record.events(task=TASK) if e.kind.startswith("suggestion_")]
    assert ("suggestion_eligible", "run_reproduction") in kinds and ("suggestion_displayed", "run_reproduction") not in kinds
    assert ("suggestion_displayed", "rerun_at_base") in kinds
    assert [s["kind"] for s in built["suggestions"]] == ["rerun_at_base"]
    block = view.block(built)
    assert len(block) <= view.MAX_LINES and not any("A next check you may run" in line for line in block)
    assert any(line.startswith("- **You may (a cheap, reversible check") and "burden unknown" in line for line in block)
