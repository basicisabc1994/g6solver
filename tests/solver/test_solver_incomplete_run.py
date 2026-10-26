# SPDX-License-Identifier: Apache-2.0
"""An incomplete run (a planned step had no runner) never reads as a plain draft: its warning card leads with
``G6_E_SOLVER_INCOMPLETE`` and names every step that did not run, even when a constraint passed.

Before, such a run came back ``qualified-draft`` with ``G6_E_SOLVER_CHECKED_DRAFT``, "the named checks passed", and
only ``failures[]`` said that three of its four planned steps never ran."""
from __future__ import annotations

from g6.invariants import check_envelope
from g6.solver import ConstraintSpec, SolverInput, solve

GOAL = "What is 2 + 2?"
SAYS_4 = ConstraintSpec(raw="says 4", kind="contains", value="4")
ALL = {"goal_setting": lambda ctx: "add two numbers", "code_construction": lambda ctx: {"answer": "4"},
       "solution_execution": lambda ctx: {"log": "ran"}, "solution_evaluation": lambda ctx: {"ok": True}}


def test_an_incomplete_run_whose_constraint_passed_names_the_steps_that_did_not_run() -> None:
    out = solve(SolverInput(goal=GOAL, constraints=[SAYS_4]), {"code_construction": ALL["code_construction"]})
    assert out.status == "incomplete" and out.success and out.completion_state == "qualified-draft"
    envelope = out.to_envelope()
    card = envelope["warning_card"]
    assert card["code"] == "G6_E_SOLVER_INCOMPLETE", card
    for step in ("goal_setting", "solution_execution", "solution_evaluation"):
        assert step in card["message"] and step in out.degradation_reason
    assert "did not run" in card["message"] and "constraint:" in card["message"]
    assert check_envelope(envelope) == []


def test_an_incomplete_unchecked_run_names_them_too() -> None:
    card = solve(SolverInput(goal=GOAL), {"code_construction": ALL["code_construction"]}).to_envelope()["warning_card"]
    assert card["code"] == "G6_E_SOLVER_INCOMPLETE" and "goal_setting" in card["message"]
    assert "no deterministic named check passed" in card["message"].lower()


def test_survival_a_complete_checked_run_keeps_the_checked_draft_card() -> None:
    out = solve(SolverInput(goal=GOAL, constraints=[SAYS_4]), ALL)
    assert out.status == "completed"
    assert out.to_envelope()["warning_card"]["code"] == "G6_E_SOLVER_CHECKED_DRAFT"
