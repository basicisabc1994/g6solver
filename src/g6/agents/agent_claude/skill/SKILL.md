---
name: g6-agent-claude
description: Use when a coding task should be handed to Anthropic's Claude Code, the command-line coding agent the person installed and signed in to, in one project folder, with its own tools named on every run (read tools only unless edits are granted, never its shell or web tools), a deadline, G6's stop, and its own report kept as a claim to check.
---

# G6 Claude Code adapter

Hands one task to Anthropic's Claude Code on this machine, through the agents family's runtime. The person names the
project folder and whether Claude Code may edit in it. G6 decides the run before anything starts, starts `claude -p`
in that folder with each of its tools named (read tools allowed, edit tools only when edits are granted, its shell,
web tools and sub-agents denied), in its default permission mode, with no MCP server and none of the repository's own
Claude Code settings, gives it a clean environment and the prompt on stdin, ends its whole process tree at the
deadline or when G6 is stopped, and returns its result as a qualified draft, with what it says of its own work kept as
a claim.

## When to use it

- A bounded coding task (explain a failure, review a change, make a failing test pass by editing) in one project
  folder, with Claude Code installed and signed in.
- When the run must not get more tools than it needs: read tools by default, edit tools only when granted, never a
  shell or the web.
- When the run must be stoppable and bounded in time and turns, and its result checked by something other than
  Claude Code.

## When not to use it

- Without a project folder, or with secrets anywhere your account can read: Claude Code sends what it reads to its
  model under your account, and its read tools read beyond the folder.
- For a task that needs commands run (tests, builds, package installs): G6 never grants Claude Code's shell, because
  it would run with your rights and no sandbox G6 can state. Use `agent_codex`, whose commands run in Codex's sandbox,
  or run the commands yourself.
- For an edit run in a folder that is not under version control: G6 refuses it.
- To get a checked answer: Claude Code's word is a claim. Check the output with `g6.harness`, and send a patch to
  `g6.workforce.submit_patch`.
- To use Claude as a plain model for another component: that is `g6.core.llm`'s `AnthropicCaller`, not this.
- On a server whose owner has not chosen it: a run spends the person's Claude subscription and can change files, so
  both ops are sensitive.

## Ops

- `run`: one Claude Code run on the workspace (`goal`, `workspace`, `writes`, `model`, `context`, `max_turns`, `deadline_s`, `dry_run`), read tools only unless `writes`, and a dry run, which starts nothing and needs no Claude Code installed, unless `dry_run` is false.
- `available`: whether Claude Code answers its version check, and which Claude Code it is.

## Reading the result

A `run` reply is the runtime's outcome in the library envelope. `qualified-draft` with `G6_E_AGENT_DRY_RUN`: nothing
started, and `detail.plan` holds the command line, folder and environment names a run would use; when Claude Code is
not found, `detail.program_missing` says how to install it. `qualified-draft` with `G6_E_AGENT_SELF_REPORTED`: Claude
Code finished; `output` is its result and `claimed_status` its first line, which decides nothing; `detail` holds the
tools allowed and denied, the turns it took, the tokens and cost it reported (as it reported them), the tools its own
permission system refused (`permission_denials`), the exit code and the end of what it printed. The run's decision
carries `gate.not_evaluated`: what the action gate did not judge. `blocked-escalated` codes:
`G6_E_OUTSIDE_PROGRAM_MISSING` (with the install steps), `G6_E_AGENT_WORKSPACE`, `G6_E_AGENT_NOT_VERSIONED`,
`G6_E_AGENT_CLAUDE_GOAL_REQUIRED`, `G6_E_AGENT_GATE_BLOCKED` (the action gate held the folder for a person),
`G6_E_LLM_DISABLED`, `G6_E_STOPPED`, `G6_E_AGENT_DEADLINE`, `G6_E_AGENT_STEP_LIMIT` (it used its turns),
`G6_E_AGENT_EXIT_FAILED` (an error exit or an error result, often its sign-in: run `claude` and sign in),
`G6_E_AGENT_NO_OUTCOME` (it printed no result), `G6_E_AGENT_OUTPUT_TOO_LARGE`, `G6_E_AGENT_CLEANUP_UNCONFIRMED` and
`G6_E_AGENT_CIRCUIT_OPEN` (three failures in a row keep it off for a minute). An edit run ended early says files may be
half-changed: look at `git status` and `git diff`. `available` answers `G6_E_AGENT_CLAUDE_FOUND` with `where` and
`version`, or `G6_E_STOPPED` while G6 is stopped.

## Alone and composed

Alone, from Python: `get_registry().invoke("agent_claude", "run", {"goal": ..., "workspace": ..., "dry_run": False})`,
or `run_agent(ClaudeCodeRuntime(), ClaudeCodeRuntime.task(goal, workspace, writes=True), dry_run=False)`. Over MCP:
`g6_invoke("agent_claude", "run", params_json)`, only when the person who runs the server lists `agent_claude` (or
`agent_claude.run`) in `G6_MCP_SENSITIVE`; otherwise it is refused with `G6_E_SENSITIVE_COMPONENT_DENIED`.

Composed:

- `agent_runtime`: the runtime this adapter runs on; `check_task` and `decide` show its gate's decision before you run.
- `g6.harness`: `run(standard, executor=as_executor(ClaudeCodeRuntime(), task, dry_run=False))` judges the output
  against checks you froze first.
- `g6.workforce.submit_patch`: checks a patch Claude Code made.
- `coding_agent_gate`: checks a risky plan before you hand it over.

## Failure modes it catches

- `unguarded_tool_use` (an agent using tools with no guard on what they do): every run passes Claude Code's closed
  list of tools (`--tools`), exactly those it grants, and denies the rest G6 knows (its shells, monitor, web tools,
  sub-agents and every MCP tool), so its edit tools run only when the person granted edits; its default permission
  mode is stated; hooks are off for the run; no MCP server is started; and the run itself is decided by the runtime's
  gate before anything starts.
- `overprivileged_agent` (a coding agent run with more access than it needs): read tools only unless edits are
  granted, never a flag that skips its permission checks or adds a folder; only in the folder the person named, never
  G6's own state folder or installed code, a drive's root, or the home folder or a folder that holds it; with only
  the environment a program needs to start and find its sign-in, so no key of G6's or of the environment reaches it.
  It narrows what Claude Code may change and run, not what it may read: see the blind spots.

## Blind spots

- Claude Code's read tools read every file your account can read, your `~/.g6/.env` (G6's key file) and your `~/.ssh`
  included, and it sends what it reads to its model under your account (`leaked_secret`). The action gate holds a
  folder whose name looks secret; it does not stop Claude Code reading outside the folder.
- A file in the folder (a README, a test, a comment, a `CLAUDE.md`) can carry instructions it follows as if you had
  given them (`prompt_injection`); with edits granted, it acts on them inside the folder.
- Its tools are kept to the posture by Claude Code's own permission system (its deny rules win over allow rules), not
  by G6's gate; G6 states the posture and trusts it. A tool a later Claude Code adds is not on G6's lists.
- Hooks are off for the run (`disableAllHooks`), except hooks an administrator sets in managed settings, which still
  run with your rights; the card says so. Your other settings still apply; the repository's are left out.
- The flags G6 passes are taken from Claude Code's documentation and are untried against a real Claude Code: whether
  each is accepted, whether the prompt is read from stdin, and what a tool outside both lists does in a run with no
  one to answer a permission prompt.
- G6 sees none of Claude Code's tool calls, only what it reports, and not what it changed: compare the folder, or send
  the patch to `g6.workforce.submit_patch`.
- The action gate judges a run as reading (or writing) the folder; the online side, sending the folder and the task to
  Anthropic, is not evaluated by `action_gating`, and the decision says so.
- Its sign-in, model and settings come from its own configuration in your home folder; the tokens and cost recorded
  are what it reports. The circuit breaker counts failures in this process only.

## What it needs

Claude Code, an outside program you install and sign in to yourself (proprietary, under Anthropic's terms, never
bundled; paid and online: it runs on your own Claude subscription): `npm install -g @anthropic-ai/claude-code`, then
`claude` once to sign in. G6 finds it through `G6_CLAUDE` or as `claude` on PATH, and never installs it, signs in for
you or passes it a key. A dry run needs none of this. An edit run needs the folder under version control (`git init`,
then a first commit). From the library it uses `action_gating` and `safety_engine`, through the family's runtime. No
pip extra.

## Examples

```g6-example op=run
{"goal": "Explain why the test in tests/test_parse.py fails", "workspace": "/path/to/your/project", "dry_run": true}
```

Put the full path of your project folder in `workspace`. As written, the placeholder folder does not exist, so the
reply is `blocked-escalated` with `G6_E_AGENT_WORKSPACE`. With a real folder it is a dry run: `qualified-draft` with
`G6_E_AGENT_DRY_RUN`, and `detail.plan.argv` is the claude program, then `-p`. Nothing starts.

```g6-example op=available
{}
```

The reply names the Claude Code a run would start, or says how to install it.

```python
from g6.agents.agent_claude import ClaudeCodeRuntime

runtime = ClaudeCodeRuntime(model="claude-opus-5-5", max_turns=10)
argv = runtime.argv("claude", writes=False, mcp_config="none.json", settings="hooks_off.json")
assert argv[:2] == ["claude", "-p"] and argv[argv.index("--permission-mode") + 1] == "default"
denied = argv[argv.index("--disallowedTools") + 1:argv.index("--max-turns")]
assert {"Bash", "PowerShell", "WebFetch", "Agent", "Edit", "Write"} <= set(denied)
assert argv[argv.index("--tools") + 1] == "Read,Glob,Grep"   # the closed list: no other built-in tool is there
assert not [a for a in argv if "dangerously" in a or "bypass" in a]

task = ClaudeCodeRuntime.task("Explain the failing test", "/path/to/project")
assert task.permissions.write_files is False and task.tools[0].online  # read-only, and an outward action
```

## The four open problems

- **Learning signals**: each outcome records the tools allowed and denied, the gate's decision, the turns, the tools
  Claude Code's own permission system refused and its claim, apart from one another. Not yet: nothing links a run's
  posture to whether its patch later passed the checks of `g6.workforce`.
- **Evaluators the model cannot influence**: the tool lists, the command line and the environment are fixed code from
  what the person declared, and nothing Claude Code says changes the label. Judging its output is left to checks
  frozen before the run.
- **Transfer by failure mode**: the posture is bound to `unguarded_tool_use` and `overprivileged_agent` through the
  runtime's permissions, which every command-line agent shares; `agent_codex` applies the same rules.
- **Enough held-out cases**: Not yet: its tests drive a stand-in program, never the real Claude Code; how often it
  keeps the stated posture is not measured.