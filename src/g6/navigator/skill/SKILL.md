---
name: g6-navigator
description: Use when an agent has composed a plan of several G6 op calls and is about to run it, and wants every step checked first (the component exists and loads, the op exists and is not sensitive, the args fit the op's contract, the authority rule admits it) so the plan does not fail halfway; or when it needs one op's card from its parameter contract.
---

# G6 navigator

`g6.navigator` checks a plan of op calls against the registry, the ops' parameter contracts and the authority rule
before anything runs, reports every problem with its step and a stable code, and runs the plan through an executor you
inject only when the whole plan passes. It also gives one op's card from its contract.

## When to use it

- You chained several `g6_invoke` calls (or Python calls) into a plan and are about to run step 1.
- A plan's later step would need inputs, permissions or authority that step 1 cannot give, and you want to know now.
- You want to bound a plan's worst side effect (`read`, `local_write`, `external`, `irreversible`) before it runs.
- You need an op's required and optional parameters, side effect, cost and a known-good example in one card.

## When not to use it

- Finding which component fits a need: `g6_find` over MCP, or `g6.core.discovery.find` from Python.
- One component's ops and fields: `g6_describe`.
- A goal's subtasks with guardrails, budgets and human approval points: `goal_engine`.
- Checking a step's result after it ran: the component's own checks, or `coding_agent_gate` (`verify_answer`) for a
  coding answer. Running a step is not checking it.

## Python API

- `check_plan`: judges a list of steps `{"component", "op", "args"}` before anything runs and returns a `PlanReport`;
  optional `contracts`, `authority` (a reading `(component, op) -> class`) and `max_effect`.
- `run_plan`: the same check, then each step in order through `executor(component, op, args)`, stopping at the first
  failed step; with no executor nothing runs. Returns a `PlanResult`.
- `PlanReport`: `valid`, `issues`, `steps`, the plan's worst `effect`, the envelope, `to_dict()`.
- `PlanResult`: the `report`, each `StepRun`, `executed`, the envelope, `to_dict()`.
- `Issue`: `step` (None for the plan as a whole), `code` and `message`.
- `StepCheck`: how one step was judged: `exists`, `effect`, `authority`, `contract_sha256`, `issues`.
- `StepRun`: one step the executor was called for: `ok` or `failed`, with its result or error.
- `card`: one op's card within `CARD_CHARS` (1,600 characters): purpose, maturity, authority class and, from its
  contract, required and optional parameters, side effect, cost and an example; None for an op nothing names.
- `contract_for`: the op's `ParamContract`, or None.
- `ops_of`: every op a contract declares on a component or its block lists.
- `purpose`: the component's own help line for the op.
- `judge`: the authority rule for one op: None when the rule admits it, else a `Finding`.
- `Finding`: the class judged, the effect it was judged on, and the class it needs.
- `shipped_authority`: the library's own authority reading, each row bound to the digest of its contract.
- `NOT_CLEARED`: the code `G6_E_OP_NOT_CLEARED`.
- `ACTS_BELOW_A2`: the code `G6_E_OP_ACTS_BELOW_A2`.

## Reading the result

A plan with no issue is `qualified-draft` with `G6_E_NAV_STATIC_CHECK`: statically consistent, and nothing ran. Any
issue makes it `blocked-escalated`, and every issue is listed with its step: `G6_E_UNKNOWN_COMPONENT`,
`G6_E_NAV_COMPONENT_UNLOADABLE`, `G6_E_NAV_UNKNOWN_OP`, `G6_E_SENSITIVE_COMPONENT_DENIED`, `G6_E_INPUT_VALIDATION`
(a missing required key, an unknown key, a wrong type), `G6_E_OP_NOT_CLEARED` (class `A0`, which includes every op no
reading measured), `G6_E_OP_ACTS_BELOW_A2` (an op that can act, or declares no side effect, below class `A2`),
`G6_E_NAV_EFFECT_OVER_BUDGET`, `G6_E_NAV_STEP_MALFORMED` (a step names only `component`, `op` and `args`; it cannot
declare its own authority) and `G6_E_NAV_EMPTY_PLAN`. A run is `blocked-escalated` with `G6_E_NAV_NO_EXECUTOR` or
`G6_E_NAV_STEP_FAILED` (it says how many later steps did not run), and at best `qualified-draft` with
`G6_E_NAV_RAN_UNCHECKED`; a step's own claim of `verified` is not carried.

## Alone and composed

It has no block, so over MCP `g6_invoke("navigator", ...)` answers `G6_E_NO_BLOCK`; `g6_describe("navigator")` serves
this skill. From Python: `from g6.navigator import card, check_plan, run_plan`.

Composed:

- `workforce`: the parameter contracts, the side-effect ladder and the authority tables all come from it, so the
  plan check and the workforce planner apply one rule.
- `metaharness` checks a declared workflow's pipeline steps with `check_plan`, passing the workflow's own authority
  reading.
- `get_registry().invoke` from `g6.core.registry` is a ready executor for `run_plan`.

## Failure modes it catches

- `plan_fails_midway` (a plan that fails halfway through): every step is judged before any runs, all problems are
  reported at once with their steps, `run_plan` refuses the whole plan if any step has a problem, and a run stops at
  the first failed step and says how many later steps did not run.

## Blind spots

- Steps take static args: nothing checks that one step's output can feed the next.
- An op with no parameter contract has its args checked only by its component when it runs; it declares no side
  effect, so it counts as acting (it needs class `A2`) and no `max_effect` admits it.
- The default authority reading measures only the workforce bench's ops, carried over and not re-measured. Every
  other op reads `A0` and is refused until you pass an `authority` reading you trust.
- A run does not undo the steps before a failure; they may have had their effects.
- Whether cards help an agent reach a correct call faster than a flat listing has not been measured.

## What it needs

Nothing outside G6: it is pure Python, and reads the parameter contracts that ship with `g6.workforce` and the
component registry. It calls no model.

## Examples

A real op with valid args passes; bad args and an unknown component are both reported; an unmeasured op is refused;
nothing runs without an executor; and an injected executor runs the step:

```python
from g6.navigator import card, check_plan, run_plan

step = {"component": "workforce", "op": "run_tests",
        "args": {"repo": "my-repo", "tests": ["tests/test_a.py::test_a"]}}
report = check_plan([step])
assert report.valid and report.completion_state == "qualified-draft"

broken = check_plan([{"component": "workforce", "op": "run_tests", "args": {"repo": 3, "extra": True}},
                     {"component": "no_such_component", "op": "x"}])
assert not broken.valid and broken.completion_state == "blocked-escalated"
assert {issue.code for issue in broken.issues} == {"G6_E_INPUT_VALIDATION", "G6_E_UNKNOWN_COMPONENT"}

claim = {"component": "grounding", "op": "check_claim",
         "args": {"claim": "The sky is blue.", "sources": {"note": "The sky is blue."}}}
assert [issue.code for issue in check_plan([claim]).issues] == ["G6_E_OP_NOT_CLEARED"]

assert run_plan([step]).warning_card["code"] == "G6_E_NAV_NO_EXECUTOR"
calls = []
result = run_plan([step], executor=lambda component, op, args: calls.append(op) or {"passed": True})
assert calls == ["run_tests"] and result.completion_state == "qualified-draft"
assert card("workforce", "run_tests")["side_effect"] == "read"
```

## The four open problems

- **Learning signals**: Not yet: it writes nothing to a run ledger. A `PlanReport`'s issues (step, code, message) and
  a `PlanResult`'s failed step are what a caller can record as labelled incidents.
- **Evaluators the model cannot influence**: the check is code over the registry, the shipped contracts (bound by
  digest) and an authority reading; a step cannot declare its own authority. The `authority` you pass is trusted as
  given, so it must come from the person running G6, not from the agent planning.
- **Transfer by failure mode**: the same checks apply to any plan of any components. Not yet: no catch rate has been
  measured on task families.
- **Enough held-out cases**: Not yet: neither the check's catch rate nor the cards' benefit has been judged on held-out
  cases.
