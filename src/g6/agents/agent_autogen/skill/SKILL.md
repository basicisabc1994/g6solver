---
name: g6-agent-autogen
description: Use when a task should be worked by a team of AutoGen agents (taking turns, picked by a model, or handing the task over) on a model the person passes in, with a message limit G6's trusted policy caps, every tool call decided by G6's gate, G6's stop checked after every message, and the team's answer kept as a draft to check.
---

# G6 AutoGen adapter

Runs an AutoGen team through the agents family's runtime. The team is a typed spec (its pattern, agents, tools and
limit) or a team you built with G6's chat client and gated tools. Every agent talks to the `g6.core.llm` caller passed
in, every tool call is decided by the runtime's gate, and after every message G6 checks its stop, the message limit
(capped by the trusted policy) and the time limit. A team that runs out of messages without saying it has finished is
blocked; one that finished is a qualified draft.

## When to use it

- A task that gains from several roles (a writer and a critic, a planner and a checker), each with its own
  instructions, and a hard limit on how long they may talk.
- A team that hands work between agents (a swarm) or lets a model pick the next speaker (a selector).
- Agents whose tool calls must each be decided before they run.

## When not to use it

- When one agent, or one model call, would do: a team costs a model call per message.
- To get a checked answer: the team's last message is a claim. Check it with `g6.harness`, never with a judge agent
  inside the team.
- For a team that must run code or ask a person mid-run: G6 builds no such agent and refuses one in a team of yours.
- Over MCP on a server whose owner has not chosen it: `run_team` asks a model, which may be paid or online.

## Ops

- `run_team`: run the team on the model you configured for G6 (`team`, `task`, `deadline_s`, `dry_run`), every tool call decided by the gate, stopped at its message limit; a dry run, the default, asks no model and answers the plan; sensitive.
- `check_team`: the team as G6 would run it (`team`, `task`), its limits capped by the trusted ceilings and what the gate does with each tool; nothing runs.

## Reading the result

`run_team` answers with the runtime's outcome in the library envelope. `qualified-draft` with `G6_E_AGENT_DRY_RUN`
(the default): no model was asked and nothing ran; `detail.plan` holds the pattern, the agents, the limits, the model
a run would ask and the tools. `qualified-draft` with
`G6_E_AGENT_SELF_REPORTED`: the agents said the stop text; `output` is the last message and `claimed_status` its first
line; `detail.transcript` holds every message (its sender, its kind and its text) and `detail.stop_reason` AutoGen's
own. A tool call's result, as the model saw it, is `verdict` (`run`, `dry_run` or `refused`), its `code`, and its
`result` when it ran. `blocked-escalated` codes: `G6_E_AGENT_STEP_LIMIT` (the team used its messages without
finishing), `G6_E_AGENT_AUTOGEN_NO_MODEL`, `G6_E_LLM_DISABLED`, `G6_E_AGENT_AUTOGEN_UNCHECKED_TEAM` (a team of yours G6
cannot see into), `G6_E_AGENT_AUTOGEN_NOT_INSTALLED`, `G6_E_AGENT_AUTOGEN_TEAM_FAILED`, `G6_E_STOPPED`,
`G6_E_AGENT_DEADLINE` and `G6_E_AGENT_AUTOGEN_WORKER_ALIVE` (the team's thread had not stopped when the run ended, or
an earlier run's thread is still running). `detail.worker_stopped` is true only when G6 saw the thread end. `check_team` answers `G6_E_AGENT_AUTOGEN_TEAM_CHECKED` with `limits`, `tools` (each one's effect,
whether it is permitted and whether its calls are outward) and `autogen_installed`.

## Alone and composed

Alone, from Python: `run_agent(runtime, runtime.task())` with `AutoGenRuntime(spec, task=..., llm=..., tools={...})`,
or `AutoGenRuntime.from_team(team, team_id=..., task=..., tool_specs=[...])` for a team you built from
`AssistantAgent(name, G6ChatClient(llm), tools=[GatedTool(name, fn)])`. Over MCP: `check_team`, and `run_team` where
the server's owner lists it in `G6_MCP_SENSITIVE`.

Composed:

- `agent_runtime`: the runtime this adapter runs on; its gate decides every tool call.
- `g6.harness`: `run(standard, executor=as_executor(runtime, runtime.task()))` judges the team's answer against
  checks frozen first.
- `g6.debate`: for two sides argued and judged, rather than a team working one task.

## Failure modes it catches

- `unbounded_agent_workflow` (multi-agent workflows run without limits): every message is a step, and the steps are
  capped by the trusted policy's ceiling, whatever the caller asks; the team G6 builds also stops itself at that
  many messages; G6's stop and the time limit are checked after every message and while a model works. A team that
  loops, or agrees with itself, ends at its limit and is reported as not finished, never as done.

## Blind spots

- An agent reads the other agents' messages and the tools' results, and text there can carry instructions its model
  follows (`prompt_injection`); the gate still decides every tool call.
- Whether the team's answer is right is not checked here; agents agreeing is not evidence.
- The tool-calling form is G6's own text protocol over a model that takes text: a model that does not keep to it
  calls no tool, and its reply is read as text.
- A model call already under way when G6 stops a run finishes in the team's thread on its own; nothing is done with
  its answer. G6 waits 10 seconds for the thread; one still running then is reported, counts as a failure, and no
  new team starts until it has stopped.
- A team of yours is looked into through AutoGen's own attributes; an AutoGen release that renamed them makes G6
  refuse the team, not run it unseen.
- The usage AutoGen reports is zero: `g6.core.llm` callers report no tokens.

## What it needs

AutoGen's AgentChat, behind the extra: `pip install 'g6[agents-autogen] @ git+https://github.com/basicisabc1994/g6solver'` (`autogen-agentchat` and `autogen-core`,
MIT, with `jsonref`, `opentelemetry-api`, `protobuf` and Pillow, all permissive). Without it a run says so. A model:
a `g6.core.llm` caller passed in from Python, or, for `run_team`, the one you configured for G6. From the library it
uses `action_gating` and `safety_engine`, through the family's runtime. No outside program.

## Examples

```g6-example op=check_team
{"team": {"pattern": "round_robin", "max_messages": 6, "agents": [{"name": "writer", "system_message": "You draft the note."}, {"name": "critic", "system_message": "You check the note. Say TERMINATE when it is right."}]}, "task": "Draft the release note"}
```

The team as G6 would run it: two agents taking turns, at most six messages, no tools; nothing runs.

```g6-example op=run_team
{"team": {"pattern": "round_robin", "max_messages": 6, "agents": [{"name": "writer"}, {"name": "critic"}]}, "task": "Draft the release note"}
```

Runs on the model you configured for G6; without one it is `G6_E_AGENT_AUTOGEN_NO_MODEL`. Over MCP it runs only
where the server's owner allows it.

```python
from g6.agents.agent_autogen import AutoGenRuntime, TeamSpec

spec = TeamSpec.model_validate({
    "pattern": "swarm", "max_messages": 8,
    "agents": [{"name": "planner", "handoffs": ["checker"]}, {"name": "checker"}],
})
task = AutoGenRuntime(spec, task="Plan the migration").task()
assert task.limits.max_steps == 8 and task.tools == ()   # each message is a step, capped by the trusted policy
```

## The four open problems

- **Learning signals**: each run keeps the transcript, the stop reason and every tool decision. Not yet: nothing
  records which team shapes finish within their limit, which is the signal that would say when a team is worth it.
- **Evaluators the model cannot influence**: the limit, the gate and the label are fixed code; what the agents say
  never changes the label. Judging the answer is left to checks frozen before the run, never to an agent in the team.
- **Transfer by failure mode**: `unbounded_agent_workflow` is handled by the runtime's step limit and the team's own
  stop, for every pattern and for teams of yours. Not yet: measured only on the teams of its tests.
- **Enough held-out cases**: Not yet: its tests drive teams with scripted replies; how often real teams loop or agree
  with themselves is not measured.