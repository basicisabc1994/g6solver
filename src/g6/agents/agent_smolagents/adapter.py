# SPDX-License-Identifier: Apache-2.0
"""SmolagentsRuntime: a smolagents agent as an adapter on the agents family's runtime (``g6.agents._runtime``).

G6 builds the agent from an :class:`~g6.agents.agent_smolagents.spec.AgentSpec`: a ``ToolCallingAgent`` whose every
tool is gated (:func:`~g6.agents.agent_smolagents.models.gated`), or a ``CodeAgent`` with no tools whose every code step
runs in G6's sandbox (:class:`~g6.agents.agent_smolagents.models.G6Executor`). Both talk to the ``g6.core.llm`` caller
passed in (:class:`~g6.agents.agent_smolagents.models.G6Model`). A declared tool must be passed in as a smolagents
``Tool`` of that name, or be a G6 component's op (``component.op``); otherwise nothing starts
(``G6_E_AGENT_SMOLAGENTS_TOOL_MISSING``).

A dry run (``run_agent``'s default) asks no model and runs nothing: it answers ``G6_E_AGENT_DRY_RUN`` with
``detail.plan`` (the agent's kind, its limits, the model a run would ask and its tools), after the same refusals a run
would meet first.

Each of the agent's steps is a ``step`` event, so ``run_agent`` checks G6's stop, the step limit (capped by the trusted
policy) and the time limit between steps. An agent that has not answered when its steps run out has not finished
(``G6_E_AGENT_STEP_LIMIT``), even though smolagents then asks the model for an answer. One that answered is
``qualified-draft``: its answer is the output and the answer's first line the ``claimed_status``, confined or not;
``detail.sandbox`` says how its code steps ran (:meth:`~g6.agents.agent_smolagents.sandbox.SandboxSession.record`).
"""
from __future__ import annotations

import contextlib
import os
from collections.abc import Callable, Iterator, Mapping
from typing import Any

from g6.agents._runtime import (
    AgentEvent,
    AgentRefusal,
    AgentTask,
    Availability,
    GatedCalls,
    Limits,
    ToolGate,
    codes,
    model_named,
)
from g6.agents.agent_smolagents.sandbox import ALLOWED_IMPORTS, SandboxSession
from g6.agents.agent_smolagents.spec import AgentSpec
from g6.core.llm import LLMCaller, NoModel
from g6.core.resources import ResourceUsage

_P = "G6_E_AGENT_SMOLAGENTS_"
NOT_INSTALLED = _P + "NOT_INSTALLED"
NO_MODEL = _P + "NO_MODEL"
TOOL_MISSING = _P + "TOOL_MISSING"
AGENT_FAILED = _P + "AGENT_FAILED"
LLM_DISABLED = "G6_E_LLM_DISABLED"
INSTALL = "smolagents is not installed: pip install 'g6[agents-smolagents]'"
_SELF = "agent_smolagents"
_SAID = 4000


def missing() -> str:
    """Why smolagents cannot be used here, or ``""`` when it can."""
    try:
        import smolagents  # noqa: F401
    except ImportError:
        return INSTALL
    return ""


class SmolagentsRuntime:
    """See the module docstring. ``task`` is what the agent is asked to do; ``llm`` its model; ``tools`` the
    smolagents ``Tool`` objects you pass in, by their declared names."""

    name = "smolagents"

    def __init__(self, spec: AgentSpec, *, task: str, llm: LLMCaller | None = None,
                 tools: Mapping[str, Any] | None = None, deadline_s: float = 600.0) -> None:
        if not task or not task.strip():
            raise ValueError("an agent needs a task")
        self.spec, self.task_text, self.llm, self.tools = spec, task, llm, dict(tools or {})
        self.deadline_s = deadline_s

    def task(self) -> AgentTask:
        """The task this run is: the agent's task, its tools, the permissions they were given and its limits."""
        spec = self.spec
        return AgentTask(goal=self.task_text, tools=spec.tools, permissions=spec.permissions,
                         limits=Limits(max_steps=spec.max_steps, max_tool_calls=spec.max_tool_calls,
                                       wall_clock_s=self.deadline_s))

    def available(self) -> Availability:
        """Whether smolagents imports here; nothing is run."""
        why = missing()
        return Availability(found=not why, detail=why or "smolagents imports", code=NOT_INSTALLED if why else "")

    def _agent(self, gate: ToolGate, calls: GatedCalls, session: SandboxSession) -> Any:
        from smolagents import CodeAgent, ToolCallingAgent
        from smolagents.monitoring import LogLevel

        from g6.agents.agent_smolagents.models import G6Executor, G6Model, gated

        spec, model = self.spec, G6Model(self.llm)
        common = {"model": model, "max_steps": gate.limits.max_steps, "verbosity_level": LogLevel.OFF,
                  "instructions": spec.instructions or None}
        if spec.agent_type == "code":
            return CodeAgent(tools=[], executor=G6Executor(session), additional_authorized_imports=[], **common)
        tools = [gated(calls, declared.name, self.tools.get(declared.name)) for declared in spec.tools]
        return ToolCallingAgent(tools=tools, **common)

    def _check_tools(self) -> None:
        """Refuse a declared tool that was not passed in, or was passed in under another name (a run or a dry run)."""
        for declared in self.spec.tools:
            inner = self.tools.get(declared.name)
            if inner is None and "." not in declared.name:
                raise AgentRefusal(TOOL_MISSING, f"the tool {declared.name!r} is declared and was not passed in: pass "
                                                 "a smolagents Tool of that name, or name a G6 op as component.op")
            if inner is not None and getattr(inner, "name", None) != declared.name:
                raise AgentRefusal(TOOL_MISSING, f"the tool passed in for {declared.name!r} is named "
                                                 f"{getattr(inner, 'name', None)!r}")

    def _plan(self, gate: ToolGate) -> dict[str, Any]:
        """What a dry run answers: what a run would be, with no model asked and nothing run."""
        spec, limits = self.spec, gate.limits
        plan: dict[str, Any] = {
            "agent_type": spec.agent_type, "max_steps": limits.max_steps, "max_tool_calls": limits.max_tool_calls,
            "wall_clock_s": limits.wall_clock_s, "model": model_named(self.llm), "model_calls": 0,
            "tools": [{"name": t.name, "effect": t.effect, "outward": t.outward} for t in spec.tools]}
        if spec.agent_type == "code":
            plan |= {"code_timeout_s": spec.code_timeout_s, "allowed_imports": list(ALLOWED_IMPORTS)}
        return {"agent_type": spec.agent_type, "steps": [], "code_runs": 0, "dry_run": True, "plan": plan}

    def run(self, task: AgentTask, *, gate: ToolGate, usage: ResourceUsage,
            stop: Callable[[], str | None]) -> Iterator[AgentEvent]:
        """One run of the agent; see the module docstring."""
        why = missing()
        if why:
            raise AgentRefusal(NOT_INSTALLED, why)
        if self.llm is None or isinstance(self.llm, NoModel):
            raise AgentRefusal(NO_MODEL, "the agent needs a model and none was passed in: pass a g6.core.llm caller "
                                         "(SmolagentsRuntime(spec, task=..., llm=...)), or set G6_LLM_PROVIDER")
        if os.environ.get("G6_DISABLE_LLM"):
            raise AgentRefusal(LLM_DISABLED, "model calls are disabled by G6_DISABLE_LLM, and the agent calls a model, "
                                             "so it was not started")
        self._check_tools()
        if gate.dry_run:
            yield AgentEvent(kind="finished", payload={"output": "", "detail": self._plan(gate)})
            return
        from smolagents.memory import ActionStep, FinalAnswerStep
        from smolagents.utils import AgentMaxStepsError

        calls = GatedCalls(gate, self.tools, own=_SELF)
        # a code step gets its own limit or the run's remaining time, whichever is less
        session = SandboxSession(timeout_s=self.spec.code_timeout_s,
                                 remaining=lambda: gate.limits.wall_clock_s - usage.elapsed_seconds)
        agent = self._agent(gate, calls, session)
        steps: list[dict[str, Any]] = []
        detail: dict[str, Any] = {"agent_type": self.spec.agent_type, "steps": steps, "limit": gate.limits.max_steps,
                                  "random_seed": session.seed}   # what replayed steps draw from (see the sandbox)
        seen: set[int] = set()
        answer: Any = None
        finished = False
        try:
            with contextlib.closing(agent.run(self.task_text, stream=True, max_steps=gate.limits.max_steps)) as events:
                for event in events:
                    if isinstance(event, FinalAnswerStep):
                        answer, finished = event.output, True
                        break
                    if not isinstance(event, ActionStep) or id(event) in seen:
                        continue
                    seen.add(id(event))
                    steps.append({"step": event.step_number, "code": event.code_action,
                                  "model_output": str(event.model_output or "")[:_SAID],
                                  "observations": str(event.observations or "")[:_SAID],
                                  "error": str(event.error)[:_SAID] if event.error else ""})
                    for call_id in calls.take():
                        yield AgentEvent(kind="tool_result", payload={"call_id": call_id})
                    yield AgentEvent(kind="step", payload={"step": event.step_number,
                                                           "error": bool(event.error)})
        except AgentRefusal:
            raise
        except Exception as exc:  # noqa: BLE001 - the agent's own failure, reported with what it had done
            detail["sandbox"] = session.record()
            raise AgentRefusal(AGENT_FAILED, f"the agent failed: {type(exc).__name__}: {exc}"[:500], detail=detail,
                               backend_failure=True) from None
        detail["code_runs"] = session.runs
        detail["sandbox"] = session.record()   # how its code steps ran, confined or not, from what the sandbox applied
        last = agent.memory.steps[-1] if agent.memory.steps else None
        if not finished or isinstance(getattr(last, "error", None), AgentMaxStepsError):
            raise AgentRefusal(codes.STEP_LIMIT, f"the agent used its {gate.limits.max_steps} steps without "
                                                 "answering", detail=detail)
        output = str(answer)
        claimed = next((line.strip() for line in output.splitlines() if line.strip()), "")[:200]
        yield AgentEvent(kind="finished", payload={"output": output, "claimed_status": claimed, "detail": detail})


__all__ = ["INSTALL", "NOT_INSTALLED", "NO_MODEL", "TOOL_MISSING", "SmolagentsRuntime", "missing"]