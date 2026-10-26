---
name: g6-agent-runtime
description: Use when an agent runtime (a coding agent's command line, a framework's agent loop, your own adapter) is to run a task under limits a person set, with every tool call decided before it runs, the run stoppable, every failure handled the same way, and the agent's own "done" kept as a claim rather than taken as proof.
---

# G6 agent runtime

The one runtime every agent adapter in G6's agents family goes through. A person declares the task: its goal, the
tools the agent may call and what each does, the permissions (a workspace, and reading, writing, a shell or the
network inside it) and the limits. The runtime caps the limits by trusted ceilings, decides every tool call before it
runs, checks G6's stop before each step, turns whatever an adapter does wrong into an outcome with a stable code, and
labels a finished run a qualified draft at best. The agent's own report ("done", "tests passed") is kept apart, as a
claim.

## When to use it

- Before you let an agent act: check the task (`check_task`) to see the limits it would really get and which of its
  tools it may call.
- Before an agent runs one tool: ask the gate (`decide`) whether that call may run, and why not.
- From Python, to run an adapter (such as the Codex one) with `run_agent`, or to hand its output to `g6.harness.run`
  through `as_executor`, so checks you froze first judge it.
- When you write an adapter for another agent framework: implement `AgentRuntime` and let the runtime do the rest.

## When not to use it

- To judge whether the agent's output is right: that is `g6.harness` (checks frozen before the run), or for a patch,
  `g6.workforce.submit_patch`. This runtime never says the output is right.
- To decide one outward action that is not an agent's tool call: `action_gating`.
- For limits across a whole operator loop with budgets and an approval queue: `g6.operator`.
- As an operating-system sandbox: it is not one. An adapter runs as you; a command-line agent's own sandbox governs
  what that agent does inside its run.

## Ops

- `check_task`: the task as G6 would run it: limits capped by the trusted ceilings, the permissions, and what each tool does, whether it needs the network permission (an online tool does), whether it is permitted, whether the action gate sees it and whether its calls are outward; nothing is run.
- `decide`: the gate's decision for one proposed tool call within a task (`run`, `dry_run` or `refused`), with its code, its reason and what the action gate decided; nothing is run.

## Reading the result

`check_task` answers `qualified-draft` with `G6_E_AGENT_TASK_CHECKED`, or `blocked-escalated` with
`G6_E_AGENT_WORKSPACE` when the workspace is not an existing folder named by its full path, or is a drive's root, your
home folder or a folder that holds it, or is, holds or sits inside G6's own state folder (`$G6_HOME`, with its key
file, audit trail and stop) or installed code. Its `limits` are what a run would get: the default policy's ceilings
are 25 steps, 50 tool calls and 900 seconds, and a caller's larger number is lowered to them.

`decide`'s `decision.verdict` is `run`, `dry_run` (decided, not run: an outward call while the run is a dry run, the
default) or `refused`. A refused call is `blocked-escalated` with the decision's own code: `G6_E_AGENT_TOOL_UNDECLARED`,
`G6_E_AGENT_PERMISSION`, `G6_E_AGENT_PATHS_REQUIRED` (a file tool that names no file), `G6_E_AGENT_OUTSIDE_WORKSPACE`,
`G6_E_AGENT_TOOL_LIMIT`, `G6_E_AGENT_POLICY_BLOCKED`, `G6_E_AGENT_GATE_BLOCKED` (the action gate did not allow it; its
decision and message are in `decision.gate`), `G6_E_AGENT_GATE_ERROR` (the gate could not decide, so it refused) or
`G6_E_STOPPED`. `G6_E_AGENT_TASK_REQUIRED` and `G6_E_AGENT_CALL_REQUIRED` say what the op was not given.

A run's outcome (Python) is `qualified-draft` with `G6_E_AGENT_SELF_REPORTED` (the agent finished; its words are a
claim), or `G6_E_AGENT_DRY_RUN` (its outward calls were all dry runs). Every other code is `blocked-escalated`:
`G6_E_AGENT_NOT_A_RUNTIME`, `G6_E_AGENT_FAILED` (the adapter raised an error), `G6_E_AGENT_BAD_EVENT`,
`G6_E_AGENT_NO_OUTCOME`, `G6_E_AGENT_STEP_LIMIT`, `G6_E_AGENT_DEADLINE`, `G6_E_RESOURCE_LIMIT`,
`G6_E_AGENT_CIRCUIT_OPEN` (it failed several times in a row), `G6_E_AGENT_UNGATED_TOOL` (it reported a tool the gate
never let run), `G6_E_AGENT_WORKSPACE`, `G6_E_STOPPED`, or an adapter's own code. `claimed_status` is the agent's word
and decides nothing; the evidence's `independent_check` is empty until a check of yours runs.

## Alone and composed

Alone, over MCP: `g6_invoke("agent_runtime", "check_task", {"task": {...}})` and `g6_invoke("agent_runtime", "decide",
{"task": {...}, "call": {...}})`. A task is `{"goal", "context", "tools": [{"name", "effect", "online"}],
"permissions": {"workspace", "read_files", "write_files", "shell", "network"}, "limits": {"max_steps",
"max_tool_calls", "wall_clock_s"}}`; an effect is `none`, `read`, `write`, `delete`, `shell`, `network`, `send` or
`pay`, and `online` marks a tool that sends what it is given off this machine. A call is `{"tool", "arguments",
"paths", "call_id"}`. Any other field is refused, so an approval cannot be written in.

From Python: `from g6.agents._runtime import AgentTask, Permissions, ToolSpec, run_agent`, then `run_agent(adapter,
task)`. It takes `policy` (the trusted `g6.safety_engine.RuntimePolicy`, never the caller's), `bounds`
(`g6.core.resources.ResourceBounds`), `dry_run` (true by default: outward calls are decided, not run), `breaker` and
`audit_logger`.

Composed:

- `g6.harness`: `run(standard, executor=as_executor(adapter, task))` runs the agent once and judges its output against
  the standard's frozen checks; a blocked run is the executor's failure, with its code.
- `action_gating`: every tool call with an effect, and everything outside the workspace, is evaluated there and
  recorded in its audit trail.
- `safety_engine`: its policy engine applies the trusted ceilings and forbidden paths to each call.
- `agent_codex`: an adapter on this runtime for the Codex command-line agent.

## Failure modes it catches

- `unhandled_agent_failure` (each agent handling failure differently, or not at all): every adapter runs through
  `run_agent`, which refuses an object that is not an adapter, checks the stop and the limits before each step, and
  turns any error, malformed event, missing finish, step or time overrun, or unreported tool into a
  `blocked-escalated` outcome with its own code, never a raised error. A deterministic circuit breaker refuses an
  adapter that keeps failing until its cooldown passes, and counts a backend answering with a server error as failing
  while a rate limit only backs off. Only a finished run that was not a dry run closes it; an outcome that shows nothing
  of the backend (a dry run, a stop, a refusal before it ran) releases its one trial run, so it never stays open for
  good.

## Blind spots

- An in-process adapter that runs a tool without asking the gate and never reports it is not seen: the runtime
  checks only the results an adapter reports.
- A command-line agent's own tool use is governed by its own sandbox. The gate decides whether it starts, and on which
  folder with which access; it does not see each command inside the run.
- A tool's effect is what the task's author declares: a tool declared `none` that in fact writes is judged as
  declared.
- An online tool that reads, writes or runs a shell is judged by the action gate as that file or shell action only.
  The online side, sending the workspace and the task to the agent's model provider, is not evaluated by
  `action_gating`, and each such decision says so in `decision.gate.not_evaluated`: the person's `dry_run=false` on a
  sensitive op is what authorises it. An online tool needs the `network` permission.
- The action gate holds every shell, network, send and pay call for a person, and takes no approval, so an agent
  that needs them cannot run them through G6 yet. It holds every delete too, and a write in a workspace not under
  version control: the runtime names the workspace's `.git` as a write's checkpoint, which the gate reads for being
  there, not for the file's earlier content.
- `decide` uses a fresh gate for each call, so ceilings that count calls apply only inside one `run_agent`.
- The stop is a check between steps: an in-process adapter finishes the step it is in; a command-line agent is
  polled while it runs and its process tree ended.
- It declares no failure mode of its own use.

## What it needs

Nothing outside G6: pure Python with pydantic. It uses `action_gating` (whose audit trail is
`action_gating_audit.db` under `$G6_HOME`) and `safety_engine` from the library. No model is called.

## Examples

```g6-example op=check_task
{"task": {"goal": "Tidy the notes", "tools": [{"name": "lookup", "effect": "none"}, {"name": "email", "effect": "send"}], "limits": {"max_tool_calls": 500}}}
```

The reply is `qualified-draft`: `limits.max_tool_calls` is 50, the ceiling, and `email` is not permitted, because the
task grants no network.

```g6-example op=decide
{"task": {"goal": "Tidy the notes", "tools": [{"name": "email", "effect": "send"}]}, "call": {"tool": "email", "arguments": {"to": "customer@example.com"}}}
```

The reply is `blocked-escalated` with `G6_E_AGENT_PERMISSION`: sending needs the network permission, which the task
does not grant. Nothing was sent.

```python
from g6.action_gating.audit import ActionGateAuditLogger
from g6.agents._runtime import AgentEvent, AgentTask, Availability, ToolCall, ToolSpec, codes, run_agent


class Notes:
    """A small adapter: it asks the gate before each call and reports what it ran."""

    name = "notes"

    def available(self):
        return Availability(found=True, detail="pure Python")

    def run(self, task, *, gate, usage, stop):
        yield AgentEvent(kind="step")
        if gate.decide(ToolCall(tool="lookup", call_id="c1")).runs:
            yield AgentEvent(kind="tool_result", payload={"call_id": "c1", "result": "3 notes"})
        gate.decide(ToolCall(tool="email", call_id="c2"))  # not declared, so refused
        yield AgentEvent(kind="finished", payload={"output": "Tidied 3 notes.", "claimed_status": "done, verified"})


class Broken(Notes):
    def run(self, task, *, gate, usage, stop):
        yield AgentEvent(kind="step")
        raise KeyError("a bug in the adapter")


task = AgentTask(goal="Tidy the notes", tools=(ToolSpec(name="lookup", effect="none"),))
memory = ActionGateAuditLogger()  # in memory for this example; the default trail is a file under $G6_HOME
outcome = run_agent(Notes(), task, audit_logger=memory)
assert outcome.state == "qualified-draft" and outcome.code == codes.SELF_REPORTED
assert outcome.claimed_status == "done, verified"  # kept apart: it decides nothing
assert [d.verdict for d in outcome.decisions] == ["run", "refused"]
assert outcome.decisions[1].code == codes.TOOL_UNDECLARED

failed = run_agent(Broken(), task, audit_logger=memory)
assert failed.state == "blocked-escalated" and failed.code == codes.FAILED  # reported, never raised
```

## The four open problems

- **Learning signals**: each outcome records its code, every gate decision with its verdict and reason, the agent's
  claim apart from it, and the usage; the action gate keeps its own audit trail. Not yet: nothing learns from them,
  and the circuit breaker only counts failures in a row.
- **Evaluators the model cannot influence**: the gate and the run loop are fixed code with no model call, the label is
  set from what the runtime saw and never from the agent's words, and judging the output is left to checks frozen
  before the run (`g6.harness`). The agent still writes its own calls, and the task's author declares each tool's
  effect.
- **Transfer by failure mode**: the rules are bound to `unhandled_agent_failure` and to what every tool call has (an
  effect, the paths it touches, a workspace), not to one framework. Not yet: they are exercised only on scripted
  adapters and the Codex adapter.
- **Enough held-out cases**: Not yet: its catch rate on adapters it was not written around is not measured; its tests
  are fixed regressions.
