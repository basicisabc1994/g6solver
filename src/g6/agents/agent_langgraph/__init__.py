# SPDX-License-Identifier: Apache-2.0
"""g6.agents.agent_langgraph — run a LangGraph workflow that saves its state after every step, so a run that is stopped or crashes resumes where it was.

An extension in the agents family, an adapter on its one runtime (``g6.agents._runtime``). The owner, 2026-10-20: the
archive's ``agent_langgraph`` rewritten; its checkpoints were kept in memory on the Python path and in one file under
the home folder rewritten whole on every write on the MCP path, its llm node went through litellm on the platform's
key or ran ``claude -p``, its tool node reached any component in process, and it called a graph ``verified`` when no
node said it failed.

Capability label: ``implemented``.

    from g6.agents._runtime import run_agent
    from g6.agents.agent_langgraph import GraphSpec, LangGraphRuntime
    spec = GraphSpec.model_validate({"state": {"notes": []}, "entry": "a",
                                     "nodes": [{"name": "a", "op": "append", "key": "notes", "value": "a"}],
                                     "edges": [{"source": "a", "target": "END"}]})
    runtime = LangGraphRuntime(spec, thread_id="t1")
    outcome = run_agent(runtime, runtime.task())               # "qualified-draft"; detail["final_state"]
    LangGraphRuntime(spec, thread_id="t1", resume=True)          # after a stop or a crash: carries on from the checkpoint

LangGraph (MIT) is an extra: ``pip install 'g6[agents-langgraph] @ git+https://github.com/basicisabc1994/g6solver'``; without it every op says so. The graph is a typed
spec (:mod:`~g6.agents.agent_langgraph.spec`) or a ``StateGraph`` of your own (``LangGraphRuntime.from_builder``). It
is compiled with G6's checkpoint store (:mod:`~g6.agents.agent_langgraph.checkpoints`: SQLite under G6's state
folder, read back without pickle and rebuilding only the types LangGraph lists as safe and those you allow) and run
with each super-step on disk before the next starts (:mod:`~g6.agents.agent_langgraph.adapter`). G6's stop, the steps
and the time limit are checked between super-steps. A thread belongs to the graph that started it. An llm node uses
only the ``g6.core.llm`` caller you pass in. Every tool call goes through the runtime's gate, and a G6 component's op
is called through the registry's door for untrusted callers. A finished graph is ``qualified-draft``.

Ops (:mod:`~g6.agents.agent_langgraph.agent_langgraph_block`): ``run_graph``, ``resume`` and ``list_checkpoints``,
none sensitive.

Not ported from the archive: the whole-file JSON saver under the home folder, the in-memory saver, litellm and the
platform key, the ``claude -p`` llm node, the tool node that invoked any component in process, ``str(state)`` as the
prompt, the two differing executors and their fixed operation lists (one spec here, and your own graphs), the planner,
the templates and the scaffold.
"""
from g6.agents.agent_langgraph.adapter import LangGraphRuntime, call_tool, list_checkpoints
from g6.agents.agent_langgraph.agent_langgraph_block import SENSITIVE_OPS, AgentLangGraphBlock, AgentLangGraphInput
from g6.agents.agent_langgraph.spec import GraphSpec

__g6_component__ = True
__g6_block__ = "AgentLangGraphBlock"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS
__g6_requires__: tuple[str, ...] = ("action_gating", "safety_engine")  # through the family's runtime (g6.agents._runtime)
__g6_extras__: dict[str, tuple[str, ...]] = {"agents-langgraph": ("langgraph",)}
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (an llm node reads
# what is in the state, and text a tool put there can carry instructions the model follows).
__g6_mitigates__: tuple[str, ...] = ("unresumable_workflow",)
__g6_introduces__: tuple[str, ...] = ("prompt_injection",)
__g6_outside__: tuple = ()

__all__ = ["AgentLangGraphBlock", "AgentLangGraphInput", "GraphSpec", "LangGraphRuntime", "call_tool",
           "list_checkpoints"]