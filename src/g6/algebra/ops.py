# SPDX-License-Identifier: Apache-2.0
"""The SymPy ops of g6.algebra as Python functions. Each reads its input through G6's parser here, then runs in G6's
own worker process (:mod:`g6.algebra.runner`), so its time limit holds however long SymPy takes.

In order, stopping at the first that fails: the limits (``time_limit`` at most 120 seconds, 30 by default; a memory
cap; an output cap); the input, read by :func:`g6.algebra.inputs.read`, every expression through G6's parser, with its
codes, fields, lines and columns; SymPy installed (``pip install 'g6[cas]'``, checked without importing it); then the
worker, which answers and re-checks. When the time limit stops the worker before it answered, the op is
``G6_E_ALGEBRA_TIME_LIMIT``; when it stops it during the re-check, the answer is reported as not re-checked.

Every function takes, besides its own parameters, ``time_limit``, ``memory_mb`` (256 to 16384, 2048 by default),
``max_output_bytes``, ``seed`` (the re-check's random points) and the ``request_id`` and ``run_id`` it echoes. Nothing
here imports SymPy.
"""
from __future__ import annotations

import importlib.util
import json
import math
from typing import Any

from g6.algebra import inputs, runner
from g6.algebra.envelope import (
    CAS_UNAVAILABLE,
    ENGINE_ERROR,
    INPUT_INVALID,
    INPUT_TOO_LARGE,
    INSTALL_CAS,
    OUTPUT_TOO_LARGE,
    OUTPUT_UNRECOGNISED,
    RUN_FAILED,
    TIME_LIMIT,
    TIME_LIMIT_TOO_LONG,
    Refusal,
    labelled,
)
from g6.algebra.schema import AlgebraResult

DEFAULT_TIME_LIMIT_S = 30.0
MAX_TIME_LIMIT_S = 120.0
DEFAULT_MEMORY_MB, MIN_MEMORY_MB, MAX_MEMORY_MB = 2048, 256, 16384
_STATUSES = ("passed", "refuted", "undecided")


def seconds(value: Any, cap: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value < float("inf"):
        raise Refusal(INPUT_INVALID, f"time_limit must be a positive number of seconds, not {value!r}")
    if value > cap:
        raise Refusal(TIME_LIMIT_TOO_LONG, f"time_limit {value:g} s is above the cap of {cap:g} s")
    return float(value)


def whole(value: Any, what: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < low:
        raise Refusal(INPUT_INVALID, f"{what} must be a whole number of at least {low}, not {value!r}")
    if value > high:
        raise Refusal(INPUT_TOO_LARGE, f"{what} {value} is above the limit of {high}")
    return value


def sympy_installed() -> bool:
    """Whether SymPy can be imported here, found without importing it."""
    try:
        return importlib.util.find_spec("sympy") is not None
    except (ImportError, ValueError):
        return False


def run(op: str, params: dict[str, Any], *, time_limit: float = DEFAULT_TIME_LIMIT_S,
        memory_mb: int = DEFAULT_MEMORY_MB, max_output_bytes: int = runner.MAX_OUTPUT_BYTES, seed: int = 0,
        request_id: str = "", run_id: str = "") -> AlgebraResult:
    """One SymPy op on ``params``, in G6's worker, labelled. Every public function below is this."""
    def work(state: dict[str, Any]) -> dict[str, Any]:
        extra = state.setdefault("extra", {})
        limit = seconds(time_limit, MAX_TIME_LIMIT_S)
        memory = whole(memory_mb, "memory_mb", MIN_MEMORY_MB, MAX_MEMORY_MB)
        cap = whole(max_output_bytes, "max_output_bytes", 1024, runner.OUTPUT_CAP_LIMIT)
        rng_seed = whole(seed, "seed", 0, 2**32)
        problem = inputs.read(op, params)
        state["input"] = problem.shown()
        try:
            payload = json.loads(json.dumps(params))
        except (TypeError, ValueError):
            raise Refusal(INPUT_INVALID, "the parameters must be plain text, numbers and lists") from None
        if not sympy_installed():
            raise Refusal(CAS_UNAVAILABLE, f"SymPy is not installed, so there is nothing to compute with: {INSTALL_CAS}")
        request = {"op": op, "params": payload, "seed": rng_seed, "mem_mb": memory, "cpu_s": math.ceil(limit) + 2,
                   "file_bytes": cap + 1024 * 1024}
        lines, stopped = runner.call_worker(request, seconds=limit, memory_mb=memory, cap=cap, record=extra)
        return read_lines(state, lines, stopped, limit)

    return labelled(op, work, request_id, run_id)


def read_lines(state: dict[str, Any], lines: list[dict[str, Any]], stopped: str, limit: float) -> dict[str, Any]:
    """The worker's lines read into the result's fields; a refusal it reported raised as one."""
    extra = state.setdefault("extra", {})
    answer: dict[str, Any] | None = None
    final: dict[str, Any] | None = None
    partial: dict[str, Any] | None = None
    for line in lines:
        stage = line["stage"]
        if stage == "start":
            state["engine_version"] = str(line.get("sympy_version") or "")
            extra["mpmath_version"] = str(line.get("mpmath_version") or "")
        elif stage == "refusal":
            code = str(line.get("code") or "")
            if not code.startswith("G6_E_ALGEBRA_"):
                raise Refusal(OUTPUT_UNRECOGNISED, f"G6's worker refused with a code G6 does not read: {code[:80]}")
            keep = {"refusals": line["refusals"]} if isinstance(line.get("refusals"), list) else {}
            raise Refusal(code, str(line.get("reason") or ""), **keep)
        elif stage == "error":
            raise Refusal(ENGINE_ERROR, str(line.get("reason") or "G6's worker failed")[:500])
        elif stage == "answer":
            answer = line
        elif stage == "partial" and isinstance(line.get("recheck"), dict):
            partial = line["recheck"]
        elif stage == "recheck" and isinstance(line.get("recheck"), dict):
            final = line["recheck"]
    if stopped == "output":
        raise Refusal(OUTPUT_TOO_LARGE, "G6's worker wrote more than its output cap, so it was stopped and its process "
                                        "tree killed")
    if answer is None:
        if stopped == "time":
            raise Refusal(TIME_LIMIT, f"SymPy did not answer within {limit:g} s, so G6's worker was stopped and its "
                                      "process tree killed")
        raise Refusal(RUN_FAILED, "G6's worker finished without an answer")
    if final is None:
        why = (f"the time limit of {limit:g} s was reached before G6's re-check finished" if stopped == "time" else
               "G6's worker stopped before the re-check finished")
        if partial is not None and partial.get("status") == "refuted":
            final = partial
        else:
            found = partial or {}
            final = {**found, "status": "undecided",
                     "why": why + (f" ({found['why']})" if found.get("why") else "")}
    status = final.get("status")
    if status not in _STATUSES:
        raise Refusal(OUTPUT_UNRECOGNISED, f"G6's worker gave a re-check status G6 does not read: {str(status)[:40]}")
    answers = answer.get("answers")
    complete = answer.get("complete")
    state |= {
        "engine_ran": True,
        "answer": str(answer.get("answer") or ""),
        "latex": str(answer.get("latex") or ""),
        "answers": answers if isinstance(answers, list) else [],
        "complete": complete if isinstance(complete, bool) else None,
        "completeness": str(answer.get("completeness") or ""),
        "details": answer.get("details") if isinstance(answer.get("details"), dict) else {},
        "recheck": {"status": status, "methods": list(final.get("methods") or []),
                    "failures": list(final.get("failures") or []), "why": str(final.get("why") or ""),
                    "points": int(final.get("points") or 0), "tolerance": final.get("tolerance"),
                    "details": final.get("details") if isinstance(final.get("details"), dict) else {}},
    }
    return state


def _with(symbols: Any, **params: Any) -> dict[str, Any]:
    return {"symbols": symbols, **{k: v for k, v in params.items() if v is not None}}


def simplify(expression: str, *, symbols: Any = None, **options: Any) -> AlgebraResult:
    """SymPy's ``simplify`` of ``expression``, re-checked numerically and symbolically (or exactly)."""
    return run("simplify", _with(symbols, expression=expression), **options)


def expand(expression: str, *, symbols: Any = None, **options: Any) -> AlgebraResult:
    """SymPy's ``expand``, re-checked numerically and, for a rational expression, exactly."""
    return run("expand", _with(symbols, expression=expression), **options)


def factor(expression: str, *, symbols: Any = None, **options: Any) -> AlgebraResult:
    """SymPy's ``factor`` (over the rationals), re-checked by expanding the factors back, numerically and exactly."""
    return run("factor", _with(symbols, expression=expression), **options)


def differentiate(expression: str, variable: str, *, order: int = 1, symbols: Any = None,
                  **options: Any) -> AlgebraResult:
    """The ``order``-th derivative, re-checked by numeric differentiation and, for a rational expression, exactly."""
    return run("differentiate", _with(symbols, expression=expression, variable=variable, order=order), **options)


def integrate(expression: str, variable: str, *, lower: Any = None, upper: Any = None, symbols: Any = None,
              **options: Any) -> AlgebraResult:
    """An antiderivative (re-checked by differentiating it back) or, with ``lower`` and ``upper``, a definite integral
    (re-checked by numeric quadrature). ``oo`` may stand as a bound."""
    return run("integrate", _with(symbols, expression=expression, variable=variable, lower=lower, upper=upper),
               **options)


def solve(equations: Any, *, unknowns: Any = None, domain: str = "complex", symbols: Any = None,
          **options: Any) -> AlgebraResult:
    """The solutions of one equation or a small system (``left = right``, or an expression meaning ``= 0``), each
    substituted back; says whether the set is complete, as SymPy reports."""
    texts = [equations] if isinstance(equations, str) else equations
    return run("solve", _with(symbols, equations=texts, unknowns=unknowns, domain=domain), **options)


def limit(expression: str, variable: str, *, point: Any = 0, direction: str = "+-", symbols: Any = None,
          **options: Any) -> AlgebraResult:
    """The limit as ``variable`` approaches ``point`` (``oo`` allowed) from ``direction`` (``+-`` both sides, ``+``
    from above, ``-`` from below), re-checked by approaching the point numerically."""
    return run("limit", _with(symbols, expression=expression, variable=variable, point=point, direction=direction),
               **options)


def series(expression: str, variable: str, *, point: Any = 0, order: int = 6, symbols: Any = None,
           **options: Any) -> AlgebraResult:
    """The Taylor series up to ``(variable - point)^(order - 1)``, each coefficient re-checked against derivatives."""
    return run("series", _with(symbols, expression=expression, variable=variable, point=point, order=order),
               **options)


def determinant(matrix: Any, *, symbols: Any = None, **options: Any) -> AlgebraResult:
    """The determinant of a square matrix (rows of expressions), re-checked by G6's own elimination."""
    return run("determinant", _with(symbols, matrix=matrix), **options)


def inverse(matrix: Any, *, symbols: Any = None, **options: Any) -> AlgebraResult:
    """The inverse of a square matrix, re-checked by multiplying back to the identity."""
    return run("inverse", _with(symbols, matrix=matrix), **options)


def eigenvalues(matrix: Any, *, symbols: Any = None, **options: Any) -> AlgebraResult:
    """The eigenvalues with their multiplicities, each re-checked by ``det(A - lambda I) = 0``."""
    return run("eigenvalues", _with(symbols, matrix=matrix), **options)


def factor_integer(n: int, **options: Any) -> AlgebraResult:
    """The prime factorisation of ``n``, re-checked by multiplying it back and testing each factor."""
    return run("factor_integer", {"n": n}, **options)


def is_prime(n: int, **options: Any) -> AlgebraResult:
    """Whether ``n`` is prime, as SymPy answers, saying whether that answer is definitive at this size, and
    re-checked by G6's own test."""
    return run("is_prime", {"n": n}, **options)


def gcd(numbers: Any, **options: Any) -> AlgebraResult:
    """The greatest common divisor of whole numbers, re-checked by Bezout coefficients."""
    return run("gcd", {"numbers": numbers}, **options)


__all__ = ["DEFAULT_MEMORY_MB", "DEFAULT_TIME_LIMIT_S", "MAX_MEMORY_MB", "MAX_TIME_LIMIT_S", "MIN_MEMORY_MB",
           "determinant", "differentiate", "eigenvalues", "expand", "factor", "factor_integer", "gcd", "integrate",
           "inverse", "is_prime", "limit", "read_lines", "run", "seconds", "series", "simplify", "solve",
           "sympy_installed", "whole"]
