---
name: g6-solver
description: Use when an agent has a set of step functions (prompts, a code runner, an evaluator) and wants them run through a fixed 15-step table in a known order, with retries and fallbacks, every missing, skipped or failed step recorded, and the final answer checked against stated properties before anyone calls the run a success.
---

# G6 solver

`g6.solver` runs a goal through a fixed table of 15 named steps, each one a callable you supply. It plans which steps
run, retries and falls back, picks the final answer, checks it against your deterministic constraints and labels the
run. Every step returning a value is not a check: the best state is `qualified-draft`.

## When to use it

- You have runners for some of the steps (`goal_setting`, `code_construction`, `solution_execution`,
  `solution_evaluation`, ...) and want one record of what ran, what was skipped, what failed and what fell back.
- You are about to report a run as a success because every step returned something.
- You want a stated property of the answer (it contains a value, stays under a length) checked by code before the
  answer leaves the run.

## When not to use it

- A simple question: one direct model call (`reasoning.DirectStep`). Whether a 15-step run beats one call is not
  established.
- A coding agent's answer before "done": `coding_agent_gate` (`verify_answer`).
- An answer that must be exact: hand it to `formal`, `algebra` or `optimisation` yourself; a runner that calls one
  does not set `outcome`.
- A goal tree with guardrails, budgets and approvals: `goal_engine`.

## Python API

- `SolverHistory`: `record(output, series=..., dry_run=True)` accepts only unchanged outputs observed by `solve`
  in this process. Persistence requires an explicit `dry_run=False`; it is a local state write, not an outward
  action, so the action gate is not asked. Duplicate run/series pairs refuse.
  `trend(series=..., window=30)` reads recent runs without creating state and reports the Clopper-Pearson exact
  two-sided 95% interval (2.5% in each tail; `method` in the evidence) and whether the pass rate fell significantly
  from the previous window (`decline_significant`: this window's upper bound below the previous window's lower
  bound). `check_regression(threshold=...)` needs at least `min_samples` checked outcomes (10, and never fewer); it
  is `regression` when the upper bound is below the threshold or the decline is significant, `pass` only when the
  lower bound reaches the threshold and the interval is at most `max_ci_width=0.30` wide, and otherwise
  `review_required`. Ten passes in ten at threshold 0.8 is not a pass: the lower bound is 0.69.
- `HistoryReport`: `decision`, `code`, `reason`, `metrics`, `completion_state` and `to_envelope()`.

- `solve`: runs the planned steps through `runners` (step name to a callable taking a `StepContext`), with retries
  and optional `CategoryRegistry` fallbacks; picks the final answer, checks the constraints and, if given, runs an
  `LLMQualityChecker`; returns a `SolverOutput`.
- `SolverInput`: `goal`, `context`, `constraints`, `complexity_override`, `skip_steps`, `run_mode` and
  `reviewer_signature`; unknown fields are validation errors.
- `ConstraintSpec`: a deterministic check on the final answer: `contains` or `excludes` a `value`, `max_chars` or
  `min_chars` a `threshold`. One with no `kind` cannot be checked, so the run is refused before any step.
- `SolverOutput`: `status`, `outcome`, `success`, `completion_state`, `steps`, `failures`, `checks`,
  `final_answer`, `agentic_evidence`, `to_envelope()`; the label fields are re-derived on every construction.
- `plan_run`: the complexity and the steps a run would take, without running any.
- `SolverPlanner`: the three planning decisions (complexity, steps, which candidate answer), deterministic without a
  model; with an `LLMCaller`, a failed or off-menu reply falls back and is recorded as `degraded`.
- `classify_task`: the keyword heuristic: `simple`, `moderate` or `complex`.
- `profile_for`: the steps each complexity runs: simple 1, 8, 10, 11; moderate adds 12; complex runs all 15.
- `STEP_REGISTRY`: the 15 steps by number and runner name, from `goal_setting` to `metalearning`.
- `execute_step`: one step: its runner up to the step's attempt limit, then each fallback once.
- `StepContext`: what a runner sees: goal, context, constraints, complexity, the step and earlier outputs.
- `CategoryRegistry`: named fallback runners per `ComponentCategory`.
- `LLMQualityChecker`: a red-flag scan, then optionally a model's judgement; no model, a failed call or an unreadable
  reply is `unchecked`, never a pass.
- `red_flags`: `EMPTY_OUTPUT`, `REFUSAL` and `REPETITION`.
- `evaluate_constraints`: the constraint checks on one answer.
- `derive_completion_state`: the label rule, from visible fields only.

## Reading the result

`status` says whether the steps returned: `completed`, `incomplete` (a planned step had no runner), `halted` (a step
failed after retries and fallbacks) or `refused`. `outcome` says whether a deterministic named check passed on the
answer: `passed`, `failed` or `unchecked`; `success` is `outcome == "passed"`. A model's judgement can lower the
outcome, never raise it. `blocked-escalated` comes with `G6_E_SOLVER_REFUSED` or the refusal's own code
(`G6_E_SOLVER_EMPTY_GOAL`, `G6_E_SOLVER_PRODUCTION_UNSUPPORTED`, `G6_E_SOLVER_SIGNATURE_UNVERIFIABLE`,
`G6_E_SOLVER_CONSTRAINT_UNEVALUABLE`), `G6_E_SOLVER_STEP_FAILED`, `G6_E_SOLVER_NO_ANSWER` or
`G6_E_SOLVER_CHECK_FAILED`. A `qualified-draft` card is `G6_E_SOLVER_INCOMPLETE` when a planned step had no runner
(it names each step that did not run, whatever the checks say, as does `degradation_reason`), otherwise
`G6_E_SOLVER_CHECKED_DRAFT` (the named checks passed, on those properties only) or `G6_E_SOLVER_UNCHECKED`. A runner
under an unknown step name raises `ValueError` with `G6_E_SOLVER_UNKNOWN_STEP`.

## Alone and composed

It has no block, so over MCP `g6_invoke("solver", ...)` answers `G6_E_NO_BLOCK`; `g6_describe("solver")` serves this
skill. From Python: `from g6.solver import ConstraintSpec, SolverInput, solve`.

Composed:

- A runner may call any G6 component: a `code_construction` runner can draft with a model, a `solution_evaluation`
  runner can run `formal` or `algebra` and return its verdict; `solve` keeps that output but does not read it as a
  check: only a `ConstraintSpec` (or `LLMQualityChecker`) sets `outcome`.
- Give `SolverPlanner` and `LLMQualityChecker` a `reasoning.MeteredCaller` so a `Budget` caps their calls.
- To find out whether the 15-step run helps on your goals, compare it with `reasoning.DirectStep` through
  `reasoning.compare` at equal calls.

## Failure modes it catches

- `unchecked_answer` (an answer with skipped steps that nobody checked): `success` needs a deterministic named check
  that ran on the final answer and passed; a free-text constraint is refused before any step instead of being
  ignored; a planned step with no runner is `unavailable`, not done; and every step is in `steps` with its status, and
  every missing, failed or fallback step is also in `failures`.

- `unnoticed_drift`: named histories expose changes in G6's observed check outcomes. An exact upper bound below the
  threshold, or a significant fall from the previous window, blocks; a pass needs the exact lower bound at the
  threshold; insufficient samples or a wide interval require review. No history result is verified.

## Blind spots

- History measures satisfaction of the named checks, not correctness. Unchecked runs are counted but excluded from
  accuracy samples. Windows count runs, not days. The exact interval assumes independent runs with one pass rate;
  correlated runs make it too narrow. The decline flag compares only two adjacent windows and misses a slow fall
  within one. Thresholds need task-specific calibration.
- Process-local receipts prevent ordinary constructed, deserialised or edited outputs from entering history;
  they do not defend against arbitrary Python executing in the trusted process or a writer to the state database.

- A constraint checks the one property it names: "contains 4" is not "is right".
- A step is `completed` when its runner returned a non-empty value that is not a refusal, not when the value is
  correct.
- An `incomplete` run whose constraint passed is still `qualified-draft` (its card is `G6_E_SOLVER_INCOMPLETE` and
  names the steps that did not run): a reader of `completion_state` alone does not see that part of the plan never
  ran.
- The final answer is the first text under an answer key (`final_answer`, `value`, `result`, `answer`, ...) in a
  fixed step priority; a runner that puts other text there makes it the answer. With two or more different candidate
  answers, a planner model (if you supplied one) picks one; without one, the step priority does.
- When no priority step uses an answer key, the longest string any of those steps returned becomes the answer, such
  as a log line.
- The complexity classifier reads surface wording and misjudges reworded tasks.
- Only four constraint kinds exist; anything richer needs a checker you run on `final_answer` yourself, since a
  runner's verdict is not read as a check.

## What it needs

History uses SQLite under `state_path("solver", "history.db")`. Construction and reads create nothing.
Database and provenance failures return `G6_E_SOLVER_HISTORY_FAILED`; no silent empty-history fallback.

Nothing outside G6: it is Python on pydantic, which installs with g6. A model is optional and reached only through
the `g6.core.llm` caller you pass.

## Examples

A simple run whose constraint passes, the same run with no constraint (unchecked), a failing constraint, and a
free-text constraint refused before any step:

```python
from g6.solver import ConstraintSpec, SolverInput, solve

runners = {"goal_setting": lambda ctx: "add two numbers",
           "code_construction": lambda ctx: {"answer": "4"},
           "solution_execution": lambda ctx: {"log": "ran"},
           "solution_evaluation": lambda ctx: {"ok": True}}
says_4 = ConstraintSpec(raw="says 4", kind="contains", value="4")
out = solve(SolverInput(goal="What is 2 + 2?", constraints=[says_4]), runners)
assert (out.status, out.outcome, out.success, out.completion_state) == ("completed", "passed", True,
                                                                        "qualified-draft")
assert out.final_answer == "4" and out.complexity_detected == "simple"

unchecked = solve(SolverInput(goal="What is 2 + 2?"), runners)
assert unchecked.outcome == "unchecked" and not unchecked.success

wrong = solve(SolverInput(goal="What is 2 + 2?",
                          constraints=[ConstraintSpec(raw="says 5", kind="contains", value="5")]), runners)
assert wrong.completion_state == "blocked-escalated" and wrong.error_code == "G6_E_SOLVER_CHECK_FAILED"

refused = solve(SolverInput(goal="What is 2 + 2?", constraints=[ConstraintSpec(raw="be correct")]), runners)
assert refused.status == "refused" and refused.error_code == "G6_E_SOLVER_CONSTRAINT_UNEVALUABLE"
```

## The four open problems

- **Learning signals**: Opt-in history records checked outcomes with observed_by; it learns nothing or promotes nothing. `failures` (each with the
  operator action it needs) and `checks` are what a caller can record as labelled incidents.
- **Evaluators the model cannot influence**: the label is derived by code from visible fields; constraints are
  deterministic; the planner's model is never asked for a state, and a model's quality judgement can only lower the
  outcome. The runners are yours: a runner that grades its own work is not an outside check.
- **Transfer by failure mode**: the rule (no deterministic check, no success) holds for any goal. Not yet: no catch
  rate has been measured on task families.
- **Enough held-out cases**: Not yet: neither the step profiles nor the 15-step run's benefit over one call has been
  judged on held-out cases.
