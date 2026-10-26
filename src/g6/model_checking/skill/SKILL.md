---
name: g6-model-checking
description: Use when a design has states and steps (a protocol, a lock, a retry loop, a workflow, a state machine, concurrent or distributed steps) and you are about to say it never reaches a bad state, never deadlocks or can always reach a goal, from tests or a reading that tried only some orders of events.
---

# G6 model checking

Checks a state machine for broken invariants, deadlocks and reachable goals: symbolic models in SMT-LIB checked with
Z3 in process, and TLA+ specs through TLC and SANY out of process. Every symbolic trace and every "nothing found" is
re-checked on fresh solvers before it is reported.

## When to use it

- You are about to write "this lock cannot deadlock", "the counter never exceeds N" or "every request is eventually
  served" about a design.
- A bug appears only under some interleaving and tests have not found it.
- You want a concrete trace from an initial state to the bad state, not a suspicion.
- You have a TLA+ spec and want TLC's verdict, or a syntax and level check from SANY.

## When not to use it

- A single logical step with no notion of time: use `formal`.
- Safety bounds and LTL properties on an agent model: use `csf`.
- Whether the code's tests pass: run them, and report through `coding_agent_gate`.
- A schedule or route to optimise: use `optimisation`.

## Ops

- `check_invariant`: bounded model checking to `depth` steps (at most 50); a state breaking an invariant with its
  trace from an initial state, or none on paths of up to that depth, which is all it claims.
- `prove_invariant`: k-induction up to `depth` as the largest k; the invariants inductive at k, a counterexample from
  the initial states, or "not inductive at k" with an induction-step trace that need not start in a reachable state.
- `check_deadlock`: a reachable state with no successor, with its trace, within the bound.
- `reachable`: a trace to a state meeting `goal`, or none within the bound.
- `tlc_check`: TLC's verdict on a TLA+ `spec`, given either `config` text or the fields `specification`,
  `invariants`, `properties` and `constants` (not both), with `check_deadlock` and `workers`, and its counts and
  trace (sensitive: runs TLC, from the TLA+ tools on Java, as you with no sandbox; a spec can write files through
  TLC's own modules, such as `Json`).
- `sany_parse`: SANY's syntax, semantic and level check of `spec`, nothing model-checked (sensitive: runs SANY on
  Java, as you, with no sandbox).

## Reading the result

A symbolic `model` is `{"variables": {"x": "Int"}, "init": term, "transition": term, "invariants": {name: term}}`, all
SMT-LIB read through `formal`'s closed grammar; the next-state copy of `x` is `next_x`. The reply gives `verdict`,
`trace` (states, step by step), `trace_kind`, `k` and `recheck` (each re-check query and its answer).

- `qualified-draft`: `G6_E_MODEL_CHECK_BOUNDED` (nothing found on paths up to the depth only),
  `G6_E_MODEL_CHECK_ALL_PATHS_END` (every path ended within it), `G6_E_MODEL_CHECK_K_INDUCTION` (the `verdict`
  field says `k_inductive`, with the k in `k`), `G6_E_MODEL_CHECK_TRACE_FOUND` (a witness for `reachable`),
  `G6_E_MODEL_CHECK_TLC_NO_ERROR` (TLC's own verdict), `G6_E_MODEL_CHECK_SANY_NO_ERROR` (a clean parse; nothing
  model-checked). These are on `warning_card.code`; the result's own `code` is empty. Nothing here is `verified`.
- `blocked-escalated`, the trace kept in `trace`: `G6_E_MODEL_CHECK_VIOLATION`, `G6_E_MODEL_CHECK_DEADLOCK`,
  `G6_E_MODEL_CHECK_NOT_INDUCTIVE`, `G6_E_MODEL_CHECK_TLC_INVARIANT_VIOLATED` and TLC's other violations.
- `blocked-escalated` with no verdict: `G6_E_MODEL_CHECK_VACUOUS_INIT` and `G6_E_MODEL_CHECK_VACUOUS_TRANSITION`
  (the model has no initial state, or no state has a successor), `G6_E_MODEL_CHECK_CERTIFICATE_REFUTED`, a missing jar
  or Java, a digest mismatch, a time limit.

## Alone and composed

Alone, over MCP: `g6_invoke("model_checking", "check_invariant", params_json)`. From Python:
`from g6.model_checking import check_invariant, prove_invariant, tlc_check`; the TLA+ ops also run from
`get_registry().invoke("model_checking", "tlc_check", {...})`.

Composed:

- It requires `formal`: its terms go through `formal`'s grammar to Z3. Decide a single step's logic there first.
- Run `check_invariant` for a counterexample, then `prove_invariant` for the depth-free claim; give `coding_agent_gate`'s
  `verify_answer` the reply as an evidence item of kind `log` before reporting a design as sound (it counts as
  caller-declared).

## Failure modes it catches

- `rare_interleaving` (a design that fails only in a rare order of events nobody tried): the solver searches every
  path up to the depth, not the orders a test happened to run, and returns the trace that breaks an invariant or
  deadlocks. Each trace is re-checked state by state (state 0 satisfies init, each step the transition, the last
  state breaks the invariant), and every claim that nothing was found, that every path ends or that k-induction holds
  is decided again in a second Z3 context.

## Blind spots

- The model is yours. If the SMT-LIB or the TLA+ spec does not match the code, the verdict is about the model, not
  the code; G6 does not extract a model from source.
- `check_invariant`, `check_deadlock` and `reachable` claim nothing past `depth`. Only `prove_invariant` reaches
  beyond, and "not inductive at k" is not a counterexample: the invariant may hold yet need strengthening.
- No CTL, LTL or fairness on symbolic models; TLC checks temporal properties of TLA+ specs instead.
- TLC's "no error" is TLC's own verdict, not re-checked by G6, and holds only for the constants and state space TLC
  explored.
- Z3 can answer unknown on non-linear arithmetic (`G6_E_MODEL_CHECK_SOLVER_UNKNOWN`); that is a refusal, not a pass.

## What it needs

- `pip install "g6[formal] @ git+https://github.com/basicisabc1994/g6solver"` for Z3, which the symbolic ops use. Without it they are `blocked-escalated` with
  `G6_E_MODEL_CHECK_SOLVER_UNAVAILABLE`.
- Java, only for `tlc_check` and `sany_parse`: Java 11 or later on PATH, or `G6_JAVA` naming the java program. Then
  download `tla2tools.jar` yourself from the TLA+ project's releases (G6 is tested on v1.7.4), check it, set
  `G6_TLA2TOOLS_JAR` to its path and `G6_TLA2TOOLS_SHA256` to its SHA-256. G6 never downloads it and runs only the jar
  whose digest you pinned. Over MCP these two ops run only when the person running the server lists them (or
  `model_checking`) in `G6_MCP_SENSITIVE`; an agent never edits that setting to grant itself one.

## Examples

```g6-example op=check_invariant
{"model": {"variables": {"x": "Int"}, "init": "(= x 0)", "transition": "(= next_x (+ x 1))", "invariants": {"small": "(<= x 3)"}}, "depth": 10}
```

The reply is `blocked-escalated` with `G6_E_MODEL_CHECK_VIOLATION`: `violated` is `["small"]` and `trace` runs x = 0,
1, 2, 3, 4, with `recheck.passed` true.

```g6-example op=prove_invariant
{"model": {"variables": {"x": "Int"}, "init": "(= x 0)", "transition": "(= next_x (+ x 1))", "invariants": {"natural": "(>= x 0)"}}}
```

`qualified-draft` with `G6_E_MODEL_CHECK_K_INDUCTION`, `verdict` `k_inductive` and `k` 1: the invariant is
1-inductive, so it holds in every reachable state, not only up to a depth, as a solver decision on this model.

## The four open problems

- **Learning signals.** Not yet: it writes nothing to a run ledger itself. Each catch is a labelled result with its
  own `G6_E_MODEL_CHECK_*` code, its trace in `trace` and the trace's re-check in `evidence`; recording it is the
  caller's step today.
- **Evaluators the model cannot influence.** The search is Z3's or TLC's, and the re-checks are the package's own
  queries in a second Z3 context; TLC runs only from a jar pinned by its SHA-256. No model call runs anywhere in the
  package; the caller writes the state model, never the check.
- **Transfer by failure mode.** The method belongs to state machines, not to a task: any model the grammar can write
  is searched the same way. Not yet: its catch rate on task families it was not built on has not been measured.
- **Enough held-out cases.** Not yet: its own catch rate and false-alarm rate are not judged by a sequential test. A
  repeated workflow that uses it can be tuned with `g6.metaharness`, whose gate judges a change on held-out cases.
