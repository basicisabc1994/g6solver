---
name: g6-formal
description: Use when an answer rests on a logical step that must be exactly right, such as whether premises entail a conclusion, whether constraints can all hold, a counterexample to a claim, two conditions being equivalent, which requirements conflict, or a small optimum over integer or real constraints, and you are about to argue it in words instead of deciding it.
---

# G6 formal

Decides logic questions exactly, in process: Z3 on SMT-LIB terms over symbols you declare, a pure-Python DPLL for
Boolean formulas, and Belnap's four-valued logic with modal frames. The answers of `optimise`, `unsat_core`,
`simplify`, `eliminate_quantifiers` and `count_models` are checked again on fresh solvers; a validity check also asks
whether its premises can hold; every decision records the exact queries it rests on, which `g6.formal.recheck` can
run again.

## When to use it

- You are about to write "this follows", "these rules cannot conflict", "this guard is always true" or "no input
  breaks it".
- A review hinges on a condition: two guards equivalent, a branch unreachable, a bound that always holds.
- Requirements seem to conflict and you need the smallest conflicting set.
- You want a concrete counterexample to a claim, not an argument.

## When not to use it

- Algebra or calculus (simplify, solve, integrate): use `algebra`.
- Everything that facts and rules derive: use `datalog`; with graded truth values, `graph_reasoning`.
- A state machine over many steps (invariants, deadlocks): use `model_checking`, which reads its terms through this
  package's grammar.
- Linear, integer, routing or scheduling problems of real size: use `optimisation`; `optimise` here is for small
  symbolic objectives.
- A claim in prose that rests on sources: use `grounding` or `epistemic_integrity`. This component decides only the
  encoding you write.

## Ops

- `check_contract`: checks up to 32 `properties` (SMT terms) against `premises` and `sorts`, then aggregates with
  Belnap conjunction. Missing properties refuse by default; non-vacuity cannot be switched off. With premises, a
  property that holds is checked again without them: one that holds alone is N (`G6_E_FORMAL_VACUOUS_PROPERTY`).
  `timeout` is one deadline for the whole call: each check gets the time that remains (a validity check, which can
  run two queries, half of it), and running out is N with `G6_E_FORMAL_SOLVER_TIMEOUT`.

- `check_sat`: whether `formula` with `assertions` can hold over the declared `sorts`, with a model.
- `check_validity`: whether `premises` entail `formula`: valid, `vacuous` (the premises cannot hold), circular (every
  conjunct of the conclusion appears literally among the premises), or a counterexample.
- `check_entailment`: whether `formula` entails `other`.
- `find_counterexample`: a model in which the claim `formula` is false while the `premises` hold.
- `check_propositional`: a Boolean formula (`A & ~B`) with `assertions`, decided by the built-in DPLL; no Z3 needed.
- `optimise`: the lexicographic optimum of `objectives` (`[["minimise", term]]` or `maximise`) under `assertions` and
  `bounds`, confirmed by a bound and an attainment check.
- `unsat_core`: which of the `named` assertions conflict, shrunk to a minimal core (`minimal` says whether it was).
- `simplify`: Z3's simplified term, confirmed equivalent.
- `eliminate_quantifiers`: an equivalent quantifier-free formula.
- `count_models`: how many models over the declared symbols, exact or at least `cap`.
- `check_equivalence`: whether `formula` and `other` are equivalent under `premises`, or a distinguishing assignment.
- `belnap_eval`: the four-valued value of a formula under `values` (`T`, `F`, `B` for both, `N` for neither).
- `belnap_table`: the four-valued truth table over the formula's atoms.
- `modal_eval`: necessity or possibility (`modality`) at `world` over a Kripke frame of `worlds` and `access`.
- `frame_check`: which conditions of an alethic, epistemic, deontic or temporal `modal_frame` the `worlds` and
  `access` meet and miss, with a witness.

## Reading the result

Terms are SMT-LIB, such as `(and (> x 2) (< x 4))`, over symbols declared in `sorts` (`Int`, `Real`, `Bool`,
`String`, `(_ BitVec n)`, `(Array S T)`); nothing is declared for you. A decision is `qualified-draft` with
`G6_E_FORMAL_SOLVER_DECISION` on `warning_card.code` (the result's own `code` is empty), and `artifact.checks` holds
the queries the decision rests on, with Z3's answers (`optimise` adds `attainment`, `unsat_core` adds `minimality`),
so anyone can run them again (`g6.formal.recheck` does). `blocked-escalated` codes, on `code` and the card, include `G6_E_FORMAL_VACUOUS_PREMISES`,
`G6_E_FORMAL_CONCLUSION_ASSUMED`, `G6_E_FORMAL_SOLVER_UNKNOWN` and `G6_E_FORMAL_SOLVER_TIMEOUT` (unknown is never read
as a decision), `G6_E_FORMAL_CERTIFICATE_REFUTED` and `G6_E_FORMAL_PARSE_ERROR`. A four-valued claim that comes out
`F`, `B` or `N` is `blocked-escalated` (`G6_E_FORMAL_CLAIM_REFUTED`, `G6_E_FORMAL_CLAIM_CONFLICT`,
`G6_E_FORMAL_CLAIM_UNKNOWN`), the value kept.

## Alone and composed

Alone, over MCP: `g6_invoke("formal", "find_counterexample", params_json)`. From Python:
`from g6.formal import check_sat, check_validity, find_counterexample, optimise`.

Composed:

- `model_checking` reads its state models through this package's closed grammar and checks them with Z3.
- `cegis`'s `Z3Verifier` asks `g6.formal.check_sat` for an input on which a synthesised candidate breaks your spec.
- Before reporting a conclusion that rests on a decision, give `coding_agent_gate`'s `verify_answer` the reply as an
  evidence item of kind `log`; it counts as caller-declared.

## Failure modes it catches

- `confident_reasoning_error` (a reasoning slip stated with confidence): the step is decided by Z3 or DPLL instead of
  argued. A validity check also asks whether the premises can hold at all, so contradictory premises come back
  `vacuous`, not `valid`, and a conclusion whose every conjunct appears literally among the premises is flagged as
  circular. The depth ops' answers are checked again on fresh solvers (an optimum shown unbeaten and attained, a core
  shrunk until minimal, a simplified term shown equivalent, a model count shown exact); an answer its own check
  contradicts is refused.

- `vacuous_proof`: an empty contract, inconsistent premises, a property that holds without the premises, a missing
  solver or a failed property cannot yield T.
  Contract evidence retains every property's solver checks and counterexample. Jointly inconsistent property
  formulas surface as B; a false property with a consistent specification is F. Neither is a proof.

## Blind spots

- Contract properties are simultaneous requirements. A B conflict is a conflict in the specification, not conflicting
  real-world observations. Per-property timeouts can accumulate across the bounded list.

- The encoding is yours. If the SMT-LIB does not say what the claim in words says, the decision does not either; G6
  does not translate prose into logic or check that a translation is faithful.
- Z3 can answer unknown or run out of time, especially on non-linear arithmetic and quantifiers; that is a refusal,
  not an answer.
- `count_models` is a lower bound when it reaches `cap`.
- `check_sat`, `check_validity`, `check_entailment`, `find_counterexample` and `check_equivalence` report one Z3
  decision, not checked again on a second solver.
- A core of more than 50 members is not shrunk, and a shrink query Z3 answers unknown leaves `minimal` null: the core
  may then hold more than the conflict needs.
- Circularity is caught only literally: a conclusion that is a rewording of a premise is not flagged.
- A Boolean formula whose CNF would pass the DPLL's clause limit is refused (`G6_E_FORMAL_INPUT_TOO_LARGE`; from
  `dpll_solve`, `FormulaTooLarge`), since distribution into CNF can grow exponentially.
- A four-valued result is only as good as the values you assign each atom.

## What it needs

- `pip install "g6[formal] @ git+https://github.com/basicisabc1994/g6solver"` for Z3 (`z3-solver`), imported only when a check runs. Without it every Z3 op is
  `blocked-escalated` with `G6_E_FORMAL_SOLVER_UNAVAILABLE` and the install line.
- No outside program: `check_propositional` and the four-valued and modal ops are pure Python. No op runs code, writes
  a file or reaches the network, so none is sensitive.

## Examples

```g6-example op=check_contract
{"properties":["(> x 0)","(< x 10)"],"premises":["(= x 4)"],"sorts":{"x":"Int"}}
```

```g6-example op=find_counterexample
{"formula": "(> (* x x) x)", "premises": ["(> x -5)", "(< x 5)"], "sorts": {"x": "Int"}}
```

The claim "x squared is greater than x" is false here: the reply is `qualified-draft`, `valid` is false and
`counterexample` gives an `x` where it fails, with the query Z3 decided in `artifact.checks`.

```g6-example op=check_validity
{"premises": ["(> x 1)", "(< x 0)"], "formula": "(> x 5)", "sorts": {"x": "Int"}}
```

The premises cannot both hold, so the reply is `blocked-escalated` with `G6_E_FORMAL_VACUOUS_PREMISES` and `vacuous`
true: "valid" would mean nothing here.

```python
from g6.formal import check_propositional, check_validity

valid = check_validity(["(> x 1)"], "(> x 0)", sorts={"x": "Int"})
assert valid.valid and not valid.vacuous and valid.completion_state == "qualified-draft"
assert check_propositional("A & ~B", assertions=["B | C"]).satisfiable is True
```

## The four open problems

- **Learning signals.** Not yet: it writes nothing to a run ledger itself. Each catch is a labelled result with its
  own `G6_E_FORMAL_*` code and the exact queries in `evidence`; recording it is the caller's step today.
- **Evaluators the model cannot influence.** The decision is Z3's or the package's own DPLL, and the vacuity check and
  the depth ops' re-checks are the package's own code. No model call runs anywhere in the package; the caller writes
  the encoding, never the check.
- **Transfer by failure mode.** The checks belong to the logic, not to a task: vacuity, circularity and the depth ops'
  re-checks apply to any encoding. Not yet: its catch rate on task families it was not built on has not been measured.
- **Enough held-out cases.** Not yet: its own catch rate and false-alarm rate are not judged by a sequential test. A
  repeated workflow that uses it can be tuned with `g6.metaharness`, whose gate judges a change on held-out cases.
