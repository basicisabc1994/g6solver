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
