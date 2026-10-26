# SPDX-License-Identifier: Apache-2.0
"""g6.core.formula: G6's own expression parser, moved out of g6.algebra so a jobs member can read a formula without
loading SymPy or the algebra family. A dialect says what a language allows (its functions and their arities, its
constants, comparisons, min and max, piecewise, the declared rounding functions) and the prefix of its codes; nothing
it reads is evaluated."""
from __future__ import annotations

import os
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import pytest

from g6.core import formula as F

PLAIN = F.Dialect(name="plain", prefix="G6_E_TESTLANG", functions={"sqrt": (1,)}, constants=("pi",))
RICH = F.Dialect(name="rich", prefix="G6_E_TESTLANG", functions={"sqrt": (1,), "exp": (1,)}, constants=("pi",),
                 comparisons=True, min_max=True, piecewise=True, rounding=True,
                 refused_names={"E": "write exp(1) for the number e", "I": "every value is real"})
XY = F.declare(["x", "y"], PLAIN)


def _refused(text: str, dialect: F.Dialect = PLAIN, symbols=XY) -> dict:
    with pytest.raises(F.FormulaRefused) as caught:
        F.parse(text, symbols, dialect, field="steps[0]")
    first = caught.value.keep["refusals"][0]
    assert first["code"] == caught.value.code and first["reason"] == caught.value.reason
    return first


def test_importing_the_core_parser_loads_neither_sympy_nor_the_algebra_family() -> None:
    # Fails if g6/core/formula.py imports g6.algebra (or anything that loads SymPy).
    src = Path(__file__).resolve().parents[2] / "src"
    code = ("import sys, g6.core.formula; "
            "print(sorted(m for m in sys.modules if m == 'sympy' or m.startswith('sympy.') "
            "or m == 'g6.algebra' or m.startswith('g6.algebra.')))")
    env = {k: v for k, v in os.environ.items() if k in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP")}
    env["PYTHONPATH"] = str(src)
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]"


def test_codes_carry_the_dialects_prefix_field_line_and_column() -> None:
    # Fails if the refusal code is built without Dialect.prefix (the algebra prefix would leak into every dialect).
    found = _refused("x +\n  * y")
    assert found["code"] == "G6_E_TESTLANG_PARSE_ERROR"
    assert (found["field"], found["line"], found["column"]) == ("steps[0]", 2, 3)
    assert _refused("cos(x)")["code"] == "G6_E_TESTLANG_UNKNOWN_FUNCTION"
    assert _refused("z")["code"] == "G6_E_TESTLANG_UNKNOWN_SYMBOL"
    assert _refused("sqrt(x, y)")["code"] == "G6_E_TESTLANG_WRONG_ARGUMENTS"
    assert _refused("x.real")["code"] == "G6_E_TESTLANG_CONSTRUCT_REFUSED"
    assert _refused("2^(10^10)")["code"] == "G6_E_TESTLANG_EXPONENT_TOO_LARGE"
    assert _refused("x" + " + x" * 600)["code"] == "G6_E_TESTLANG_INPUT_TOO_LARGE"
    with pytest.raises(F.FormulaRefused) as caught:
        F.declare(["sqrt"], PLAIN)
    assert caught.value.code == "G6_E_TESTLANG_SYMBOL_INVALID"


def test_a_dialect_without_comparisons_refuses_them() -> None:
    # Fails if the tokenizer reads < as a comparison when Dialect.comparisons is False.
    assert _refused("x < y")["code"] == "G6_E_TESTLANG_CONSTRUCT_REFUSED"
    assert _refused("min(x, y)")["code"] == "G6_E_TESTLANG_UNKNOWN_FUNCTION"
    assert _refused("piecewise((x, x < y), (y, otherwise))")["code"] == "G6_E_TESTLANG_CONSTRUCT_REFUSED"
    assert _refused("piecewise(x, y)")["code"] == "G6_E_TESTLANG_UNKNOWN_FUNCTION"


def test_piecewise_pairs_end_with_otherwise_and_comparisons_live_only_in_conditions() -> None:
    # Fails if Compare nodes are accepted outside a piecewise condition, or a piecewise may end without otherwise.
    symbols = F.declare(["x", "y"], RICH)
    tree = F.parse("piecewise((x/12.92, x <= 0.04045), (((x + 0.055)/1.055)^2.4, otherwise))", symbols, RICH)
    assert isinstance(tree, F.Call) and tree.name == "piecewise"
    first, last = tree.args
    assert first.cond == F.Compare("<=", F.Sym("x"), F.Num(Fraction("0.04045")))
    assert last.cond is None
    assert F.parse(F.render(tree), symbols, RICH) == tree
    for text in ("x < y", "max(x, y < 1)", "piecewise((x, x < y))", "piecewise((x, otherwise), (y, x < 1))",
                 "piecewise((x, 1 < x < 2), (y, otherwise))", "x = y"):
        with pytest.raises(F.FormulaRefused):
            F.parse(text, symbols, RICH)
    assert F.parse("max(x, y, 2) + min(x, 1)", symbols, RICH)
    assert F.parse("round_half_up(x, 2) + ceil_to(y, 0.5)", symbols, RICH)
    assert F.parse("piecewise((1, x == y), (0, otherwise))", symbols, RICH).args[0].cond.op == "=="


def test_refused_names_are_refused_and_cannot_be_declared() -> None:
    # Fails if Dialect.refused_names is not checked before a name is read as a symbol or a constant.
    symbols = F.declare(["x"], RICH)
    found = _refused("x + E", RICH, symbols)
    assert found["code"] == "G6_E_TESTLANG_CONSTRUCT_REFUSED" and "exp(1)" in found["reason"]
    assert _refused("I*x", RICH, symbols)["code"] == "G6_E_TESTLANG_CONSTRUCT_REFUSED"
    assert _refused("otherwise", RICH, symbols)["code"] == "G6_E_TESTLANG_CONSTRUCT_REFUSED"
    for name in ("E", "I", "otherwise", "min", "piecewise", "round_half_up"):
        with pytest.raises(F.FormulaRefused):
            F.declare([name], RICH)


def test_nothing_is_evaluated_and_the_tree_is_g6s_own() -> None:
    # Fails if parse returns anything but the core's own frozen node classes.
    tree = F.parse("sqrt(x) * pi - 3/4", XY, PLAIN)
    assert tree == F.BinOp("-", F.BinOp("*", F.Call("sqrt", (F.Sym("x"),)), F.Const("pi")),
                           F.BinOp("/", F.Num(Fraction(3)), F.Num(Fraction(4))))
    assert F.free_symbols(tree) == {"x"}
    assert F.render(tree) == "sqrt(x)*pi - 3/4"
