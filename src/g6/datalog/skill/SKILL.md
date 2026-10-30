---
name: g6-datalog
description: Use when an answer depends on what follows from a set of facts and rules (permissions, eligibility, dependencies, reachability, policy, family or ownership relations) and you are about to say that something does or does not follow, or to list everything that does, from your own reading of the rules.
---

# G6 datalog

Works out what follows from facts and rules in a strict Datalog, on two engines that must agree: G6's own pure-Python
engine and Z3's Datalog engine. The model is checked in pure Python before any answer is built from it, and each
answer carries its derivation towards the facts (a long tree is cut, and the cut is marked).

## When to use it

- You are about to say "so this user can access that", "this package depends on that" or "nothing makes X true".
- Rules use recursion (ancestors, transitive dependencies, reachability) or negation ("not banned"), where reading by
  eye misses steps.
- You need every consequence of the rules, not the first few you think of.
- You need the chain of rules and facts behind a conclusion, to show a person.

## When not to use it

- Facts with degrees of truth or confidence: use `graph_reasoning`.
- Arithmetic, quantifiers or a counterexample over numbers: use `formal`.
- Whether a source text supports a claim: use `grounding`.
- A state machine over time: use `model_checking`.

## Ops

- `assert_facts`: check and retain `facts` for a named `session`; supply `program` on first use. Sensitive, dry-run by default.
- `reset`: clear a session's asserted facts, retaining its original program and original facts. Sensitive, dry-run by default.

- `evaluate`: the relations the rules define (or those named in `relations`), from both engines, checked for
  closure, support and negation.
- `query`: each distinct answer to `query` (such as `ancestor(alice, X)`), or to each query written in the program,
  with its derivation.
- `explain`: why a ground atom holds, as its derivation tree down to the facts, or that it is not derivable.
- `check`: the parse, arity, safety and stratification report, with nothing evaluated.

## Reading the result

With `session`, read ops use its saved program and facts. A different program or new facts on a read is refused.
`assert_facts` and `reset` persist only with `dry_run=false` and successful model checks. They are local writes,
declared sensitive (refused over MCP unless sensitive ops are allowed), not outward actions, so the action gate is
not asked. A session holds at most 10000 asserted facts (more is `G6_E_DATALOG_INPUT_TOO_LARGE`); a repeated row
is stored once. Writes use one transaction and read-back. Missing or corrupt sessions, failed checks and storage errors
block with `G6_E_DATALOG_SESSION_*` codes; they never silently become empty sessions. Sessions are isolated by name.

The language: facts `parent(alice, bob).`, rules `ancestor(X, Z) :- parent(X, Y), ancestor(Y, Z).`, stratified
negation `not p(X)`, comparisons on bound variables, and queries `ancestor(alice, X)?`. Variables start upper-case or
with `_`; constants are names, double-quoted strings or integers. Facts may also come as JSON in `facts`
(`{"parent": [["alice", "bob"]]}`). `engine` is `auto`, `z3` or `python`; `time_limit` is at most 60 seconds.

- `qualified-draft`, `G6_E_DATALOG_ENGINE_ANSWER`: both engines derived the same relations and the check passed. The
  best state; nothing here is `verified`.
- `qualified-draft`, `G6_E_DATALOG_ONE_ENGINE`: Z3 is not installed, or `engine` is `python`, so only the
  pure-Python engine ran (still checked); the card says how to add the second.
- `qualified-draft`, `G6_E_DATALOG_NOT_EVALUATED`: `check`'s card; the program was checked and nothing evaluated.
- `blocked-escalated`: `G6_E_DATALOG_ENGINES_DISAGREE`, `G6_E_DATALOG_CERTIFICATE_REFUTED`, or a refused program
  (`G6_E_DATALOG_PARSE_ERROR`, `G6_E_DATALOG_UNSAFE_RULE`, `G6_E_DATALOG_NOT_STRATIFIABLE`,
  `G6_E_DATALOG_ARITY_CLASH`), each with line and column.

A draft's code is on `warning_card.code`; the result's own `code` is empty unless it is blocked.

## Alone and composed

Alone, over MCP: `g6_invoke("datalog", "query", params_json)`. From Python:
`from g6.datalog import evaluate, query, explain, check_program`.

Composed:

- When the same rules need graded facts, move them to `graph_reasoning`, whose annotated rules take intervals.
- `explain`'s derivation is the chain to show a person; give the reply to `coding_agent_gate`'s `verify_answer` as an
  evidence item of kind `log` before reporting a conclusion that rests on it (it counts as caller-declared).
- A conclusion in prose that also cites sources goes to `epistemic_integrity`'s `adjudicate`.

## Failure modes it catches

- `unsupported_conclusion` (a conclusion the rules and facts do not support): `explain` and `query` answer only from
  the program's model, and the support check shows every tuple in it derives from the facts through tuples derived
  before it, so a conclusion with no derivation comes back "not derivable" with the reason.
- `missed_implication` (a consequence the rules imply that goes unnoticed): `evaluate` computes the whole model, and
  the closure check shows applying every rule once more adds nothing. Two independent engines must agree tuple for
  tuple, or the answer is blocked with `G6_E_DATALOG_ENGINES_DISAGREE`.

- `inconsistent_rule_use`: a session retains fixed rules and checked assertions across calls; facts never install model-proposed rules.

## Blind spots

- Session names are namespaces, not user authentication. Anyone with write access to G6's state can alter it.
- No priority, salience, semantic matching or automatic rule training is offered. Reset does not remove facts in program text.

- The model is what your program says. A wrong fact or a missing rule gives a wrong answer that passes every check.
- Not derivable means not in the model (a closed world), not false in the world.
- No aggregates, no function symbols, no arithmetic beyond comparisons on bound variables.
- An unsafe rule or negation through recursion is refused, not guessed at; rewrite it.
- Size and time limits refuse large programs (`G6_E_DATALOG_TOO_MANY_TUPLES`, `G6_E_DATALOG_TIME_LIMIT`).
- A derivation tree is cut at 25 nodes per `query` answer and 1,000 for `explain`; a cut node keeps its atom and is
  marked `truncated`. Run `explain` on that atom to go on.

## What it needs

- `pip install "g6[formal] @ git+https://github.com/basicisabc1994/g6solver"` for Z3, the second engine, imported only when an op runs. Without it the pure-Python
  engine answers alone (`G6_E_DATALOG_ONE_ENGINE`), and asking for `engine` `z3` is refused.
- No outside program or network. Session writes are sensitive and refused over MCP unless the server allows them; all other ops remain read-only. State lives under state_path in datalog/sessions.db.

## Examples

```g6-example op=query
{"program": "parent(alice, bob). parent(bob, carol). parent(carol, dave).\nancestor(X, Y) :- parent(X, Y).\nancestor(X, Z) :- parent(X, Y), ancestor(Y, Z).\n", "query": "ancestor(alice, X)?"}
```

The reply is `qualified-draft`: `queries[0].answers` binds `X` to `bob`, `carol` and `dave`, each with its derivation
down to the `parent` facts.

```g6-example op=explain
{"program": "parent(alice, bob). parent(bob, carol). parent(carol, dave).\nancestor(X, Y) :- parent(X, Y).\nancestor(X, Z) :- parent(X, Y), ancestor(Y, Z).\n", "query": "ancestor(alice, dave)"}
```

`explanation.derivable` is true and `explanation.derivation` is the tree: the recursive rule twice, then the base rule.

```python
from g6.datalog import evaluate, explain, query

family = """
parent(alice, bob). parent(bob, carol).
ancestor(X, Y) :- parent(X, Y).
ancestor(X, Z) :- parent(X, Y), ancestor(Y, Z).
"""
found = query(family, "ancestor(alice, X)")
assert [a["bindings"]["X"] for a in found.queries[0]["answers"]] == ["bob", "carol"]
assert found.completion_state == "qualified-draft" and found.check["passed"] is True
assert explain(family, "ancestor(carol, alice)").explanation["derivable"] is False
unsafe = evaluate("p(X) :- q(Y).")
assert unsafe.completion_state == "blocked-escalated" and unsafe.code == "G6_E_DATALOG_UNSAFE_RULE"
```

```g6-example op=evaluate
{"program":"p(a). q(X) :- p(X).","engine":"python"}
```

```g6-example op=check
{"program":"p(a). q(X) :- p(X)."}
```

## The four open problems

- **Learning signals.** Not yet: it writes nothing to a run ledger itself. Each catch is a labelled result with its
  own `G6_E_DATALOG_*` code, the check and the engines' agreement in `evidence` (and, for an answer, its derivations
  in `queries` or `explanation`); recording it is the caller's step today.
- **Evaluators the model cannot influence.** The engines and the closure, support and negation checks are the
  package's own code and Z3; no model call runs anywhere in the package. The caller writes the program, never the
  check.
- **Transfer by failure mode.** The checks belong to Datalog, not to a task: closure and support apply to any program
  the language can write. Not yet: its catch rate on task families it was not built on has not been measured.
- **Enough held-out cases.** Not yet: its own catch rate and false-alarm rate are not judged by a sequential test. A
  repeated workflow that uses it can be tuned with `g6.metaharness`, whose gate judges a change on held-out cases.
