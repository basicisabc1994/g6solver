# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The cost axis of the hard runtime ceilings (Phase E Part 1, E1c; Ledger §20).

Pinned: an action is projected at what was charged so far plus the most it may cost, against the trusted
``max_cost_cents``; a breach is a BLOCK naming the cost ceiling; what an executed action cost is charged to the run;
planner data can never lower the projection (a negative ceiling counts as 0); an action that declares no cost is
unchanged, even at a zero ceiling. Offline.
"""
from __future__ import annotations

from g6.safety_engine.ceilings import COST_CEILING, exceeds_ceiling
from g6.safety_engine.policy_engine import PolicyEngine
from g6.safety_engine.schema import ActionRequest, RuntimePolicy
from g6.safety_engine.state import RunState

POLICY = RuntimePolicy(max_cost_cents=50)


def test_an_action_that_fits_passes_and_one_that_does_not_is_blocked():
    state = RunState(policy=POLICY)
    assert exceeds_ceiling(ActionRequest(tool_name="t", cost_ceiling_cents=50), state, POLICY) == (False, "")
    exceeded, reason = exceeds_ceiling(ActionRequest(tool_name="t", cost_ceiling_cents=51), state, POLICY)
    assert exceeded and reason.startswith(COST_CEILING) and "51 > 50" in reason


def test_what_was_charged_counts_against_the_next_action():
    state = RunState(policy=POLICY, cost_cents=20)
    state.record_cost(25)
    assert state.cost_cents == 45
    assert exceeds_ceiling(ActionRequest(tool_name="t", cost_ceiling_cents=5), state, POLICY)[0] is False
    assert exceeds_ceiling(ActionRequest(tool_name="t", cost_ceiling_cents=6), state, POLICY)[0] is True


def test_planner_data_cannot_lower_the_projection():
    at_limit = RunState(policy=POLICY, cost_cents=50)
    assert exceeds_ceiling(ActionRequest(tool_name="t", cost_ceiling_cents=-40), at_limit, POLICY)[0] is False
    over = RunState(policy=POLICY, cost_cents=51)
    assert exceeds_ceiling(ActionRequest(tool_name="t", cost_ceiling_cents=-40), over, POLICY)[0] is True
    at_limit.record_cost(-10)
    assert at_limit.cost_cents == 50


def test_an_action_that_declares_no_cost_is_unchanged_even_at_a_zero_ceiling():
    zero = RuntimePolicy(max_cost_cents=0)
    assert exceeds_ceiling(ActionRequest(tool_name="t"), RunState(policy=zero), zero) == (False, "")


def test_the_policy_engine_blocks_on_the_cost_axis_and_names_it():
    engine = PolicyEngine()
    state = RunState(policy=POLICY, run_id="r", cost_cents=40)
    decision = engine.authorize_action(ActionRequest(tool_name="t", cost_ceiling_cents=30), state, POLICY)
    assert decision.decision == "BLOCK" and decision.permitted_action is None
    assert decision.policy_checks[-1] == "ceiling" and COST_CEILING in decision.reason
