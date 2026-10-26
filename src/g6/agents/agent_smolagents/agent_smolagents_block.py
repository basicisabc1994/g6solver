# SPDX-License-Identifier: Apache-2.0
"""AgentSmolagentsBlock: the registry and MCP door (``g6_invoke``) to the smolagents adapter.

* ``check(agent, task)``: the agent as G6 would run it (its kind, its steps after the trusted policy's ceiling, the
  sandbox a code step runs in and the imports it allows, the operating-system isolation a code step would run under
  here or why there is none (read from the access lists alone, as ``g6 doctor`` reads it: nothing is started), the
  tools and what the gate does with each, whether smolagents is installed); nothing runs, no model is asked. A run's
  own ``detail.sandbox`` says how each of its code steps ran;
* ``run(agent, task, deadline_s, dry_run=true)``: run the agent on the model the person configured for G6
  (``g6.core.llm.provider_from_env``); without one it is refused. It asks a model, which may be a paid or online
  service, and a code agent runs model-written code in G6's sandbox, so it is sensitive: callable from Python, and
  over MCP only when the person who runs the server lists it in ``G6_MCP_SENSITIVE``. A dry run (the default) asks no
  model and runs nothing: it answers the plan; ``dry_run`` false runs the agent.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.agents._runtime import codes, effective_limits, run_agent
from g6.agents._runtime.model import DOES, NEEDS
from g6.agents.agent_smolagents.adapter import INSTALL, NOT_INSTALLED, SmolagentsRuntime, missing
from g6.agents.agent_smolagents.sandbox import ALLOWED_IMPORTS, MEM_MB, REFUSED_EVENTS
from g6.agents.agent_smolagents.spec import AgentSpec
from g6.core import confine
from g6.core.ai_block import AIBlock
from g6.core.llm import provider_from_env
from g6.core.maturity import component_maturity
from g6.core.reliability_envelope import WarningCard
from g6.core.result import Result
from g6.core.sandbox import interpreter
from g6.safety_engine.schema import RuntimePolicy

#: run asks the model the person configured, and a code agent runs model-written code in G6's sandbox.
SENSITIVE_OPS: tuple[str, ...] = ("run",)
CHECKED = "G6_E_AGENT_SMOLAGENTS_CHECKED"
#: What holds for a code step, said by check and by the skill: exactly this, and no more.
HOLDS = "a restricted syntax plus a bounded child process with a clean environment and a deadline"
NOT_A_BOUNDARY = "not a security boundary: run agents you do not trust inside your own sandbox (a container or VM)"
AGENT_REQUIRED = "G6_E_AGENT_SMOLAGENTS_AGENT_REQUIRED"
_OPS: dict[str, str] = {
    "run": "run(agent, task, deadline_s=600, dry_run=true): run the agent on the model you configured for G6, code "
           "steps in G6's sandbox and every tool call decided by the gate; a dry run, the default, asks no model and "
           "answers the plan (sensitive: it asks a model and runs model-written code; from Python, or over MCP when "
           "the server's G6_MCP_SENSITIVE lists it)",
    "check": "check(agent, task): the agent as G6 would run it, its sandbox, its allowed imports and what the gate does "
             "with each tool; nothing is run",
}
_ACTIONS = {
    NOT_INSTALLED: "Install the extra: pip install 'g6[agents-smolagents] @ git+https://github.com/basicisabc1994/g6solver'.",
    CHECKED: "Run it with run, from Python or where the server's owner allows it.",
    AGENT_REQUIRED: "Give the agent (its kind, tools and limits) and the task.",
    "G6_E_AGENT_SMOLAGENTS_NO_MODEL": "Configure a model for G6 (G6_LLM_PROVIDER, with its key in your key file), or "
                                      "run from Python with a g6.core.llm caller.",
    "G6_E_AGENT_SMOLAGENTS_TOOL_MISSING": "Pass each declared tool as a smolagents Tool of that name, or name a G6 op.",
}


class AgentSmolagentsInput(BaseModel):
    """One op's parameters; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["run", "check", "ops", "help"] = "help"
    agent: AgentSpec | None = None
    task: str = Field("", max_length=100_000)
    deadline_s: float = Field(600.0, gt=0, le=900, allow_inf_nan=False)
    dry_run: bool = True
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _reply(op: str, state: str, code: str, message: str, data: AgentSmolagentsInput, **fields: Any) -> dict[str, Any]:
    card = WarningCard(code=code, message=message, suggested_action=_ACTIONS.get(code, codes.action(code)),
                       severity="blocked" if state == "blocked-escalated" else "amber").model_dump()
    return {"component": "agent_smolagents", "op": op, **fields, "code": code, "message": message,
            "completion_state": state, "warning_card": card,
            "evidence": [{"kind": f"agent_smolagents_{op}", "code": code, **fields}], "request_id": data.request_id,
            "run_id": data.run_id}


def _isolation() -> dict[str, Any]:
    """The operating-system isolation a code step would run under here (``g6.core.confine.backend`` for the sandbox's
    interpreter, with no test start): the backend, or none and why. Nothing is started or created."""
    python = interpreter()
    if python is None:
        return {"backend": None, "why": "no Python interpreter was found (set G6_PYTHON)"}
    name, why = confine.backend(python, probe=False)
    return {"backend": name, "why": why}


def _check(data: AgentSmolagentsInput) -> dict[str, Any]:
    if data.agent is None or not data.task.strip():
        return _reply("check", "blocked-escalated", AGENT_REQUIRED, "there is no agent, or no task for it", data)
    spec = data.agent
    task = SmolagentsRuntime(spec, task=data.task, deadline_s=data.deadline_s).task()
    tools = []
    for tool in task.tools:
        need = NEEDS.get(tool.effect, "")
        granted = (not need or bool(getattr(task.permissions, need))) and (not tool.online or task.permissions.network)
        tools.append({"name": tool.name, "effect": tool.effect, "does": DOES[tool.effect], "permitted": granted,
                      "gated": tool.gated, "outward": tool.outward})
    limits = effective_limits(task.limits, RuntimePolicy())
    sandbox = None
    if spec.agent_type == "code":
        isolation = _isolation()
        sandbox = {"holds": HOLDS, "security_boundary": False, "advice": NOT_A_BOUNDARY,
                   "timeout_s": spec.code_timeout_s, "mem_mb": MEM_MB, "network": "refused",
                   "process": "another Python process, its whole tree ended at the time limit",
                   "audit_hook_refuses": sorted(REFUSED_EVENTS) + ["ctypes"],
                   "os_sandbox": isolation["backend"] is not None, "isolation": isolation}
    installed = not missing()
    message = (f"A {spec.agent_type} agent of at most {limits.max_steps} steps"
               + (f": each code step is held to {HOLDS}; {NOT_A_BOUNDARY}" if spec.agent_type == "code" else
                  ", every tool call decided by the gate") + ". Nothing was run." + ("" if installed else
                                                                                     f" {INSTALL}."))
    return _reply("check", "qualified-draft", CHECKED, message, data, agent_type=spec.agent_type,
                  limits=limits.model_dump(), tools=tools, sandbox=sandbox,
                  allowed_imports=list(ALLOWED_IMPORTS) if spec.agent_type == "code" else [],
                  smolagents_installed=installed)


def _run(data: AgentSmolagentsInput) -> dict[str, Any]:
    if data.agent is None or not data.task.strip():
        return _reply("run", "blocked-escalated", AGENT_REQUIRED, "there is no agent, or no task for it", data)
    runtime = SmolagentsRuntime(data.agent, task=data.task, llm=provider_from_env(), deadline_s=data.deadline_s)
    outcome = run_agent(runtime, runtime.task(), dry_run=data.dry_run, request_id=data.request_id, run_id=data.run_id)
    if outcome.code == NOT_INSTALLED:
        return _reply("run", "blocked-escalated", NOT_INSTALLED, INSTALL, data)
    return {"component": "agent_smolagents", "op": "run", **outcome.envelope()}


@component_maturity(
    "beta",
    reason="runs smolagents tool-calling and code agents on the model passed in, code steps screened and run in G6's "
           "sandbox, every tool call decided by the family's gate; an answer is a qualified draft",
)
class AgentSmolagentsBlock(AIBlock[AgentSmolagentsInput, dict, dict]):
    """Check and run smolagents agents under the family's runtime. Never ``verified``."""

    def __init__(self, name: str = "agent_smolagents", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: AgentSmolagentsInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        return Result.ok(_check(data) if data.op == "check" else _run(data))


__all__ = ["CHECKED", "HOLDS", "NOT_A_BOUNDARY", "SENSITIVE_OPS", "AgentSmolagentsBlock", "AgentSmolagentsInput"]