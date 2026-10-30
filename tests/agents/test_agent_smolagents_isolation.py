# SPDX-License-Identifier: Apache-2.0
"""How agent_smolagents says a code agent's code ran: confined by the operating system (``g6.core.confine``) or not,
read from what each run in ``g6.core.sandbox`` applied, never a constant. These need no smolagents: the sandbox
session and ``check`` run without it. An unconfined run is said to be one, and nothing here raises a label."""
from __future__ import annotations

import json

import pytest

import g6.agents.agent_smolagents.sandbox as sandbox_mod
from g6.agents.agent_smolagents.sandbox import CodeFailed, SandboxSession
from g6.core import confine
from g6.core.sandbox import ScriptRun
from g6.mcp import invoke_reply

CODE = {"agent_type": "code", "max_steps": 4, "code_timeout_s": 20}
OFF = "unavailable: switched off by this test"
CONFINED = {"backend": "appcontainer", "why": "", "network": False, "container": "S-1-15-2-1-2-3",
            "profile": "g6.confined.test"}


def _ran(isolation: dict, *, status: str = "ok") -> ScriptRun:
    """What ``g6.core.sandbox.run_script`` returns for one step: its status, the limits it applied, its isolation."""
    limits = {"timeout_s": 20, "mem_mb": 512, "os_sandbox": bool(isolation.get("backend"))}
    return ScriptRun(status, 0 if status == "ok" else None, "", "" if status == "ok" else "refused", {}, 0.1, limits,
                     isolation)


def _returns(monkeypatch: pytest.MonkeyPatch, *runs: ScriptRun) -> None:
    queue = list(runs)
    monkeypatch.setattr(sandbox_mod, "run_script", lambda *args, **kwargs: queue.pop(0))


def test_a_session_that_ran_no_code_says_no_code_was_run() -> None:
    record = SandboxSession().record()
    # fails if SandboxSession.record says anything ran confined when nothing ran
    assert record["os_sandbox"] is False and record["runs"] == []
    assert (record["isolation"]["backend"], record["isolation"]["why"]) == (None, "no untrusted code was run")


def test_a_confined_step_is_recorded_with_its_backend_and_the_limits_it_ran_under(monkeypatch) -> None:
    _returns(monkeypatch, _ran(CONFINED))
    session = SandboxSession()
    session.run("x = 1")
    record = session.record()
    # fails if SandboxSession.run stops keeping the limits and isolation run_script applied (self.applied.append)
    assert record["os_sandbox"] is True and record["isolation"]["backend"] == "appcontainer"
    assert record["runs"] == [{"timeout_s": 20, "mem_mb": 512, "os_sandbox": True}]


def test_one_unconfined_step_makes_the_whole_record_unconfined_and_names_why(monkeypatch) -> None:
    _returns(monkeypatch, _ran(CONFINED), _ran({"backend": None, "why": OFF}))
    session = SandboxSession()
    session.run("x = 1")
    session.run("y = 2")
    record = session.record()
    # fails if SandboxSession.record lets one confined step speak for a run that also had an unconfined one
    assert record["os_sandbox"] is False and record["isolation"] == {"backend": None, "why": OFF}
    assert [run["os_sandbox"] for run in record["runs"]] == [True, False]


def test_a_step_the_sandbox_refused_is_in_the_record_too(monkeypatch) -> None:
    why = "[G6_E_ISOLATION_UNAVAILABLE] G6_REQUIRE_ISOLATION is set and no operating-system isolation can confine it"
    _returns(monkeypatch, _ran({"backend": None, "why": why}, status="refused"))
    session = SandboxSession()
    with pytest.raises(CodeFailed):
        session.run("x = 1")
    record = session.record()
    # fails if SandboxSession.run records a step only when it ran cleanly
    assert record["os_sandbox"] is False and record["isolation"]["why"] == why and len(record["runs"]) == 1


def test_a_real_step_with_no_backend_is_recorded_unconfined_with_the_reason(monkeypatch) -> None:
    monkeypatch.setattr(confine, "_select", lambda: (None, OFF))
    monkeypatch.setenv(confine.ALLOW, "1")   # and the person allowed unconfined runs, so this one goes ahead
    session = SandboxSession(timeout_s=30)
    output, printed, final = session.run("print(6 * 7)")
    record = session.record()
    assert "42" in printed and final is False
    # fails if SandboxSession.record is not built from the ScriptRun g6.core.sandbox.run_script returned
    assert record["os_sandbox"] is False and (record["isolation"]["backend"], record["isolation"]["why"]) == (None, OFF)
    assert record["runs"][0]["os_sandbox"] is False and record["runs"][0]["mem_mb"] == sandbox_mod.MEM_MB


@pytest.mark.parametrize(("found", "confined"), [(("appcontainer", ""), True), ((None, OFF), False)])
def test_check_names_the_backend_a_code_step_would_run_under_or_why_there_is_none(monkeypatch, found,
                                                                                   confined) -> None:
    asked: list[bool] = []

    def backend(python: str | None = None, *, probe: bool = True) -> tuple[str | None, str]:
        asked.append(probe)
        return found

    monkeypatch.setattr(confine, "backend", backend)
    result = invoke_reply("agent_smolagents", "check", json.dumps({"agent": CODE, "task": "x"}))["result"]
    sandbox = result["sandbox"]
    # fails if agent_smolagents_block._check writes "os_sandbox": False as a constant again
    assert sandbox["os_sandbox"] is confined and sandbox["isolation"] == {"backend": found[0], "why": found[1]}
    assert asked == [False]                          # read from the access lists alone: check starts nothing
    assert result["completion_state"] == "qualified-draft"   # confined or not, check is a draft, never more


def test_check_of_a_tool_calling_agent_has_no_sandbox_to_read(monkeypatch) -> None:
    monkeypatch.setattr(confine, "backend", lambda *args, **kwargs: pytest.fail("no code step, nothing to read"))
    agent = {"agent_type": "toolcalling", "max_steps": 4, "tools": []}
    result = invoke_reply("agent_smolagents", "check", json.dumps({"agent": agent, "task": "x"}))["result"]
    assert result["sandbox"] is None
