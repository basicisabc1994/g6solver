---
name: g6-csf
description: Use when an agent's plan could reach a harmful state and nobody has worked out how likely that is: write the agent as a small model of states and transition probabilities with the unsafe states named, then get an upper bound on reaching one within N steps, check a temporal property on a trace or on every path, check whether the plan's operations can be undone, or checkpoint a git working tree before risky edits.
---

# G6 safety bounds

`g6.csf` computes safety bounds and checks on an agent model you supply: a finite Markov chain over named states with
explicit transition probabilities and named unsafe states. It gives the union bound on reaching an unsafe state within
N steps, finite-trace LTL, the composition of two bounds, rollback feasibility by operation name, a git checkpoint,
and, with Z3, a check of an LTL property on every path of the model. Every number is computed on your model, never
measured.

## When to use it

- Before an agent runs a multi-step plan in which some outcome is harmful (data loss, an email sent, money moved), and
  nobody has written down which states are unsafe or how likely they are.
- You have a trace of states and want to know whether a property such as `G(!harm)` held on it.
- You are about to rely on rollback and want to know whether every operation in the plan can be undone.
- You are about to let an agent edit files in a git repository and want a snapshot it can be reset to.

## When not to use it

- A state machine with invariants, deadlocks or reachable goals, checked by Z3 or TLA+'s TLC: `model_checking`.
- One action an agent is about to take outside the conversation: `action_gating` (`evaluate`).
- Runtime ceilings and test weakening: `safety_engine`.
- You have no basis at all for the probabilities: a bound on invented numbers is an invented bound. Start from
  observed rates, or use `model_checking` for questions that need no probabilities.

## Python API

- `fit_from_trace`: `(state, next_state)` pairs, `unsafe` labels, `min_support` and `resources` (the horizon, always
  yours) give a `BoundedAgent` with `transition_counts` and a `fit` record in its evidence. Each safe state's chance
  of an unsafe next state is its Clopper-Pearson exact one-sided upper bound, 95% for all safe states together
  (Bonferroni); the largest is used for every safe state, and the hazard the trace did not show goes to an added
  absorbing unsafe state, `<unobserved>`. Thin data gives a large bound, never 0. The initial state is the first
  source. Empty or thin traces, missing hazards or outgoing observations refuse (`G6_E_CSF_TRACE_*`, model codes).
- `EpsilonPolicy`: immutable `name` and `max_epsilon`; `approval_scope` binds both values.
- `tighten`: returns the smaller epsilon only with a live human approval through `approval_authority`;
  absent, revoked or mismatched approval is ignored. Authority errors raise `G6_E_CSF_POLICY_AUTHORITY`.

- `BoundedAgent`: the model: `states`, explicit `transitions` rows `{"from", "to", "probability"}`, `initial_state`,
  `unsafe_states` and `resources`; validated when built.
- `ResourceTriple`: `N`, the horizon every check uses (1 to 100,000); `T` and `M` are recorded and checked by
  nothing.
- `validate_transitions`: the one validator: it refuses undeclared states, unreadable or out-of-range probabilities,
  repeated rows, a state with no outgoing row and outgoing probabilities that do not sum to 1.
- `TransitionOutcome`: one successor and its probability.
- `reachability_bound`: the union bound on P(reach an unsafe state within N steps), step 0 included, against an
  `epsilon` in [0, 1]; returns a `SafetyReport`. A model that names no unsafe state is refused.
- `SafetyReport`: `vub` (and `p_unsafe`, the same number), `meets_bound`, the per-step `steps`, `to_envelope()`.
- `StepHazard`: one step's unsafe mass and the running bound.
- `parse_ltl`: LTL text (`!`, `&`, `|`, `G`, `F`, `X`, `U`, `R`, atoms) to a formula; `ValueError` when it is not one.
- `evaluate_ltl`: whether a formula holds at a position of a finite trace of `{proposition: bool}` mappings.
- `ltl_to_str`: a formula back to text that parses to the same formula.
- `compose_bound`: P(either of two parts fails) at most `eps1 + eps2`, whatever their dependence; the independence
  value only when you pass `assume_independent=True`.
- `rollback_feasibility`: classifies a plan's operations by `OPERATIONS`; feasible only when every one is reversible.
- `RollbackFeasibility`: `feasible`, `irreversible_ops`, `unknown_ops`, `snapshot_steps`, `to_envelope()`.
- `classify_operation`: `reversible`, `irreversible` or `unknown` (never treated as reversible).
- `OPERATIONS`: the one operation vocabulary.
- `GitCheckpoint`: `snapshot()` commits changes to tracked files only; `reset(commit)` runs `git reset --hard` only
  when built with `allow_reset=True`; the root must be the repository's top level. Git runs with no hook, filter,
  fsmonitor, signing program or transport the repository's config names (the tree may be an agent's), so a
  checkpoint commit is unsigned and stores each file's own bytes.
- `CSFError`: a refusal or failure with a `G6_E_CSF_*` code and its reason.
- `CSFInputError`: an input it will not compute on, such as an invalid model or epsilon.
- `smt.check_z3`: asks Z3 for a path of the model, over steps 0 to N, on which an LTL property fails; an
  atom names a state. Needs the `formal` extra.

## Reading the result

A bound within epsilon is `qualified-draft` (`G6_E_CSF_MODEL_BOUND`) and one above it `blocked-escalated`
(`G6_E_CSF_BOUND_EXCEEDED`); the evidence carries the model and its assumptions. Rollback is `qualified-draft`
(`G6_E_CSF_STATIC_CLASSIFICATION`: names classified, effects not observed) or `blocked-escalated`
(`G6_E_CSF_ROLLBACK_INFEASIBLE`). `check_z3` is `qualified-draft` with `G6_E_CSF_SOLVER_DECISION` when no violating
path exists, and `blocked-escalated` with a counterexample path and its probability (`G6_E_CSF_PROPERTY_VIOLATED`),
or with `G6_E_CSF_PROPERTY_NOT_ENCODABLE`, `G6_E_CSF_LTL_PARSE_ERROR`, `G6_E_CSF_ENCODING_MISMATCH`, or a code
passed through from `g6.formal` when Z3 gives no decision: `G6_E_FORMAL_SOLVER_TIMEOUT`, `G6_E_FORMAL_SOLVER_UNKNOWN`,
`G6_E_FORMAL_SOLVER_ERROR`, or `G6_E_FORMAL_SOLVER_UNAVAILABLE` without Z3. An invalid model raises `CSFInputError`
with `G6_E_CSF_MODEL_INVALID`, and so does `reachability_bound` on a model with no unsafe state; git problems
raise `CSFError` with `G6_E_CSF_GIT_RESET_DISABLED`, `G6_E_CSF_GIT_OUTSIDE_ROOT`, `G6_E_CSF_GIT_UNAVAILABLE`,
`G6_E_CSF_GIT_FAILED`, or `G6_E_CSF_GIT_UNSAFE` (a repository holding a submodule, or a git older than 2.31, where G6
cannot keep git from starting a program).

## Alone and composed

It has no block, so over MCP `g6_invoke("csf", ...)` answers `G6_E_NO_BLOCK`; `g6_describe("csf")` serves this skill.
From Python: `from g6.csf import BoundedAgent, ResourceTriple, reachability_bound`.

Composed:

- `formal`: `check_z3` runs through `g6.formal.check_sat`.
- `action_gating`: gate each operation `rollback_feasibility` lists as irreversible before the agent runs it.
- `goal_engine`: its `csf_hazard` guardrail reads a fixed per-action table, not this package; use
  `reachability_bound` when you have a model of the plan.
- `model_checking`: the same states written as a state machine, for invariants and deadlocks.

## Failure modes it catches

- `unanalysed_hazard` (unsafe outcomes nobody worked out in advance): it takes the unsafe states you name (it refuses
  to bound a model that names none, since the bound would be 0 whatever the transitions) and needs every transition
  before it computes anything, refuses an incomplete or inconsistent model, counts an unsafe initial state, and
  blocks on a bound above epsilon, a violated property, or an operation no checkpoint can undo; an operation outside
  the vocabulary is never assumed reversible.

- `model_reality_gap`: fitting retains observed counts and explicit hazard labels, refuses unsupported transitions,
  and bounds the hazard the trace could not rule out instead of reading unseen as impossible.

## Blind spots

- A fitted bound holds at 95% only if each pair is an independent draw from one fixed kernel and the trace saw every
  state the agent can be in; dependent or biased sampling, unseen states and wrong labels are not bounded. The
  largest state bound everywhere and an absorbing hazard make it loose.
- A trusted control plane issues approvals. Caller names cannot approve policies; a cap does not validate the model.

- The states, probabilities and unsafe labels are yours; nothing here measured them, so a wrong model gives a wrong
  bound.
- The union bound can exceed the true probability (an agent that stays in an unsafe state is counted at every step),
  so it can block a plan that is in fact within epsilon.
- The model is a time-homogeneous Markov chain with a finite horizon; a property that holds on it says nothing beyond
  the model or beyond N steps.
- `assume_independent=True` is your assertion; nothing checks it.
- Rollback is judged by operation name: `llm_call` counts as reversible although the prompt has left the machine.
- `GitCheckpoint` neither saves nor removes untracked files, and does not checkpoint a repository with submodules.

## What it needs

- **git**, for `GitCheckpoint` only. G6 finds it on `PATH` (`shutil.which("git")`). Install git from git-scm.com and
  put it on `PATH`. Without it the checkpoint raises `G6_E_CSF_GIT_UNAVAILABLE`.
- **Z3**, for `check_z3` only: `pip install "g6[formal] @ git+https://github.com/basicisabc1994/g6solver"` (z3-solver). Everything else is pure Python on the standard
  library.

## Examples

A plan-act model whose bound is within 0.2 but not within 0.05, an LTL property on two traces, the composed bound, a
plan whose operations cannot be undone, and a model that does not sum to 1:

```python
from g6.csf import (BoundedAgent, CSFInputError, ResourceTriple, compose_bound, evaluate_ltl, parse_ltl,
                    reachability_bound, rollback_feasibility)

rows = [{"from": "plan", "to": "act", "probability": 1.0},
        {"from": "act", "to": "done", "probability": 0.95},
        {"from": "act", "to": "harm", "probability": 0.05},
        {"from": "done", "to": "done", "probability": 1.0},
        {"from": "harm", "to": "harm", "probability": 1.0}]
agent = BoundedAgent(states=["plan", "act", "done", "harm"], transitions=rows, initial_state="plan",
                     unsafe_states=["harm"], resources=ResourceTriple(N=3))
report = reachability_bound(agent, epsilon=0.2)
assert abs(report.vub - 0.1) < 1e-12 and report.meets_bound and report.completion_state == "qualified-draft"
assert reachability_bound(agent, epsilon=0.05).completion_state == "blocked-escalated"

never_harm = parse_ltl("G(!harm)")
assert evaluate_ltl(never_harm, [{"plan": True}, {"act": True}, {"done": True}])
assert not evaluate_ltl(never_harm, [{"plan": True}, {"act": True}, {"harm": True}])

assert compose_bound(0.1, 0.1) == 0.2
plan = rollback_feasibility(["file_write", "email_send", "teleport"], 3)
assert not plan.feasible and plan.irreversible_ops == ("email_send",) and plan.unknown_ops == ("teleport",)

try:
    BoundedAgent(["a"], [{"from": "a", "to": "a", "probability": 0.9}], "a", [], ResourceTriple(N=1))
    raise AssertionError("a state whose transitions do not sum to 1 is refused")
except CSFInputError as refusal:
    assert refusal.code == "G6_E_CSF_MODEL_INVALID"
```

The true probability of harm here is 0.05: the bound counts the agent in `harm` at steps 2 and 3.

## The four open problems

- **Learning signals**: Not yet: it persists nothing and writes nothing to a run ledger. Each `to_envelope()` carries
  the code, the model and its assumptions, which a caller can record as a labelled incident.
- **Evaluators the model cannot influence**: the bound, the LTL evaluator and the rollback table are fixed code, and a
  counterexample from Z3 is re-read by `evaluate_ltl` before it is reported. The model's numbers are the weak point:
  if a model wrote them, the bound inherits its errors.
- **Transfer by failure mode**: the checks take any agent written as states and transitions, whatever the task. Not
  yet: no catch rate has been measured on task families.
- **Enough held-out cases**: Not yet: the probabilities are the caller's, or exact upper bounds from one trace
  (`fit_from_trace`), and no bound has been compared with rates observed on held-out runs.
