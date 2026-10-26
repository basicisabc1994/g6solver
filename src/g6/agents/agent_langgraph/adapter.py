# SPDX-License-Identifier: Apache-2.0
"""LangGraphRuntime: a LangGraph graph as an adapter on the agents family's runtime (``g6.agents._runtime``), durable
after every super-step.

The graph is a :class:`~g6.agents.agent_langgraph.spec.GraphSpec` (the form it takes over MCP) or a ``StateGraph`` of
your own (:meth:`LangGraphRuntime.from_builder`). It is compiled with G6's checkpoint store
(:mod:`~g6.agents.agent_langgraph.checkpoints`, under G6's state folder) and run with ``durability="sync"``, so each
super-step's state is on disk before the next starts. Each super-step is a ``step`` event, so ``run_agent`` checks G6's
stop, the steps and the time limit between super-steps, and ends the run there when one says so; the thread then
resumes (``resume=True``) from its last checkpoint, here or in another process, and a node that had finished does not
run again. A node that was running when the run ended (a stop between super-steps never cuts one; a crash or a failing
node does) runs again on resume, so it should be safe to repeat.

A thread belongs to the graph that started it: starting a thread again is refused (``G6_E_AGENT_LANGGRAPH_THREAD_EXISTS``)
and so is resuming it with another graph (``G6_E_AGENT_LANGGRAPH_GRAPH_CHANGED``). An llm node uses only the model
passed in (a ``g6.core.llm`` caller): without one a graph with an llm node never starts
(``G6_E_AGENT_LANGGRAPH_NO_MODEL``), and with ``G6_DISABLE_LLM`` set neither does it. Every tool call, from a tool node
or from a node of your own through :func:`call_tool`, is decided first by the runtime's gate (declared, permitted,
within the ceilings, ``g6.action_gating`` for its effect, a dry run when it acts outward); a tool is a Python callable
you pass in, or a G6 component's op (``component.op``) called through the registry's door for untrusted callers, so
a sensitive op is refused, and so is this component itself. What the call came to (``verdict`` run, dry_run or
refused, its code, and its result when it ran) is the node's result: a refusal does not end the graph.

A dry run (``run_agent``'s default) of a spec with an llm node asks no model and runs nothing: it answers
``G6_E_AGENT_DRY_RUN`` with ``detail.plan`` (the nodes, the llm nodes, the tools, the limits and the model a run would
ask), and the thread is not started. A graph with no llm node runs on a dry run, its outward tool calls decided and
not run.

A finished run is ``qualified-draft``: the graph's state is what its nodes wrote, never ``verified``.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import typing
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
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
from g6.agents.agent_langgraph.spec import (
    END,
    MAX_VALUE_CHARS,
    TOO_LARGE,
    GraphNode,
    GraphSpec,
    TooLarge,
    fill,
    lookup,
    size_of,
)
from g6.core.llm import DEFAULT_MAX_TOKENS, LLMCaller, LLMError
from g6.core.resources import ResourceUsage

_P = "G6_E_AGENT_LANGGRAPH_"
NOT_INSTALLED = _P + "NOT_INSTALLED"
NO_MODEL = _P + "NO_MODEL"
NO_THREAD = _P + "NO_THREAD"
THREAD_EXISTS = _P + "THREAD_EXISTS"
GRAPH_CHANGED = _P + "GRAPH_CHANGED"
NODE_FAILED = _P + "NODE_FAILED"
MODEL_FAILED = _P + "MODEL_FAILED"
INPUT_UNDECLARED = _P + "INPUT_UNDECLARED"
LLM_DISABLED = "G6_E_LLM_DISABLED"
INSTALL = "LangGraph is not installed: pip install 'g6[agents-langgraph] @ git+https://github.com/basicisabc1994/g6solver'"
THREAD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}")
TOOLS_KEY = "__g6_tools__"
_SELF = "agent_langgraph"


def missing() -> str:
    """Why LangGraph cannot be used here, or ``""`` when it can."""
    try:
        import langgraph.graph  # noqa: F401
    except ImportError:
        return INSTALL
    return ""


def _tools() -> GatedCalls:
    from langgraph.config import get_config

    try:
        tools = (get_config().get("configurable") or {}).get(TOOLS_KEY)
    except RuntimeError:
        tools = None
    if not isinstance(tools, GatedCalls):
        raise RuntimeError("call_tool works only inside a node of a graph that G6's LangGraphRuntime runs")
    return tools


def call_tool(name: str, arguments: Mapping[str, Any] | None = None, paths: Sequence[str] = ()) -> dict[str, Any]:
    """Call ``name`` from a node of your own graph, decided first by the run's gate. Only inside a node of a graph G6
    runs."""
    return _tools().call(name, arguments, paths)


def _node(node: GraphNode, llm: LLMCaller | None) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def run(state: dict[str, Any]) -> dict[str, Any]:
        try:
            update = work(state)
        except TooLarge as exc:
            raise AgentRefusal(TOO_LARGE, f"node {node.name}: {exc}") from None
        for key, value in update.items():
            if size_of(value, MAX_VALUE_CHARS) > MAX_VALUE_CHARS:
                raise AgentRefusal(TOO_LARGE, f"node {node.name} would write more than {MAX_VALUE_CHARS} characters "
                                              f"to {key}, so nothing was written")
        return update

    def work(state: dict[str, Any]) -> dict[str, Any]:
        if node.op in ("pass", "branch"):
            return {}
        if node.op == "set":
            return {node.key: fill(node.value, state)}
        if node.op == "append":
            held = state.get(node.key)
            if not isinstance(held, list):
                raise TypeError(f"node {node.name}: {node.key} holds no list to append to")
            return {node.key: [*held, fill(node.value, state)]}
        if node.op == "increment":
            held = state.get(node.key)
            if isinstance(held, bool) or not isinstance(held, (int, float)):
                raise TypeError(f"node {node.name}: {node.key} holds no number to increment")
            by = int(node.by) if float(node.by).is_integer() and isinstance(held, int) else node.by
            return {node.key: held + by}
        if node.op == "llm":
            if os.environ.get("G6_DISABLE_LLM"):
                raise AgentRefusal(LLM_DISABLED, "model calls are disabled by G6_DISABLE_LLM")
            assert llm is not None   # a graph with an llm node never starts without one
            return {node.key: llm.complete(fill(node.system, state), fill(node.prompt, state),
                                           max_tokens=DEFAULT_MAX_TOKENS)}
        arguments = fill(node.arguments, state)
        return {node.key: _tools().call(node.tool, arguments, [str(p) for p in fill(list(node.paths), state)])}
    run.__name__ = f"g6_{node.op}_{node.name}"
    return run


def _router(node: GraphNode) -> Callable[[dict[str, Any]], str]:
    def route(state: dict[str, Any]) -> str:
        value = lookup(state, node.key)
        go = value == node.value if node.value is not None else bool(value)
        return node.then if go else node.otherwise
    return route


def build(spec: GraphSpec, llm: LLMCaller | None) -> Any:
    """``spec`` as a LangGraph ``StateGraph`` (not compiled)."""
    from langgraph.graph import END as LG_END
    from langgraph.graph import StateGraph

    state = typing.TypedDict("GraphState", {key: typing.Any for key in spec.state})  # type: ignore[misc]
    builder = StateGraph(state)
    for node in spec.nodes:
        builder.add_node(node.name, _node(node, llm))
    builder.set_entry_point(spec.entry)
    target = {END: LG_END}
    for edge in spec.edges:
        builder.add_edge(edge.source, target.get(edge.target, edge.target))
    for node in spec.nodes:
        if node.op == "branch":
            builder.add_conditional_edges(node.name, _router(node),
                                          {t: target.get(t, t) for t in (node.then, node.otherwise)})
    return builder


class LangGraphRuntime:
    """See the module docstring. ``spec`` or (through :meth:`from_builder`) your own ``StateGraph``; ``thread_id``
    names the run's thread; ``resume`` continues it from its last checkpoint instead of starting it; ``llm`` is the
    model for llm nodes; ``tools`` are Python callables by tool name; ``checkpoints`` is the store's file (G6's state
    folder by default); ``allowed_types`` are the ``(module, class)`` pairs a checkpoint may rebuild beyond
    LangGraph's own safe types."""

    name = "langgraph"

    def __init__(self, spec: GraphSpec | None = None, *, thread_id: str, input: Mapping[str, Any] | None = None,
                 resume: bool = False, llm: LLMCaller | None = None,
                 tools: Mapping[str, Callable[..., Any]] | None = None, max_steps: int = 25,
                 max_tool_calls: int = 20, deadline_s: float = 600.0, checkpoints: str | Path | None = None,
                 allowed_types: Sequence[tuple[str, str]] = ()) -> None:
        if not THREAD.fullmatch(thread_id or ""):
            raise ValueError(f"{thread_id!r} is not a thread id: letters, digits and _ . : -, at most 100")
        self.spec, self.thread_id, self.input = spec, thread_id, dict(input or {})
        self.resume, self.llm, self.tools = bool(resume), llm, dict(tools or {})
        self.max_steps, self.max_tool_calls, self.deadline_s = max_steps, max_tool_calls, deadline_s
        self.checkpoints, self.allowed_types = checkpoints, tuple(allowed_types)
        self._builder: Any = None
        self.graph_id = spec.digest() if spec is not None else ""
        self._tool_specs: tuple[ToolSpec, ...] = spec.tools if spec is not None else ()
        self._permissions = spec.permissions if spec is not None else Permissions()

    @classmethod
    def from_builder(cls, builder: Any, *, graph_id: str, thread_id: str, input: Mapping[str, Any] | None = None,
                     tool_specs: Sequence[ToolSpec | Mapping[str, Any]] = (), permissions: Permissions | None = None,
                     **options: Any) -> LangGraphRuntime:
        """A ``StateGraph`` of your own (not compiled), named ``graph_id``; ``tool_specs`` declare the tools its nodes
        may call through :func:`call_tool`."""
        if not THREAD.fullmatch(graph_id or ""):
            raise ValueError(f"{graph_id!r} is not a graph id")
        runtime = cls(None, thread_id=thread_id, input=input, **options)
        runtime._builder, runtime.graph_id = builder, f"python:{graph_id}"
        runtime._tool_specs = tuple(t if isinstance(t, ToolSpec) else ToolSpec.model_validate(t) for t in tool_specs)
        runtime._permissions = permissions or Permissions()
        return runtime

    def task(self) -> AgentTask:
        """The task this run is: its tools, the permissions it was given and its limits."""
        return AgentTask(goal=f"run the graph {self.graph_id} on the thread {self.thread_id}", tools=self._tool_specs,
                         permissions=self._permissions,
                         limits=Limits(max_steps=self.max_steps, max_tool_calls=self.max_tool_calls,
                                       wall_clock_s=self.deadline_s))

    def available(self) -> Availability:
        """Whether LangGraph imports here; nothing is run."""
        why = missing()
        return Availability(found=not why, detail=why or "LangGraph imports", code=NOT_INSTALLED if why else "")

    def run(self, task: AgentTask, *, gate: ToolGate, usage: ResourceUsage,
            stop: Callable[[], str | None]) -> Iterator[AgentEvent]:
        """One run (or resume) of the graph; see the module docstring."""
        why = missing()
        if why:
            raise AgentRefusal(NOT_INSTALLED, why)
        spec = self.spec
        if spec is not None and spec.has_llm:
            if self.llm is None:
                raise AgentRefusal(NO_MODEL, "the graph has an llm node and no model was passed in; from Python, pass "
                                             "one (LangGraphRuntime(spec, llm=...)); over MCP no model is passed")
            if os.environ.get("G6_DISABLE_LLM"):
                raise AgentRefusal(LLM_DISABLED, "model calls are disabled by G6_DISABLE_LLM, and the graph has an "
                                                 "llm node, so it was not started")
        if size_of(self.input, MAX_VALUE_CHARS) > MAX_VALUE_CHARS:
            raise AgentRefusal(TOO_LARGE, f"the input holds more than {MAX_VALUE_CHARS} characters, so the graph was "
                                          "not started")
        if spec is not None:
            extra = sorted(set(self.input) - set(spec.state))
            if extra:
                raise AgentRefusal(INPUT_UNDECLARED, f"the input sets {extra}, which the graph's state does not declare")
            if gate.dry_run and spec.has_llm:   # its llm nodes would ask the model: the plan, and nothing run
                yield AgentEvent(kind="finished", payload={"output": "", "detail": self._plan(spec, gate)})
                return
        from g6.agents.agent_langgraph.checkpoints import Checkpoints

        store = Checkpoints(self.checkpoints, allowed_types=self.allowed_types)
        try:
            yield from self._run(store, gate)
        finally:
            store.close()

    def _plan(self, spec: GraphSpec, gate: ToolGate) -> dict[str, Any]:
        """What a dry run of a graph with an llm node answers: what a run would be, with no model asked."""
        limits = gate.limits
        plan = {"graph": self.graph_id, "nodes": [n.name for n in spec.nodes], "entry": spec.entry,
                "edges": [[e.source, e.target] for e in spec.edges],
                "llm_nodes": [n.name for n in spec.nodes if n.op == "llm"],
                "tools": [{"name": t.name, "effect": t.effect, "outward": t.outward} for t in spec.tools],
                "max_steps": limits.max_steps, "max_tool_calls": limits.max_tool_calls,
                "wall_clock_s": limits.wall_clock_s, "model": model_named(self.llm), "model_calls": 0}
        return {"thread_id": self.thread_id, "graph": self.graph_id, "resumed": self.resume, "dry_run": True,
                "plan": plan}

    def _run(self, store: Any, gate: ToolGate) -> Iterator[AgentEvent]:
        held = store.thread(self.thread_id)
        detail: dict[str, Any] = {"thread_id": self.thread_id, "graph": self.graph_id, "resumed": self.resume,
                                  "checkpoints": str(store.path)}
        if self.resume and held is None:
            raise AgentRefusal(NO_THREAD, f"there is no thread {self.thread_id!r} to resume: it was never started "
                                          "here, or its checkpoints are gone", detail=detail)
        if self.resume and held["graph_digest"] != self.graph_id:
            raise AgentRefusal(GRAPH_CHANGED, f"the thread {self.thread_id!r} was started by another graph "
                                              f"({held['graph_digest']}), so it is not resumed with this one",
                               detail=detail)
        if not self.resume and held is not None:
            raise AgentRefusal(THREAD_EXISTS, f"the thread {self.thread_id!r} was started already: resume it, or start "
                                              "another thread", detail=detail)
        if not self.resume:
            store.claim(self.thread_id, self.graph_id)
        builder = self._builder if self._builder is not None else build(self.spec, self.llm)
        graph = builder.compile(checkpointer=store)
        tools = GatedCalls(gate, self.tools, own=_SELF)
        config = {"configurable": {"thread_id": self.thread_id, TOOLS_KEY: tools},
                  "recursion_limit": gate.limits.max_steps + 5}
        start: Any = None
        if self.resume:
            if not graph.get_state(config).next:
                values = dict(graph.get_state(config).values)
                yield AgentEvent(kind="finished", payload={"output": json.dumps(values, default=str), "detail": {
                    **detail, "already_finished": True, "path": [], "final_state": values}})
                return
        else:
            start = {**(self.spec.state if self.spec is not None else {}), **self.input}
        path: list[str] = []
        try:
            with contextlib.closing(graph.stream(start, config, stream_mode=["updates", "checkpoints"],
                                                 durability="sync")) as stream:
                for mode, chunk in stream:
                    if mode == "checkpoints":
                        meta = chunk.get("metadata") or {}
                        if chunk.get("next") and meta.get("source") != "input":
                            yield AgentEvent(kind="step", payload={"step": meta.get("step"),
                                                                   "next": list(chunk["next"])})
                        continue
                    nodes = [n for n in chunk if not n.startswith("__")]
                    path.extend(nodes)
                    for call_id in tools.take():
                        yield AgentEvent(kind="tool_result", payload={"call_id": call_id})
                    yield AgentEvent(kind="message", payload={"nodes": nodes})
        except AgentRefusal as refusal:
            raise AgentRefusal(refusal.code, refusal.reason, detail={**detail, "path": path}) from None
        except LLMError as exc:
            raise AgentRefusal(MODEL_FAILED, f"an llm node's model failed: {exc}"[:500], backend_failure=True,
                               detail={**detail, "path": path, "resume_from": list(graph.get_state(config).next)}) \
                from None
        except Exception as exc:  # noqa: BLE001 - a node that fails ends the run, resumable from its checkpoint
            limit = type(exc).__name__ == "GraphRecursionError"
            raise AgentRefusal(codes.STEP_LIMIT if limit else NODE_FAILED,
                               f"the graph went past its {gate.limits.max_steps} steps" if limit else
                               f"a node failed: {type(exc).__name__}: {exc}"[:500],
                               detail={**detail, "path": path, "resume_from": list(graph.get_state(config).next)}) \
                from None
        snapshot = graph.get_state(config)
        values = dict(snapshot.values)
        detail |= {"path": path, "final_state": values, "already_finished": False,
                   "checkpoint_id": (snapshot.config.get("configurable") or {}).get("checkpoint_id")}
        yield AgentEvent(kind="finished", payload={"output": json.dumps(values, default=str), "detail": detail})


def list_checkpoints(thread_id: str, *, path: str | Path | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """The thread's checkpoints, newest first: each one's id, its parent's, its step and what wrote it."""
    why = missing()
    if why:
        raise AgentRefusal(NOT_INSTALLED, why)
    from g6.agents.agent_langgraph.checkpoints import Checkpoints

    store = Checkpoints(path)
    try:
        found = store.list({"configurable": {"thread_id": thread_id}}, limit=max(1, min(int(limit), 200)))
        return [{"checkpoint_id": t.config["configurable"]["checkpoint_id"],
                 "parent_id": (t.parent_config or {}).get("configurable", {}).get("checkpoint_id"),
                 "step": t.metadata.get("step"), "source": t.metadata.get("source")} for t in found]
    finally:
        store.close()


__all__ = ["INSTALL", "NOT_INSTALLED", "TOO_LARGE", "LangGraphRuntime", "build", "call_tool", "list_checkpoints",
           "missing"]