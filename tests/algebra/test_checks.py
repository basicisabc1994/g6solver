# SPDX-License-Identifier: Apache-2.0
"""G6's independent re-checks of SymPy's answers (owner, 2026-10-20), run in process here: each is fed a known answer,
whose oracle is written in the test, and a planted wrong answer it must refute. Planted answers are built through
G6's own parser and :mod:`g6.algebra.build`, so no text reaches SymPy here either. A re-check that cannot decide says
``undecided``, never ``passed``."""
from __future__ import annotations

from fractions import Fraction

import pytest

sympy = pytest.importorskip("sympy", reason="SymPy is not installed (pip install 'g6[cas] @ git+https://github.com/basicisabc1994/g6solver')")
mpmath = pytest.importorskip("mpmath")

from g6.algebra import build, checks, numeric  # noqa: E402
from g6.algebra import language as L  # noqa: E402


def ctx(symbols=("x",)) -> checks.Context:
    return checks.Context.make(L.declare(symbols if isinstance(symbols, dict) else list(symbols)), seed=7)


def tree(c: checks.Context, text: str, **kw) -> L.Node:
    return L.parse(text, c.declared, **kw)


def sym(c: checks.Context, text: str, **kw):
    return build.expression(tree(c, text, **kw), c.table)


def matrix(c: checks.Context, rows):
    return [[tree(c, str(v)) for v in row] for row in rows]


# --- G6's own evaluator agrees with SymPy's semantics, function by function -----------------------------------------

@pytest.mark.parametrize("name", sorted(L.FUNCTIONS))
def test_g6s_evaluator_matches_sympy_at_a_complex_point(name: str) -> None:
    c = ctx(["x"])
    args = "x" if L.FUNCTIONS[name][0] == 1 else "x, 3"
    node = tree(c, f"{name}({args})")
    point = numeric.Point({"x": (Fraction(43, 100), Fraction(-17, 10))})
    ours = numeric.stable(lambda: numeric.evaluate(node, point.mp()))
    theirs = numeric.sympy_value(build.expression(node, c.table), point.sympy(c.table))
    assert numeric.close(ours, theirs), (name, ours, theirs)


def test_exact_values_and_taylor_coefficients_of_rational_expressions() -> None:
    c = ctx(["x", "y"])
    node = tree(c, "(x^2 - 1)/(x - y) + 3*y^-2")
    assert numeric.is_rational(node) and not numeric.is_rational(tree(c, "sin(x)")) \
        and not numeric.is_rational(tree(c, "x^(1/2)"))
    assert numeric.exact(node, {"x": Fraction(3), "y": Fraction(1)}) == Fraction(8, 2) + 3
    # 1/(1 - x) = 1 + x + x^2 + ...; at x = 1/2 the coefficients of t^k are 2^(k+1)
    assert numeric.taylor(tree(c, "1/(1 - x)"), "x", {}, Fraction(1, 2), 4) == [2, 4, 8, 16]
    with pytest.raises(numeric.NotRational):
        numeric.exact(tree(c, "exp(x)"), {"x": Fraction(1)})


def _refuted(found, why: str = "") -> bool:
    return found.status == "refuted" and found.failures and any(why in f for f in found.failures)


# --- simplify, expand, factor: equal to the original ----------------------------------------------------------------

@pytest.mark.parametrize(("op", "original", "right", "wrong"), [
    ("simplify", "sin(x)^2 + cos(x)^2", "1", "2"),
    ("simplify", "(x^2 - 1)/(x - 1)", "x + 1", "x - 1"),
    ("simplify", "exp(log(x)) + 0*x", "x", "x^2"),
    ("expand", "(x + 1)^2", "x^2 + 2*x + 1", "x^2 + 1"),
    ("expand", "exp(x + 2)", "exp(x)*E^2", "exp(x)*E"),
    ("factor", "x^2 - 5*x + 6", "(x - 2)*(x - 3)", "(x - 2)*(x + 3)"),
])
def test_an_equal_answer_passes_and_a_planted_one_is_refuted(op, original, right, wrong) -> None:
    c = ctx()
    good = checks.equal(tree(c, original), sym(c, right), c, op)
    assert good.status == "passed" and good.points >= numeric.MIN_POINTS, good
    assert _refuted(checks.equal(tree(c, original), sym(c, wrong), c, op))


def test_a_rational_answer_is_also_checked_exactly_at_integer_points() -> None:
    c = ctx(["x", "y"])
    found = checks.equal(tree(c, "(x + y)^3"), sym(c, "x^3 + 3*x^2*y + 3*x*y^2 + y^3"), c, "expand")
    assert found.status == "passed" and any("exact" in m for m in found.methods)
    planted = checks.equal(tree(c, "(x + y)^3"), sym(c, "x^3 + 3*x^2*y + 3*x*y^2 + y^3 + 10^(-40)"), c, "expand")
    assert _refuted(planted, "exact")       # a difference far below any numeric tolerance, caught exactly


# --- differentiate ----------------------------------------------------------------------------------------------

def test_a_derivative_is_rechecked_by_numeric_differences() -> None:
    c = ctx()
    assert checks.derivative(tree(c, "sin(x)^2"), sym(c, "2*sin(x)*cos(x)"), "x", 1, c).status == "passed"
    assert _refuted(checks.derivative(tree(c, "sin(x)^2"), sym(c, "2*sin(x)"), "x", 1, c))
    second = checks.derivative(tree(c, "x^3 + x"), sym(c, "6*x"), "x", 2, c)
    assert second.status == "passed" and any("exact" in m for m in second.methods)
    assert _refuted(checks.derivative(tree(c, "x^3 + x"), sym(c, "3*x^2 + 1"), "x", 2, c))


def test_a_derivative_through_abs_of_a_complex_variable_is_undecided() -> None:
    c = ctx()
    found = checks.derivative(tree(c, "abs(x)"), sym(c, "sign(x)"), "x", 1, c)
    assert found.status == "undecided" and "complex" in found.why
    real = ctx({"x": ["real"]})
    assert checks.derivative(tree(real, "abs(x)^2"), sym(real, "2*x"), "x", 1, real).status == "passed"


# --- integrate --------------------------------------------------------------------------------------------------

def test_an_antiderivative_is_differentiated_back() -> None:
    c = ctx()
    assert checks.antiderivative(tree(c, "x*E^x"), sym(c, "(x - 1)*exp(x)"), "x", c).status == "passed"
    assert _refuted(checks.antiderivative(tree(c, "x*E^x"), sym(c, "x*exp(x)"), "x", c))
    assert checks.antiderivative(tree(c, "1/x"), sym(c, "log(x)"), "x", c).status == "passed"


def test_a_definite_integral_is_rechecked_by_quadrature() -> None:
    c = ctx()
    zero, pi = tree(c, "0"), tree(c, "pi")
    good = checks.definite(tree(c, "sin(x)"), sympy.Integer(2), "x", zero, pi, c)
    assert good.status == "passed" and good.tolerance
    assert _refuted(checks.definite(tree(c, "sin(x)"), sympy.Integer(1), "x", zero, pi, c))
    inf = tree(c, "oo", infinity=True)
    assert checks.definite(tree(c, "exp(-x)"), sympy.Integer(1), "x", zero, inf, c).status == "passed"
    a = ctx(["x", "a"])
    with_a = checks.definite(tree(a, "a*x"), sym(a, "a/2"), "x", tree(a, "0"), tree(a, "1"), a)
    assert with_a.status == "passed" and with_a.points >= numeric.MIN_POINTS
    assert _refuted(checks.definite(tree(a, "a*x"), sym(a, "a/3"), "x", tree(a, "0"), tree(a, "1"), a))


# --- solve ------------------------------------------------------------------------------------------------------

def test_solutions_are_substituted_back() -> None:
    c = ctx()
    equation = [tree(c, "x^2 - 5*x + 6 = 0", equation=True)]
    assert checks.solutions(equation, ["x"], [{"x": sympy.Integer(2)}, {"x": sympy.Integer(3)}], c).status == "passed"
    assert _refuted(checks.solutions(equation, ["x"], [{"x": sympy.Integer(2)}, {"x": sympy.Integer(4)}], c), "4")
    radical = [tree(c, "x^2 = 2", equation=True)]
    assert checks.solutions(radical, ["x"], [{"x": sym(c, "sqrt(2)")}, {"x": sym(c, "-sqrt(2)")}], c).status \
        == "passed"


def test_a_system_and_a_parametric_solution() -> None:
    c = ctx(["x", "y"])
    system = [tree(c, "x + y = 3", equation=True), tree(c, "x - y = 1", equation=True)]
    assert checks.solutions(system, ["x", "y"], [{"x": sympy.Integer(2), "y": sympy.Integer(1)}], c).status == "passed"
    assert _refuted(checks.solutions(system, ["x", "y"], [{"x": sympy.Integer(1), "y": sympy.Integer(2)}], c))
    line = [tree(c, "x + y = 1", equation=True)]
    assert checks.solutions(line, ["x", "y"], [{"x": sym(c, "1 - y"), "y": sym(c, "y")}], c).status == "passed"
    assert _refuted(checks.solutions(line, ["x", "y"], [{"x": sym(c, "2 - y"), "y": sym(c, "y")}], c))


def test_a_family_of_solutions_over_the_integers() -> None:
    c = ctx()
    n = sympy.Symbol("n", integer=True)
    family = [{"x": 2 * n * sympy.pi}, {"x": 2 * n * sympy.pi + sympy.pi}]
    eq = [tree(c, "sin(x)")]
    assert checks.solutions(eq, ["x"], family, c, families=[n, n]).status == "passed"
    assert _refuted(checks.solutions(eq, ["x"], [{"x": n * sympy.pi / 2}], c, families=[n]))


def test_a_solution_outside_the_real_domain_is_refuted() -> None:
    c = ctx()
    eq = [tree(c, "x^2 + 1")]
    assert checks.solutions(eq, ["x"], [{"x": sympy.I}], c).status == "passed"
    assert _refuted(checks.solutions(eq, ["x"], [{"x": sympy.I}], c, domain="real"), "real")


# --- limit ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("expression", "point", "direction", "right", "wrong"), [
    ("sin(x)/x", "0", "+-", "1", "0"),
    ("x*log(x)", "0", "+", "0", "1"),
    ("(1 + 1/x)^x", "oo", "-", "E", "1"),
    ("(cos(x) - 1)/x^2", "0", "+-", "-1/2", "1/2"),
])
def test_a_limit_is_rechecked_by_approaching_the_point(expression, point, direction, right, wrong) -> None:
    c = ctx({"x": ["real"]})
    where = tree(c, point, infinity=True)
    good = checks.limit(tree(c, expression), sym(c, right), "x", where, direction, c)
    assert good.status == "passed", good
    assert _refuted(checks.limit(tree(c, expression), sym(c, wrong), "x", where, direction, c))


def test_an_infinite_limit() -> None:
    c = ctx({"x": ["real"]})
    assert checks.limit(tree(c, "1/x"), sympy.oo, "x", tree(c, "0"), "+", c).status == "passed"
    assert checks.limit(tree(c, "1/x"), -sympy.oo, "x", tree(c, "0"), "+", c).status != "passed"
    assert _refuted(checks.limit(tree(c, "1/x"), sympy.oo, "x", tree(c, "oo", infinity=True), "-", c))


# --- series -----------------------------------------------------------------------------------------------------

def test_series_coefficients_are_rechecked_against_derivatives() -> None:
    c = ctx()
    zero = tree(c, "0")
    good = checks.series(tree(c, "exp(x)"), sym(c, "1 + x + x^2/2 + x^3/6"), "x", zero, 4, c)
    assert good.status == "passed"
    assert _refuted(checks.series(tree(c, "exp(x)"), sym(c, "1 + x + x^2 + x^3/6"), "x", zero, 4, c), "x^2")
    rational = checks.series(tree(c, "1/(1 - x)"), sym(c, "1 + x + x^2"), "x", zero, 3, c)
    assert rational.status == "passed" and any("exact" in m for m in rational.methods)
    assert _refuted(checks.series(tree(c, "1/(1 - x)"), sym(c, "1 + x + x^2 + x^3"), "x", zero, 3, c), "degree")
    shifted = checks.series(tree(c, "exp(x)"), sym(c, "E + E*(x - 1)"), "x", tree(c, "1"), 2, c)
    assert shifted.status == "passed"


def test_a_laurent_series_is_undecided() -> None:
    c = ctx()
    found = checks.series(tree(c, "1/sin(x)"), sym(c, "1/x + x/6"), "x", tree(c, "0"), 3, c)
    assert found.status == "undecided" and "Laurent" in found.why


# --- matrices ---------------------------------------------------------------------------------------------------

def test_a_determinant() -> None:
    c = ctx(["a", "b", "c1", "d"])
    numbers = matrix(c, [[1, 2], [3, 4]])
    assert checks.determinant(numbers, sympy.Integer(-2), c).status == "passed"
    assert _refuted(checks.determinant(numbers, sympy.Integer(2), c))
    letters = matrix(c, [["a", "b"], ["c1", "d"]])
    assert checks.determinant(letters, sym(c, "a*d - b*c1"), c).status == "passed"
    assert _refuted(checks.determinant(letters, sym(c, "a*d + b*c1"), c))


def test_an_inverse_multiplies_back_to_the_identity() -> None:
    c = ctx()
    m = matrix(c, [[1, 2], [3, 4]])
    good = sympy.Matrix([[-2, 1], [sympy.Rational(3, 2), sympy.Rational(-1, 2)]])
    assert checks.inverse(m, good, c).status == "passed"
    wrong = sympy.Matrix([[-2, 1], [sympy.Rational(3, 2), sympy.Rational(1, 2)]])
    assert _refuted(checks.inverse(m, wrong, c), "identity")


def test_eigenvalues_make_a_singular_matrix() -> None:
    c = ctx()
    m = matrix(c, [[2, 1], [1, 2]])
    assert checks.eigenvalues(m, {sympy.Integer(1): 1, sympy.Integer(3): 1}, c).status == "passed"
    assert _refuted(checks.eigenvalues(m, {sympy.Integer(1): 1, sympy.Integer(4): 1}, c), "4")
    assert _refuted(checks.eigenvalues(m, {sympy.Integer(3): 2}, c))
    rotation = matrix(c, [[0, -1], [1, 0]])
    assert checks.eigenvalues(rotation, {sympy.I: 1, -sympy.I: 1}, c).status == "passed"
