---
name: g6-goal-engine
description: Use when an agent loop runs a long, multi-step goal and must check before each step that it is still inside its declared token, time and disk limits, that the action it is about to take is allowed, and that a person has signed off at the points named in advance; it reads declared controls against what the executor reports and executes nothing itself.
---

# G6 goal engine

`g6.goal_engine` holds the subtasks you declare as a tree and checks them against what your executor reports. You
declare the goal with guardrails, checkpoints, resource bounds and breakpoints, and it reads every control in force at
one node against your executor's report for it. It executes nothing and halts nothing: your executor calls
`evaluate_controls` before each node's work and obeys the report. The planner itself generates no subtask; over MCP,
`propose_goal_tree` asks the model you configured to propose some (at most 100 calls), and that tree is a proposal
until you or a mission accept it.

## When to use it

- Your agent is about to start step 7 of a long task and nothing checks that the work is still within its token or
  time budget.
- Some steps must not start until a person approves them (a send, a publish, a deletion), and you want the pause tied
  to that exact plan and node.
- You want hard checks (a metric above a threshold, an action class) applied to a node and everything below it.
- You want the declared plan written out with every subtask it could not accept listed, not dropped.

## When not to use it

- To decide the subtasks: write them, or have a model propose them with `propose_goal_tree` and review the
  proposal; this package accepts no plan for you.
- A plan of G6 op calls, checked before it runs: `navigator` (`check_plan`).
- Ceilings enforced on the run itself rather than on the executor's reports: `safety_engine`.
- One action outside the conversation (send, pay, post): `action_gating` (`evaluate`).
- A bound on reaching an unsafe state, computed on a model of the agent: `csf`.
- What the person actually wants, before any plan: `intent_engine`.

## Ops

- `propose_goal_tree`: a tree of subgoals for `objective` the configured model proposes, breadth first, within
  `branching_factor` (at most 12), `max_depth` (8), `max_nodes` (1,000) and `max_model_calls` (12 by default, at
  most 100), each call through the prompt catalogue's `mc-goal-decompose`; `constraints` and `success_criteria`
  go to the model and to the root. With no model configured it refuses (`G6_E_GOAL_NO_MODEL`).

## Python API

- `GoalSpec`: the goal as declared: `goal`, `context`, `constraints`, `resource_bounds`, `guardrails`, `checkpoints`,
  `breakpoints`, nested `subtasks`, `success_criteria`. Unknown keys and non-finite numbers are validation errors;
  `digest()` names the exact spec.
- `build_plan_tree`: the declared subtasks as `PlanNode`s with ids `root`, `root.0`, `root.0.1`; every subtask it
  cannot accept is listed with a code, with the nodes below it.
- `PlanTree`: `nodes`, `rejected`, `truncated`, the measured `depth`, and the envelope.
- `PlanNode`: a kept node, with its `parent_id`, `depth` and `children`.
- `DroppedNode`: a node left out, with its `code`, `reason` and `subtree_size`.
- `Observation`: what the executor reports for one node: `node`, `action`, `usage`, `metrics`.
- `evaluate_controls`: every control declared on the node and on every node above it, read against one observation.
- `ControlReport`: `results`, `pauses`, `blocked`, `may_proceed`, and the envelope.
- `ControlResult`: one guardrail or checkpoint with its `verdict`: `pass`, `fail` or `unknown`.
- `PauseRequest`: an active breakpoint at a node, with the approval `scope` that releases it.
- `release_pause`: the `ApprovalRecord` that releases a pause, only for a live approval an `ApprovalAuthority`
  holds as human-issued for that scope (and run); otherwise None. It issues no approval.
- `register_predicate`: adds a predicate factory by name; a built-in is replaced only with `override=True`.
- `list_predicates`: the registered names; built in are `resource_limit`, `metric_above`, `metric_below` and
  `csf_hazard`.
- `ResourceBoundsSpec`: `max_execution_seconds`, `max_disk_bytes` and `max_tokens_per_minute` up to
  `max_tokens_per_month`, each checked against the matching `usage` figure.
- `GuardrailSpec`: a hard check, a registered `predicate` built from `params`; a fail or an unknown blocks.
- `CheckpointSpec`: a soft check built the same way; reported, never blocking.
- `BreakpointSpec`: a human gate; while `active`, each node it applies to gets a `PauseRequest`.
- `HAZARDS`: the fixed per-action estimates `csf_hazard` reads.
- `DEFAULT_MAX_DEPTH`: 15.

## Reading the result

`may_proceed` is False when a guardrail fails (`G6_E_GOAL_GUARDRAIL_FAILED`), when one cannot be decided because the
observation does not report the usage, metric or action it needs (`G6_E_GOAL_GUARDRAIL_UNKNOWN`: unknown is never a
pass), and while a pause waits for approval (`G6_E_GOAL_APPROVAL_REQUIRED`). Checkpoints are reported and never block.
A node the spec does not have is `G6_E_GOAL_NODE_UNKNOWN`. Otherwise the report is `qualified-draft` with
`G6_E_GOAL_OBSERVATION_UNCHECKED`: the guardrails passed on the executor's word. A plan is `qualified-draft` at best
(`G6_E_GOAL_PLAN_DECLARED`, or `G6_E_GOAL_NOT_DECOMPOSED` for a root with no subtasks), and says what it left out
(`G6_E_GOAL_PLAN_REJECTED`, `G6_E_GOAL_PLAN_TRUNCATED`).

## Alone and composed

Over MCP, `g6_invoke("goal_engine", "propose_goal_tree", ...)` proposes a tree with the model you configured; a
reply that is not one JSON object of subgoals is that node's error, never a subgoal, and nothing a reply says sets a
state. From Python: `from g6.goal_engine import GoalSpec, Observation, build_plan_tree, evaluate_controls`, and
`g6.goal_engine.decomposition.propose_goal_tree` with a proposer of your own.

Composed:

- `intent_engine`: `GoalSpec` keeps the field names of its `GoalInput`, and its contract's success criteria are what
  your subtasks should serve.
- `g6.core.approval_authority`: a person records an approval for `PauseRequest.scope` with
  `ApprovalAuthority.issue(...)`; the executor only presents the id to `release_pause`. An agent never issues its own.
- `action_gating`: gate the node's outward action itself; `csf_hazard` here only reads a fixed number per action
  name.
- `safety_engine` (`PolicyEngine.authorize_action`) checks runtime ceilings before each action that asks it; use it
  beside this package, which only reads the usage your executor reports.

## Failure modes it catches

- `goal_drift` (a long task drifting from its goal): the plan is exactly the declared tree, every control applies to
  its node and every node below it, an executor that asks gets a report before each node, a breakpoint holds
  `may_proceed` False at its nodes until an approval recorded for that exact plan and node is presented, and nothing
  is dropped from the plan silently.
- `budget_overrun` (a task overrunning its token, cost or time budget): declared resource bounds are compared with the
  reported usage before the node starts; a figure over its limit fails, and a limited figure that is not reported is
  `unknown`, which blocks.

## Blind spots

- The observation is the executor's word: nothing checks that it measured what it reports.
- It enforces nothing. An executor that ignores `may_proceed` runs anyway.
- `csf_hazard` reads fixed, uncalibrated per-action numbers; an action with no estimate fails.
- `constraints` and `success_criteria` are carried as text and not checked, and nothing checks that the subtasks
  achieve the goal or that their order is right.
- There are no cost limits in money: only seconds, disk bytes and tokens per period.
- A predicate you register is your code; it should be pure, and one that raises reads `unknown`.
- `unchecked_plan`: a proposed tree is a model's guess. Its shape is checked, its subgoals are not; follow none of
  them without checking each step against what its actions need.

## What it needs

Nothing outside G6: it is Python on pydantic, which installs with g6, and `align_prompt_library`'s catalogue. Only
`propose_goal_tree` calls a model: the one you configured (`G6_LLM_PROVIDER` in your key file).

## Examples

A plan of three declared nodes, then four reports: within bounds, over the token limit, with no usage reported, and at
a node behind a breakpoint:

```python
from g6.goal_engine import GoalSpec, Observation, build_plan_tree, evaluate_controls

spec = GoalSpec.model_validate({
    "goal": "Publish the quarterly report",
    "resource_bounds": {"max_tokens_per_hour": 200000},
    "guardrails": [{"name": "safe_actions", "predicate": "csf_hazard", "params": {"max_prob": 0.05}}],
    "subtasks": [{"goal": "Draft the report"},
                 {"goal": "Send it out", "breakpoints": [{"name": "review_before_send"}]}],
})
plan = build_plan_tree(spec)
assert [node.node_id for node in plan.nodes] == ["root", "root.0", "root.1"]
assert plan.completion_state == "qualified-draft"

draft = evaluate_controls(spec, Observation(node="root.0", action="file_write", usage={"tokens_per_hour": 1200}))
assert draft.may_proceed and draft.completion_state == "qualified-draft"
assert [(r.name, r.verdict) for r in draft.results] == [("resource_bounds", "pass"), ("safe_actions", "pass")]

over = evaluate_controls(spec, Observation(node="root.0", action="file_write", usage={"tokens_per_hour": 250000}))
assert not over.may_proceed and over.warning_card["code"] == "G6_E_GOAL_GUARDRAIL_FAILED"

unreported = evaluate_controls(spec, Observation(node="root.0", action="file_write"))
assert not unreported.may_proceed and unreported.warning_card["code"] == "G6_E_GOAL_GUARDRAIL_UNKNOWN"

send = evaluate_controls(spec, Observation(node="root.1", action="file_write", usage={"tokens_per_hour": 1200}))
assert not send.may_proceed and len(send.pauses) == 1 and send.completion_state == "blocked-escalated"
```

Propose a tree for an objective (with no model configured, it refuses with `G6_E_GOAL_NO_MODEL`):

```g6-example op=propose_goal_tree
{"objective": "Fix the crash in the export step", "constraints": ["keep the public API"], "max_depth": 1, "max_model_calls": 1}
```

## The four open problems

- **Learning signals**: Not yet: it writes nothing to a run ledger. Each report's `evidence` lists every guardrail or
  checkpoint that did not pass, with its code, node and reason: what a caller can record as a labelled incident.
- **Evaluators the model cannot influence**: the controls are code and the plan's own declarations; predicates read
  only the observation, never the spec's claims about itself, and a pause opens only for an approval the
  `ApprovalAuthority` holds as human-issued for that scope; it cannot tell a person from code that calls `issue`, so
  keep `issue` out of the agent's reach. The other weak point is the observation, which the executor (possibly a
  model) writes.
- **Transfer by failure mode**: a control is declared on a node and applies to every node below it, whatever the task.
  Not yet: no catch rate has been measured on task families.
- **Enough held-out cases**: Not yet: the hazard numbers and any thresholds you declare are not judged on held-out
  cases.
