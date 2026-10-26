# SPDX-License-Identifier: Apache-2.0
"""The expression language of g6.algebra, read by G6's own parser into a tree of its own. Nothing in it is evaluated.

SymPy's string readers evaluate their input as Python, so caller text reaches nothing but this parser; the SymPy
objects are built from its tree, through SymPy's constructors (:mod:`g6.algebra.build`). The parser itself is the
core's (:mod:`g6.core.formula`); this module is g6.algebra's dialect of it, with the same names as before. The
language, strictly:

* numbers: integers (``42``) and decimals (``2.5``, read as the exact rational 5/2); a rational is a division
  (``3/4``). At most 200 digits each; no scientific notation (write ``1.5*10^3``);
* symbols the caller declares (:func:`declare`): letters and digits, single underscores between them, at most 31
  characters, each with optional assumptions from ``real``, ``positive`` and ``integer``. A name that is a Python
  keyword or built-in, a function or a constant cannot be declared;
* ``+ - * / ^`` (``**`` is the same as ``^``), unary ``+`` and ``-``, and parentheses. ``^`` binds tighter than unary
  minus (``-x^2`` is ``-(x^2)``) and groups to the right (``2^3^2`` is ``2^9``). Every product is written (``2*x``);
* the functions in :data:`FUNCTIONS`, called by name with their number of arguments, and the constants ``pi``, ``E``
  and ``I``; ``oo`` (infinity) only where an op takes a point or a bound;
* in an equation, one ``=`` (or ``==``) between two expressions, at the top level.

Everything else is refused, with its code, the field, the line and the column: strings, attribute access (``.``),
brackets, braces, keywords (``lambda``, ``import``), names with leading, trailing or doubled underscores
(``__import__``), comments, semicolons, keyword arguments, ``!``, comparisons, and any character outside printable
ASCII (``G6_E_ALGEBRA_CONSTRUCT_REFUSED``); an unknown function (``_UNKNOWN_FUNCTION``), an undeclared name
(``_UNKNOWN_SYMBOL``), a wrong number of arguments (``_WRONG_ARGUMENTS``) and a malformed expression
(``_PARSE_ERROR``).

Limits, checked before anything is built, so SymPy is never asked to compute a number without end: at most 2,000
characters, 500 nodes and 40 levels of nesting (``_INPUT_TOO_LARGE``); every exponent at most 1,000 in size, judged on
each part of the exponent that has no symbol, so ``2^(10^10)``, ``x^(10^10)`` and ``2^(x + 10^10)`` are all refused,
and an exponent G6 cannot bound (``2^(1/sin(1))``) is refused too (``_EXPONENT_TOO_LARGE``); factorial and binomial
arguments at most 1,000 (``_FACTORIAL_TOO_LARGE``); exact numbers of at most 10,000 digits, and ``floor`` and
``ceiling`` only of numbers G6 can bound to that size (``_NUMBER_TOO_LARGE``). The bounds are worked out on the tree
from upper bounds of each part's size, never by evaluating it.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Any

from g6.algebra.envelope import Refusal
from g6.core import formula as _formula
from g6.core.formula import (
    ASSUMPTIONS,
    MAX_BITS,
    MAX_CHARS,
    MAX_DEPTH,
    MAX_DIGITS,
    MAX_EXPONENT,
    MAX_FACTORIAL,
    MAX_LITERAL_DIGITS,
    MAX_NAME,
    MAX_NODES,
    MAX_SYMBOLS,
    BinOp,
    Call,
    Const,
    Equation,
    Neg,
    Node,
    Num,
    Sym,
    children,
    free_symbols,
    render,
)

#: The functions an expression may call, by name, with the numbers of arguments each takes.
FUNCTIONS: dict[str, tuple[int, ...]] = {
    "sin": (1,), "cos": (1,), "tan": (1,), "cot": (1,), "sec": (1,), "csc": (1,),
    "asin": (1,), "acos": (1,), "atan": (1,),
    "sinh": (1,), "cosh": (1,), "tanh": (1,), "asinh": (1,), "acosh": (1,), "atanh": (1,),
    "exp": (1,), "log": (1, 2), "sqrt": (1,), "root": (2,),
    "abs": (1,), "sign": (1,), "floor": (1,), "ceiling": (1,), "re": (1,), "im": (1,), "conjugate": (1,),
    "factorial": (1,), "binomial": (2,),
}
#: SageMath's functions ``sage_evaluate`` may call: maths on their arguments, none taking a string, opening a file or
#: reaching the network. Keyword arguments cannot be written at all (``integrate``'s ``algorithm=`` can send work to an
#: outside service).
SAGE_FUNCTIONS: dict[str, tuple[int, ...]] = {
    "sin": (1,), "cos": (1,), "tan": (1,), "cot": (1,), "sec": (1,), "csc": (1,),
    "arcsin": (1,), "arccos": (1,), "arctan": (1,),
    "sinh": (1,), "cosh": (1,), "tanh": (1,), "arcsinh": (1,), "arccosh": (1,), "arctanh": (1,),
    "exp": (1,), "log": (1, 2), "sqrt": (1,), "abs": (1,), "floor": (1,), "ceil": (1,),
    "real_part": (1,), "imag_part": (1,), "conjugate": (1,), "factorial": (1,), "binomial": (2,),
    "factor": (1,), "expand": (1,), "simplify": (1,), "diff": (2, 3), "integrate": (2, 4), "taylor": (4,),
    "solve": (2,), "is_prime": (1,), "gcd": (2,), "lcm": (2,), "euler_phi": (1,), "divisors": (1,),
    "next_prime": (1,), "fibonacci": (1,), "numerical_approx": (1, 2),
}
CONSTANTS = ("pi", "E", "I")
INFINITY = "oo"
#: g6.algebra's dialect of G6's expression language: codes ``G6_E_ALGEBRA_*``, refusals
#: :class:`g6.algebra.envelope.Refusal`, and the SageMath names reserved so no symbol can take them.
DIALECT = _formula.Dialect(
    name="algebra", prefix="G6_E_ALGEBRA", functions=FUNCTIONS, constants=CONSTANTS, infinity=INFINITY,
    reserved=frozenset(SAGE_FUNCTIONS) | {"var", "assume", "latex", "version", "Integer", "Rational", "RealNumber",
                                          "infinity"},
    refusal=Refusal)
_RESERVED = DIALECT.names_reserved()


def declare(symbols: Any, implicit: Iterable[str] = ()) -> dict[str, tuple[str, ...]]:
    """The symbols an op may use, each with its assumptions: ``symbols`` is a list of names or a mapping from each name
    to a list drawn from ``real``, ``positive`` and ``integer``; ``implicit`` are names the op itself declares (its
    variable or unknowns), added without assumptions when ``symbols`` does not list them."""
    return _formula.declare(symbols, DIALECT, implicit)


def parse(text: Any, symbols: Mapping[str, tuple[str, ...]], *, field: str = "expression", equation: bool = False,
          infinity: bool = False, functions: Mapping[str, tuple[int, ...]] | None = None) -> Node:
    """``text`` read as one expression (or, with ``equation``, an equation or an expression meaning ``= 0``) over the
    declared ``symbols``; refused with a code, ``field``, the line and the column otherwise. ``infinity`` lets ``oo``
    stand as a point or a bound. Its size limits are checked before it is returned."""
    dialect = DIALECT if functions is None else replace(DIALECT, functions=functions)
    return _formula.parse(text, symbols, dialect, field=field, equation=equation, infinity=infinity)


__all__ = ["ASSUMPTIONS", "BinOp", "CONSTANTS", "Call", "Const", "DIALECT", "Equation", "FUNCTIONS", "INFINITY",
           "MAX_BITS", "MAX_CHARS", "MAX_DEPTH", "MAX_DIGITS", "MAX_EXPONENT", "MAX_FACTORIAL", "MAX_LITERAL_DIGITS",
           "MAX_NAME", "MAX_NODES", "MAX_SYMBOLS", "Neg", "Node", "Num", "SAGE_FUNCTIONS", "Sym", "children",
           "declare", "free_symbols", "parse", "render"]
