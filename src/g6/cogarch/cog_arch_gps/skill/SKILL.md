---
name: g6-cog-arch-gps
description: Use when an agent needs a short sequence of steps to reach a goal from declared actions (each with preconditions, what it adds and what it removes), or must check a plan someone else wrote, a model's included, step by step before acting on it, so a plan that skips a precondition or undoes an earlier goal is caught before anyone follows it.
---

# G6 cog_arch_gps

A small planner in the style of the General Problem Solver: means-ends analysis over STRIPS operators. To reach a
goal it picks an operator that adds it, reaches that operator's preconditions first, then applies it. Every plan,
whoever made it, is replayed against the declared operators before it is offered.

## When to use it

- Small symbolic planning: a few dozen facts and operators, such as set-up steps, a workflow with prerequisites or a
  toy puzzle.
- Checking a plan an agent or a model wrote against the actions you declared: `validate_plan` names the step that
  fails and why.
- Turning a description into a problem with a model you give it, where the draft is only offered with a plan that
  passed the replay.

## When not to use it

- Hard or large planning domains: means-ends analysis is incomplete, so a failure to find a plan is not proof that
  none exists. Use a PDDL planner there, and `validate_plan` on its output.
- Numbers, resources, time or uncertainty: facts are plain strings that hold or do not. For linear programmes use
  `g6.optimisation`.
- As evidence that a plan works in the world: the replay checks it against the operators you declared.

## Ops

- `solve`: a plan by means-ends analysis, goals in the order given, every conjunction checked again once reached, then replayed against the operators; a plan whose replay fails is refused with the failing step.
- `validate_plan`: replay any plan (a person's, a planner's or a model's) step by step against the declared operators.
- `encode`: a model you give the block from Python writes the problem; it is parsed into the operator schema and offered only with a plan that passed the replay.
- `keep_operators`: keep a named operator set under G6's state folder for `operator_set` in later calls.

## Reading the result

An operator is `{"name", "preconditions", "add_list", "delete_list"}` (lists of facts; `description` optional). Pass
`operators`, or `operator_set` naming a kept set, not both.

`solve` returns `plan`, `steps` (each step's operator and the facts it added and deleted), `final_state` and
`G6_E_GPS_PLANNED`, `qualified-draft`, with two evidence entries: the search (nodes, goal orders tried, caps) and the
replay. A failed `validate_plan` is `G6_E_GPS_PLAN_FAILS`, `blocked-escalated`, with `failed_at` (the step number) and
one of `unknown_operator`, `missing` (preconditions that did not hold) or `goals_missing` with `clobbered_by` (the step
that last deleted each missing goal).

Refusals: `G6_E_GPS_NO_PLAN` (none found, which is not proof that none exists), `G6_E_GPS_CAP` (`cap` names which:
`max_depth`, `max_nodes`, `max_orders` or `time_limit_s`), `G6_E_GPS_REPLAY_FAILED` (the search's own plan failed its
replay: a defect, never offered), `G6_E_GPS_NO_MODEL`, `G6_E_GPS_MODEL_FAILED`, `G6_E_GPS_ENCODE_MALFORMED`,
`G6_E_GPS_ENCODED_NO_PLAN` (the draft problem is returned for you to correct), `G6_E_GPS_NO_SET`,
`G6_E_GPS_SET_EXISTS`, `G6_E_GPS_STORE` (a kept set edited after it was kept) and `G6_E_GPS_INPUT`.

## Alone and composed

Alone: `solve` with `initial`, `goals` (an ordered list: the order given is tried first, then other orders up to
`max_orders`) and `operators`. Caps and their ceilings: `max_depth` 50 (100), `max_nodes` 20,000 (200,000),
`max_orders` 24 (720), `time_limit_s` 10 (60).

Composed: an agent or a model proposes a plan; `validate_plan` replays it before `g6.action_gating` decides on each
outward step. A plan from a PDDL planner can be replayed the same way. From Python, `CogArchGpsBlock(caller=...)` takes
any `g6.core.llm` caller for `encode`, local first. Over MCP, `encode` uses the model the person set in `~/.g6/.env`
(for example `G6_LLM_PROVIDER=claude_code`); with none, write the problem yourself and call `solve`.

## Failure modes it catches

- `no_plan_to_goal`: an agent acting step by step with no plan. `solve` gives one that reaches every goal from the
  declared operators, or says it found none and why (no operator adds a goal, or a cap cut the search).
- `unchecked_plan`: a plan from an agent or a model followed without checking each step. The replay applies each
  step to the state and checks its preconditions, and checks every goal at the end, whoever wrote the plan, so a plan
  that fails halfway through, or ends having undone an earlier goal, is caught before anyone follows it.

## Blind spots

- The operators are the caller's description of the world. A plan that holds against wrong operators is still wrong;
  the replay cannot see the world.
- Means-ends analysis is incomplete and not optimal: it may miss a plan, or find a longer one than needed.
- A model's encoding may leave out a precondition or a deletion, which makes a plan look easier than it is: read the
  draft's operators.
- Only plain facts: no variables in operators, no numbers, no time.

## What it needs

Nothing outside G6: no key, no outside program, no extra, no network. `encode` needs a model: a `g6.core.llm` caller
given from Python, or over MCP the one the person set in their key file. Kept sets are files under G6's state folder.

## Examples

```g6-example op=solve
{"initial": [], "goals": ["a", "b"], "operators": [{"name": "op1", "add_list": ["a"]}, {"name": "op2", "add_list": ["b"], "delete_list": ["a"]}]}
```

The order given fails (`op2` undoes `a`), so the other order is tried: the plan is `op2`, `op1`, replayed with both
goals holding at the end.

```g6-example op=validate_plan
{"initial": [], "goals": ["kettle_boiled", "tea_made"], "operators": [{"name": "boil", "add_list": ["kettle_boiled"]}, {"name": "pour", "preconditions": ["kettle_boiled"], "add_list": ["tea_made"], "delete_list": ["kettle_boiled"]}], "plan": ["boil", "pour"]}
```

Refused with `G6_E_GPS_PLAN_FAILS`: `kettle_boiled` is missing at the end, deleted by step 2.

```g6-example op=keep_operators
{"name": "tea", "operators": [{"name": "boil", "add_list": ["kettle_boiled"]}, {"name": "pour", "preconditions": ["kettle_boiled"], "add_list": ["tea_made"]}]}
```

```g6-example op=encode
{"description": "Make a cup of tea: boil the kettle, then pour."}
```

With no model configured, this is refused with `G6_E_GPS_NO_MODEL`, which says how to configure one; with one, the
model's draft problem comes back with a replayed plan.

```python
import json

from g6.cogarch.cog_arch_gps import CogArchGpsBlock, CogArchGpsInput

class Drafter:  # in this example, a model that writes the problem and a plan that skips a step
    def complete(self, system, user, *, max_tokens=1024):
        return json.dumps({"initial": [], "goals": ["tea_made"], "plan": ["pour"], "operators": [
            {"name": "boil", "add_list": ["kettle_boiled"]},
            {"name": "pour", "preconditions": ["kettle_boiled"], "add_list": ["tea_made"]}]})

out = CogArchGpsBlock(caller=Drafter()).infer(CogArchGpsInput(op="encode", description="Make tea.")).unwrap()
assert out["plan"] == ["boil", "pour"] and out["plan_source"] == "means-ends analysis"
assert out["completion_state"] == "qualified-draft"
```

## The four open problems

- **Learning signals**: each replay names the step and operator that failed, the signal for which operators are
  wrong. Not yet: nothing learns from it; a person corrects the operators.
- **Evaluators the model cannot influence**: the replay, fixed code over the declared operators and the plan, shared
  with neither the search nor the model.
- **Transfer by failure mode**: bound to `unchecked_plan` and `no_plan_to_goal`; the same replay holds a plan from
  any source.
- **Enough held-out cases**: Not yet: the tests are small hand-built problems with known plans, not a planning
  benchmark.
