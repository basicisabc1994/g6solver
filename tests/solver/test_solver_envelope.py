# SPDX-License-Identifier: Apache-2.0
"""Integration: every solver result envelope passes ``g6.invariants.check_envelope`` and none is above
qualified-draft: a checked pass, an unchecked run, a failed check and a refused production request."""
from __future__ import annotations

import pytest

from g6.invariants import check_envelope
from g6.solver import ConstraintSpec, SolverInput, solve

_RUNNERS = {"goal_setting": lambda ctx: "add two numbers", "code_construction": lambda ctx: {"answer": "4"},
            "solution_execution": lambda ctx: {"log": "ran"}, "solution_evaluation": lambda ctx: {"ok": True}}
_SAYS = lambda value: [ConstraintSpec(raw=f"says {value}", kind="contains", value=value)]  # noqa: E731


@pytest.mark.parametrize(("solver_input", "state", "outcome"), [
    (SolverInput(goal="What is 2 + 2?", constraints=_SAYS("4")), "qualified-draft", "passed"),
    (SolverInput(goal="What is 2 + 2?"), "qualified-draft", "unchecked"),
    (SolverInput(goal="What is 2 + 2?", constraints=_SAYS("5")), "blocked-escalated", "failed"),
    (SolverInput(goal="What is 2 + 2?", run_mode="production"), "blocked-escalated", "unchecked"),
])
def test_solver_envelopes_pass_the_library_check(solver_input: SolverInput, state: str, outcome: str) -> None:
    out = solve(solver_input, _RUNNERS)
    envelope = out.to_envelope()
    assert check_envelope(envelope) == [], envelope
    assert (envelope["completion_state"], out.outcome) == (state, outcome)
