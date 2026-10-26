# SPDX-License-Identifier: Apache-2.0
"""g6.agents.agent_langgraph: LangGraph graphs on the family's runtime, durable after every super-step (D1,
unresumable_workflow): a graph stopped or killed mid-run resumes from its last checkpoint, in a new process, and every
node that had finished is not run again (AGENT_LANGGRAPH-02). A finished graph is a qualified draft, never ``verified``
(AGENT_LANGGRAPH-01); a tool node goes through the runtime's gate and the registry's untrusted door
(AGENT_LANGGRAPH-03); an llm node uses only the model passed in (AGENT_LANGGRAPH-04). Tests use FakeLLM and G6-owned
Python tools; no model, network or paid service."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("langgraph", reason="LangGraph is not installed (pip install 'g6[agents-langgraph]')")

import g6.agents.agent_langgraph as agent_langgraph_pkg  # noqa: E402
from g6.action_gating.audit import ActionGateAuditLogger  # noqa: E402
from g6.agents._runtime import AgentRuntime, codes, run_agent  # noqa: E402
from g6.agents.agent_langgraph import GraphSpec, LangGraphRuntime, list_checkpoints  # noqa: E402
from g6.agents.agent_langgraph.checkpoints import Checkpoints  # noqa: E402
from g6.core import stop as core_stop  # noqa: E402
from g6.core.llm import FakeLLM  # noqa: E402
from g6.core.registry import get_registry  # noqa: E402
from g6.mcp import invoke_reply  # noqa: E402

MEMORY = ActionGateAuditLogger()
LINE = {"state": {"notes": []},
        "nodes": [{"name": "a", "op": "append", "key": "notes", "value": "a"},
                  {"name": "b", "op": "append", "key": "notes", "value": "b"},
                  {"name": "c", "op": "append", "key": "notes", "value": "c"}],
        "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "c"}, {"source": "c", "target": "END"}],
        "entry": "a"}


@pytest.fixture(autouse=True)
def own_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "g6-home"
    home.mkdir()
    monkeypatch.setenv("G6_HOME", str(home))
    return home


def spec(**changes: object) -> GraphSpec:
    return GraphSpec.model_validate({**LINE, **changes})


def run(runtime: LangGraphRuntime, **kw: object):
    return run_agent(runtime, runtime.task(), audit_logger=MEMORY, **kw)


def invoke(op: str, params: dict) -> dict:
    reply = invoke_reply("agent_langgraph", op, json.dumps(params))
    assert reply["ok"], reply
    return reply["result"]


# --- D1: a stopped or killed graph resumes from its checkpoint, every finished node run once -------------------------

def test_d1_a_graph_stopped_mid_run_resumes_from_its_checkpoint_and_finishes_once() -> None:
    seen: list[int] = []

    def stop_after_first_step() -> str | None:   # the person's stop, engaged once a's super-step is done
        seen.append(1)
        return "stopped by the person" if len(seen) > 3 else None

    first = run(LangGraphRuntime(spec(), thread_id="t1"), stop=stop_after_first_step)
    assert first.state == "blocked-escalated" and first.code == codes.STOPPED
    resumed = run(LangGraphRuntime(spec(), thread_id="t1", resume=True))     # a new runtime, a new connection
    assert resumed.state == "qualified-draft", (resumed.code, resumed.message)
    assert resumed.detail["final_state"]["notes"] == ["a", "b", "c"]          # each node's write exactly once
    assert "a" not in resumed.detail["path"] and resumed.detail["path"][-1] == "c"


CRASH = r'''
import json, os, sys
from g6.agents._runtime import run_agent
from g6.agents.agent_langgraph import GraphSpec, LangGraphRuntime
spec = GraphSpec.model_validate(json.loads(sys.argv[1]))
def boom(**arguments):
    os._exit(17)                       # the process dies in the middle of node b
runtime = LangGraphRuntime(spec, thread_id="crash", tools={"boom": boom})
run_agent(runtime, runtime.task(), dry_run=False)
'''


def test_d1_a_graph_whose_process_dies_mid_node_resumes_in_another_process(own_state: Path) -> None:
    killed = spec(nodes=[{"name": "a", "op": "append", "key": "notes", "value": "a"},
                         {"name": "b", "op": "tool", "tool": "boom", "key": "out"},
                         {"name": "c", "op": "append", "key": "notes", "value": "c"}],
                  state={"notes": [], "out": None}, tools=[{"name": "boom", "effect": "none"}])
    done = subprocess.run([sys.executable, "-I", "-c", CRASH, killed.model_dump_json(by_alias=True)], timeout=120,
                          env={**os.environ, "G6_HOME": str(own_state)}, capture_output=True, text=True)
    assert done.returncode == 17, done.stderr[-2000:]
    calls: list[dict] = []
    resumed = run(LangGraphRuntime(killed, thread_id="crash", resume=True,
                                   tools={"boom": lambda **arguments: calls.append(arguments) or "survived"}))
    assert resumed.state == "qualified-draft", (resumed.code, resumed.message)
    final = resumed.detail["final_state"]
    assert final["notes"] == ["a", "c"] and final["out"]["result"] == "survived" and len(calls) == 1
    assert resumed.detail["path"] == ["b", "c"]


def test_d1_without_a_durable_checkpoint_there_is_nothing_to_resume() -> None:
    reply = run(LangGraphRuntime(spec(), thread_id="never-ran", resume=True))
    assert reply.state == "blocked-escalated" and reply.code == "G6_E_AGENT_LANGGRAPH_NO_THREAD"


# --- AGENT_LANGGRAPH-01: a finished graph is a draft -------------------------------------------------------------------

def test_agent_langgraph_01_a_graph_that_writes_verified_into_its_state_is_a_qualified_draft() -> None:
    claims = spec(state={"notes": [], "status": ""},
                  nodes=[*LINE["nodes"][:2], {"name": "c", "op": "set", "key": "status", "value": "verified"}])
    outcome = run(LangGraphRuntime(claims, thread_id="claims"))
    assert outcome.state == "qualified-draft" and outcome.code == codes.SELF_REPORTED
    assert outcome.envelope()["evidence"][0]["independent_check"] is None
    reply = invoke("run_graph", {"spec": claims.model_dump(mode="json", by_alias=True), "thread_id": "over-mcp"})
    assert reply["completion_state"] == "qualified-draft" and reply["code"] == codes.SELF_REPORTED


# --- AGENT_LANGGRAPH-02: durable, scoped, read back strictly ---------------------------------------------------------

def test_agent_langgraph_02_checkpoints_live_under_g6s_state_folder_and_list_by_thread(own_state: Path) -> None:
    run(LangGraphRuntime(spec(), thread_id="t-list"))
    store = own_state / "agents" / "langgraph_checkpoints.db"
    assert store.is_file()
    rows = list_checkpoints("t-list")
    assert rows and [r["step"] for r in rows] == sorted((r["step"] for r in rows), reverse=True)
    assert invoke("list_checkpoints", {"thread_id": "t-list"})["checkpoints"] == rows
    assert list_checkpoints("another-thread") == []


def test_agent_langgraph_02_a_thread_is_never_restarted_or_resumed_with_another_graph() -> None:
    run(LangGraphRuntime(spec(), thread_id="t-own"))
    again = run(LangGraphRuntime(spec(), thread_id="t-own"))
    assert again.code == "G6_E_AGENT_LANGGRAPH_THREAD_EXISTS"
    other = spec(nodes=[*LINE["nodes"][:2], {"name": "c", "op": "append", "key": "notes", "value": "z"}])
    assert run(LangGraphRuntime(other, thread_id="t-own", resume=True)).code == "G6_E_AGENT_LANGGRAPH_GRAPH_CHANGED"


MARKER = '''
from dataclasses import dataclass
from pathlib import Path
BUILT = []
@dataclass
class Marker:
    note: str
    def __post_init__(self):
        BUILT.append(self.note)
'''


def test_agent_langgraph_02_a_checkpoint_rebuilds_only_the_types_the_person_allows(tmp_path: Path,
                                                                                  monkeypatch) -> None:
    from langgraph.graph import END, StateGraph
    from typing_extensions import TypedDict

    (tmp_path / "g6_marker_mod.py").write_text(MARKER, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    import g6_marker_mod

    class State(TypedDict):
        item: object

    builder = StateGraph(State)
    builder.add_node("make", lambda state: {"item": g6_marker_mod.Marker("from the run")})
    builder.set_entry_point("make")
    builder.add_edge("make", END)
    runtime = LangGraphRuntime.from_builder(builder, graph_id="markers", thread_id="m1", input={"item": None})
    assert run(runtime).state == "qualified-draft"
    g6_marker_mod.BUILT.clear()
    strict = Checkpoints().get_tuple({"configurable": {"thread_id": "m1"}})
    assert not isinstance(strict.checkpoint["channel_values"]["item"], g6_marker_mod.Marker)
    assert g6_marker_mod.BUILT == []                       # its constructor never ran
    allowed = Checkpoints(allowed_types=[("g6_marker_mod", "Marker")]).get_tuple({"configurable": {"thread_id": "m1"}})
    assert isinstance(allowed.checkpoint["channel_values"]["item"], g6_marker_mod.Marker)


# --- AGENT_LANGGRAPH-03: tool nodes go through the gate and the untrusted door ---------------------------------------

def _tool_graph(tool: str, effect: str = "none", **node: object) -> GraphSpec:
    return spec(state={"notes": [], "out": None}, tools=[{"name": tool, "effect": effect}],
                nodes=[{"name": "a", "op": "tool", "tool": tool, "key": "out", **node},
                       {"name": "b", "op": "append", "key": "notes", "value": "b"},
                       {"name": "c", "op": "append", "key": "notes", "value": "c"}])


@pytest.mark.parametrize("tool", ["agent_codex.run", "agent_langgraph.run_graph"])
def test_agent_langgraph_03_a_tool_node_cannot_reach_a_sensitive_op_or_the_graph_runner(tool: str) -> None:
    outcome = run(LangGraphRuntime(_tool_graph(tool, arguments={"goal": "x"}), thread_id=f"t-{tool}"), dry_run=False)
    out = outcome.detail["final_state"]["out"]
    assert out["verdict"] == "refused" and out["code"].startswith("G6_E_"), out
    assert outcome.state == "qualified-draft"   # the refusal is the tool's result; the graph went on


def test_agent_langgraph_03_a_declared_component_op_runs_through_the_registry_door() -> None:
    outcome = run(LangGraphRuntime(_tool_graph("agent_runtime.ops"), thread_id="t-door"), dry_run=False)
    out = outcome.detail["final_state"]["out"]
    assert out["verdict"] == "run" and out["result"]["ops"] == ["check_task", "decide"]
    assert [d.verdict for d in outcome.decisions] == ["run"]


@pytest.mark.parametrize("dry_run", [True, False])
def test_agent_langgraph_03_a_declared_effect_of_none_never_opens_a_g6_op_that_deletes(dry_run: bool) -> None:
    from g6.agents.agent_nanoclaw import Mailbox

    box = Mailbox()
    box.create_group("crew", ["a", "b"])
    box.send("crew", "a", "keep me", to="b")
    graph = _tool_graph("agent_nanoclaw.delete_group", effect="none", arguments={"group": "crew"})
    if dry_run:   # the default graph: a dry run
        outcome = run(LangGraphRuntime(graph, thread_id="t-del-dry"))
    else:         # a real run: the call goes through the registry's door with block_sensitive=True
        outcome = run(LangGraphRuntime(graph, thread_id="t-del-real"), dry_run=False)
    out = outcome.detail["final_state"]["out"]
    assert out["verdict"] == "refused" and out["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED", out
    assert [m["body"] for m in box.history("crew")["messages"]] == ["keep me"]   # the group and its message are there


def test_agent_langgraph_03_an_outward_tool_is_a_dry_run_unless_the_person_says_otherwise() -> None:
    sent: list[dict] = []
    graph = _tool_graph("email", effect="send", arguments={"to": "{notes}"})
    graph = graph.model_copy(update={"permissions": graph.permissions.model_copy(update={"network": True})})
    outcome = run(LangGraphRuntime(graph, thread_id="t-dry", tools={"email": lambda **a: sent.append(a)}))
    assert sent == [] and outcome.detail["final_state"]["out"]["verdict"] in ("dry_run", "refused")


def test_agent_langgraph_03_an_undeclared_tool_is_refused_by_the_spec() -> None:
    bad = dict(LINE, nodes=[{"name": "a", "op": "tool", "tool": "rm", "key": "notes"}], edges=[])
    reply = invoke_reply("agent_langgraph", "run_graph", json.dumps({"spec": bad, "thread_id": "t-bad"}))
    assert not reply["ok"] and "G6_E_INPUT_VALIDATION" in reply["error"]


def test_agent_langgraph_03_a_python_tool_is_called_with_its_filled_arguments_once_the_gate_lets_it() -> None:
    got: list[dict] = []
    graph = _tool_graph("lookup", arguments={"what": "notes so far: {notes}", "items": "{notes}"})
    outcome = run(LangGraphRuntime(graph, thread_id="t-py", tools={"lookup": lambda **a: got.append(a) or 7}))
    assert got == [{"what": "notes so far: []", "items": []}] and outcome.detail["final_state"]["out"]["result"] == 7
    assert [e["kind"] for e in outcome.events].count("tool_result") == 1


# --- AGENT_LANGGRAPH-04: an llm node uses the injected model and nothing else -----------------------------------------

def _llm_graph() -> GraphSpec:
    return spec(state={"notes": ["first"], "summary": ""},
                nodes=[{"name": "a", "op": "llm", "key": "summary", "system": "Be brief.",
                        "prompt": "Summarise: {notes}"}, *LINE["nodes"][1:]])


def test_agent_langgraph_04_an_llm_node_sends_its_filled_prompt_to_the_model_passed_in() -> None:
    llm = FakeLLM(default="One note.")
    outcome = run(LangGraphRuntime(_llm_graph(), thread_id="t-llm", llm=llm), dry_run=False)
    assert outcome.state == "qualified-draft" and outcome.detail["final_state"]["summary"] == "One note."
    assert llm.calls == [("Be brief.", 'Summarise: ["first"]')]


def test_agent_langgraph_04_with_no_model_or_models_disabled_an_llm_graph_never_starts(monkeypatch) -> None:
    assert run(LangGraphRuntime(_llm_graph(), thread_id="t-none")).code == "G6_E_AGENT_LANGGRAPH_NO_MODEL"
    reply = invoke("run_graph", {"spec": _llm_graph().model_dump(mode="json", by_alias=True), "thread_id": "t-mcp"})
    assert reply["completion_state"] == "blocked-escalated" and reply["code"] == "G6_E_AGENT_LANGGRAPH_NO_MODEL"
    monkeypatch.setenv("G6_DISABLE_LLM", "1")
    llm = FakeLLM()
    assert run(LangGraphRuntime(_llm_graph(), thread_id="t-off", llm=llm)).code == "G6_E_LLM_DISABLED"
    assert llm.calls == []
    assert list_checkpoints("t-none") == [] and list_checkpoints("t-off") == []


def test_a_dry_run_of_a_graph_with_an_llm_node_returns_the_plan_and_asks_no_model() -> None:
    llm = FakeLLM(default="One note.")
    outcome = run(LangGraphRuntime(_llm_graph(), thread_id="t-llm-dry", llm=llm))     # the default: a dry run
    assert llm.calls == [], llm.calls                                                  # zero model calls
    assert outcome.state == "qualified-draft" and outcome.code == codes.DRY_RUN, (outcome.code, outcome.message)
    plan = outcome.detail["plan"]
    assert plan["llm_nodes"] == ["a"] and plan["model"] == "FakeLLM" and plan["entry"] == "a"
    assert plan["nodes"] == ["a", "b", "c"] and plan["max_steps"] == 25
    assert list_checkpoints("t-llm-dry") == []                     # nothing ran, so the thread was never started


def test_a_template_reads_state_values_and_never_attributes() -> None:
    graph = spec(state={"notes": [], "out": None}, tools=[{"name": "echo", "effect": "none"}],
                 nodes=[{"name": "a", "op": "tool", "tool": "echo", "key": "out",
                         "arguments": {"x": "{notes.__class__}"}}, *LINE["nodes"][1:]])
    outcome = run(LangGraphRuntime(graph, thread_id="t-tpl", tools={"echo": lambda **a: a}))
    assert outcome.state == "blocked-escalated" and outcome.code == "G6_E_AGENT_LANGGRAPH_NODE_FAILED"


# --- limits, stop, refusals ------------------------------------------------------------------------------------------

def test_a_graph_that_loops_is_ended_at_its_step_limit() -> None:
    loop = spec(state={"n": 0, "notes": []},
                nodes=[{"name": "a", "op": "increment", "key": "n"},
                       {"name": "b", "op": "branch", "key": "n", "value": 1000, "then": "END", "else": "a"}],
                edges=[{"source": "a", "target": "b"}])
    outcome = run(LangGraphRuntime(loop, thread_id="t-loop", max_steps=6))
    assert outcome.state == "blocked-escalated" and outcome.code == codes.STEP_LIMIT


def test_a_graph_whose_state_doubles_each_step_is_refused_at_the_cap_and_the_store_stays_under_it(
        own_state: Path) -> None:
    from g6.agents.agent_langgraph.checkpoints import MAX_CHECKPOINT_BYTES, MAX_THREAD_BYTES

    double = spec(state={"x": "ab", "notes": []}, nodes=[{"name": "a", "op": "set", "key": "x", "value": "{x}{x}"}],
                  edges=[{"source": "a", "target": "a"}])                  # a self-loop: x doubles every super-step
    outcome = run(LangGraphRuntime(double, thread_id="t-double", max_steps=21))
    assert outcome.state == "blocked-escalated" and outcome.code == "G6_E_AGENT_LANGGRAPH_TOO_LARGE", \
        (outcome.code, outcome.message)
    with sqlite3.connect(own_state / "agents" / "langgraph_checkpoints.db") as conn:
        biggest, held = conn.execute("SELECT MAX(LENGTH(checkpoint)), SUM(LENGTH(checkpoint)) FROM checkpoints "
                                     "WHERE thread_id = 't-double'").fetchone()
        writes = conn.execute("SELECT COALESCE(SUM(LENGTH(value)), 0) FROM writes WHERE thread_id = 't-double'"
                              ).fetchone()[0]
    assert biggest <= MAX_CHECKPOINT_BYTES and held + writes <= MAX_THREAD_BYTES


def test_a_checkpoint_past_its_cap_is_refused_and_not_written(own_state: Path) -> None:
    from g6.agents.agent_langgraph.checkpoints import MAX_CHECKPOINT_BYTES

    big = "n" * 600_000                                     # each value under its cap; the two together are not
    pair = spec(state={"a": big, "b": "", "notes": []}, nodes=[{"name": "a", "op": "set", "key": "b", "value": "{a}"}],
                edges=[{"source": "a", "target": "END"}])
    outcome = run(LangGraphRuntime(pair, thread_id="t-pair"))
    assert outcome.code == "G6_E_AGENT_LANGGRAPH_TOO_LARGE" and "checkpoint" in outcome.message, outcome.message
    with sqlite3.connect(own_state / "agents" / "langgraph_checkpoints.db") as conn:
        biggest = conn.execute("SELECT MAX(LENGTH(checkpoint)) FROM checkpoints WHERE thread_id = 't-pair'"
                               ).fetchone()[0]
    assert biggest is not None and biggest <= MAX_CHECKPOINT_BYTES


def test_a_nodes_value_the_starting_state_and_the_input_are_capped_before_anything_runs() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        spec(nodes=[{"name": "a", "op": "set", "key": "notes", "value": "n" * 200_001}], edges=[])
    with pytest.raises(ValidationError):
        spec(state={"notes": ["n" * (1024 * 1024 + 1)]})
    huge = invoke_reply("agent_langgraph", "run_graph", json.dumps({"spec": LINE, "thread_id": "t-huge",
                                                                    "input": {"notes": ["n" * (1024 * 1024 + 1)]}}))
    assert not huge["ok"] and "G6_E_INPUT_VALIDATION" in huge["error"]
    outcome = run(LangGraphRuntime(spec(), thread_id="t-huge-py", input={"notes": ["n" * (1024 * 1024 + 1)]}))
    assert outcome.code == "G6_E_AGENT_LANGGRAPH_TOO_LARGE" and list_checkpoints("t-huge-py") == []


def test_listing_checkpoints_reads_only_as_many_as_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    run(LangGraphRuntime(spec(), thread_id="t-limit"))
    store = Checkpoints()
    read: list[int] = []
    real = Checkpoints._tuple

    def counted(self, cur, row):
        read.append(1)
        return real(self, cur, row)
    monkeypatch.setattr(Checkpoints, "_tuple", counted)
    try:
        assert len(list(store.list({"configurable": {"thread_id": "t-limit"}}, limit=1))) == 1
    finally:
        store.close()
    assert read == [1]                                   # one checkpoint read back, however many the thread holds


def test_a_branch_goes_where_the_state_says() -> None:
    fork = spec(state={"ok": True, "notes": []},
                nodes=[{"name": "a", "op": "branch", "key": "ok", "then": "b", "else": "c"}, *LINE["nodes"][1:]],
                edges=[{"source": "b", "target": "END"}, {"source": "c", "target": "END"}])
    outcome = run(LangGraphRuntime(fork, thread_id="t-fork"))
    assert outcome.detail["final_state"]["notes"] == ["b"]


def test_g4_an_engaged_stop_starts_nothing() -> None:
    core_stop.engage("test stop")
    try:
        outcome = run(LangGraphRuntime(spec(), thread_id="t-stop"))
    finally:
        core_stop.release()
    assert outcome.code == codes.STOPPED and list_checkpoints("t-stop") == []


def test_g3_an_approval_cannot_be_written_into_the_input() -> None:
    for extra in ({"approved": True}, {"reviewer": "the owner"}):
        reply = invoke_reply("agent_langgraph", "run_graph", json.dumps({"spec": LINE, "thread_id": "x", **extra}))
        assert not reply["ok"] and "G6_E_INPUT_VALIDATION" in reply["error"]
    unknown = invoke_reply("agent_langgraph", "run_graph", json.dumps({"spec": LINE, "thread_id": "x",
                                                                       "input": {"not_declared": 1}}))
    assert unknown["result"]["completion_state"] == "blocked-escalated"


def test_without_langgraph_every_op_says_how_to_install_it(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "langgraph", None)
    for op, params in (("run_graph", {"spec": LINE, "thread_id": "x"}), ("resume", {"spec": LINE, "thread_id": "x"}),
                       ("list_checkpoints", {"thread_id": "x"})):
        reply = invoke(op, params)
        assert reply["completion_state"] == "blocked-escalated" and reply["code"] == "G6_E_AGENT_LANGGRAPH_NOT_INSTALLED"
        assert "pip install" in reply["warning_card"]["message"]


def test_resume_over_mcp_finishes_a_stopped_thread() -> None:
    seen: list[int] = []
    run(LangGraphRuntime(spec(), thread_id="t-mcp-resume"), stop=lambda: "stop" if seen.append(1) or len(seen) > 3
        else None)
    reply = invoke("resume", {"spec": LINE, "thread_id": "t-mcp-resume"})
    assert reply["completion_state"] == "qualified-draft" and json.loads(reply["output"])["notes"] == ["a", "b", "c"]
    again = invoke("resume", {"spec": LINE, "thread_id": "t-mcp-resume"})
    assert again["detail"]["already_finished"] is True and again["detail"]["path"] == []


def test_d5_a_graph_of_your_own_runs_with_g6s_checkpoints_and_gated_tools() -> None:
    from langgraph.graph import END, StateGraph
    from typing_extensions import TypedDict

    from g6.agents.agent_langgraph import call_tool

    class State(TypedDict):
        found: object

    def node(state: State) -> dict:
        return {"found": call_tool("agent_runtime.ops")}

    builder = StateGraph(State)
    builder.add_node("look", node)
    builder.set_entry_point("look")
    builder.add_edge("look", END)
    runtime = LangGraphRuntime.from_builder(builder, graph_id="mine", thread_id="own", input={"found": None},
                                            tool_specs=[{"name": "agent_runtime.ops", "effect": "none"}])
    outcome = run(runtime, dry_run=False)
    assert outcome.detail["final_state"]["found"]["result"]["ops"] == ["check_task", "decide"]
    assert [d.tool for d in outcome.decisions] == ["agent_runtime.ops"] and list_checkpoints("own")


def test_the_block_lists_its_ops_none_sensitive_and_the_adapter_implements_the_interface() -> None:
    assert invoke("ops", {})["ops"] == ["run_graph", "resume", "list_checkpoints"]
    assert getattr(get_registry().get_module("agent_langgraph"), "__g6_sensitive_ops__") == ()
    assert isinstance(LangGraphRuntime(spec(), thread_id="x"), AgentRuntime)
    assert agent_langgraph_pkg.__g6_extras__ == {"agents-langgraph": ("langgraph",)}


def test_the_checkpoint_file_is_sqlite_with_g6s_thread_table(own_state: Path) -> None:
    run(LangGraphRuntime(spec(), thread_id="t-sql"))
    with sqlite3.connect(own_state / "agents" / "langgraph_checkpoints.db") as conn:
        rows = conn.execute("SELECT thread_id, graph_digest FROM g6_threads").fetchall()
    assert rows == [("t-sql", spec().digest())]