---
name: g6-agent-langgraph
description: Use when a multi-step workflow should run as a LangGraph graph that saves its state after every step under G6's state folder, so a run that is stopped, killed or fails resumes where it was without repeating finished steps, with every tool call decided by G6's gate, llm steps only on a model the person passes in, and the result kept as a draft to check.
---

# G6 LangGraph adapter

Runs a LangGraph graph through the agents family's runtime. The graph is a typed spec (state, nodes, edges, entry,
tools) or a `StateGraph` of your own. G6 compiles it with its own checkpoint store, one SQLite file under G6's state
folder, and runs it with each super-step on disk before the next starts. G6's stop, the step limit and the time limit
are checked between super-steps; a run that ends there, or whose process dies, resumes from its last checkpoint and
finishes the rest. A finished graph is a qualified draft.

## When to use it

- A workflow of several steps that must survive a stop, a crash or a failing step without starting again.
- A workflow with branches or loops, bounded by a step limit that G6 enforces.
- Steps that call tools (G6 components' ops, or Python functions of yours) that must each be decided before they run.

## When not to use it

- For one step, or one agent run: call the component, or use `agent_codex` or `agent_claude`, directly.
- To get a checked answer: the graph's state is what its nodes wrote. Check it with `g6.harness`.
- For a step with side effects that cannot be repeated safely, unless you make it idempotent: a step that was running
  when a run crashed runs again on resume.
- Over MCP with an llm step: no model is passed over MCP, so such a graph is refused. Run it from Python with a model.

## Ops

- `run_graph`: start a thread with the graph (`spec`, `thread_id`, `input`, `max_steps`, `deadline_s`, `dry_run`), checkpointed after every super-step; outward tool calls are dry runs unless `dry_run` is false.
- `resume`: continue the thread from its last checkpoint (`spec`, `thread_id`, `max_steps`, `deadline_s`, `dry_run`), with the graph that started it.
- `list_checkpoints`: the thread's checkpoints, newest first (`thread_id`, `limit`).

## Reading the result

`run_graph` and `resume` answer with the runtime's outcome in the library envelope. `qualified-draft` with
`G6_E_AGENT_SELF_REPORTED` (or `G6_E_AGENT_DRY_RUN` when every outward call was a dry
run): `output` is the final state as JSON, and `detail` holds `final_state`, `path` (the nodes that ran in this run),
`thread_id`, `graph` (the spec's digest) and `checkpoint_id`; a resume of a finished thread says `already_finished`.
A dry run of a graph with an llm node asks no model and runs nothing: `G6_E_AGENT_DRY_RUN` with `detail.plan` (its
nodes, its llm nodes, its tools, its limits and the model a run would ask), and the thread is not started.
A tool node writes what its call came to into its key: `verdict` (`run`, `dry_run` or `refused`), its `code`, and
`result` when it ran. `blocked-escalated` codes: `G6_E_AGENT_LANGGRAPH_NOT_INSTALLED`, `G6_E_AGENT_LANGGRAPH_NO_MODEL`,
`G6_E_LLM_DISABLED`, `G6_E_AGENT_LANGGRAPH_THREAD_EXISTS`, `G6_E_AGENT_LANGGRAPH_NO_THREAD`,
`G6_E_AGENT_LANGGRAPH_GRAPH_CHANGED`, `G6_E_AGENT_LANGGRAPH_INPUT_UNDECLARED`, `G6_E_AGENT_LANGGRAPH_NODE_FAILED` and
`G6_E_AGENT_LANGGRAPH_MODEL_FAILED` (with `resume_from`: the nodes a resume runs next), `G6_E_AGENT_LANGGRAPH_TOO_LARGE`
(a value, a checkpoint or the thread would pass its cap; nothing past it was written), `G6_E_AGENT_STEP_LIMIT`,
`G6_E_AGENT_DEADLINE` and `G6_E_STOPPED` (resume the thread to carry on).

Sizes are capped: a filled value, a value a node writes, the starting state and the input hold at most 1 MiB (in
characters); a node's own `value` and `arguments` at most 100,000; one checkpoint, or one batch of a node's writes,
at most 1 MiB; a thread's checkpoints and writes together at most 32 MiB.

## Alone and composed

Alone, over MCP: `g6_invoke("agent_langgraph", "run_graph", params_json)`, then `resume` with the same spec and
thread. From Python: `run_agent(runtime, runtime.task())` with `LangGraphRuntime(spec, thread_id=..., llm=...,
tools={...})`, or `LangGraphRuntime.from_builder(builder, graph_id=..., thread_id=...)` for a graph of your own, whose
nodes call tools through `call_tool(name, arguments)`.

Composed:

- `agent_runtime`: the runtime this adapter runs on; its gate decides every tool call.
- `g6.harness`: `run(standard, executor=as_executor(runtime, runtime.task()))` judges the final state against checks
  frozen first.
- Any component whose ops are not sensitive, as a tool: declare `component.op` in the spec's tools. What the op does
  is the component's word, not the effect you declare: a dry run calls only the ops it declares read-only.

## Failure modes it catches

- `unresumable_workflow` (a long workflow that cannot resume after a failure): every super-step's state, and the
  writes of the steps that finished within a super-step that failed, are in G6's checkpoint store before the next
  super-step starts, so a run stopped by G6, killed with its process or ended by a failing node resumes from its last
  checkpoint, in this process or another, and a node that had finished does not run again. A thread belongs to the
  graph that started it: it is never started twice, and never resumed with another graph.

## Blind spots

- A node that was running when the run crashed or failed runs again on resume; a stop between super-steps never cuts
  one. Make steps with side effects safe to repeat.
- G6's stop and time limit are checked between super-steps, not inside a node: a long node (a slow model, a slow
  tool) runs to its end first. Nodes run in G6's process.
- An llm node reads what is in the state, and text a tool put there can carry instructions the model follows
  (`prompt_injection`); the gate still decides every tool call after it.
- A node of your own graph is your code: a call it makes without `call_tool` is not gated, and a model it asks is
  asked on a dry run too.
- The checkpoint file holds the graph's state as it is: keep secrets out of the state. Anyone who can write G6's state
  folder can change what a resume sees; G6 reads it back without pickle and rebuilds only the types LangGraph lists as
  safe and those you allow (`allowed_types`).
- The graph's state is what its nodes wrote; nothing here checks that it is right.

## What it needs

LangGraph, behind the extra: `pip install 'g6[agents-langgraph]'` (LangGraph and its checkpoint package, MIT, with
their own permissive dependencies). Without it every op says so. From the library it uses `action_gating` and
`safety_engine`, through the family's runtime. A model only for llm nodes, passed in from Python as a `g6.core.llm`
caller. No outside program.

## Examples

```g6-example op=run_graph
{"spec": {"state": {"notes": []}, "entry": "a", "nodes": [{"name": "a", "op": "append", "key": "notes", "value": "first"}, {"name": "b", "op": "append", "key": "notes", "value": "second"}], "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "END"}]}, "thread_id": "example-notes"}
```

A two-step graph: `qualified-draft` with `detail.final_state.notes` `["first", "second"]`, or, when the thread was
started before, `G6_E_AGENT_LANGGRAPH_THREAD_EXISTS`.

```g6-example op=resume
{"spec": {"state": {"notes": []}, "entry": "a", "nodes": [{"name": "a", "op": "append", "key": "notes", "value": "first"}, {"name": "b", "op": "append", "key": "notes", "value": "second"}], "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "END"}]}, "thread_id": "example-notes"}
```

Carries the thread on from its last checkpoint; a finished thread answers `already_finished`.

```g6-example op=list_checkpoints
{"thread_id": "example-notes"}
```

The thread's checkpoints, newest first, each with its step.

```python
from g6.agents.agent_langgraph import GraphSpec, LangGraphRuntime

spec = GraphSpec.model_validate({
    "state": {"n": 0}, "entry": "count",
    "nodes": [{"name": "count", "op": "increment", "key": "n"},
              {"name": "enough", "op": "branch", "key": "n", "value": 3, "then": "END", "else": "count"}],
    "edges": [{"source": "count", "target": "enough"}]})
assert spec.digest().startswith("spec:") and not spec.has_llm
task = LangGraphRuntime(spec, thread_id="count-to-three", max_steps=10).task()
assert task.limits.max_steps == 10 and task.tools == ()   # the loop is bounded by G6's step limit
```

## The four open problems

- **Learning signals**: each run records the path it took, its steps and every tool decision; a resume records where
  it carried on from. Not yet: nothing learns from which nodes fail and are resumed.
- **Evaluators the model cannot influence**: the checkpoint store, the step limit, the gate and the thread rules are
  fixed code; an llm node's text never changes the label. Judging the state is left to checks frozen before the run.
- **Transfer by failure mode**: `unresumable_workflow` is handled by the store and the runtime, so any graph, a spec or
  your own, gets it. Not yet: measured only on the graphs of its tests.
- **Enough held-out cases**: Not yet: its tests stop, kill and fail graphs of a few nodes; long graphs and parallel
  branches are not measured.