---
name: g6-decision-making
description: Use when an agent must choose what to try next (which experiment, which arm, which action in a small known model) or how many times to retry each task within a budget, and the choice must be a standard rule with its working shown and the same answer on every run, rather than an ad hoc guess.
---

# G6 decision_making

Standard decision rules over the inputs you give: value of information, expected utility, Thompson sampling and
UCB1, value iteration on a finite MDP, the next experiment within a budget, and retries within a budget. Inputs are
validated fail closed and never normalised; every random draw comes from your seed; budgets only tighten. Every answer
carries its working.

## When to use it

- Choosing the next experiment or test: `plan_next_action` picks the affordable one with the most expected
  information gain, or says stop.
- Deciding how many retries each task gets: `retry_budget` shares a budget out by what one more attempt is worth.
- Choosing among options with uncertain payoffs (`bandit_select`), valuing information before acting (EVPI,
  information gain, expected improvement), or solving a small known MDP (`value_iteration`).

## When not to use it

- When the probabilities are unknown: they decide everything, and the block does not estimate them. Measure or
  elicit them first.
- For large or continuous problems: tables are bounded (1,000 states, 100,000 transition rows).
- As an optimiser over a model you cannot write down: use `UCT` from Python with a generative model.

## Ops

- `expected_value_of_perfect_information`: EVPI of knowing the state before acting (`prior` state to probability, `utilities` action to state to utility).
- `expected_information_gain`: the bits a binary test can change a binary belief by; a test that cannot refute scores 0.
- `expected_improvement`: the Gaussian expected improvement of a candidate `N(mean, std^2)` over the incumbent `best`.
- `expected_utility`: `E[u]` over `[[outcome, probability], ...]` with the linear utility `scale * x + offset`.
- `value_iteration`: the exact values and greedy policy of a finite MDP; refused, never approximated, when it has not converged.
- `plan_next_action`: the affordable experiment with the most expected information gain, or STOP.
- `bandit_select`: the next arm from UCB1 or seeded Thompson sampling after replaying `[[arm, reward], ...]`.
- `retry_budget`: how many retries each task gets within `budget`, granted one at a time by the value of each task's next retry.

## The rules

- `plan_next_action` picks by gain alone among the affordable candidates; cost enters only through `budget` and the
  stopping rule (stop when gain less `cost_weight` times cost is below `min_net_gain`), so a much cheaper experiment
  with slightly less gain loses.
- `retry_budget`: a retry follows a failure, so retry `r` succeeds with the Beta-Bernoulli chance
  `(prior_alpha + successes) / (prior_alpha + prior_beta + successes + failures + r - 1)`; its net value is that
  chance times `value` less `cost` (same units). Retries go one at a time to the task whose next retry is worth most
  (ties by declared order), paid from `budget`, until the best is below `min_net_gain` (0), nothing is affordable or
  every task has `max_retries` (10, at most 100). The `successes` and `failures` you give are your record, not
  checked.
- `value_iteration`: `discount` in [0, 1); refused with `cap` `max_iterations` (10,000) or `time_limit_s` (10, at most
  60) when the values have not converged below `tolerance`. A non-finite Q-value, residual or value is refused
  before convergence can be claimed, even when all inputs were finite; no values or policy are returned.

## Reading the result

`result` holds the op's answer: a number, `{values, policy}`, a decision with its `rationale` (each candidate's gain,
net gain, whether it is affordable, the budget left and the stop reason), `{algorithm, arm}` (`arm` null when every
arm is masked), or `{retries, grants, stop_reason, budget_spent, budget_remaining, rule}`. The code is
`G6_E_DECISION_COMPUTED`, `qualified-draft`. Refusals are `blocked-escalated`: `G6_E_DECISION_INPUT` (a malformed
distribution, a negative or non-finite cost, a duplicate arm or id, a missing utility, an unknown next state) and
`G6_E_DECISION_CAP`, or `G6_E_DECISION_NUMERIC` (arithmetic exceeded the finite numeric range; rescale rewards).

## Alone and composed

Alone: call an op with its inputs. Composed: `retry_budget` sets how many times an agent may retry a failing step
before `g6.action_gating` decides on it; `plan_next_action` orders the checks a search runs; `g6.abstraction`'s search
can use `bandit_select` to choose among strategies. From Python: `UCT(model, seed, playouts, horizon, time_limit_s)`
plans over a generative model (playouts at most 100,000, horizon at most 1,000); `record_decision` appends a decision
to a ledger you hold; `BoundedAllocator` and `BudgetedBandit` keep spending under a cap.

## Failure modes it catches

- `ad_hoc_choice`: ad hoc, unrepeatable choices about what to try next. Each choice is a standard rule over stated
  inputs, returned with its working; the same seed and inputs give the same answer in every call and process.

## Blind spots

- The inputs decide the answer: a wrong prior, rate or cost gives a confident wrong choice. The block cannot tell.
- Thompson sampling replays history into a fresh posterior on each call; a reward is read as success when it is
  finite and non-zero. NaN and either infinity are refused with `G6_E_DECISION_INPUT`; 0 remains failure and 1 success.
- Information gain is for binary tests of binary hypotheses only.

## What it needs

Nothing outside G6: no key, no outside program, no extra, no network. No state is kept.

## Examples

```g6-example op=expected_value_of_perfect_information
{"prior": {"rain": 0.3, "dry": 0.7}, "utilities": {"umbrella": {"rain": 5, "dry": 3}, "none": {"rain": 0, "dry": 6}}}
```

```g6-example op=expected_information_gain
{"prior": 0.5, "true_positive_rate": 0.9, "false_positive_rate": 0.1}
```

```g6-example op=expected_improvement
{"mean": 1.2, "std": 0.5, "best": 1.0}
```

```g6-example op=expected_utility
{"outcomes": [[10, 0.25], [2, 0.75]], "scale": 2, "offset": 1}
```

```g6-example op=value_iteration
{"states": ["s", "goal"], "terminal": ["goal"], "discount": 0.9, "transitions": {"s": {"stay": [[1.0, "s", 0.0]], "go": [[1.0, "goal", 1.0]]}}}
```

```g6-example op=plan_next_action
{"budget": 5, "candidates": [{"candidate_id": "sharp", "prior": 0.5, "true_positive_rate": 1, "false_positive_rate": 0, "cost": 2}, {"candidate_id": "weak", "prior": 0.5, "true_positive_rate": 0.6, "false_positive_rate": 0.4, "cost": 1}]}
```

```g6-example op=bandit_select
{"algorithm": "thompson", "arms": ["a", "b"], "history": [["a", 1], ["b", 0]], "seed": 7}
```

```g6-example op=retry_budget
{"budget": 10, "tasks": [{"task_id": "deploy", "value": 10, "cost": 2, "failures": 1}, {"task_id": "lint", "value": 4, "cost": 1}]}
```

```python
from g6.cogarch.decision_making import UCT

class Corridor:  # step right to the end for a reward of 1
    def actions(self, state):
        return ["left", "right"]
    def step(self, state, action, rng):
        nxt = min(state + 1, 3) if action == "right" else max(state - 1, 0)
        return nxt, 1.0 if nxt == 3 else 0.0
    def is_terminal(self, state):
        return state == 3

assert UCT(Corridor(), seed=1, playouts=200, horizon=5).plan(0) == "right"
```

## The four open problems

- **Learning signals**: the history and records you pass (rewards per arm, successes and failures per task); the
  block keeps none of them.
- **Evaluators the model cannot influence**: the formulas themselves over validated inputs; no model takes part.
- **Transfer by failure mode**: bound to `ad_hoc_choice`; the same rules serve any domain whose probabilities are
  stated.
- **Enough held-out cases**: Not yet: the tests are known values worked by hand, not a record of real choices.
