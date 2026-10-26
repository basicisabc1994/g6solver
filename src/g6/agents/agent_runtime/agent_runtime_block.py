# SPDX-License-Identifier: Apache-2.0
"""AgentRuntimeBlock: the registry and MCP door (``g6_invoke``) to the agents family's runtime.

Two ops, both read-only and callable over MCP, and neither runs an agent or a tool:

* ``check_task``: the task as G6 would run it: the caller's limits capped by the trusted policy's ceilings, the
  permissions, and for each declared tool what it does, whether its calls need the network permission (an online tool
  does), whether it is permitted, whether the action gate sees its calls and whether its calls are outward (a dry run
  by default). A workspace that cannot be used is refused;
* ``decide``: the gate's decision for one proposed call within a task (``run``, ``dry_run`` or ``refused``, with its
  code and reason, and what ``g6.action_gating`` decided when it was asked). Each call is decided by a fresh gate, so
  ceilings that count calls across a run are not reached here; ``run_agent`` keeps one gate for a whole run.

A ``refused`` decision and every refusal of the op itself are ``blocked-escalated`` with their own code; any other
answer is ``qualified-draft``. Running an agent is Python (``run_agent`` with an adapter such as ``agent_codex``'s),
because an adapter starts a program or calls a model.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.agents._runtime import codes
from g6.agents._runtime.gate import ToolGate
from g6.agents._runtime.model import DOES, NEEDS, AgentTask, ToolCall, effective_limits, workspace_problem
from g6.core.ai_block import AIBlock
from g6.core.maturity import component_maturity
from g6.core.reliability_envelope import WarningCard
from g6.core.result import Result
from g6.safety_engine.schema import RuntimePolicy

#: None: neither op starts a program, reaches the network or changes the machine.
SENSITIVE_OPS: tuple[str, ...] = ()
_OPS: dict[str, str] = {
    "check_task": "check_task(task): the task as G6 would run it, its limits capped by the trusted ceilings, and what "
                  "each tool may do; nothing is run",
    "decide": "decide(task, call, dry_run=true): the gate's decision for one proposed tool call (run, dry_run or "
              "refused), with its code, its reason and what g6.action_gating decided; nothing is run",
}


class AgentRuntimeInput(BaseModel):
    """One op's parameters; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["check_task", "decide", "ops", "help"] = "help"
    task: AgentTask | None = None
    call: ToolCall | None = None
    dry_run: bool = True
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _reply(op: str, state: str, code: str, message: str, data: AgentRuntimeInput, *,
           evidence: dict[str, Any] | None = None, **fields: Any) -> dict[str, Any]:
    card = WarningCard(code=code, message=message, suggested_action=codes.action(code),
                       severity="blocked" if state == "blocked-escalated" else "amber").model_dump()
    return {"component": "agent_runtime", "op": op, **fields, "code": code, "completion_state": state,
            "warning_card": card, "evidence": [{"kind": f"agent_runtime_{op}", "code": code, **(evidence or {})}],
            "request_id": data.request_id, "run_id": data.run_id}


def _tools(task: AgentTask) -> list[dict[str, Any]]:
    out = []
    for spec in task.tools:
        need = NEEDS.get(spec.effect, "")
        granted = (not need or bool(getattr(task.permissions, need))) and (not spec.online or task.permissions.network)
        out.append({"name": spec.name, "effect": spec.effect, "does": DOES[spec.effect], "online": spec.online,
                    "needs": need, "network": spec.needs_network, "permitted": granted, "gated": spec.gated,
                    "outward": spec.outward})
    return out


def _check_task(data: AgentRuntimeInput) -> dict[str, Any]:
    if data.task is None:
        return _reply("check_task", "blocked-escalated", codes.TASK_REQUIRED, "there is no task to check", data)
    task, policy = data.task, RuntimePolicy()
    fields = {"limits": effective_limits(task.limits, policy).model_dump(),
              "ceilings": {"max_steps": policy.max_steps, "max_tool_calls": policy.max_tool_calls,
                           "max_runtime_seconds": policy.max_runtime_seconds},
              "permissions": task.permissions.model_dump(), "tools": _tools(task), "dry_run_by_default": True}
    problem = workspace_problem(task.permissions)
    if problem:
        return _reply("check_task", "blocked-escalated", codes.WORKSPACE, problem, data, **fields)
    blocked = [t["name"] for t in fields["tools"] if not t["permitted"]]
    message = ("The task as G6 would run it: the limits are capped by the trusted policy's ceilings, and every call "
               "is decided by the gate before it runs. Nothing was run."
               + (f" Calls to {', '.join(blocked)} will be refused: the task does not grant what they need."
                  if blocked else ""))
    return _reply("check_task", "qualified-draft", codes.TASK_CHECKED, message, data, **fields)


def _decide(data: AgentRuntimeInput) -> dict[str, Any]:
    if data.task is None:
        return _reply("decide", "blocked-escalated", codes.TASK_REQUIRED, "there is no task to decide the call in",
                      data)
    if data.call is None:
        return _reply("decide", "blocked-escalated", codes.CALL_REQUIRED, "there is no call to decide", data)
    problem = workspace_problem(data.task.permissions)
    if problem:
        return _reply("decide", "blocked-escalated", codes.WORKSPACE, problem, data)
    decision = ToolGate(data.task, dry_run=data.dry_run).decide(data.call)
    state = "blocked-escalated" if decision.verdict == "refused" else "qualified-draft"
    message = f"{decision.verdict}: {decision.reason}. The gate decided; nothing was run."
    dumped = decision.model_dump(mode="json")
    return _reply("decide", state, decision.code, message, data, evidence={"decision": dumped}, decision=dumped)


@component_maturity(
    "beta",
    reason="the gate and the run loop are deterministic; tool calls are decided from what the task declares, and an "
           "agent's own report is never more than a qualified draft",
)
class AgentRuntimeBlock(AIBlock[AgentRuntimeInput, dict, dict]):
    """Check a task and decide a tool call as the agents family's runtime would. Never ``verified``."""

    def __init__(self, name: str = "agent_runtime", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: AgentRuntimeInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        return Result.ok(_check_task(data) if data.op == "check_task" else _decide(data))


__all__ = ["SENSITIVE_OPS", "AgentRuntimeBlock", "AgentRuntimeInput"]
