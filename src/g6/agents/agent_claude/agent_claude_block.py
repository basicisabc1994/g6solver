# SPDX-License-Identifier: Apache-2.0
"""AgentClaudeBlock: the registry and MCP door (``g6_invoke``) to the Claude Code adapter.

Both ops start the claude program the person installed (``run`` with ``dry_run`` false, which also spends their Claude
subscription and, with ``writes``, changes files), so both are in :data:`SENSITIVE_OPS`: callable from Python, and over
MCP only when the person who runs the server lists them in ``G6_MCP_SENSITIVE``. ``ops`` and ``help`` answer over MCP.

``run`` runs one task through ``g6.agents._runtime.run_agent``: a goal, the workspace (a full path; there is no
default, so G6 never falls back to the folder it runs in), ``writes`` (false: read tools only), an optional model,
``max_turns`` (at most 100), a deadline (at most the trusted policy's ceiling of 900 seconds) and ``dry_run`` (true:
the run is decided and its command line shown, nothing starts, and Claude Code need not be installed). A write run
needs the workspace under version control. Every answer is the runtime's outcome in the library envelope:
``qualified-draft`` at best, with what Claude Code said kept as ``claimed_status``. One circuit breaker per process
refuses Claude Code for a minute after three failures in a row. ``available`` runs Claude Code's version check, through
the same bounded runner, and not while G6 is stopped.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.agents._runtime import CircuitBreaker, codes, run_agent
from g6.agents.agent_claude.adapter import ClaudeCodeRuntime
from g6.core.ai_block import AIBlock
from g6.core.maturity import component_maturity
from g6.core.outside import OUTSIDE_PROGRAM_MISSING
from g6.core.reliability_envelope import WarningCard
from g6.core.result import Result

#: Both start the claude program you installed: from Python, or over MCP when the server's G6_MCP_SENSITIVE lists them.
SENSITIVE_OPS: tuple[str, ...] = ("run", "available")
FOUND = "G6_E_AGENT_CLAUDE_FOUND"
GOAL_REQUIRED = "G6_E_AGENT_CLAUDE_GOAL_REQUIRED"
_PY = (" (sensitive: it starts the claude program you installed; from Python, or over MCP when the server's "
       "G6_MCP_SENSITIVE lists it)")
_OPS: dict[str, str] = {
    "run": "run(goal, workspace, writes=false, model, context, max_turns=20, deadline_s=600, dry_run=true): one Claude "
           "Code run on the workspace, its read tools only unless writes (which need the workspace under version "
           "control), never its shell or web tools, a dry run, which starts nothing, unless dry_run is false" + _PY,
    "available": "available(): whether Claude Code answers its version check, and which Claude Code it is" + _PY,
}
_ACTIONS = {
    FOUND: "Run a task with run; start with a dry run to see the command line.",
    GOAL_REQUIRED: "Say what Claude Code should do in goal.",
    OUTSIDE_PROGRAM_MISSING: "Install Anthropic's Claude Code yourself as the reason says, or set G6_CLAUDE to it.",
}
#: Three failures in a row (an error exit, a deadline, a cleanup not confirmed) keep Claude Code off for a minute.
_BREAKER = CircuitBreaker(failure_threshold=3, cooldown_s=60.0)


class AgentClaudeInput(BaseModel):
    """One op's parameters; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["run", "available", "ops", "help"] = "help"
    goal: str = Field("", max_length=100_000)
    context: str = Field("", max_length=200_000)
    workspace: str = Field("", max_length=1000)
    writes: bool = False
    model: str = Field("", max_length=256, pattern=r"^(|[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255})$")
    max_turns: int = Field(20, ge=1, le=100)
    deadline_s: float = Field(600.0, gt=0, le=900, allow_inf_nan=False)
    dry_run: bool = True
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _reply(op: str, state: str, code: str, message: str, data: AgentClaudeInput, **fields: Any) -> dict[str, Any]:
    card = WarningCard(code=code, message=message, suggested_action=_ACTIONS.get(code, codes.action(code)),
                       severity="blocked" if state == "blocked-escalated" else "amber").model_dump()
    return {"component": "agent_claude", "op": op, **fields, "code": code, "message": message,
            "completion_state": state, "warning_card": card,
            "evidence": [{"kind": f"agent_claude_{op}", "code": code, **fields}], "request_id": data.request_id,
            "run_id": data.run_id}


def _run(data: AgentClaudeInput) -> dict[str, Any]:
    if not data.goal.strip():
        return _reply("run", "blocked-escalated", GOAL_REQUIRED, "there is no goal to give Claude Code", data)
    if not data.workspace.strip():
        return _reply("run", "blocked-escalated", codes.WORKSPACE, "Claude Code needs a workspace: the full path of the "
                      "project folder it works in. G6 never falls back to the folder it runs in.", data)
    task = ClaudeCodeRuntime.task(data.goal, data.workspace, writes=data.writes, deadline_s=data.deadline_s,
                                  context=data.context)
    outcome = run_agent(ClaudeCodeRuntime(model=data.model, max_turns=data.max_turns), task, dry_run=data.dry_run,
                        breaker=_BREAKER, request_id=data.request_id, run_id=data.run_id)
    reply = {"component": "agent_claude", "op": "run", **outcome.envelope()}
    held = outcome.detail.get("not_held")
    if held:   # what the flags cannot hold goes on the card, so it is read with the result (fail closed: said, not hidden)
        reply["warning_card"]["message"] += " Not held by G6's flags: " + " ".join(held)
    return reply


def _available(data: AgentClaudeInput) -> dict[str, Any]:
    found = ClaudeCodeRuntime().available()
    if not found.found:
        return _reply("available", "blocked-escalated", found.code or OUTSIDE_PROGRAM_MISSING, found.detail, data)
    return _reply("available", "qualified-draft", FOUND, f"{found.where} answered {found.version!r}. This says which "
                  "Claude Code a run would start; it ran no task.", data, where=found.where, version=found.version)


@component_maturity(
    "beta",
    reason="starts Anthropic's Claude Code, which you installed, with its read tools only unless you grant writes and "
           "never its shell or web tools, in the workspace you name, with a clean environment and a deadline; what it "
           "reports is a claim, a qualified draft at best",
)
class AgentClaudeBlock(AIBlock[AgentClaudeInput, dict, dict]):
    """Hand a task to the Claude Code command-line agent under the family's runtime. Never ``verified``."""

    def __init__(self, name: str = "agent_claude", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: AgentClaudeInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        return Result.ok(_run(data) if data.op == "run" else _available(data))


__all__ = ["FOUND", "GOAL_REQUIRED", "SENSITIVE_OPS", "AgentClaudeBlock", "AgentClaudeInput"]