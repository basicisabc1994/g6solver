# SPDX-License-Identifier: Apache-2.0
"""Symbolic state models checked with Z3 (owner, 2026-10-20): known answers with their oracles written here, the bound
said and never exceeded, k-induction reported with its k, vacuous models flagged, and the limits refused with their
own codes. Each oracle is either worked by hand (in the docstring) or computed by a small explicit-state search over a
finite domain, written in this file, that shares no code with the package."""
from __future__ import annotations

import importlib.util
from collections.abc import Callable, Iterable

import pytest

from g6.invariants import check_envelope
from g6.model_checking import check_deadlock, check_invariant, prove_invariant, reachable
from g6.model_checking.envelope import (
    ALL_PATHS_END,
    BOUNDED,
    DEADLOCK,
    INPUT_INVALID,
    INPUT_TOO_LARGE,
    K_INDUCTION,
    NOT_INDUCTIVE,
    PARSE_ERROR,
    TIME_LIMIT,
    TIME_LIMIT_TOO_LONG,
    TRACE_FOUND,
    UNKNOWN_SORT,
    VACUOUS_INIT,
    VACUOUS_TRANSITION,
    VIOLATION,
)

pytestmark = pytest.mark.skipif(importlib.util.find_spec("z3") is None,
                                reason="z3-solver is not installed (pip install 'g6[formal]')")

COUNTER = {"variables": {"x": "Int"}, "init": "(= x 0)", "transition": "(= next_x (+ x 1))",
           "invariants": {"small": "(<= x 3)"}}
# x: 0 -> 1 -> 0 -> ...; 2 -> 3 -> 3; any other value stays. 3 is unreachable, but 2 keeps the invariant and steps to 3.
TWO_INDUCTIVE = {"variables": {"x": "Int"}, "init": "(= x 0)",
                 "transition": "(= next_x (ite (= x 0) 1 (ite (= x 1) 0 (ite (or (= x 2) (= x 3)) 3 x))))",
                 "invariants": {"not_three": "(not (= x 3))"}}
MUTEX_STEPS = ("(or (and (= pc1 0) (= next_pc1 1) (= next_pc2 pc2) (= next_lock lock))"
               " (and (= pc1 1) (not lock) (= next_pc1 2) (= next_pc2 pc2) next_lock)"
               " (and (= pc1 2) (= next_pc1 0) (= next_pc2 pc2) (not next_lock))"
               " (and (= pc2 0) (= next_pc2 1) (= next_pc1 pc1) (= next_lock lock))"
               " (and (= pc2 1) (not lock) (= next_pc2 2) (= next_pc1 pc1) next_lock)"
               " (and (= pc2 2) (= next_pc2 0) (= next_pc1 pc1) (not next_lock)))")
MUTEX = {"variables": {"pc1": "Int", "pc2": "Int", "lock": "Bool"},
         "init": "(and (= pc1 0) (= pc2 0) (not lock))", "transition": MUTEX_STEPS,
         "invariants": {"mutex": "(not (and (= pc1 2) (= pc2 2)))"}}
DEADLOCKS = {"variables": {"x": "Int"}, "init": "(= x 0)", "transition": "(and (< x 2) (= next_x (+ x 1)))",
             "invariants": {"bounded": "(<= x 5)"}}


def xs(result) -> list:
    return [s["state"]["x"] for s in result.trace]


def _envelope(result) -> None:
    payload = result.model_dump(mode="json")
    assert check_envelope(payload) == [] and result.completion_state != "verified"
    assert result.warning_card["code"].startswith("G6_E_MODEL_CHECK_")


# --- the oracle: explicit-state search over a finite domain, independent of the package ------------------------------

def _least_k(states: Iterable, initial: Callable, successors: Callable, holds: Callable, most: int) -> int | None:
    """The least k at which k-induction (paths with no repeated state) proves ``holds``, or None up to ``most``.

    Base for k: no state breaking ``holds`` is reachable in fewer than k steps. Step for k: no path of k steps, every
    state on it different, keeps ``holds`` for its first k states and breaks it at the last."""
    states = list(states)
    reach, frontier = {s for s in states if initial(s)}, {s for s in states if initial(s)}
    depth_of = {s: 0 for s in reach}
    for d in range(1, most + 1):
        frontier = {t for s in frontier for t in successors(s) if t not in reach}
        reach |= frontier
        depth_of |= {t: d for t in frontier}

    def bad_path(k: int) -> bool:
        def walk(path: list) -> bool:
            if len(path) == k + 1:
                return not holds(path[-1])
            if not holds(path[-1]):
                return False
            return any(walk([*path, t]) for t in successors(path[-1]) if t not in path)
        return any(walk([s]) for s in states)

    for k in range(1, most + 1):
        if any(not holds(s) and depth_of[s] < k for s in reach):
            return None                                   # a real violation: no proof at any k
        if not bad_path(k):
            return k
    return None


def _two_inductive_step(x: int) -> list[int]:
    return [{0: 1, 1: 0, 2: 3, 3: 3}.get(x, x)]


def _mutex_step(s: tuple) -> list[tuple]:
    pc1, pc2, lock = s
    out = []
    for me, other, put in ((pc1, pc2, lambda a, b, lk: (a, b, lk)), (pc2, pc1, lambda a, b, lk: (b, a, lk))):
        if me == 0:
            out.append(put(1, other, lock))
        elif me == 1 and not lock:
            out.append(put(2, other, True))
        elif me == 2:
            out.append(put(0, other, False))
    return out


def test_the_oracles_agree_with_the_hand_worked_answers() -> None:
    """Hand-worked: TWO_INDUCTIVE's only step into x = 3 is from x = 2, and nothing steps to 2, so k = 2. MUTEX: a state
    with both in the critical section comes only from (2, 1, unlocked) or (1, 2, unlocked); each comes only from
    (2, 0, unlocked) or (0, 2, unlocked), which nothing that keeps the invariant steps to, so k = 3."""
    assert _least_k(range(-2, 8), lambda x: x == 0, _two_inductive_step, lambda x: x != 3, 10) == 2
    mutex_states = [(a, b, lk) for a in range(4) for b in range(4) for lk in (False, True)]
    assert _least_k(mutex_states, lambda s: s == (0, 0, False), _mutex_step, lambda s: s[:2] != (2, 2), 10) == 3
    # survival control: an invariant that fails has no k
    assert _least_k(range(0, 8), lambda x: x == 0, lambda x: [x + 1] if x < 7 else [], lambda x: x <= 3, 10) is None


# --- check_invariant: bounded model checking -------------------------------------------------------------------------

def test_the_counter_breaks_x_le_3_at_depth_4_with_the_trace_worked_by_hand() -> None:
    """Hand-worked: x = 0, 1, 2, 3 keep x <= 3; the fourth step gives x = 4, the first state that breaks it."""
    result = check_invariant(COUNTER, depth=10, request_id="q", run_id="r")
    assert result.verdict == "violated" and xs(result) == [0, 1, 2, 3, 4] and result.violated == ["small"]
    assert [s["step"] for s in result.trace] == [0, 1, 2, 3, 4] and result.trace_kind == "counterexample"
    assert result.completion_state == "blocked-escalated" and result.code == VIOLATION
    assert result.recheck["passed"] is True and result.recheck["failures"] == []
    assert result.engine == "z3" and result.engine_version and (result.request_id, result.run_id) == ("q", "r")
    _envelope(result)


def test_a_bound_below_the_violation_says_it_covers_only_its_depth() -> None:
    result = check_invariant(COUNTER, depth=3)
    assert result.verdict == "no_violation_within_bound" and result.trace == []
    assert result.completion_state == "qualified-draft" and result.warning_card["code"] == BOUNDED
    assert (result.depth, result.explored_depth, result.all_paths_end) == (3, 3, False)
    message = result.warning_card["message"]
    assert "up to 3 steps" in message and "not ruled out" in message and "every reachable state" not in message
    assert "not verified" in message and result.recheck["passed"] is True
    _envelope(result)


def test_when_every_path_ends_within_the_bound_the_search_says_so() -> None:
    result = check_invariant(DEADLOCKS, depth=10)
    assert result.verdict == "no_violation_within_bound" and result.all_paths_end is True
    assert (result.depth, result.explored_depth) == (10, 2) and result.warning_card["code"] == ALL_PATHS_END
    assert "every path from the initial states ends within 2 steps" in result.warning_card["message"]
    assert result.completion_state == "qualified-draft" and result.recheck["passed"] is True


def test_bit_vectors_wrap_and_reals_stay_exact() -> None:
    bits = check_invariant({"variables": {"b": "(_ BitVec 4)"}, "init": "(= b #x0)",
                            "transition": "(= next_b (bvadd b #x1))", "invariants": {"below_15": "(bvult b #xf)"}},
                           depth=20)
    assert bits.verdict == "violated" and [s["state"]["b"] for s in bits.trace] == list(range(16))
    halves = check_invariant({"variables": {"r": "Real", "on": "Bool"}, "init": "(and (= r 0.0) on)",
                              "transition": "(and (= next_r (+ r (/ 1.0 2.0))) (= next_on (not on)))",
                              "invariants": {"below_one": "(< r 1.0)"}}, depth=5)
    assert [s["state"]["r"] for s in halves.trace] == ["0", "1/2", "1"]
    assert [s["state"]["on"] for s in halves.trace] == [True, False, True] and halves.recheck["passed"] is True


def test_the_violated_names_are_those_false_in_the_last_state() -> None:
    model = {**COUNTER, "invariants": {"small": "(<= x 3)", "tiny": "(<= x 1)", "natural": "(>= x 0)"}}
    result = check_invariant(model, depth=10)
    assert xs(result) == [0, 1, 2] and result.violated == ["tiny"]


# --- prove_invariant: k-induction --------------------------------------------------------------------------------------

def test_the_mutual_exclusion_invariant_is_proved_at_the_oracles_k() -> None:
    mutex_states = [(a, b, lk) for a in range(4) for b in range(4) for lk in (False, True)]
    expected = _least_k(mutex_states, lambda s: s == (0, 0, False), _mutex_step, lambda s: s[:2] != (2, 2), 10)
    result = prove_invariant(MUTEX, depth=10)
    assert expected == 3 and result.verdict == "k_inductive" and result.k == expected
    assert result.completion_state == "qualified-draft" and result.warning_card["code"] == K_INDUCTION
    message = result.warning_card["message"]
    assert "k-induction at k = 3" in message and "not verified" in message and result.trace == []
    assert result.recheck["passed"] is True and {c["expected"] for c in result.recheck["checks"]} == {"unsat"}
    _envelope(result)


def test_a_strengthened_mutex_is_one_inductive() -> None:
    model = {**MUTEX, "invariants": {**MUTEX["invariants"], "one_holds": "(=> (= pc1 2) lock)",
                                     "two_holds": "(=> (= pc2 2) lock)"}}
    result = prove_invariant(model)
    assert result.verdict == "k_inductive" and result.k == 1


def test_an_invariant_that_is_two_inductive_but_not_one_inductive() -> None:
    expected = _least_k(range(-2, 8), lambda x: x == 0, _two_inductive_step, lambda x: x != 3, 10)
    proved = prove_invariant(TWO_INDUCTIVE, depth=5)
    assert expected == 2 and proved.verdict == "k_inductive" and proved.k == 2
    stuck = prove_invariant(TWO_INDUCTIVE, depth=1)
    assert stuck.verdict == "not_inductive" and stuck.k == 1 and stuck.completion_state == "blocked-escalated"
    assert stuck.code == NOT_INDUCTIVE and stuck.trace_kind == "induction_step" and xs(stuck) == [2, 3]
    reason = stuck.reason + " " + stuck.warning_card["message"]
    assert "not a counterexample" in reason and "need not be reachable" in reason
    assert stuck.recheck["passed"] is True
    _envelope(stuck)


def test_a_stutter_step_does_not_stop_the_proof() -> None:
    """With x' = x allowed everywhere, plain k-induction would always find 2, 2, ..., 2, 3. Paths with a repeated state
    are left out of the induction step, so the proof still closes at k = 2."""
    model = {**TWO_INDUCTIVE, "transition": f"(or {TWO_INDUCTIVE['transition']} (= next_x x))"}
    result = prove_invariant(model, depth=5)
    assert result.verdict == "k_inductive" and result.k == 2


def test_prove_reports_a_real_violation_as_a_counterexample_from_the_initial_states() -> None:
    result = prove_invariant(COUNTER, depth=10)
    assert result.verdict == "violated" and xs(result) == [0, 1, 2, 3, 4] and result.trace_kind == "counterexample"
    assert result.code == VIOLATION and result.completion_state == "blocked-escalated"


# --- check_deadlock and reachable -----------------------------------------------------------------------------------------

def test_a_deadlocking_model_stops_at_x_2() -> None:
    """Hand-worked: from 0 and 1 the guard x < 2 holds and x goes up by one; at x = 2 no next state satisfies it."""
    result = check_deadlock(DEADLOCKS, depth=10)
    assert result.verdict == "deadlock" and xs(result) == [0, 1, 2] and result.trace_kind == "deadlock"
    assert result.code == DEADLOCK and result.completion_state == "blocked-escalated"
    assert result.recheck["passed"] is True
    last = [c for c in result.recheck["checks"] if "no successor" in c["what"]]
    assert len(last) == 1 and last[0]["expected"] == "unsat" and last[0]["answer"] == "unsat"
    _envelope(result)


def test_the_mutex_has_no_deadlock_within_its_bound() -> None:
    result = check_deadlock(MUTEX, depth=6)
    assert result.verdict == "no_deadlock_within_bound" and result.warning_card["code"] == BOUNDED
    assert "up to 6 steps" in result.warning_card["message"] and result.completion_state == "qualified-draft"


def test_reachable_finds_a_witness_and_says_when_none_is_within_reach() -> None:
    found = reachable(COUNTER, "(= x 3)", depth=10)
    assert found.verdict == "reached" and xs(found) == [0, 1, 2, 3] and found.trace_kind == "witness"
    assert found.completion_state == "qualified-draft" and found.warning_card["code"] == TRACE_FOUND
    assert found.recheck["passed"] is True
    none = reachable(COUNTER, "(< x 0)", depth=5)
    assert none.verdict == "not_reached_within_bound" and none.warning_card["code"] == BOUNDED
    assert "up to 5 steps" in none.warning_card["message"]
    _envelope(found)
    _envelope(none)


# --- vacuous models ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("run", [
    lambda m: check_invariant(m), lambda m: prove_invariant(m), lambda m: check_deadlock(m),
    lambda m: reachable(m, "(= x 1)"),
])
def test_an_unsatisfiable_initial_condition_is_flagged_vacuous(run) -> None:
    result = run({**COUNTER, "init": "(and (> x 0) (< x 0))"})
    assert result.code == VACUOUS_INIT and result.completion_state == "blocked-escalated"
    assert "no initial state" in result.reason and result.trace == [] and result.verdict == ""
    _envelope(result)


@pytest.mark.parametrize("transition", ["false", "(and (= next_x 1) (= next_x 2))"])
@pytest.mark.parametrize("run", [
    lambda m: check_invariant(m), lambda m: prove_invariant(m), lambda m: check_deadlock(m),
    lambda m: reachable(m, "(= x 1)"),
])
def test_a_transition_with_no_successor_anywhere_is_flagged_vacuous(transition: str, run) -> None:
    result = run({**COUNTER, "transition": transition})
    assert result.code == VACUOUS_TRANSITION and result.completion_state == "blocked-escalated"
    assert "no state has a successor" in result.reason


# --- refusals and limits ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("model", "code", "says"), [
    ({**COUNTER, "variables": {"x": "String"}}, UNKNOWN_SORT, "Int, Real, Bool"),
    ({**COUNTER, "variables": {"x": "(Array Int Int)"}}, UNKNOWN_SORT, "Int, Real, Bool"),
    ({**COUNTER, "variables": {"x": "Int", "next_y": "Int"}}, INPUT_INVALID, "next_"),
    ({**COUNTER, "variables": {}}, INPUT_INVALID, "at least one"),
    ({**COUNTER, "init": "(= next_x 0)"}, PARSE_ERROR, "only the transition"),
    ({**COUNTER, "init": "(set-option :regular-output-channel \"x\")"}, PARSE_ERROR, ""),
    ({**COUNTER, "invariants": {"small": "(+ x 1)"}}, PARSE_ERROR, ""),
    ({**COUNTER, "invariants": {"bad name": "(<= x 3)"}}, INPUT_INVALID, "plain name"),
    ({**COUNTER, "invariants": {}}, INPUT_INVALID, "invariant"),
    ({**COUNTER, "transition": ""}, PARSE_ERROR, ""),
    ({**COUNTER, "code": "import os"}, INPUT_INVALID, "code"),
    ({**COUNTER, "variables": {f"v{i}": "Int" for i in range(65)}}, INPUT_TOO_LARGE, "64"),
    ({**COUNTER, "invariants": {f"i{i}": "(<= x 3)" for i in range(51)}}, INPUT_TOO_LARGE, "50"),
    ({**COUNTER, "init": "(and " + "(= x 0) " * 2000 + ")"}, INPUT_TOO_LARGE, "10000"),
])
def test_a_model_outside_the_limits_or_the_grammar_is_refused(model: dict, code: str, says: str) -> None:
    result = check_invariant(model)
    assert result.code == code and result.completion_state == "blocked-escalated", (result.code, result.reason)
    assert says in result.reason and result.trace == []
    _envelope(result)


@pytest.mark.parametrize(("kwargs", "code"), [
    ({"depth": 51}, INPUT_TOO_LARGE), ({"depth": 0}, INPUT_INVALID), ({"depth": True}, INPUT_INVALID),
    ({"time_limit": 61}, TIME_LIMIT_TOO_LONG), ({"time_limit": 0}, INPUT_INVALID),
    ({"time_limit": float("nan")}, INPUT_INVALID), ({"time_limit": 1e-9}, TIME_LIMIT),
])
def test_depth_and_time_limits_are_refused_with_their_own_codes(kwargs: dict, code: str) -> None:
    result = check_invariant(COUNTER, **kwargs)
    assert result.code == code and result.completion_state == "blocked-escalated", (result.code, result.reason)


def test_reachable_needs_a_goal_and_reads_it_through_the_same_grammar() -> None:
    assert reachable(COUNTER, "").code == PARSE_ERROR
    assert reachable(COUNTER, "(= next_x 1)").code == PARSE_ERROR
    assert reachable({k: v for k, v in COUNTER.items() if k != "invariants"}, "(= x 2)").verdict == "reached"
