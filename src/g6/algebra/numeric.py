# SPDX-License-Identifier: Apache-2.0
"""G6's own numbers for its re-checks, computed from G6's parse tree and never by SymPy.

* :func:`evaluate`: the original expression evaluated on mpmath, function by function from the tree (each function is
  mpmath's own, with the same principal branches as SymPy's), at a working precision; :func:`stable` evaluates it at
  30 and at 60 digits and keeps a value only when both agree, are finite and are not huge, so a point near a singular
  point or with heavy cancellation is not used;
* :func:`exact`: a rational expression (numbers, symbols, + - * / and whole powers) evaluated exactly in fractions at
  integer points; two rational functions that agree at random integer points drawn from a range of two million are
  equal but with a chance below their degree divided by two million at each point (Schwartz and Zippel);
* :func:`taylor`: the exact Taylor coefficients of a rational expression at a rational point, by arithmetic on
  truncated power series in fractions (so ``k!`` times the ``k``-th is the ``k``-th derivative, exactly);
* :func:`draw`: seeded random points that honour each symbol's assumptions (integers for ``integer``, positive reals for
  ``positive``, reals for ``real``, complex numbers otherwise), the same exact numbers given to both sides;
* :func:`sympy_value`: SymPy's answer evaluated by SymPy at such a point, converted to mpmath;
* :func:`close`: agreement within :data:`REL_TOL` of the larger size, or :data:`ABS_TOL`.
"""
from __future__ import annotations

import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

import mpmath
import sympy

from g6.algebra.language import BinOp, Call, Const, Equation, Neg, Node, Num, Sym

LOW_DPS, HIGH_DPS, SYMPY_DPS = 30, 60, 50
REL_TOL, ABS_TOL = 1e-15, 1e-20
HUGE = mpmath.mpf(10) ** 50
MIN_POINTS, TARGET_POINTS, MAX_ATTEMPTS = 3, 6, 40
EXACT_RANGE = 10**6
_MAX_EXACT_BITS = 200_000


class Unusable(Exception):  # noqa: N818 -- a point, not an error of G6's
    """A point where a value is singular, not a number, too large or unstable: it is skipped, never counted."""


class NotRational(Exception):  # noqa: N818
    """The expression is not rational (it has a function, a constant or a power that is not whole)."""


def _log(x: Any, base: Any = None) -> Any:
    return mpmath.log(x) if base is None else mpmath.log(x, base)


_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "sin": mpmath.sin, "cos": mpmath.cos, "tan": mpmath.tan, "cot": mpmath.cot, "sec": mpmath.sec, "csc": mpmath.csc,
    "asin": mpmath.asin, "acos": mpmath.acos, "atan": mpmath.atan,
    "sinh": mpmath.sinh, "cosh": mpmath.cosh, "tanh": mpmath.tanh, "asinh": mpmath.asinh, "acosh": mpmath.acosh,
    "atanh": mpmath.atanh, "exp": mpmath.exp, "log": _log, "sqrt": mpmath.sqrt,
    "root": lambda x, n: mpmath.power(x, 1 / mpmath.mpmathify(n)),
    "abs": mpmath.fabs, "sign": mpmath.sign, "floor": mpmath.floor, "ceiling": mpmath.ceil, "re": mpmath.re,
    "im": mpmath.im, "conjugate": mpmath.conj, "factorial": mpmath.factorial, "binomial": mpmath.binomial,
}


def evaluate(node: Node, values: Mapping[str, Any]) -> Any:
    """``node`` at ``values`` (name to an mpmath number), at mpmath's current precision. An equation is ``left -
    right``. Raises :class:`Unusable` at a singular point."""
    try:
        return _evaluate(node, values)
    except (ZeroDivisionError, ValueError, OverflowError, ArithmeticError) as exc:
        raise Unusable(f"{type(exc).__name__}: {exc}"[:200]) from None


def _evaluate(node: Node, values: Mapping[str, Any]) -> Any:
    if isinstance(node, Num):
        return mpmath.mpf(node.value.numerator) / node.value.denominator
    if isinstance(node, Sym):
        return values[node.name]
    if isinstance(node, Const):
        return {"pi": mpmath.pi, "E": mpmath.e, "I": mpmath.j, "oo": mpmath.inf}[node.name] + 0
    if isinstance(node, Neg):
        return -_evaluate(node.operand, values)
    if isinstance(node, Equation):
        return _evaluate(node.left, values) - _evaluate(node.right, values)
    if isinstance(node, Call):
        return _FUNCTIONS[node.name](*(_evaluate(a, values) for a in node.args))
    a, b = _evaluate(node.left, values), _evaluate(node.right, values)
    if node.op == "+":
        return a + b
    if node.op == "-":
        return a - b
    if node.op == "*":
        return a * b
    if node.op == "/":
        if b == 0:
            raise ZeroDivisionError("division by zero")
        return a / b
    if a == 0 and b != 0 and mpmath.re(b) <= 0:
        raise ZeroDivisionError("0 to a power whose real part is not positive")
    return mpmath.power(a, b)


def size(value: Any) -> Any:
    return mpmath.fabs(value)


def usable(value: Any) -> bool:
    """A finite number that is not huge."""
    try:
        return bool(mpmath.isfinite(value)) and size(value) < HUGE
    except (TypeError, ValueError):
        return False


def close(a: Any, b: Any, rel: float = REL_TOL, abs_tol: float = ABS_TOL) -> bool:
    return size(a - b) <= abs_tol + rel * max(size(a), size(b))


def at(function: Callable[[], Any], dps: int) -> Any:
    with mpmath.workdps(dps):
        return function()


def stable(function: Callable[[], Any]) -> Any:
    """``function()`` at 30 and at 60 digits: the 60-digit value when both are usable and agree, else
    :class:`Unusable`."""
    low = at(function, LOW_DPS)
    high = at(function, HIGH_DPS)
    if not (usable(low) and usable(high)):
        raise Unusable("a value is not finite, or is too large, here")
    if not close(low, high, 1e-20, 1e-25):
        raise Unusable("the value changes with the working precision here (cancellation or a singular point)")
    return high


def to_mp(value: Any, dps: int = SYMPY_DPS) -> Any:
    """A SymPy number as an mpmath number with ``dps`` digits (mpmath rounds a new number to its working precision, so
    it is made inside one this wide); :class:`Unusable` when it is not a finite number."""
    if not getattr(value, "is_number", False) or value.has(sympy.zoo, sympy.oo, -sympy.oo, sympy.nan):
        raise Unusable("SymPy's value here is not a finite number")
    real, imag = value.as_real_imag()
    try:
        with mpmath.workdps(dps + 10):
            parts = [mpmath.mpf(sympy.Float(p, dps)._mpf_) for p in (real, imag)]
            return parts[0] if parts[1] == 0 else mpmath.mpc(parts[0], parts[1])
    except (TypeError, ValueError, AttributeError) as exc:
        raise Unusable(f"SymPy's value here is not a number: {exc}"[:200]) from None


def sympy_value(expr: Any, subs: Mapping[Any, Any], dps: int = SYMPY_DPS) -> Any:
    """``expr`` evaluated by SymPy at ``subs`` (SymPy symbol to an exact SymPy number), as an mpmath number."""
    try:
        value = expr.evalf(dps, subs=dict(subs)) if subs else expr.evalf(dps)
    except (ZeroDivisionError, ValueError, TypeError, ArithmeticError, RecursionError) as exc:
        raise Unusable(f"SymPy could not evaluate its answer here: {type(exc).__name__}"[:200]) from None
    out = to_mp(value, dps)
    if not usable(out):
        raise Unusable("SymPy's value here is not finite, or is too large")
    return out


# --- points --------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Point:
    """One sample point: each symbol's value as an exact real and imaginary part."""

    values: dict[str, tuple[Fraction, Fraction]]

    def mp(self) -> dict[str, Any]:
        out = {}
        for name, (re_, im_) in self.values.items():
            real = mpmath.mpf(re_.numerator) / re_.denominator
            out[name] = real if im_ == 0 else mpmath.mpc(real, mpmath.mpf(im_.numerator) / im_.denominator)
        return out

    def sympy(self, table: Mapping[str, Any]) -> dict[Any, Any]:
        return {table[name]: sympy.Rational(r.numerator, r.denominator)
                + (sympy.I * sympy.Rational(i.numerator, i.denominator) if i else 0)
                for name, (r, i) in self.values.items() if name in table}

    def shown(self) -> dict[str, str]:
        return {name: (str(r) if not i else f"{r} + {i}*I") for name, (r, i) in self.values.items()}


def draw(declared: Mapping[str, tuple[str, ...]], rng: random.Random, names: Any = None) -> Point:
    """A seeded random point for ``names`` (all declared symbols by default), honouring each one's assumptions."""
    out: dict[str, tuple[Fraction, Fraction]] = {}
    for name in sorted(declared if names is None else names):
        assumed = declared.get(name, ())
        if "integer" in assumed:
            value = Fraction(rng.randint(1, 9) if "positive" in assumed else rng.randint(-9, 9))
            out[name] = (value, Fraction(0))
        elif "positive" in assumed:
            out[name] = (Fraction(rng.randint(100, 3000), 1000), Fraction(0))
        elif "real" in assumed:
            out[name] = (Fraction(rng.randint(-3000, 3000), 1000), Fraction(0))
        else:
            out[name] = (Fraction(rng.randint(-2000, 2000), 1000), Fraction(rng.randint(-2000, 2000), 1000))
    return Point(out)


def draw_integers(declared: Mapping[str, tuple[str, ...]], rng: random.Random, names: Any = None) -> dict[str, Fraction]:
    """A random integer point for the exact checks (positive where the symbol is declared positive)."""
    return {name: Fraction(rng.randint(1, EXACT_RANGE) if "positive" in declared.get(name, ()) else
                           rng.randint(-EXACT_RANGE, EXACT_RANGE))
            for name in sorted(declared if names is None else names)}


# --- exact arithmetic on rational expressions -----------------------------------------------------------------------

def is_rational(node: Node) -> bool:
    """Numbers, symbols, + - * / and powers whose exponent is a whole number written as one."""
    if isinstance(node, Num | Sym):
        return True
    if isinstance(node, Neg):
        return is_rational(node.operand)
    if isinstance(node, Equation):
        return is_rational(node.left) and is_rational(node.right)
    if isinstance(node, BinOp):
        if node.op == "^":
            return is_rational(node.left) and _whole(node.right) is not None
        return is_rational(node.left) and is_rational(node.right)
    return False


def _whole(node: Node) -> int | None:
    if isinstance(node, Num) and node.value.denominator == 1:
        return node.value.numerator
    if isinstance(node, Neg):
        inner = _whole(node.operand)
        return None if inner is None else -inner
    return None


def exact(node: Node, values: Mapping[str, Fraction]) -> Fraction:
    """A rational ``node`` at ``values``, exactly. :class:`NotRational` when it is not rational, :class:`Unusable` at a
    pole or when the numbers grow past a size G6 computes."""
    if isinstance(node, Num):
        return node.value
    if isinstance(node, Sym):
        return values[node.name]
    if isinstance(node, Neg):
        return -exact(node.operand, values)
    if isinstance(node, Equation):
        return exact(node.left, values) - exact(node.right, values)
    if not isinstance(node, BinOp):
        raise NotRational(type(node).__name__)
    if node.op == "^":
        n = _whole(node.right)
        if n is None:
            raise NotRational("a power that is not whole")
        base = exact(node.left, values)
        if base == 0 and n < 0:
            raise Unusable("0 to a negative power")
        if (base.numerator.bit_length() + base.denominator.bit_length()) * abs(n) > _MAX_EXACT_BITS:
            raise Unusable("the exact value is too large to compute")
        return base ** n
    a, b = exact(node.left, values), exact(node.right, values)
    if node.op == "/":
        if b == 0:
            raise Unusable("a pole")
        out = a / b
    else:
        out = a + b if node.op == "+" else a - b if node.op == "-" else a * b
    if out.numerator.bit_length() + out.denominator.bit_length() > _MAX_EXACT_BITS:
        raise Unusable("the exact value is too large to compute")
    return out


def sympy_exact(expr: Any, table: Mapping[str, Any], values: Mapping[str, Fraction]) -> Fraction | None:
    """SymPy's ``expr`` at ``values``, exactly, when it comes out a rational number (``None`` otherwise)."""
    try:
        out = expr.xreplace({table[n]: sympy.Rational(v.numerator, v.denominator) for n, v in values.items()
                             if n in table})
    except (ZeroDivisionError, ValueError, TypeError, RecursionError):
        return None
    if isinstance(out, sympy.Rational):
        return Fraction(int(out.p), int(out.q))
    return None


def taylor(node: Node, variable: str, values: Mapping[str, Fraction], point: Fraction, order: int) -> list[Fraction]:
    """The first ``order`` Taylor coefficients of a rational ``node`` in ``variable`` at ``point``, exactly."""
    zero = [Fraction(0)] * order

    def const(v: Fraction) -> list[Fraction]:
        return [v, *zero[1:]]

    def mul(a: list[Fraction], b: list[Fraction]) -> list[Fraction]:
        return [sum((a[i] * b[k - i] for i in range(k + 1)), Fraction(0)) for k in range(order)]

    def div(a: list[Fraction], b: list[Fraction]) -> list[Fraction]:
        if b[0] == 0:
            raise Unusable("a pole at the expansion point")
        q: list[Fraction] = []
        for k in range(order):
            q.append((a[k] - sum((q[i] * b[k - i] for i in range(k)), Fraction(0))) / b[0])
        return q

    def walk(n: Node) -> list[Fraction]:
        if isinstance(n, Num):
            return const(n.value)
        if isinstance(n, Sym):
            return [point, Fraction(1), *zero[2:]][:order] if n.name == variable else const(values[n.name])
        if isinstance(n, Neg):
            return [-c for c in walk(n.operand)]
        if not isinstance(n, BinOp):
            raise NotRational(type(n).__name__)
        if n.op == "^":
            k = _whole(n.right)
            if k is None:
                raise NotRational("a power that is not whole")
            base, out = walk(n.left), const(Fraction(1))
            for _ in range(abs(k)):
                out = mul(out, base)
            return out if k >= 0 else div(const(Fraction(1)), out)
        a, b = walk(n.left), walk(n.right)
        if n.op == "+":
            return [x + y for x, y in zip(a, b, strict=True)]
        if n.op == "-":
            return [x - y for x, y in zip(a, b, strict=True)]
        return mul(a, b) if n.op == "*" else div(a, b)

    return walk(node)


__all__ = ["ABS_TOL", "EXACT_RANGE", "HIGH_DPS", "LOW_DPS", "MAX_ATTEMPTS", "MIN_POINTS", "NotRational", "Point",
           "REL_TOL", "SYMPY_DPS", "TARGET_POINTS", "Unusable", "at", "close", "draw", "draw_integers", "evaluate",
           "exact", "is_rational", "size", "stable", "sympy_exact", "sympy_value", "taylor", "to_mp", "usable"]
