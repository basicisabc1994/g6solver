# SPDX-License-Identifier: Apache-2.0
"""A model that names no unsafe state is refused by ``reachability_bound``, never bounded at 0.

With an empty unsafe set the union bound is 0 whatever the transitions say, so it met every epsilon, 0 included, and
came back ``qualified-draft``: an unnamed hazard read as safety. ``check_z3`` reads a property over states, not the
unsafe labels, so it still takes such a model."""
from __future__ import annotations

import importlib.util

import pytest

from g6.csf import BoundedAgent, CSFInputError, ResourceTriple, reachability_bound
from g6.csf.schema import MODEL_INVALID

_ROWS = [{"from": "a", "to": "harm", "probability": 1.0}, {"from": "harm", "to": "harm", "probability": 1.0}]


def _agent(unsafe: list[str]) -> BoundedAgent:
    return BoundedAgent(states=["a", "harm"], transitions=_ROWS, initial_state="a", unsafe_states=unsafe,
                        resources=ResourceTriple(N=3))


def test_a_model_that_names_no_unsafe_state_is_refused_at_every_epsilon() -> None:
    agent = _agent([])
    for epsilon in (0.0, 0.5, 1.0):
        with pytest.raises(CSFInputError) as refusal:
            reachability_bound(agent, epsilon)
        assert refusal.value.code == MODEL_INVALID and "no unsafe state" in refusal.value.reason


def test_survival_the_same_model_with_its_unsafe_state_named_is_bounded() -> None:
    report = reachability_bound(_agent(["harm"]), 0.5)
    assert report.vub == 1.0 and report.completion_state == "blocked-escalated"


def test_survival_a_named_unsafe_state_that_is_never_reached_still_gives_zero() -> None:
    rows = [{"from": "a", "to": "a", "probability": 1.0}, {"from": "harm", "to": "harm", "probability": 1.0}]
    agent = BoundedAgent(["a", "harm"], rows, "a", ["harm"], ResourceTriple(N=3))
    report = reachability_bound(agent, 0.0)
    assert report.vub == 0.0 and report.completion_state == "qualified-draft"


@pytest.mark.skipif(importlib.util.find_spec("z3") is None, reason="z3-solver is not installed (pip install 'g6[formal] @ git+https://github.com/basicisabc1994/g6solver')")
def test_check_z3_still_takes_a_model_with_no_unsafe_state() -> None:
    from g6.csf.smt import check_z3

    result = check_z3(_agent([]), "F(harm)")
    assert result.holds is True and result.completion_state == "qualified-draft"
