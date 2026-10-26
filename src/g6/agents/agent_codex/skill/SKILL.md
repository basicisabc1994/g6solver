---
name: g6-agent-codex
description: Use when a coding task should be handed to OpenAI's Codex, the command-line coding agent the person installed and signed in to, in one project folder, with no more access than the task needs (read-only unless writes are granted), a deadline, G6's stop, and Codex's own report kept as a claim to check.
---

# G6 Codex adapter

Hands one task to OpenAI's Codex on this machine, through the agents family's runtime. The person names the project
folder and whether Codex may write in it. G6 decides the run before anything starts, starts Codex in that folder with
the least write access that fits (`--sandbox read-only`, or `workspace-write` when writes are granted; never full
access), gives it a clean environment and the prompt on stdin, ends its whole process tree at the deadline or when G6
is stopped, and returns Codex's last message as a qualified draft, with what Codex says of its own work kept as a claim.

## When to use it

- A bounded coding task (explain a failure, review a change, make a failing test pass) in one project folder, with
  Codex installed and signed in.
- When the run must not get more access than it needs: read-only by default, writes only inside the folder, no
  network for the commands Codex runs.
- When the run must be stoppable and bounded in time, and its result checked by something other than Codex.

## When not to use it

- Without a project folder, or with secrets anywhere your account can read: Codex sends what it reads to its model
  under your account, and its sandbox reads beyond the folder.
- For a write run in a folder that is not under version control: G6 refuses it, because you could not see or undo
  what Codex changed.
- For work whose commands need the network (installing packages): G6 turns that off for every run.
- To get a checked answer: Codex's word is a claim. Check the output with `g6.harness`, and send a patch to
  `g6.workforce.submit_patch`.
- To use Codex as a plain model for another component: that belongs in `g6.core.llm`, not here.
- On a server whose owner has not chosen it: a run spends the person's Codex subscription and can change files, so
  both ops are sensitive.

## Ops

- `run`: one Codex run on the workspace (`goal`, `workspace`, `writes`, `model`, `context`, `deadline_s`, `dry_run`), read-only unless `writes`, and a dry run, which starts nothing and needs no Codex installed, unless `dry_run` is false.
- `available`: whether Codex answers its version check, and which Codex it is.

## Reading the result

A `run` reply is the runtime's outcome in the library envelope. `qualified-draft` with `G6_E_AGENT_DRY_RUN`: nothing
started, and `detail.plan` holds the command line, folder and environment names a run would use; when Codex is not
found, `detail.program_missing` says how to install it. `qualified-draft` with `G6_E_AGENT_SELF_REPORTED`: Codex
finished; `output` is its last message and `claimed_status` its first line, which decides nothing; `detail` holds the
sandbox, the exit code, the command line, the environment names passed and the end of Codex's output. The run's
decision carries `gate.not_evaluated`: what the action gate did not judge. `blocked-escalated` codes:
`G6_E_OUTSIDE_PROGRAM_MISSING` (with the install steps), `G6_E_AGENT_WORKSPACE`, `G6_E_AGENT_NOT_VERSIONED` (a write
run in a folder not under version control), `G6_E_AGENT_CODEX_GOAL_REQUIRED`, `G6_E_AGENT_GATE_BLOCKED` (the action
gate held the folder for a person, as it does a secret-looking one), `G6_E_LLM_DISABLED` (model calls are switched
off), `G6_E_STOPPED`, `G6_E_AGENT_DEADLINE`, `G6_E_AGENT_EXIT_FAILED` (Codex exited with an error, often its sign-in),
`G6_E_AGENT_OUTPUT_TOO_LARGE`, `G6_E_AGENT_CLEANUP_UNCONFIRMED` and `G6_E_AGENT_CIRCUIT_OPEN` (three failures in a
row keep Codex off for a minute). A run ended early keeps what G6 knew in `detail`, and a write run ended early says
files in the folder may be half-changed: look at `git status` and `git diff` before you go on. `available` answers
`G6_E_AGENT_CODEX_FOUND` with `where` and `version`, or `G6_E_STOPPED` while G6 is stopped.

## Alone and composed

Alone, from Python: `get_registry().invoke("agent_codex", "run", {"goal": ..., "workspace": ..., "dry_run": False})`,
or `run_agent(CodexRuntime(), CodexRuntime.task(goal, workspace, writes=True), dry_run=False)`. Over MCP:
`g6_invoke("agent_codex", "run", params_json)`, only when the person who runs the server lists `agent_codex` (or
`agent_codex.run`) in `G6_MCP_SENSITIVE`; otherwise it is refused with `G6_E_SENSITIVE_COMPONENT_DENIED`.

Composed:

- `agent_runtime`: the runtime this adapter runs on. Its gate decides each run (declared, permitted, inside the
  workspace, within the trusted ceilings, allowed by `action_gating`), and `check_task` and `decide` show that decision
  before you run.
- `g6.harness`: `run(standard, executor=as_executor(CodexRuntime(), task, dry_run=False))` judges Codex's output
  against checks you froze first.
- `g6.workforce.submit_patch`: checks a patch Codex made.
- `coding_agent_gate`: checks a risky plan before you hand it to Codex.

## Failure modes it catches

- `overprivileged_agent` (a coding agent run with more access than it needs): Codex starts read-only unless the person
  grants writes, never with full access or a flag that skips its sandbox or approvals; only in the folder the person
  named, never G6's own state folder or installed code, a drive's root, or the home folder or a folder that holds it;
  with four settings stated on every run (no approval prompt, no network for its commands, nothing writable beyond
  the folder, not `/tmp`), so your Codex configuration and a repository's cannot widen those four; with only the
  environment a program needs to start, so no key or setting of G6's is passed to it; and only after the action gate
  has allowed the folder. It narrows what Codex may write and run, not what it may read: see the blind spots.

## Blind spots

- Codex's read-only sandbox reads the whole disk: any file your account can read, your `~/.g6/.env` (G6's key file)
  and your `~/.ssh` included, can be read, and Codex sends it to its model under your account (`leaked_secret`). The
  action gate holds a folder whose name looks secret; it does not stop Codex reading outside the folder.
- A file in the folder (a README, a test, a comment) can carry instructions that Codex follows as if you had given
  them (`prompt_injection`); with writes granted, it acts on them inside the folder.
- G6 states four settings only: no approval prompt, and the network, writable folders and `/tmp` of
  `workspace-write`. The rest of your own Codex configuration still applies: its MCP servers, which run outside Codex's
  sandbox with your rights, its profiles, and web search when you turned it on. Overriding `mcp_servers` is untried
  against a real Codex, so G6 does not pass it.
- Codex's own sandbox enforces read-only and `workspace-write`; G6 trusts its implementation, which differs by
  operating system.
- The settings G6 states are Codex configuration keys; a Codex release that renamed one would ignore it, and G6 does
  not read Codex's effective settings back.
- G6 sees none of the commands Codex runs inside its run, and not what it changed: compare the folder, or send the
  patch to `g6.workforce.submit_patch`.
- The action gate judges a run as reading (or writing) the folder; the online side, sending the workspace and the task
  to Codex's model provider, is not evaluated by `action_gating`, and the run's decision says so. Your `dry_run=false`
  on the sensitive `run` op is what authorises it.
- Codex's sign-in, model and other settings come from its own configuration in your home folder.
- The circuit breaker counts failures in this process only.

## What it needs

OpenAI's Codex, an outside program you install and sign in to yourself (Apache-2.0; paid and online: it runs on your
own Codex subscription): `npm install -g @openai/codex`, then `codex login`. G6 finds it through `G6_CODEX` or as
`codex` on PATH, and never installs it, bundles it, signs in for you or passes it a key. A dry run needs none of
this. A write run needs the folder under version control (`git init`, then a first commit), so you can see and undo
what Codex changed. From the library it uses `action_gating` and `safety_engine`, through the family's runtime. No
pip extra.

## Examples

```g6-example op=run
{"goal": "Explain why the test in tests/test_parse.py fails", "workspace": "/path/to/your/project", "dry_run": true}
```

Put the full path of your project folder in `workspace`. As written, the placeholder folder does not exist, so the
reply is `blocked-escalated` with `G6_E_AGENT_WORKSPACE`. With a real folder it is a dry run: `qualified-draft` with
`G6_E_AGENT_DRY_RUN`, and `detail.plan.argv` is the codex program, then `exec --sandbox read-only`. Nothing starts, and
Codex need not be installed. Over MCP it runs only when the server's owner allows it.

```g6-example op=available
{}
```

The reply names the Codex that a run would start, or says how to install it.

```python
from g6.agents.agent_codex import CodexRuntime

runtime = CodexRuntime(model="gpt-5-codex")
argv = runtime.argv("codex", writes=False, last_message="last.txt")
assert argv[:4] == ["codex", "exec", "--sandbox", "read-only"]
assert "sandbox_workspace_write.network_access=false" in argv and argv[-1] == "-"  # the prompt goes on stdin
assert not [a for a in argv if "danger" in a or "bypass" in a]

task = CodexRuntime.task("Explain the failing test", "/path/to/project")
assert task.permissions.write_files is False and task.tools[0].online  # read-only, and an outward action
```

## The four open problems

- **Learning signals**: each outcome records the sandbox mode, the gate's decision, the exit code and Codex's claim,
  apart from one another. Not yet: nothing links a run's sandbox mode to whether its patch later passed the checks of
  `g6.workforce`, which is the signal that would say when read-only was enough.
- **Evaluators the model cannot influence**: the sandbox mode, the command line and the environment are fixed code
  from what the person declared, and nothing Codex says changes the label. Judging its output is left to checks frozen
  before the run.
- **Transfer by failure mode**: the posture is bound to `overprivileged_agent` through the runtime's permissions, which
  any command-line agent has. Not yet: shown only for Codex.
- **Enough held-out cases**: Not yet: its tests drive a stand-in program, never the real Codex; how often Codex keeps
  the stated posture is not measured.