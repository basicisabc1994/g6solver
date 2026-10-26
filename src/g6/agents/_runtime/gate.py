# SPDX-License-Identifier: Apache-2.0
"""ToolGate: the decision made before each tool call an agent proposes, and the record of every decision.

``decide(call)`` answers ``run``, ``dry_run`` or ``refused``, with a stable code and the reason. The checks, in order:

1. G6's stop (``g6.core.stop``, or the stop the caller passes): while it is engaged, every call is refused;
2. the tool is one the task declares (and, when the trusted policy lists tools, one the policy lists);
3. the effect the task declares for the tool has its permission (:class:`~g6.agents._runtime.model.Permissions`), and
   an online tool has the ``network`` permission too; a tool that reads, writes or deletes files names the files it
   touches, and the task declares a workspace;
4. every path is resolved against the workspace (``..`` and symbolic links followed); a call that touches anything
   outside the workspace is refused, after ``g6.action_gating`` has evaluated it, so the action gate's audit trail
   holds the attempt;
5. the trusted ceilings of ``g6.safety_engine`` (tool calls, shell commands, files modified, runtime, the policy's
   forbidden paths), through its ``PolicyEngine``;
6. every call with an effect (reads included, so a secret-looking file goes to a person) and every online call goes
   through ``g6.action_gating.evaluate_action``; anything but ``allow`` or ``allow_with_notice`` is refused, and so is a
   gate that cannot decide. A write names the workspace's version control as the checkpoint it can be undone from,
   so a write in a workspace without one is refused; a delete is irreversible whatever it names. An online tool that
   reads, writes, deletes or runs a shell is evaluated as that file or shell action, so its record says
   (``gate["not_evaluated"]``) that the online side, sending the workspace and the task to the agent's model provider,
   was not evaluated by ``g6.action_gating``: only the person's ``dry_run=False`` on a sensitive op authorises it;
7. an outward call (one that writes, deletes, runs a shell, reaches the network, sends, pays, or is online, or one the
   caller of ``decide`` says ``changes`` something: a G6 component's op its component does not declare read-only) is a
   dry run unless the gate was made with ``dry_run=False``: decided and recorded, not run.

The gate decides; it runs nothing. An adapter runs a tool only on a ``run`` verdict and reports its result with the
call's id, which the runtime checks against this record. Nothing written into a task, a call or its arguments (an
approval, a reviewer, a risk level) is read by any step, and nothing here calls a model.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import g6.action_gating as action_gating
from g6.agents._runtime import codes
from g6.agents._runtime.model import (
    DOES,
    FILE_EFFECTS,
    NEEDS,
    AgentTask,
    ToolCall,
    ToolDecision,
    ToolSpec,
    effective_limits,
    inside,
    resolve_in,
)
from g6.core import stop as core_stop
from g6.safety_engine.policy_engine import PolicyEngine
from g6.safety_engine.schema import ActionRequest, RuntimePolicy
from g6.safety_engine.state import RunState

#: The action gate's profile for each effect; a profile can only raise the risk of what it names. An online tool with
#: no effect of its own is an API call. A delete is ``delete_file``, irreversible by its profile (ACTION_GATING-01).
_ACTION_TYPE = {"read": "read_file", "write": "write_file", "delete": "delete_file", "shell": "run_code",
                "network": "call_api", "send": "send_email", "pay": "payment", "none": "call_api"}
_GATE_ALLOWS = frozenset({"allow", "allow_with_notice"})
_REASON = 300
#: Said on the record of every online call the action gate judged as a file or shell action.
ONLINE_NOT_EVALUATED = ("the online side (sending the workspace and the task to the agent's model provider) was not "
                        "evaluated by g6.action_gating, which judged the call as {action_type} on the paths it names; "
                        "only the person's dry_run=false on a sensitive op authorises it")
_LOCAL_TYPES = frozenset({"read_file", "write_file", "delete_file", "run_code"})


def _version_control(workspace: Path | None) -> Path | None:
    """The workspace's version control (the ``.git`` that ``model.versioned`` finds), or ``None``."""
    places = (workspace, *workspace.parents) if workspace is not None else ()
    return next(((place / ".git").resolve() for place in places if (place / ".git").exists()), None)


def _stopped(stop: Callable[[], str | None]) -> str | None:
    """Why the stop is engaged, or ``None``; a stop that cannot be read counts as engaged."""
    try:
        why = stop()
    except Exception as exc:  # noqa: BLE001 - fail closed
        return f"the stop could not be read ({type(exc).__name__})"
    return str(why) if why else None


class ToolGate:
    """Decides each tool call before it runs, for one task; see the module docstring. ``limits`` is what the run gets:
    the task's, capped by the trusted policy and by ``max_seconds`` when one is given; an adapter that starts a program
    hands ``limits.wall_clock_s`` to it as its deadline."""

    def __init__(self, task: AgentTask, *, policy: RuntimePolicy | None = None, dry_run: bool = True,
                 audit_logger: Any = None, stop: Callable[[], str | None] | None = None,
                 max_seconds: float | None = None) -> None:
        self.task, self.dry_run = task, bool(dry_run)
        self.policy = policy or RuntimePolicy()
        self.limits = effective_limits(task.limits, self.policy)
        if max_seconds is not None:   # a time limit of the person's (ResourceBounds), when it is tighter
            self.limits = self.limits.model_copy(
                update={"wall_clock_s": max(0.0, min(self.limits.wall_clock_s, float(max_seconds)))})
        self._specs = {spec.name: spec for spec in task.tools}
        self._engine_policy = replace(self.policy, allowed_tools=frozenset(self._specs),
                                      max_tool_calls=self.limits.max_tool_calls,
                                      max_runtime_seconds=math.ceil(self.limits.wall_clock_s))
        self._state = RunState(policy=self._engine_policy)
        self._engine = PolicyEngine()
        self._audit = audit_logger
        self._stop = stop or core_stop.engaged
        self._workspace = Path(task.permissions.workspace).resolve() if task.permissions.workspace else None
        self._log: list[ToolDecision] = []
        self._first: dict[str, ToolDecision] = {}

    @property
    def decisions(self) -> tuple[ToolDecision, ...]:
        """Every decision, in order."""
        return tuple(self._log)

    def allowed(self, call_id: object) -> bool:
        """Whether the call with this id was decided ``run``."""
        found = self._first.get(call_id) if isinstance(call_id, str) else None
        return found is not None and found.runs

    def decide(self, call: ToolCall, *, changes: bool = False) -> ToolDecision:
        """Decide ``call``; see the module docstring. ``changes``: the call changes something whatever effect the task
        declares for it (``GatedCalls`` says so for a G6 component's op its component does not declare read-only), so
        it is outward. Never raises: a gate that cannot decide refuses."""
        spec = self._specs.get(call.tool)
        if call.call_id in self._first:
            decided = self._decision(call, spec, "refused", codes.CALL_REUSED,
                                     f"the call id {call.call_id!r} was already decided")
        else:
            try:
                decided = self._decide(call, spec, changes)
            except Exception as exc:  # noqa: BLE001 - a gate that cannot decide refuses (G4)
                decided = self._decision(call, spec, "refused", codes.GATE_ERROR,
                                         f"the gate could not decide: {type(exc).__name__}: {exc}"[:_REASON])
            self._first[call.call_id] = decided
        self._log.append(decided)
        return decided

    def _decision(self, call: ToolCall, spec: ToolSpec | None, verdict: str, code: str, reason: str, *,
                  paths: tuple[str, ...] = (), gate: dict[str, Any] | None = None,
                  outward: bool | None = None) -> ToolDecision:
        return ToolDecision(call_id=call.call_id, tool=call.tool, effect=spec.effect if spec else "",
                            verdict=verdict, code=code, reason=reason,
                            outward=bool(spec and spec.outward) if outward is None else outward,
                            paths=paths or tuple(call.paths), gate=gate)

    def _decide(self, call: ToolCall, spec: ToolSpec | None, changes: bool = False) -> ToolDecision:
        def refused(code: str, reason: str, **kw: Any) -> ToolDecision:
            return self._decision(call, spec, "refused", code, reason, **kw)

        why = _stopped(self._stop)
        if why:
            return refused(codes.STOPPED, f"G6 is stopped: {why}")
        if spec is None:
            return refused(codes.TOOL_UNDECLARED, f"{call.tool!r} is not a tool the task declares")
        if self.policy.allowed_tools and call.tool not in self.policy.allowed_tools:
            return refused(codes.TOOL_UNDECLARED, f"the trusted policy does not list {call.tool!r}")
        need = NEEDS.get(spec.effect)
        if need and not getattr(self.task.permissions, need):
            return refused(codes.PERMISSION, f"{call.tool} {DOES[spec.effect]}, and the task does not grant {need}")
        if spec.online and not self.task.permissions.network:
            return refused(codes.PERMISSION, f"{call.tool} is online: it sends what it is given off this machine, and "
                                             "the task does not grant network")
        if (spec.effect in FILE_EFFECTS or call.paths) and self._workspace is None:
            return refused(codes.PERMISSION, "the task declares no workspace, so the agent may touch no file")
        if spec.effect in FILE_EFFECTS and not call.paths:
            return refused(codes.PATHS_REQUIRED, f"{call.tool} {DOES[spec.effect]}, and the call names none")
        resolved = tuple(str(resolve_in(self._workspace, p)) for p in call.paths) if self._workspace else ()
        outside = [p for p in resolved if not inside(Path(p), self._workspace)]
        if outside:
            seen = self._evaluate(call, spec, outside, outside_workspace=True)
            return refused(codes.OUTSIDE_WORKSPACE, f"{call.tool} would touch {outside[0]}, outside the workspace "
                                                    f"{self._workspace}", paths=resolved, gate=seen)
        request = ActionRequest(tool_name=call.tool, files_touched=resolved, is_shell_command=spec.effect == "shell")
        authorised = self._engine.authorize_action(request, self._state, self._engine_policy)
        if authorised.decision != "ALLOW":
            reason = authorised.reason
            code = (codes.STOPPED if reason.startswith(("kill switch", "G6 is stopped")) else
                    codes.TOOL_LIMIT if "ceiling" in reason else codes.POLICY_BLOCKED)
            return refused(code, reason, paths=resolved)
        seen = self._evaluate(call, spec, list(resolved)) if spec.gated else None
        if seen is not None and seen["decision"] not in _GATE_ALLOWS:
            return refused(codes.GATE_BLOCKED, f"g6.action_gating decided {seen['decision']}: {seen['message']}",
                           paths=resolved, gate=seen)
        outward = spec.outward or changes
        if outward and self.dry_run:
            does = (f"{DOES[spec.effect]}{' and is online' if spec.online else ''}" if spec.outward else
                    "may change what its component holds (the component does not declare it read-only)")
            return self._decision(call, spec, "dry_run", codes.DRY_RUN,
                                  f"a dry run: {call.tool} {does}, so it is not run unless the person runs with "
                                  "dry_run=False", paths=resolved, gate=seen, outward=True)
        modified = resolved if spec.effect in ("write", "delete") else ()
        self._state.record_execution(replace(request, files_touched=modified))
        return self._decision(call, spec, "run", codes.TOOL_ALLOWED, f"{call.tool} may run", paths=resolved,
                              gate=seen, outward=outward)

    def _evaluate(self, call: ToolCall, spec: ToolSpec, paths: list[str], *,
                  outside_workspace: bool = False) -> dict[str, Any]:
        """What ``g6.action_gating`` decides about the call, declared as an agent's action on ``paths``."""
        params = {"tool": call.tool, "paths": list(paths),
                  **{f"argument_{k}": v for k, v in call.arguments.items() if isinstance(v, (str, list, tuple))}}
        action: dict[str, Any] = {
            "action_type": _ACTION_TYPE[spec.effect], "target_resource": paths[0] if paths else call.tool,
            "description": f"an agent proposes {call.tool}, which {DOES[spec.effect]}", "requested_by": "agent",
            "actor_type": "agent", "action_params": params}
        checkpoint = _version_control(self._workspace) if spec.effect == "write" and not outside_workspace else None
        if checkpoint is not None:  # what the person can undo the write from: the action gate checks it can read it
            params["checkpoint"] = str(checkpoint)
        if outside_workspace:
            action["blast_radius"] = "unknown"
        evaluated = (action_gating.evaluate_action(action) if self._audit is None else
                     action_gating.evaluate_action(action, audit_logger=self._audit))
        decided = evaluated.gate_decision
        seen = {"decision": decided.decision, "reason_codes": list(decided.reason_codes),
                "message": decided.required_user_message, "audit_id": decided.audit_id,
                "action_type": action["action_type"]}
        if spec.online and action["action_type"] in _LOCAL_TYPES:
            seen["not_evaluated"] = ONLINE_NOT_EVALUATED.format(action_type=action["action_type"])
        return seen


__all__ = ["ONLINE_NOT_EVALUATED", "ToolGate"]
