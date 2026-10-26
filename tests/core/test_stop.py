# SPDX-License-Identifier: Apache-2.0
"""One process-wide stop (audit 007 SE-010): engaged in process, by ``G6_STOP`` or by ``$G6_HOME/STOP``, each place
that consults it refuses before it starts work, and a contained call already running is interrupted. Each test has
its own ``G6_HOME``, so a stop file can never leak into the rest of the suite. Offline, fakes.
"""
from __future__ import annotations

import time

import pytest

from g6.core import component_process, stop
from g6.safety_engine.policy_engine import PolicyEngine
from g6.safety_engine.schema import ActionRequest, RuntimePolicy
from g6.safety_engine.state import RunState
from g6.workforce import calls as wcall
from g6.workforce import capabilities as wcap
from g6.workforce import workers as ww

LEDGER = wcap.ParamContract.from_dict({
    "component": "fake_ledger", "op": "total", "required": {"numbers": "list"}, "probe_params": {"numbers": [1]},
    "expect_field": "total", "side_effect": "read", "check": "input_model"})


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    monkeypatch.delenv("G6_STOP", raising=False)
    stop.release()
    yield
    stop.release()


def _door(invoke) -> wcall.Door:
    pair = ("fake_ledger", "total")
    policy = RuntimePolicy(max_shell_commands=0, max_files_modified=0, max_lines_changed=0,
                           allowed_tools=frozenset({"fake_ledger.total"}))
    worker = ww.WorkerSpec(template="verifier", purpose="p", rung="verify", allowed=(pair,), policy=policy,
                           read_only=True)
    return wcall.Door(worker, contracts=lambda c, o: LEDGER if (c, o) == pair else None, run_id="run_stop",
                      invoke=invoke)


def test_it_is_not_engaged_by_default():
    assert stop.engaged() is None and not stop.stopped()


def test_engaged_in_process_keeps_the_first_reason_until_released():
    stop.engage("first")
    stop.engage("second")
    assert stop.engaged() == "first"
    stop.release()
    assert stop.engaged() is None


def test_the_environment_engages_it(monkeypatch):
    monkeypatch.setenv("G6_STOP", "maintenance")
    assert stop.engaged() == "G6_STOP is set (maintenance)"


def test_a_stop_file_engages_it_from_outside_the_process():
    stop.stop_file().write_text("owner: pausing for review\n", encoding="utf-8")
    assert stop.engaged().endswith(": owner: pausing for review")
    stop.stop_file().unlink()
    assert stop.engaged() is None


def test_a_stop_file_that_cannot_be_read_counts_as_engaged():
    stop.stop_file().mkdir()  # it exists, and cannot be read as a file
    assert "could not be read" in stop.engaged()


def test_the_door_refuses_before_invoking():
    reached = []

    def invoke(component, op, params):
        reached.append(op)
        return {"ok": True, "result": {"total": 1}}

    assert _door(invoke).reply("fake_ledger", "total", {"numbers": [1]})["ok"] is True  # control: it runs
    stop.engage("an owner stop")
    out = _door(invoke).call("fake_ledger", "total", {"numbers": [1]})
    assert out.refusal_code == "refused:killed" and "an owner stop" in out.refused
    assert reached == ["total"], "the stopped call reached the component"
    assert _door(invoke).reply("fake_ledger", "total", {"numbers": [1]})["code"] == "G6_E_POLICY_BLOCKED"


def test_a_running_contained_call_is_interrupted():
    def invoke(component, op, params):
        stop.stop_file().write_text("stop now", encoding="utf-8")  # someone outside stops it mid-call
        for _ in range(100):
            component_process.checkpoint()
            time.sleep(.01)
        return {"ok": True, "result": {"total": 1}}

    started = time.monotonic()
    out = _door(invoke).call("fake_ledger", "total", {"numbers": [1]})
    assert out.raised == "timeout" and out.reply is None and time.monotonic() - started < .9


def test_the_policy_engine_blocks_first():
    policy = RuntimePolicy()
    action = ActionRequest(tool_name="t")
    assert PolicyEngine().authorize_action(action, RunState(policy=policy), policy).decision == "ALLOW"  # control
    stop.engage("an owner stop")
    decision = PolicyEngine().authorize_action(action, RunState(policy=policy), policy)
    assert (decision.decision, decision.policy_checks) == ("BLOCK", ("kill_switch",))
    assert "an owner stop" in decision.reason


def test_the_harness_runs_nothing():
    from g6.harness import Check, Standard, run

    ran = []
    standard = Standard(goal="Write a greeting", checks=(Check("says hello", lambda out: "hello" in out),))
    assert run(standard, executor=lambda contract: ran.append(1) or "hello")["completion_state"] != "blocked-escalated"
    stop.engage("an owner stop")
    found = run(standard, executor=lambda contract: ran.append(1) or "hello")
    assert found["completion_state"] == "blocked-escalated" and found["warning_card"]["code"] == stop.STOPPED
    assert ran == [1], "the executor ran while stopped"
