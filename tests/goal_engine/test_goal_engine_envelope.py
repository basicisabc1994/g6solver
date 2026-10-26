# SPDX-License-Identifier: Apache-2.0
"""Integration: every PlanTree and ControlReport, in every state, passes ``g6.invariants.check_envelope``, carries the
caller's ids, and is never above qualified-draft."""
from __future__ import annotations

import pytest

from g6.goal_engine import GoalSpec, Observation, build_plan_tree, evaluate_controls
from g6.invariants import check_envelope

_IDS = {"request_id": "req-1", "run_id": "run-1"}
_SPEC = GoalSpec.model_validate({
    "goal": "Release the build",
    "guardrails": [{"name": "tokens", "predicate": "resource_limit", "params": {"max_tokens_per_hour": 1000}}],
    "subtasks": [{"goal": "Test it"}, {"goal": "Ship it", "breakpoints": [{"name": "review"}]}, {"goal": " "}],
})

_PLANS = {
    "declared": lambda: build_plan_tree(_SPEC, **_IDS),
    "not decomposed": lambda: build_plan_tree(GoalSpec(goal="g"), **_IDS),
    "truncated": lambda: build_plan_tree(_SPEC, max_depth=0, **_IDS),
    "refused": lambda: build_plan_tree(GoalSpec(goal=""), **_IDS),
    "bad input": lambda: build_plan_tree("goal", **_IDS),  # type: ignore[arg-type]
}
_REPORTS = {
    "passed": lambda: evaluate_controls(_SPEC, Observation(node="root.0", usage={"tokens_per_hour": 5}), **_IDS),
    "failed": lambda: evaluate_controls(_SPEC, Observation(usage={"tokens_per_hour": 5000}), **_IDS),
    "unknown": lambda: evaluate_controls(_SPEC, Observation(), **_IDS),
    "paused": lambda: evaluate_controls(_SPEC, Observation(node="root.1", usage={"tokens_per_hour": 5}), **_IDS),
    "unknown node": lambda: evaluate_controls(_SPEC, Observation(node="root.9"), **_IDS),
    "bad input": lambda: evaluate_controls(_SPEC, None, **_IDS),  # type: ignore[arg-type]
}
_STATES = {"declared": "qualified-draft", "not decomposed": "qualified-draft", "truncated": "qualified-draft",
           "refused": "blocked-escalated", "passed": "qualified-draft", "failed": "blocked-escalated",
           "unknown": "blocked-escalated", "paused": "blocked-escalated", "unknown node": "blocked-escalated",
           "bad input": "blocked-escalated"}


@pytest.mark.parametrize("case", sorted(_PLANS))
def test_plan_envelopes_pass_the_library_check(case: str) -> None:
    envelope = _PLANS[case]().model_dump()
    assert check_envelope(envelope) == [], envelope
    assert envelope["completion_state"] == _STATES[case]
    assert (envelope["request_id"], envelope["run_id"]) == ("req-1", "run-1")
    assert envelope["warning_card"]["completion_state"] == envelope["completion_state"]


@pytest.mark.parametrize("case", sorted(_REPORTS))
def test_control_report_envelopes_pass_the_library_check(case: str) -> None:
    report = _REPORTS[case]()
    envelope = report.model_dump()
    assert check_envelope(envelope) == [], envelope
    assert envelope["completion_state"] == _STATES[case]
    assert (envelope["request_id"], envelope["run_id"]) == ("req-1", "run-1")
    assert report.may_proceed is (case == "passed")
