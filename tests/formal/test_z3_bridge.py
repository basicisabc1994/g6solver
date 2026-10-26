# SPDX-License-Identifier: Apache-2.0
"""g6.formal's Z3 checks (audit 017 FM-014, FM-003, FM-001, FM-015), with survival controls for every gate.

The archive cannot run here; each regression test says in its docstring what the archive did on the same input.
"""
from __future__ import annotations

import pytest

z3 = pytest.importorskip("z3")

from g6.formal import (  # noqa: E402 - after the importorskip, so the module is skipped without z3
    apply_envelope,
    check_entailment,
    check_sat,
    check_validity,
    find_counterexample,
    recheck,
    solve,
)
from g6.formal.envelope import (  # noqa: E402
    CONCLUSION_ASSUMED,
    INPUT_INVALID,
    PARSE_ERROR,
    SOLVER_DECISION,
    SOLVER_TIMEOUT,
    SOLVER_UNKNOWN,
    UNKNOWN_SORT,
    VACUOUS_PREMISES,
    Refusal,
)
from g6.formal.z3_bridge import run_query  # noqa: E402
from g6.invariants import check_envelope  # noqa: E402

INT = {"x": "Int"}
HARD = "(= (* x x x) (+ (* y y y) 33))"  # sums of three cubes: Z3 cannot settle this in 50 ms


def _blocked(result, code: str) -> None:
    assert result.completion_state == "blocked-escalated" and result.code == code, (result.code, result.reason)
    assert result.warning_card["code"] == code and check_envelope(result.model_dump()) == []


# --- survival controls: a gate that refused everything would fail these ---------------------------------------------

def test_a_satisfiable_formula_still_gets_a_model() -> None:
    """The archive's measured case: a sort typed "int" in lower case once became Bool and answered UNSAT."""
    result = check_sat("(and (> x 2) (< x 4))", {"x": "int"})
    assert result.satisfiable is True and result.model == {"x": 3}
    assert result.completion_state == "qualified-draft" and result.warning_card["code"] == SOLVER_DECISION
    assert check_envelope(result.model_dump()) == []


def test_an_unsatisfiable_formula_is_a_decision_not_a_refusal() -> None:
    result = check_sat("(> x 2)", INT, ["(< x 3)"])
    assert result.satisfiable is False and result.completion_state == "qualified-draft"


def test_a_genuine_entailment_is_still_valid_as_a_qualified_draft() -> None:
    result = check_validity(["(> x 1)"], "(> x 0)", sorts=INT)
    assert (result.valid, result.vacuous, result.conclusion_assumed) == (True, False, False)
    assert result.completion_state == "qualified-draft" and result.warning_card["code"] == SOLVER_DECISION
    assert [c.result for c in result.artifact.checks] == ["unsat", "sat"]  # decided, and the premises can hold
    assert check_envelope(result.model_dump()) == []


def test_a_conclusion_that_does_not_follow_gets_a_counterexample() -> None:
    result = check_validity(["(> x 0)"], "(> x 1)", sorts=INT)
    assert result.valid is False and result.counterexample == {"x": 1}
    assert result.completion_state == "qualified-draft"


def test_entailment_and_counterexample_helpers() -> None:
    bools = {"p": "Bool", "q": "Bool", "r": "Bool"}
    assert check_entailment("(and p q)", "(or p r)", sorts=bools).valid is True
    found = find_counterexample("(> (* x x) x)", ["(> x -5)", "(< x 5)"], sorts=INT)
    assert found.valid is False and found.counterexample["x"] in (0, 1)
    none = find_counterexample("(>= (* x x) 0)", sorts=INT)
    assert none.valid is True and none.counterexample is None and none.completion_state == "qualified-draft"


# --- FM-014: explicit sorts, vacuity ---------------------------------------------------------------------------------

def test_fm014_unsatisfiable_premises_are_vacuous_never_valid() -> None:
    """Archive: check_proof(["(assert false)"], "(> 1 2)", "smtlib") returned valid=True."""
    result = check_validity(["(assert false)"], "(> 1 2)")
    assert result.valid is False and result.vacuous is True
    _blocked(result, VACUOUS_PREMISES)


def test_fm014_no_integer_lies_strictly_between_0_and_1() -> None:
    """Archive: the Python dialect typed x as Int, and check_proof(["x > 0", "x < 1"], "x == 5") returned
    valid=True. Over Int the premises are inconsistent, so the result is vacuous; over Real they are refuted."""
    over_int = check_validity(["(> x 0)", "(< x 1)"], "(= x 5)", sorts={"x": "Int"})
    assert over_int.valid is False and over_int.vacuous is True
    _blocked(over_int, VACUOUS_PREMISES)
    over_real = check_validity(["(> x 0)", "(< x 1)"], "(= x 5)", sorts={"x": "Real"})
    assert over_real.valid is False and over_real.vacuous is False and over_real.counterexample == {"x": "1/2"}
    assert over_real.completion_state == "qualified-draft"


def test_fm014_an_undeclared_symbol_is_refused_never_typed_int() -> None:
    """Archive: every free name in the Python dialect was declared z3.Int without being asked."""
    result = check_validity(["(> x 0)"], "(> x 1)")
    _blocked(result, PARSE_ERROR)
    assert "unknown constant x" in result.reason


def test_fm014_the_python_dialect_is_refused() -> None:
    """Archive: ``x > 0`` was parsed as Python and typed Int; here it is not an SMT-LIB term."""
    _blocked(check_validity(["x > 0", "x < 1"], "x == 5", sorts=INT), PARSE_ERROR)


# String, (_ BitVec n) and (Array S T) are sorts since 2026-10-20 (owner: formal methods ported into the library).
@pytest.mark.parametrize("sort", ["Integer", "Whole", "(_ FloatingPoint 8 24)", "", "BitVec"])
def test_fm014_an_unknown_sort_is_refused(sort: str) -> None:
    _blocked(check_sat("(> x 0)", {"x": sort}), UNKNOWN_SORT)


@pytest.mark.parametrize(("premises", "conclusion"), [
    (["(> x 0)"], "(> x 0)"),
    (["(assert (> x 0))"], "(> x 0)"),
    (["(and (< x 9) (> x 0))"], "(> x 0)"),
    (["(> x 0)", "(< x 9)"], "(and (> x 0) (< x 9))"),
    (["(>   x    0)"], "(assert (> x 0))"),
])
def test_fm014_a_conclusion_assumed_among_the_premises_is_flagged(premises: list[str], conclusion: str) -> None:
    """Archive: valid=True with nothing flagged, so a claim restated as a premise was "proved"."""
    result = check_validity(premises, conclusion, sorts=INT)
    assert result.conclusion_assumed is True
    _blocked(result, CONCLUSION_ASSUMED)


# --- FM-003, the check_proof half (triangulation itself is not ported) ----------------------------------------------

def test_fm003_premises_that_assume_the_claim_are_not_a_proof() -> None:
    """The audit's stub for "every integer is positive": premises (assert (> x 0)), conclusion (> x 0). Archive:
    triangulation returned completion_state="verified"."""
    result = check_validity(["(assert (> x 0))"], "(> x 0)", sorts=INT)
    assert result.completion_state != "verified" and result.conclusion_assumed is True
    _blocked(result, CONCLUSION_ASSUMED)


def test_fm003_contradictory_premises_are_vacuous() -> None:
    """Audit stub: (> x 1) and (< x 0) "proved" (= x 42). Archive: completion_state="verified"."""
    result = check_validity(["(assert (> x 1))", "(assert (< x 0))"], "(= x 42)", sorts=INT)
    assert result.vacuous is True and result.valid is False
    _blocked(result, VACUOUS_PREMISES)


def test_fm003_a_declaration_among_the_premises_is_refused() -> None:
    """Archive: premise lines starting (declare were passed to Z3 raw. Sorts are declared with ``sorts=`` only."""
    _blocked(check_validity(["(declare-const x Int)", "(assert (> x 0))"], "(> x 0)"), PARSE_ERROR)


# --- the closed input grammar ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("formula", [
    "(> x 0)) (assert false",
    "(> x 0) (< x 0)",
    '(= x "a")',
    "(> x 0) ; (assert false)",
    "(! (> x 0) :named a)",
    "(set-option :regular-output-channel out)",
    "|x|",
    "(> x 0",
])
def test_a_term_cannot_carry_a_command_or_change_the_query(formula: str, tmp_path, monkeypatch) -> None:
    """The archive passed caller text to parse_smt2_string verbatim inside an (assert ...) wrapper."""
    monkeypatch.chdir(tmp_path)
    result = check_sat(formula, INT)
    assert result.completion_state == "blocked-escalated" and result.satisfiable is None
    assert result.code == PARSE_ERROR, result.reason
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("query", [
    '(set-option :regular-output-channel "out.txt")\n(check-sat)',
    "(declare-fun f (Int) Int)\n(check-sat)",
    "(push)\n(check-sat)",
    "(assert true)",
    "(assert true)\n(check-sat)\n(check-sat)",
    "(declare-const x (_ FloatingPoint 8 24))\n(check-sat)",
    "(declare-const x string)\n(check-sat)",
])
def test_run_query_refuses_anything_outside_the_closed_grammar(query: str, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(Refusal):
        run_query(query, 1.0)
    assert list(tmp_path.iterdir()) == []


# --- unknown and bad parameters --------------------------------------------------------------------------------------

def test_unknown_is_blocked_never_a_decision() -> None:
    sat = check_sat(HARD, {"x": "Int", "y": "Int"}, timeout=0.05)
    assert sat.satisfiable is None and sat.completion_state == "blocked-escalated"
    assert sat.code in (SOLVER_TIMEOUT, SOLVER_UNKNOWN) and sat.artifact.checks[0].result == "unknown"
    validity = check_validity([], f"(not {HARD})", sorts={"x": "Int", "y": "Int"}, timeout=0.05)
    assert validity.valid is False and validity.completion_state == "blocked-escalated"


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), 601, True, "10"])
def test_a_timeout_outside_its_range_is_refused(timeout) -> None:
    _blocked(check_sat("(> x 0)", INT, timeout=timeout), INPUT_INVALID)


@pytest.mark.parametrize("premises", ["(> x 0)", None, 7])
def test_premises_must_be_a_list(premises) -> None:
    _blocked(check_validity(premises, "(> x 0)", sorts=INT), INPUT_INVALID)


# --- FM-001: the artifact binds what was decided, and a recheck re-runs it -------------------------------------------

def test_fm001_the_artifact_binds_statement_solver_version_and_answers() -> None:
    result = check_validity(["(> x 1)"], "(> x 0)", sorts=INT)
    artifact = result.artifact
    assert (artifact.solver, artifact.solver_version) == ("z3", z3.get_version_string())
    assert artifact.statement == "(=> (> x 1) (> x 0))" and artifact.goal == "(> x 0)"
    assert artifact.checks[0].query == "(declare-const x Int)\n(assert (> x 1))\n(assert (not (> x 0)))\n(check-sat)"
    assert artifact.checks[1].query == "(declare-const x Int)\n(assert (> x 1))\n(check-sat)"
    assert result.evidence[0]["checks"] == [c.model_dump() for c in artifact.checks]
    assert result.evidence[0]["solver_version"] == z3.get_version_string()
    ok, how = recheck(artifact)
    assert ok is True, how


def _edits(artifact):
    first, second = artifact.checks
    yield "answer flipped", {"checks": (first.model_copy(update={"result": "sat"}), second)}
    yield "statement edited", {"statement": "(=> (> x 1) (> x 5))"}
    yield "goal edited", {"goal": "(> x 5)"}
    yield "query edited", {"checks": (first.model_copy(update={"query": first.query.replace("(> x 1)", "(> x 5)")}),
                                      second)}
    yield "consistency check dropped", {"checks": (first,)}
    yield "sort edited", {"sorts": {"x": "Real"}}
    yield "solver renamed", {"solver": "lean"}


def test_fm001_an_edited_artifact_fails_recheck() -> None:
    """Archive: validation recomputed an unkeyed SHA-256 over the artifact's own fields, so an edit plus a
    recomputed hash passed. Here every edit either breaks the statement-to-query binding or changes an answer."""
    artifact = check_validity(["(> x 1)"], "(> x 0)", sorts=INT).artifact
    for name, update in _edits(artifact):
        ok, why = recheck(artifact.model_copy(update=update))
        assert ok is False, f"{name}: {why}"


def test_fm001_a_rechecked_artifact_never_lifts_the_label() -> None:
    """Archive: a validated artifact lifted the envelope to verified. Here the best state stays qualified-draft."""
    result = check_validity(["(> x 1)"], "(> x 0)", sorts=INT)
    assert recheck(result.artifact)[0] is True
    again = apply_envelope(result, backend="z3", operation="check_validity")
    assert again.completion_state == "qualified-draft" and again.warning_card["verified"] is False


# --- FM-015: every Z3 result carries the envelope --------------------------------------------------------------------

@pytest.mark.parametrize("call", [
    lambda **ids: check_sat("(> x 0)", INT, **ids),
    lambda **ids: check_sat("(> x 0)", {}, **ids),
    lambda **ids: check_validity(["(assert false)"], "(> 1 2)", **ids),
    lambda **ids: check_validity(["(> x 1)"], "(> x 0)", sorts=INT, **ids),
    lambda **ids: check_entailment("(> x 1)", "(> x 0)", sorts=INT, **ids),
    lambda **ids: find_counterexample("(> x 0)", sorts=INT, **ids),
    lambda **ids: solve({"formula": "(> x 0)", "variables": INT, **ids}),
])
def test_fm015_every_z3_result_carries_the_envelope(call) -> None:
    """Archive: the skill MCP ``formal_verify`` JSON had no completion_state, warning_card, evidence or ids."""
    dumped = call(request_id="req-1", run_id="run-1").model_dump()
    for key in ("completion_state", "warning_card", "evidence", "request_id", "run_id"):
        assert key in dumped
    assert (dumped["request_id"], dumped["run_id"]) == ("req-1", "run-1") and dumped["evidence"]
    assert check_envelope(dumped) == [] and dumped["completion_state"] != "verified"
