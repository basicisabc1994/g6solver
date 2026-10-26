# SPDX-License-Identifier: Apache-2.0
"""AutoGenRuntime: an AutoGen team as an adapter on the agents family's runtime (``g6.agents._runtime``).

The team is a :class:`~g6.agents.agent_autogen.spec.TeamSpec` (G6 builds it: a round robin, a selector or a swarm of
AutoGen ``AssistantAgent``) or a team of your own (:meth:`AutoGenRuntime.from_team`). Either way every agent talks to
the model passed in, through :class:`~g6.agents.agent_autogen.client.G6ChatClient`, and every tool is a
:class:`~g6.agents.agent_autogen.client.GatedTool`, decided by the runtime's gate before each call. A team of your own
is looked into before it starts, and refused (``G6_E_AGENT_AUTOGEN_UNCHECKED_TEAM``) unless it is a round robin,
selector or swarm of plain ``AssistantAgent`` participants, each with G6's chat client, only G6's gated tools and no
memory, and a selector's own model is G6's too: a participant that would run code, wait on a person or call a model or
a tool G6 cannot see is never started.

A dry run (``run_agent``'s default) asks no model and runs nothing: after the refusals a run would meet first, it
answers ``G6_E_AGENT_DRY_RUN`` with ``detail.plan`` (the pattern, the agents, the limits, the model a run would ask
and the tools).

Each message an agent sends is a ``step`` event, so ``run_agent`` checks G6's stop, the step limit (the most messages,
capped by the trusted policy) and the time limit after every message; the team G6 builds also stops itself at that
many messages. The team runs in a thread of its own, paused after each message until G6 has checked it, and G6 waits
for a message with its stop and deadline in view, so a long model call does not hold them off. When a run ends, G6
asks the team's thread to stop and waits for it; it reports ``detail.worker_stopped`` true only when the thread has
stopped. A thread that has not stopped in time (a model call that does not come back) ends the run with
``G6_E_AGENT_AUTOGEN_WORKER_ALIVE`` as a backend failure, which counts in a circuit breaker, and no new team starts
while it is alive. A team that ends at
its message limit without its agents saying the stop text has not finished: ``G6_E_AGENT_STEP_LIMIT``. A team that
finished is ``qualified-draft``: its last message is the output and that message's first line the
``claimed_status``.

No code runs: G6 builds no code-running agent and refuses one in a team of yours (the archive's ``exec_code`` started
``claude -p`` with its shell and edit tools on the server's folder, AGENT_AUTOGEN-01).
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import queue
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import Any

from g6.agents._runtime import (
    AgentEvent,
    AgentRefusal,
    AgentTask,
    Availability,
    GatedCalls,
    Limits,
    Permissions,
    ToolGate,
    ToolSpec,
    codes,
    model_named,
)
from g6.agents.agent_autogen.spec import TeamSpec
from g6.core.llm import LLMCaller, NoModel
from g6.core.resources import ResourceUsage

_P = "G6_E_AGENT_AUTOGEN_"
NOT_INSTALLED = _P + "NOT_INSTALLED"
NO_MODEL = _P + "NO_MODEL"
UNCHECKED_TEAM = _P + "UNCHECKED_TEAM"
TEAM_FAILED = _P + "TEAM_FAILED"
WORKER_ALIVE = _P + "WORKER_ALIVE"
#: The team threads of earlier runs that had not stopped when their run ended; no new team starts while one is alive.
_LIVE: list[threading.Thread] = []
_LIVE_LOCK = threading.Lock()
LLM_DISABLED = "G6_E_LLM_DISABLED"
INSTALL = "AutoGen is not installed: pip install 'g6[agents-autogen] @ git+https://github.com/basicisabc1994/g6solver'"
_SELF = "agent_autogen"
_WAIT_S = 0.2
_JOIN_S = 10.0
_SAID = 4000


def _still_running() -> int:
    """How many team threads of earlier runs are still running."""
    with _LIVE_LOCK:
        _LIVE[:] = [t for t in _LIVE if t.is_alive()]
        return len(_LIVE)


def missing() -> str:
    """Why AutoGen cannot be used here, or ``""`` when it can."""
    try:
        import autogen_agentchat.teams  # noqa: F401
        import autogen_core  # noqa: F401
    except ImportError:
        return INSTALL
    return ""


def team_problems(team: Any) -> list[str]:
    """Why G6 cannot see into ``team`` well enough to run it, in words; ``[]`` when it can (the module docstring)."""
    from autogen_agentchat.agents import AssistantAgent
    from autogen_agentchat.teams import RoundRobinGroupChat, SelectorGroupChat, Swarm
    from autogen_core.tools import StaticWorkbench

    from g6.agents.agent_autogen.client import G6ChatClient, GatedTool

    if type(team) not in (RoundRobinGroupChat, SelectorGroupChat, Swarm):
        return [f"{type(team).__name__} is not a round robin, selector or swarm team"]
    out: list[str] = []
    if type(team) is SelectorGroupChat and not isinstance(team.__dict__.get("_model_client"), G6ChatClient):
        out.append("the selector's own model is not G6's chat client")
    participants = team.__dict__.get("_participants")
    if not isinstance(participants, list) or not participants:
        return out + ["G6 cannot see the team's participants"]
    for agent in participants:
        name = getattr(agent, "name", type(agent).__name__)
        if type(agent) is not AssistantAgent:
            out.append(f"{name} is a {type(agent).__name__}, not a plain AssistantAgent (it could run code, wait on a "
                       "person or run a team G6 cannot see)")
            continue
        seen = agent.__dict__
        if not isinstance(seen.get("_model_client"), G6ChatClient):
            out.append(f"{name}'s model is not G6's chat client")
        if seen.get("_memory"):
            out.append(f"{name} has a memory, which G6 cannot see into")
        benches = seen.get("_workbench")
        if not isinstance(benches, list):
            out.append(f"G6 cannot see {name}'s tools")
            continue
        for bench in benches:
            tools = bench.__dict__.get("_tools") if isinstance(bench, StaticWorkbench) else None
            if not isinstance(tools, list):
                out.append(f"{name} has a {type(bench).__name__}, whose tools G6 cannot see")
            elif any(not isinstance(t, GatedTool) for t in tools):
                out.append(f"{name} has a tool that is not one of G6's gated tools")
    return out


class AutoGenRuntime:
    """See the module docstring. ``spec`` or (through :meth:`from_team`) a team of your own; ``task`` is what the team
    is asked to do; ``llm`` is the model every agent of a spec's team talks to; ``tools`` are Python callables by
    tool name (a declared tool with none is a G6 component's op)."""

    name = "autogen"

    def __init__(self, spec: TeamSpec | None = None, *, task: str, llm: LLMCaller | None = None,
                 tools: Mapping[str, Callable[..., Any]] | None = None, deadline_s: float = 600.0) -> None:
        if not task or not task.strip():
            raise ValueError("a team needs a task")
        self.spec, self.task_text, self.llm, self.tools = spec, task, llm, dict(tools or {})
        self.deadline_s = deadline_s
        self._team: Any = None
        self.team_id = f"spec:{spec.pattern}" if spec is not None else ""
        self._tool_specs: tuple[ToolSpec, ...] = spec.tools if spec is not None else ()
        self._permissions = spec.permissions if spec is not None else Permissions()
        self._max_messages = spec.max_messages if spec is not None else 12
        self._max_tool_calls = spec.max_tool_calls if spec is not None else 20

    @classmethod
    def from_team(cls, team: Any, *, team_id: str, task: str,
                  tool_specs: Sequence[ToolSpec | Mapping[str, Any]] = (), permissions: Permissions | None = None,
                  max_messages: int = 12, max_tool_calls: int = 20, deadline_s: float = 600.0) -> AutoGenRuntime:
        """A team of your own (built with ``G6ChatClient`` and ``GatedTool``), named ``team_id``; ``tool_specs``
        declare its tools."""
        runtime = cls(None, task=task, deadline_s=deadline_s)
        runtime._team, runtime.team_id = team, f"python:{team_id}"
        runtime._tool_specs = tuple(t if isinstance(t, ToolSpec) else ToolSpec.model_validate(t) for t in tool_specs)
        runtime._permissions = permissions or Permissions()
        runtime._max_messages, runtime._max_tool_calls = max_messages, max_tool_calls
        return runtime

    def task(self) -> AgentTask:
        """The task this run is: the team's task, its tools, the permissions they were given and its limits."""
        return AgentTask(goal=self.task_text, tools=self._tool_specs, permissions=self._permissions,
                         limits=Limits(max_steps=self._max_messages, max_tool_calls=self._max_tool_calls,
                                       wall_clock_s=self.deadline_s))

    def available(self) -> Availability:
        """Whether AutoGen imports here; nothing is run."""
        why = missing()
        return Availability(found=not why, detail=why or "AutoGen imports", code=NOT_INSTALLED if why else "")

    def _build(self, limit: int) -> Any:
        from autogen_agentchat.agents import AssistantAgent
        from autogen_agentchat.conditions import MaxMessageTermination, TextMentionTermination
        from autogen_agentchat.teams import RoundRobinGroupChat, SelectorGroupChat, Swarm

        from g6.agents.agent_autogen.client import G6ChatClient, GatedTool

        spec = self.spec
        about = {t.name: t.description for t in spec.tools}
        agents = [AssistantAgent(
            a.name, G6ChatClient(self.llm), description=a.description or f"the agent {a.name}",
            system_message=a.system_message or f"You are {a.name}. Reply with {spec.stop_text} when the task is done.",
            tools=[GatedTool(t, self.tools.get(t), about.get(t, "")) for t in a.tools] or None,
            handoffs=list(a.handoffs) or None) for a in spec.agents]
        stop = MaxMessageTermination(limit + 1) | TextMentionTermination(spec.stop_text)   # the task is one message
        if spec.pattern == "selector":
            return SelectorGroupChat(agents, model_client=G6ChatClient(self.llm), termination_condition=stop)
        if spec.pattern == "swarm":
            return Swarm(agents, termination_condition=stop)
        return RoundRobinGroupChat(agents, termination_condition=stop)

    def run(self, task: AgentTask, *, gate: ToolGate, usage: ResourceUsage,
            stop: Callable[[], str | None]) -> Iterator[AgentEvent]:
        """One run of the team; see the module docstring."""
        why = missing()
        if why:
            raise AgentRefusal(NOT_INSTALLED, why)
        if self.spec is not None:
            if self.llm is None or isinstance(self.llm, NoModel):
                raise AgentRefusal(NO_MODEL, "the team's agents need a model and none was passed in: pass a g6.core.llm "
                                             "caller (AutoGenRuntime(spec, task=..., llm=...)), or set G6_LLM_PROVIDER")
            if os.environ.get("G6_DISABLE_LLM"):
                raise AgentRefusal(LLM_DISABLED, "model calls are disabled by G6_DISABLE_LLM, and every agent of a team "
                                                 "calls a model, so the team was not started")
        limit = gate.limits.max_steps
        if self._team is not None:
            problems = team_problems(self._team)
            if problems:
                raise AgentRefusal(UNCHECKED_TEAM, "G6 does not run this team: " + "; ".join(problems))
        if gate.dry_run:
            yield AgentEvent(kind="finished", payload={"output": "", "detail": self._plan(gate)})
            return
        left = _still_running()
        if left:
            raise AgentRefusal(WORKER_ALIVE, f"{left} team thread(s) of an earlier run have not stopped (one may still "
                                             "be waiting on a model call), so no new team is started until they have",
                               backend_failure=True)
        team = self._team if self._team is not None else self._build(limit)
        calls = GatedCalls(gate, self.tools, own=_SELF)
        transcript: list[dict[str, Any]] = []
        detail: dict[str, Any] = {"team": self.team_id, "transcript": transcript, "limit": limit}
        result: Any = None
        from autogen_agentchat.base import TaskResult
        from autogen_agentchat.messages import BaseChatMessage

        with contextlib.closing(self._stream(team, calls, gate, usage, stop, detail)) as items:
            for item in items:
                if isinstance(item, TaskResult):
                    result = item
                    break
                if not isinstance(item, BaseChatMessage):
                    continue
                transcript.append({"source": item.source, "type": type(item).__name__,
                                   "content": item.to_text()[:_SAID]})
                for call_id in calls.take():
                    yield AgentEvent(kind="tool_result", payload={"call_id": call_id})
                if item.source != "user":
                    yield AgentEvent(kind="step", payload={"source": item.source, "type": type(item).__name__})
        reason = str(getattr(result, "stop_reason", "") or "")
        detail["stop_reason"] = reason
        said = [m for m in transcript if m["source"] != "user"]
        if result is None or "Maximum number of" in reason or not said:
            raise AgentRefusal(codes.STEP_LIMIT, f"the team used its {limit} messages without saying it had finished"
                                                 f"{f' ({reason})' if reason else ''}", detail=detail)
        output = said[-1]["content"]
        claimed = next((line.strip() for line in output.splitlines() if line.strip()), "")[:200]
        yield AgentEvent(kind="finished", payload={"output": output, "claimed_status": claimed, "detail": detail})

    def _plan(self, gate: ToolGate) -> dict[str, Any]:
        """What a dry run answers: what a run would be, with no model asked and nothing run."""
        limits = gate.limits
        if self.spec is not None:
            pattern, agents, model = self.spec.pattern, [a.name for a in self.spec.agents], model_named(self.llm)
        else:
            pattern, model = type(self._team).__name__, "each agent's own G6ChatClient"
            agents = [str(getattr(a, "name", type(a).__name__)) for a in self._team.__dict__.get("_participants") or ()]
        plan = {"team": self.team_id, "pattern": pattern, "agents": agents, "max_steps": limits.max_steps,
                "max_tool_calls": limits.max_tool_calls, "wall_clock_s": limits.wall_clock_s, "model": model,
                "model_calls": 0,
                "tools": [{"name": t.name, "effect": t.effect, "outward": t.outward} for t in self._tool_specs]}
        return {"team": self.team_id, "transcript": [], "limit": limits.max_steps, "dry_run": True, "plan": plan}

    def _stream(self, team: Any, calls: GatedCalls, gate: ToolGate, usage: ResourceUsage,
                stop: Callable[[], str | None], detail: dict[str, Any]) -> Iterator[Any]:
        """The team's messages, one at a time: the team runs in a thread of its own and waits after each message until
        the next is asked for; G6's stop and deadline are watched while it works."""
        from autogen_core import CancellationToken

        from g6.agents.agent_autogen.client import CURRENT

        token = CancellationToken()
        out: queue.Queue[tuple[str, Any]] = queue.Queue()
        go, ending = threading.Semaphore(0), threading.Event()

        def work() -> None:
            CURRENT.set(calls)
            loop = asyncio.new_event_loop()

            async def main() -> None:
                stream = team.run_stream(task=self.task_text, cancellation_token=token)
                try:
                    async for item in stream:
                        out.put(("item", item))
                        go.acquire()                    # the whole team waits here until G6 asks for more
                        if ending.is_set():
                            break
                finally:
                    with contextlib.suppress(BaseException):
                        await stream.aclose()

            try:
                loop.run_until_complete(main())
                out.put(("done", None))
            except BaseException as exc:  # noqa: BLE001 - reported to the run, never lost in the thread
                out.put(("error", exc))
            finally:
                for left in asyncio.all_tasks(loop):
                    left.cancel()
                with contextlib.suppress(BaseException):
                    loop.run_until_complete(asyncio.gather(*asyncio.all_tasks(loop), return_exceptions=True))
                loop.close()

        worker = threading.Thread(target=work, name="g6-autogen-team", daemon=True)
        worker.start()
        ended = "the run ended"
        try:
            while True:
                try:
                    kind, item = out.get(timeout=_WAIT_S)
                except queue.Empty:
                    try:
                        why = stop()
                    except Exception as exc:  # noqa: BLE001 - a stop that cannot be read counts as engaged
                        why = f"the stop could not be read ({type(exc).__name__})"
                    if why:
                        ended = f"G6 is stopped: {why}"
                        raise AgentRefusal(codes.STOPPED, ended, detail=detail) from None
                    if usage.elapsed_seconds >= gate.limits.wall_clock_s:
                        ended = f"the team used its {gate.limits.wall_clock_s:g} seconds"
                        raise AgentRefusal(codes.DEADLINE, ended, detail=detail) from None
                    continue
                if kind == "done":
                    return
                if kind == "error":
                    ended = f"the team failed: {type(item).__name__}: {item}"[:500]
                    raise AgentRefusal(TEAM_FAILED, ended, detail=detail, backend_failure=True)
                yield item
                go.release()
        finally:
            ending.set()
            token.cancel()
            go.release()
            worker.join(_JOIN_S)
            detail["worker_stopped"] = not worker.is_alive()   # true only when G6 saw the thread end
            if worker.is_alive():
                with _LIVE_LOCK:
                    _LIVE.append(worker)
                raise AgentRefusal(WORKER_ALIVE, f"{ended}, and the team's thread did not stop within {_JOIN_S:g} "
                                                 "seconds: it may still be waiting on a model call, whose answer is not "
                                                 "used; no new team starts until it has stopped", detail=detail,
                                   backend_failure=True)


__all__ = ["INSTALL", "NOT_INSTALLED", "NO_MODEL", "UNCHECKED_TEAM", "WORKER_ALIVE", "AutoGenRuntime", "missing",
           "team_problems"]