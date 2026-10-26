---
name: g6-agent-smolagents
description: Use when a task suits a smolagents agent, either calling declared tools or writing Python to work it out, on a model the person passes in, with every tool call decided by G6's gate and every code step held to a restricted syntax and run in a bounded child process (a clean environment, a time limit, no network, a fixed import list), never in G6's process, and the answer kept as a draft to check.
---

# G6 smolagents adapter

Runs a smolagents agent through the agents family's runtime. G6 builds it from a typed spec: a tool-calling agent,
whose every tool call is decided by the runtime's gate, or a code agent, which takes no tools and whose every code step
is held to a restricted syntax and run in `g6.core.sandbox`. Both talk to the `g6.core.llm` caller passed in. G6's
stop, the step limit (capped by the trusted policy) and the time limit are checked between steps. An agent that runs
out of steps has not finished; one that answered is a qualified draft.

What holds for a code step is a restricted syntax plus a bounded child process with a clean environment and a
deadline. It is not a security boundary: run agents you do not trust inside your own sandbox (a container or VM).

## When to use it

- A task the model can work out by writing a little Python (arithmetic, statistics, text and date handling), where
  the code must never run in G6's process or reach the network.
- A task where the model should call a few declared tools, each call decided before it runs.
- When the run must be bounded in steps and time, and its answer checked by something other than the agent.

## When not to use it

- For code that needs other libraries, files, a shell or the network: a code step may import only a fixed list of
  standard modules, and G6 refuses the rest.
- For code you wrote yourself: run it with `g6.core.sandbox` directly.
- To get a checked answer: the agent's answer is a claim. Check it with `g6.harness`.
- Over MCP on a server whose owner has not chosen it: `run` asks a model, which may be paid or online, and runs the
  code it writes.

## Ops

- `run`: run the agent on the model you configured for G6 (`agent`, `task`, `deadline_s`, `dry_run`), code steps in G6's sandbox and every tool call decided by the gate; a dry run, the default, asks no model and answers the plan; sensitive.
- `check`: the agent as G6 would run it (`agent`, `task`), its sandbox, its allowed imports and what the gate does with each tool; nothing runs.

## Reading the result

`run` answers with the runtime's outcome in the library envelope. `qualified-draft` with `G6_E_AGENT_DRY_RUN` (the
default): no model was asked and nothing ran; `detail.plan` holds the agent's kind, its limits, the model a run would
ask and its tools. `qualified-draft` with `G6_E_AGENT_SELF_REPORTED`:
`output` is the agent's answer and `claimed_status` its first line; `detail.steps` holds each step (its code, what the
code printed, its error) and `detail.code_runs` how many code steps ran in the sandbox. `detail.sandbox` says how
they ran: `os_sandbox` is true only when every code step ran confined by the operating system, `isolation` names the
backend or why there was none, and `runs` holds the limits each step ran under. A step the restricted syntax
refused says `G6_E_AGENT_SMOLAGENTS_CODE_REFUSED`, the construct and its line (a construct off the list, an import
off the list, a refused name or attribute); one the child refused says `G6 refused` and why (a module's private part,
another module it holds, an event its audit hook refuses); a step that ran too long says it took longer than its
limit. `blocked-escalated` codes:
`G6_E_AGENT_STEP_LIMIT` (no answer within the steps), `G6_E_AGENT_SMOLAGENTS_NO_MODEL`, `G6_E_LLM_DISABLED`,
`G6_E_AGENT_SMOLAGENTS_TOOL_MISSING`, `G6_E_AGENT_SMOLAGENTS_NOT_INSTALLED`, `G6_E_AGENT_SMOLAGENTS_AGENT_FAILED`,
`G6_E_STOPPED` and `G6_E_AGENT_DEADLINE`. `check` answers `G6_E_AGENT_SMOLAGENTS_CHECKED` with `limits`, `sandbox`,
`allowed_imports`, `tools` and `smolagents_installed`; for a code agent, `sandbox.isolation` names the operating-system
isolation a code step would run under here, or why there is none, read without starting anything.

## Alone and composed

Alone, from Python: `run_agent(runtime, runtime.task())` with `SmolagentsRuntime(spec, task=..., llm=..., tools={name:
tool})`, where each tool is a smolagents `Tool` of the declared name. Over MCP: `check`, and `run` where the server's
owner lists it in `G6_MCP_SENSITIVE`; over MCP a tool is a G6 component's op, declared as `component.op` and called by
the model as `component__op` with its parameters in `params`.

Composed:

- `agent_runtime`: the runtime this adapter runs on; its gate decides every tool call.
- `g6.harness`: `run(standard, executor=as_executor(runtime, runtime.task()))` judges the answer against checks frozen
  first.
- `g6.core.sandbox`: where every code step runs.

## Failure modes it catches

- `unbounded_code_execution` (an agent running generated code without limits): every code step is held to a
  restricted syntax (an allow-list of constructs, with no `match`, `global`, `class`, `with` or decorator; imports
  from a fixed list only; no `exec`, `eval`, `open`, `getattr` and their like; no private names or attributes; no
  attribute that leads to a running frame), then run in `g6.core.sandbox`: another process with a clean environment,
  a time limit that ends its whole process tree (its own, or the run's remaining time when that is less), capped
  memory, Python's network refused and an audit hook that
  refuses starting a program, `ctypes` and socket connections. Code never runs in G6's process, and nothing the
  caller sends is run as a tool.

## Blind spots

- It is not a security boundary: run agents you do not trust inside your own sandbox (a container or VM). The
  restricted syntax checks how the code is written, not what its values do when it runs; unless `detail.sandbox`
  names an operating-system backend, the child process runs with your account's rights and can read what you can.
- Earlier code steps are run again, quietly, before each new one, so their variables are there: a step that takes a
  long time costs that time again at every later step. `random` is seeded once per run (`detail.random_seed`), so a
  replayed step draws the numbers the model saw; other sources of change, such as the clock, can differ on replay.
- The model reads tool results and what its code printed, and text there can carry instructions it follows
  (`prompt_injection`); the gate still decides every tool call.
- A code agent takes no tools: code in the sandbox cannot reach the gate.
- The tool-call and code forms are smolagents' own, read from the text of a model that takes text; a model that does
  not keep to them loses the step.
- When its steps run out, smolagents asks the model once more for an answer; G6 reports the run as not finished.

## What it needs

smolagents, behind the extra: `pip install 'g6[agents-smolagents]'` (Apache-2.0, with `huggingface-hub`, `rich`,
`jinja2`, `requests`, Pillow and `python-dotenv`, all permissive). Without it a run says so. A model: a `g6.core.llm`
caller passed in from Python, or, for `run`, the one you configured for G6. A Python for the sandbox (this one, or
`G6_PYTHON`). From the library it uses `action_gating` and `safety_engine`, through the family's runtime. No hosted
executor: e2b and Modal are paid online services that would receive the code, so they are not offered. No outside
program.

## Examples

```g6-example op=check
{"agent": {"agent_type": "code", "max_steps": 6, "code_timeout_s": 20}, "task": "What is the mean of 3, 5 and 10?"}
```

A code agent of at most six steps, each code step screened and run in G6's sandbox with a 20 second limit; the reply
lists the imports allowed. Nothing runs.

```g6-example op=run
{"agent": {"agent_type": "code", "max_steps": 6}, "task": "What is the mean of 3, 5 and 10?"}
```

Runs on the model you configured for G6; without one it is `G6_E_AGENT_SMOLAGENTS_NO_MODEL`. Over MCP it runs only
where the server's owner allows it.

```python
from g6.agents.agent_smolagents import AgentSpec
from g6.agents.agent_smolagents.sandbox import ALLOWED_IMPORTS, screen

assert screen("import statistics\nprint(statistics.mean([3, 5, 10]))") == ""
assert "not allowed" in screen("import os\nprint(os.listdir('.'))")
assert "restricted syntax" in screen("class Box:\n    pass")
assert "os" not in ALLOWED_IMPORTS
assert AgentSpec(agent_type="code").tools == ()   # code in the sandbox cannot reach the gate, so it takes no tools
```

## The four open problems

- **Learning signals**: each run keeps every step's code, output and error, and how many code steps ran; refused steps
  say why. Not yet: nothing learns which refusals the model works around and which end its run.
- **Evaluators the model cannot influence**: the screen, the sandbox, the gate and the label are fixed code; what the
  model writes never changes the label. Judging the answer is left to checks frozen before the run.
- **Transfer by failure mode**: `unbounded_code_execution` is handled by the restricted syntax and the sandbox, which
  bound any code the same way, whatever wrote it. Not yet: measured only on the cases of its tests.
- **Enough held-out cases**: Not yet: its tests check that the restricted syntax refuses what is off its list; how
  often a model's own code is refused is not measured.