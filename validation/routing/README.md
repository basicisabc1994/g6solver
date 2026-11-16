# Routing trials

Does a fresh agent with the `using-g6` skill make the right G6 calls, and stay quiet when it should? This folder is
the instrument for the "Routing" row of the 0.3 design's section 5 (design 14.1).

**State: `evidence_pending`.** No session has been run. Nothing here is a measured result.

## What is here

- `tasks.json`: the development cohort, frozen. One positive, one negative and one missing-tool task for every route
  of `src/g6/skills/using-g6/routing.json`. Each route's entry under `routes` is the digest of that route: its anchor,
  its members, its own section of the root skill, its detail files, and what the servers show for each of its tools
  (description and input schema). A route that changes unbinds its own tasks and no other's. One more entry,
  `every-route`, is the digest of everything a session reads that no route owns: the rest of the root skill (its
  front matter, the rule for an absent tool, "Read and ask"), every other file of the skill folder a workspace gets,
  each server's instructions, the tools no route holds, and the fixtures. When it changes every task is unbound.
  Nothing runs until the tasks are reviewed and the new digest is bound.
- `final_tasks.json`: absent. The final cohort is written by someone other than the builders of the routes, after the
  last route of the release has merged: two positive and two negative tasks per route and one missing-tool task per
  route. It is bound by putting its digest in `gates.json`, and run once per client.
- `final_runs.jsonl`: absent until a final cohort is run. `run.py` adds a line, beside `gates.json`, before the first
  session of a client's final cohort: the client, the repeat, and the digests of the task list, the bars and the
  `g6` package. The first line freezes the final list: from then on no other list is run or scored as the final
  cohort, whatever `gates.json` says. A second run of the same client and repeat is refused in any folder. A run in
  which no session was started (every task refused for want of a grant, or the client absent) is marked void and
  may be made again. Commit this file with the final cohort's folders.
- `gates.json`: the bars. `gates.json` is frozen at digest `9ece1f1270b5c92be487a94db62aff4f8a7a01958b343a856d6080547620f043`.
- `frozen.json`: the pinned digests of the bars and of the development task list. Nothing runs and nothing is
  scored on a gate file whose bars differ from the pin, or on a development task that is not the pinned list's own.
- `score.py`: the scorer. Offline and deterministic.
- `run.py`: one fresh session per task; `server.py`: the MCP server a session gets, with a task's withheld tools
  removed; `fixtures/`: four small synthetic repositories. `../freeze.py`: the helper every frozen measurement file
  in `validation/` uses.

## The bars

Set by research into what current agents do on short tool tasks with guidance, and adopted before any run:

| Gate | Bar |
|---|---|
| Useful completion on positive tasks | at least 0.70, and the lower end of its 95% Wilson interval at least 0.50 |
| Negative tasks with an unnecessary call | at most 0.15 |
| Tasks with a malformed call | at most 0.10 |
| Recovery on missing-tool tasks | at least 0.80 |

The task is the unit. A task that was refused, could not be run or was interrupted stays in every denominator and
counts against every gate it belongs to, so calling something on every task, calling nothing, and refusing everything
each fail. Each client is scored apart and results are never pooled. The release needs at least one client to meet
every bar on the final cohort; a client that misses is published as "routing bar not met", one that was not run as
"not run", and each is a known limit. After the gate run, the final cohort's positive tasks are run twice more and
the share that passes all three is published with no bar.

A pass is an upper bound for real work: the repositories are synthetic and the tasks are short.

## What a score is read from

A score is read from each session's kept event log and from frozen files, and from nothing a row says about itself.

- Each session's event log is kept in the run's folder as `events/<sha256>.log`: the bytes the client printed, under
  their digest. `score` and `release` read every session again from that log. What a row says of its own state,
  calls, final text, cost and questions is dropped. A row that says `completed` and has no such log, or whose log
  does not hash to the digest the row names, cannot pass: the reading is `unevidenced`.
- A session is `completed` only when the client exited with code 0 and its log holds the client's one success event:
  for Claude Code a `result` event with subtype `success` and `is_error` false (a turn cap or an error during
  execution is not one); for Codex `turn.completed`, with no `turn.failed` and no `error` event (an agent message
  is not one). Anything else, an event the runner does not know included, is `interrupted` and counts against
  every gate. One function, `reading`, holds this, and `run`, `score` and `release` all call it.
- Each row carries its session's record: the run it belongs to, and the digests of the task, the task list, the bars
  and the `g6` package the session ran on. A record of another task, list, bars or package is refused, so a list or
  a tree changed after a run cannot score that run.
- Every session that was started has a row, whatever its events hold and whatever went wrong afterwards.
- A row says what produced it: the client program's file name, what its version check answered and the digest of the
  program file (no folder of the person's machine), or the stand-in that took its place. A stand-in's rows can fail
  a gate and never pass one (`simulated`); rows that name no client program cannot pass either (`unattributed`).
- A log holds one row per task and repeat; a second run into the same folder is refused, and a log with two is not
  scored. The report names the digests of the task list and the gate file it was scored with, and its `evidence`
  block counts the rows read from a kept log.
- `python validation/routing/run.py release --out <folder> --out <folder>` makes each client's final report again
  from its folder's kept logs and reads the release from those; a report file is never read. `score.py`'s own
  `release` takes `pass` from no report that lacks the evidence block, a client named for every row, or the pinned
  bars.

What this does not hold: nothing signs a kept log. A person who writes a log, its digest and its row by hand has
written evidence, and the scorer does not see it. The exit code and the time a session took are recorded by the
runner and cannot be read again from the log.

## What is sent, and where

Each session sends the task's prompt, the fixture repository the agent reads and G6's guidance to the client's
provider: `anthropic:claude_code` for Claude Code, `openai:codex` for Codex. The fixtures hold no user data. Every
session passes G6's one consent check first, with the fixture declared as a source. Without the person's grant the
session is not started and its transcript is `refused`.

List what to grant, then grant each line in the client's own dialog (`g6_cloud_grant`):

    python validation/routing/run.py sources --client codex --out <folder>

Keep `<folder>` the same for the grant and the run: the workspace folders are part of what is granted.

Each session's servers use a G6 home of their own inside `<folder>` (`g6_homes/<task>.<repeat>`), made empty for
that session and removed when it ends, so no session starts with what another wrote. It holds a copy of the
person's sharing decisions and nothing else of theirs; the copy is read through G6's own reader (chain and
checkpoint checked) before the session starts. A task states the host permissions its server starts with (the review
task allows `patch_review.review`, so G6 runs the synthetic repository's tests, confined); every other task's server
starts with none.

Which G6 serves: the runner hands each trial server the digest of the `g6` package the tasks are bound to. The server
is started by the client and imports whatever `g6` its interpreter finds; when that is another package it serves
nothing, and the session's record says which package each server found. Run the trials with an interpreter whose
installed `g6` is the tree under test.

What the agent can touch: the Codex arm runs in Codex's `workspace-write` sandbox. The Claude Code arm is allowed
`Read`, `Glob`, `Grep`, `Edit`, `Write` and `Skill`, and is refused the shell, the web and sub-agents. Nothing in
this runner confines those file tools to the workspace: no path rule is passed and no sandbox is set, so an agent
could read or change any file the person's account can. That is what the `agent_environment` line of `sources`
asks the person to grant. Run the Claude Code arm in an account or a machine that holds nothing else.

G6's stop holds here as it does for G6's own Claude Code and Codex adapters: with `G6_STOP` set, a `STOP` file in
the G6 home, or `G6_DISABLE_LLM` set, no session is started, and a stop engaged during a run ends the session under
way and starts no other.

## How to run

    python validation/routing/run.py run --client codex --cohort development --out <folder>
    python validation/routing/run.py score --client codex --cohort development --out <folder>

Sessions run one at a time, with nothing else running beside them on the machine. The skill is installed in each
workspace where the client reads skills: `.claude/skills/using-g6` for Claude Code and `.codex/skills/using-g6` for
Codex, the folders `g6 skills install --client <client>` writes. No `AGENTS.md` and no Codex plugin ships, so that
folder is the only guidance the Codex arm gets.

The development cohort may be run again, in a folder of its own, after a revision of the skill; each revision
re-binds the digests it changes. The final cohort's rows and kept logs are read by `run.py score` and
`run.py release` and by nothing else.

## Before the first run

`run.py`'s command line for each client and its two event parsers are written from each client's documented
interface. No session was started when they were built, so their fit to a real session has not been observed. First
run one development task per client and check, against the raw event log, that every MCP call the agent made is in
the transcript, with its arguments, and that a refused call is marked as not valid. A call's own time is not in
either client's events, so a call counts as timely only when its whole session met the task's time budget.

## What is owed

1. One observed session per client (above), and any repair of `run.py` it calls for.
2. The development cohort on each client (30 sessions each at ten routes), and skill revisions from it.
3. The final cohort's tasks, written and frozen as above once the last route has merged.
4. The final cohort on each client, once, then the two consistency repeats of its positive tasks.

## Moment-of-need cohort

Does G6's value reach a coding agent at the moment it needs it, when nobody asks for G6? The cohorts above measure
prompted routing: most of their prompts say "Use G6". In this one no prompt names G6, its server or a tool, or asks for
a review or a check (`score.NAMES_A_TOOL` refuses such a prompt). Each prompt reads like a person's request to a coding
agent on a synthetic repository. It is run twice per client: the before arm on the tree as it is, and the after arm
once the wiring has landed. The tasks were written by someone who builds none of the wiring.

**State: `evidence_pending`.** No session has been run. Nothing here is a measured result.

- `moment_tasks.json`: 24 tasks, run by each client. 12 are positive: 4 `before_done`, 4 `stuck` and 4
  `after_refusal`. 8 are `negative` and 4 are `missing_tool`. Each task gives its prompt, its fixture, the calls that
  count (any one of them, with the argument constraints that make a call well-formed), its moment rule, the host
  permissions its server starts with, any withheld tools, the grants it starts with, and why it is fair. The list
  binds no route, because the guidance changes between the arms. Each session records what guidance it had instead.
- `moment_gates.json`: the bars. `moment_frozen.json` pins both files: the task list at
  `1976b25f8d885fcb60086adbacd48d7f9bea0c85db79e88a159ab5f235933a75` and the bars at
  `9aa0237097e7dd22f013664850d3008513ac750834f577d793cab6f28556de59`. Nothing runs and nothing is scored on a list or
  bars that differ from these. A list may hold fewer tasks, so one task can be tried first. It may never hold a
  changed one. It also pins the outcome arm's bar and held-out checks (see "Outcome arm").
- `moment_fixtures/ledger`: one more synthetic repository, kept out of `fixtures/` so that the development cohort's
  bound digest of the fixtures does not move. Its four failing test files are the stuck tasks. Each holds a
  constraint that the two natural first fixes miss. `tests/validation/test_routing_moment_fixture.py` shows that both
  natural fixes fail and that a right fix passes.

### What a hit is

Each kind is read from the order of the session's steps: its MCP calls (with the `G6_E_*` code a reply carried), the
files it changed, the test runs (and whether they failed) and its final message. That order is read from the kept event
log only, as `reading` reads every other field.

| Kind | Moment | Hit |
|---|---|---|
| `before_done` (`call_before_final`) | the last edit to a `.py` file | a well-formed `g6_review_patch` or `g6_review_until_clean` call on the repository after it, before the final message |
| `stuck` (`stuck_call`) | the second failed test run | after it, before the final message, a well-formed call that addresses the failure: `g6_select` (phase `stuck` or `diagnose`), `g6_premortem`, `g6_review_patch` with `proposed_checks`, `g6_review_until_clean`, or `g6_invoke` on `swe_diagnostics` |
| `after_refusal` (`call_after_refusal`) | the first review call refused with the task's code | the next call is the documented remedy (`g6_cloud_grant` for a withheld source), or the final message names what the person must allow (`patch_review.review`, or the withheld source) and the refused call is not retried |
| `negative` | none | the session completes with no G6 call at all |
| `missing_tool` | none | as in the cohorts above: the final message says a withheld tool is absent, or the agent falls back to `g6_find` |

Two of the refusals are G6's own sensitive-operation refusal: the server allows no review. The other two are its
consent refusal: the server allows the review, but the session starts without `host_configuration`, `jobs` or
`completion_history` shared. `g6_select` is planned, not yet registered (`planned_tools`). In the before arm no
call can name it.

A positive task whose moment never came still counts as a miss: no Python change, fewer than two failed test runs, or
no refusal. So a strong agent that was never stuck counts against the bar. The report gives `moment_reached` beside
`moment_hits`, so that case can be seen. A hit is the call, not what the call returned.

### The bars

Pre-registered before any run. **The owner may tighten these bars before the first run.** To do so, retire
`moment_gates.json` with its reason (`freeze.retire`), freeze the new bars and pin them in `moment_frozen.json`. A bar
is never loosened after a run.

| Bar | Value |
|---|---|
| Positive moment hits | at least 0.60, and the lower end of its 95% Wilson interval at least 0.35 |
| Negative tasks with an unnecessary G6 call | at most 0.125 |
| Tasks with a malformed call | at most 0.10 |
| Recovery on missing-tool tasks | at least 0.75 |

The release rule is that at least one client meets every bar. Each client is scored on its own and results are never
pooled. The task is the unit, and every denominator is full. The before and after arms are reported per paired task:
each kind and polarity gets its four cells and an exact McNemar p-value. The report makes no claim, because the cohort
was not sized to decide a difference.

### What each session starts with

What ships, with as little else as the runner can manage:

- **Claude Code arm.** The session loads the plugin this tree ships with `--plugin-dir` (its hooks and skills). The
  plugin's `.mcp.json` is left out, because the trial server stands in its place. The `using-g6` skill sits where
  `g6 setup --client claude` puts it, at the workspace's scope. The session may run the tests (`python -m pytest`,
  `pytest`) and nothing else of the shell, the web or sub-agents.
- **Codex arm.** The skill sits where `g6 setup --client codex` puts it, at the workspace's scope, in Codex's
  `workspace-write` sandbox. Codex also reads what setup writes in the person's home (`~/.codex/AGENTS.md`, once the
  wiring adds a block there). Run `g6 setup --client codex` and `g6 setup --client claude` from the tree under test
  before each arm. `codex exec` asks nobody, so with no setting every MCP call fails ("MCP tool call requires
  approval, but approval policy is never"). The one-task pilot of 2026-11-16, before any arm ran, showed this. Each
  trial server's tools are therefore approved (`mcp_servers.<name>.default_tools_approval_mode="approve"`), as for a
  person who always allows G6's tools. Nothing else is approved, and the sandbox stays `workspace-write`.
- **Guidance record.** Every session's record names, as digests, the guidance it could read: the workspace's skill,
  the plugin, what the servers show, and what `g6 setup` writes in the person's home for each client. An absent file
  is "". The labels name no folder. The arms are compared on these. `run.py compare` prints both arms' `g6` package
  and guidance.
- **Server and G6 home.** Each server states its task's permissions, even when there are none, so a registration of
  the person's own cannot add any. The client is given the session's G6 home, so the plugin's hooks use it. That home
  holds no key file, so the completion loop stays off.
- **Fresh start.** A session starts only while none of the list's `ungranted` sources is shared with the client's
  provider. The component catalogue is one of them: it is not pre-seeded. Otherwise the session is `refused` with
  `G6_E_ROUTING_NOT_FRESH` and nothing starts.

### What is sent, and where

Each session sends the same things as the cohorts above: the task's prompt, the fixture repository and G6's guidance,
to the client's provider. The fixtures hold no user data. List what to grant, then grant each line in the client's own
dialog (`g6_cloud_grant`). Each line is the client's environment, one fixture, or one workspace folder:

    python validation/routing/run.py sources --client codex --cohort moment --out <folder>

**Claude Code arm caveat.** Its file tools are not confined to the workspace, and the tests it runs, which the agent
may edit, run with the person's own rights. Run it in an account or on a machine that holds nothing else.

### How to run both arms

    python validation/routing/run.py run --client codex --cohort moment --out <before folder>
    python validation/routing/run.py score --client codex --cohort moment --out <before folder>
    (the wiring lands; g6 setup again from the new tree)
    python validation/routing/run.py run --client codex --cohort moment --out <after folder>
    python validation/routing/run.py compare --client codex --out <before folder> --out <after folder>
    python validation/routing/run.py release --cohort moment --out <after folder>

A moment record is held to the pinned list and bars. It is not held to the tree that reads it, because its two arms ran
on two trees. It must name the `g6` package it ran on, and its servers must have found that package. Malformed calls
are read against the schemas of the tree that scores. Score each arm on its own tree, and compare once both arms exist.

### Outcome arm

The moment hits measure reach: was G6 called at the moment. This arm measures what the work came to, on the same
sessions and with no extra session: did the agent's final work come out right more often with the wiring than without?
The owner's ruling of 2026-11-16 is that 0.3.0 is beta when it is "functional and provides some kind of benefit". This
arm is the measure of the second part. It was written and frozen before any arm ran. Only one-task pilots had run, and
their rows are not scored.

- **The final work.** Each moment session's record names `final_diff_sha256`: the workspace as the session left it,
  against the fixture commit, as a binary git diff. It holds every change, deletion and new file, the agent's own
  commits included. It leaves out what the interpreter and pytest write, and the skill the runner installed. The diff is
  kept as `events/<sha256>.diff`, beside the event logs, before the next session on that fixture wipes the workspace.
  An empty diff is a session that changed nothing. Nothing else in a row's meaning changes.
- **Held-out checks.** `moment_outcome/<task id>/test_outcome.py` holds, for each positive task, pytest checks that no
  session sees. They are not in any fixture or prompt, and a session's workspace is a copy of its fixture only. They
  cover the task's stated behaviour, the hidden constraint each stuck fixture holds, and for the `before_done` and
  `after_refusal` tasks at least one edge the prompt implies. `tests/validation/test_routing_moment_outcome_checks.py`
  shows that each task's checks fail on the fixture as shipped and pass on a right fix. It also shows that each
  plausible wrong fix written there fails, on the check it was written to trip. Those fixes live only in that test.
- **Harm.** For each negative and missing-tool task, `moment_outcome/<task id>/protected.json` names the fixture files
  the session was not asked to change. A session whose final diff changes or deletes one of them is counted as harm, so
  an unnecessary G6 detour that damages work shows. A new file is not harm. The two `notes` tasks protect nothing,
  because their only file is the one they ask to change.
- **The outcome.** `score.outcome` applies the kept diff to a fresh copy of the fixture with git and runs the task's
  checks against it with pytest. The checks sit outside the copy, with their own configuration, and no `conftest.py`
  is read. The run gets no setting of the person's, and it has 120 seconds. A positive task passes only when its session
  completed, changed something, the diff applied and every check passed. No diff, an empty diff, a diff that does not
  apply, a session that did not complete or whose record does not bind, and a run out of time all count as fail. The
  checks run inside an operating-system boundary (`g6.core.confine`) where a backend can confine them and a probe check
  passes there. Otherwise they run in a plain subprocess with the timeout. Each row's `ran` says which, and why.
- **The bar** (`moment_outcome_gates.json`): for each client on its own, the after arm's net on the positive tasks is
  at least +1, where the net is wins (passed after only) minus losses (passed before only). The after arm's harm must
  not be above the before arm's. `compare` prints, per client, the paired table (wins, losses, ties), the net, the
  exact McNemar p-value of the discordant pairs, the harm of each arm, and each pair's moment hit beside its outcome.
  It makes no claim (`claim` is `null`). Twelve positive pairs cannot decide a difference: the bar reads a direction.
- **Pins.** `moment_frozen.json` pins the outcome bar at
  `bc825f3fb4a20f76452f66beba3929024522501479b4ca8f1a98d246a3ee2684` and the held-out checks (the whole
  `moment_outcome/` folder) at `6787d535ad8440824a72c81a25c75f8538e1f9141c5ff9c7f7d82ea2fcba05be`. A changed check or
  bar scores nothing. To change either before the first arm, retire the old file with its reason (`freeze.retire`),
  freeze the new one and pin it.

How to run: nothing extra. `run` keeps each diff; `score` writes the outcome block in each arm's report; `compare`
prints the paired outcome beside the moment hits:

    python validation/routing/run.py score --client codex --cohort moment --out <before folder>
    python validation/routing/run.py compare --client codex --out <before folder> --out <after folder>

Limits of the outcome arm:

- The checks are held out from the sessions, not hidden from a determined reader. A session's MCP configuration names
  this tree's `server.py`, so an agent that wandered out of its workspace could find this folder.
- Where no backend confines them, the checks run code a session wrote with the scorer's own rights. Score where nothing
  else is held, as for the Claude Code arm.
- Inside a boundary the child's environment is G6's minimal one, so string hashing is not seeded. Only a fix that
  breaks a tie by a set's order could then read differently from run to run.
- Harm is read per file, so a damaging edit inside a file the task asked to change is not harm. The checks catch it in a
  positive task. In a negative task it is not caught.
- In this setup a review's reply is withheld by consent (see "Not yet observed"), so an agent cannot use what a review
  found. The arm measures the wiring as it stands.

### Not yet observed

The steps a session's log holds were read from each client's documented interface:

- Claude Code: `Edit`, `Write` and `Bash` tool uses and their results, and the `result` event.
- Codex: `file_change`, `command_execution` and `agent_message` items.

The same holds for `--plugin-dir`. Before the first arm, run one moment task per client. Check the session's
`timeline` against the raw log: every edit, test run and call it holds should be there, in order, and a failed test
run should be marked failed.

Further limits:

- In this setup the review's reply is withheld by consent (see above), so the agent never sees a review's result.
- The READMEs of the four development fixtures, which every moment task outside `ledger` uses, name "G6's routing
  trials", a mild cue. They stay as they are so that the development cohort stays bound.
- A trap may not stop a strong agent.
