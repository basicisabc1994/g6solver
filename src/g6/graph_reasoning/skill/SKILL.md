---
name: g6-graph-reasoning
description: Use when conclusions spread over a graph of people, services, accounts or documents from facts that are only partly certain (trust, risk, popularity, reliability scores) and you are about to state how sure a conclusion is, or to combine rules that might contradict each other, from your own reading.
---

# G6 graph reasoning

Works out the truth interval `[lower, upper]` of each label on a graph's nodes and edges from facts and annotated
rules, in the style of PyReason, on a pure-Python fixpoint over exact rational intervals. Every interpretation is
checked before it is reported, and a contradiction is blocked with both sides' derivations, never resolved silently.

## When to use it

- You are about to say "this account is risky", "this supplier is trustworthy" or "this user is influential" from
  facts that are themselves uncertain.
- Rules chain over edges (friends of friends, dependencies of dependencies) and each rule's head interval, or an
  annotation function, should set how sure the conclusion is.
- Two rules may push the same conclusion in opposite directions, and you need to know if they collide.
- You need to show how a conclusion's bounds were reached, step by step down to the facts.

## When not to use it

- Every fact is plainly true or false: use `datalog`, which also cross-checks on a second engine.
- Probabilities from data, or cause and effect: use `causal`.
- Logic over numbers or a counterexample: use `formal`.
- A conclusion that rests on source passages: use `grounding`.

## Ops

- `reason`: the truth interval of every label on the graph's nodes and edges (or those named in `labels`) that the
  facts and annotated rules give, checked for closure, support and soundness.
- `query`: the interval of one `atom` (such as `popular(alice)`), with the steps that set its lower and upper bounds.
- `explain`: why an interval holds, as its derivation down to the facts, or why it stayed unknown.
- `check`: the parse, interval, arity, safety and stratification report, and whether PyReason could cross-check the
  model, with nothing evaluated.

## Reading the result

The language: facts `friend(alice, bob).` (true), `friend(carol, dave) : [0.9, 1].`, rules
`popular(x) : [0.7, 1] <- friend(x, y) : [0.9, 1], popular(y) : [0.7, 1].`, clause thresholds (`at least 2`,
`at least 50%`) and annotation functions from a fixed set (`average`, `average_lower`, `maximum`, `minimum`,
`product`). The graph and facts may also come as JSON in `graph` and `facts`. An answer's `bounds` and `status`
(`true`, `false`, `between` or `unknown`) say how sure it is.

- `qualified-draft`, `G6_E_GRAPH_REASONING_ONE_ENGINE`: the pure-Python engine ran and the check passed;
  `G6_E_GRAPH_REASONING_ENGINE_ANSWER` when PyReason ran too and agreed; `G6_E_GRAPH_REASONING_NOT_EVALUATED` for
  `check`, which evaluates nothing. These codes are on `warning_card.code`; the result's own `code` is empty. Nothing
  here is `verified`.
- `blocked-escalated`: `G6_E_GRAPH_REASONING_CONFLICT` (with `conflict`: the atom, the interval it had, the one that
  met it, and both derivations), `G6_E_GRAPH_REASONING_CERTIFICATE_REFUTED`,
  `G6_E_GRAPH_REASONING_ENGINES_DISAGREE`, `G6_E_GRAPH_REASONING_PARSE_ERROR` and the other model refusals, each with
  line and column.

## Alone and composed

Alone, over MCP: `g6_invoke("graph_reasoning", "query", params_json)`. From Python:
`from g6.graph_reasoning import reason, query, explain, check_model`.

Composed:

- Rules whose facts are all certain belong in `datalog`; move them there for its second engine.
- Give `coding_agent_gate`'s `verify_answer` the reply as an evidence item of kind `log` before reporting a conclusion
  that rests on it (it counts as caller-declared), and keep the interval, not a rounded "yes".

## Failure modes it catches

- `overstated_certainty` (a conclusion stated with more certainty than the facts give): each label carries the
  interval its facts give or its rules' heads assign (or an annotation function computes), in exact fractions; an
  uncertain edge lowers a conclusion only if the rule's head interval or annotation says so, and one nothing supports
  stays `unknown` at `[0, 1]`. The support check shows every interval comes from a fact or a rule whose body held, so
  nothing rests on itself.
- `contradictory_rules` (rules that contradict each other without anyone noticing): a step that would leave an
  interval empty is a conflict, blocked with `G6_E_GRAPH_REASONING_CONFLICT` and both sides' derivations, where
  PyReason by default resets such an atom to `[0, 1]`.

## Blind spots

- The intervals are what your facts and rules say. A fact given too much confidence passes every check, and a rule
  with no head interval assigns `[1, 1]` once its body holds, however uncertain the body was.
- A conflict is found only when both sides (a fact or a rule) reach the same atom with the given facts; rules that
  would contradict on other facts are not flagged.
- One static fixpoint over a fixed graph: no timesteps, rule delays, facts over time, edge inference or head
  functions; what it does not read is refused, not ignored.
- PyReason 3.7.0 does not import on this library's Python, so in practice only the pure-Python engine runs, and the
  card says so.
- Size and time limits (at most 60 seconds) refuse large models.

## What it needs

- Nothing outside G6: a pure-Python engine over exact rational intervals, with no extra. PyReason is used as a second
  engine only if it imports as it is, which PyReason 3.7.0 does not on this library's Python, so it is not an extra.
- No op runs code, writes a file or reaches the network, so none is sensitive.

## Examples

```g6-example op=query
{"program": "friend(alice, bob). friend(bob, carol). friend(carol, dave) : [0.9, 1].\npopular(dave).\npopular(x) : [0.7, 1] <- friend(x, y) : [0.9, 1], popular(y) : [0.7, 1].\n", "atom": "popular(alice)"}
```

`answer.bounds` is `[0.7, 1.0]` with `status` `between`: alice is popular at least to 0.7, not certainly, and
`answer.derivation` walks back through bob, carol and dave to the facts.

```g6-example op=reason
{"program": "trusted(a) : [0.8, 1]. flagged(a).\ntrusted(x) : [0, 0.3] <- flagged(x).\n"}
```

The reply is `blocked-escalated` with `G6_E_GRAPH_REASONING_CONFLICT`: `conflict.before` is `[0.8, 1.0]` from the
fact, `conflict.met` is `[0.0, 0.3]` from the rule, and nothing is reported as the answer.

```python
from g6.graph_reasoning import query, reason

social = """
friend(alice, bob). friend(bob, carol). friend(carol, dave) : [0.9, 1].
popular(dave).
popular(x) : [0.7, 1] <- friend(x, y) : [0.9, 1], popular(y) : [0.7, 1].
"""
alice = query(social, "popular(alice)")
assert alice.answer["bounds"] == [0.7, 1.0] and alice.answer["status"] == "between"
assert alice.completion_state == "qualified-draft"
clash = reason("trusted(a) : [0.8, 1]. flagged(a).\ntrusted(x) : [0, 0.3] <- flagged(x).")
assert clash.code == "G6_E_GRAPH_REASONING_CONFLICT" and clash.conflict["atom"] == "trusted(a)"
```

## The four open problems

- **Learning signals.** Not yet: it writes nothing to a run ledger itself. Each catch is a labelled result with its
  own `G6_E_GRAPH_REASONING_*` code and, for a conflict, both derivations in `evidence`; recording it is the caller's
  step today.
- **Evaluators the model cannot influence.** The engine and the closure, support and soundness checks are the
  package's own code with its own matcher; no model call runs anywhere in the package. The caller writes the facts
  and rules, never the check.
- **Transfer by failure mode.** The checks belong to annotated logic, not to a task: an empty interval is a conflict
  in any graph. Not yet: its catch rate on task families it was not built on has not been measured.
- **Enough held-out cases.** Not yet: its own catch rate and false-alarm rate are not judged by a sequential test. A
  repeated workflow that uses it can be tuned with `g6.metaharness`, whose gate judges a change on held-out cases.
