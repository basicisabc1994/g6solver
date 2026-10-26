# SPDX-License-Identifier: Apache-2.0
"""The complex expression domain colouring draws: parsed against an allow-list, then walked by G6's own evaluator.

``ast.parse`` only parses; nothing here hands text to ``eval``, ``exec`` or ``compile`` (the archive's ``eval`` with
emptied builtins could be escaped by an attribute walk, ADAPT_GENERATIVE_ART-02). :func:`parse` accepts only: the name
``z``; the constants ``pi``, ``e``, ``i`` and ``j``; numbers (not booleans); ``+ - * / **``; unary ``+`` and ``-``; and
one-argument calls of the functions in :data:`FUNCTIONS`. Anything else (attributes, subscripts, other names, keyword
or starred arguments, comprehensions, lambdas, comparisons, strings) is refused with :class:`ExpressionError` before
anything is evaluated. :func:`evaluate` computes in NumPy complex numbers, so a huge power overflows to infinity instead
of building a Python big integer.
"""
from __future__ import annotations

import ast
import math
from typing import Any

#: The functions an expression may call, by the NumPy function that computes each.
FUNCTIONS = ("sin", "cos", "tan", "sinh", "cosh", "tanh", "exp", "log", "sqrt", "abs", "conj", "real", "imag")
CONSTANTS = {"pi": math.pi, "e": math.e, "i": 1j, "j": 1j}
MAX_LENGTH = 200
MAX_NODES = 80
MAX_DEPTH = 24
_BINARY = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)
_UNARY = (ast.UAdd, ast.USub)


class ExpressionError(ValueError):
    """An expression outside the allow-list."""


def _check(node: ast.AST, depth: int, count: list[int]) -> None:
    count[0] += 1
    if depth > MAX_DEPTH or count[0] > MAX_NODES:
        raise ExpressionError(f"the expression is too deep or too long (at most {MAX_DEPTH} levels, {MAX_NODES} parts)")
    if isinstance(node, ast.BinOp) and isinstance(node.op, _BINARY):
        _check(node.left, depth + 1, count)
        _check(node.right, depth + 1, count)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, _UNARY):
        _check(node.operand, depth + 1, count)
    elif isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, int | float | complex):
            raise ExpressionError(f"{node.value!r} is not allowed: only numbers")
    elif isinstance(node, ast.Name):
        if node.id != "z" and node.id not in CONSTANTS:
            raise ExpressionError(f"the name {node.id!r} is not allowed: use z, pi, e, i or j")
    elif isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
            raise ExpressionError(f"that call is not allowed: call one of {', '.join(FUNCTIONS)}")
        if len(node.args) != 1 or node.keywords or isinstance(node.args[0], ast.Starred):
            raise ExpressionError(f"{node.func.id} takes exactly one plain argument")
        _check(node.args[0], depth + 1, count)
    else:
        raise ExpressionError(f"{type(node).__name__} is not allowed in an expression")


def parse(text: str) -> ast.expr:
    """The checked syntax tree of ``text``, or :class:`ExpressionError`."""
    if not text.strip() or len(text) > MAX_LENGTH:
        raise ExpressionError(f"an expression has 1 to {MAX_LENGTH} characters")
    try:
        tree = ast.parse(text, mode="eval")
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        raise ExpressionError(f"not an expression: {type(exc).__name__}") from None
    _check(tree.body, 0, [0])
    return tree.body


def evaluate(node: ast.expr, z: Any) -> Any:
    """``node`` (from :func:`parse`) computed over the complex array ``z``."""
    import numpy as np

    def walk(n: ast.expr) -> Any:
        if isinstance(n, ast.Constant):
            return np.complex128(n.value)
        if isinstance(n, ast.Name):
            return z if n.id == "z" else np.complex128(CONSTANTS[n.id])
        if isinstance(n, ast.UnaryOp):
            value = walk(n.operand)
            return -value if isinstance(n.op, ast.USub) else value
        if isinstance(n, ast.BinOp):
            left, right = walk(n.left), walk(n.right)
            op = type(n.op)
            if op is ast.Add:
                return left + right
            if op is ast.Sub:
                return left - right
            if op is ast.Mult:
                return left * right
            if op is ast.Div:
                return left / right
            return np.power(left, right)
        if isinstance(n, ast.Call):
            arg = walk(n.args[0])
            name = n.func.id  # type: ignore[attr-defined]
            if name in ("real", "imag"):
                return np.complex128(1) * getattr(np, name)(arg)
            return getattr(np, name)(arg)
        raise ExpressionError(f"{type(n).__name__} is not allowed in an expression")  # parse refused it already

    with np.errstate(all="ignore"):
        return walk(node)


__all__ = ["CONSTANTS", "FUNCTIONS", "MAX_LENGTH", "ExpressionError", "evaluate", "parse"]
