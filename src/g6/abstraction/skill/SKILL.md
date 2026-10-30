---
name: g6-abstraction
description: Use when each evaluation in a black-box search is costly (a training run, a simulation, a benchmark), you hold a trace of configurations already evaluated with their scores, and you are about to spend more budget on blind trial and error; it fits a checked model of the score landscape to the trace, measures on the recorded trace what pruning would have saved or lost, and gives guarded advice on which fresh candidates cannot beat the incumbent.
---

# G6 abstraction

`g6.abstraction` is abstraction engineering for costly black-box search. An abstraction is a small validated program
(`Quotient`, `Project`, `Sufficient`, `Product`) fitted to the recorded trace of evaluated configurations, which bounds
the best score a region can hold. Around it: a property-based soundness gate, a counterfactual replay of the trace,
a permutation stress test, a description-length score, counterexample-guided refinement, a library with named
statuses, and pruning advice that every guard can only switch off.

## When to use it

- Each evaluation costs real time or money and you have a trace (CSV, JSON or rows) of configurations and scores.
- You are about to evaluate the next batch of candidates blind and want to know which ones the evidence says cannot
  beat the current best.
- You want to know whether a pruning rule would have discarded the optimum on the data you already have.

## When not to use it

- The problem has a known model (linear, integer, constraint, routing, scheduling): `optimisation` solves it and
  checks the answer.
- Logic constraints: `formal`. Filling holes in a code sketch: `cegis`.
- You want a standard optimiser's next suggestion: this package does not propose configurations, and no benefit over
  standard optimisers has been established.
- Fewer than about 30 evaluated points: `diagnose` warns `LOW_DATA`, and the gate needs at least 4.

## Python API

- `ConcreteDomain`: the search space and the trace, in evaluation order (`from_rows`, `from_csv`, `from_json`,
  `append`); the objective is data and is never computed.
- `ParamSpec`: one dimension: `float`, `int` or `categorical`.
- `Record`: one evaluated point; a NaN or infinite objective is refused.
- `Quotient`: similarity balls around visited points; bound = best nearby score + `lipschitz` x `radius`.
- `Project`: projection onto some dimensions, split into bins.
- `Sufficient`: per-arm statistics on one dimension; guidance only, never prunes.
- `Product`: two abstractions together; the bound is the smaller of the two.
- `from_spec`: an abstraction from a JSON spec, validated against hard limits.
- `SpecError`: a spec outside those limits.
- `run_gate`: property-based checks (extensivity, monotonicity, prospective bound soundness) with counterexamples and
  calibration; an aborted check counts as not passed.
- `replay`: counterfactual replay of the recorded trace: evaluations saved, the incumbent with and without pruning,
  and regret events (a pruned point that beat the incumbent).
- `bootstrap_replay`: replay over permutations of the trace; any regret on any ordering means not robustly sound.
- `RobustReport`: the benefits, `total_regret_events`, `sound_robust`, `valuable` and `lcb`.
- `mdl_score`: a heuristic description-length score.
- `find_counterexamples`: soundness and precision counterexamples.
- `refine_until`: refines toward soundness on counterexamples, within an iteration cap.
- `tighten_until`: the reverse for a sound abstraction whose bounds never bind.
- `TightenResult`: the spec before and after, and whether it tightened.
- `Library`: evaluates an abstraction and stores a `LibraryEntry` with a status; `save` and `load` a JSON file you
  name. Entries loaded from a file never authorise pruning until re-evaluated with
  `Library.evaluate(..., force=True)` (the server's `revalidate`); a plain `evaluate` returns the stored entry. A
  file written when the top status was called `verified` loads as `checked`.
- `LibraryEntry`: the spec, the gate, replay, robustness and description-length reports, `status` and `trace_len`.
- `diagnose`: findings about the library's own decisions (`INERT_MECHANISM`, `BOUND_MISCALIBRATED`, `STALE_ENTRY`,
  `WINNERS_CURSE`, `NO_CHECKED`, `LOW_DATA`, ...), each with a remediation.
- `Diagnosis`: the findings and a summary.
- `Finding`: one finding's severity, code, subject, detail and remediation.
- `lift`: applies a trace-generic abstraction to the search over abstractions itself.
- `liftable`: whether an abstraction can be lifted.
- `meta_domain`: the library's entries as a domain of their own (at least 4).
- `pruning.advise_pruning`: prune or no-prune advice per candidate, with the guards that switched pruning off.
- `g6_adapter.surface_state`: an entry status mapped down to a G6 envelope state.
- `g6_adapter.attach_abstraction_envelope`: an entry wrapped in an envelope with `human_review_required` always true,
  and the `request_id` and `run_id` you pass.

## Reading the result

`Library.evaluate` gives each entry one status: `rejected` (the gate failed, or a regret on any ordering), `degenerate`
(a single cell), `provisional` (robustly sound, value not shown), or the module's own top status, `checked`: robustly
sound on the recorded trace with a positive lower order statistic of benefit over permutations, adjusted for the
number of candidates tried. It is not a G6 reliability state. Read entries through the adapter, which maps `checked`
and `provisional` down to `qualified-draft`, and everything else (an unknown status, the old `verified` included) to
`blocked-escalated`, with the warning code `G6_E_ABSTRACTION_REVIEW_REQUIRED`. `advise_pruning` may say prune only for
a `checked` entry that was evaluated in this session, is not stale (the trace has not doubled since), and for a
candidate in range and within 0.25 of the data.

## Alone and composed

It has no block, so over MCP `g6_invoke("abstraction", ...)` answers `G6_E_NO_BLOCK`; `g6_describe("abstraction")`
serves this skill. From Python: `from g6.abstraction import ConcreteDomain, Library, Quotient, replay, run_gate`. A
separate stdio server, `python -P -m g6.abstraction.server`, offers these steps as tools of its own. Its file tools,
`load_dataset`, `save_state` and `load_state`, are refused (`G6_E_SENSITIVE_COMPONENT_DENIED`) unless the person who
runs the server lists them (`abstraction.save_state`, ...) or `abstraction` in `G6_MCP_SENSITIVE`, and every path
must lie inside the directory that person names in `ABSTRACTION_MCP_DIR` (`G6_E_ABSTRACTION_PATH_REFUSED`
otherwise). The agent can set neither.

Composed: it imports no other G6 component. You run the evaluations and `append` each result to the domain. To claim
that abstraction-guided search beats your current search, run both as `reasoning.compare` arms on held-out problems at
equal evaluation budgets.

## Failure modes it catches

- `blind_trial_and_error` (spending a budget on blind trial and error): it fits a model of the landscape from the
  points already evaluated, checks that its bounds held on that trace before it may prune, measures the evaluations a
  rule would have saved and the optima it would have lost, and lets a candidate be skipped only when every guard
  agrees.

## Blind spots

- Not established: that pruning never discards a better configuration unlike the ones in the trace, calibrated
  confidence, or a benefit over standard optimisers.
- Soundness is empirical, on the recorded trace; a `lipschitz` constant that is too small for the true landscape
  makes bounds wrong elsewhere.
- `lcb` is a low order statistic over re-orderings of one trace, an order-sensitivity test, not a confidence bound.
- The gate runs Hypothesis, which may keep a `.hypothesis` cache folder in the working directory.

## What it needs

`pip install "g6[abstraction] @ git+https://github.com/basicisabc1994/g6solver"` for Hypothesis and NumPy; without them `import g6.abstraction` still works, and each
public name raises an `ImportError` that says what to install. The separate stdio server also needs the MCP extra
(`pip install "g6[abstraction,mcp] @ git+https://github.com/basicisabc1994/g6solver"`); with `ABSTRACTION_MCP_STATE` set to a file inside `ABSTRACTION_MCP_DIR` it
loads its state from that file at start and saves to it as its tools change the state, and it will not start when
that file is outside the directory. Nothing else: no outside program, no model.

## Examples

A toy trace of 40 points: an honest abstraction passes the gate, a reckless one fails it and loses the optimum in
replay, the library rejects it, and the adapter's state follows:

```python
from g6.abstraction import ConcreteDomain, Library, Quotient, replay, run_gate
from g6.abstraction.g6_adapter import attach_abstraction_envelope
from g6.abstraction.pruning import advise_pruning

order = [(7 * i) % 40 for i in range(40)]
rows = [{"x": k / 39, "score": -(k / 39 - 0.3) ** 2} for k in order]
domain = ConcreteDomain.from_rows(rows, objective="score", maximise=True, name="toy")

honest = Quotient(radius=0.1, lipschitz=2.0)
assert run_gate(honest, domain).passed

reckless = Quotient(radius=0.5, lipschitz=0.0)
assert not run_gate(reckless, domain).checks["bound_soundness"]
assert replay(reckless, domain, budget=20).regret_events  # it would have pruned better points

library = Library(k_permutations=4)
rejected = library.evaluate(reckless, domain, budget=20)
assert rejected.status == "rejected"
assert attach_abstraction_envelope("evaluate", rejected)["completion_state"] == "blocked-escalated"

kept = library.evaluate(honest, domain, budget=20)
envelope = attach_abstraction_envelope("evaluate", kept, request_id="r1")
assert kept.status == "checked" and envelope["completion_state"] == "qualified-draft"
assert envelope["human_review_required"] is True and envelope["request_id"] == "r1"
advice = advise_pruning(library, kept.entry_id, domain, [{"x": 0.31}, {"x": 2.0}])
assert advice["candidates"][0]["prune"] is False  # next to the optimum
assert advice["candidates"][1]["prune"] is False and advice["candidates"][1]["out_of_range"] == ["x"]
```

## The four open problems

- **Learning signals**: inside its own loop it learns only from evaluated points (replay never learns from a point it
  skipped), and regret events and counterexamples are labelled with their configurations. Not yet: nothing is written
  to a run ledger.
- **Evaluators the model cannot influence**: the objective is data, never computed by an abstraction; the gate, replay
  and permutation test are fixed code; a proposer (a model, say) only supplies specs, which `from_spec` validates.
  Re-evaluating the same spec on the same trace returns the existing entry, and every new candidate tried on a domain
  makes the value test stricter.
- **Transfer by failure mode**: `Library.promotable` lists the combinator kinds at the top status on at least two
  distinct domains, and `lift` reuses trace-generic mechanisms one level up. Not yet: no catch rate has been measured
  on task families they were not fitted on.
- **Enough held-out cases**: the value claim is Bonferroni-adjusted by the number of candidates tried on the domain,
  and `diagnose` warns below 30 records. Not yet: there is no held-out trace and no sequential test; the permutations
  reuse the same observations.
