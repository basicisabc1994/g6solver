# SPDX-License-Identifier: Apache-2.0
"""AlgebraBlock: the registry and MCP door (``g6_invoke``) to g6.algebra.

Each op calls one public function of the package and returns its labelled result unchanged, with ``component`` and
``op``; the envelope (``completion_state``, ``warning_card``, ``evidence``, ``request_id``, ``run_id``) comes from
:func:`g6.algebra.envelope.finish`, so a refusal (the parser's, a limit, SymPy or SageMath missing, a time limit, no
answer, a refuted answer) is a ``blocked-escalated`` result, never a raised error. Unknown ops and unknown fields are
refused by the typed input (``G6_E_INPUT_VALIDATION``), so no field can carry code: the earlier version's ``run_code``
is not an op, and no field names a program, a flag or a memory size.

The SymPy ops run in G6's own worker process, where caller text reaches nothing but G6's parser, so they are not
sensitive and answer over MCP. ``sage_evaluate`` and ``sage_version`` run SageMath, a program on your machine, so the
package lists them in ``__g6_sensitive_ops__``: they run from Python, or over MCP only when the person running the
server lists them in ``G6_MCP_SENSITIVE``.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.algebra import inputs, ops, sage
from g6.algebra.envelope import INSTALL_CAS, SAGE_ENV
from g6.algebra.language import MAX_CHARS, MAX_SYMBOLS
from g6.core.ai_block import AIBlock
from g6.core.maturity import component_maturity
from g6.core.result import Result

#: Run SageMath, out of process: sensitive.
SENSITIVE_OPS: tuple[str, ...] = ("sage_evaluate", "sage_version")
_SENSITIVE = (" (sensitive: runs from Python, or over MCP only when the person running the server lists it in "
              "G6_MCP_SENSITIVE)")
_COMMON = "symbols=[names] or {name: [real, positive, integer]}, time_limit (seconds, at most 120), seed"
_HOW = f"The SymPy ops need SymPy: {INSTALL_CAS}; every answer is qualified-draft at best"
_OPS: dict[str, str] = {
    "simplify": f"simplify(expression, {_COMMON}): SymPy's simplest form, re-checked by G6's own evaluation at random "
                f"points and by the difference simplifying to 0 (or exactly, for a rational expression). {_HOW}",
    "expand": f"expand(expression, {_COMMON}): products and powers multiplied out, re-checked numerically and, for a "
              "rational expression, exactly at random integer points",
    "factor": f"factor(expression, {_COMMON}): factors over the rationals, re-checked by expanding them back, "
              "numerically and exactly",
    "differentiate": f"differentiate(expression, variable, order=1, {_COMMON}): the derivative, re-checked by numeric "
                     "differentiation of G6's own evaluation and exactly for a rational expression",
    "integrate": f"integrate(expression, variable, lower, upper, {_COMMON}): an antiderivative, re-checked by "
                 "differentiating it back, or with lower and upper (oo allowed) a definite integral, re-checked by "
                 "numeric quadrature",
    "solve": f"solve(equations=['x^2 - 5*x + 6 = 0'], unknowns, domain=complex|real, {_COMMON}): every solution "
             "substituted back; says whether the solution set is complete, as SymPy reports",
    "limit": f"limit(expression, variable, point=0 (oo allowed), direction=+-|+|-, {_COMMON}): the limit, re-checked "
             "by approaching the point numerically",
    "series": f"series(expression, variable, point=0, order=6, {_COMMON}): the Taylor series, each coefficient "
              "re-checked against derivatives",
    "determinant": f"determinant(matrix=[[...], ...], {_COMMON}): re-checked by G6's own elimination",
    "inverse": f"inverse(matrix, {_COMMON}): re-checked by multiplying back to the identity",
    "eigenvalues": f"eigenvalues(matrix, {_COMMON}): with multiplicities, each re-checked by det(A - lambda I) = 0 and "
                   "against mpmath's own eigenvalues",
    "factor_integer": "factor_integer(n): the prime factors, re-checked by multiplying back and G6's own primality "
                      "test",
    "is_prime": "is_prime(n): SymPy's answer, saying whether it is definitive at this size (below 2^64), re-checked by "
                "G6's own test",
    "gcd": "gcd(numbers=[...]): the greatest common divisor, re-checked by Bezout coefficients",
    "sage_evaluate": "sage_evaluate(expression in SageMath's syntax, symbols, time_limit (at most 600)): screened by "
                     "G6's parser with a fixed table of SageMath functions, then evaluated by SageMath and not "
                     f"re-checked. SageMath is GPL and G6 never installs it: put sage on PATH or set {SAGE_ENV}"
                     + _SENSITIVE,
    "sage_version": "sage_version(time_limit): which SageMath would compute, from sage --version" + _SENSITIVE,
}


class AlgebraInput(BaseModel):
    """One op's parameters. Fields an op does not use are ignored by it; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["simplify", "expand", "factor", "differentiate", "integrate", "solve", "limit", "series",
                "determinant", "inverse", "eigenvalues", "factor_integer", "is_prime", "gcd", "sage_evaluate",
                "sage_version", "ops", "help"] = "help"
    expression: str = Field("", max_length=2 * MAX_CHARS)
    equations: list[str] = Field(default_factory=list, max_length=2 * inputs.MAX_EQUATIONS)
    symbols: list[str] | dict[str, list[str]] | None = Field(None, max_length=2 * MAX_SYMBOLS)
    variable: str = Field("", max_length=64)
    unknowns: list[str] = Field(default_factory=list, max_length=2 * inputs.MAX_EQUATIONS)
    order: int | None = None
    lower: str | int | None = None
    upper: str | int | None = None
    point: str | int = 0
    direction: Literal["+-", "+", "-"] = "+-"
    domain: Literal["complex", "real"] = "complex"
    matrix: list[list[str | int]] = Field(default_factory=list, max_length=2 * inputs.MAX_MATRIX)
    n: int | None = None
    numbers: list[int] = Field(default_factory=list, max_length=2 * inputs.MAX_NUMBERS)
    seed: int = Field(0, ge=0, le=2**32)
    time_limit: float | None = Field(None, gt=0, allow_inf_nan=False)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _common(d: AlgebraInput) -> dict[str, Any]:
    out: dict[str, Any] = {"seed": d.seed, "request_id": d.request_id, "run_id": d.run_id}
    if d.time_limit is not None:
        out["time_limit"] = d.time_limit
    return out


def _sage(d: AlgebraInput) -> dict[str, Any]:
    out: dict[str, Any] = {"request_id": d.request_id, "run_id": d.run_id}
    if d.time_limit is not None:
        out["time_limit"] = d.time_limit
    return out


_RUN: dict[str, Callable[[AlgebraInput], Any]] = {
    "simplify": lambda d: ops.simplify(d.expression, symbols=d.symbols, **_common(d)),
    "expand": lambda d: ops.expand(d.expression, symbols=d.symbols, **_common(d)),
    "factor": lambda d: ops.factor(d.expression, symbols=d.symbols, **_common(d)),
    "differentiate": lambda d: ops.differentiate(d.expression, d.variable, order=1 if d.order is None else d.order,
                                                 symbols=d.symbols, **_common(d)),
    "integrate": lambda d: ops.integrate(d.expression, d.variable, lower=d.lower, upper=d.upper, symbols=d.symbols,
                                         **_common(d)),
    "solve": lambda d: ops.solve(d.equations or ([d.expression] if d.expression else []), unknowns=d.unknowns,
                                 domain=d.domain, symbols=d.symbols, **_common(d)),
    "limit": lambda d: ops.limit(d.expression, d.variable, point=d.point, direction=d.direction, symbols=d.symbols,
                                 **_common(d)),
    "series": lambda d: ops.series(d.expression, d.variable, point=d.point, order=6 if d.order is None else d.order,
                                   symbols=d.symbols, **_common(d)),
    "determinant": lambda d: ops.determinant(d.matrix, symbols=d.symbols, **_common(d)),
    "inverse": lambda d: ops.inverse(d.matrix, symbols=d.symbols, **_common(d)),
    "eigenvalues": lambda d: ops.eigenvalues(d.matrix, symbols=d.symbols, **_common(d)),
    "factor_integer": lambda d: ops.factor_integer(d.n, **_common(d)),
    "is_prime": lambda d: ops.is_prime(d.n, **_common(d)),
    "gcd": lambda d: ops.gcd(d.numbers, **_common(d)),
    "sage_evaluate": lambda d: sage.sage_evaluate(d.expression, symbols=d.symbols, **_sage(d)),
    "sage_version": lambda d: sage.sage_version(**_sage(d)),
}


@component_maturity(
    "beta",
    reason="SymPy in G6's own worker process under a time limit, on input read only by G6's own parser; every answer "
           "re-checked independently before it is reported, undecided re-checks said so; SageMath only as an "
           "external program the user installs, its ops sensitive; qualified-draft at best",
)
class AlgebraBlock(AIBlock[AlgebraInput, dict, dict]):
    """Do computer algebra with SymPy, re-check every answer, and say what the re-check rests on. Never
    ``verified``."""

    def __init__(self, name: str = "algebra", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: AlgebraInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        result = _RUN[data.op](data)
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


__all__ = ["AlgebraBlock", "AlgebraInput", "SENSITIVE_OPS"]
