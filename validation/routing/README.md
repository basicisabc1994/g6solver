# Routing trials

Does a fresh agent with the `using-g6` skill make the right G6 calls, and stay quiet when it should? This folder is
the instrument for the "Routing" row of the 0.3 design's section 5 (design 14.1).

**State: `evidence_pending`.** No session has been run. Nothing here is a measured result.

## What is here

- `tasks.json`: the development cohort, frozen. One positive, one negative and one missing-tool task for every route
  of `src/g6/skills/using-g6/routing.json`. Each route's entry under `routes` is the digest of that route (its anchor,
  detail files and members): a route that changes unbinds its own tasks and no other's, and nothing runs until its
  tasks are reviewed and the new digest is bound.
- `final_tasks.json`: absent. The final cohort is written by someone other than the builders of the routes, after the
  last route of the release has merged: two positive and two negative tasks per route and one missing-tool task per
  route. It is frozen by putting its digest in `gates.json`, and run once.
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

## What a transcript is evidence of

A session is `completed` only when the client exited with code 0 and printed its own terminal event; a client that
exits with an error, or prints no such event, is `interrupted` and counts against every gate. Each transcript says
how it was produced: the client program and its version, its exit code and the digest of its event log. A transcript
a stand-in produced says so, and can fail a gate but never pass one: its reading is `simulated`. A log holds one
transcript per task and repeat; a second run into the same log is refused, and a log with two is not scored. The
report names the digests of the task list and the gate file it was scored with.

## What is sent, and where

Each session sends the task's prompt, the fixture repository the agent reads and G6's guidance to the client's
provider: `anthropic:claude_code` for Claude Code, `openai:codex` for Codex. The fixtures hold no user data. Every
session passes G6's one consent check first, with the fixture declared as a source. Without the person's grant the
session is not started and its transcript is `refused`.

List what to grant, then grant each line in the client's own dialog (`g6_cloud_grant`):

    python validation/routing/run.py sources --client codex --out <folder>

Keep `<folder>` the same for the grant and the run: the workspace folders are part of what is granted.

The trial's servers use a G6 home inside `<folder>`, with a copy of the person's sharing decisions and nothing else
of theirs. A task states the host permissions its server starts with (the review task allows `patch_review.review`,
so G6 runs the synthetic repository's tests, confined); every other task's server starts with none.

## How to run

    python validation/routing/run.py run --client codex --cohort development --out <folder>
    python validation/routing/run.py score --client codex --cohort development --out <folder>

Sessions run one at a time, with nothing else running beside them on the machine. The skill is installed in each
workspace where the client reads skills: `.claude/skills/using-g6` for Claude Code and `.codex/skills/using-g6` for
Codex, the folders `g6 skills install --client <client>` writes. No `AGENTS.md` and no Codex plugin ships, so that
folder is the only guidance the Codex arm gets.

The development cohort may be run again after a revision of the skill; each revision that changes a route re-binds
that route's digest. The final cohort's transcripts are read by `score.py` and by nothing else.

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
