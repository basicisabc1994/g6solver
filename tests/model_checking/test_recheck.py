# SPDX-License-Identifier: Apache-2.0
"""Every answer is checked again before it is reported (owner, 2026-10-20). A trace is re-checked state by state on a
fresh solver: the first state satisfies init, each step the transition, and the last state violates the invariant, has
no successor or meets the goal. A claim that nothing was found, or a k-induction proof, is decided again in a second Z3
context. Each re-check is fed a planted wrong answer it must refute, and an op whose search is made to lie is blocked
with ``G6_E_MODEL_CHECK_CERTIFICATE_REFUTED``."""
from __future__ import annotations

import importlib.util

import pytest

from g6.formal.z3_bridge import run_query
from g6.invariants import check_envelope
from g6.model_checking import (
    check_deadlock,
    check_invariant,
    prove_invariant,
    reachable,
    recheck_trace,
    symbolic,
)
from g6.model_checking.envelope import CERTIFICATE_REFUTED
from g6.model_checking.symbolic import Outcome

pytestmark = pytest.mark.skipif(importlib.util.find_spec("z3") is None,
                                reason="z3-solver is not installed (pip install 'g6[formal] @ git+https://github.com/basicisabc1994/g6solver')")

COUNTER = {"variables": {"x": "Int"}, "init": "(= x 0)", "transition": "(= next_x (+ x 1))",
           "invariants": {"small": "(<= x 3)"}}
DEADLOCKS = {"variables": {"x": "Int"}, "init": "(= x 0)", "transition": "(and (< x 2) (= next_x (+ x 1)))",
             "invariants": {"bounded": "(<= x 5)"}}
TWO_INDUCTIVE = {"variables": {"x": "Int"}, "init": "(= x 0)",
                 "transition": "(= next_x (ite (= x 0) 1 (ite (= x 1) 0 (ite (or (= x 2) (= x 3)) 3 x))))",
                 "invariants": {"not_three": "(not (= x 3))"}}


def trace(*values: int) -> list[dict]:
    return [{"step": i, "state": {"x": v}} for i, v in enumerate(values)]


def failed(report: dict, words: str) -> bool:
    return report["passed"] is False and any(words in f for f in report["failures"])


# --- the trace re-checks, each with a planted wrong trace -------------------------------------------------------------

@pytest.mark.parametrize(("model", "values", "kind", "goal", "violated"), [
    (COUNTER, (0, 1, 2, 3, 4), "counterexample", "", ["small"]),
    (DEADLOCKS, (0, 1, 2), "deadlock", "", []),
    (COUNTER, (0, 1, 2), "witness", "(= x 2)", []),
    (TWO_INDUCTIVE, (2, 3), "induction_step", "", ["not_three"]),
])
def test_a_true_trace_passes_its_recheck(model, values, kind, goal, violated) -> None:
    report = recheck_trace(model, trace(*values), kind, goal=goal, violated=violated)
    assert report["passed"] is True and report["failures"] == [], report["failures"]
    assert all(c["answer"] == c["expected"] for c in report["checks"])


def test_each_recorded_query_decides_the_same_on_a_fresh_solver() -> None:
    """The queries in the report are g6.formal's closed grammar: anyone can run them again with run_query."""
    report = recheck_trace(COUNTER, trace(0, 1, 2, 3, 4), "counterexample", violated=["small"])
    for check in report["checks"]:
        assert run_query(check["query"], 10)[0] == check["expected"], check["what"]


def test_a_first_state_outside_init_is_refuted() -> None:
    assert failed(recheck_trace(COUNTER, trace(1, 2, 3, 4), "counterexample", violated=["small"]),
                  "state 0 does not satisfy init")


def test_a_step_outside_the_transition_is_refuted() -> None:
    assert failed(recheck_trace(COUNTER, trace(0, 1, 3, 4), "counterexample", violated=["small"]),
                  "step 1 to 2 does not satisfy the transition")


def test_a_last_state_that_keeps_the_invariant_is_refuted() -> None:
    assert failed(recheck_trace(COUNTER, trace(0, 1, 2, 3), "counterexample", violated=["small"]),
                  "does not violate small")
    assert failed(recheck_trace(COUNTER, trace(0, 1, 2, 3, 4), "counterexample", violated=[]),
                  "names no invariant")


def test_a_deadlock_whose_last_state_has_a_successor_is_refuted() -> None:
    assert failed(recheck_trace(DEADLOCKS, trace(0, 1), "deadlock"), "state 1 has a successor")


def test_a_witness_whose_last_state_misses_the_goal_is_refuted() -> None:
    assert failed(recheck_trace(COUNTER, trace(0, 1), "witness", goal="(= x 2)"), "does not meet the goal")


def test_an_induction_step_trace_is_held_to_its_own_rules() -> None:
    """It need not start in init, but every state before the last must keep the invariants, all must differ, and the
    last must break them."""
    assert recheck_trace(TWO_INDUCTIVE, trace(2, 3), "induction_step", violated=["not_three"])["passed"] is True
    assert failed(recheck_trace(TWO_INDUCTIVE, trace(3, 3), "induction_step", violated=["not_three"]),
                  "state 0 does not keep not_three")
    looped = {**TWO_INDUCTIVE, "transition": f"(or {TWO_INDUCTIVE['transition']} (= next_x x))"}
    assert failed(recheck_trace(looped, trace(2, 2, 3), "induction_step", violated=["not_three"]),
                  "states 0 and 1 are the same")
    assert failed(recheck_trace(TWO_INDUCTIVE, trace(0, 1), "induction_step", violated=["not_three"]),
                  "does not violate not_three")


@pytest.mark.parametrize(("bad", "words"), [
    ([{"step": 0, "state": {"x": 0}}, {"step": 2, "state": {"x": 1}}], "numbered"),
    ([{"step": 0, "state": {}}], "no value for x"),
    ([{"step": 0, "state": {"x": 0, "y": 1}}], "not a state variable"),
    ([{"step": 0, "state": {"x": "zero"}}], "x is an Int"),
    ([{"step": 0, "state": {"x": True}}], "x is an Int"),
    ([], "empty"),
])
def test_a_malformed_trace_fails_its_recheck_rather_than_raising(bad: list, words: str) -> None:
    assert failed(recheck_trace(COUNTER, bad, "counterexample", violated=["small"]), words)


def test_a_bit_vector_or_real_value_out_of_its_sort_fails() -> None:
    bits = {"variables": {"b": "(_ BitVec 4)"}, "init": "(= b #x0)", "transition": "(= next_b (bvadd b #x1))",
            "invariants": {"low": "(bvult b #x2)"}}
    good = [{"step": 0, "state": {"b": 0}}, {"step": 1, "state": {"b": 1}}, {"step": 2, "state": {"b": 2}}]
    assert recheck_trace(bits, good, "counterexample", violated=["low"])["passed"] is True
    assert failed(recheck_trace(bits, [{"step": 0, "state": {"b": 16}}], "counterexample", violated=["low"]),
                  "4-bit")
    reals = {"variables": {"r": "Real"}, "init": "(= r 0.0)", "transition": "(= next_r (+ r (/ 1.0 2.0)))",
             "invariants": {"below_one": "(< r 1.0)"}}
    halves = [{"step": i, "state": {"r": v}} for i, v in enumerate(["0", "1/2", "1"])]
    assert recheck_trace(reals, halves, "counterexample", violated=["below_one"])["passed"] is True
    assert failed(recheck_trace(reals, [{"step": 0, "state": {"r": "a half"}}], "counterexample",
                                violated=["below_one"]), "r is a Real")


# --- planted wrong answers through each op ------------------------------------------------------------------------------

def _lie(monkeypatch: pytest.MonkeyPatch, name: str, outcome: Outcome) -> None:
    monkeypatch.setattr(symbolic, name, lambda *args, **kwargs: outcome)


def _refuted(result, words: str) -> None:
    assert result.code == CERTIFICATE_REFUTED and result.completion_state == "blocked-escalated", result.reason
    assert words in result.reason and result.trace == [] and result.recheck["passed"] is False
    assert check_envelope(result.model_dump(mode="json")) == []


def test_a_counterexample_the_solver_did_not_find_is_refuted(monkeypatch: pytest.MonkeyPatch) -> None:
    _lie(monkeypatch, "_bmc", Outcome(verdict="violated", trace=trace(0, 1, 3, 4), explored=3, violated=["small"],
                                      kind="counterexample"))
    result = check_invariant(COUNTER)
    _refuted(result, "transition")
    assert result.evidence[0]["refuted_trace"] == trace(0, 1, 3, 4)


def test_a_deadlock_with_a_successor_is_refuted(monkeypatch: pytest.MonkeyPatch) -> None:
    _lie(monkeypatch, "_bmc", Outcome(verdict="deadlock", trace=trace(0, 1), explored=1, kind="deadlock"))
    _refuted(check_deadlock(DEADLOCKS), "successor")


def test_a_witness_that_misses_the_goal_is_refuted(monkeypatch: pytest.MonkeyPatch) -> None:
    _lie(monkeypatch, "_bmc", Outcome(verdict="reached", trace=trace(0, 1, 2), explored=2, kind="witness"))
    _refuted(reachable(COUNTER, "(= x 5)"), "goal")


def test_no_violation_claimed_where_there_is_one_is_refuted(monkeypatch: pytest.MonkeyPatch) -> None:
    _lie(monkeypatch, "_bmc", Outcome(verdict="no_violation_within_bound", explored=10))
    _refuted(check_invariant(COUNTER, depth=10), "fresh")


def test_paths_claimed_to_end_where_they_go_on_are_refuted(monkeypatch: pytest.MonkeyPatch) -> None:
    _lie(monkeypatch, "_bmc", Outcome(verdict="no_violation_within_bound", explored=2, all_paths_end=True))
    _refuted(check_invariant(COUNTER, depth=10), "longer")


def test_a_proof_at_the_wrong_k_is_refuted(monkeypatch: pytest.MonkeyPatch) -> None:
    """TWO_INDUCTIVE is not 1-inductive (2 keeps the invariant and steps to 3): a claimed proof at k = 1 must fail its
    fresh induction step."""
    _lie(monkeypatch, "_k_induction", Outcome(verdict="k_inductive", k=1, explored=0))
    _refuted(prove_invariant(TWO_INDUCTIVE), "induction step")


def test_a_proof_whose_base_fails_is_refuted(monkeypatch: pytest.MonkeyPatch) -> None:
    always = {**COUNTER, "transition": "(= next_x (+ x 1))", "invariants": {"small": "(<= x 0)"}}
    _lie(monkeypatch, "_k_induction", Outcome(verdict="k_inductive", k=2, explored=1))
    _refuted(prove_invariant(always), "base")


def test_survival_control_the_true_answers_pass_through_the_same_path() -> None:
    """The same ops, unpatched, report each answer with its re-check passed, so a refutation above means something."""
    assert check_invariant(COUNTER).recheck["passed"] is True
    assert check_deadlock(DEADLOCKS).recheck["passed"] is True
    assert reachable(COUNTER, "(= x 2)").recheck["passed"] is True
    assert prove_invariant(TWO_INDUCTIVE).recheck["passed"] is True
