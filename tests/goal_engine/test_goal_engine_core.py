# SPDX-License-Identifier: Apache-2.0
"""Survival controls and behaviour of g6.goal_engine: a declared tree is reported in full, an observation that meets
every guardrail passes, registration works, and only a recorded approval releases a pause."""
from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

import g6.goal_engine.predicates as predicates
from g6.core.approval_authority import ISSUER_POLICY_ORACLE, ApprovalAuthority
from g6.goal_engine import (
    GoalSpec,
    GuardrailSpec,
    Observation,
    build_plan_tree,
    evaluate_controls,
    list_predicates,
    register_predicate,
    release_pause,
)
from g6.goal_engine.codes import (
    CHECKPOINT_FAILED,
    CONTROL_INVALID,
    INPUT_INVALID,
    NODE_UNKNOWN,
    OBSERVATION_UNCHECKED,
    PLAN_DECLARED,
    PREDICATE_ERROR,
)


def sample(max_prob: float = 0.10) -> dict[str, Any]:
    """The shape of the archive's examples/sample_goal.json: top-level and per-subtask controls, five subtasks."""
    return {
        "goal": "Build and check an intrusion-detection pipeline",
        "constraints": ["F1 on the test set above 0.92"],
        "resource_bounds": {"max_execution_seconds": 600, "max_tokens_per_hour": 500000, "max_disk_bytes": 1 << 29},
        "guardrails": [
            {"name": "csf_code_execute", "predicate": "csf_hazard",
             "params": {"action": "code_execute", "max_prob": max_prob}},
            {"name": "token_budget", "predicate": "resource_limit", "params": {"max_tokens_per_hour": 500000}},
        ],
        "checkpoints": [{"name": "f1_progress", "predicate": "metric_above", "params": {"metric": "f1",
                                                                                       "threshold": 0.85}}],
        "breakpoints": [{"name": "pre_deploy_review", "description": "review before production", "active": True}],
        "subtasks": [
            {"goal": "Ingest and profile the dataset", "resource_bounds": {"max_execution_seconds": 60}},
            {"goal": "Train the classifier", "resource_bounds": {"max_execution_seconds": 300},
             "checkpoints": [{"name": "training_f1_gate", "predicate": "metric_above",
                              "params": {"metric": "f1", "threshold": 0.92}}]},
            {"goal": "Ground the method"},
            {"goal": "Check the safety invariants",
             "guardrails": [{"name": "csf_verify_hazard", "predicate": "csf_hazard",
                             "params": {"action": "code_execute", "max_prob": max_prob}}]},
            {"goal": "Build the deployment artefacts",
             "breakpoints": [{"name": "pre_deploy_artefact_review", "active": True}]},
        ],
    }


GOOD_USAGE = {"execution_seconds": 30.0, "tokens_per_hour": 1000.0, "disk_bytes": 1024.0}


def test_a_declared_subtask_tree_is_reported_in_full() -> None:
    plan = build_plan_tree(GoalSpec.model_validate(sample()))
    assert [n.node_id for n in plan.nodes] == ["root", "root.0", "root.1", "root.2", "root.3", "root.4"]
    assert plan.nodes[0].children == ("root.0", "root.1", "root.2", "root.3", "root.4")
    assert plan.rejected == () and plan.truncated == ()
    assert plan.depth == 1 and plan.completion_state == "qualified-draft"
    assert plan.warning_card["code"] == PLAN_DECLARED


def test_nested_declared_order_is_kept() -> None:
    spec = GoalSpec(goal="a", subtasks=[GoalSpec(goal="b", subtasks=[GoalSpec(goal="c")]), GoalSpec(goal="d")])
    plan = build_plan_tree(spec)
    assert [(n.node_id, n.parent_id, n.depth, n.goal) for n in plan.nodes] == [
        ("root", None, 0, "a"), ("root.0", "root", 1, "b"), ("root.0.0", "root.0", 2, "c"), ("root.1", "root", 1, "d")]


def test_a_stored_intent_engine_goal_loads_as_a_goal_spec() -> None:
    from g6.intent_engine import GoalInput  # the archive's schema, kept in the library unchanged

    spec = GoalSpec.model_validate(GoalInput.model_validate(sample()).model_dump())
    assert spec.subtasks[1].checkpoints[0].name == "training_f1_gate"


def test_an_observation_that_meets_every_guardrail_passes_them() -> None:
    spec = GoalSpec.model_validate(sample())
    report = evaluate_controls(spec, Observation(node="root.1", action="code_execute", usage=GOOD_USAGE,
                                                 metrics={"f1": 0.95}))
    guards = [(r.name, r.declared_by, r.verdict) for r in report.results if r.kind == "guardrail"]
    assert guards == [("resource_bounds", "root", "pass"), ("csf_code_execute", "root", "pass"),
                      ("token_budget", "root", "pass"), ("resource_bounds", "root.1", "pass")]
    assert not report.blocked
    assert [p.breakpoint for p in report.pauses] == ["pre_deploy_review"]  # inherited from the root


def test_with_no_pause_in_scope_a_passing_observation_may_proceed() -> None:
    spec = GoalSpec.model_validate({**sample(), "breakpoints": []})
    report = evaluate_controls(spec, Observation(node="root.2", action="llm_call", usage=GOOD_USAGE,
                                                 metrics={"f1": 0.9}))
    assert report.may_proceed and report.completion_state == "qualified-draft"
    assert report.warning_card["code"] == OBSERVATION_UNCHECKED


def test_a_failing_checkpoint_is_reported_and_does_not_block() -> None:
    spec = GoalSpec.model_validate({**sample(), "breakpoints": []})
    report = evaluate_controls(spec, Observation(node="root.1", action="file_write", usage=GOOD_USAGE,
                                                 metrics={"f1": 0.90}))
    checks = {r.name: (r.verdict, r.code) for r in report.results if r.kind == "checkpoint"}
    assert checks == {"f1_progress": ("pass", ""), "training_f1_gate": ("fail", CHECKPOINT_FAILED)}
    assert report.may_proceed and any(e["code"] == CHECKPOINT_FAILED for e in report.evidence)


def test_csf_hazard_scoped_to_one_action_passes_another_known_action() -> None:
    spec = GoalSpec(goal="g", guardrails=[GuardrailSpec(name="no_risky_writes", predicate="csf_hazard",
                                                        params={"action": "file_write", "max_prob": 0.001},
                                                        message="Halt before a risky write")])
    assert evaluate_controls(spec, Observation(action="llm_call")).may_proceed
    blocked = evaluate_controls(spec, Observation(action="file_write"))
    assert blocked.blocked and blocked.results[0].message == "Halt before a risky write"
    assert evaluate_controls(spec, Observation(action="shell")).blocked  # an unknown action blocks in any scope


def test_a_misspelt_csf_scope_is_a_spec_error_not_a_bypass() -> None:
    spec = GoalSpec(goal="g", guardrails=[GuardrailSpec(name="g", predicate="csf_hazard",
                                                        params={"action": "code_exec", "max_prob": 0.05})])
    result = evaluate_controls(spec, Observation(action="code_execute")).results[0]
    assert (result.verdict, result.code) == ("unknown", CONTROL_INVALID)
    assert build_plan_tree(spec).completion_state == "blocked-escalated"  # the root cannot be planned


def test_register_predicate_adds_a_check_that_reads_the_observation(monkeypatch) -> None:
    monkeypatch.setattr(predicates, "_PREDICATES", dict(predicates._PREDICATES))

    def tests_green(params):
        suite = params["suite"]
        return lambda obs: ("pass", "") if obs.metrics.get(suite) == 1.0 else ("fail", f"{suite} not green")

    register_predicate("tests_green", tests_green)
    assert "tests_green" in list_predicates()
    spec = GoalSpec(goal="g", guardrails=[GuardrailSpec(name="ci", predicate="tests_green", params={"suite": "unit"})])
    assert evaluate_controls(spec, Observation(metrics={"unit": 1.0})).may_proceed
    assert evaluate_controls(spec, Observation(metrics={"unit": 0.0})).blocked


def test_register_predicate_refusals(monkeypatch) -> None:
    monkeypatch.setattr(predicates, "_PREDICATES", dict(predicates._PREDICATES))
    with pytest.raises(ValueError, match="G6_E_GOAL_PREDICATE_REGISTRATION: built-in"):
        register_predicate("csf_hazard", lambda params: (lambda obs: True))
    register_predicate("mine", lambda params: (lambda obs: True))
    with pytest.raises(ValueError, match="already registered"):
        register_predicate("mine", lambda params: (lambda obs: False))
    for name in ("", "  ", "has space", "semi;colon", 3):
        with pytest.raises(ValueError, match="invalid predicate name"):
            register_predicate(name, lambda params: (lambda obs: True))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="must be callable"):
        register_predicate("not_callable", 3)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="exactly one required params"):
        register_predicate("no_params", lambda: (lambda obs: True))
    register_predicate("csf_hazard", lambda params: (lambda obs: "unknown"), override=True)
    assert list_predicates().count("csf_hazard") == 1


def test_a_factory_may_refuse_empty_params(monkeypatch) -> None:
    # The archive probed factories with {} and refused one that raised, forcing a default for every parameter.
    monkeypatch.setattr(predicates, "_PREDICATES", dict(predicates._PREDICATES))
    register_predicate("strict", lambda params: (lambda obs: True) if params.get("x") else 1 / 0)
    missing = GoalSpec(goal="g", guardrails=[GuardrailSpec(name="s", predicate="strict")])
    assert evaluate_controls(missing, Observation()).results[0].code == CONTROL_INVALID


@pytest.mark.parametrize(("returned", "verdict"), [
    (True, "pass"), (False, "fail"), ("pass", "pass"), (("fail", "why"), "fail"), (None, "unknown"), (1, "unknown"),
    ("yes", "unknown"), (("pass",), "unknown"), (("pass", 3), "unknown"), ([], "unknown")])
def test_only_a_recognised_verdict_counts(monkeypatch, returned, verdict) -> None:
    monkeypatch.setattr(predicates, "_PREDICATES", dict(predicates._PREDICATES))
    register_predicate("fixed", lambda params: (lambda obs: returned))
    spec = GoalSpec(goal="g", guardrails=[GuardrailSpec(name="f", predicate="fixed")])
    assert evaluate_controls(spec, Observation()).results[0].verdict == verdict


def test_a_raising_predicate_is_unknown_and_blocks(monkeypatch) -> None:
    monkeypatch.setattr(predicates, "_PREDICATES", dict(predicates._PREDICATES))
    register_predicate("boom", lambda params: (lambda obs: 1 / 0))
    spec = GoalSpec(goal="g", guardrails=[GuardrailSpec(name="b", predicate="boom")])
    report = evaluate_controls(spec, Observation())
    assert (report.results[0].verdict, report.results[0].code) == ("unknown", PREDICATE_ERROR)
    assert report.blocked and "ZeroDivisionError" in report.results[0].reason


@pytest.mark.parametrize("node", ["root.5", "root.0.0", "x", "root.", "root.-1", "root.01", "", "Root"])
def test_an_unknown_node_is_refused(node) -> None:
    spec = GoalSpec(goal="g", subtasks=[GoalSpec(goal="s")])
    report = evaluate_controls(spec, Observation(node=node))
    assert report.completion_state == "blocked-escalated" and report.warning_card["code"] == NODE_UNKNOWN
    assert not report.may_proceed


def test_wrong_input_types_are_refused_with_a_code() -> None:
    spec = GoalSpec(goal="g")
    assert evaluate_controls({"goal": "g"}, Observation()).warning_card["code"] == INPUT_INVALID  # type: ignore[arg-type]
    assert evaluate_controls(spec, {"node": "root"}).warning_card["code"] == INPUT_INVALID  # type: ignore[arg-type]
    assert build_plan_tree({"goal": "g"}).warning_card["code"] == INPUT_INVALID  # type: ignore[arg-type]
    for bad in (-1, True, 2.0, "3"):
        refused = build_plan_tree(spec, max_depth=bad)  # type: ignore[arg-type]
        assert refused.completion_state == "blocked-escalated" and refused.warning_card["code"] == INPUT_INVALID


def test_evaluation_changes_nothing() -> None:
    spec = GoalSpec.model_validate(sample())
    before, names = spec.digest(), list_predicates()
    for node in ("root", "root.1", "root.4"):
        evaluate_controls(spec, Observation(node=node, action="code_execute", usage=GOOD_USAGE))
    build_plan_tree(spec)
    assert spec.digest() == before and list_predicates() == names


def test_inactive_breakpoints_ask_for_nothing() -> None:
    spec = GoalSpec.model_validate({"goal": "g", "breakpoints": [{"name": "off", "active": False}]})
    assert evaluate_controls(spec, Observation()).pauses == ()


def test_only_a_recorded_human_approval_for_this_pause_releases_it() -> None:
    spec = GoalSpec.model_validate(sample())
    pause = evaluate_controls(spec, Observation(node="root.4"), run_id="run-7").pauses[-1]
    assert (pause.breakpoint, pause.declared_by, pause.run_id) == ("pre_deploy_artefact_review", "root.4", "run-7")
    authority = ApprovalAuthority()
    for made_up in ("approved", "", None, 1, pause.scope):
        assert release_pause(pause, made_up, authority=authority) is None
    assert release_pause(pause, authority.issue(reviewer_id="r", scope=pause.scope + "x").approval_id,
                         authority=authority) is None
    assert release_pause(pause, authority.issue(reviewer_id="r", scope=pause.scope, run_id="run-8").approval_id,
                         authority=authority) is None
    assert release_pause(pause, authority.issue(reviewer_id="r", scope=pause.scope, decision="rejected").approval_id,
                         authority=authority) is None
    oracle = authority.issue(reviewer_id="policy", scope=pause.scope, issuer_kind=ISSUER_POLICY_ORACLE,
                             policy_version="v1")
    assert release_pause(pause, oracle.approval_id, authority=authority) is None
    record = authority.issue(reviewer_id="dana", scope=pause.scope, run_id="run-7")
    assert release_pause(pause, record.approval_id, authority=authority).reviewer_id == "dana"
    assert release_pause(pause, record.approval_id, authority=authority) is None  # single use, consumed


def test_an_approval_for_one_spec_does_not_release_the_same_pause_in_another() -> None:
    first = GoalSpec(goal="Ship v1", breakpoints=[{"name": "review"}])
    second = GoalSpec(goal="Ship v2", breakpoints=[{"name": "review"}])
    one, two = (evaluate_controls(s, Observation()).pauses[0] for s in (first, second))
    authority = ApprovalAuthority()
    record = authority.issue(reviewer_id="r", scope=one.scope, single_use=False)
    assert one.scope != two.scope
    assert release_pause(two, record.approval_id, authority=authority) is None
    assert release_pause(one, record.approval_id, authority=authority) is not None


@pytest.mark.parametrize("bad", [
    {"goal": "g", "guardrails": [{"name": "a", "predicate": "p"}, {"name": "a", "predicate": "q"}]},
    {"goal": "g", "guardrails": [{"name": "resource_bounds", "predicate": "resource_limit"}]},
    {"goal": "g", "breakpoints": [{"name": "a:b"}]},
    {"goal": "g", "breakpoints": [{"name": " a"}]},
    {"goal": "g", "breakpoints": [{"name": "a", "active": "yes"}]},
    {"goal": "g", "checkpoints": [{"name": "c", "predicate": " "}]},
    {"goal": "g", "guardrails": [{"name": "c", "predicate": "p", "params": {"x": {1, 2}}}]},
])
def test_ambiguous_or_malformed_controls_are_refused(bad) -> None:
    with pytest.raises(ValidationError):
        GoalSpec.model_validate(bad)


@pytest.mark.parametrize("bad", [{"usage": {"tokens": 1}}, {"usage": {"tokens_per_hour": -1}},
                                 {"usage": {"tokens_per_hour": float("inf")}}, {"metrics": {"f1": float("nan")}},
                                 {"metrics": {"f1": "0.9"}}, {"node": "root", "who": "me"}])
def test_malformed_observations_are_refused(bad) -> None:
    with pytest.raises(ValidationError):
        Observation.model_validate(bad)
