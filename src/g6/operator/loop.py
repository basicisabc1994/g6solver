# SPDX-License-Identifier: Apache-2.0
"""One tick of the operator: one task, budgeted, judged by the operator's own record, its drafts queued for a person.

1. **Stop and reserve.** The task's ``reserve`` is reserved on the month's ``model`` budget in the same transaction as
   the stop check (venture status, unknown budget, overspend). A refusal ends the tick as ``blocked-escalated``.
2. **Execute.** ``executor.execute(task)`` gets the task and nothing else: not the queue, not the store. The run is
   charged its full reservation, or the cost it reports when that is higher.
3. **Judge.** The task's checks (``g6.harness.Check``) run on the output and the operator records the receipts.
   Completion is read back from that record, never from the executor's claimed status (audit 011, VEN-007): no check
   means ``self-reported``; a failed check blocks and its drafts are not queued. The checks are the caller's own, so a
   tick is ``qualified-draft`` at best and never ``verified``.
4. **Queue.** Each outward draft goes through ``g6.action_gating.evaluate_action`` (a ``block`` stops it here), then the
   draft checks (``router.route``), then into the queue as ``proposed`` with the actor that drafted it.

Simulation first: the only executor here is :class:`SimulatedExecutor`. A real executor is the caller's, and it can do
whatever its own process can; run it in a sandbox the caller controls (VEN-001, VEN-002).
"""
from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from g6.core import stop
from g6.harness import Check
from g6.operator.queue import ActionQueue, QueueError
from g6.operator.router import BLOCKED, Context, route
from g6.operator.store import BudgetRefused

_GATE_CONTEXT = ("verification_contract", "coordination_context", "world_state", "hypothesis_context",
                 "factual_preconditions")
_GATE_TYPE = {"email": "send_email", "spend": "payment"}   # the gate's own profiles; a profile can only raise risk
_COMPLETION = {
    "failed": ("blocked-escalated", "G6_E_OPERATOR_CHECK_FAILED", "a check failed on the output",
               "Revise the task's output; its drafts were not queued."),
    "unsettled": ("qualified-draft", "G6_E_OPERATOR_CHECK_UNKNOWN", "a check could not answer on the output",
                  "Fix the check that could not answer, then judge the output again."),
    "self-reported": ("qualified-draft", "G6_E_OPERATOR_SELF_REPORTED",
                      "no check ran on the output; that the task is done is the executor's word only",
                      "Give the task a check the operator can run on its output."),
    "checked": ("qualified-draft", "G6_E_OPERATOR_CALLER_CHECKS",
                "the task's checks passed; they are the caller's own, so this is not independent verification",
                "A person reviews the output and decides each queued draft."),
}


@dataclass(frozen=True)
class Task:
    task_id: str
    description: str
    reserve: float                  # the most one run may cost, in the venture's model-budget unit; reserved first
    checks: tuple[Check, ...] = ()  # how the operator judges the output


@dataclass(frozen=True)
class ExecutionResult:
    output: str
    proposals: tuple[Mapping[str, Any], ...] = ()   # outward drafts: {"type", "body", ...}; a person decides each
    cost: float | None = None       # the run's own report: it can raise the charge, never lower it
    claimed_status: str = ""        # recorded, never used to decide completion


class Executor(Protocol):
    actor: str                      # who drafts what it proposes; that actor can never approve it

    def execute(self, task: Task) -> ExecutionResult: ...


class SimulatedExecutor:
    """Replays scripted results by task id, or from a function of the task. No model, shell, file or network."""

    def __init__(self, script: Mapping[str, ExecutionResult] | Callable[[Task], ExecutionResult], *,
                 actor: str = "simulated-executor") -> None:
        self.actor, self._script, self.calls = actor, script, []

    def execute(self, task: Task) -> ExecutionResult:
        self.calls.append(task.task_id)
        return self._script(task) if callable(self._script) else self._script[task.task_id]


def _outcome(check: Check, output: str) -> str:
    try:
        value = check.run(output)
    except Exception:  # noqa: BLE001 - a check that raises has not answered
        return "unknown"
    return "pass" if value is True or value == "pass" else "fail" if value is False or value == "fail" else "unknown"


def _completion(receipts: Mapping[str, str]) -> str:
    if not receipts:
        return "self-reported"
    return "failed" if "fail" in receipts.values() else "unsettled" if "unknown" in receipts.values() else "checked"


def gate(action: Mapping[str, Any], actor: str) -> dict[str, Any]:
    """``g6.action_gating.evaluate_action`` on an outward draft, declared as an external action by ``actor``."""
    from g6.action_gating import evaluate_action

    kind = str(action.get("type", "")) or "unknown"
    capsule = {"action_type": _GATE_TYPE.get(kind, kind), "target_backend": "person",
               "target_resource": str(action.get("to") or action.get("channel") or kind),
               "action_params": {k: v for k, v in action.items() if k not in _GATE_CONTEXT},
               "requested_by": actor, "actor_type": "agent", "external_side_effect": True,
               "blast_radius": "external_party", "irreversible_or_costly_to_reverse": kind == "spend",
               **{k: action[k] for k in _GATE_CONTEXT if k in action}}
    try:
        decided = evaluate_action(capsule).gate_decision
    except Exception as exc:  # noqa: BLE001 - a gate that cannot evaluate the draft blocks it
        return {"decision": "block", "reason_codes": ["G6_E_OPERATOR_GATE_ERROR"], "message": f"{type(exc).__name__}"}
    return {"decision": decided.decision, "reason_codes": list(decided.reason_codes),
            "message": decided.required_user_message}


def _envelope(state: str, code: str, message: str, *, request_id: str, run_id: str, task_id: str,
              evidence: list[dict[str, Any]] | None = None, **extra: Any) -> dict[str, Any]:
    from g6.core.reliability_envelope import envelope_fields, warning_card

    envelope = envelope_fields(reliability_label=state, request_id=request_id, run_id=run_id, task_id=task_id,
                               evidence=evidence or [])
    if envelope["completion_state"] != "verified":
        envelope["warning_card"] = warning_card(code, message, extra.get("next_action"))
    return {**envelope, **extra}


def tick(queue: ActionQueue, venture_id: str, task: Task, executor: Executor, *, context: Context | None = None,
         now: datetime | None = None) -> dict[str, Any]:
    """Run ``task`` once for the queue's owner; the reply is the library envelope plus ``completion``, ``charged``,
    ``queued`` (action ids awaiting a person) and ``blocked`` (drafts stopped before the queue, and why)."""
    scope = queue.scope
    vid = scope.resolve(venture_id)
    request_id, run_id = uuid.uuid4().hex[:12], uuid.uuid4().hex
    month = (now or datetime.now(UTC)).strftime("%Y-%m")

    def reply(state: str, code: str, message: str, **extra: Any) -> dict[str, Any]:
        return _envelope(state, code, message, request_id=request_id, run_id=run_id, task_id=task.task_id,
                         evidence=scope.evidence(vid, ref=run_id), **extra)

    stopped = stop.engaged()   # audit 007 SE-010 / 011: nothing is reserved or run while G6 is stopped
    if stopped is not None:
        scope.record(vid, "stop", ref=run_id, detail={"task_id": task.task_id, "reason": stopped})
        return reply("blocked-escalated", stop.STOPPED, f"G6 is stopped: {stopped}", stage="stop",
                     next_action="Whoever engaged the stop releases it; the task can then run again.")
    actor = str(getattr(executor, "actor", "") or "").strip()
    keys = [c.key for c in task.checks]
    if not actor or len(set(keys)) != len(keys):
        why = "the executor names no actor, so nothing could stop it approving its own drafts" if not actor else \
            "two of the task's checks share an id"
        return reply("blocked-escalated", "G6_E_OPERATOR_TASK", why, stage="intake",
                     next_action="Give the executor an actor id and each check its own id.")
    try:
        reservation = scope.reserve(vid, month=month, account="model", amount=task.reserve)
    except BudgetRefused as exc:
        scope.record(vid, "stop", ref=run_id, detail={"task_id": task.task_id, "reason": str(exc)})
        return reply("blocked-escalated", "G6_E_OPERATOR_STOP", str(exc), stage="budget",
                     next_action="A person raises the cap or resumes the venture, or the next month begins.")

    try:
        result = executor.execute(task)
        if not isinstance(result, ExecutionResult):
            raise TypeError("the executor did not return an ExecutionResult")
    except Exception as exc:  # noqa: BLE001 - the executor's failure is reported, never raised through
        charged = scope.settle(vid, reservation, reported=None)
        scope.record(vid, "task", ref=run_id, detail={"task_id": task.task_id, "actor": actor, "charged": charged,
                                                      "error": f"{type(exc).__name__}: {exc}"})
        return reply("blocked-escalated", "G6_E_OPERATOR_EXECUTOR_FAILED", f"{type(exc).__name__}: {exc}",
                     stage="execute", charged=charged, next_action="Fix the executor and run the task again.")
    charged = scope.settle(vid, reservation, reported=result.cost)

    output = result.output if isinstance(result.output, str) else str(result.output)
    scope.record(vid, "task", ref=run_id, detail={
        "task_id": task.task_id, "actor": actor, "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
        "receipts": {c.key: _outcome(c, output) for c in task.checks}, "claimed_status": result.claimed_status,
        "reported_cost": result.cost, "charged": charged})
    receipts = scope.evidence(vid, kind="task", ref=run_id)[-1]["detail"]["receipts"]   # the record decides
    completion = _completion(receipts)
    scope.record(vid, "completion", ref=run_id, detail={"task_id": task.task_id, "completion": completion})

    queued: list[str] = []
    blocked: list[dict[str, Any]] = []
    proposals = result.proposals if completion != "failed" else ()
    for action in map(dict, proposals):
        decided = gate(action, actor)
        entry: dict[str, Any] = {"type": action.get("type")}
        if decided["decision"] == "block":
            entry.update(stage="gate", **decided)
        else:
            routed = route(action, context)
            if routed.state == BLOCKED:
                entry.update(stage="checks", fired=list(routed.fired), message=routed.message)
            else:
                try:
                    row = queue.enqueue(vid, task_id=task.task_id, action=action, drafted_by=actor,
                                        review={**routed.as_dict(), "gate": decided})
                    queued.append(row["action_id"])
                    continue
                except QueueError as exc:
                    entry.update(stage="queue", message=str(exc))
        blocked.append(entry)
        scope.record(vid, "draft_blocked", ref=run_id, detail=entry)
    if queued:
        scope.record(vid, "queued", ref=run_id, detail={"action_ids": queued})

    state, code, message, next_action = _COMPLETION[completion]
    failing = sorted(k for k, v in receipts.items() if v != "pass")
    return reply(state, code, message + (f": {failing}" if failing else ""), stage="done", completion=completion,
                 receipts=receipts, charged=charged, queued=queued, blocked=blocked, next_action=next_action)
