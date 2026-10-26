---
name: g6-reasoning
description: Use when an agent runs a reasoning method that makes several model calls (sampling, voting, self-consistency, a prompt chain, a debate) and needs hard ceilings on calls, output and time, a vote that does not mistake a plurality for a consensus, or an honest test of whether the method beats one direct call at the same compute.
---

# G6 reasoning

`g6.reasoning` holds what any multi-call reasoning method needs to be judged fairly: a model caller that counts every
call against hard ceilings, a weighted vote that says whether a real majority exists, the one-call and n-call
baselines, and a paired comparison gate that will not promote a method on noise or on extra compute. Models are reached
only through the `g6.core.llm` caller you inject.

## When to use it

- You are about to loop over model calls and want a ceiling nothing can forget: wrap the caller in `MeteredCaller`.
- You sampled several answers and are about to report the most common one as "the answer".
- You built a prompt chain, a debate or a multi-step method and are about to say it is better than one call.
- You need the JSON object out of a model reply wrapped in prose or LaTeX.

## When not to use it

- Ceilings on a whole agent run (actions, test weakening) belong to `safety_engine`.
- Tuning a workflow you repeat, on held-out cases, with a person approving the change: use `metaharness` (it already
  runs on this package's budget and sign test).
- Crediting a prompt hint learned from failures: use `learning` (`compare_arms`, paired runs with and without it).
- An answer that must be exact goes to a checker (`formal`, `algebra`, `optimisation`). A vote over samples of one
  model is not a check.

## Python API

- `metrics.evaluate`: recompute accuracy, exact match, MSE, MAE, RMSE and macro/micro/weighted precision, recall
  and F1 against labels. Supply a threshold for each metric; direction is declared per metric. Invalid numbers,
  lengths or undefined denominators refuse with `G6_E_METRIC_INPUT`, never a perfect zero loss.
- `metrics.exact_interval`: exact two-sided binomial tail inversion for accuracy or exact-match counts; reports
  sample size, assumes independent Bernoulli trials. Other metrics have no exact interval here.

- `structure_map`: compare directed graphs given as `{"nodes":{"id":"label"},"edges":[["id","id","label"]]}`.
  Exact node and edge labels, at most seven nodes, no parallel edges. Returns the object mapping, unmapped source
  relations and `isomorphic`, `partial(mapped=k/n)` or `no_consistent_mapping`; always draft-only.

- `Budget`: the ceilings `max_model_calls` (32), `max_output_chars` (400,000) and `max_wall_clock_s` (120).
- `MeteredCaller`: wraps an `LLMCaller`; counts every attempt, failed ones and an inner caller's retries included;
  refuses once a ceiling is hit, and refuses a reply that arrives after the time limit (still charged).
- `BudgetExhausted`: raised with `G6_E_BUDGET_EXHAUSTED` and the ceiling's name.
- `weighted_vote`: groups equivalent answers; when any candidate is verified, unverified ones are not counted;
  empty answers are abstentions that count against a majority.
- `Candidate`: an answer, a `weight` and a `verified` flag you set only when an independent check accepted it.
- `VoteResult`: `winner` (None on a tie), `has_majority`, `abstained`, `groups`.
- `DirectStep`: one model call per problem, the baseline every multi-call method must beat; a failed call or an
  unusable reply is `""`.
- `self_consistency`: `n` direct answers and a plain vote, the compute-matched baseline for `n` calls.
- `compare`: runs a baseline and a candidate `Arm` on the same `Case`s and returns a `Verdict`.
- `Arm`: a name and `run(problem) -> (answer, model_calls_used)`.
- `Case`: a problem and `score(answer)` in [0, 1], written by the case author, never by the arms.
- `Verdict`: `promoted`, `reason`, `wins`, `losses`, `ties`, `p_value`, mean scores and calls per case,
  `compute_matched`, `failures`.
- `sign_test_p`: the one-sided exact sign-test p-value `compare` uses, from wins and losses.
- `first_json_object`: the first balanced JSON object in a reply, LaTeX backslashes kept; `ValueError` when none.

## Reading the result

`Verdict.reason` is one of: `insufficient_cases` (fewer scored cases than `min_cases`, 30 by default),
`no_improvement`, `within_noise` (the one-sided exact sign test p is not below `alpha`, 0.05), `not_compute_matched`
(the candidate used more calls per case than the baseline), `beats_baseline`, or `beats_baseline_with_more_compute`
(only when you pass `matched=False`, and then `compute_matched` is False). A crashing arm or scorer is counted in
`failures`, never hidden. A `VoteResult` without `has_majority` is a plurality; report it as one.

## Alone and composed

It has no block, so over MCP `g6_invoke("reasoning", ...)` answers `G6_E_NO_BLOCK`; `g6_describe("reasoning")`
serves this skill. From Python: `from g6.reasoning import Budget, MeteredCaller, compare, self_consistency`.

Composed:

- `metaharness` runs each workflow's model calls through `Budget` and `MeteredCaller` and judges candidates with this
  package's exact sign test.
- `debate` sends you here: put `run_debate` inside an `Arm` that returns its call count, and compare it with
  `self_consistency` at the same number of calls.
- Wrap the model inside each `Arm` in a `MeteredCaller` and return its `calls`, so the count `compare` reads is
  counted, not claimed.
- `g6.bundles` and `metaharness` parse model replies with `first_json_object`.

## Failure modes it catches

- `impression_not_measurement`: computed scores, per-metric directions and thresholds; a single correct answer
  still has a wide exact interval. Use `compare` on paired cases for baseline claims. The ML check bundle shares
  these metrics, exact accuracy intervals and paired gate. Numeric cases use utility `1/(1+absolute_error)`, and
  the model's whole MAE interval must also sit below the mean's MAE, so a few huge misses cannot hide behind many
  small wins; other intervals remain seeded bootstrap estimates. Scores cover at most 100,000 values
  (`metrics.MAX_VALUES`). No imported score or benchmark store is added.

- `false_analogy`: labelled VF2 checks relations and direction, never similarity between object names. Missing
  NetworkX or a failed check blocks with `G6_E_GRAPH_NOT_INSTALLED` or `G6_E_GRAPH_CHECK_FAILED`.

- `confounded_gain` (a claimed gain that extra compute or luck explains): `compare` refuses below `min_cases` scored
  cases (30 by default),
  needs more wins than losses with an exact sign test below `alpha` and a mean gain above `min_margin`, and refuses a
  candidate that used more model calls per case than the baseline unless you declare it. `MeteredCaller` makes the
  call counts real, and `self_consistency` gives the baseline at equal calls.

## Blind spots

- `compare` is only as good as `Case.score`: a scorer that rewards the wrong thing promotes the wrong thing.
- The call count an `Arm` returns is its own word unless it comes from a `MeteredCaller`.
- A candidate tuned on the same cases it is compared on can win by overfitting; keep the cases held out.
- The sign test counts wins and losses, not their size, and `min_margin` defaults to 0.
- A call in flight cannot be interrupted: a slow call is charged, then refused after it returns.
- `weighted_vote` groups by normalised text (case, spaces, final punctuation) unless you pass `equivalent`: "4" and
  "four" are two answers.
- `Candidate.verified` is the caller's flag; nothing here checks it.
- Nothing here says a multi-call method is worth its cost: no stepper is shipped, because none was shown to beat one
  call.

## What it needs

Structure comparison alone needs `g6[graph]` (NetworkX, BSD-3-Clause), imported only when called. Supplied graphs
can omit important relations; a partial mapping establishes only its listed subgraph, never domain equivalence.

Other functions use the standard library. Model calls go only through the `g6.core.llm` caller you
pass (`FakeLLM` in tests).

## Examples

A budget that stops the fourth call, a vote that a candidate flagged `verified` wins, and the comparison gate refusing
a small sample and a candidate that used more calls:

```python
from g6.core.llm import FakeLLM
from g6.reasoning import (Arm, Budget, BudgetExhausted, Candidate, Case, MeteredCaller, compare,
                          self_consistency, weighted_vote)

llm = MeteredCaller(FakeLLM(default='{"answer": "4"}'), Budget(max_model_calls=3))
vote = self_consistency(llm, "What is 2 + 2?", 3)
assert vote.winner == "4" and vote.has_majority
assert llm.calls == 3 and llm.check() == "max_model_calls"
try:
    llm.complete("system", "one call too many")
    raise AssertionError("the budget lets no fourth call reach the model")
except BudgetExhausted:
    pass

assert weighted_vote([Candidate("5"), Candidate("5"), Candidate("4", verified=True)]).winner == "4"
assert not weighted_vote([Candidate("4"), Candidate(""), Candidate("")]).has_majority  # one survivor of three

cases = [Case(f"case-{i}", lambda answer: 1.0 if answer == "ok" else 0.0) for i in range(30)]
base = Arm("baseline", lambda problem: ("no", 1))
better = compare(base, Arm("candidate", lambda problem: ("ok", 1)), cases)
assert better.promoted and better.reason == "beats_baseline"
assert compare(base, Arm("candidate", lambda problem: ("ok", 1)), cases[:10]).reason == "insufficient_cases"
assert compare(base, Arm("candidate", lambda problem: ("ok", 8)), cases).reason == "not_compute_matched"
```

## The four open problems

- **Learning signals**: Not yet: it writes nothing to a run ledger. A `Verdict` (its `reason` and `failures`) and a
  `BudgetExhausted` carry what a caller can record as a labelled incident.
- **Evaluators the model cannot influence**: the decision in `compare` is fixed code (an exact sign test and call
  counts), and scores come from `Case.score`, written by the case author. When each arm's model sits behind a
  `MeteredCaller`, the model being compared sets neither its score nor its call count.
- **Transfer by failure mode**: the gate is bound to the failure mode, a gain that compute or luck explains, not to a
  task: the same rules apply to any two arms. Not yet: no catch rate has been measured on task families it was not
  built on.
- **Enough held-out cases**: `compare` refuses below `min_cases` (30) and uses an exact test, but it is one test at the
  end and does not say how many more cases it needs. The meta-harness's evaluation, which uses this sign test, adds a
  sequential confirmation and a power estimate for workflows; not yet for `compare` itself.
