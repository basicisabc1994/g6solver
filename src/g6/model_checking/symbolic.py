# SPDX-License-Identifier: Apache-2.0
"""The four symbolic ops of g6.model_checking, each returning one labelled
:class:`~g6.model_checking.schema.ModelCheckResult`. Z3 in process (``pip install 'g6[formal]'``, imported when an op
runs); the model is read by :mod:`g6.model_checking.encoding`.

* :func:`check_invariant`: bounded model checking to ``depth`` steps. A state breaking an invariant comes back with
  its trace from an initial state; none found covers paths of up to ``depth`` steps only, and says so;
* :func:`prove_invariant`: k-induction for k = 1 up to ``depth``. For each k the base case asks for a path of fewer than
  k steps from the initial states that breaks the invariants (found: a counterexample), and the induction step asks for
  k states in a row, all different, that keep them followed by one that breaks them (none: the verdict
  ``k_inductive``, with that k). Leaving out paths that repeat a state keeps the step sound and stops a stutter step
  from blocking it (Sheeran, Singh and Stålmarck, 2000). Not k-inductive for any k up to ``depth`` is "not inductive at
  k", with the last induction-step trace, which need not start in a reachable state and is labelled so;
* :func:`check_deadlock`: a reachable state with no successor, within ``depth`` steps;
* :func:`reachable`: a path to a state meeting ``goal``, within ``depth`` steps.

Before any search, a model whose init no state satisfies, or whose transition no state has a successor under, is
refused as vacuous, as g6.formal refuses vacuous premises. A search that runs out of paths before ``depth`` (every
path ends sooner) says so, since it then covered every reachable state. Every answer is re-checked by
:mod:`g6.model_checking.checks` before :func:`~g6.model_checking.envelope.finish` labels it, and one time limit (at
most 60 seconds) covers the search and the re-check together.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from g6.model_checking import checks
from g6.model_checking.checks import DEFAULT_TIME_LIMIT_S, MAX_TIME_LIMIT_S, Clock
from g6.model_checking.encoding import Encoding, Model, read_model, z3_module
from g6.model_checking.envelope import (
    INPUT_INVALID,
    INPUT_TOO_LARGE,
    SOLVER_ERROR,
    SOLVER_UNKNOWN,
    TIME_LIMIT,
    VACUOUS_INIT,
    VACUOUS_TRANSITION,
    Refusal,
    induction,
    steps,
)
from g6.model_checking.envelope import labelled as _run
from g6.model_checking.schema import ModelCheckResult

DEFAULT_DEPTH = 10
MAX_DEPTH = 50
_FOUND = {"invariant": ("violated", "no_violation_within_bound", "counterexample"),
          "deadlock": ("deadlock", "no_deadlock_within_bound", "deadlock"),
          "goal": ("reached", "not_reached_within_bound", "witness")}


@dataclass
class Outcome:
    """What a search reports, before G6's re-check: its verdict, a trace (``[{"step": i, "state": {...}}]``) and how far
    it went."""

    verdict: str
    trace: list[dict[str, Any]] = field(default_factory=list)
    explored: int = 0
    all_paths_end: bool = False
    k: int | None = None
    violated: list[str] = field(default_factory=list)
    kind: str = ""


def _depth(depth: Any) -> int:
    if isinstance(depth, bool) or not isinstance(depth, int) or depth < 1:
        raise Refusal(INPUT_INVALID, f"depth is a whole number of steps from 1 to {MAX_DEPTH}, not {depth!r}")
    if depth > MAX_DEPTH:
        raise Refusal(INPUT_TOO_LARGE, f"depth {depth} is above the limit of {MAX_DEPTH} steps")
    return depth


def _check(enc: Encoding, solver: Any, clock: Clock) -> str:
    z3 = enc.z3
    solver.set("timeout", clock.ms())
    try:
        answer = solver.check()
    except z3.Z3Exception as exc:
        raise Refusal(SOLVER_ERROR, f"z3 failed: {exc}"[:500]) from None
    if answer == z3.sat:
        return "sat"
    if answer == z3.unsat:
        return "unsat"
    why = solver.reason_unknown()
    if "timeout" in why or "canceled" in why:
        raise Refusal(TIME_LIMIT, "the search did not finish within the time limit")
    raise Refusal(SOLVER_UNKNOWN, f"z3 returned unknown ({why or 'no reason given'}); unknown is not an answer")


def _trace(enc: Encoding, solver: Any, last: int) -> list[dict[str, Any]]:
    found = solver.model()
    return [{"step": i, "state": enc.state(found, i)} for i in range(last + 1)]


def _broken(enc: Encoding, solver: Any, i: int) -> list[str]:
    found = solver.model()
    return [n for n in enc.invariants if enc.z3.is_false(found.evaluate(enc.named(n, i), model_completion=True))]


def _bmc(enc: Encoding, kind: str, depth: int, clock: Clock) -> Outcome:
    """Bounded model checking: at each depth j up to ``depth``, is there a path of j steps from an initial state to the
    target? A path of j steps that cannot exist at all means every path ended by j - 1."""
    found, none, trace_kind = _FOUND[kind]
    solver = enc.z3.Solver(ctx=enc.ctx)
    solver.add(enc.at(enc.init, 0))
    for j in range(depth + 1):
        if j > 0 and _check(enc, solver, clock) == "unsat":
            return Outcome(verdict=none, explored=j - 1, all_paths_end=True)
        solver.push()
        solver.add(enc.target(kind, j))
        if _check(enc, solver, clock) == "sat":
            broken = _broken(enc, solver, j) if kind == "invariant" else []
            return Outcome(verdict=found, trace=_trace(enc, solver, j), explored=j, violated=broken, kind=trace_kind)
        solver.pop()
        if j < depth:
            solver.add(enc.step(j))
    return Outcome(verdict=none, explored=depth)


def _k_induction(enc: Encoding, depth: int, clock: Clock) -> Outcome:
    """k-induction for k = 1 to ``depth``, the induction step over paths with no repeated state."""
    z3 = enc.z3
    base, step = z3.Solver(ctx=enc.ctx), z3.Solver(ctx=enc.ctx)
    base.add(enc.at(enc.init, 0))
    step.add(enc.invariant(0))
    last: Outcome | None = None
    for k in range(1, depth + 1):
        j = k - 1
        if j > 0:
            base.add(enc.step(j - 1))
        base.push()
        base.add(z3.Not(enc.invariant(j)))
        if _check(enc, base, clock) == "sat":
            return Outcome(verdict="violated", trace=_trace(enc, base, j), explored=j, violated=_broken(enc, base, j),
                           kind="counterexample")
        base.pop()
        step.add(enc.step(k - 1), *[enc.different(i, k) for i in range(k)])
        step.push()
        step.add(z3.Not(enc.invariant(k)))
        if _check(enc, step, clock) == "unsat":
            return Outcome(verdict="k_inductive", explored=k - 1, k=k)
        last = Outcome(verdict="not_inductive", trace=_trace(enc, step, k), explored=k - 1, k=k,
                       violated=_broken(enc, step, k), kind="induction_step")
        step.pop()
        step.add(enc.invariant(k))
    assert last is not None  # depth >= 1
    return last


def _prepare(state: dict[str, Any], model: Any, depth: Any, time_limit: Any, *, need_invariants: bool,
             goal: Any = None) -> tuple[Model, Encoding, int, Clock]:
    """The limits checked, the model read, Z3 loaded and the model refused if it is vacuous."""
    clock = Clock(time_limit)
    state["depth"] = _depth(depth)
    read = read_model(model, need_invariants=need_invariants, goal=goal)
    state["model"] = read.summary()
    z3 = z3_module()
    state |= {"engine": "z3", "engine_version": str(z3.get_version_string())}
    enc = Encoding(z3, read)
    starts = z3.Solver(ctx=enc.ctx)
    starts.add(enc.at(enc.init, 0))
    if _check(enc, starts, clock) == "unsat":
        raise Refusal(VACUOUS_INIT, "no initial state satisfies init, so every claim about the model would hold "
                                    "vacuously")
    moves = z3.Solver(ctx=enc.ctx)
    moves.add(enc.step(0))
    if _check(enc, moves, clock) == "unsat":
        raise Refusal(VACUOUS_TRANSITION, "no state has a successor under the transition, so the model never moves and "
                                          "every claim about its paths would hold vacuously")
    return read, enc, state["depth"], clock


def _reason(outcome: Outcome) -> str:
    length = steps(len(outcome.trace) - 1)
    if outcome.verdict == "violated":
        names = ", ".join(outcome.violated) or "the invariants"
        return (f"{names} {'is' if len(outcome.violated) == 1 else 'are'} violated after {length}: the trace is a "
                "counterexample from an initial state, checked state by state on fresh solvers")
    if outcome.verdict == "deadlock":
        return (f"a state reachable in {length} has no successor: the trace leads to it from an initial state, checked "
                "state by state on fresh solvers")
    if outcome.verdict == "not_inductive":
        k = outcome.k or 1
        base, _ = induction(k)
        lead = "a state that keeps them" if k == 1 else f"{k} different states that keep them"
        return (f"the invariants are not inductive at k = {k}: the trace is an induction step, {lead} followed by one "
                "that breaks them. Its first state need not be reachable, so it is not a counterexample. As far as the "
                f"base case goes, {base}.")
    return ""


def _report(state: dict[str, Any], model: Model, outcome: Outcome, clock: Clock) -> dict[str, Any]:
    state |= {"verdict": outcome.verdict, "explored_depth": outcome.explored, "all_paths_end": outcome.all_paths_end,
              "k": outcome.k, "violated": outcome.violated, "trace": outcome.trace, "trace_kind": outcome.kind}
    state["recheck"] = checks.recheck(model, outcome, clock)
    state["reason"] = _reason(outcome)
    return state


def check_invariant(model: Any, *, depth: int = DEFAULT_DEPTH, time_limit: float = DEFAULT_TIME_LIMIT_S,
                    request_id: str = "", run_id: str = "") -> ModelCheckResult:
    """Is there a path of at most ``depth`` steps from an initial state to a state that breaks an invariant?"""
    def work(state: dict[str, Any]) -> dict[str, Any]:
        read, enc, k, clock = _prepare(state, model, depth, time_limit, need_invariants=True)
        return _report(state, read, _bmc(enc, "invariant", k, clock), clock)

    return _run("check_invariant", work, request_id, run_id)


def prove_invariant(model: Any, *, depth: int = DEFAULT_DEPTH, time_limit: float = DEFAULT_TIME_LIMIT_S,
                    request_id: str = "", run_id: str = "") -> ModelCheckResult:
    """Do the invariants hold in every reachable state? k-induction for k = 1 up to ``depth``."""
    def work(state: dict[str, Any]) -> dict[str, Any]:
        read, enc, k, clock = _prepare(state, model, depth, time_limit, need_invariants=True)
        return _report(state, read, _k_induction(enc, k, clock), clock)

    return _run("prove_invariant", work, request_id, run_id)


def check_deadlock(model: Any, *, depth: int = DEFAULT_DEPTH, time_limit: float = DEFAULT_TIME_LIMIT_S,
                   request_id: str = "", run_id: str = "") -> ModelCheckResult:
    """Is a state with no successor reachable in at most ``depth`` steps?"""
    def work(state: dict[str, Any]) -> dict[str, Any]:
        read, enc, k, clock = _prepare(state, model, depth, time_limit, need_invariants=False)
        return _report(state, read, _bmc(enc, "deadlock", k, clock), clock)

    return _run("check_deadlock", work, request_id, run_id)


def reachable(model: Any, goal: str, *, depth: int = DEFAULT_DEPTH, time_limit: float = DEFAULT_TIME_LIMIT_S,
              request_id: str = "", run_id: str = "") -> ModelCheckResult:
    """Is a state meeting ``goal`` (a term over the state variables) reachable in at most ``depth`` steps?"""
    def work(state: dict[str, Any]) -> dict[str, Any]:
        read, enc, k, clock = _prepare(state, model, depth, time_limit, need_invariants=False, goal=goal)
        return _report(state, read, _bmc(enc, "goal", k, clock), clock)

    return _run("reachable", work, request_id, run_id)


__all__ = ["DEFAULT_DEPTH", "DEFAULT_TIME_LIMIT_S", "MAX_DEPTH", "MAX_TIME_LIMIT_S", "Outcome", "check_deadlock",
           "check_invariant", "prove_invariant", "reachable"]
