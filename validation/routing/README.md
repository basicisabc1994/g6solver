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
Codex, the folders `g6 skills install --client <client>` writes. G6's server, built as G6 builds it, also sends its
instructions (`g6.mcp.instructions`) to each client when it connects. `g6 setup --client codex` offers the person a
block for Codex's own `AGENTS.md`, written only on their yes (the file's mode is kept; Windows attributes and ACLs
are not); this runner writes no `AGENTS.md`, and no Codex plugin ships, so the Codex arm's guidance is that folder and
the server's instructions.

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
once the wiring has landed. The lead amended the tasks from the research audit before any arm (2026-11-17).

**State: `evidence_pending`.** No session has been run. Nothing here is a measured result.

- `moment_tasks.json`: 24 tasks, run by each client. 12 are positive: 4 `before_done`, 4 `stuck` and 4
  `after_refusal`. 8 are `negative` and 4 are `missing_tool`. Each task gives its prompt, its fixture, the calls that
  count (any one of them, with the argument constraints that make a call well-formed), its moment rule, the host
  permissions its server starts with, any withheld tools, the grants it starts with, and why it is fair. The list
  binds no route, because the guidance changes between the arms. Each session records what guidance it had instead.
- `moment_gates.json`: the bars. `moment_frozen.json` pins both files: the task list at
  `e2168da8cc01efa41c19f736d54c91b92fc9fbaac068e500099ed27842051973` and the bars at
  `9aa0237097e7dd22f013664850d3008513ac750834f577d793cab6f28556de59`. Nothing runs and nothing is scored on a list or
  bars that differ from these. A list may hold fewer tasks, so one task can be tried first. It may never hold a
  changed one. It also pins the outcome arm's bar and held-out checks (see "Outcome arm"), the withheld file, and
  the fixtures: every file of every fixture a moment task names (the cohort-specific copies, invoices, fees, stock, ledger_amounts and ledger_report under `fixtures/`), at `4ba390a5443a5beb6446dfe1b0652b5d54e71d8e2c48990d2d4f7deec05307ff`. A run refuses
  before anything starts, and the outcome arm scores nothing, when one byte of them differs.
- `moment_withheld.json`: the sources the person grants for every session beyond the fixtures
  (`host_configuration`, `jobs` and `completion_history`, which a review's reply carries), and, per task, those its
  session starts without. Only the two consent-refusal tasks (`moment.after_refusal.3` and `.4`) withhold them. It is a
  file of its own so the frozen task list did not change, and it is pinned at
  `9c031f4cfab260cd8101a1ff9efcc975f7c831b1eeae375c5da5661dfdb8e4a2`. The lead set it before any arm ran, so that a
  review can return a result where the task is not about consent.
- Moment fixtures: separate copies of shop, classify, notes and rules, plus invoices, fees, stock,
  ledger_amounts and ledger_report. Each stuck fixture holds only its own visible test file. Their files name
  no G6, routing, trials or measurement. The development inventory and its original fixture bytes stay bound.

### What a hit is

Each kind is read from the order of the session's steps: its MCP calls (with the `G6_E_*` code a reply carried), the
files it changed, the test runs (and whether they failed) and its final message. That order is read from the kept event
log only, as `reading` reads every other field.

| Kind | Moment | Hit |
|---|---|---|
| `before_done` (`call_before_final`) | the last edit to a `.py` file | a well-formed `g6_review_patch` or `g6_review_until_clean` call on the repository after it, before the final message |
| `stuck` (`stuck_call`) | the second failed test run after the first `.py` edit | after it, before the final message, a well-formed call that addresses the failure: `g6_select` (phase `stuck` or `diagnose`), `g6_premortem`, `g6_review_patch` with `proposed_checks`, `g6_review_until_clean`, or `g6_invoke` on `swe_diagnostics` |
| `after_refusal` (`call_after_refusal`) | the first review call refused with the task's code | a remedy precedes any repeat; `g6_select` naming the refusal code may come first; a repeat after a successful remedy is allowed, or the final message names what the person must allow (`patch_review.review`, or the withheld source) and the refused call is not retried |
| `negative` | none | the session completes with no G6 call at all |
| `missing_tool` | none | as in the cohorts above: the final message says a withheld tool is absent, or the agent falls back to `g6_find` |

Two of the refusals are G6's own sensitive-operation refusal: the server allows no review. The other two are its
consent refusal: the server allows the review, but the session starts without `host_configuration`, `jobs` or
`completion_history` shared. Only these two sessions start without them (`moment_withheld.json`); every other session
starts with them, so its review can answer. `g6_select` is registered in the merged wiring. Both arms record the guidance and package they actually use.

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
  `g6 setup --client claude` puts it, at the workspace's scope. The session may run the tests (`python -m pytest`, `python3 -m pytest`, `py -m pytest`,
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
- **Fresh start.** A session starts only while none of the list's `ungranted` sources that the person is not asked
  to grant is shared with the client's provider. The component catalogue and its data are such sources: they are not
  pre-seeded. Otherwise the session is `refused` with `G6_E_ROUTING_NOT_FRESH` and nothing starts.
- **Granted sources.** A session starts only while the person shares with the client's provider every source
  `moment_withheld.json` grants that its task does not withhold. Otherwise it is `refused` with
  `G6_E_ROUTING_NOT_GRANTED` and nothing starts.
- **Withheld sources.** For a task that withholds sources, the session's G6 home gets the person's sharing decisions
  less every grant and revoke of those sources. This overlay can only remove sharing. The copy holds the person's own
  rows, each with its own body and in their order, chained again in the session's home. No row is ever added, and
  the person's own decisions are only read. The session truly lacks those sources: its servers and the plugin's hooks
  read the copy through G6's own reader, and the runner reads the copy back before anything starts. A grant the
  person makes in the client's dialog during the session is written to the copy as usual. The session's record names
  the sources its task withholds (`withheld_sources`). A row whose record names other ones is read as no evidence.
- **Asked of the session's own home.** Before each session starts, the runner asks the session's own G6 home, not
  the person's. First, through G6's own reader, the copy must share every source the task starts with: the client's
  environment, the fixture, the workspace folder, the folder the home keeps saved reviews in, and each granted source
  the task does not withhold. It must share none of the sources the task withholds. Then, through G6's own server,
  built as the session's is in the runner's process with model calls off, one review is taken on the workspace and
  its job polled (`server.probe`). The reply must come back, or, for a task that withholds sources, be withheld on
  them. Otherwise the session is `refused` with `G6_E_ROUTING_NOT_GRANTED` (the copy lacks a source),
  `G6_E_ROUTING_NOT_FRESH` (a withheld source is shared) or `G6_E_ROUTING_REPLY_WITHHELD` (a review's reply would not
  come back), and nothing starts. The home and the workspace are then made again, so the probe leaves the session
  nothing. The record keeps the probe's answer (`reply_probe`).

### What is sent, and where

Each session sends the same things as the cohorts above: the task's prompt, the fixture repository and G6's guidance,
to the client's provider. The fixtures hold no user data. List what to grant, then grant each line in the client's own
dialog (`g6_cloud_grant`). Each line is the client's environment, one fixture, one workspace folder, one session's
reviews folder, or one of the three sources a review's reply carries:

    python validation/routing/run.py sources --client codex --cohort moment --out <folder>

For each client, 38 lines: `agent_environment`, the five fixtures (`routing-fixture.classify`, `.ledger`, `.notes`,
`.rules`, `.shop`), the five workspace folders (`repository:<digest>`, one per fixture, under `<folder>/work`), one
reviews folder per task (`repository:<digest>` of `<folder>/g6_homes/<task id>.0/reviews`: a finished review's reply
names the folder in which the session's own G6 home saved it), and these six, three per client:

    completion_history   anthropic:claude_code
    host_configuration   anthropic:claude_code
    jobs                 anthropic:claude_code
    completion_history   openai:codex
    host_configuration   openai:codex
    jobs                 openai:codex

Those three, and the reviews folders, hold the session's own trial state, not the person's data: each session's G6 home is the runner's, and it
holds the trial's own records and a copy of the person's sharing decisions. The component catalogue and its data are
never on the list.

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
- **The bar** (`moment_outcome_gates.json`): at least one client has an after-arm net of at least +1 on the positive
  tasks, where the net is wins (passed after only) minus losses (passed before only), and on no client is the after
  arm's harm above the before arm's. This matches the moment bars (at least one client meets every bar) and the
  owner's "some kind of benefit". Both clients are read: a client whose arms were not compared leaves its harm
  unread, and the bar is then not met. `compare` prints, per client, the paired table (wins, losses, ties), the net,
  the exact McNemar p-value of the discordant pairs, the harm of each arm, each pair's moment hit beside its outcome,
  and that client's two parts of the bar. `outcome` reads the bar across both clients. Neither makes a claim (`claim`
  is `null`). Twelve positive pairs cannot decide a difference: the bar reads a direction.
- **The bar before it.** The first frozen bar asked the net of each client on its own. The lead retired it before any
  arm ran, to match the owner's ruling and the moment bars. It is kept, with that reason, as
  `moment_outcome_gates.retired-bc825f3fb4a2.json`.
- **Pins.** `moment_frozen.json` pins the outcome bar at
  `bbdb333860ee44545b5b337213319e0780572f1fbd3fa24b8bc6abfe837dd1f8` and the held-out checks (the whole
  `moment_outcome/` folder) at `55fa5cd21cb6e932eba67df80c9b5bd5ffb64b0615814a93a0674abb64409a8e`. A changed check or
  bar scores nothing. To change either before the first arm, retire the old file with its reason (`freeze.retire`),
  freeze the new one and pin it.

How to run: nothing extra. `run` keeps each diff; `score` writes the outcome block in each arm's report; `compare`
prints one client's paired outcome beside the moment hits; `outcome` takes each client's before and after folders, in
pairs, and reads the bar across both clients:

    python validation/routing/run.py score --client codex --cohort moment --out <before folder>
    python validation/routing/run.py compare --client codex --out <before folder> --out <after folder>
    python validation/routing/run.py outcome --out <before folder> --out <after folder>

Limits of the outcome arm:

- The checks are held out from the sessions, not hidden from a determined reader. A session's MCP configuration names
  this tree's `server.py`, so an agent that wandered out of its workspace could find this folder.
- Where no backend confines them, the checks run code a session wrote with the scorer's own rights. Score where nothing
  else is held, as for the Claude Code arm.
- Inside a boundary the child's environment is G6's minimal one, so string hashing is not seeded. Only a fix that
  breaks a tie by a set's order could then read differently from run to run.
- Harm is read per file, so a damaging edit inside a file the task asked to change is not harm. The checks catch it in a
  positive task. In a negative task it is not caught.
- In the two consent-refusal tasks a review's reply is withheld by consent, by design, so there an agent cannot use
  what a review found. In every other task the person grants what a review's reply carries, and a session starts
  only once a review's reply would come back there (see "Further limits" for the historical pilot and the pending post-merge probe).

### Not yet observed

The steps a session's log holds were read from each client's documented interface:

- Claude Code: `Edit`, `Write` and `Bash` tool uses and their results, and the `result` event.
- Codex: `file_change`, `command_execution` and `agent_message` items.

The same holds for `--plugin-dir`. Before the first arm, run one moment task per client. Check the session's
`timeline` against the raw log: every edit, test run and call it holds should be there, in order, and a failed test
run should be marked failed.

Further limits:

- **Historical pilot, before reply delivery merged.** The one-task pilot of
  2026-11-16 (Claude Code, `moment.before_done.1`, every line granted) called `g6_review_patch` unprompted. Its
  `metaharness_job` poll was withheld. The session's copy shared every named source of the reply. The one source it
  did not share is `input:<sha256 of the review call's own arguments>`. G6 counts a client's own input as shared only
  in the call that carries those exact bytes (`cloud_consent._refuse_unshared`). The poll is another call: the job
  keeps the review call's input as its origin (`metaharness/jobs.py`, `start`), but the poll's reply is admitted with
  the poll's own arguments only. No grant made before the call can name that input. G6's own test of a delivered
  review (`tests/s16r3/test_completed_review_delivery.py`) grants the call's exact input first. The runner's check
  above therefore refused every session that starts with the granted sources (`G6_E_ROUTING_REPLY_WITHHELD`) until
  G6 lets a client's own input come back to it at the poll of its own job, or the person grants each call's input
  in the client's dialog during the session. `tests/validation/test_routing_moment_reply.py` holds this offline.
- Historical cue, removed from the active moment cohort by the dated amendment below: the development fixture
  READMEs name G6. Their original bytes stay bound to development; moment tasks now use their own cue-free copies.
- A trap may not stop a strong agent.


### Amendment, 2026-11-17 (before any arm)

2026-11-17 before any arm: research audit; lead scoring rulings; reply delivery merged. Replace seven tasks, revise four, isolate cue-free fixtures, bind provenance and unchanged-test checks; count only post-edit failures, allow selector before refusal remedy, report attempted beside recovery. All routing and outcome bars remain unchanged.

The active registration is `moment_amendment_2026-11-17.json`; task sources are in `moment_sources.md`. Moment fixtures have their own names under `fixtures/`; development fixtures remain byte for byte intact. Only failed test runs after the first Python edit count for stuck. A selector naming the refusal code may precede a remedy; a retry before remedy misses, and a retry after remedy needs a successful reply. A final escalation must name what the person must allow or share. Missing-tool reports show attempted beside recovered, using every task for the unchanged recovery bar. Absence must be in the same sentence as the withheld tool name. The allowed test spellings include python3 and py.

- `bars_digest`: `9aa0237097e7dd22f013664850d3008513ac750834f577d793cab6f28556de59`
- `tasks_digest`: `e2168da8cc01efa41c19f736d54c91b92fc9fbaac068e500099ed27842051973`
- `outcome_bars_digest`: `bbdb333860ee44545b5b337213319e0780572f1fbd3fa24b8bc6abfe837dd1f8`
- `checks_digest`: `55fa5cd21cb6e932eba67df80c9b5bd5ffb64b0615814a93a0674abb64409a8e`
- `withheld_digest`: `9c031f4cfab260cd8101a1ff9efcc975f7c831b1eeae375c5da5661dfdb8e4a2`
- `fixtures_digest`: `4ba390a5443a5beb6446dfe1b0652b5d54e71d8e2c48990d2d4f7deec05307ff`

The prior reply-withheld pilot describes the pre-merge tree. Reply delivery is now merged. The actual offline pre-start admission regression is `test_routing_research_reply.py`, for both client identities with synthetic homes. Its sandbox run stopped at pytest temporary-directory setup (`PermissionError [WinError 5]`); no successful probe or session-start result is asserted here. The lead must run that regression outside this sandbox before any arm.

### Audit fix pass, 2026-11-17 (before any arm)

The fix pass recorded in `moment_amendment_2026-11-17.json` keeps the scoring rules and every bar.
`moment_outcome/fixture_paths.py` is the file-role table used by checks, outcome path validation and mutation
helpers. The runner takes fixture roots from the active task inventory and binds every file in every active
fixture. It copies the table beside the held-out checks; sessions never receive it. The split ledger fixtures
still have a package named `ledger`; their imports run only inside behavioural checks, after the fixture is in
place. Unchanged-test checks load without importing the package. The invoice check remains as specified; its
obsolete label-counting probe was replaced with printed-only grouping and a shared-formatter counterexample.

`amendments/2026-11-17/select_tasks.unbound.json` holds one positive and one negative task per cohort and client,
plus one development missing-tool task shared by both clients. Each case records the supplied research sources,
its rationale, client schedule and a task in the existing schema. The `select` digest is **null**, intentionally
unbound. These are drafts and cannot run. The lead extracts the task records for each listed cohort/client,
retains provenance here, and binds route digests once on the integration line after the using-g6 split and phase C
merge, before the before arm. No route digest, development task, final-cohort pin, or bar changes on this branch.
The four known development route-binding failures therefore remain open. Final-cohort scheduling remains the
lead's responsibility, including its existing per-route shape and independent authorship requirement.

Grant-based refusal recovery requires a positive `status: granted` reply even without a retry. A job-start handle,
a cancelled grant or a missing reply is insufficient. Telling the person what they must allow/share remains a
separate qualifying path when the refused call is never repeated. This fix pass's new regression runs both clients
with and without a retry; the prior audit's cancelled-without-retry assertion was strengthened to match this ruling.

### User-value and retention amendment (2026-11-17, before any arm)

**State: `evidence_pending`.** `amendments/2026-11-17/audit_measures.json` records the owner's outside-audit
request, the previous moment pins, the added pins and the rulings. No arm was run to author this amendment.
`audit_protocol.json`, `retention_tasks.json` and `audit_frozen.json` were written with `validation/freeze.py`.
The pre-arm import correction retired their initial versions where necessary, with the reason and initial
fixture bytes beside the amendment. Existing moment tasks, fixture/check bytes, pins and every gate remain
unchanged. The beta bar remains outcome net >= +1 with hidden checks and the final diff kept, OR >= 3 dogfood
catches; the existing harm and routing gates still apply. These additional reports introduce no bar.

`score.audit_report`, `score.compare_audit` and `score.retention_report` compute the measures. The read-only
outcome reader `audit.py` checks the added freeze, reads kept artifacts again, and prints the reports. It runs
no session, hidden check, model or network request. Before the before arm, check **both** the existing moment
registration and the additive audit registration:

    python validation/routing/audit.py --check

For the paired user-value comparison, use each existing moment task with the same client, model/version/settings,
fixture bytes, task and budgets in two conditions: `alone` (no G6 server, tools, hooks, skill or history) and
`with_g6` (the same agent plus G6). Record the exact model/version/settings together in `model`. The total budget
includes G6's model calls, waits and retries: 900 wall seconds, 20 total model calls and one question for these
tasks; no extra assistance or uncharged G6 budget. Exceedance and missing budget observations are reported.
Alternate which condition goes first by frozen task index, separately for each client. Do not pool clients.
The original guidance-before/guidance-after arms both have access to G6: they remain the unchanged release
comparison and must not be renamed as an agent-alone control. The independent audit conditions are supplemental.

Every scheduled task remains in each arm's report, including absent recordings. For each task and each arm the
report gives final hidden-check pass, confirmed G6 defect finds, missed defects, false alarms, unadjudicated
claims, unknown defect observations, wall seconds, model cost or calls, abandonment and budget observation.
The paired table carries both complete measure rows, with matching task registration, model and budgets.
Unrun tasks have unknown outcomes; ended sessions without a completed kept final diff fail. An empty **kept**
diff is not abandonment and must actually pass the hidden checks to count as success. An ongoing session
is reported separately. Unknown costs/calls/time remain null, with observed/missing counts beside totals.

Defects are counted by registered class, once per task, rather than by number of repeated G6 warnings. Each
existing positive task's `task_outcome` is its full frozen hidden-check set; negative/missing-tool tasks have no
hidden success check, so that measure is unknown there (their existing protected-file harm measure remains).
An independent adjudicator maps actual G6 defect claims to these IDs, retaining unmatched claims, and runs
the same hidden checks on the **reviewed snapshot before remediation** and on the final kept diff. A reviewed
snapshot check failing confirms a claimed defect; passing makes that claim a false alarm. A confirmed defect
without a G6 claim is missed, even if the agent repairs it independently. In the alone condition, use the same
registered initial defect snapshot for that observation; there are no G6 finds. A final passing repair cannot
by itself establish whether the original warning was false. Unchecked or unmatched claims stay explicitly
unadjudicated; they are never silently counted as correct or false. Record adjudications blind to arm identity.

Calibration buckets preserve the exact structured completion states `verified`, `qualified-draft`,
`blocked-escalated`, and review outcomes `test_integrity_compromised`, `defect_detected`, `insufficient_evidence`,
`supported_within_scope`. Each distinct label counts once per session, and buckets may overlap. The numerator
is sessions whose final diff passes every hidden check; the denominator is every session giving that label,
with unknown outcomes also shown. Empty buckets have null shares. `verified` is never expected and is flagged
as unexpected if observed; the scorer does not promote any label to a guarantee. This is descriptive
calibration against final task success, not a probability-calibration claim or proof about the reviewed snapshot.

`retention_tasks.json` adds four independent A-then-B pairs: decimal half-cent rounding, UTC day normalization,
nested mutable ownership and cache identity. Each pair has two snapshots of the **same fixture repository**
under `retention_fixtures/<family>/{a,b}/`. Run A first, then reset that repository's working files to its B
snapshot, which reintroduces the same defect class in a related function. No A final diff carries into B.
Compare B `retained` with B `fresh`: both use the same B seed, model and budget, and both agent conversations
start fresh. Only retained imports **G6's saved history** from A; fresh imports none. No A agent conversation
is carried into either B. Alternate B condition order by frozen pair index. A itself starts fresh. Record the
canonical digest of A's original record in retained `history_from`; fresh's list is empty. Retained's
`g6_history_before_sha256` must equal A's `g6_history_after_sha256`, and the saved bytes must exist under that
digest. A must have ended with a kept log and G6 history, but need not have succeeded. This narrower control
was also retired and refrozen before any arm, as recorded in the amendment's `history_control_refinement`.
Missing prerequisites leave the pair incomplete. Report B's measures, calibration, paired outcomes and net
direction; no retention result enters a gate. Hidden checks under `retention_outcome/` were written before any
run, outside all fixture copies; neither prompts nor fixture files name G6. Tests demonstrate each seed fails
its held-out check, a reference repair passes, and an unrelated edit still fails.

The recording interface is deliberately separate from trial execution. A future runner or independent scorer
exports `<folder>/records.json`, with bytes kept under `<folder>/artifacts/<sha256>` (no suffix). Its envelope is:

```json
{"format": "g6.routing_audit_records/1", "pins": "copy the object in audit_frozen.json", "records": []}
```

Replace the illustrative pins string with the exact object. Each record contains:

- `task_id`, `client` (`codex` or `claude_code`), `arm`, `model` (exact model/version/settings), `task_digest`
  (freeze.digest of its audit task entry), `fixture_digest` and `budget` (the task's frozen values).
- Boolean `started`, `ended`; process `exit_code`; `seconds` (full session wall time, null if unknown);
  `events_sha256` and `final_diff_sha256` (null if not kept); `history_from` (normally empty).
- `g6_history_before_sha256` and `g6_history_after_sha256` (saved G6 history artifacts, null when absent).
  Retention A must keep its output snapshot. Retained B's input digest must match it; fresh B has null input.
- `adjudication_sha256`: an independent JSON artifact containing matching `task_id`, `task_digest`,
  `events_sha256`, `final_diff_sha256`, `pins` (the exact active audit pin object), plus `reviewed_diff_sha256`,
  `final_checks`, `defect_checks`, `g6_claims`.
  Each check map uses registered IDs and boolean results (true means check passes), null or omitted if unknown.
  Claims are distinct registered defect IDs or unmatched identifiers, coded from the G6 reply. Keep the reviewed
  diff bytes too. The independent recorder retains raw hidden-check logs and claim citations for human audit;
  digest binding establishes artifact identity, not adjudicator honesty. Never copy an agent's asserted success
  into this artifact. A record's own claims/checks/labels/completion/cost are ignored by the reader.
- Optional `usage_sha256`: JSON artifact with matching `events_sha256`, `includes_g6: true`, `cost_usd` and
  `model_calls` (null if unknown). It records total exact usage including G6's delegated work, with the raw usage
  receipts retained for audit. Without this artifact both cost and calls remain unknown, not an estimate of
  G6 work from the number of tool calls.

The reader derives completion and labels from the kept client event log, reads final-diff and adjudication
bytes under their digests, and refuses missing/substituted artifacts, duplicate rows and mismatched pairs.
For already-recorded data only:

    python validation/routing/audit.py --records <folder>/records.json

The export adapter and actual controlled trials are pending; this amendment specifies the recording contract
and scoring only. Nothing above reports a measured user benefit, abandonment rate or retention effect.

### Development re-freeze, 2026-11-21 (before any arm)

**State: `evidence_pending`.** `amendments/2026-11-21/routing_rebind.json` records it: tool and route text changed by
the 0.3.0 wiring merges; re-frozen before any F0 arm runs. Every route digest, and the digest of what every session
reads, changed on the 0.3.0 release candidate. As the 2026-11-17 fix pass deferred to this integration line, the
development list now binds every route as it stands, the `select` route's included; no task was added, removed or
changed. The `select` route still has no development task: the drafts in
`amendments/2026-11-17/select_tasks.unbound.json` use moment fixtures (fees, notes_m, rules_m, stock), which the
2026-11-17 registration keeps apart from the development cohort, so they were not extracted. Until the lead writes
them on development fixtures (or the owner rules the drafts' fixtures may join), the two task-coverage checks of
`tests/validation/test_routing_tasks.py` fail for `select` alone. `tasks.json` and `frozen.json` were retired with
`freeze.retire` (kept beside them as `tasks.retired-7337dbe982f5.json` and `frozen.retired-76b7a6f05b98.json`) and
written again with `freeze.freeze`. The bars and `gates.json` are unchanged; no session has been run.
`tests/validation/test_routing_rebind_amendment.py` holds the record to the files.
