# SPDX-License-Identifier: Apache-2.0
"""Review 1, class 3 (findings 2 and 3): check, then act on what was checked. The compiled model is a frozen copy whose
pin execution rechecks, and the executor runs the step copies it validated, never the caller's mutable dictionaries."""
from __future__ import annotations

from returns.result import Success

from g6.cognitive_bridge import ClaimIR, MethodRouter, MixedMethodExecutor
from g6.cognitive_bridge.assurance import canonical_json, sha256


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def invoke(self, name, op, params=None, **kwargs):
        self.calls.append(params)
        return Success({"completion_state": "qualified-draft"})


def test_finding_2_the_callers_model_cannot_change_what_runs_after_compilation(assurance_case):
    c = assurance_case
    registry = _Recorder()
    c["orchestrator"].registry = registry
    pin = c["compiled"].selected_model_hashes["v"]
    c["source"]["premises"].append("False")
    result = c["orchestrator"].execute(c["compiled"], approved=True)
    assert registry.calls and all(sha256(canonical_json(params)) == pin for params in registry.calls), (
        f'changed model executed: {registry.calls}; state={result["completion_state"]}')


def test_finding_2_a_compiled_model_edited_after_its_pin_is_refused_before_anything_runs(assurance_case):
    c = assurance_case
    registry = _Recorder()
    c["orchestrator"].registry = registry
    c["compiled"].executable_plan.claims[0].models["formal"]["premises"].append("False")
    result = c["orchestrator"].execute(c["compiled"], approved=True)
    assert registry.calls == [], f"edited compiled model executed: {registry.calls}"
    assert result["completion_state"] == "blocked-escalated" and result["code"] == "G6_E_COGNITIVE_PLAN_TAMPERED"


def test_finding_3_a_later_step_cannot_change_between_the_canonical_check_and_its_invocation():
    plan = MethodRouter().plan([ClaimIR("one", "first claim", ("formal",), {"formal": {"formula": "A"}}),
                                ClaimIR("two", "second claim", ("formal",), {"formal": {"formula": "B"}})])
    calls = []

    class Registry:
        def invoke(self, name, op, params=None, **kwargs):
            calls.append(params["formula"])
            if len(calls) == 1:  # a caller edits the pending plan while the first call runs
                plan.steps[1].params["formula"] = "False"
            return Success({"completion_state": "qualified-draft"})
    result = MixedMethodExecutor(Registry()).execute(plan, approved=True)
    assert "False" not in calls, f'changed pending step executed: {calls}; state={result["completion_state"]}'
    assert calls == ["A", "B"]
