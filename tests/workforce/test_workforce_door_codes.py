# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""A refusal at the single door carries its stable code (Gate W's W-A, spec amendment 2026-10-05; plan
2026-10-05-gate-w-authority-ladder, Task 3).

Measured before: ``Door.reply`` answered a refusal with ``blocked-escalated`` and its reason only (the W0 finding,
``calls.py:210-215``), so a malformed call refused at the door carried no ``G6_E_*`` code. Pinned: a malformed call,
a sensitive op and a call that timed out each answer the code a person would read for it (``classes.surface_code``),
nothing malformed reaches the component, and a good call's answer is unchanged. Offline, fakes.
"""
from __future__ import annotations

from g6.safety_engine.schema import RuntimePolicy
from g6.workforce import calls as wcall
from g6.workforce import capabilities as wcap
from g6.workforce import workers as ww

LEDGER = wcap.ParamContract.from_dict({
    "component": "fake_ledger", "op": "total", "required": {"numbers": "list"}, "probe_params": {"numbers": [1]},
    "expect_field": "total", "side_effect": "read", "check": "input_model"})
STRIPE = wcap.ParamContract.from_dict({
    "component": "billing_stripe", "op": "status", "required": {}, "probe_params": {}, "expect_field": "status",
    "side_effect": "read", "check": "input_model"})
CONTRACTS = {("fake_ledger", "total"): LEDGER, ("billing_stripe", "status"): STRIPE}


def _door(invoke) -> wcall.Door:
    allowed = tuple(CONTRACTS)
    policy = RuntimePolicy(max_shell_commands=0, max_files_modified=0, max_lines_changed=0,
                           allowed_tools=frozenset(f"{c}.{o}" for c, o in allowed))
    worker = ww.WorkerSpec(template="verifier", purpose="p", rung="verify", allowed=allowed, policy=policy,
                           read_only=True)
    return wcall.Door(worker, contracts=lambda c, o: CONTRACTS.get((c, o)), run_id="run_codes", invoke=invoke)


def test_a_malformed_call_is_refused_with_its_code_and_never_reaches_the_component():
    reached = []
    out = _door(lambda c, o, p: reached.append((c, o)) or {"ok": True}).reply(
        "fake_ledger", "total", {"numbers": [1], "colour": "red"})
    assert (out["ok"], out["completion_state"], out["code"]) == (False, "blocked-escalated", "G6_E_MISSING_PARAM")
    assert "unknown key(s) ['colour']" in out["error"] and reached == []




def test_a_call_that_timed_out_says_so_with_its_code():
    def slow(component, op, params):
        raise TimeoutError("the ledger took too long")

    out = _door(slow).reply("fake_ledger", "total", {"numbers": [1]})
    assert (out["ok"], out["code"]) == (False, "G6_E_TIMEOUT")


def test_a_good_call_answers_as_the_component_said():
    answer = {"ok": True, "result": {"total": 1}}
    assert _door(lambda c, o, p: answer).reply("fake_ledger", "total", {"numbers": [1]}) == answer
