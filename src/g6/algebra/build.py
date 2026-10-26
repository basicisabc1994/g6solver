# SPDX-License-Identifier: Apache-2.0
"""SymPy objects built from G6's own parse tree, through SymPy's constructors.

No text ever reaches SymPy: a number becomes ``Rational(numerator, denominator)`` from Python integers, a symbol the
``Symbol`` declared under its checked name (:func:`g6.algebra.language.declare`), an operator ``Add``, ``Mul`` or
``Pow``, and a function the SymPy class the table below names. Imported only where SymPy runs: G6's own worker
process (:mod:`g6.algebra.worker`) and tests.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import sympy

from g6.algebra.language import Call, Const, Equation, Neg, Node, Num, Sym

FUNCTIONS: dict[str, Any] = {
    "sin": sympy.sin, "cos": sympy.cos, "tan": sympy.tan, "cot": sympy.cot, "sec": sympy.sec, "csc": sympy.csc,
    "asin": sympy.asin, "acos": sympy.acos, "atan": sympy.atan,
    "sinh": sympy.sinh, "cosh": sympy.cosh, "tanh": sympy.tanh, "asinh": sympy.asinh, "acosh": sympy.acosh,
    "atanh": sympy.atanh, "exp": sympy.exp, "log": sympy.log, "sqrt": sympy.sqrt, "root": sympy.root,
    "abs": sympy.Abs, "sign": sympy.sign, "floor": sympy.floor, "ceiling": sympy.ceiling, "re": sympy.re,
    "im": sympy.im, "conjugate": sympy.conjugate, "factorial": sympy.factorial, "binomial": sympy.binomial,
}
CONSTANTS: dict[str, Any] = {"pi": sympy.pi, "E": sympy.E, "I": sympy.I, "oo": sympy.oo}
_MINUS_ONE = sympy.Integer(-1)


def symbols(declared: Mapping[str, tuple[str, ...]]) -> dict[str, sympy.Symbol]:
    """One SymPy symbol per declared name, with its assumptions (``real``, ``positive``, ``integer``)."""
    return {name: sympy.Symbol(name, **{a: True for a in assumed}) for name, assumed in declared.items()}


def expression(node: Node, table: Mapping[str, sympy.Symbol]) -> Any:
    """The SymPy expression for ``node`` (an equation becomes ``Eq``)."""
    if isinstance(node, Num):
        return sympy.Rational(node.value.numerator, node.value.denominator)
    if isinstance(node, Sym):
        return table[node.name]
    if isinstance(node, Const):
        return CONSTANTS[node.name]
    if isinstance(node, Neg):
        return sympy.Mul(_MINUS_ONE, expression(node.operand, table))
    if isinstance(node, Equation):
        return sympy.Eq(expression(node.left, table), expression(node.right, table))
    if isinstance(node, Call):
        return FUNCTIONS[node.name](*(expression(a, table) for a in node.args))
    a, b = expression(node.left, table), expression(node.right, table)
    if node.op == "+":
        return sympy.Add(a, b)
    if node.op == "-":
        return sympy.Add(a, sympy.Mul(_MINUS_ONE, b))
    if node.op == "*":
        return sympy.Mul(a, b)
    if node.op == "/":
        return sympy.Mul(a, sympy.Pow(b, _MINUS_ONE))
    return sympy.Pow(a, b)


def difference(node: Node, table: Mapping[str, sympy.Symbol]) -> Any:
    """``left - right`` for an equation, the expression itself otherwise (an expression stands for ``= 0``)."""
    if isinstance(node, Equation):
        return sympy.Add(expression(node.left, table), sympy.Mul(_MINUS_ONE, expression(node.right, table)))
    return expression(node, table)


__all__ = ["CONSTANTS", "FUNCTIONS", "difference", "expression", "symbols"]
