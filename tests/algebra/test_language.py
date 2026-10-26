# SPDX-License-Identifier: Apache-2.0
"""g6.algebra's own expression parser (owner, 2026-10-20): SymPy's string readers evaluate their input as Python, so
caller text reaches nothing but this parser, which builds a tree of its own and refuses everything outside a small
language, each refusal with its code, line and column. Pure Python: these tests need no SymPy."""
from __future__ import annotations

from fractions import Fraction

import pytest

from g6.algebra import language as L
from g6.algebra.envelope import (
    CONSTRUCT_REFUSED,
    EXPONENT_TOO_LARGE,
    FACTORIAL_TOO_LARGE,
    INPUT_INVALID,
    INPUT_TOO_LARGE,
    NUMBER_TOO_LARGE,
    PARSE_ERROR,
    SYMBOL_INVALID,
    UNKNOWN_FUNCTION,
    UNKNOWN_SYMBOL,
    WRONG_ARGUMENTS,
    Refusal,
)

XY = L.declare(["x", "y"])


def _refused(text: str, symbols=XY, **kw) -> dict:
    with pytest.raises(Refusal) as caught:
        L.parse(text, symbols, **kw)
    first = caught.value.keep["refusals"][0]
    assert first["code"] == caught.value.code and first["reason"] == caught.value.reason
    return first


# --- what the language accepts --------------------------------------------------------------------------------------

def test_numbers_are_exact_integers_decimals_and_rationals() -> None:
    assert L.parse("42", XY) == L.Num(Fraction(42))
    assert L.parse("2.5", XY) == L.Num(Fraction(5, 2))
    assert L.parse("0.125", XY) == L.Num(Fraction(1, 8))
    assert L.parse("3/4", XY) == L.BinOp("/", L.Num(Fraction(3)), L.Num(Fraction(4)))


def test_operators_precedence_and_associativity() -> None:
    assert L.parse("x^2", XY) == L.parse("x**2", XY) == L.BinOp("^", L.Sym("x"), L.Num(Fraction(2)))
    assert L.parse("2^3^2", XY) == L.BinOp("^", L.Num(Fraction(2)), L.BinOp("^", L.Num(Fraction(3)),
                                                                             L.Num(Fraction(2))))
    assert L.parse("-x^2", XY) == L.Neg(L.BinOp("^", L.Sym("x"), L.Num(Fraction(2))))
    assert L.parse("x^-1", XY) == L.BinOp("^", L.Sym("x"), L.Neg(L.Num(Fraction(1))))
    assert L.parse("x - y - 1", XY) == L.BinOp("-", L.BinOp("-", L.Sym("x"), L.Sym("y")), L.Num(Fraction(1)))
    assert L.parse("x / y * 2", XY) == L.BinOp("*", L.BinOp("/", L.Sym("x"), L.Sym("y")), L.Num(Fraction(2)))
    assert L.parse(" ( x + y ) * 2 ", XY) == L.BinOp("*", L.BinOp("+", L.Sym("x"), L.Sym("y")), L.Num(Fraction(2)))


def test_functions_constants_and_equations() -> None:
    assert L.parse("sin(x)^2 + cos(x)^2", XY) == L.BinOp(
        "+", L.BinOp("^", L.Call("sin", (L.Sym("x"),)), L.Num(Fraction(2))),
        L.BinOp("^", L.Call("cos", (L.Sym("x"),)), L.Num(Fraction(2))))
    assert L.parse("log(x, 2)", XY) == L.Call("log", (L.Sym("x"), L.Num(Fraction(2))))
    assert L.parse("E^(I*pi)", XY) == L.BinOp("^", L.Const("E"), L.BinOp("*", L.Const("I"), L.Const("pi")))
    assert L.parse("x^2 = 4", XY, equation=True) == L.Equation(L.BinOp("^", L.Sym("x"), L.Num(Fraction(2))),
                                                              L.Num(Fraction(4)))
    assert L.parse("x^2 == 4", XY, equation=True) == L.parse("x^2 = 4", XY, equation=True)
    assert L.parse("-oo", XY, infinity=True) == L.Neg(L.Const("oo"))
    assert set(L.FUNCTIONS) >= {"sin", "cos", "tan", "exp", "log", "sqrt", "abs", "factorial", "binomial"}


def test_free_symbols_and_render_round_trip() -> None:
    texts = ["x^2 - 5*x + 6", "2*(x + 1)^2", "-(x - y)", "x/(y*2)", "2^3^2", "(2^3)^2", "-x^2", "(-x)^2",
             "sin(x)^2/3", "x - (y - 1)", "log(x, 2) + sqrt(y)", "1/2*x", "x^(1/2)", "(x + y)^-1", "E^(I*pi)",
             "2.5*x", "-1/2", "x*-1"]
    for text in texts:
        node = L.parse(text, XY)
        assert L.parse(L.render(node), XY) == node, (text, L.render(node))
    assert L.render(L.parse("x^2-5*x+6", XY)) == "x^2 - 5*x + 6"
    assert L.render(L.parse("2.50", XY)) == "2.5" and L.render(L.parse("0.125", XY)) == "0.125"
    assert L.render(L.parse("x**2", XY)) == "x^2"
    assert L.free_symbols(L.parse("x + sin(y) + pi", XY)) == {"x", "y"}


def test_declared_symbols_carry_their_assumptions() -> None:
    declared = L.declare({"x": ["real"], "n": ["integer", "positive"], "t": []}, implicit=["s"])
    assert declared == {"x": ("real",), "n": ("integer", "positive"), "t": (), "s": ()}
    assert L.declare(None, implicit=["x"]) == {"x": ()}
    assert L.declare(["x"], implicit=["x"]) == {"x": ()}


# --- what it refuses, each with a code, the line and the column -----------------------------------------------------

@pytest.mark.parametrize(("text", "code", "column"), [
    ("__import__('os')", CONSTRUCT_REFUSED, 1),
    ("x.real", CONSTRUCT_REFUSED, 2),
    ("(1).__class__", CONSTRUCT_REFUSED, 4),
    ("lambda: 1", CONSTRUCT_REFUSED, 1),
    ("lambda x: x", CONSTRUCT_REFUSED, 1),
    ("'os'", CONSTRUCT_REFUSED, 1),
    ('x + "y"', CONSTRUCT_REFUSED, 5),
    ("x[0]", CONSTRUCT_REFUSED, 2),
    ("[1, 2]", CONSTRUCT_REFUSED, 1),
    ("{x}", CONSTRUCT_REFUSED, 1),
    ("x; y", CONSTRUCT_REFUSED, 2),
    ("x # note", CONSTRUCT_REFUSED, 3),
    ("5!", CONSTRUCT_REFUSED, 2),
    ("x < y", CONSTRUCT_REFUSED, 3),
    ("x = 1", CONSTRUCT_REFUSED, 3),
    ("sin(x=1)", CONSTRUCT_REFUSED, 6),
    ("x²", CONSTRUCT_REFUSED, 2),
    ("π", CONSTRUCT_REFUSED, 1),
    ("x\x00", CONSTRUCT_REFUSED, 2),
    ("import os", CONSTRUCT_REFUSED, 1),
    ("not x", CONSTRUCT_REFUSED, 1),
    ("None", CONSTRUCT_REFUSED, 1),
    ("x_", CONSTRUCT_REFUSED, 1),
    ("oo", CONSTRUCT_REFUSED, 1),
    ("eval(x)", UNKNOWN_FUNCTION, 1),
    ("exec(x)", UNKNOWN_FUNCTION, 1),
    ("sympify(x)", UNKNOWN_FUNCTION, 1),
    ("open(x)", UNKNOWN_FUNCTION, 1),
    ("foo(x)", UNKNOWN_FUNCTION, 1),
    ("z + 1", UNKNOWN_SYMBOL, 1),
    ("x + os", UNKNOWN_SYMBOL, 5),
    ("sin(x, y)", WRONG_ARGUMENTS, 1),
    ("log()", WRONG_ARGUMENTS, 1),
    ("sin + 1", PARSE_ERROR, 1),
    ("pi(2)", PARSE_ERROR, 3),
    ("2x", PARSE_ERROR, 2),
    ("1e10", PARSE_ERROR, 2),
    ("(x + 1", PARSE_ERROR, 7),
    ("x + 1)", PARSE_ERROR, 6),
    ("x +", PARSE_ERROR, 4),
    ("* x", PARSE_ERROR, 1),
    ("x y", PARSE_ERROR, 3),
    ("x^^2", PARSE_ERROR, 3),
    ("sin(x,)", PARSE_ERROR, 7),
])
def test_everything_outside_the_language_is_refused_where_it_is(text: str, code: str, column: int) -> None:
    found = _refused(text)
    assert (found["code"], found["line"], found["column"]) == (code, 1, column), found
    assert found["field"] == "expression" and found["reason"]


def test_a_refusal_names_the_line_and_the_field() -> None:
    found = _refused("x +\n  z", field="equations[1]")
    assert (found["code"], found["line"], found["column"], found["field"]) == (UNKNOWN_SYMBOL, 2, 3, "equations[1]")
    assert "symbols" in found["reason"]


def test_an_equation_has_one_equals_sign_at_the_top() -> None:
    assert _refused("x = y = 1", equation=True)["code"] == CONSTRUCT_REFUSED
    assert _refused("(x = 1)", equation=True)["code"] == CONSTRUCT_REFUSED
    assert _refused("= 1", equation=True)["code"] == PARSE_ERROR


@pytest.mark.parametrize(("text", "code"), [
    ("2^(10^10)", EXPONENT_TOO_LARGE),
    ("x^(10^10)", EXPONENT_TOO_LARGE),
    ("2^(x + 10^10)", EXPONENT_TOO_LARGE),
    ("2^1001", EXPONENT_TOO_LARGE),
    ("x^(-1001)", EXPONENT_TOO_LARGE),
    ("2^(1/sin(1))", EXPONENT_TOO_LARGE),
    ("2^floor(exp(100))", EXPONENT_TOO_LARGE),
    ("2^factorial(10)", EXPONENT_TOO_LARGE),
    ("(10^1000)^1000", NUMBER_TOO_LARGE),
    ("10^1000*10^1000*10^1000*10^1000*10^1000*10^1000*10^1000*10^1000*10^1000*10^1000*10^1000", NUMBER_TOO_LARGE),
    ("floor(exp(10^6))", NUMBER_TOO_LARGE),
    ("factorial(1001)", FACTORIAL_TOO_LARGE),
    ("factorial(10^6)", FACTORIAL_TOO_LARGE),
    ("factorial(x + 2000)", FACTORIAL_TOO_LARGE),
    ("binomial(10^5, 3)", FACTORIAL_TOO_LARGE),
    ("1" * 201, INPUT_TOO_LARGE),
])
def test_blow_ups_are_refused_before_anything_is_built(text: str, code: str) -> None:
    assert _refused(text)["code"] == code


@pytest.mark.parametrize("text", ["2^1000", "x^1000", "2^(x + 1000)", "2^(1/2)", "x^(1/3)", "E^(I*pi)", "2^pi",
                                  "2^sin(1)", "10^1000", "factorial(1000)", "factorial(x)", "binomial(1000, 500)",
                                  "exp(10^6)", "floor(7/2)", "2^(x^2 + 1)", "(x + 1)^1000", "2^-1000"])
def test_large_but_bounded_inputs_are_accepted(text: str) -> None:
    L.parse(text, XY)


def test_size_limits() -> None:
    assert _refused("")["code"] == INPUT_INVALID
    assert _refused("   ")["code"] == INPUT_INVALID
    assert _refused("x+" * 1000 + "x")["code"] == INPUT_TOO_LARGE
    assert _refused("(" * 41 + "x" + ")" * 41)["code"] == INPUT_TOO_LARGE
    L.parse("(" * 30 + "x" + ")" * 30, XY)
    assert _refused("+".join(["x"] * 600))["code"] == INPUT_TOO_LARGE


@pytest.mark.parametrize("bad", [["_x"], ["x__y"], ["1x"], ["sin"], ["pi"], ["E"], ["I"], ["oo"], ["lambda"],
                                 ["print"], ["x y"], [""], ["a" * 40], {"x": ["complex"]}, {"x": ["prime"]},
                                 {"x": "real"}, [1], "x"])
def test_a_symbol_must_be_a_plain_name_with_known_assumptions(bad) -> None:
    with pytest.raises(Refusal) as caught:
        L.declare(bad)
    assert caught.value.code == SYMBOL_INVALID


def test_too_many_symbols() -> None:
    with pytest.raises(Refusal) as caught:
        L.declare([f"s{i}" for i in range(L.MAX_SYMBOLS + 1)])
    assert caught.value.code == INPUT_TOO_LARGE


def test_a_custom_function_table_is_used_when_given() -> None:
    table = {"diff": (2, 3), "sin": (1,)}
    assert L.parse("diff(sin(x), x)", XY, functions=table) == L.Call("diff", (L.Call("sin", (L.Sym("x"),)),
                                                                              L.Sym("x")))
    with pytest.raises(Refusal) as caught:
        L.parse("cos(x)", XY, functions=table)
    assert caught.value.code == UNKNOWN_FUNCTION
