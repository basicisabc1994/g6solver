# SPDX-License-Identifier: Apache-2.0
"""G6's own re-check of every symbolic answer, before it is reported.

A trace is checked state by state, each check one query in g6.formal's closed grammar decided by a fresh Z3 solver
(:func:`g6.formal.z3_bridge.run_query`) with every variable fixed to the trace's value, so each query asks only whether
a term holds at those values. The queries are kept in the report, so anyone can run them again:

* ``counterexample``: state 0 satisfies init, each step satisfies the transition, and each invariant the result names
  is false in the last state;
* ``deadlock``: the same first two, and the last state has no successor (with the next-state copies left free, the
  transition is unsatisfiable);
* ``witness``: the same first two, and the last state meets the goal;
* ``induction_step``: every state but the last keeps every invariant, each step satisfies the transition, no two states
  are the same, and the last state breaks an invariant. It need not start in init, which is why it is not a
  counterexample.

A claim that nothing was found, that every path ends, or a k-induction proof is decided again in a second Z3 context,
built anew from the model's canonical text, on solvers that never saw the search. Each failure is named; one failure
fails the re-check, and :func:`~g6.model_checking.envelope.finish` then blocks the answer with
``G6_E_MODEL_CHECK_CERTIFICATE_REFUTED``.
"""
from __future__ import annotations

import math
import time
from collections.abc import Mapping, Sequence
from typing import Any

from g6.formal import envelope as formal
from g6.formal.z3_bridge import run_query
from g6.model_checking.encoding import NEXT, Encoding, Model, equalities, from_formal, queries, read_model, z3_module
from g6.model_checking.envelope import (
    INPUT_INVALID,
    SOLVER_ERROR,
    SOLVER_UNKNOWN,
    TIME_LIMIT,
    TIME_LIMIT_TOO_LONG,
    Refusal,
    induction,
    steps,
)

KINDS = ("counterexample", "deadlock", "witness", "induction_step")
DEFAULT_TIME_LIMIT_S = 10.0
MAX_TIME_LIMIT_S = 60.0
#: Query text kept in a report, in all; past it, checks are still made but their queries are not kept.
MAX_QUERY_TEXT = 200_000


class Clock:
    """One deadline for a whole op: every solver call gets what is left of it."""

    def __init__(self, seconds: Any) -> None:
        if isinstance(seconds, bool) or not isinstance(seconds, int | float) or not math.isfinite(seconds) \
                or seconds <= 0:
            raise Refusal(INPUT_INVALID, f"time_limit must be a positive number of seconds, not {seconds!r}")
        if seconds > MAX_TIME_LIMIT_S:
            raise Refusal(TIME_LIMIT_TOO_LONG, f"time_limit {seconds:g} s is above the cap of {MAX_TIME_LIMIT_S:g} s")
        # perf_counter, not monotonic: on Windows monotonic ticks every 15.6 ms, so a short deadline could look unmet
        self.deadline = time.perf_counter() + float(seconds)

    def seconds(self) -> float:
        left = self.deadline - time.perf_counter()
        if left <= 0:
            raise Refusal(TIME_LIMIT, "the check did not finish within the time limit")
        return left

    def ms(self) -> int:
        return max(1, int(self.seconds() * 1000))


class _Report:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []
        self.failures: list[str] = []
        self._text = 0

    def add(self, what: str, expected: str, answer: str, query: str = "", failure: str = "") -> None:
        entry: dict[str, Any] = {"what": what, "expected": expected, "answer": answer}
        if query and self._text + len(query) <= MAX_QUERY_TEXT:
            entry["query"] = query
            self._text += len(query)
        elif query:
            entry["query_omitted"] = True
        self.checks.append(entry)
        if answer != expected:
            self.failures.append(failure or what)

    def fail(self, failure: str) -> None:
        self.failures.append(failure)

    def as_dict(self) -> dict[str, Any]:
        return {"passed": not self.failures and bool(self.checks), "checks": self.checks, "failures": self.failures}


def _decide(query: str, clock: Clock) -> str:
    try:
        answer, _, why = run_query(query, clock.seconds())
    except formal.Refusal as exc:
        raise from_formal(exc) from None
    if answer == "unknown":
        raise Refusal(TIME_LIMIT if "timeout" in why or "canceled" in why else SOLVER_UNKNOWN,
                      f"z3 returned unknown while re-checking ({why or 'no reason given'}); unknown is not an answer")
    return answer


def _states(model: Model, trace: Any) -> list[dict[str, Any]]:
    """The trace's states, in order; ``ValueError`` saying what is wrong with its shape."""
    if isinstance(trace, str | bytes) or not isinstance(trace, Sequence) or not trace:
        raise ValueError("the trace is empty or not a list of states")
    states = []
    for i, entry in enumerate(trace):
        if not isinstance(entry, Mapping) or entry.get("step") != i or not isinstance(entry.get("state"), Mapping):
            raise ValueError(f"entry {i} of the trace is not {{\"step\": {i}, \"state\": {{...}}}}: the steps must be "
                             "numbered from 0 in order")
        states.append(dict(entry["state"]))
    for state in states:
        equalities(model, state)  # each value of its variable's sort, every variable given
    return states


def _trace_checks(report: _Report, model: Model, trace: Any, kind: str, violated: Sequence[str],
                  clock: Clock) -> None:
    try:
        states = _states(model, trace)
    except ValueError as exc:
        report.fail(f"the trace is malformed: {exc}")
        return
    last = len(states) - 1
    here = [equalities(model, s) for s in states]
    there = [equalities(model, s, NEXT) for s in states]
    if kind == "induction_step":
        for i in range(last):
            for name, term in model.invariants.items():
                query = queries(model, [*here[i], term])
                report.add(f"state {i} keeps {name}", "sat", _decide(query, clock), query,
                           f"state {i} does not keep {name}, so the trace is not an induction step")
        for i in range(len(states)):
            for j in range(i + 1, len(states)):
                if states[i] == states[j]:
                    report.fail(f"states {i} and {j} are the same; an induction step repeats no state")
    else:
        query = queries(model, [*here[0], model.init])
        report.add("state 0 satisfies init", "sat", _decide(query, clock), query, "state 0 does not satisfy init")
    for i in range(last):
        query = queries(model, [*here[i], *there[i + 1], model.transition], with_next=True)
        report.add(f"step {i} to {i + 1} satisfies the transition", "sat", _decide(query, clock), query,
                   f"step {i} to {i + 1} does not satisfy the transition")
    if kind in ("counterexample", "induction_step"):
        if not violated:
            report.fail(f"the trace names no invariant that state {last} violates")
        for name in violated:
            if name not in model.invariants:
                report.fail(f"{name} is not an invariant of the model")
                continue
            query = queries(model, [*here[last], f"(not {model.invariants[name]})"])
            report.add(f"state {last} violates {name}", "sat", _decide(query, clock), query,
                       f"state {last} does not violate {name}")
    elif kind == "deadlock":
        query = queries(model, [*here[last], model.transition], with_next=True)
        report.add(f"state {last} has no successor", "unsat", _decide(query, clock), query,
                   f"state {last} has a successor, so it is not a deadlock")
    else:
        query = queries(model, [*here[last], model.goal])
        report.add(f"state {last} meets the goal", "sat", _decide(query, clock), query,
                   f"state {last} does not meet the goal")


def _fresh(enc: Encoding, terms: list[Any], clock: Clock) -> str:
    """Decide ``terms`` together on a new solver in ``enc``'s own context."""
    z3 = enc.z3
    solver = z3.Solver(ctx=enc.ctx)
    solver.set("timeout", clock.ms())
    solver.add(*terms)
    try:
        answer = solver.check()
    except z3.Z3Exception as exc:
        raise Refusal(SOLVER_ERROR, f"z3 failed while re-checking: {exc}"[:500]) from None
    if answer == z3.unknown:
        why = solver.reason_unknown()
        raise Refusal(TIME_LIMIT if "timeout" in why or "canceled" in why else SOLVER_UNKNOWN,
                      f"z3 returned unknown while re-checking ({why or 'no reason given'}); unknown is not an answer")
    return str(answer)


_TARGET = {"no_violation_within_bound": ("invariant", "a state that breaks the invariants"),
           "no_deadlock_within_bound": ("deadlock", "a state with no successor"),
           "not_reached_within_bound": ("goal", "a state that meets the goal")}


def _claim_checks(report: _Report, model: Model, outcome: Any, clock: Clock) -> None:
    """Decide the outcome's unsatisfiable claims again, in a second Z3 context built from the canonical text."""
    verdict = outcome.verdict
    if verdict not in _TARGET and not (verdict in ("k_inductive", "not_inductive") and outcome.k):
        return
    enc = Encoding(z3_module(), model)
    z3 = enc.z3
    if verdict in _TARGET:
        kind, what = _TARGET[verdict]
        depth = outcome.explored
        report.add(f"no path of at most {steps(depth)} from init reaches {what}", "unsat",
                   _fresh(enc, [enc.at(enc.init, 0), enc.none_within(kind, depth)], clock),
                   failure=f"a fresh Z3 context finds a path of at most {steps(depth)} to {what}, so the claim that "
                           "there is none is false")
        if outcome.all_paths_end:
            report.add(f"no path from init is longer than {steps(depth)}", "unsat",
                       _fresh(enc, [enc.at(enc.init, 0), *enc.path(depth + 1)], clock),
                       failure=f"a fresh Z3 context finds a path longer than {steps(depth)}, so the paths do not all "
                               "end there")
    elif verdict in ("k_inductive", "not_inductive") and outcome.k:
        k = outcome.k
        report.add(f"base: {induction(k)[0].replace('them', 'the invariants')}", "unsat",
                   _fresh(enc, [enc.at(enc.init, 0), enc.none_within("invariant", k - 1)], clock),
                   failure=f"the base case fails in a fresh Z3 context: a path of at most {steps(k - 1)} from the "
                           "initial states breaks the invariants")
        if verdict == "k_inductive":
            kept = [enc.invariant(i) for i in range(k)]
            apart = [enc.different(i, j) for j in range(k + 1) for i in range(j)]
            report.add(f"induction step at k = {k}", "unsat",
                       _fresh(enc, [*kept, *enc.path(k), *apart, z3.Not(enc.invariant(k))], clock),
                       failure=f"the induction step at k = {k} is satisfiable in a fresh Z3 context, so the invariants "
                               f"are not {k}-inductive")


def recheck(model: Model, outcome: Any, clock: Clock) -> dict[str, Any]:
    """G6's re-check of a search's ``outcome`` on ``model``: its trace, then its unsatisfiable claims."""
    report = _Report()
    if outcome.trace:
        _trace_checks(report, model, outcome.trace, outcome.kind, outcome.violated, clock)
    _claim_checks(report, model, outcome, clock)
    if not report.checks and not report.failures:
        report.fail(f"there is nothing to re-check in a {outcome.verdict or 'blank'} answer")
    return report.as_dict()


def recheck_trace(model: Any, trace: Any, kind: str, *, goal: str = "", violated: Sequence[str] = (),
                  time_limit: float = DEFAULT_TIME_LIMIT_S) -> dict[str, Any]:
    """Re-check one ``trace`` of ``kind`` (counterexample, deadlock, witness or induction_step) against ``model``,
    state by state on fresh solvers: ``{"passed", "checks", "failures"}``. A malformed trace fails; it does not raise.
    ``Refusal`` only for a model that cannot be read, a missing Z3 or the time limit."""
    if kind not in KINDS:
        raise Refusal(INPUT_INVALID, f"kind must be one of {', '.join(KINDS)}, not {kind!r}")
    clock = Clock(time_limit)
    read = read_model(model, need_invariants=kind in ("counterexample", "induction_step"),
                      goal=goal if kind == "witness" else None)
    z3_module()
    report = _Report()
    _trace_checks(report, read, trace, kind, list(violated), clock)
    return report.as_dict()


__all__ = ["Clock", "DEFAULT_TIME_LIMIT_S", "KINDS", "MAX_TIME_LIMIT_S", "recheck", "recheck_trace"]
