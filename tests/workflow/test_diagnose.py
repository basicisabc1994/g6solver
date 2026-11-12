# SPDX-License-Identifier: Apache-2.0
"""The coding-workflow diagnosis adapter (design 17.2 "A constraint hypothesis"; 17.4 step 3; AT-003, AT-004):
hypotheses only, each with another explanation, what would count against it and a check that tells them apart;
abstention reachable and the default; no winner forced; no probability or score anywhere."""
from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from g6.harness.workflow import diagnose as dg
from g6.harness.workflow import obstacle as ob
from g6.harness.workflow import record as rec
from g6.harness.workflow import view
from tests.workflow.kit import event, sha

R1, R2, R3 = "a" * 32, "b" * 32, "c" * 32
OUTCOME = ob.Outcome(sha("goal"), "add returns the sum", "attributed_label")
CHECK = "tests/test_calc.py::test_add"
OPEN = sha("g6_review_1/test_a.py")


def _req(status="failed", provenance="stated", verdict="failed"):
    return {"id": "R1", "status": status, "provenance": provenance,
            "checks": [{"id": CHECK, "verdict": verdict, "provenance": provenance}]}


def _assessment(*requirements, base=None):
    scope = {"checks_run": [{"check": "stated checks at the base commit", "verdict": "failed", "outcomes": base}]} \
        if base else {}
    return {"report": "REPORT", "requirements": list(requirements), "reasons": [], "scope": scope}


def _run(run, start, *, rounds=(), ending=None, code="", review=None, kind="loop_session", end=None):
    events = [event(run, "run_started", seconds=start, data={"origin_kind": kind, "origin_sha256": sha(run)})]
    for number, still_open in enumerate(rounds, 1):
        events.append(event(run, "loop_round", str(number), seconds=start + number,
                            data={"round": number, "open_sha256": list(still_open)}))
    if review is not None:
        events.append(event(run, "review_finished", seconds=start + 5, data=review))
    if ending is not None:
        events.append(event(run, "loop_ended", seconds=end or start + 9, data={"ending": ending, **({"code": code} if code else {})}))
    return events


def _pending(seconds):
    return event("d" * 32, "question_pending", "q1", seconds=seconds, refs=[rec.Ref("question", sha("q1"), sha("q1"))])


def _rules(diagnosis):
    return [h.why_code for h in diagnosis.hypotheses]


def test_slow_noncritical_stage_forces_no_winner():
    slow = [*_run(R1, 0, rounds=[()], ending="clean", end=90000),
            event(R1, "tool_call", "t:1", seconds=10, data={"tool": "g6_review_until_clean", "seconds": 80000.0})]
    passing = _assessment(_req(status="passed_on_stated_checks", verdict="passed"))
    found = dg.diagnose(slow, assessment=passing, outcome=OUTCOME)
    assert found.status == "insufficient_observation" and found.hypotheses == () and found.obstacles == ()
    # the same slow stage beside a real blocking dependency: only the dependency is named, never the slow stage
    both = dg.diagnose(slow, assessment=_assessment(_req()), outcome=OUTCOME)
    assert _rules(both) == ["accepted_check_blocks"] and "slow" not in str(both.to_dict()).lower()


def test_overlapping_work_forces_no_winner_and_no_capacity_view():
    overlap = [*_run(R1, 0, rounds=[()], ending="clean", end=50), *_run(R2, 10, rounds=[()], ending="clean", end=40)]
    stages = [event(R1, "stage_measured", name, seconds=1, data={"stage": name, "attempted": 4, "succeeded": 2,
                                                                 "capacity_per_day": 8.0}) for name in ("review", "repair")]
    found = dg.diagnose([*overlap, *stages], outcome=OUTCOME)
    assert dg.overlapping(overlap) and found.hypotheses == () and found.status == "insufficient_observation"
    assert found.capacity is None and "overlapped" in found.capacity_abstained
    with_check = dg.diagnose([*overlap, *stages], assessment=_assessment(_req()), outcome=OUTCOME)
    assert all(h.overlapping for h in with_check.hypotheses) and with_check.capacity is None
    serial = [*_run(R1, 0, rounds=[()], ending="clean", end=5), *_run(R2, 10, rounds=[()], ending="clean", end=40)]
    assert not dg.overlapping(serial)


def test_failing_noncritical_check_forces_no_winner():
    for requirement in (_req(provenance="model_hypothesis"), _req(status="not_established", verdict="collection_error")):
        found = dg.diagnose(_run(R1, 0, rounds=[()]), assessment=_assessment(requirement), outcome=OUTCOME)
        assert found.hypotheses == () and found.status == "insufficient_observation"
    integrity = {**_assessment(), "reasons": [{"stage": "integrity", "code": "x", "detail": "d"}]}
    only = dg.diagnose(_run(R1, 0), assessment=integrity, outcome=OUTCOME)
    assert only.status == "obstacle_only" and [o.kind for o in only.obstacles] == ["integrity_block"]


def test_explicit_blocking_dependency_gives_the_scoped_obstacle_and_hypothesis():
    found = dg.diagnose(_run(R1, 0), assessment=_assessment(_req()), outcome=OUTCOME, job_id="job-1")
    assert found.status == "hypothesis"
    (one,) = found.hypotheses
    assert one.basis == "blocking_dependency" and one.why_code == "accepted_check_blocks"
    assert one.obstacle.kind == "failing_accepted_check" and one.scope == "requirement R1 on this patch"
    assert one.obstacle.receipt.id == "job-1" and one.outcome == OUTCOME and one.overlapping is False
    assert one.check["kind"] == "rerun_at_base" and [e.code for e in one.explanations] == [
        "patch_wrong_for_case", "check_stale_or_wrong"]
    assert dg.diagnose(_run(R1, 0), assessment=_assessment(_req()), outcome=OUTCOME, job_id="job-1").hypotheses[0].id == one.id


def test_two_rules_firing_stay_unresolved_in_stable_order():
    events = [_pending(0), *_run(R1, 10, review={"outcome": "insufficient_evidence"}, kind="review_job"),
              *_run(R2, 20, review={"outcome": "insufficient_evidence"}, kind="review_job")]
    found = dg.diagnose(events, assessment=_assessment(_req()), outcome=OUTCOME)
    assert found.status == "several" and _rules(found) == ["accepted_check_blocks", "question_waits"]
    again = dg.diagnose(list(reversed(events)), assessment=_assessment(_req()), outcome=OUTCOME)
    assert [h.id for h in again.hypotheses] == [h.id for h in found.hypotheses]         # no order from the input
    said = found.to_dict()
    assert not {"winner", "primary", "rank", "best"} & set(said) and "preferred" not in str(said["hypotheses"])


def test_missing_events_abstain():
    nothing = dg.diagnose((), outcome=OUTCOME)
    assert nothing.status == "insufficient_observation" and nothing.hypotheses == () and nothing.obstacles == ()
    assert "no_g6_observation" in nothing.missing
    assert dg.diagnose((), outcome=None).status == "insufficient_observation"
    assert dg.diagnose(_run(R1, 0, rounds=[(OPEN,)]), outcome=OUTCOME).hypotheses == ()   # one round is no pattern


def test_unknown_intent_abstains_from_a_hypothesis_and_keeps_the_obstacle():
    found = dg.diagnose(_run(R1, 0), assessment=_assessment(_req()), outcome=None)
    assert found.status == "unknown_outcome" and found.hypotheses == ()
    assert [o.kind for o in found.obstacles] == ["failing_accepted_check"] and "stated_outcome" in found.missing
    pattern = [*_run(R1, 0, rounds=[(OPEN,)]), *_run(R2, 20, rounds=[(OPEN,)])]
    assert dg.diagnose(pattern, outcome=None).hypotheses == ()


def _firing(rule: str):
    if rule == "accepted_check_blocks":
        return {"events": _run(R1, 0), "assessment": _assessment(_req())}
    if rule == "repair_repeats":
        return {"events": [*_run(R1, 0, rounds=[(OPEN,)], ending="open"), *_run(R2, 20, rounds=[(OPEN,)], ending="open")]}
    if rule == "question_waits":
        return {"events": [_pending(0), *_run(R1, 10, review={"outcome": "insufficient_evidence"}, kind="review_job"),
                           *_run(R2, 20, review={"outcome": "insufficient_evidence"}, kind="review_job")]}
    code = "G6_E_NO_PYTHON_CHANGED"
    return {"events": [*_run(R1, 0, ending="untested", code=code), *_run(R2, 20, ending="untested", code=code)]}


def _withdrawn(rule: str):
    if rule == "accepted_check_blocks":                 # the check fails at the base commit too
        return {"events": _run(R1, 0), "assessment": _assessment(_req(), base={CHECK: "failed"})}
    if rule == "repair_repeats":                        # the test passed in the latest run
        return {"events": [*_run(R1, 0, rounds=[(OPEN,)], ending="open"), *_run(R2, 20, rounds=[(OPEN,)], ending="open"),
                           *_run(R3, 40, rounds=[()], ending="fixed")]}
    if rule == "question_waits":                        # a later run ended otherwise, the question still pending
        return {"events": [_pending(0), *_run(R1, 10, review={"outcome": "insufficient_evidence"}, kind="review_job"),
                           *_run(R2, 20, review={"outcome": "defect_detected"}, kind="review_job")]}
    code = "G6_E_NO_PYTHON_CHANGED"                     # a run completed between the two
    return {"events": [*_run(R1, 0, ending="untested", code=code), *_run(R3, 10, rounds=[()], ending="clean"),
                       *_run(R2, 20, ending="untested", code=code)]}


@pytest.mark.parametrize("rule", [rule.name for rule in dg.RULES])
def test_every_hypothesis_has_two_explanations_and_a_check(rule):
    given = _firing(rule)
    found = dg.diagnose(given["events"], assessment=given.get("assessment"), outcome=OUTCOME)
    assert _rules(found) == [rule], found.status
    (one,) = found.hypotheses
    assert len(one.explanations) >= 2 and len({e.predicts for e in one.explanations}) == len(one.explanations)
    assert one.check["observable"] and one.check["command"] and one.basis in dg.BASES
    said = one.to_dict()
    assert said["why"] == dg.WORDS[rule] and said["would_count_against_it"] == dg.AGAINST[rule]
    assert all(e["said"] for e in said["explanations"])


@pytest.mark.parametrize("rule", [rule.name for rule in dg.RULES])
def test_counterevidence_withdraws_the_hypothesis(rule):
    given = _withdrawn(rule)
    found = dg.diagnose(given["events"], assessment=given.get("assessment"), outcome=OUTCOME)
    assert rule not in _rules(found)


def test_a_repair_that_repeats_within_one_run_needs_three_rounds():
    two = dg.diagnose(_run(R1, 0, rounds=[(OPEN,), (OPEN,)]), outcome=OUTCOME)
    three = dg.diagnose(_run(R1, 0, rounds=[(OPEN,), (OPEN,), (OPEN,)]), outcome=OUTCOME)
    assert two.hypotheses == () and _rules(three) == ["repair_repeats"]
    assert three.hypotheses[0].basis == "repeated_pattern" and three.hypotheses[0].obstacle.kind == "reviewer_test_stays_open"


def test_no_probability_or_score_field_exists():
    banned = {"score", "probability", "confidence", "utility", "minutes", "likelihood", "weight", "rank"}
    for cls in (dg.Explanation, dg.Hypothesis, dg.Diagnosis, dg.Rule, ob.Obstacle, ob.Outcome):
        names = {f.name for f in dataclasses.fields(cls)}
        assert not names & banned, cls.__name__
    given = _firing("accepted_check_blocks")
    said = dg.diagnose(given["events"], assessment=given["assessment"], outcome=OUTCOME).to_dict()

    def keys(value):
        if isinstance(value, dict):
            return set(value) | {k for one in value.values() for k in keys(one)}
        return {k for one in value for k in keys(one)} if isinstance(value, list) else set()
    assert not keys(said) & banned
    assert not any(isinstance(v, float) for h in said["hypotheses"] for v in h.values())


def test_capacity_view_abstains_unless_stages_are_independent_and_serial():
    run = _run(R1, 0, rounds=[()], ending="clean")
    stage = lambda name, **data: event(R1, "stage_measured", name, seconds=1, data={  # noqa: E731
        "stage": name, "attempted": 4, "succeeded": 2, "capacity_per_day": 8.0, **data})
    assert dg.capacity_view(run) == (None, "no independently measured serial stages for this task")
    assert dg.capacity_view([*run, stage("review")])[0] is None                      # one stage is no comparison
    labelled = dataclasses.replace(stage("repair"), provenance="attributed_label")
    assert "not observed by the host" in dg.capacity_view([*run, stage("review"), labelled])[1]
    assert "no attempted work" in dg.capacity_view([*run, stage("review"), stage("repair", attempted=0, succeeded=0)])[1]
    result, why = dg.capacity_view([*run, stage("review"), stage("repair", succeeded=1)])
    assert why == "" and result.bottleneck == "repair" and result.throughput_bound == 2.0


def test_capacity_view_result_is_labelled_a_calculation_not_a_cause():
    run = _run(R1, 0, rounds=[()], ending="clean")
    stages = [event(R1, "stage_measured", name, seconds=1, data={"stage": name, "attempted": 4, "succeeded": done,
                                                                 "capacity_per_day": 8.0}) for name, done in (("review", 2), ("repair", 1))]
    said = dg.diagnose([*run, *stages], outcome=OUTCOME).to_dict()
    assert said["hypotheses"] == [] and said["status"] == "insufficient_observation"      # arithmetic names no hypothesis
    assert said["capacity"]["said"] == dg.CALCULATION and "identifies no cause" in dg.CALCULATION
    assert "bottleneck" not in str(said) and "constraint" not in str(said["capacity"]).lower()


def test_person_task_rows_are_never_read_by_tick_rules():
    tree = ast.parse(Path(dg.__file__).read_text(encoding="utf-8"))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module} | {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert not {name for name in imported if "tick_rca" in name or "reflex" in name or name.startswith("g6.metaharness")}
    assert all(rule.line and rule.against and rule.reads and len(rule.explanations) >= 2 for rule in dg.RULES)
    assert [rule.name for rule in dg.RULES] == sorted(rule.name for rule in dg.RULES)


def _level(monkeypatch, level, tmp_path):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("G6_WORKFLOW_RECORD", "on")
    monkeypatch.setattr(view, "ADMITTED_LEVEL", level)
    monkeypatch.setattr(view, "shared", lambda project, provider: True)


def test_diagnosis_level_keeps_the_prefix_and_line_cap(monkeypatch, tmp_path):
    from tests.workflow.kit import PROJECT, TASK

    _level(monkeypatch, "diagnosis", tmp_path)
    built = view.build(PROJECT, TASK, assessment=_assessment(_req()), intent={"goal": "add returns the sum"},
                       provider="openai:codex", job_id="job-1", request="fix this bug")
    assert built["level"] == "diagnosis" and built["diagnosis"]["status"] == "hypothesis"
    block = view.block(built)
    assert len(block) == view.MAX_LINES == 8 and view.with_block("REPORT", built).startswith("REPORT\n\n")
    narrow = view.block({key: value for key, value in built.items() if key != "diagnosis"})
    assert len(block) - len(narrow) == 2 and [line for line in block if line not in narrow] == block[4:6]
    _level(monkeypatch, "narrow", tmp_path)                 # not admitted: the same inputs carry no diagnosis
    assert "diagnosis" not in view.build(PROJECT, TASK, assessment=_assessment(_req()), intent={"goal": "g"},
                                         provider="openai:codex")


def test_hypothesis_line_says_hypothesis_and_names_what_would_count_against_it(monkeypatch, tmp_path):
    from tests.workflow.kit import PROJECT, TASK

    _level(monkeypatch, "diagnosis", tmp_path)
    built = view.build(PROJECT, TASK, assessment=_assessment(_req()), intent={"goal": "add returns the sum"},
                       provider="openai:codex", job_id="job-1")
    hypothesis, against = dg.lines(built["diagnosis"])
    assert hypothesis.startswith("- **A hypothesis, not a finding:**") and "It could instead be that" in hypothesis
    assert against.startswith("- **What would count against it:**") and "fails at the base commit too" in against
    assert "A check that tells them apart:" in against
    said = "\n".join(view.block(built)).lower()
    for banned in ("the constraint is", "the bottleneck is", "binding constraint", "root cause", "verified", "saves",
                   "minutes", "probab", "likely"):
        assert banned not in said
    assert dg.lines({"hypotheses": []}) == [] and dg.lines({}) == []
