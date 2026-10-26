# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The stop/escalation conditions intake derives from declared facts.

Covers ``derive_escalation_triggers`` both as a pure function and through
``run_intake``'s single ``IntakeContract`` construction site. The archive gated
them behind ``G6_METHOD_IR`` for an unported consumer; the library always derives
them (audit 009 RP-011).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from g6.reliability_pipeline.intake import derive_escalation_triggers, run_intake
from g6.reliability_pipeline.task_contract import SuccessCriterion, TaskMode


def _request(goal: str, criteria=("done",), **extra) -> SimpleNamespace:
    return SimpleNamespace(goal=goal, context={"acceptance_criteria": list(criteria)}, **extra)


def _contract_of(result):
    contract = result.metadata.get("task_contract")
    assert contract is not None, f"no task_contract in {result.state} result: {result.reason}"
    return contract


# ── 1. no flag: the triggers are derived whatever the environment says (RP-011) ──

@pytest.mark.parametrize("flag", [None, "0", "1"])
def test_rp011_triggers_are_derived_without_any_flag(monkeypatch, flag):
    if flag is None:
        monkeypatch.delenv("G6_METHOD_IR", raising=False)
    else:
        monkeypatch.setenv("G6_METHOD_IR", flag)
    req = _request(
        "delete production payment records",
        task_mode="exploratory",
        declined_clarification=True,
        accepted_risky_intent=True,
    )
    contract = _contract_of(run_intake(req))
    assert contract.escalation_triggers == (
        "exploratory mode: stop at qualified-draft; do not certify.",
        "feasibility flagged risky: escalate before certifying.",
    )


# ── 2. exploratory mode -> exploratory trigger, exact string ──────

def test_exploratory_mode_trigger():
    req = _request(
        "Draft some brainstorming ideas for a landing page.",
        task_mode="exploratory",
        declined_clarification=True,
    )
    result = run_intake(req)
    contract = _contract_of(result)
    assert contract.escalation_triggers == (
        "exploratory mode: stop at qualified-draft; do not certify.",
    )


# ── 3. feasibility "risky" -> feasibility trigger, exact string ───

def test_feasibility_risky_trigger():
    req =_request("delete production payment records", declined_clarification=True)
    result = run_intake(req)
    contract = _contract_of(result)
    assert contract.escalation_triggers == (
        "feasibility flagged risky: escalate before certifying.",
    )


# ── 4. structured critical criterion with no verifier -> trigger ──

def test_critical_criterion_without_verifier_trigger():
    req =_request(
        "Summarise the attached policy in three bullet points.",
        success_criteria_structured=(
            {"text": "must be formally proven", "critical": True, "verifier": ""},
        ),
        declined_clarification=True,
    )
    result = run_intake(req)
    contract = _contract_of(result)
    assert contract.escalation_triggers == (
        "critical criterion 'must be formally proven' has no available verifier: escalate.",
    )


# ── 5. intent with non_goals -> stop-if trigger per non-goal ──────

def test_intent_non_goals_trigger():
    req =_request(
        "Summarise the attached policy in three bullet points.",
        declined_clarification=True,
        intent_answers={"non_goals": ["invent new policy terms"]},
    )
    result = run_intake(req)
    contract = _contract_of(result)
    assert contract.escalation_triggers == (
        "stop if the output would assert or pursue 'invent new policy terms'.",
    )


# ── pure-function: non_goals then failure_criteria, blanks skipped/stripped ─

def test_pure_function_non_goals_then_failure_criteria_order_and_strip():
    intent = SimpleNamespace(non_goals=["  ng1  ", "", "   "], failure_criteria=["fc1"])
    result = derive_escalation_triggers(
        mode=TaskMode.EVIDENTIARY,
        criteria=(),
        feasibility_classification="clear",
        intent=intent,
    )
    assert result == (
        "stop if the output would assert or pursue 'ng1'.",
        "stop if the output would assert or pursue 'fc1'.",
    )


# ── 6. never-raises: a broken intent escalates, never a partial or empty list ─

def test_never_raises_and_escalates_on_broken_intent():
    """Audit 009 RP-014: an empty list would read as 'no stop condition' (fail open)."""
    class _BrokenIntent:
        @property
        def non_goals(self):
            raise RuntimeError("boom")

    result = derive_escalation_triggers(
        mode=TaskMode.EXPLORATORY,
        criteria=(SuccessCriterion("must be proven", critical=True, verifier=""),),
        feasibility_classification="risky",
        intent=_BrokenIntent(),
    )
    assert result == ("escalation triggers could not be derived: escalate before certifying.",)


# ── 7. pure-function ordering: a case combining a+b+c in declared order ─────

def test_pure_function_ordering_combines_a_b_c():
    criteria = (
        SuccessCriterion("must be proven", critical=True, verifier=""),
        SuccessCriterion("nice to have", critical=False, verifier=""),
    )
    result = derive_escalation_triggers(
        mode=TaskMode.EXPLORATORY,
        criteria=criteria,
        feasibility_classification="too_large",
        intent=None,
    )
    assert result == (
        "exploratory mode: stop at qualified-draft; do not certify.",
        "feasibility flagged too_large: escalate before certifying.",
        "critical criterion 'must be proven' has no available verifier: escalate.",
    )
