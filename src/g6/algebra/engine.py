# SPDX-License-Identifier: Apache-2.0
"""The SymPy work of each op, and the re-check that goes with it. Runs in G6's worker (:mod:`g6.algebra.worker`).

:func:`compute` builds SymPy objects from the problem's parse trees (:mod:`g6.algebra.build`), asks SymPy for the
answer, and returns the answer with a function that re-checks it (:mod:`g6.algebra.checks`,
:mod:`g6.algebra.integers`), so the worker can report the answer before the re-check runs. An answer SymPy could not
give (an integral left unevaluated, an equation it returned as a condition, a limit that does not exist, a singular
matrix) is refused with its own code, and an answer too long to report is refused too.

For ``solve``: one equation in one unknown goes to SymPy's ``solveset``, whose answer is the whole solution set in the
domain unless it leaves part of it as a condition; a linear system to ``linsolve``, which finds every solution; any
other system to ``nonlinsolve``, which finds every solution of a polynomial system (by a Groebner basis) and makes no
such promise for others.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import sympy
from sympy.matrices.exceptions import MatrixError

from g6.algebra import build, checks, integers, numeric
from g6.algebra.envelope import ANSWER_TOO_LARGE, NO_ANSWER, NOT_INVERTIBLE, Refusal
from g6.algebra.inputs import Problem
from g6.algebra.language import free_symbols
from g6.algebra.schema import Recheck

MAX_ANSWER_CHARS = 100_000
_DOMAIN_WORDS = {"complex": "the complex numbers", "real": "the real numbers"}


def text(expr: Any) -> str:
    """SymPy's canonical text, on one line (SymPy lays a matrix out over several)."""
    if isinstance(expr, sympy.MatrixBase):
        rows = ", ".join("[" + ", ".join(sympy.sstr(e) for e in expr.row(i)) + "]" for i in range(expr.rows))
        return f"Matrix([{rows}])"
    return sympy.sstr(expr)


def latex(expr: Any) -> str:
    return sympy.latex(expr)


def _answer(expr: Any, **more: Any) -> dict[str, Any]:
    out = {"answer": text(expr), "latex": latex(expr), **more}
    if len(out["answer"]) + len(out["latex"]) > MAX_ANSWER_CHARS:
        raise Refusal(ANSWER_TOO_LARGE, f"SymPy's answer is {len(out['answer'])} characters; G6 reports at most "
                                        f"{MAX_ANSWER_CHARS} with its LaTeX")
    return out


def compute(problem: Problem, c: checks.Context) -> tuple[dict[str, Any], Callable[[], Recheck]]:
    """The answer (``answer``, ``latex``, and any of ``answers``, ``complete``, ``completeness``, ``details``) and the
    re-check to run on it."""
    op = problem.op
    if op == "factor_integer":
        return _factor_integer(problem.integer)
    if op == "is_prime":
        n = problem.integer
        prime = bool(sympy.isprime(n))
        definitive = n < integers.SYMPY_DETERMINISTIC_BOUND
        words = ("SymPy's isprime is definitive below 2^64, and this number is below it." if definitive else
                 "SymPy's isprime is definitive below 2^64; this number is above it, where SymPy's answer rests on "
                 "the BPSW test, which has no known counterexample but is not a proof.")
        return ({"answer": str(prime), "latex": rf"\text{{{prime}}}", "completeness": words,
                 "details": {"is_prime": prime, "sympy_deterministic": definitive}},
                lambda: integers.check_is_prime(n, prime))
    if op == "gcd":
        g = int(sympy.igcd(*problem.numbers))
        return {"answer": str(g), "latex": str(g)}, lambda: integers.check_gcd(problem.numbers, g)
    if problem.matrix:
        return _matrix(problem, c)
    if op == "solve":
        return _solve(problem, c)
    node = problem.expression
    e = build.expression(node, c.table)
    if op in ("simplify", "expand", "factor"):
        result = {"simplify": sympy.simplify, "expand": sympy.expand, "factor": sympy.factor}[op](e)
        return _answer(result), lambda: checks.equal(node, result, c, op)
    x = c.table[problem.variable]
    if op == "differentiate":
        result = sympy.diff(e, x, problem.order)
        return _answer(result), lambda: checks.derivative(node, result, problem.variable, problem.order, c)
    if op == "integrate":
        return _integrate(problem, e, x, c)
    if op == "limit":
        return _limit(problem, e, x, c)
    return _series(problem, e, x, c)


def _integrate(problem: Problem, e: Any, x: Any, c: checks.Context) -> tuple[dict[str, Any], Callable[[], Recheck]]:
    node = problem.expression
    if problem.lower is None:
        result = sympy.integrate(e, x)
        if result.has(sympy.Integral):
            raise Refusal(NO_ANSWER, f"SymPy found no antiderivative in closed form: it returned {text(result)[:300]}")
        return (_answer(result, details={"constant": "any constant may be added to an antiderivative"}),
                lambda: checks.antiderivative(node, result, problem.variable, c))
    lo, hi = build.expression(problem.lower, c.table), build.expression(problem.upper, c.table)
    result = sympy.integrate(e, (x, lo, hi))
    if result.has(sympy.Integral):
        raise Refusal(NO_ANSWER, f"SymPy could not evaluate the integral: it returned {text(result)[:300]}")
    return _answer(result), lambda: checks.definite(node, result, problem.variable, problem.lower, problem.upper, c)


def _limit(problem: Problem, e: Any, x: Any, c: checks.Context) -> tuple[dict[str, Any], Callable[[], Recheck]]:
    x0 = build.expression(problem.point, c.table)
    try:
        result = sympy.limit(e, x, x0) if x0 in (sympy.oo, -sympy.oo) else sympy.limit(e, x, x0, problem.direction)
    except (ValueError, NotImplementedError) as exc:
        raise Refusal(NO_ANSWER, f"SymPy gives no limit: {' '.join(str(exc).split())}"[:400]) from None
    if result.has(sympy.Limit):
        raise Refusal(NO_ANSWER, f"SymPy could not find the limit: it returned {text(result)[:300]}")
    if isinstance(result, sympy.AccumBounds):
        return (_answer(result, details={"bounds": True}),
                lambda: Recheck("undecided", why=f"SymPy answered with the bounds {text(result)}: the values stay "
                                                 "between them without a limit, which G6 does not re-check"))
    return _answer(result), lambda: checks.limit(problem.expression, result, problem.variable, problem.point,
                                                 problem.direction, c)


def _series(problem: Problem, e: Any, x: Any, c: checks.Context) -> tuple[dict[str, Any], Callable[[], Recheck]]:
    x0 = build.expression(problem.point, c.table)
    try:
        result = sympy.series(e, x, x0, problem.order)
    except (ValueError, NotImplementedError, sympy.PoleError) as exc:
        raise Refusal(NO_ANSWER, f"SymPy gives no series here: {' '.join(str(exc).split())}"[:400]) from None
    polynomial = result.removeO()
    return (_answer(result, details={"without_order_term": text(polynomial)}),
            lambda: checks.series(problem.expression, polynomial, problem.variable, problem.point, problem.order, c))


# --- solve ---------------------------------------------------------------------------------------------------------

def _shown_family(expr: Any, dummy: Any, c: checks.Context) -> tuple[str, str, str]:
    name = next(n for n in ("n", "k", "m", "j", "n1", "n2", "n3") if n not in c.table)
    named = expr.xreplace({dummy: sympy.Symbol(name, integer=True)})
    return text(named), latex(named), name


def _solve(problem: Problem, c: checks.Context) -> tuple[dict[str, Any], Callable[[], Recheck]]:
    differences = [build.difference(e, c.table) for e in problem.equations]
    xs = [c.table[u] for u in problem.unknowns]
    if len(differences) == 1 and len(xs) == 1:
        return _solveset(problem, differences[0], xs[0], c)
    polynomial = all(d.is_polynomial(*xs) for d in differences)
    linear = polynomial and all(sympy.Poly(d, *xs).total_degree() <= 1 for d in differences)
    try:
        found = sympy.linsolve(differences, xs) if linear else sympy.nonlinsolve(differences, xs)
    except (ValueError, NotImplementedError, TypeError) as exc:
        raise Refusal(NO_ANSWER, f"SymPy could not solve the system: {' '.join(str(exc).split())}"[:400]) from None
    if not (found is sympy.S.EmptySet or isinstance(found, sympy.FiniteSet)):
        raise Refusal(NO_ANSWER, f"SymPy returned {text(found)[:300]}, not a set of solutions G6 reads")
    plain = [t for t in found if not any(isinstance(v, sympy.Set) for v in t)]
    unsolved = len(plain) < len(found)
    complete = polynomial and not unsolved
    if linear:
        words = "SymPy's linsolve finds every solution of a linear system."
    elif unsolved:
        words = "SymPy found only some solutions: it left the others as sets it did not solve."
    elif polynomial:
        words = "SymPy's nonlinsolve solves a polynomial system by a Groebner basis, which finds every solution."
    else:
        words = ("SymPy's nonlinsolve makes no promise of every solution of a system that is not polynomial, so "
                 "others may exist.")
    solutions = [dict(zip(problem.unknowns, t, strict=True)) for t in plain]
    answers = [{"values": {u: text(v) for u, v in s.items()}, "latex": {u: latex(v) for u, v in s.items()}}
               for s in solutions]
    out = _answer(found, answers=answers, complete=complete, completeness=words)
    return out, lambda: checks.solutions(problem.equations, problem.unknowns, solutions, c)


def _solveset(problem: Problem, difference: Any, x: Any, c: checks.Context) -> tuple[dict[str, Any],
                                                                                     Callable[[], Recheck]]:
    domain = sympy.S.Reals if problem.domain == "real" else sympy.S.Complexes
    where = _DOMAIN_WORDS[problem.domain]
    try:
        found = sympy.solveset(difference, x, domain)
    except (ValueError, NotImplementedError, TypeError) as exc:
        raise Refusal(NO_ANSWER, f"SymPy could not solve it: {' '.join(str(exc).split())}"[:400]) from None
    if isinstance(found, sympy.ConditionSet):
        raise Refusal(NO_ANSWER, f"SymPy could not solve it: it returned {text(found)[:300]}")
    unknown = problem.unknowns[0]
    if found == domain:
        return (_answer(found, answers=[{"set": f"every value in {where}"}], complete=True,
                        completeness=f"SymPy's solveset reports that every value in {where} is a solution."),
                lambda: checks.whole_set(problem.equations[0], unknown, c))
    parts = list(found.args) if isinstance(found, sympy.Union) else [found]
    solutions, families, answers, other = [], [], [], []
    for part in parts:
        if isinstance(part, sympy.FiniteSet):
            for v in part:
                solutions.append({unknown: v})
                families.append(None)
                answers.append({"values": {unknown: text(v)}, "latex": {unknown: latex(v)}})
        elif isinstance(part, sympy.ImageSet) and part.base_sets == (sympy.S.Integers,) \
                and len(part.lamda.variables) == 1:
            dummy = part.lamda.variables[0]
            solutions.append({unknown: part.lamda.expr})
            families.append(dummy)
            shown, shown_latex, name = _shown_family(part.lamda.expr, dummy, c)
            answers.append({"values": {unknown: shown}, "latex": {unknown: shown_latex}, "for_every_integer": name})
        elif part is sympy.S.EmptySet:
            continue
        else:
            other.append(part)
    unsolved = [p for p in other if isinstance(p, sympy.ConditionSet)]
    complete = not unsolved
    words = (f"SymPy's solveset reports this as the complete solution set in {where}." if complete else
             f"SymPy found only some solutions in {where}: it left the rest unsolved, as {text(unsolved[0])[:200]}.")
    if not solutions and not other:
        words = f"SymPy's solveset reports no solution in {where}."
    out = _answer(found, answers=answers, complete=complete, completeness=words)
    if other and not unsolved:
        return out, lambda: Recheck("undecided", why=f"part of the answer is a set G6 does not re-check: "
                                                     f"{text(other[0])[:200]}")
    return out, lambda: checks.solutions(problem.equations, problem.unknowns, solutions, c, domain=problem.domain,
                                         families=families)


# --- matrices and integers ------------------------------------------------------------------------------------------

def _matrix(problem: Problem, c: checks.Context) -> tuple[dict[str, Any], Callable[[], Recheck]]:
    rows = problem.matrix
    m = sympy.Matrix([[build.expression(e, c.table) for e in row] for row in rows])
    n = len(rows)
    if problem.op == "determinant":
        result = m.det()
        return _answer(result), lambda: checks.determinant(rows, result, c)
    if problem.op == "inverse":
        try:
            result = m.inv()
        except (ValueError, MatrixError):     # NonInvertibleMatrixError is both
            raise Refusal(NOT_INVERTIBLE, _singular(rows)) from None
        entries = [[text(result[i, j]) for j in range(n)] for i in range(n)]
        return _answer(result, details={"matrix": entries}), lambda: checks.inverse(rows, result, c)
    try:
        found = m.eigenvals()
    except (MatrixError, NotImplementedError, ValueError) as exc:
        raise Refusal(NO_ANSWER, f"SymPy could not find the eigenvalues: {' '.join(str(exc).split())}"[:400]) from None
    total = sum(found.values())
    complete = total == n
    words = (f"The multiplicities add up to the matrix's size, {n}, so no eigenvalue is missing." if complete else
             f"The multiplicities add up to {total}, less than the matrix's size, {n}: SymPy did not find every "
             "eigenvalue.")
    answers = [{"value": text(v), "latex": latex(v), "multiplicity": int(k)} for v, k in found.items()]
    shown = sympy.Dict({v: k for v, k in found.items()})
    return (_answer(shown, answers=answers, complete=complete, completeness=words),
            lambda: checks.eigenvalues(rows, found, c))


def _singular(rows: Any) -> str:
    said = "the matrix has no inverse: SymPy finds its determinant is 0"
    entries = [e for row in rows for e in row]
    if all(numeric.is_rational(e) and not free_symbols(e) for e in entries):
        exact = checks.fraction_det([[numeric.exact(e, {}) for e in row] for row in rows])
        said += (", and G6's own exact elimination in fractions also gives 0" if exact == 0 else
                 f", but G6's own exact elimination gives {exact}")
    return said


def _factor_integer(n: int) -> tuple[dict[str, Any], Callable[[], Recheck]]:
    found = {int(p): int(k) for p, k in sympy.factorint(n).items()}
    ordered = sorted(found.items())
    shown = " * ".join(f"{p}^{k}" if k > 1 else f"({p})" if p < 0 else str(p) for p, k in ordered)
    shown_latex = r" \cdot ".join(f"{p}^{{{k}}}" if k > 1 else str(p) for p, k in ordered)
    answers = [{"prime": str(p), "power": k} for p, k in ordered]
    return ({"answer": shown, "latex": shown_latex, "answers": answers},
            lambda: integers.check_factorisation(n, found))


__all__ = ["MAX_ANSWER_CHARS", "compute", "latex", "text"]
