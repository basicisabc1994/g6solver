# SPDX-License-Identifier: Apache-2.0
"""g6.formal's Z3 depth (owner, 2026-10-20: the archive's ``z3_mcp`` operations, in process, without ``run_code`` or
``interpolate``): a known answer for every operation, the bit-vector, array and string sorts, and for each operation a
planted wrong answer that its independent check must catch."""
from __future__ import annotations

from fractions import Fraction

import pytest

z3 = pytest.importorskip("z3")

from g6.formal import (  # noqa: E402 - after the importorskip, so the module is skipped without z3
    check_equivalence,
    check_sat,
    count_models,
    eliminate_quantifiers,
    find_counterexample,
    optimise,
    recheck,
    simplify,
    unsat_core,
)
from g6.formal import z3_depth  # noqa: E402
from g6.formal.envelope import (  # noqa: E402
    CERTIFICATE_REFUTED,
    INPUT_INVALID,
    NO_OPTIMUM,
    PARSE_ERROR,
    SOLVER_DECISION,
    SOLVER_TIMEOUT,
    SOLVER_UNKNOWN,
    UNKNOWN_SORT,
)
from g6.invariants import check_envelope  # noqa: E402

INT2 = {"x": "Int", "y": "Int"}
HARD = "(= (* x x x) (+ (* y y y) 33))"  # sums of three cubes: Z3 cannot settle this in 50 ms


def _draft(result) -> None:
    assert result.completion_state == "qualified-draft" and result.code == "", (result.code, result.reason)
    assert result.warning_card["code"] == SOLVER_DECISION and result.warning_card["verified"] is False
    assert check_envelope(result.model_dump()) == [] and result.evidence[0]["kind"] == "formal_decision"


def _blocked(result, code: str) -> None:
    assert result.completion_state == "blocked-escalated" and result.code == code, (result.code, result.reason)
    assert result.warning_card["code"] == code and check_envelope(result.model_dump()) == []


# --- optimise ---------------------------------------------------------------------------------------------------------

def test_optimise_finds_a_known_minimum_and_proves_the_bound() -> None:
    result = optimise([["minimise", "(+ x y)"]], ["(>= x 2)", "(>= y 3)", "(<= (+ x y) 10)"], INT2)
    _draft(result)
    assert result.optimal is True and result.satisfiable is True and result.model == {"x": 2, "y": 3}
    assert [(o["direction"], o["term"], o["value"]) for o in result.objectives] == [("minimise", "(+ x y)", "5")]
    assert result.artifact.goal == "(>= (+ x y) 5)" and [c.result for c in result.artifact.checks] == ["unsat", "sat"]
    assert result.attainment.result == "sat" and "(assert (= (+ x y) 5))" in result.attainment.query
    assert result.evidence[0]["certificate"]["objectives"] == result.objectives
    assert recheck(result.artifact)[0] is True


def test_optimise_is_lexicographic_over_several_objectives() -> None:
    result = optimise([["maximise", "x"], ["min", "y"]], ["(>= x 0)", "(>= y 1)", "(<= (+ x y) 10)"], INT2)
    _draft(result)
    assert result.model == {"x": 9, "y": 1} and [o["value"] for o in result.objectives] == ["9", "1"]
    assert result.artifact.goal == "(and (<= x 9) (=> (= x 9) (>= y 1)))"


def test_optimise_takes_bounds_as_constraints() -> None:
    result = optimise([["maximize", "(+ (* 3 x) (* 2 y))"]], ["(<= (+ x y) 7)"], INT2, bounds={"x": [0, 4], "y": [0, None]})
    _draft(result)
    assert result.model == {"x": 4, "y": 3} and result.objectives[0]["value"] == "18"
    assert "(<= 0 x)" in result.artifact.assumptions and "(<= x 4)" in result.artifact.assumptions


def test_optimise_over_reals_and_bit_vectors() -> None:
    real = optimise([["minimise", "r"]], ["(>= (* 2 r) 1)"], {"r": "Real"})
    _draft(real)
    assert real.model == {"r": "1/2"} and real.objectives[0]["value"] == "(/ 1.0 2.0)"
    bits = optimise([["maximise", "b"]], ["(bvult b #x10)"], {"b": "(_ BitVec 8)"})
    _draft(bits)
    assert bits.model == {"b": 15} and bits.objectives[0]["value"] == "#x0f"
    assert bits.artifact.goal == "(bvule b #x0f)" and recheck(bits.artifact)[0] is True


def test_infeasible_constraints_are_a_decision_with_no_optimum() -> None:
    result = optimise([["minimise", "x"]], ["(> x 5)", "(< x 3)"], {"x": "Int"})
    _draft(result)
    assert result.satisfiable is False and result.optimal is False and result.artifact.checks[0].result == "unsat"


@pytest.mark.parametrize(("constraint", "sorts"), [("(< x 5)", {"x": "Int"}), ("(> x 0)", {"x": "Real"})])
def test_an_unbounded_or_unattained_objective_has_no_optimum(constraint: str, sorts: dict) -> None:
    """minimise x below 5 has no least value; minimise a real above 0 has an infimum (0) it never attains."""
    result = optimise([["minimise", "x"]], [constraint], sorts)
    _blocked(result, NO_OPTIMUM)
    assert result.optimal is False


def test_planted_wrong_optima_are_caught_by_the_bound_and_the_attainment_checks(monkeypatch) -> None:
    """The optimiser's answer is checked on fresh solvers: a value above the true minimum fails the bound (a better
    point exists), and one below it fails attainment (no point reaches it)."""
    constraints, sorts = ["(>= x 2)", "(>= y 3)", "(<= (+ x y) 10)"], INT2
    real = z3_depth._optimum

    def planted(value: str):
        def fake(*args):
            answer, values, bounds, model, why = real(*args)
            return answer, [value], bounds, model, why
        return fake

    monkeypatch.setattr(z3_depth, "_optimum", planted("6"))
    too_high = optimise([["minimise", "(+ x y)"]], constraints, sorts)
    _blocked(too_high, CERTIFICATE_REFUTED)
    assert too_high.optimal is False and "better" in too_high.reason and too_high.artifact.checks[0].result == "sat"
    monkeypatch.setattr(z3_depth, "_optimum", planted("4"))
    too_low = optimise([["minimise", "(+ x y)"]], constraints, sorts)
    _blocked(too_low, CERTIFICATE_REFUTED)
    assert too_low.attainment.result == "unsat" and "attain" in too_low.reason


@pytest.mark.parametrize(("objectives", "code"), [
    ([["minimise", "(> x 0)"]], INPUT_INVALID),
    ([["lowest", "x"]], INPUT_INVALID),
    ([["minimise"]], INPUT_INVALID),
    ([], INPUT_INVALID),
    ([["minimise", "(+ x z)"]], PARSE_ERROR),
])
def test_bad_objectives_are_refused(objectives, code: str) -> None:
    _blocked(optimise(objectives, ["(> x 0)"], {"x": "Int"}), code)


@pytest.mark.parametrize("bounds", [{"z": [0, 1]}, {"x": [0]}, {"x": [True, 3]}, {"x": [0.5, 3]}, {"x": "0..3"}])
def test_bad_bounds_are_refused(bounds) -> None:
    _blocked(optimise([["minimise", "x"]], [], {"x": "Int"}, bounds=bounds), INPUT_INVALID)


def test_an_optimiser_timeout_is_blocked_never_an_optimum() -> None:
    result = optimise([["minimise", "x"]], [HARD], INT2, timeout=0.05)
    assert result.code in (SOLVER_TIMEOUT, SOLVER_UNKNOWN) and result.completion_state == "blocked-escalated"
    assert result.optimal is False and check_envelope(result.model_dump()) == []


# --- unsat cores -----------------------------------------------------------------------------------------------------

def test_unsat_core_names_the_conflict_and_leaves_out_the_bystander() -> None:
    result = unsat_core({"big": "(> x 5)", "small": "(< x 3)", "other": "(> y 0)"}, INT2)
    _draft(result)
    assert result.satisfiable is False and result.core == ["big", "small"] and result.minimal is True
    assert result.artifact.assumptions == ("(> x 5)", "(< x 3)") and result.artifact.checks[0].result == "unsat"
    assert [c.result for c in result.minimality] == ["sat", "sat"] and recheck(result.artifact)[0] is True


def test_unsat_core_is_shrunk_to_a_minimal_one() -> None:
    result = unsat_core({"a": "(> x 5)", "b": "(> x 6)", "c": "(< x 3)"}, {"x": "Int"})
    _draft(result)
    assert len(result.core) == 2 and "c" in result.core and result.minimal is True


def test_satisfiable_assertions_have_no_core_and_get_a_model() -> None:
    result = unsat_core({"a": "(> x 0)", "b": "(< x 2)"}, {"x": "Int"})
    _draft(result)
    assert result.satisfiable is True and result.core == [] and result.model == {"x": 1}


def test_planted_wrong_cores_are_caught(monkeypatch) -> None:
    """A "core" that can be satisfied is refuted; one that carries a bystander is shrunk until it is minimal."""
    named = {"big": "(> x 5)", "small": "(< x 3)", "other": "(> y 0)"}
    monkeypatch.setattr(z3_depth, "_core", lambda *args: ("unsat", ["other"], {}, ""))
    wrong = unsat_core(named, INT2)
    _blocked(wrong, CERTIFICATE_REFUTED)
    assert wrong.artifact.checks[0].result == "sat" and wrong.core == ["other"]
    monkeypatch.setattr(z3_depth, "_core", lambda *args: ("unsat", ["big", "small", "other"], {}, ""))
    padded = unsat_core(named, INT2)
    _draft(padded)
    assert padded.core == ["big", "small"] and padded.minimal is True


@pytest.mark.parametrize("named", [{}, {"1bad": "(> x 0)"}, ["(> x 0)"], {"a": 7}])
def test_bad_named_assertions_are_refused(named) -> None:
    assert unsat_core(named, {"x": "Int"}).code in (INPUT_INVALID, PARSE_ERROR)


# --- simplify and quantifier elimination -----------------------------------------------------------------------------

@pytest.mark.parametrize(("term", "sorts", "expected"), [
    ("(and (> x 0) (> x 0) (or (= y 1) true))", INT2, "(not (<= x 0))"),
    ("(+ x 1 1)", INT2, "(+ 2 x)"),
    ("(bvadd b b #x00)", {"b": "(_ BitVec 8)"}, "(bvmul #x02 b)"),
    ('(str.len (str.++ s "abc"))', {"s": "String"}, "(+ 3 (str.len s))"),
])
def test_simplify_returns_an_equivalent_term_and_proves_it(term: str, sorts: dict, expected: str) -> None:
    result = simplify(term, sorts)
    _draft(result)
    assert result.result_term == expected and result.equivalent is True
    assert result.artifact.goal == f"(= {result.term} {expected})" and recheck(result.artifact)[0] is True


def test_a_planted_wrong_simplification_is_refuted_with_a_counterexample(monkeypatch) -> None:
    monkeypatch.setattr(z3_depth, "_simplified", lambda *args: "(> x 1)")
    result = simplify("(> x 0)", {"x": "Int"})
    _blocked(result, CERTIFICATE_REFUTED)
    assert result.equivalent is False and result.counterexample == {"x": 1}


@pytest.mark.parametrize(("formula", "sorts", "expected"), [
    ("(exists ((z Int)) (= x (* 2 z)))", {"x": "Int"}, "(= 0 (mod x 2))"),
    ("(exists ((z Real)) (and (< x z) (< z y)))", {"x": "Real", "y": "Real"}, "(not (<= 0.0 (+ x (* (- 1.0) y))))"),
])
def test_quantifier_elimination_gives_an_equivalent_quantifier_free_formula(formula, sorts, expected) -> None:
    result = eliminate_quantifiers(formula, sorts)
    _draft(result)
    assert result.result_term == expected and result.quantifier_free is True and result.equivalent is True
    assert recheck(result.artifact)[0] is True


def test_a_planted_wrong_elimination_is_refuted(monkeypatch) -> None:
    """"x is even" is not "true": the equivalence check finds an odd x."""
    monkeypatch.setattr(z3_depth, "_eliminated", lambda *args: "true")
    result = eliminate_quantifiers("(exists ((z Int)) (= x (* 2 z)))", {"x": "Int"})
    _blocked(result, CERTIFICATE_REFUTED)
    assert result.counterexample["x"] % 2 == 1


def test_an_elimination_that_leaves_a_quantifier_is_not_reported_as_done() -> None:
    """Non-linear: z3's qe tactic hands the cubic back still quantified."""
    result = eliminate_quantifiers("(exists ((z Int)) (= x (+ (* z z z) 33)))", {"x": "Int"})
    _blocked(result, SOLVER_UNKNOWN)
    assert result.quantifier_free is False and "quantifier" in result.reason


# --- model counting --------------------------------------------------------------------------------------------------

def test_count_models_is_exact_below_the_cap_and_says_when_the_cap_was_hit() -> None:
    """Archive (before its 2026-09-22 fix): "Found 10 models" for a problem with 55, a lower bound worded as a count."""
    exact = count_models("(and (>= x 1) (<= x 55))", {"x": "Int"}, cap=100)
    _draft(exact)
    assert (exact.count, exact.exact, exact.cap_hit) == (55, True, False)
    assert exact.artifact.checks[0].result == "unsat" and recheck(exact.artifact)[0] is True
    capped = count_models("(and (>= x 1) (<= x 55))", {"x": "Int"}, cap=10)
    _draft(capped)
    assert (capped.count, capped.exact, capped.cap_hit) == (10, False, True)
    assert capped.artifact.checks[0].result == "sat"  # an eleventh model exists: the count is a lower bound
    assert "10 is a lower bound" in capped.warning_card["message"]
    assert "lower bound" not in exact.warning_card["message"]


@pytest.mark.parametrize(("formula", "sorts", "count"), [
    ("(or p q)", {"p": "Bool", "q": "Bool"}, 3),
    ("(= b b)", {"b": "(_ BitVec 2)"}, 4),
    ('(and (= (str.len s) 1) (str.in_re s (re.range "a" "c")))', {"s": "String"}, 3),
    ("(and p (not p))", {"p": "Bool"}, 0),
])
def test_count_models_over_bools_bit_vectors_and_strings(formula: str, sorts: dict, count: int) -> None:
    result = count_models(formula, sorts)
    _draft(result)
    assert (result.count, result.exact) == (count, True) and len(result.models) == count
    assert result.satisfiable is (count > 0)


def test_exactly_cap_models_is_exact() -> None:
    result = count_models("(and (>= x 1) (<= x 3))", {"x": "Int"}, cap=3)
    assert (result.count, result.exact, result.cap_hit) == (3, True, False)


def test_a_planted_short_count_is_refuted(monkeypatch) -> None:
    real = z3_depth._enumerate

    def drop_one(*args):
        models, blocks, answer, why = real(*args)
        return models[:-1], blocks[:-1], answer, why

    monkeypatch.setattr(z3_depth, "_enumerate", drop_one)
    result = count_models("(and (>= x 1) (<= x 5))", {"x": "Int"})
    _blocked(result, CERTIFICATE_REFUTED)
    assert result.exact is False and result.artifact.checks[0].result == "sat"


@pytest.mark.parametrize(("sorts", "cap"), [({"a": "(Array Int Int)"}, 10), ({"x": "Int"}, 0), ({"x": "Int"}, 10_000)])
def test_count_models_refuses_arrays_and_caps_out_of_range(sorts: dict, cap: int) -> None:
    _blocked(count_models("true", sorts, cap=cap), INPUT_INVALID)


# --- equivalence -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("a", "b", "sorts"), [
    ("(> x 0)", "(>= x 1)", {"x": "Int"}),
    ("(=> p q)", "(or (not p) q)", {"p": "Bool", "q": "Bool"}),
    ("(+ x x)", "(* 2 x)", {"x": "Int"}),
    ("(bvshl b #x01)", "(bvmul b #x02)", {"b": "(_ BitVec 8)"}),
])
def test_equivalent_formulas_and_terms(a: str, b: str, sorts: dict) -> None:
    result = check_equivalence(a, b, sorts=sorts)
    _draft(result)
    assert result.valid is True and result.counterexample is None and recheck(result.artifact)[0] is True


def test_a_planted_non_equivalence_gets_a_distinguishing_assignment() -> None:
    """(> x 0) and (>= x 1) agree over Int but not over Real; a misapplied De Morgan is caught too."""
    real = check_equivalence("(> x 0)", "(>= x 1)", sorts={"x": "Real"})
    _draft(real)
    assert real.valid is False and 0 < Fraction(real.counterexample["x"]) < 1
    demorgan = check_equivalence("(not (and p q))", "(and (not p) (not q))", sorts={"p": "Bool", "q": "Bool"})
    assert demorgan.valid is False and demorgan.counterexample["p"] != demorgan.counterexample["q"]


def test_equivalence_under_inconsistent_premises_is_vacuous() -> None:
    result = check_equivalence("(> x 0)", "(< x 0)", premises=["(> x 1)", "(< x 0)"], sorts={"x": "Int"})
    assert result.valid is False and result.vacuous is True and result.completion_state == "blocked-escalated"


# --- the bit-vector, array and string sorts, and the closed grammar around them --------------------------------------

def test_bit_vector_array_and_string_sorts_through_check_sat() -> None:
    bits = check_sat("(= (bvadd b #x01) #x05)", {"b": "(_ BitVec 8)"})
    assert bits.satisfiable is True and bits.model == {"b": 4} and recheck(bits.artifact)[0] is True
    array = check_sat("(= (select (store a 1 5) 1) x)", {"a": "(Array Int Int)", "x": "Int"})
    assert array.satisfiable is True and array.model["x"] == 5
    text = check_sat('(= (str.++ s "b") "ab")', {"s": "String"})
    assert text.satisfiable is True and text.model == {"s": "a"} and recheck(text.artifact)[0] is True
    nested = check_sat("(= (select m #x01) true)", {"m": "(array (_ bitvec 8) bool)"})
    assert nested.satisfiable is True and nested.artifact.sorts == {"m": "(Array (_ BitVec 8) Bool)"}


def test_an_eight_bit_overflow_is_a_counterexample() -> None:
    """b + 1 > b fails for unsigned 8-bit b = 255, which the Int reading of the same claim would never show."""
    found = find_counterexample("(bvugt (bvadd b #x01) b)", sorts={"b": "(_ BitVec 8)"})
    assert found.valid is False and found.counterexample == {"b": 255}


def test_a_string_literal_is_data_and_cannot_carry_a_command(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    literal = '(= s "(check-sat) ; (set-option :x y) | ""quoted""")'
    result = check_sat(literal, {"s": "String"})
    assert result.satisfiable is True and result.model == {"s": '(check-sat) ; (set-option :x y) | "quoted"'}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("formula", [
    '(= s "a") (set-option :regular-output-channel "out")',
    '(= s "unclosed)',
    '(= s "line\nbreak")',
    "(= b #z00)",
    "(= b #x)",
    '(= s "café")',
])
def test_strings_and_numerals_outside_the_grammar_are_refused(formula: str) -> None:
    _blocked(check_sat(formula, {"s": "String", "b": "(_ BitVec 8)"}), PARSE_ERROR)


@pytest.mark.parametrize("sort", ["(_ BitVec 0)", "(_ BitVec 99999)", "(_ BitVec x)", "(Array Int)", "BitVec 8",
                                  "(_ FloatingPoint 8 24)", "(Array Int (Seq Int))"])
def test_sorts_outside_the_set_are_refused(sort: str) -> None:
    _blocked(check_sat("true", {"v": sort}), UNKNOWN_SORT)


# --- every operation: the envelope, the ids, never verified ----------------------------------------------------------

@pytest.mark.parametrize("call", [
    lambda **ids: optimise([["minimise", "x"]], ["(> x 0)"], {"x": "Int"}, **ids),
    lambda **ids: optimise([["minimise", "x"]], ["(> x"], {"x": "Int"}, **ids),
    lambda **ids: unsat_core({"a": "(> x 0)", "b": "(< x 0)"}, {"x": "Int"}, **ids),
    lambda **ids: simplify("(+ x 0)", {"x": "Int"}, **ids),
    lambda **ids: eliminate_quantifiers("(forall ((z Int)) (> (+ x z z) z))", {"x": "Int"}, **ids),
    lambda **ids: count_models("(< 0 x 3)", {"x": "Int"}, **ids),
    lambda **ids: check_equivalence("p", "(not (not p))", sorts={"p": "Bool"}, **ids),
])
def test_every_depth_result_carries_the_envelope_and_is_never_verified(call) -> None:
    dumped = call(request_id="req-1", run_id="run-1").model_dump()
    for key in ("completion_state", "warning_card", "evidence", "request_id", "run_id"):
        assert key in dumped
    assert (dumped["request_id"], dumped["run_id"]) == ("req-1", "run-1") and dumped["evidence"]
    assert check_envelope(dumped) == [] and dumped["completion_state"] != "verified"
