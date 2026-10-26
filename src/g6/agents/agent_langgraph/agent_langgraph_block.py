# SPDX-License-Identifier: Apache-2.0
"""AgentLangGraphBlock: the registry and MCP door (``g6_invoke``) to the LangGraph adapter.

Three ops, none sensitive: each runs only G6's own code in G6's process, keeps its checkpoints under G6's state folder,
and calls no tool but a G6 component's op through the registry's door for untrusted callers (a sensitive op is
refused there). No model is passed over MCP, so a graph with an llm node is refused before it runs; from Python, pass
one to ``LangGraphRuntime``.

* ``run_graph(spec, thread_id, input, max_steps, deadline_s, dry_run=true)``: start a thread with the graph;
* ``resume(spec, thread_id, ...)``: continue the thread from its last checkpoint, with the graph that started it;
* ``list_checkpoints(thread_id, limit)``: the thread's checkpoints, newest first.

Each answer is the runtime's outcome in the library envelope (``qualified-draft`` at best), or the list.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from g6.agents._runtime import AgentRefusal, codes, run_agent
from g6.agents.agent_langgraph.adapter import INSTALL, NOT_INSTALLED, LangGraphRuntime, list_checkpoints
from g6.agents.agent_langgraph.spec import MAX_VALUE_CHARS, GraphSpec, size_of
from g6.core.ai_block import AIBlock
from g6.core.maturity import component_maturity
from g6.core.reliability_envelope import WarningCard
from g6.core.result import Result

#: None: each op runs only G6's own code, and keeps its state under G6's state folder.
SENSITIVE_OPS: tuple[str, ...] = ()
LISTED = "G6_E_AGENT_LANGGRAPH_CHECKPOINTS_LISTED"
SPEC_REQUIRED = "G6_E_AGENT_LANGGRAPH_SPEC_REQUIRED"
THREAD_REQUIRED = "G6_E_AGENT_LANGGRAPH_THREAD_REQUIRED"
_OPS: dict[str, str] = {
    "run_graph": "run_graph(spec, thread_id, input, max_steps=25, deadline_s=600, dry_run=true): start the thread with "
                 "the graph, checkpointed after every super-step; outward tool calls are dry runs unless dry_run is "
                 "false",
    "resume": "resume(spec, thread_id, max_steps, deadline_s, dry_run=true): continue the thread from its last "
              "checkpoint, with the graph that started it",
    "list_checkpoints": "list_checkpoints(thread_id, limit=20): the thread's checkpoints, newest first",
}
_ACTIONS = {
    NOT_INSTALLED: "Install the extra: pip install 'g6[agents-langgraph]'.",
    LISTED: "Resume the thread with resume, giving the graph that started it.",
    SPEC_REQUIRED: "Give the graph as spec: its state, nodes, edges and entry.",
    THREAD_REQUIRED: "Name the thread in thread_id.",
    "G6_E_AGENT_LANGGRAPH_NO_MODEL": "Run the graph from Python with a model: LangGraphRuntime(spec, llm=...).",
    "G6_E_AGENT_LANGGRAPH_NO_THREAD": "Start the thread with run_graph first.",
    "G6_E_AGENT_LANGGRAPH_THREAD_EXISTS": "Resume the thread with resume, or start another thread_id.",
    "G6_E_AGENT_LANGGRAPH_GRAPH_CHANGED": "Resume with the graph that started the thread, or start a new thread.",
    "G6_E_AGENT_LANGGRAPH_NODE_FAILED": "Fix what the reason names, then resume: nodes that finished do not run again.",
}


class AgentLangGraphInput(BaseModel):
    """One op's parameters; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["run_graph", "resume", "list_checkpoints", "ops", "help"] = "help"
    spec: GraphSpec | None = None
    thread_id: str = Field("", max_length=100, pattern=r"^(|[A-Za-z0-9][A-Za-z0-9_.:-]{0,99})$")
    input: dict[str, Any] = Field(default_factory=dict)
    max_steps: int = Field(25, ge=1, le=200)
    deadline_s: float = Field(600.0, gt=0, le=900, allow_inf_nan=False)
    limit: int = Field(20, ge=1, le=200)
    dry_run: bool = True
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)

    @field_validator("input")
    @classmethod
    def _input_within_its_cap(cls, value: dict[str, Any]) -> dict[str, Any]:
        if size_of(value, MAX_VALUE_CHARS) > MAX_VALUE_CHARS:
            raise ValueError(f"the input holds more than {MAX_VALUE_CHARS} characters")
        return value


def _reply(op: str, state: str, code: str, message: str, data: AgentLangGraphInput, **fields: Any) -> dict[str, Any]:
    card = WarningCard(code=code, message=message, suggested_action=_ACTIONS.get(code, codes.action(code)),
                       severity="blocked" if state == "blocked-escalated" else "amber").model_dump()
    return {"component": "agent_langgraph", "op": op, **fields, "code": code, "message": message,
            "completion_state": state, "warning_card": card,
            "evidence": [{"kind": f"agent_langgraph_{op}", "code": code, **fields}], "request_id": data.request_id,
            "run_id": data.run_id}


def _run(data: AgentLangGraphInput) -> dict[str, Any]:
    if data.spec is None:
        return _reply(data.op, "blocked-escalated", SPEC_REQUIRED, "there is no graph to run", data)
    if not data.thread_id:
        return _reply(data.op, "blocked-escalated", THREAD_REQUIRED, "there is no thread to run the graph on", data)
    runtime = LangGraphRuntime(data.spec, thread_id=data.thread_id, input=data.input, resume=data.op == "resume",
                               max_steps=data.max_steps, deadline_s=data.deadline_s)
    outcome = run_agent(runtime, runtime.task(), dry_run=data.dry_run, request_id=data.request_id, run_id=data.run_id)
    if outcome.code == NOT_INSTALLED:
        return _reply(data.op, "blocked-escalated", NOT_INSTALLED, INSTALL, data)
    return {"component": "agent_langgraph", "op": data.op, **outcome.envelope()}


def _list(data: AgentLangGraphInput) -> dict[str, Any]:
    if not data.thread_id:
        return _reply("list_checkpoints", "blocked-escalated", THREAD_REQUIRED, "there is no thread to list", data)
    try:
        found = list_checkpoints(data.thread_id, limit=data.limit)
    except AgentRefusal as refusal:
        return _reply("list_checkpoints", "blocked-escalated", refusal.code, refusal.reason, data)
    return _reply("list_checkpoints", "qualified-draft", LISTED, f"{len(found)} checkpoint(s) of the thread "
                  f"{data.thread_id!r}, newest first; each is the graph's state after one super-step", data,
                  thread_id=data.thread_id, checkpoints=found)


@component_maturity(
    "beta",
    reason="runs LangGraph graphs checkpointed after every super-step under G6's state folder, with every tool call "
           "decided by the family's gate and llm nodes only on the model passed in; a finished graph is a qualified "
           "draft",
)
class AgentLangGraphBlock(AIBlock[AgentLangGraphInput, dict, dict]):
    """Run, resume and list LangGraph threads under the family's runtime. Never ``verified``."""

    def __init__(self, name: str = "agent_langgraph", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: AgentLangGraphInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        return Result.ok(_list(data) if data.op == "list_checkpoints" else _run(data))


__all__ = ["LISTED", "SENSITIVE_OPS", "AgentLangGraphBlock", "AgentLangGraphInput"]