# SPDX-License-Identifier: Apache-2.0
"""AgentAutoGenBlock: the registry and MCP door (``g6_invoke``) to the AutoGen adapter.

* ``check_team(team, task, max_tool_calls...)``: the team as G6 would run it (its pattern and agents, the most
  messages after the trusted policy's ceiling, the tools and what the gate does with each, whether AutoGen is
  installed); nothing runs, no model is asked;
* ``run_team(team, task, deadline_s, dry_run=true)``: run the team on the model the person configured for G6
  (``g6.core.llm.provider_from_env``: ``G6_LLM_PROVIDER``, with any key from the person's key file); without one it is
  refused. It asks a model, which may be a paid or online service, so it is sensitive: callable from Python, and over
  MCP only when the person who runs the server lists it in ``G6_MCP_SENSITIVE``. A dry run (the default) asks no model
  and runs nothing: it answers the plan; ``dry_run`` false runs the team.

Each run answer is the runtime's outcome in the library envelope, ``qualified-draft`` at best.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.agents._runtime import codes, effective_limits, run_agent
from g6.agents._runtime.model import DOES, NEEDS
from g6.agents.agent_autogen.adapter import INSTALL, NOT_INSTALLED, AutoGenRuntime, missing
from g6.agents.agent_autogen.spec import TeamSpec
from g6.core.ai_block import AIBlock
from g6.core.llm import provider_from_env
from g6.core.maturity import component_maturity
from g6.core.reliability_envelope import WarningCard
from g6.core.result import Result
from g6.safety_engine.schema import RuntimePolicy

#: run_team asks the model the person configured, which may be a paid or online service.
SENSITIVE_OPS: tuple[str, ...] = ("run_team",)
CHECKED = "G6_E_AGENT_AUTOGEN_TEAM_CHECKED"
TEAM_REQUIRED = "G6_E_AGENT_AUTOGEN_TEAM_REQUIRED"
_OPS: dict[str, str] = {
    "run_team": "run_team(team, task, deadline_s=600, dry_run=true): run the team on the model you configured for "
                "G6, every tool call decided by the gate, stopped at its message limit; a dry run, the default, asks "
                "no model and answers the plan (sensitive: it asks a model, which may be paid or online; from Python, "
                "or over MCP when the server's G6_MCP_SENSITIVE lists it)",
    "check_team": "check_team(team, task): the team as G6 would run it, its limits capped by the trusted ceilings and "
                  "what the gate does with each tool; nothing is run",
}
_ACTIONS = {
    NOT_INSTALLED: "Install the extra: pip install 'g6[agents-autogen]'.",
    CHECKED: "Run it with run_team, from Python or where the server's owner allows it; start with dry_run true.",
    TEAM_REQUIRED: "Give the team (its pattern and agents) and the task.",
    "G6_E_AGENT_AUTOGEN_NO_MODEL": "Configure a model for G6 (G6_LLM_PROVIDER, with its key in your key file), or run "
                                   "from Python with a g6.core.llm caller.",
    "G6_E_AGENT_AUTOGEN_UNCHECKED_TEAM": "Build the team from plain AssistantAgent participants with G6ChatClient and "
                                         "GatedTool only.",
}


class AgentAutoGenInput(BaseModel):
    """One op's parameters; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["run_team", "check_team", "ops", "help"] = "help"
    team: TeamSpec | None = None
    task: str = Field("", max_length=100_000)
    deadline_s: float = Field(600.0, gt=0, le=900, allow_inf_nan=False)
    dry_run: bool = True
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _reply(op: str, state: str, code: str, message: str, data: AgentAutoGenInput, **fields: Any) -> dict[str, Any]:
    card = WarningCard(code=code, message=message, suggested_action=_ACTIONS.get(code, codes.action(code)),
                       severity="blocked" if state == "blocked-escalated" else "amber").model_dump()
    return {"component": "agent_autogen", "op": op, **fields, "code": code, "message": message,
            "completion_state": state, "warning_card": card,
            "evidence": [{"kind": f"agent_autogen_{op}", "code": code, **fields}], "request_id": data.request_id,
            "run_id": data.run_id}


def _check(data: AgentAutoGenInput) -> dict[str, Any]:
    if data.team is None or not data.task.strip():
        return _reply("check_team", "blocked-escalated", TEAM_REQUIRED, "there is no team, or no task for it", data)
    runtime = AutoGenRuntime(data.team, task=data.task, deadline_s=data.deadline_s)
    task, team = runtime.task(), data.team
    tools = []
    for spec in task.tools:
        need = NEEDS.get(spec.effect, "")
        granted = (not need or bool(getattr(task.permissions, need))) and (not spec.online or task.permissions.network)
        tools.append({"name": spec.name, "effect": spec.effect, "does": DOES[spec.effect], "permitted": granted,
                      "gated": spec.gated, "outward": spec.outward,
                      "agents": [a.name for a in team.agents if spec.name in a.tools]})
    installed = not missing()
    message = (f"A {team.pattern} team of {len(team.agents)} agent(s), stopped after at most "
               f"{effective_limits(task.limits, RuntimePolicy()).max_steps} messages; every tool call is decided by the "
               "gate. Nothing was run." + ("" if installed else f" {INSTALL}."))
    return _reply("check_team", "qualified-draft", CHECKED, message, data, pattern=team.pattern,
                  agents=[a.name for a in team.agents], limits=effective_limits(task.limits, RuntimePolicy()).model_dump(),
                  tools=tools, stop_text=team.stop_text, autogen_installed=installed)


def _run(data: AgentAutoGenInput) -> dict[str, Any]:
    if data.team is None or not data.task.strip():
        return _reply("run_team", "blocked-escalated", TEAM_REQUIRED, "there is no team, or no task for it", data)
    runtime = AutoGenRuntime(data.team, task=data.task, llm=provider_from_env(), deadline_s=data.deadline_s)
    outcome = run_agent(runtime, runtime.task(), dry_run=data.dry_run, request_id=data.request_id, run_id=data.run_id)
    if outcome.code == NOT_INSTALLED:
        return _reply("run_team", "blocked-escalated", NOT_INSTALLED, INSTALL, data)
    return {"component": "agent_autogen", "op": "run_team", **outcome.envelope()}


@component_maturity(
    "beta",
    reason="runs AutoGen round robin, selector and swarm teams on the model passed in, every tool call decided by the "
           "family's gate, stopped at a message limit the trusted policy caps; a finished team is a qualified draft",
)
class AgentAutoGenBlock(AIBlock[AgentAutoGenInput, dict, dict]):
    """Check and run AutoGen teams under the family's runtime. Never ``verified``."""

    def __init__(self, name: str = "agent_autogen", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: AgentAutoGenInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        return Result.ok(_check(data) if data.op == "check_team" else _run(data))


__all__ = ["CHECKED", "SENSITIVE_OPS", "AgentAutoGenBlock", "AgentAutoGenInput"]