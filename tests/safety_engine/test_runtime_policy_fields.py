# SPDX-License-Identifier: Apache-2.0
"""Every RuntimePolicy field is read, and only the switch's owner can reset it. Offline.

``max_steps`` bounds the decisions a run may ask for, allowed or refused; ``require_tests_before_final`` holds an
action marked final until the run has recorded a passing test run; a kill switch resets only with the token handed
to whoever made it, once, and a default switch cannot be reset at all.
"""
from __future__ import annotations

import pytest

from g6.safety_engine import kill_switch
from g6.safety_engine.kill_switch import KillSwitch
from g6.safety_engine.policy_engine import PolicyEngine
from g6.safety_engine.schema import ActionRequest, RuntimePolicy
from g6.safety_engine.state import RunState


def test_max_steps_bounds_the_decisions_a_run_asks_for() -> None:
    policy = RuntimePolicy(max_steps=2, allowed_tools=frozenset({"ok"}))
    state, engine = RunState(policy=policy), PolicyEngine()
    first = engine.authorize_action(ActionRequest(tool_name="ok"), state, policy)
    refused = engine.authorize_action(ActionRequest(tool_name="other"), state, policy)  # a refusal is a step too
    third = engine.authorize_action(ActionRequest(tool_name="ok"), state, policy)
    assert (first.decision, refused.decision) == ("ALLOW", "BLOCK")
    assert third.decision == "BLOCK" and "step ceiling" in third.reason and third.policy_checks[-1] == "ceiling"
    assert (state.steps, state.tool_calls) == (2, 0)  # steps are decisions asked for, not calls recorded


def test_a_final_action_waits_for_a_passing_test_run() -> None:
    policy = RuntimePolicy()
    state, engine = RunState(policy=policy), PolicyEngine()
    final = ActionRequest(tool_name="answer", is_final=True)
    held = engine.authorize_action(final, state, policy)
    assert held.decision == "BLOCK" and held.policy_checks[-1] == "tests_before_final"
    state.record_tests(passed=True)
    assert engine.authorize_action(final, state, policy).decision == "ALLOW"
    state.record_tests(passed=False)  # a later failing run holds it again
    assert engine.authorize_action(final, state, policy).decision == "BLOCK"
    assert engine.authorize_action(ActionRequest(tool_name="answer"), state, policy).decision == "ALLOW"


@pytest.mark.parametrize("claim", ["yes", 1, "True", None])
def test_only_a_boolean_true_records_a_passing_test_run(claim) -> None:
    state = RunState(policy=RuntimePolicy())
    state.record_tests(passed=claim)
    assert state.tests_passed is False


def test_a_policy_that_does_not_require_tests_lets_a_final_action_run() -> None:
    policy = RuntimePolicy(require_tests_before_final=False)
    decision = PolicyEngine().authorize_action(ActionRequest(tool_name="answer", is_final=True),
                                               RunState(policy=policy), policy)
    assert decision.decision == "ALLOW"


def test_no_module_function_hands_out_a_reset_capability() -> None:
    assert not hasattr(kill_switch, "trusted_reset_token")
    switch = KillSwitch()
    switch.trip("stopped")
    for token in (None, b"", object(), "token"):
        with pytest.raises(PermissionError):
            switch.reset(token)
    assert switch.tripped and switch.reason == "stopped"


def test_a_kill_switch_resets_only_with_its_owners_token() -> None:
    switch, token = KillSwitch.with_reset_token()
    _other, other_token = KillSwitch.with_reset_token()
    switch.trip("stopped")
    with pytest.raises(PermissionError):
        switch.reset(other_token)
    assert switch.tripped
    switch.reset(token)
    assert not switch.tripped and switch.reason == ""


def test_the_switch_keeps_no_copy_of_its_reset_token() -> None:
    switch, token = KillSwitch.with_reset_token()
    assert token not in [getattr(switch, name) for name in KillSwitch.__slots__]  # a digest only
