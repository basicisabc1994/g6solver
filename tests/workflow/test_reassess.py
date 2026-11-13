# SPDX-License-Identifier: Apache-2.0
"""Predictions before acting, and reassessment after (design 17.2; AT-008 and AT-009 in part): what each explanation
expects is on record before the check runs; a contradicted hypothesis is retired or revised; a result that does not
tell the explanations apart leaves it unresolved; and a removed obstacle claims nothing more than that."""
from __future__ import annotations

import pytest

from g6.harness.workflow import diagnose as dg
from g6.harness.workflow import obstacle as ob
from g6.harness.workflow import record as rec
from g6.harness.workflow import view
from tests.workflow.kit import PROJECT, TASK, at, event, sha, started

OUTCOME = ob.Outcome(sha("goal"), "add returns the sum", "attributed_label")
CHECK = "tests/test_calc.py::test_add"


def _assessment(status="failed", verdict="failed"):
    return {"report": "REPORT", "reasons": [], "requirements": [
        {"id": "R1", "status": status, "provenance": "stated",
         "checks": [{"id": CHECK, "verdict": verdict, "provenance": "stated"}]}]}


@pytest.fixture
def held(home):
    record = rec.ProjectRecord(PROJECT)
    run = started(record)
    (hypothesis,) = dg.diagnose(record.events(task=TASK), assessment=_assessment(), outcome=OUTCOME).hypotheses
    return record, run, hypothesis


def _observed(run, result, seconds=100, observable="accepted_check_at_base_commit"):
    return event(run, "check_observed", f"{observable}:{result}:{seconds}", seconds=seconds,
                 data={"observable": observable, "result": result})


def test_predictions_are_recorded_before_the_check_runs(held):
    record, run, hypothesis = held
    assert dg.record_predictions(record, TASK, hypothesis, at=at(10)) is True
    assert dg.record_predictions(record, TASK, hypothesis, at=at(11)) is False          # once; a repeat writes nothing
    predictions = [e for e in record.events(run=run) if e.kind == "prediction"]
    assert [e.data for e in predictions] == [
        {"hypothesis": hypothesis.id, "explanation": "patch_wrong_for_case", "rule": "accepted_check_blocks",
         "observable": "accepted_check_at_base_commit", "expected": "passes"},
        {"hypothesis": hypothesis.id, "explanation": "check_stale_or_wrong", "rule": "accepted_check_blocks",
         "observable": "accepted_check_at_base_commit", "expected": "fails"}]
    assert {e.provenance for e in predictions} == {"rule_derived"}                    # never a model's inference
    record.append(_observed(run, "passes", seconds=100))
    kinds = [e.kind for e in record.events(run=run)]
    assert kinds.index("check_observed") > max(i for i, kind in enumerate(kinds) if kind == "prediction")
    assert all(e.at < at(100) for e in predictions)
    # an observation from before the predictions were recorded decides nothing
    early = _observed(run, "passes", seconds=5)
    assert dg.reassess(record, TASK, hypothesis.id, [early], at=at(120)) == "unresolved"


def test_a_confirming_result_retains_the_hypothesis_as_a_hypothesis(held):
    record, run, hypothesis = held
    dg.record_predictions(record, TASK, hypothesis, at=at(10))
    assert dg.reassess(record, TASK, hypothesis.id, [_observed(run, "passes")], at=at(200)) == "retained"
    last = record.events(run=run)[-1]
    assert last.kind == "hypothesis_reassessed" and last.data == {"hypothesis": hypothesis.id, "result": "retained",
                                                                  "standing": "patch_wrong_for_case"}
    assert last.provenance == "rule_derived"


def test_contradicted_prediction_retires_the_hypothesis(held):
    record, run, hypothesis = held
    dg.record_predictions(record, TASK, hypothesis, at=at(10))
    # the check fails at the base commit too: the rule's own explanation is contradicted, the other one fits
    assert dg.reassess(record, TASK, hypothesis.id, [_observed(run, "fails")], at=at(200)) == "revised"
    assert record.events(run=run)[-1].data["standing"] == "check_stale_or_wrong"
    # a result neither explanation predicted retires it
    assert dg.reassess(record, TASK, hypothesis.id, [_observed(run, "could_not_run")], at=at(300)) == "retired"
    assert "standing" not in record.events(run=run)[-1].data
    assert set(dg.RESULTS) == {"retained", "revised", "retired", "unresolved"}


def test_indistinct_result_leaves_it_unresolved(held):
    record, run, hypothesis = held
    assert dg.reassess(record, TASK, hypothesis.id, [_observed(run, "passes")], at=at(50)) == "unresolved"   # nothing predicted
    dg.record_predictions(record, TASK, hypothesis, at=at(60))
    assert dg.reassess(record, TASK, hypothesis.id, [], at=at(200)) == "unresolved"                       # nothing observed
    other = _observed(run, "passes", observable="some_other_check")
    assert dg.reassess(record, TASK, hypothesis.id, [other], at=at(210)) == "unresolved"                  # another check
    same = dg.Hypothesis(**{**hypothesis.__dict__, "id": "f" * 32, "explanations": tuple(
        dg.Explanation(e.id, e.code, "passes") for e in hypothesis.explanations)})
    dg.record_predictions(record, TASK, same, at=at(220))
    assert dg.reassess(record, TASK, same.id, [_observed(run, "passes", seconds=300)], at=at(310)) == "unresolved"


def test_removed_obstacle_does_not_claim_sole_constraint_or_delivery_gain(held, monkeypatch):
    record, run, hypothesis = held
    dg.record_predictions(record, TASK, hypothesis, at=at(10))
    dg.reassess(record, TASK, hypothesis.id, [_observed(run, "passes")], at=at(200))
    monkeypatch.setattr(view, "ADMITTED_LEVEL", "diagnosis")
    monkeypatch.setattr(view, "shared", lambda project, provider: True)
    fixed = _assessment(status="passed_on_stated_checks", verdict="passed")          # the check passes now
    built = view.build(PROJECT, TASK, assessment=fixed, intent={"goal": "add returns the sum"}, provider="openai:codex")
    assert built["obstacles"] == [] and built["diagnosis"]["hypotheses"] == []
    assert built["value_result"] == "not_measured"
    said = "\n".join(view.block(built)).lower()
    for claim in ("the only obstacle was", "sole", "delivery improved", "improved", "faster", "resolved the", "was the cause",
                  "the constraint", "saved", "now unblocked"):
        assert claim not in said, claim
    assert "observation is insufficient" in said and "value to your work:** not measured" in said


def test_changed_apparent_constraint_is_recorded(held):
    record, run, hypothesis = held
    before = dg.diagnose(record.events(task=TASK), assessment=_assessment(), outcome=OUTCOME)
    # after the change the check passes, and a question has now waited through two reviews
    for number, seconds in ((1, 300), (2, 400)):
        job = record.start_run(TASK, "review_job", f"job-{number}", baseline_sha256="", at=at(seconds))
        record.append(event(job, "review_finished", seconds=seconds + 5, data={"outcome": "insufficient_evidence"}))
    pending = event("d" * 32, "question_pending", "q1", seconds=250, refs=[rec.Ref("question", sha("q1"), sha("q1"))])
    fixed = _assessment(status="passed_on_stated_checks", verdict="passed")
    after = dg.diagnose([*record.events(task=TASK), pending], assessment=fixed, outcome=OUTCOME)
    assert [h.why_code for h in before.hypotheses] == ["accepted_check_blocks"]
    assert [h.why_code for h in after.hypotheses] == ["question_waits"]
    assert dg.record_change(record, TASK, before, after, at=at(500)) is True
    changed = [e for e in record.events(task=TASK) if e.kind == "apparent_constraint_changed"]
    assert len(changed) == 1 and changed[0].data == {
        "before": ["accepted_check_blocks"], "after": ["question_waits"], "before_status": "hypothesis",
        "after_status": "hypothesis"}
    assert dg.record_change(record, TASK, after, after, at=at(600)) is False            # nothing moved: nothing written
    assert dg.record_change(record, TASK, before, after, at=at(500)) is False           # the same record, once
