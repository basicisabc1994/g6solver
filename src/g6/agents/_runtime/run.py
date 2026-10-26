# SPDX-License-Identifier: Apache-2.0
"""The one interface every agent adapter implements (:class:`AgentRuntime`), and :func:`run_agent`, the one way G6 runs
one.

An adapter is an object with a ``name``, ``available()`` (a look on this machine for what it needs: no network, no
model call) and ``run(task, *, gate, usage, stop)``, which yields :class:`~g6.agents._runtime.model.AgentEvent` objects
as the agent works: ``step`` before each step, ``tool_result`` (with the call's ``call_id``) after a tool it ran,
``message``, and ``finished`` (``output``, ``claimed_status``, ``detail``) once. Before each tool call it asks
``gate.decide(call)`` and runs the tool only on a ``run`` verdict; it records tokens and cost it observes on ``usage``
(a ``g6.core.resources.ResourceUsage``). A model the adapter needs comes in through its constructor, as a
``g6.core.llm`` caller; the runtime makes no model call of its own. An adapter that cannot go on raises
:class:`~g6.agents._runtime.model.AgentRefusal` with its code.

``run_agent`` holds every adapter to the same handling (the failure mode ``unhandled_agent_failure``):

* before anything runs it refuses an object that is not an adapter, an engaged stop, a workspace that cannot be used
  and an adapter whose circuit breaker is open;
* before each event it checks the stop and the limits: steps, wall-clock time (the task's, capped by the trusted
  ``g6.safety_engine.RuntimePolicy`` and by a time limit in the ``ResourceBounds`` you pass, which the adapter gets as
  ``gate.limits.wall_clock_s`` and hands to the program it starts) and any other ``ResourceBounds``;
* whatever the adapter does (raises any error, yields something that is not an event, ends without finishing, reports
  a tool result the gate never allowed, runs past a limit) ends as a ``blocked-escalated`` outcome with a stable code,
  never a raised error, and the adapter's generator is closed, so a command-line agent's process tree is ended;
* a finished run is ``qualified-draft``: ``G6_E_AGENT_SELF_REPORTED``, or ``G6_E_AGENT_DRY_RUN`` when its outward calls
  were all dry runs. Nothing is ``verified``: what the agent says it did is ``claimed_status`` and decides nothing;
* with a ``breaker``, only a finished run that was not a dry run counts as a success, a backend failure counts as a
  failure (a rate limit or request timeout only backs off), and any other outcome counts neither way and releases a
  probe, so the circuit cannot stay open for good.

:func:`as_executor` makes an adapter and a task into the executor ``g6.harness.run`` takes, so the harness's checks,
frozen before the run, judge the output. For a change to code, send the agent's patch to ``g6.workforce.submit_patch``.
"""
from __future__ import annotations

import contextlib
import dataclasses
import time
import uuid
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from g6.agents._runtime import codes
from g6.agents._runtime.breaker import CircuitBreaker
from g6.agents._runtime.gate import ToolGate, _stopped
from g6.agents._runtime.model import (
    AgentEvent,
    AgentOutcome,
    AgentRefusal,
    AgentTask,
    Availability,
    ToolDecision,
    workspace_problem,
)
from g6.core import resources
from g6.core import stop as core_stop
from g6.core.resources import ResourceBounds, ResourceUsage
from g6.safety_engine.schema import RuntimePolicy

MAX_OUTPUT_CHARS = 1_000_000
MAX_EVENTS = 500
_REASON = 500


@runtime_checkable
class AgentRuntime(Protocol):
    """What every agent adapter implements; see the module docstring."""

    name: str

    def available(self) -> Availability: ...

    def run(self, task: AgentTask, *, gate: ToolGate, usage: ResourceUsage,
            stop: Callable[[], str | None]) -> Any: ...


def _is_runtime(thing: object) -> bool:
    name = getattr(thing, "name", None)
    return isinstance(thing, AgentRuntime) and isinstance(name, str) and bool(name.strip())


class _Record:
    """What one run has seen so far, and how it ends."""

    def __init__(self, adapter: str, request_id: str, run_id: str) -> None:
        self.adapter, self.request_id, self.run_id = adapter, request_id, run_id
        self.events: list[dict[str, Any]] = []
        self.steps, self.backend_failure = 0, False
        self.error: object = None   # what a backend failure said, for the breaker to tell a rate limit from a fault
        self.gate: ToolGate | None = None
        self.usage: ResourceUsage | None = None

    def event(self, event: AgentEvent) -> None:
        if len(self.events) < MAX_EVENTS:
            self.events.append({"seq": len(self.events) + 1, "kind": event.kind, "payload": dict(event.payload)})

    def _usage(self) -> dict[str, Any]:
        decisions = self.gate.decisions if self.gate else ()
        out: dict[str, Any] = {"steps": self.steps, "decisions": len(decisions),
                               "tool_calls_run": sum(1 for d in decisions if d.runs)}
        if self.usage is not None:
            out |= {"elapsed_s": round(self.usage.elapsed_seconds, 3), "tokens": self.usage.tokens_used,
                    "cost_usd": round(self.usage.cost_used, 6)}
        if self.gate is not None:
            out["limits"] = self.gate.limits.model_dump()
        return out

    def _outcome(self, state: str, code: str, message: str, **fields: Any) -> AgentOutcome:
        decisions: tuple[ToolDecision, ...] = self.gate.decisions if self.gate else ()
        return AgentOutcome(adapter=self.adapter, state=state, code=code, message=message, decisions=decisions,
                            events=tuple(self.events), usage=self._usage(), request_id=self.request_id,
                            run_id=self.run_id, **fields)

    def end(self, code: str, reason: str, *, detail: dict[str, Any] | None = None,
            backend_failure: bool = False, error: object = None) -> AgentOutcome:
        self.backend_failure = backend_failure
        self.error = error if error is not None else reason
        return self._outcome("blocked-escalated", code, reason[:_REASON * 4], detail=detail or {})

    def finish(self, payload: dict[str, Any]) -> AgentOutcome:
        output = payload.get("output", "")
        output = output if isinstance(output, str) else str(output)
        claimed = str(payload.get("claimed_status", "") or "")[:1000]
        detail = dict(payload["detail"]) if isinstance(payload.get("detail"), dict) else {}
        if len(output) > MAX_OUTPUT_CHARS:
            output, detail["output_truncated_at"] = output[:MAX_OUTPUT_CHARS], MAX_OUTPUT_CHARS
        decisions = self.gate.decisions if self.gate else ()
        dry = [d for d in decisions if d.verdict == "dry_run"]
        if dry and not any(d.runs and d.outward for d in decisions):
            code = codes.DRY_RUN
            message = (f"A dry run: {len(dry)} outward call(s) were decided and not run, so nothing outward happened. "
                       "The decisions say what would run.")
        elif detail.get("dry_run") is True and not any(d.runs for d in decisions):
            code = codes.DRY_RUN
            message = ("A dry run: nothing was run and no model was asked. detail.plan says what a run would do; to "
                       "carry it out, run again with dry_run=false.")
        else:
            code = codes.SELF_REPORTED
            said = f" ({claimed!r})" if claimed else ""
            message = (f"{self.adapter} finished. What it reports{said} is its own claim: G6 has not checked the "
                       "output, so it is a draft until a check you trust passes on it.")
        return self._outcome("qualified-draft", code, message, output=output, claimed_status=claimed, detail=detail)


def _limit(seconds: float, bounds: ResourceBounds | None) -> ResourceBounds:
    """The person's bounds, with the run's time limit the tighter of theirs and the task's."""
    mine = bounds or ResourceBounds()
    held = mine.max_execution_seconds
    return dataclasses.replace(mine, max_execution_seconds=seconds if held is None else min(seconds, held))


def run_agent(runtime: Any, task: AgentTask, *, policy: RuntimePolicy | None = None,
              bounds: ResourceBounds | None = None, dry_run: bool = True, audit_logger: Any = None,
              breaker: CircuitBreaker | None = None, stop: Callable[[], str | None] | None = None,
              clock: Callable[[], float] | None = None, request_id: str = "", run_id: str = "") -> AgentOutcome:
    """Run ``task`` on ``runtime`` once; see the module docstring. ``policy`` is the trusted ceilings (the default
    ``RuntimePolicy`` when none is given), never the caller's; ``dry_run`` (the default) keeps every outward call from
    running; ``audit_logger`` is the action gate's (its durable trail under ``$G6_HOME`` by default)."""
    stop = stop or core_stop.engaged
    name = getattr(runtime, "name", "")
    record = _Record(name.strip() if isinstance(name, str) and name.strip() else type(runtime).__name__,
                     request_id or uuid.uuid4().hex[:12], run_id or uuid.uuid4().hex)
    if not _is_runtime(runtime):
        return record.end(codes.NOT_A_RUNTIME, f"{type(runtime).__name__} is not an agent adapter: it needs a name, "
                                               "available() and run(task, *, gate, usage, stop)")
    why = _stopped(stop)
    if why:
        return record.end(codes.STOPPED, f"G6 is stopped: {why}")
    problem = workspace_problem(task.permissions)
    if problem:
        return record.end(codes.WORKSPACE, problem)
    if breaker is not None and not breaker.is_available():
        return record.end(codes.CIRCUIT_OPEN, f"{record.adapter} failed {breaker.failure_threshold} time(s) in a row, "
                                              f"so its circuit is open for {breaker.cooldown_s:g} seconds")
    record.gate = ToolGate(task, policy=policy, dry_run=dry_run, audit_logger=audit_logger, stop=stop,
                           max_seconds=bounds.max_execution_seconds if bounds is not None else None)
    record.usage = ResourceUsage(clock=clock or time.monotonic)
    outcome = _drive(runtime, task, record, _limit(record.gate.limits.wall_clock_s, bounds), stop)
    if breaker is not None:
        if outcome.state == "qualified-draft" and outcome.code != codes.DRY_RUN:
            breaker.record_success()
        elif record.backend_failure:
            breaker.record_transient_or_failure(record.error)   # a rate limit backs off; any other failure counts
        else:
            breaker.record_inconclusive()   # a dry run, a stop, a refusal before the backend ran: it shows nothing
    return outcome


def _drive(runtime: Any, task: AgentTask, record: _Record, limit: ResourceBounds,
           stop: Callable[[], str | None]) -> AgentOutcome:
    gate, usage, name = record.gate, record.usage, record.adapter
    stream: Any = None
    try:
        stream = runtime.run(task, gate=gate, usage=usage, stop=stop)
        try:
            events = iter(stream)
        except TypeError:
            return record.end(codes.BAD_EVENT, f"{name}.run returned {type(stream).__name__}, not a stream of "
                                               "events", backend_failure=True)
        while True:
            why = _stopped(stop)
            if why:
                return record.end(codes.STOPPED, f"G6 is stopped: {why}")
            if usage.elapsed_seconds >= (limit.max_execution_seconds or 0):
                return record.end(codes.DEADLINE, f"{name} used its {limit.max_execution_seconds:g} seconds")
            over = resources.check(limit, usage)
            if over:
                return record.end(codes.RESOURCE_LIMIT, over)
            try:
                event = next(events)
            except StopIteration:
                return record.end(codes.NO_OUTCOME, f"{name} stopped without finishing", backend_failure=True)
            if not isinstance(event, AgentEvent):
                return record.end(codes.BAD_EVENT, f"{name} reported a {type(event).__name__}, not an AgentEvent",
                                  backend_failure=True)
            record.event(event)
            if event.kind == "step":
                record.steps += 1
                if record.steps > gate.limits.max_steps:
                    return record.end(codes.STEP_LIMIT, f"{name} took more than {gate.limits.max_steps} steps")
            elif event.kind == "tool_result":
                call_id = event.payload.get("call_id")
                if not gate.allowed(call_id):
                    return record.end(codes.UNGATED_TOOL, f"{name} reported a result for the call {call_id!r}, "
                                                          "which the gate did not let run", backend_failure=True)
            elif event.kind == "finished":
                return record.finish(dict(event.payload))
    except AgentRefusal as refusal:
        return record.end(refusal.code, refusal.reason, detail=refusal.detail, backend_failure=refusal.backend_failure,
                          error=refusal)
    except Exception as exc:  # noqa: BLE001 - an adapter's error is reported, never raised through (D1)
        return record.end(codes.FAILED, f"{name} failed: {type(exc).__name__}: {exc}", backend_failure=True,
                          error=exc)
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                close()


class AgentRunBlocked(RuntimeError):
    """A run that ended ``blocked-escalated``, raised by :class:`AgentExecutor`; its text starts with the code."""

    def __init__(self, outcome: AgentOutcome) -> None:
        super().__init__(f"[{outcome.code}] {outcome.message}")
        self.outcome = outcome


class AgentExecutor:
    """The executor ``g6.harness.run`` takes: it runs the adapter on the task (the harness's contract is not read; the
    task is the person's) and returns the output. A blocked run raises :class:`AgentRunBlocked`, which the harness
    reports as the executor's failure. Every outcome is kept in ``outcomes``, decisions and claims included."""

    def __init__(self, runtime: Any, task: AgentTask, **options: Any) -> None:
        self.runtime, self.task, self.options = runtime, task, options
        self.outcomes: list[AgentOutcome] = []

    def __call__(self, contract: Any = None) -> str:
        outcome = run_agent(self.runtime, self.task, **self.options)
        self.outcomes.append(outcome)
        if outcome.state == "blocked-escalated":
            raise AgentRunBlocked(outcome)
        return outcome.output


def as_executor(runtime: Any, task: AgentTask, **options: Any) -> AgentExecutor:
    """``runtime`` and ``task`` as the executor ``g6.harness.run`` takes; ``options`` go to :func:`run_agent`."""
    return AgentExecutor(runtime, task, **options)


__all__ = ["MAX_EVENTS", "MAX_OUTPUT_CHARS", "AgentExecutor", "AgentRunBlocked", "AgentRuntime", "as_executor",
           "run_agent"]
