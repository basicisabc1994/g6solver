---
name: g6-optimisation
description: Use when you are about to propose a schedule, allocation, assignment, route, flow, packing or production plan that must keep stated rules, call a plan the best, cheapest or fastest, or tune a non-linear formula's inputs by trial and error, and the numbers come from your own working rather than a solver.
---

# G6 optimisation

Solves linear, integer, constraint, routing, flow and scheduling problems with OR-Tools, in process, and checks every
answer in pure Python before it is reported. Where optimality can be shown without trusting the solver, it is; where
it cannot, the reply says how far the answer may be from the best. A non-linear objective over a box is searched with
SciPy: a local method from several starts, or a global search, the answer re-evaluated by G6 and never called optimal.

## When to use it

- You are about to hand someone a timetable, a rota, a delivery route or a resource split.
- A plan must respect hard limits (capacities, budgets, machine order, time windows) and one broken limit matters.
- A draft says "optimal", "cheapest", "minimal" or "the best we can do".
- You need shadow prices (duals) or reduced costs from a linear programme.
- You need the lowest or highest value of a non-linear formula over bounded inputs, continuous or whole numbers:
  `minimise` or `global_search`, not a guess from a few tries.

## When not to use it

- Whether a set of logical constraints can hold at all, or a counterexample to a rule: use `formal`.
- A formula to simplify, differentiate or solve: use `algebra`.
- Deciding whether an agent may take an action: use `action_gating`.
- Checking a plan of G6 op calls before it runs: use `g6.navigator`'s `check_plan`.

## Ops

- `solve_lp`: a linear programme on GLOP; an optimum is shown by its duals, and no optimum is shown infeasible
  (least-violation duals) or unbounded (a feasible point and an improving ray) when G6 can show it; otherwise the
  reason says the solver's word is unchecked.
- `solve_mip`: a mixed-integer programme on SCIP; the answer checked, with the solver's bound and gap.
- `solve_cp`: a constraint model on CP-SAT (integer variables with finite bounds, linear constraints, `all_different`
  groups, an optional objective), checked exactly.
- `solve_knapsack`: the most valuable items that fit, any number of weight dimensions, checked.
- `solve_job_shop`: each job's tasks in order, one task at a time per machine, the makespan minimised and checked.
- `solve_assignment`: rows paired with columns at least cost (`costs`, `null` for a forbidden pair); optimality shown
  by the residual network.
- `solve_max_flow`: the largest flow from `source` to `sink`, shown maximal by a cut of the same capacity.
- `solve_min_cost_flow`: every supply met at least cost; optimality shown by the residual network.
- `solve_routing`: routes that visit every node once (`distances`, `vehicles`, `depot`, optional `demands` and
  `capacities`, `time_windows`, `travel_times`), checked, never called optimal without proof.
- `minimise`: a SciPy local method (`method`: `L-BFGS-B`, `Nelder-Mead` or `Powell`) from `starts` starting points
  (the first at `x0` if given, the rest drawn with `seed`) over continuous `variables`; the answer is a local minimum,
  with the number of different minima the starts found.
- `global_search`: differential evolution over the box, integer variables included (or a `backend` registered in
  Python); the answer is the best point found.

## Reading the result

Inputs are declarative data: a linear expression is `{variable: coefficient}`, a constraint `{"name", "terms", "lb",
"ub"}`, a network a list of arcs `{"from", "to", "capacity", "cost"}`. `sense` is `minimise` or `maximise`;
`time_limit` is at most 60 seconds. The reply gives `objective`, `solution`, `optimal`, `bound`, `gap`, `certificate`
and `check` (what was checked and any failures).

- `qualified-draft`, `G6_E_OPTIM_SOLVER_ANSWER`: an answer that passed the check. The best state; nothing here is
  `verified`.
- `qualified-draft`, `G6_E_OPTIM_NOT_PROVEN_OPTIMAL`: feasible and checked, but not proven best; the card names the
  gap, or says there is no bound.
- `blocked-escalated`: `G6_E_OPTIM_INFEASIBLE` and `G6_E_OPTIM_UNBOUNDED` (shown by G6 for a linear programme when
  it can; otherwise, and for `solve_mip` and `solve_cp`, the reason says it rests on the solver's own proof),
  `G6_E_OPTIM_TIME_LIMIT` with no answer, `G6_E_OPTIM_CERTIFICATE_REFUTED` (the check contradicted the solver),
  `G6_E_OPTIM_MODEL_INVALID`.

A draft's code is on `warning_card.code`; the result's own `code` is empty unless it is blocked.

For `minimise` and `global_search`, every variable needs finite bounds (`lb` is 0 unless given), and `expression` is a
JSON tree, never code: a number, a variable's name, or `[operation, argument, ...]` with `+`, `*`, `min`, `max`,
`-`, `/`, `^`, `neg`, `abs`, `sqrt`, `exp`, `log`, `sin`, `cos`, `tan`, `tanh`, `atan`, and `["table", [c0, c1,
...], i]` for a choice an integer variable makes; at most 2,000 nodes, 50 deep, 50 variables. The point is
re-evaluated by G6's own interpreter and its bounds checked; `minimise` also checks the projected gradient and nearby
points. The search stops at `max_evaluations` (default 20,000) or `time_limit`; `solution.converged` and
`evidence[0].search` say how far it got.

- `qualified-draft`: `G6_E_OPTIM_LOCAL_OPTIMUM` (a local minimum; the card says whether the starts disagreed),
  `G6_E_OPTIM_BEST_FOUND` (the best point a global search found, converged by its own test) or
  `G6_E_OPTIM_NOT_CONVERGED` (not shown to be a minimum: stopped early, or a better point beside it).
- `blocked-escalated`: `G6_E_OPTIM_INPUT_INVALID` (an expression outside the language, a missing bound),
  `G6_E_OPTIM_INPUT_TOO_LARGE`, `G6_E_OPTIM_CERTIFICATE_REFUTED` (the search misreported its value, or left the box),
  `G6_E_OPTIM_NO_SOLUTION`, `G6_E_OPTIM_SCIPY_UNAVAILABLE`.

## Alone and composed

Alone, over MCP: `g6_invoke("optimisation", "solve_lp", params_json)`. From Python:
`from g6.optimisation import solve_lp, solve_mip, solve_routing, solve_max_flow, minimise, global_search`;
`register_backend(name, run)` adds a `global_search` backend, whose answers are re-checked like any other.

Composed:

- Use `formal`'s `unsat_core` on the same rules written as SMT-LIB to find which ones conflict when a model here comes
  back infeasible.
- Give `coding_agent_gate`'s `verify_answer` the reply as an evidence item of kind `log` before reporting a plan as
  done (it counts as caller-declared); an outward action the plan triggers goes through `action_gating` first.

## Failure modes it catches

- `rule_breaking_plan` (a plan that breaks one of your rules): every answer is checked against the model you sent
  before it is reported: each constraint and bound, capacities and flow conservation, machine order and overlaps,
  every node visited once, loads and time windows. A violation blocks it with `G6_E_OPTIM_CERTIFICATE_REFUTED`.
- `unproven_optimum` (a plan called the best when nobody proved it): where it can, G6 shows optimality without
  trusting the solver (duals for a linear programme, an equal cut for a maximum flow, no negative cycle in the
  residual network for an assignment or a minimum-cost flow). Elsewhere `optimal` is true only when the solver proves
  it, with its bound and gap closed; a feasible answer short of that is `G6_E_OPTIM_NOT_PROVEN_OPTIMAL`, naming the
  gap or saying there is no bound.
- `wrong_optimiser` (a poor solution from the wrong optimisation method): `minimise` calls a local method's answer
  local and runs several starts, saying when they found different minima and pointing to `global_search`; a search
  stopped by its limits says it did not converge; and every point is re-evaluated, so a method that misreports is
  refused.

## Blind spots

- The model is yours. A rule you left out is a rule nobody checks, and a wrong coefficient gives a wrong plan that
  passes.
- An integer or constraint optimum rests on the solver's bound, which G6 does not re-derive, and an integer or
  constraint model called infeasible or unbounded rests on the solver's proof, which G6 does not re-check.
- Continuous values are compared within a small tolerance scaled to the numbers, not exactly.
- A hard problem may hit the time limit: with an answer, it is not proven optimal; with none, it is blocked.
- No stochastic models, and no sensitivity analysis beyond GLOP's duals and reduced costs. Non-linear objectives are
  searched only within bounds, with no other constraints, and never proven optimal: differential evolution can pass
  its own test in a local basin (run another `seed`), and the gradient check uses finite differences. Around a local
  answer a few dozen points are sampled; with `abs`, `min`, `max` or a table, SciPy's flag alone backs the word minimum.

## What it needs

- `pip install "g6[optim]"` for OR-Tools, imported only when an op runs. Without it every solving op is
  `blocked-escalated` with `G6_E_OPTIM_SOLVER_UNAVAILABLE` and the install line.
- `pip install "g6[optim-nonlinear]"` for SciPy, used by `minimise` and `global_search`; without it they are
  `blocked-escalated` with `G6_E_OPTIM_SCIPY_UNAVAILABLE` and the install line.
- No outside program. No op runs code, writes a file or reaches the network, so none is sensitive.

## Examples

```g6-example op=solve_lp
{"variables": {"doors": {}, "windows": {}}, "constraints": [{"name": "plant1", "terms": {"doors": 1}, "ub": 4}, {"name": "plant2", "terms": {"windows": 2}, "ub": 12}, {"name": "plant3", "terms": {"doors": 3, "windows": 2}, "ub": 18}], "objective": {"doors": 3, "windows": 5}, "sense": "maximise"}
```

`objective` is 36 with 2 doors and 6 windows, `optimal` is true, and `certificate.duals` with `check.certificate`
`duals` show it without trusting the solver.

```g6-example op=solve_routing
{"distances": [[0, 2, 9, 10], [2, 0, 6, 4], [9, 6, 0, 8], [10, 4, 8, 0]], "time_limit": 1}
```

A route that visits every node once, checked, but `qualified-draft` with `G6_E_OPTIM_NOT_PROVEN_OPTIMAL`: the routing
library gives no bound, so the reply says a better route may exist.

```g6-example op=minimise
{"variables": {"x": {"lb": -2, "ub": 2}}, "expression": ["+", ["^", ["-", ["^", "x", 2], 1], 2], ["*", 0.3, "x"]], "x0": {"x": 1.0}, "starts": 6, "seed": 3}
```

Two wells: the start at `x0` finds the shallow one near 0.96, other starts the deeper one near -1.04. The reply is
`G6_E_OPTIM_LOCAL_OPTIMUM` at x near -1.04, and its card says the starts found 2 different minima.

```g6-example op=global_search
{"variables": {"a": {"ub": 10, "integer": true}, "b": {"ub": 10, "integer": true}}, "expression": ["+", ["^", ["-", ["*", "a", "b"], 12], 2], ["^", ["-", ["+", "a", "b"], 7], 2]]}
```

Two whole numbers whose product is 12 and sum is 7: 3 and 4, objective 0, `G6_E_OPTIM_BEST_FOUND`.

## The four open problems

- **Learning signals.** Not yet: it writes nothing to a run ledger itself. Each catch is a labelled result with its
  own `G6_E_OPTIM_*` code and the failed check in `evidence`; recording it is the caller's step today.
- **Evaluators the model cannot influence.** The checks are the package's own pure-Python code, recomputing every
  constraint and objective from the model you sent; no model call runs anywhere in the package. The caller supplies
  the model, never the check.
- **Transfer by failure mode.** The checks belong to the problem class, not to a task: a dual certificate or an equal
  cut shows optimality for any instance. Not yet: its catch rate on task families it was not built on has not been
  measured.
- **Enough held-out cases.** Not yet: its own catch rate and false-alarm rate are not judged by a sequential test. A
  repeated workflow that uses it can be tuned with `g6.metaharness`, whose gate judges a change on held-out cases.
