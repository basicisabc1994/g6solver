---
name: g6-self-maintenance
description: Use when you have measured how a multi-stage workflow performs (each stage's attempts, successes and daily capacity) and want to know which stage limits it most and which maintenance ticket to work on next within a token budget, as a plan to review that runs, reserves and approves nothing.
---

# G6 maintenance planner

`g6.self_maintenance` applies the theory of constraints to a measured workflow. `identify_constraint` names the stage
with the least effective capacity (its capacity per day times its success rate), and only when every stage was
independently observed. `plan` ranks the tickets filed against that stage within a token budget. Over MCP both are
plans to review: nothing runs, no budget is reserved, nothing is approved. Its best state is `qualified-draft`.

## When to use it

- A workflow of serial stages (intake, coding, review, release) is slower than it should be, and you have counts for
  each stage: which one should you fix first?
- You hold several maintenance tickets and a token budget, and want the ones that raise the limiting stage ranked
  first, with the ones that do not left out and said so.
- You want the five focusing steps (identify, exploit, subordinate, elevate, repeat) laid out for the stage your
  measurements point at.

## When not to use it

- Running a fix: the cycle that runs an agent is the host's Python API, it needs a person's approval in the operator's
  queue, and its live agent path refuses in this release.
- Measuring the workflow: the counts are yours to collect; this component reads them, it does not gather them.
- Deciding whether a change is safe to merge: use `self_training` and the repository's own tests.

## Ops

- `identify_constraint`: the stage with the least effective capacity among `stages` (each `name`, `attempted`,
  `succeeded`, `capacity_per_day`, `independently_observed`), the throughput it bounds, every stage's effective
  capacity and the five focusing steps; "unknown" when a stage was not independently observed.
- `plan`: the `tickets` (each `id`, `title`, `evidence`, `files`, `tests`, `estimated_tokens`) that `ticket_stages`
  files against the constraint, ranked within the default token budget for `day` and `provider`; a simulation that
  reserves nothing.

## Alone and composed

Alone over MCP: `g6_invoke("self_maintenance", "identify_constraint", '{"stages": [...]}')`. No op is sensitive:
nothing runs and nothing is written. From Python: `from g6.self_maintenance.constraints import identify_constraint,
focus_plan` and `from g6.self_maintenance.controller import run_one_cycle`.

How it is guarded:

- Your measurements are your word: they come back under `evidence[0]` with `declared_by: "caller"`, they choose the
  constraint, and they never raise a state. A stage you did not mark `independently_observed` stops it naming one.
- Over MCP the plan uses an empty budget book: no reservation can be made or forged from a call.
- `run_one_cycle` runs an agent or a checker only with a person's approval in the operator's queue, decided by
  someone other than the requester and bound to exactly that ticket, provider and day; without it the cycle is a
  simulation, and with an approval for another ticket it is `approval_mismatch`. The report names the approver.
- The live Codex and Claude Code path refuses before any runtime is built
  (`G6_E_SELF_MAINTENANCE_LIVE_AGENT_OFF`); it waits for the mission container.

Composed:

- `operator`: its action queue holds the person's run approvals; its budget ledger holds the loop's paced provider
  weeks (`pacing`), and is where this one's token book will go.
- `self_training` and `metaharness`: a planned fix still passes their gates and a person's approval before it lands.
- `metaharness`: `g6.metaharness.loop` runs the shadow loop on `constraints`, `pacing` and `envelope`. Its adoptions
  hold only in its own workspace.

## Failure modes it catches

- `low_value_first` (effort spent on low-value tasks first): tickets are ranked only for the stage your measurements
  show limits the workflow, and tickets for other stages are left out and said so.
- `unchecked_self_change` (a self-change that slips past its own checks): over MCP nothing runs or is approved; a
  host's cycle needs a person's approval bound to the ticket, an independent checker's receipts and a changed scope
  inside the ticket's files, and its live agent path refuses in this release.

## Reading the result

- `identify_constraint` answers `qualified-draft` with `G6_E_SELF_MAINTENANCE_CONSTRAINT`, or `blocked-escalated`
  with the same code and `constraint: "unknown"` when a stage was not independently observed.
- `plan` answers with `G6_E_SELF_MAINTENANCE_PLAN`: `selected`, `ranked`, `refusals` and the `rationale`;
  `blocked-escalated` when no ticket fits the constraint or the budget.
- A refusal is `blocked-escalated` with `G6_E_SELF_MAINTENANCE_INPUT` (and why), or `G6_E_INPUT_VALIDATION`.

## Blind spots

- `wrong_problem`: the constraint is the slowest stage by your counts. If the counts measure the wrong thing, or the
  stages are not serial, the stage it names is not what limits the people the workflow serves.
- A caller's stage measurements are the caller's word: nothing here checks that `independently_observed` is true.
- Token estimates on tickets are guesses; the budget book is a ledger of reservations, not a meter.

## What it needs

Nothing outside G6 for its ops: the standard library and G6's own ledger. The host's cycle API uses `operator`,
`action_gating` and `safety_engine`; its live agent path (`agents`, `agent_codex`, `agent_claude`) is off in this
release. No extra, no outside program, no model.

## Examples

Which of two measured stages limits the workflow:

```g6-example op=identify_constraint
{"stages": [{"name": "review", "attempted": 20, "succeeded": 10, "capacity_per_day": 5, "independently_observed": true}, {"name": "coding", "attempted": 20, "succeeded": 19, "capacity_per_day": 20, "independently_observed": true}]}
```

The next ticket for that stage, as a simulation:

```g6-example op=plan
{"stages": [{"name": "review", "attempted": 20, "succeeded": 10, "capacity_per_day": 5, "independently_observed": true}, {"name": "coding", "attempted": 20, "succeeded": 19, "capacity_per_day": 20, "independently_observed": true}], "tickets": [{"id": "issue-1", "title": "Fix a user-visible error", "evidence": "failing regression fixture", "files": ["src/g6/code/swe_diagnostics/read.py"], "tests": ["tests/code/test_swe_diagnostics.py"], "estimated_tokens": 6000}], "ticket_stages": {"issue-1": "review"}, "day": "2026-10-29"}
```

## The four open problems

- **Learning signals**: the budget book records every reservation and settlement in a hash chain, and in the shadow
  loop each cycle's outcome is in the constraint log and its adoption is what the next tick runs on. Not yet: no
  scheduler, and nothing the loop adopts reaches anyone.
- **Evaluators the model cannot influence**: the constraint is arithmetic over your counts, and a cycle's checker is
  injected by the host, never chosen by the agent. Not yet: the counts themselves are the caller's.
- **Transfer by failure mode**: the guard is bound to `low_value_first` and `unchecked_self_change`, not to G6: any
  serial workflow with counts can be planned. Not yet: how often the named constraint was the real one is not measured.
- **Enough held-out cases**: Not yet: a plan is checked by fixed tests, not by G6's sequential test on held-out
  workflows.
