# SPDX-License-Identifier: Apache-2.0
"""G6's independent re-checks of SymPy's answers, run before an answer is reported.

The original input is evaluated by G6's own evaluator from G6's parse tree (:mod:`g6.algebra.numeric`, on mpmath),
never by SymPy; SymPy's answer is evaluated by SymPy. The two must agree, at seeded random points that honour each
symbol's assumptions, within 1e-15 of the larger size (or 1e-20). A point where either side is singular, not finite,
huge or changes with the working precision is skipped, never counted; a numeric leg needs agreement at three usable
points (one when nothing varies). Where the input is rational, an exact leg compares exact values at random integer
points drawn from two million (Schwartz and Zippel), or exact Taylor coefficients, in fractions.

* ``simplify``: numeric agreement, and the exact leg agreeing or SymPy's ``simplify`` of the difference reaching 0;
* ``expand``: numeric agreement and, where rational, the exact leg (expanding the answer back to the original would
  only repeat SymPy's own expansion); ``factor``: numeric agreement, and the exact leg or the factors expanded back to
  the expanded original;
* ``differentiate``: mpmath's numeric derivative of G6's evaluation (finite differences at raised precision) against
  SymPy's answer and, where rational, exact Taylor coefficients. A derivative through ``abs``, ``sign``, ``floor``,
  ``ceiling``, ``re``, ``im`` or ``conjugate`` of a variable not declared real is undecided: it has no complex sense;
* ``integrate``: an antiderivative differentiated back (by SymPy) and compared with G6's evaluation of the integrand,
  numerically or exactly, or failing that the difference simplified to 0; a definite integral against mpmath's
  quadrature of G6's evaluation, used only when the quadrature's own error estimate is small;
* ``solve``: every solution substituted back into every equation by G6's evaluator at 60 digits, the residual small
  against the size of the two sides, and confirmed with the solution at 100 digits before it refutes; a family over
  the integers at n = -2 to 2; a real-domain solution must be real;
* ``limit``: G6's evaluation at points approaching the point (from each side asked for, at up to 120 digits) must close
  in on the answer, or grow without bound for an infinite one; values that settle elsewhere refute it;
* ``series``: each coefficient against mpmath's Taylor coefficients of G6's evaluation, and exactly where rational;
  nothing at or above the order asked for. A Laurent or Puiseux series, or one at infinity, is undecided;
* ``determinant``, ``inverse``, ``eigenvalues``: G6's own exact elimination in fractions where the entries are
  rational, and mpmath's determinant and products on G6's evaluation of the entries; the inverse times the matrix must
  be the identity, each eigenvalue must make ``det(A - lambda I)`` vanish, and the eigenvalues with their
  multiplicities must match mpmath's own.

A disagreement refutes the answer (``G6_E_ALGEBRA_CERTIFICATE_REFUTED``); too few usable points, or a symbolic leg that
reaches no verdict, leaves it undecided, reported as not re-checked. Imported only where SymPy runs.
"""
from __future__ import annotations

import math
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

import mpmath
import sympy

from g6.algebra import build, numeric
from g6.algebra.language import Call, Const, Equation, Neg, Node, Num, children, free_symbols
from g6.algebra.numeric import MIN_POINTS, Unusable
from g6.algebra.schema import Recheck

_NOT_HOLOMORPHIC = frozenset({"abs", "sign", "floor", "ceiling", "re", "im", "conjugate"})
_EXACT_POINTS = 4
_SHOWN = 3
_PASS_RESIDUAL, _REFUTE_RESIDUAL = 1e-25, 1e-8


@dataclass
class Context:
    """The declared symbols, their SymPy symbols, the seed, and where to report a partial re-check."""

    declared: dict[str, tuple[str, ...]]
    table: dict[str, Any]
    seed: int = 0
    progress: Callable[[Recheck], None] = field(default=lambda found: None)

    @classmethod
    def make(cls, declared: Mapping[str, tuple[str, ...]], seed: int = 0,
             progress: Callable[[Recheck], None] | None = None) -> Context:
        return cls(dict(declared), build.symbols(declared), seed, progress or (lambda found: None))

    def rng(self, salt: str = "") -> random.Random:
        return random.Random(f"{self.seed}:{salt}")


def _show(value: Any) -> str:
    try:
        return mpmath.nstr(value, 12)
    except (TypeError, ValueError):
        return str(value)[:60]


def _names(*parts: Any, table: Mapping[str, Any], skip: Sequence[str] = ()) -> list[str]:
    """The declared names that the parse trees and SymPy expressions in ``parts`` use."""
    found: set[str] = set()
    for part in parts:
        if hasattr(part, "line"):
            found |= set(free_symbols(part))
        elif hasattr(part, "free_symbols"):
            found |= {str(s) for s in part.free_symbols if str(s) in table}
    return sorted(found - set(skip))


def _infinite(point: Node) -> int:
    """+1 for ``oo``, -1 for ``-oo``, 0 for any other point."""
    if isinstance(point, Const) and point.name == "oo":
        return 1
    if isinstance(point, Neg) and isinstance(point.operand, Const) and point.operand.name == "oo":
        return -1
    return 0


def _uses(node: Node, names: frozenset[str]) -> bool:
    return (isinstance(node, Call) and node.name in names) or any(_uses(child, names) for child in children(node))


@dataclass
class _Leg:
    """One method's outcome: ``agree``, ``disagree``, ``undecided`` or ``n/a`` (it does not apply)."""

    verdict: str
    method: str = ""
    points: int = 0
    failures: list[str] = field(default_factory=list)
    why: str = ""


def _numeric(ours: Callable[[numeric.Point], Any], theirs: Callable[[numeric.Point], Any], c: Context,
             names: Sequence[str], method: str, *, rel: float = numeric.REL_TOL, abs_tol: float = numeric.ABS_TOL) -> _Leg:
    """``ours`` against ``theirs`` at random points, until enough agree or one disagrees."""
    rng = c.rng(method)
    wanted, needed = (numeric.TARGET_POINTS, MIN_POINTS) if names else (1, 1)
    agreed, why = 0, "no usable point"
    for _ in range(numeric.MAX_ATTEMPTS if names else 1):
        if agreed >= wanted:
            break
        point = numeric.draw(c.declared, rng, names)
        try:
            a, b = ours(point), theirs(point)
        except Unusable as exc:
            why = str(exc)
            continue
        if numeric.close(a, b, rel, abs_tol):
            agreed += 1
            continue
        where = f" at {point.shown()}" if names else ""
        return _Leg("disagree", method, agreed, [f"{method}: G6 gets {_show(a)}{where}, and SymPy's answer gives "
                                                 f"{_show(b)}"])
    if agreed >= needed:
        return _Leg("agree", f"{method}, at {agreed} point{'s' if agreed != 1 else ''}", agreed)
    return _Leg("undecided", method, agreed, why=f"{method} found only {agreed} usable point(s): {why}")


def _ours(node: Node) -> Callable[[numeric.Point], Any]:
    return lambda p: numeric.stable(lambda: numeric.evaluate(node, p.mp()))


def _theirs(expr: Any, c: Context) -> Callable[[numeric.Point], Any]:
    return lambda p: numeric.sympy_value(expr, p.sympy(c.table))


def _exact(original: Callable[[dict[str, Fraction]], Fraction], expr: Any, c: Context, names: Sequence[str],
           method: str) -> _Leg:
    """Exact values at random integer points: ``original`` (G6's, in fractions) against SymPy's ``expr``."""
    rng = c.rng("exact:" + method)
    wanted = _EXACT_POINTS if names else 1
    agreed = 0
    for _ in range(4 * wanted):
        if agreed >= wanted:
            break
        values = numeric.draw_integers(c.declared, rng, names)
        try:
            a = original(values)
        except (Unusable, numeric.NotRational):
            continue
        b = numeric.sympy_exact(expr, c.table, values)
        if b is None:
            return _Leg("n/a")
        if a != b:
            where = f" at {', '.join(f'{k} = {v}' for k, v in values.items())}" if names else ""
            return _Leg("disagree", method, agreed, [f"{method}: G6 gets exactly {a}{where}, and SymPy's answer gives "
                                                     f"{b}"])
        agreed += 1
    if agreed >= min(MIN_POINTS, wanted):
        return _Leg("agree", f"{method}, at {agreed} integer point{'s' if agreed != 1 else ''}" if names else method,
                    agreed)
    return _Leg("n/a")


def _verdict(legs: Sequence[_Leg], required: Sequence[Sequence[int]], tolerance: float | None,
             details: dict[str, Any] | None = None) -> Recheck:
    """Refuted when any leg disagrees; passed when, in each group of ``required`` (leg indexes), a leg agreed;
    undecided otherwise."""
    used = [leg for leg in legs if leg.verdict != "n/a"]
    points = max((leg.points for leg in used), default=0)
    failures = [f for leg in used for f in leg.failures]
    if failures:
        return Recheck("refuted", tuple(leg.method for leg in used if leg.method), tuple(failures[:_SHOWN]),
                       points=points, tolerance=tolerance, details=details or {})
    methods = tuple(leg.method for leg in used if leg.verdict == "agree")
    for group in required:
        if not any(legs[i].verdict == "agree" for i in group if i < len(legs)):
            reasons = [legs[i].why for i in group if i < len(legs) and legs[i].why]
            return Recheck("undecided", methods, why="; ".join(reasons) or "no method that applies here agreed",
                           points=points, tolerance=tolerance, details=details or {})
    return Recheck("passed", methods, points=points, tolerance=tolerance, details=details or {})


def _pending(why: str) -> _Leg:
    return _Leg("undecided", why=why)


# --- simplify, expand, factor --------------------------------------------------------------------------------------

def equal(node: Node, result: Any, c: Context, op: str) -> Recheck:
    """``result`` equal to ``node`` (SymPy's ``op`` of it: simplify, expand or factor)."""
    names = _names(node, result, table=c.table)
    legs = [_numeric(_ours(node), _theirs(result, c), c, names, "G6's own evaluation against SymPy's answer"),
            _exact(lambda v: numeric.exact(node, v), result, c, names, "exact values at random integer points")
            if numeric.is_rational(node) else _Leg("n/a")]
    if op == "expand":
        return _verdict(legs, [[0]] + ([[1]] if legs[1].verdict != "n/a" else []), numeric.REL_TOL)
    required = [[0], [1, 2]]
    if "disagree" in (legs[0].verdict, legs[1].verdict) or legs[1].verdict == "agree":
        return _verdict(legs, required, numeric.REL_TOL)
    what = "SymPy's simplify of the difference" if op == "simplify" else "the factors expanded back"
    c.progress(_verdict([*legs, _pending(f"{what} had not finished")], required, numeric.REL_TOL))
    original = build.expression(node, c.table)
    if op == "simplify":
        zero = sympy.simplify(original - result) == 0
        legs.append(_Leg("agree", "SymPy's simplify of the difference reaches 0") if zero else
                    _pending("SymPy's simplify of the difference does not reach 0"))
    else:
        zero = sympy.expand(result) - sympy.expand(original) == 0
        legs.append(_Leg("agree", "the factors expanded back to the expanded original") if zero else
                    _pending("the factors expanded back do not match the expanded original term for term"))
    return _verdict(legs, required, numeric.REL_TOL)


# --- differentiate -------------------------------------------------------------------------------------------------

def derivative(node: Node, result: Any, variable: str, order: int, c: Context) -> Recheck:
    """``result`` the ``order``-th derivative of ``node`` in ``variable``."""
    assumed = c.declared.get(variable, ())
    if "real" not in assumed and "positive" not in assumed and "integer" not in assumed \
            and _uses(node, _NOT_HOLOMORPHIC):
        return Recheck("undecided", why=f"the expression uses abs, sign, floor, ceiling, re, im or conjugate, and "
                                        f"{variable} is not declared real: such a derivative has no complex sense, so "
                                        "G6 does not compare it numerically")
    names = sorted(set(_names(node, result, table=c.table)) | {variable})

    def ours(p: numeric.Point) -> Any:
        def value() -> Any:
            values = p.mp()
            return mpmath.diff(lambda t: numeric.evaluate(node, {**values, variable: t}), values[variable], order)
        return numeric.stable(value)

    legs = [_numeric(ours, _theirs(result, c), c, names, "mpmath's numeric derivative of G6's evaluation",
                     rel=1e-12, abs_tol=1e-15)]
    if numeric.is_rational(node):
        def original(v: dict[str, Fraction]) -> Fraction:
            return numeric.taylor(node, variable, v, v[variable], order + 1)[order] * math.factorial(order)
        legs.append(_exact(original, result, c, names, "exact Taylor coefficients at random integer points"))
    required = [[0]] + ([[1]] if len(legs) > 1 and legs[1].verdict != "n/a" else [])
    return _verdict(legs, required, 1e-12)


# --- integrate -----------------------------------------------------------------------------------------------------

def antiderivative(node: Node, result: Any, variable: str, c: Context) -> Recheck:
    """``result`` an antiderivative of ``node`` in ``variable``: differentiated back."""
    back = sympy.diff(result, c.table[variable])
    names = sorted(set(_names(node, result, table=c.table)) | {variable})
    legs = [_numeric(_ours(node), _theirs(back, c), c, names,
                     "the answer differentiated back by SymPy, against G6's evaluation of the integrand"),
            _exact(lambda v: numeric.exact(node, v), back, c, names, "the same, exactly at random integer points")
            if numeric.is_rational(node) else _Leg("n/a")]
    if "agree" in (legs[0].verdict, legs[1].verdict) or "disagree" in (legs[0].verdict, legs[1].verdict):
        return _verdict(legs, [[0, 1]], numeric.REL_TOL)
    c.progress(_verdict([*legs, _pending("simplifying the answer differentiated back had not finished")], [[0, 1, 2]],
                        numeric.REL_TOL))
    zero = sympy.simplify(back - build.expression(node, c.table)) == 0
    legs.append(_Leg("agree", "the answer differentiated back and simplified to the integrand") if zero else
                _pending("the answer differentiated back does not simplify to the integrand"))
    return _verdict(legs, [[0, 1, 2]], numeric.REL_TOL)


def definite(node: Node, result: Any, variable: str, lower: Node, upper: Node, c: Context) -> Recheck:
    """``result`` the integral of ``node`` from ``lower`` to ``upper``: against mpmath's quadrature."""
    names = sorted((set(free_symbols(node)) | set(free_symbols(lower)) | set(free_symbols(upper))
                    | set(_names(result, table=c.table))) - {variable})
    tolerance = 1e-10

    def ours(p: numeric.Point) -> Any:
        with mpmath.workdps(numeric.LOW_DPS):
            values = p.mp()
            lo, hi = numeric.evaluate(lower, values), numeric.evaluate(upper, values)
            try:
                found, error = mpmath.quad(lambda t: numeric.evaluate(node, {**values, variable: t}), [lo, hi],
                                           error=True)
            except (ZeroDivisionError, ValueError, OverflowError) as exc:
                raise Unusable(f"the quadrature failed: {type(exc).__name__}") from None
            if not numeric.usable(found) or error > 1e-14 * max(1, numeric.size(found)):
                raise Unusable(f"mpmath's quadrature estimates its own error at {_show(error)}, too large to decide")
            return found

    leg = _numeric(ours, _theirs(result, c), c, names, "mpmath's quadrature of G6's evaluation of the integrand",
                   rel=tolerance, abs_tol=1e-14)
    return _verdict([leg], [[0]], tolerance)


# --- solve ---------------------------------------------------------------------------------------------------------

def _sides(equation: Node, given: Mapping[str, Any]) -> tuple[Any, Any]:
    if isinstance(equation, Equation):
        return numeric.evaluate(equation.left, given), numeric.evaluate(equation.right, given)
    return numeric.evaluate(equation, given), mpmath.mpf(0)


def _substitute(equations: Sequence[Node], unknowns: Sequence[str], solution: Mapping[str, Any],
                subs: Mapping[Any, Any], point: numeric.Point, dps: int) -> tuple[dict[str, Any], list[tuple[Any, Any]]]:
    """The solution's values at ``dps`` digits, and each equation's residual and size with them, at ``dps + 10``."""
    values = {u: numeric.sympy_value(solution[u], subs, dps) for u in unknowns}
    with mpmath.workdps(dps + 10):
        given = {**point.mp(), **values}
        out = []
        for equation in equations:
            left, right = _sides(equation, given)
            out.append((numeric.size(left - right), max(1, numeric.size(left), numeric.size(right))))
    return values, out


def _one_solution(index: int, solution: Mapping[str, Any], family: Any, equations: Sequence[Node],
                  unknowns: Sequence[str], c: Context, domain: str) -> tuple[str, str, int]:
    """``("passed", "", points)``, ``("refuted", failure, points)`` or ``("undecided", why, points)``."""
    free = {str(s) for v in solution.values() for s in v.free_symbols}
    free.discard(str(family))
    params = sorted(free | ({n for e in equations for n in free_symbols(e)} - set(unknowns)))
    stray = [n for n in params if n not in c.table]
    if stray:
        return "undecided", f"solution {index + 1} uses {', '.join(stray)}, which G6 cannot sample", 0
    rng = c.rng(f"solve:{index}")
    settings = [sympy.Integer(k) for k in (-2, -1, 0, 1, 2)] if family is not None else [None]
    needed = 1 if family is not None or not params else MIN_POINTS
    checked, why = 0, "no usable point"
    for k in settings:
        good = 0
        for _ in range(numeric.MAX_ATTEMPTS if params else 1):
            if good >= needed:
                break
            point = numeric.draw(c.declared, rng, params)
            subs = point.sympy(c.table)
            if family is not None:
                subs[family] = k
            try:
                values, residuals = _substitute(equations, unknowns, solution, subs, point, numeric.SYMPY_DPS)
            except Unusable as exc:
                why = str(exc)
                continue
            shown = ", ".join(f"{u} = {_show(v)}" for u, v in values.items())
            at_k = f" with {family} = {k}" if family is not None else ""
            if domain == "real":
                unreal = [u for u, v in values.items() if numeric.size(mpmath.im(v)) > 1e-25 * max(1, numeric.size(v))]
                if unreal:
                    return "refuted", f"solution {index + 1} ({shown}){at_k} is not real", checked
            if all(r <= _PASS_RESIDUAL * s for r, s in residuals):
                good += 1
                continue
            try:
                _, finer = _substitute(equations, unknowns, solution, subs, point, 2 * numeric.SYMPY_DPS)
            except Unusable as exc:
                why = str(exc)
                continue
            if all(r <= _PASS_RESIDUAL * s for r, s in finer):
                good += 1
                continue
            off = [(n, r) for n, ((r, s), (r2, s2)) in enumerate(zip(residuals, finer, strict=True), start=1)
                   if r > _REFUTE_RESIDUAL * s and r2 > _REFUTE_RESIDUAL * s2]
            if off:
                number, residual = off[0]
                return "refuted", (f"solution {index + 1} ({shown}){at_k} leaves equation {number} off by "
                                   f"{_show(residual)}"), checked
            why = f"solution {index + 1} leaves a residual too large to pass and too small to refute"
        if good < needed:
            return "undecided", why, checked
        checked += good
    return "passed", "", checked


def solutions(equations: Sequence[Node], unknowns: Sequence[str], found: Sequence[Mapping[str, Any]], c: Context,
              domain: str = "complex", families: Sequence[Any] | None = None) -> Recheck:
    """Every solution in ``found`` (unknown to SymPy value; a family's values use its integer symbol, given in
    ``families`` at the same place) substituted back into every equation."""
    methods = ("every solution substituted back into every equation by G6's own evaluation, at 60 digits (confirmed "
               "at 100 before refuting)",)
    if not found:
        return Recheck("undecided", why="there is no solution to substitute back, and an empty answer cannot be "
                                        "re-checked that way")
    families = list(families) if families is not None else [None] * len(found)
    points, undecided = 0, []
    for index, (solution, family) in enumerate(zip(found, families, strict=True)):
        verdict, said, used = _one_solution(index, solution, family, equations, unknowns, c, domain)
        points += used
        if verdict == "refuted":
            return Recheck("refuted", methods, (said,), points=points, tolerance=_PASS_RESIDUAL)
        if verdict == "undecided":
            undecided.append(said)
    if undecided:
        return Recheck("undecided", methods, why="; ".join(undecided[:_SHOWN]), points=points, tolerance=_PASS_RESIDUAL)
    return Recheck("passed", methods, points=points, tolerance=_PASS_RESIDUAL)


def whole_set(equation: Node, unknown: str, c: Context) -> Recheck:
    """An answer that every value of the domain solves: the equation must hold at random points."""
    names = sorted(set(free_symbols(equation)) | {unknown})
    leg = _numeric(_ours(equation), lambda p: mpmath.mpf(0), c, names,
                   "the equation evaluated by G6 at random points of the domain")
    return _verdict([leg], [[0]], numeric.REL_TOL)


# --- limit ---------------------------------------------------------------------------------------------------------

_NEAR = (4, 8, 12, 16, 20)
_FAR = (6, 12, 18, 24, 30, 36)


def limit(node: Node, result: Any, variable: str, point: Node, direction: str, c: Context) -> Recheck:
    """``result`` the limit of ``node`` as ``variable`` approaches ``point`` from ``direction`` (``+``, ``-``, or
    ``+-`` for both sides; ignored at infinity)."""
    params = sorted((set(free_symbols(node)) | set(free_symbols(point)) | set(_names(result, table=c.table)))
                    - {variable})
    infinite = result in (sympy.oo, -sympy.oo, sympy.zoo)
    method = ("G6's own evaluation at points approaching the limit point, at up to 120 digits, "
              + ("growing without bound as the answer says" if infinite else "closing in on the answer"))
    sides = [0] if _infinite(point) else {"+": [1], "-": [-1]}.get(direction, [1, -1])
    rng = c.rng("limit")
    wanted, passed, undecided = (MIN_POINTS if params else 1), 0, []
    for _ in range(numeric.MAX_ATTEMPTS if params else 1):
        if passed >= wanted:
            break
        sample = numeric.draw(c.declared, rng, params)
        try:
            target = None if infinite else numeric.sympy_value(result, sample.sympy(c.table))
        except Unusable as exc:
            undecided.append(str(exc))
            continue
        verdicts = [_approach(node, variable, point, side, sample, target, result) for side in sides]
        refuted = [said for verdict, said in verdicts if verdict == "disagree"]
        if refuted:
            return Recheck("refuted", (method,), tuple(refuted[:_SHOWN]), points=passed, tolerance=1e-8)
        undecided += [said for verdict, said in verdicts if verdict == "undecided"]
        passed += all(verdict == "agree" for verdict, _ in verdicts)
    if passed >= wanted:
        return Recheck("passed", (method,), points=passed, tolerance=1e-8)
    return Recheck("undecided", (method,), why="; ".join(undecided[:_SHOWN]) or "no usable point", points=passed,
                   tolerance=1e-8)


def _approach(node: Node, variable: str, point: Node, side: int, sample: numeric.Point, target: Any,
              result: Any) -> tuple[str, str]:
    toward = _infinite(point)
    steps = _FAR if toward else _NEAR
    where = ("as it grows" if toward > 0 else "as it falls") if toward else ("from above" if side > 0 else
                                                                            "from below")
    seen: list[Any] = []
    with mpmath.workdps(2 * max(steps) + 40):
        values = sample.mp()
        try:
            x0 = None if toward else numeric.evaluate(point, values)
        except Unusable as exc:
            return "undecided", str(exc)
        for k in steps:
            x = toward * mpmath.mpf(10) ** k if toward else x0 + side * mpmath.mpf(10) ** -k
            try:
                value = numeric.evaluate(node, {**values, variable: x})
            except Unusable:
                continue
            if mpmath.isfinite(value):
                seen.append(value)
        if len(seen) < 3:
            return "undecided", f"approaching {where}, too few usable points"
        last, before = seen[-1], seen[-2]
        settled = numeric.size(last - before) <= 1e-12 * max(1, numeric.size(last))
        if target is None:
            growing = all(numeric.size(b) > numeric.size(a) for a, b in zip(seen[-3:], seen[-2:]))
            right_sign = result == sympy.zoo or (mpmath.re(last) > 0) == (result == sympy.oo)
            if growing and numeric.size(last) > 1e12:
                return ("agree", "") if right_sign else ("disagree", f"approaching {where}, G6's values grow to "
                                                                    f"{_show(last)}, not toward {result}")
            if settled and numeric.size(last) < 1e6:
                return "disagree", f"approaching {where}, G6's values settle at {_show(last)}, not at {result}"
            return "undecided", f"approaching {where}, G6's values neither settle nor clearly grow"
        gaps = [numeric.size(v - target) for v in seen]
        scale = max(1, numeric.size(target))
        if gaps[-1] <= 1e-8 * scale and all(b <= a + mpmath.mpf(10) ** -30 for a, b in zip(gaps[-3:], gaps[-2:])):
            return "agree", ""
        if settled and gaps[-1] > 1e-4 * scale:
            return "disagree", (f"approaching {where}, G6's values settle at {_show(last)}, not at the answer "
                                f"{_show(target)}")
        return "undecided", f"approaching {where}, G6's values have not closed in on {_show(target)}"


# --- series --------------------------------------------------------------------------------------------------------

def series(node: Node, result: Any, variable: str, point: Node, order: int, c: Context) -> Recheck:
    """``result`` (without its order term) the Taylor polynomial of ``node`` in ``variable`` at ``point``, up to the
    power ``order - 1``."""
    if _infinite(point):
        return Recheck("undecided", why="a series at infinity is not re-checked by G6")
    x, t = c.table[variable], sympy.Dummy("t")
    x0 = build.expression(point, c.table)
    shifted = sympy.expand(result.xreplace({x: t + x0}))
    try:
        poly = sympy.Poly(shifted, t)
    except sympy.PolynomialError:
        return Recheck("undecided", why="this is a Laurent or Puiseux series (negative or fractional powers, or "
                                        "logarithms), which G6 does not re-check by derivatives")
    zero_point = isinstance(point, Num) and point.value == 0
    term = (lambda k: f"{variable}^{k}") if zero_point else (lambda k: f"({variable} - {x0})^{k}")
    if poly.degree() >= order:
        return Recheck("refuted", ("the degrees of the terms",),
                       (f"the series has a term of degree {poly.degree()}, in {term(poly.degree())}, at or above the "
                        f"order {order} asked for",))
    coefficients = [poly.coeff_monomial(t ** k) for k in range(order)]
    params = sorted((set(free_symbols(node)) | set(free_symbols(point))
                     | {n for k in coefficients for n in _names(k, table=c.table)}) - {variable})
    legs = [_series_numeric(node, coefficients, variable, point, order, params, term, c)]
    if numeric.is_rational(node) and isinstance(point, Num):
        exact_legs = [_exact((lambda v, k=k: numeric.taylor(node, variable, v, point.value, order)[k]), coefficients[k],
                             c, params, f"the coefficient of {term(k)}") for k in range(order)]
        wrong = [leg for leg in exact_legs if leg.verdict == "disagree"]
        if wrong:
            legs.append(_Leg("disagree", "exact Taylor coefficients, in fractions", 0, wrong[0].failures))
        elif all(leg.verdict == "agree" for leg in exact_legs):
            legs.append(_Leg("agree", "exact Taylor coefficients, in fractions"))
    return _verdict(legs, [[0]] + ([[1]] if len(legs) > 1 else []), 1e-10)


def _series_numeric(node: Node, coefficients: list[Any], variable: str, point: Node, order: int, params: list[str],
                    term: Callable[[int], str], c: Context) -> _Leg:
    rng = c.rng("series")
    wanted, agreed, why = (MIN_POINTS if params else 1), 0, "no usable point"
    method = "each coefficient against mpmath's Taylor coefficients of G6's evaluation"
    for _ in range(numeric.MAX_ATTEMPTS if params else 1):
        if agreed >= wanted:
            break
        sample = numeric.draw(c.declared, rng, params)

        def taylor() -> list[Any]:
            values = sample.mp()
            at = numeric.evaluate(point, values)
            return mpmath.taylor(lambda s: numeric.evaluate(node, {**values, variable: s}), at, order - 1)

        try:
            low, high = numeric.at(taylor, numeric.LOW_DPS), numeric.at(taylor, numeric.HIGH_DPS)
            theirs = [numeric.sympy_value(k, sample.sympy(c.table)) for k in coefficients]
        except Unusable as exc:
            why = str(exc)
            continue
        if not all(numeric.usable(h) and numeric.close(lo, h, 1e-12, 1e-18) for lo, h in zip(low, high, strict=True)):
            why = "mpmath's Taylor coefficients change with the working precision here"
            continue
        wrong = [k for k in range(order) if not numeric.close(high[k], theirs[k], 1e-10, 1e-15)]
        if wrong:
            k = wrong[0]
            return _Leg("disagree", method, agreed, [f"the coefficient of {term(k)} is {_show(theirs[k])} in SymPy's "
                                                     f"series, and {_show(high[k])} from mpmath's derivatives of G6's "
                                                     "evaluation"])
        agreed += 1
    if agreed >= wanted:
        return _Leg("agree", f"{method}, at {agreed} point{'s' if agreed != 1 else ''}", agreed)
    return _Leg("undecided", method, agreed, why=why)


# --- matrices ------------------------------------------------------------------------------------------------------

def fraction_det(rows: list[list[Fraction]]) -> Fraction:
    """G6's own determinant, by elimination in fractions."""
    a = [list(r) for r in rows]
    n, det = len(a), Fraction(1)
    for i in range(n):
        pivot = next((r for r in range(i, n) if a[r][i] != 0), None)
        if pivot is None:
            return Fraction(0)
        if pivot != i:
            a[i], a[pivot] = a[pivot], a[i]
            det = -det
        det *= a[i][i]
        for r in range(i + 1, n):
            f = a[r][i] / a[i][i]
            a[r] = [x - f * y for x, y in zip(a[r], a[i], strict=True)]
    return det


def _rational(rows: Sequence[Sequence[Node]]) -> bool:
    return all(numeric.is_rational(e) for row in rows for e in row)


def _matrix_names(rows: Sequence[Sequence[Node]]) -> set[str]:
    return {n for row in rows for e in row for n in free_symbols(e)}


def _mp_matrix(rows: Sequence[Sequence[Node]], values: Mapping[str, Any]) -> Any:
    return mpmath.matrix([[numeric.evaluate(e, values) for e in row] for row in rows])


def determinant(rows: Sequence[Sequence[Node]], result: Any, c: Context) -> Recheck:
    names = sorted(_matrix_names(rows) | set(_names(result, table=c.table)))
    legs = [_numeric(lambda p: numeric.stable(lambda: mpmath.det(_mp_matrix(rows, p.mp()))), _theirs(result, c), c,
                     names, "mpmath's determinant of G6's evaluation of the entries"),
            _exact(lambda v: fraction_det([[numeric.exact(e, v) for e in row] for row in rows]), result, c, names,
                   "G6's own exact elimination in fractions") if _rational(rows) else _Leg("n/a")]
    return _verdict(legs, [[0]] + ([[1]] if legs[1].verdict != "n/a" else []), numeric.REL_TOL)


def inverse(rows: Sequence[Sequence[Node]], result: Any, c: Context) -> Recheck:
    n = len(rows)
    names = sorted(_matrix_names(rows) | set(_names(result, table=c.table)))
    rng = c.rng("inverse")
    method = "the matrix times the answer, by mpmath on G6's evaluation of the entries, is the identity"
    wanted, agreed, why = (MIN_POINTS if names else 1), 0, "no usable point"
    for _ in range(numeric.MAX_ATTEMPTS if names else 1):
        if agreed >= wanted:
            break
        point = numeric.draw(c.declared, rng, names)
        try:
            with mpmath.workdps(numeric.HIGH_DPS):
                m = _mp_matrix(rows, point.mp())
                inv = mpmath.matrix([[numeric.sympy_value(result[i, j], point.sympy(c.table)) for j in range(n)]
                                     for i in range(n)])
                product = m * inv
                scale = max(1, mpmath.mnorm(m, 1) * mpmath.mnorm(inv, 1))
                off = max(numeric.size(product[i, j] - (1 if i == j else 0)) for i in range(n) for j in range(n))
        except Unusable as exc:
            why = str(exc)
            continue
        if off > 1e-8 * scale:
            where = f" at {point.shown()}" if names else ""
            return Recheck("refuted", (method,), (f"the matrix times SymPy's inverse is off the identity by "
                                                  f"{_show(off)}{where}",), points=agreed, tolerance=1e-20)
        if off <= 1e-20 * scale:
            agreed += 1
        else:
            why = f"the product is off the identity by {_show(off)}, too much to pass and too little to refute"
    methods = [f"{method}, at {agreed} point{'s' if agreed != 1 else ''}"]
    entries = [[result[i, j] for j in range(n)] for i in range(n)]
    if not names and _rational(rows) and all(isinstance(e, sympy.Rational) for row in entries for e in row):
        m = [[numeric.exact(e, {}) for e in row] for row in rows]
        inv = [[Fraction(int(e.p), int(e.q)) for e in row] for row in entries]
        product = [[sum((m[i][k] * inv[k][j] for k in range(n)), Fraction(0)) for j in range(n)] for i in range(n)]
        if any(product[i][j] != (1 if i == j else 0) for i in range(n) for j in range(n)):
            return Recheck("refuted", tuple(methods), ("the matrix times SymPy's inverse, exactly in fractions, is not "
                                                       "the identity",))
        methods.append("the same product exactly in fractions, by G6")
    if agreed >= wanted:
        return Recheck("passed", tuple(methods), points=agreed, tolerance=1e-20)
    return Recheck("undecided", (method,), why=why, points=agreed, tolerance=1e-20)


def eigenvalues(rows: Sequence[Sequence[Node]], found: Mapping[Any, int], c: Context) -> Recheck:
    """Each eigenvalue makes ``det(A - lambda I)`` vanish, and with their multiplicities they match mpmath's own."""
    n = len(rows)
    names = sorted(_matrix_names(rows) | {m for v in found for m in _names(v, table=c.table)})
    rng = c.rng("eigen")
    methods = ("each eigenvalue makes det(A - lambda I) vanish, by mpmath on G6's evaluation of the entries",
               "with their multiplicities, the eigenvalues match those mpmath's own eigenvalue routine finds")
    wanted, agreed, why = (MIN_POINTS if names else 1), 0, "no usable point"
    for _ in range(numeric.MAX_ATTEMPTS if names else 1):
        if agreed >= wanted:
            break
        point = numeric.draw(c.declared, rng, names)
        try:
            with mpmath.workdps(numeric.HIGH_DPS):
                m = _mp_matrix(rows, point.mp())
                values = [(numeric.sympy_value(v, point.sympy(c.table)), k) for v, k in found.items()]
                norm = mpmath.mnorm(m, 1)
                for value, _k in values:
                    det = numeric.size(mpmath.det(m - value * mpmath.eye(n)))
                    if det > 1e-8 * (norm + numeric.size(value) + 1) ** n:
                        return Recheck("refuted", methods, (f"{_show(value)} is not an eigenvalue: det(A - lambda I) "
                                                            f"is {_show(det)}",), points=agreed)
                theirs = list(mpmath.eig(m, left=False, right=False))
        except Unusable as exc:
            why = str(exc)
            continue
        except (ZeroDivisionError, ValueError) as exc:
            why = f"mpmath's eigenvalue routine failed: {type(exc).__name__}"
            continue
        unused = list(theirs)
        for value, k in values:
            for _ in range(k):
                near = min(unused, key=lambda e, v=value: numeric.size(e - v), default=None)
                if near is None or numeric.size(near - value) > 1e-6 * max(1, numeric.size(value)):
                    listed = ", ".join(_show(e) for e in theirs)
                    return Recheck("refuted", methods, (f"{_show(value)} (multiplicity {k}) does not match the "
                                                        f"eigenvalues mpmath finds: {listed}",), points=agreed)
                unused.remove(near)
        agreed += 1
    if agreed >= wanted:
        return Recheck("passed", methods, points=agreed, tolerance=1e-6)
    return Recheck("undecided", methods, why=why, points=agreed)


__all__ = ["Context", "antiderivative", "definite", "derivative", "determinant", "eigenvalues", "equal",
           "fraction_det", "inverse", "limit", "series", "solutions", "whole_set"]
