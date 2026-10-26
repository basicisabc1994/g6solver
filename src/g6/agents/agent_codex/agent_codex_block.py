# SPDX-License-Identifier: Apache-2.0
"""AgentCodexBlock: the registry and MCP door (``g6_invoke``) to the Codex adapter.

Both ops start the codex program the person installed (``run`` with ``dry_run`` false, which also spends their Codex
subscription and, with ``writes``, changes files), so both are in :data:`SENSITIVE_OPS`: callable from Python, and over
MCP only when the person who runs the server lists them in ``G6_MCP_SENSITIVE``. ``ops`` and ``help`` answer over MCP.

``run`` runs one task through ``g6.agents._runtime.run_agent``: a goal, the workspace (a full path; there is no
default, so G6 never falls back to the folder it runs in), ``writes`` (false: Codex's read-only sandbox), an optional
model, a deadline
(at most the trusted policy's ceiling of 900 seconds) and ``dry_run`` (true: the run is decided and its command line
shown, nothing starts, and Codex need not be installed). A write run needs the workspace under version control. Every
answer is the runtime's outcome in the library envelope: ``qualified-draft`` at best,
with what Codex said kept as ``claimed_status``. One circuit breaker per process refuses Codex for a minute after three
failures in a row. ``available`` runs Codex's version check, through the same bounded runner, and not while G6 is
stopped.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.agents._runtime import CircuitBreaker, codes, run_agent
from g6.agents.agent_codex.adapter import CodexRuntime
from g6.core.ai_block import AIBlock
from g6.core.maturity import component_maturity
from g6.core.outside import OUTSIDE_PROGRAM_MISSING
from g6.core.reliability_envelope import WarningCard
from g6.core.result import Result

#: Both start the codex program you installed: from Python, or over MCP when the server's G6_MCP_SENSITIVE lists them.
SENSITIVE_OPS: tuple[str, ...] = ("run", "available")
FOUND = "G6_E_AGENT_CODEX_FOUND"
GOAL_REQUIRED = "G6_E_AGENT_CODEX_GOAL_REQUIRED"
_PY = " (sensitive: it starts the codex program you installed; from Python, or over MCP when the server's G6_MCP_SENSITIVE lists it)"
_OPS: dict[str, str] = {
    "run": "run(goal, workspace, writes=false, model, context, deadline_s=600, dry_run=true): one Codex run on the "
           "workspace, read-only unless writes (which need the workspace under version control), a dry run, which "
           "starts nothing, unless dry_run is false" + _PY,
    "available": "available(): whether Codex answers its version check, and which Codex it is" + _PY,
}
_ACTIONS = {
    FOUND: "Run a task with run; start with a dry run to see the command line.",
    GOAL_REQUIRED: "Say what Codex should do in goal.",
    OUTSIDE_PROGRAM_MISSING: "Install OpenAI's Codex yourself as the reason says, or set G6_CODEX to it.",
}
#: Three failures in a row (an error exit, a deadline, a cleanup not confirmed) keep Codex off for a minute.
_BREAKER = CircuitBreaker(failure_threshold=3, cooldown_s=60.0)


class AgentCodexInput(BaseModel):
    """One op's parameters; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["run", "available", "ops", "help"] = "help"
    goal: str = Field("", max_length=100_000)
    context: str = Field("", max_length=200_000)
    workspace: str = Field("", max_length=1000)
    writes: bool = False
    model: str = Field("", max_length=256, pattern=r"^(|[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255})$")
    deadline_s: float = Field(600.0, gt=0, le=900, allow_inf_nan=False)
    dry_run: bool = True
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _reply(op: str, state: str, code: str, message: str, data: AgentCodexInput, **fields: Any) -> dict[str, Any]:
    card = WarningCard(code=code, message=message, suggested_action=_ACTIONS.get(code, codes.action(code)),
                       severity="blocked" if state == "blocked-escalated" else "amber").model_dump()
    return {"component": "agent_codex", "op": op, **fields, "code": code, "message": message,
            "completion_state": state, "warning_card": card,
            "evidence": [{"kind": f"agent_codex_{op}", "code": code, **fields}], "request_id": data.request_id,
            "run_id": data.run_id}


def _run(data: AgentCodexInput) -> dict[str, Any]:
    if not data.goal.strip():
        return _reply("run", "blocked-escalated", GOAL_REQUIRED, "there is no goal to give Codex", data)
    if not data.workspace.strip():
        return _reply("run", "blocked-escalated", codes.WORKSPACE, "Codex needs a workspace: the full path of the "
                      "project folder it works in. G6 never falls back to the folder it runs in.", data)
    task = CodexRuntime.task(data.goal, data.workspace, writes=data.writes, deadline_s=data.deadline_s,
                             context=data.context)
    outcome = run_agent(CodexRuntime(model=data.model), task, dry_run=data.dry_run, breaker=_BREAKER,
                        request_id=data.request_id, run_id=data.run_id)
    return {"component": "agent_codex", "op": "run", **outcome.envelope()}


def _available(data: AgentCodexInput) -> dict[str, Any]:
    found = CodexRuntime().available()
    if not found.found:
        return _reply("available", "blocked-escalated", found.code or OUTSIDE_PROGRAM_MISSING, found.detail, data)
    return _reply("available", "qualified-draft", FOUND, f"{found.where} answered {found.version!r}. This says which "
                  "Codex a run would start; it ran no task.", data, where=found.where, version=found.version)


@component_maturity(
    "beta",
    reason="starts OpenAI's Codex, which you installed, read-only unless you grant writes, in the workspace you name, "
           "with a clean environment and a deadline; what Codex reports is a claim, a qualified draft at best",
)
class AgentCodexBlock(AIBlock[AgentCodexInput, dict, dict]):
    """Hand a task to the Codex command-line agent under the family's runtime. Never ``verified``."""

    def __init__(self, name: str = "agent_codex", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: AgentCodexInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        return Result.ok(_run(data) if data.op == "run" else _available(data))


__all__ = ["FOUND", "GOAL_REQUIRED", "SENSITIVE_OPS", "AgentCodexBlock", "AgentCodexInput"]
