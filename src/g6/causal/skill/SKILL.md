---
name: g6-causal
description: Use when an answer says one thing causes, drives or improves another, or estimates an effect from data, and you are about to choose which variables to control for, call an effect identifiable, or report a causal number from a correlation, a regression or your own reading of a diagram.
---

# G6 causal

Works out from a causal graph whether an effect can be estimated and how, estimates it with DoWhy from inline rows,
and tries to knock the estimate down. The graph questions are answered by exact algorithms in pure Python and, when
DoWhy is installed, by DoWhy too; the two must agree.

## When to use it

- You are about to write "X increases Y", "the campaign caused the uplift" or "controlling for Z, the effect is".
- You must choose which variables to adjust for, and some might be colliders or mediators.
- There is an unobserved confounder and you need to know whether an instrument or a frontdoor route still identifies
  the effect.
- You have rows of data and a graph, and want an estimate with a confidence interval that has faced refuters.

## When not to use it

- You have no causal graph and cannot state one: nothing in G6 discovers the graph from data, so first write down the
  assumptions with a person.
- A causal claim in prose that you need graded against its evidence: use `epistemic_integrity`'s `adjudicate`.
- Rules over facts with no causal reading: use `datalog`.
- Plain descriptive statistics: ordinary Python.

## Ops

- `d_separated`: whether `given` d-separates `x` from `y`, with a path left open when it does not.
- `backdoor_paths`: every path from `treatment` to `outcome` that starts with an arrow into the treatment, each open or
  blocked.
- `adjustment_sets`: every minimal set of observed variables to control for, by the backdoor criterion.
- `instruments`: every observed instrumental variable for the treatment and outcome.
- `identify`: whether the effect is identifiable by the backdoor, frontdoor or instrumental-variable criterion, with
  the estimand written out, or why not.
- `estimate_effect`: the effect from inline `data` rows by `method` (`backdoor.linear_regression`, propensity-score
  stratification or weighting, `iv.instrumental_variable` or `frontdoor.two_stage_regression`), with its 95%
  confidence interval, recomputed by G6 for linear regression and tried by DoWhy's placebo, random-common-cause and
  data-subset refuters (`refute`, `simulations`, `seed`).

## Reading the result

`graph` is JSON (`{"nodes": [...], "edges": [["Z", "T"], ...], "unobserved": [...]}`) or DOT
(`digraph { Z -> T; U [observed=no]; }`). The answer is in `answer`; an estimate in `estimate`,
`confidence_interval`, `refutations` and `recomputation`.

- `qualified-draft`, `G6_E_CAUSAL_GRAPH_ANSWER` (both engines agreed) or `G6_E_CAUSAL_ONE_ENGINE` (pure Python only).
- `qualified-draft`, `G6_E_CAUSAL_ESTIMATE`: the engines agreed on the identification, every refuter that tested the
  estimate passed and, for linear regression, G6's least-squares fit matched. `G6_E_CAUSAL_REFUTATION_INCOMPLETE`
  names a refuter that could not test it; `G6_E_CAUSAL_REFUTATION_OFF` means you turned refutation off.
- `blocked-escalated`: `G6_E_CAUSAL_REFUTED` and `G6_E_CAUSAL_CERTIFICATE_REFUTED` (the numbers kept),
  `G6_E_CAUSAL_ENGINES_DISAGREE`, `G6_E_CAUSAL_NOT_IDENTIFIABLE` (from `estimate_effect`; `identify` answers
  `qualified-draft` with `identifiable` false and the reason), `G6_E_CAUSAL_GRAPH_CYCLE` and the other graph refusals.

Every `qualified-draft` card says the answer rests on the graph, which data cannot confirm. Nothing here is
`verified`.

## Alone and composed

Alone, over MCP: `g6_invoke("causal", "adjustment_sets", params_json)`. From Python:
`from g6.causal import adjustment_sets, identify, estimate_effect`.

Composed:

- Run `identify` first; its route tells you which `method` fits `estimate_effect` (a mismatch is refused with
  `G6_E_CAUSAL_METHOD_NOT_APPLICABLE`).
- The claim you then write in prose goes to `epistemic_integrity`'s `adjudicate`; give `coding_agent_gate`'s
  `verify_answer` the causal reply as an evidence item of kind `log` (it counts as caller-declared).

## Failure modes it catches

- `correlation_as_cause` (taking a correlation for a cause): `estimate_effect` refuses unless the graph identifies
  the effect (`G6_E_CAUSAL_NOT_IDENTIFIABLE`). An identified estimate is tried by a placebo treatment, an added random
  common cause and data subsets unless `refute` is false; a refuter that contradicts it, judged by G6 against the
  confidence interval, blocks it (`G6_E_CAUSAL_REFUTED`), and one that could not test it is named
  (`G6_E_CAUSAL_REFUTATION_INCOMPLETE`), never counted as a pass.
- `wrong_adjustment` (controlling for the wrong variables): `adjustment_sets` gives every minimal backdoor set from
  exact graph algorithms, `backdoor_paths` shows which paths a set leaves open, and DoWhy's answer must agree with G6's
  or the reply is blocked.

## Blind spots

- Everything rests on the graph. A missing confounder you did not draw, or an arrow the wrong way, gives a confident
  wrong answer that passes every check.
- `identify` checks three criteria (backdoor, frontdoor, instrumental variable), not the complete do-calculus: "not
  identifiable" may mean another identification exists, and the reason says so.
- Only linear regression is recomputed independently; the other estimators are DoWhy's, tried by refuters only.
- Refuters test robustness to their specific changes, not truth; passing them does not rule out a bias they cannot
  reach. With `refute` false none runs, and the estimate is still `qualified-draft` (`G6_E_CAUSAL_REFUTATION_OFF`).
- An instrumental-variable estimand is the average effect only if the effect is the same for every unit; otherwise it
  is a local effect, as the estimand text says.
- Data come as inline rows, within size limits. A DoWhy call that has started is not interrupted by `time_limit`.

## What it needs

- `pip install "g6[causal]"` for DoWhy, imported only when an op runs. Without it the graph ops still answer in pure
  Python (`G6_E_CAUSAL_ONE_ENGINE`), and `estimate_effect` is `blocked-escalated` with
  `G6_E_CAUSAL_ENGINE_UNAVAILABLE` and the install line.
- No outside program. No op runs code, reads a file or reaches the network, so none is sensitive.

## Examples

```g6-example op=adjustment_sets
{"graph": {"nodes": ["Z", "T", "Y"], "edges": [["Z", "T"], ["Z", "Y"], ["T", "Y"]]}, "treatment": "T", "outcome": "Y"}
```

`answer.sets` is `[["Z"]]`, with the estimand that adjusts for `Z`; the reply is `qualified-draft`.

```g6-example op=identify
{"graph": "digraph { U [observed=no]; W -> T; U -> T; U -> Y; T -> Y }", "treatment": "T", "outcome": "Y"}
```

The confounder `U` is unobserved, so no backdoor set exists; `answer.route` is `iv`, with `W` the instrument and the
Wald-ratio estimand and its assumptions written out.

```python
from g6.causal import adjustment_sets, backdoor_paths

graph = "digraph { Z -> T; Z -> Y; T -> Y }"
sets = adjustment_sets(graph, "T", "Y", engine="python")
assert sets.answer["sets"] == [["Z"]] and sets.completion_state == "qualified-draft"
paths = backdoor_paths(graph, "T", "Y", engine="python").answer
assert paths["open"] == 1 and paths["paths"][0]["rendered"] == "T <- Z -> Y"
cycle = adjustment_sets("digraph { T -> Y; Y -> T }", "T", "Y", engine="python")
assert cycle.completion_state == "blocked-escalated" and cycle.code == "G6_E_CAUSAL_GRAPH_CYCLE"
```

## The four open problems

- **Learning signals.** Not yet: it writes nothing to a run ledger itself. Each catch is a labelled result with its
  own `G6_E_CAUSAL_*` code and, for a refuted estimate, every number in `evidence`; recording it is the caller's step
  today.
- **Evaluators the model cannot influence.** The graph criteria, the recomputation and the judgement of each refuter
  are the package's own code, with DoWhy as a second engine; no model call runs anywhere in the package. The caller
  supplies the graph and the rows, never the check.
- **Transfer by failure mode.** The criteria belong to causal graphs, not to a task: the backdoor criterion applies to
  any graph. Not yet: its catch rate on task families it was not built on has not been measured.
- **Enough held-out cases.** Not yet: its own catch rate and false-alarm rate are not judged by a sequential test. A
  repeated workflow that uses it can be tuned with `g6.metaharness`, whose gate judges a change on held-out cases.
