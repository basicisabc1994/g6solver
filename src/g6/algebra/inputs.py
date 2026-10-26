# SPDX-License-Identifier: Apache-2.0
"""Each op's parameters read into a :class:`Problem`: every expression through G6's parser, every number and name
checked, every limit applied. Pure Python, with no SymPy: the calling process reads a problem to refuse it before any
program starts, and G6's worker reads it again from the same parameters before building anything from it."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from g6.algebra import language as L
from g6.algebra.envelope import INPUT_INVALID, INPUT_TOO_LARGE, Refusal

SYMPY_OPS = ("simplify", "expand", "factor", "differentiate", "integrate", "solve", "limit", "series", "determinant",
             "inverse", "eigenvalues", "factor_integer", "is_prime", "gcd")
MAX_DERIVATIVE_ORDER = 10
MAX_SERIES_ORDER = 12
MAX_EQUATIONS = 6
MAX_MATRIX = 6
MAX_INTEGER_DIGITS = 100
MAX_NUMBERS = 20
DIRECTIONS = ("+-", "+", "-")
DOMAINS = ("complex", "real")


@dataclass(frozen=True)
class Problem:
    """One op's input, as G6 read it."""

    op: str
    declared: dict[str, tuple[str, ...]] = field(default_factory=dict)
    expression: L.Node | None = None
    equations: tuple[L.Node, ...] = ()
    matrix: tuple[tuple[L.Node, ...], ...] = ()
    variable: str = ""
    unknowns: tuple[str, ...] = ()
    order: int = 0
    lower: L.Node | None = None
    upper: L.Node | None = None
    point: L.Node | None = None
    direction: str = "+-"
    domain: str = "complex"
    integer: int | None = None
    numbers: tuple[int, ...] = ()

    def shown(self) -> dict[str, Any]:
        """G6's reading of the input, each expression written back in G6's canonical form."""
        out: dict[str, Any] = {"op": self.op}
        if self.declared:
            out["symbols"] = {name: list(assumed) for name, assumed in self.declared.items()}
        if self.expression is not None:
            out["expression"] = L.render(self.expression)
        if self.equations:
            out["equations"] = [L.render(e) for e in self.equations]
        if self.matrix:
            out["matrix"] = [[L.render(e) for e in row] for row in self.matrix]
        for name in ("variable", "direction", "domain"):
            if getattr(self, name) and (name != "direction" or self.op == "limit") and (name != "domain"
                                                                                     or self.op == "solve"):
                out[name] = getattr(self, name)
        if self.unknowns:
            out["unknowns"] = list(self.unknowns)
        if self.order:
            out["order"] = self.order
        for name in ("lower", "upper", "point"):
            if getattr(self, name) is not None:
                out[name] = L.render(getattr(self, name))
        if self.integer is not None:
            out["n"] = str(self.integer)
        if self.numbers:
            out["numbers"] = [str(n) for n in self.numbers]
        return out


def _text(value: Any, what: str) -> str:
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise Refusal(INPUT_INVALID, f"{what} is an expression written as text (or a whole number), not {value!r}"[:200])
    if isinstance(value, int):
        try:
            return str(value)
        except ValueError:
            raise Refusal(INPUT_TOO_LARGE, f"{what} is a number of more than {L.MAX_LITERAL_DIGITS} digits") from None
    return value


def _whole(value: Any, what: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < low:
        raise Refusal(INPUT_INVALID, f"{what} must be a whole number of at least {low}, not {value!r}"[:200])
    if value > high:
        raise Refusal(INPUT_TOO_LARGE, f"{what} {value} is above the limit of {high}")
    return value


def _integer(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise Refusal(INPUT_INVALID, f"{what} must be a whole number, not {value!r}"[:200])
    if abs(value) >= 10 ** MAX_INTEGER_DIGITS:
        raise Refusal(INPUT_TOO_LARGE, f"{what} has more than {MAX_INTEGER_DIGITS} digits")
    return value


def _name(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise Refusal(INPUT_INVALID, f"{what} names a symbol, such as x")
    return value


def _choice(value: Any, what: str, allowed: Sequence[str]) -> str:
    if value not in allowed:
        raise Refusal(INPUT_INVALID, f"{what} is one of {', '.join(allowed)}, not {value!r}"[:200])
    return value


def _list(value: Any, what: str, low: int, high: int) -> list[Any]:
    if not isinstance(value, list | tuple) or len(value) < low:
        raise Refusal(INPUT_INVALID, f"{what} is a list of at least {low}")
    if len(value) > high:
        raise Refusal(INPUT_TOO_LARGE, f"{what} has {len(value)} entries; the limit is {high}")
    return list(value)


def read(op: str, params: Mapping[str, Any]) -> Problem:
    """``params`` for ``op`` read into a problem, or a :class:`~g6.algebra.envelope.Refusal` naming what is wrong."""
    if op not in SYMPY_OPS:
        raise Refusal(INPUT_INVALID, f"{op!r} is not one of g6.algebra's SymPy ops")
    get = params.get
    if op in ("factor_integer", "is_prime"):
        n = _integer(get("n"), "n")
        if op == "factor_integer" and abs(n) < 2:
            raise Refusal(INPUT_INVALID, f"factor_integer factors a whole number at least 2 in size, not {n}")
        return Problem(op, integer=n)
    if op == "gcd":
        numbers = tuple(_integer(v, "numbers") for v in _list(get("numbers"), "numbers", 2, MAX_NUMBERS))
        return Problem(op, numbers=numbers)
    variable = _name(get("variable"), "variable") if op in ("differentiate", "integrate", "limit", "series") else ""
    unknowns = tuple(_name(u, "unknowns") for u in _list(get("unknowns") or [], "unknowns", 0, MAX_EQUATIONS))
    declared = L.declare(get("symbols"), implicit=([variable] if variable else []) + list(unknowns))

    def parse(text: Any, where: str, **kw: Any) -> L.Node:
        return L.parse(_text(text, where), declared, field=where, **kw)

    if op in ("determinant", "inverse", "eigenvalues"):
        rows = _list(get("matrix"), "matrix", 1, MAX_MATRIX)
        if not all(isinstance(r, list | tuple) and len(r) == len(rows) for r in rows):
            raise Refusal(INPUT_INVALID, f"matrix is a square list of rows: {len(rows)} rows of {len(rows)} entries")
        matrix = tuple(tuple(parse(v, f"matrix[{i}][{j}]") for j, v in enumerate(r)) for i, r in enumerate(rows))
        return Problem(op, declared, matrix=matrix)
    if op == "solve":
        texts = get("equations") or ([get("expression")] if get("expression") else [])
        equations = tuple(parse(t, f"equations[{i}]", equation=True)
                          for i, t in enumerate(_list(texts, "equations", 1, MAX_EQUATIONS)))
        if not unknowns:
            found = sorted({n for e in equations for n in L.free_symbols(e)})
            if not found or len(found) > len(equations):
                raise Refusal(INPUT_INVALID, f"name the unknowns to solve for: the equations use "
                                             f"{', '.join(found) or 'no symbol'}")
            unknowns = tuple(found)
        domain = _choice(get("domain", "complex"), "domain", DOMAINS)
        if domain == "real" and (len(equations) > 1 or len(unknowns) > 1):
            raise Refusal(INPUT_INVALID, "domain real is for one equation in one unknown; a system is solved over the "
                                         "complex numbers")
        return Problem(op, declared, equations=equations, unknowns=unknowns, domain=domain)
    expression = parse(get("expression"), "expression")
    if op in ("simplify", "expand", "factor"):
        return Problem(op, declared, expression=expression)
    if op == "differentiate":
        order = _whole(get("order", 1) if get("order") is not None else 1, "order", 1, MAX_DERIVATIVE_ORDER)
        return Problem(op, declared, expression=expression, variable=variable, order=order)
    if op == "integrate":
        lower, upper = get("lower"), get("upper")
        if (lower is None) != (upper is None):
            raise Refusal(INPUT_INVALID, "give both lower and upper for a definite integral, or neither")
        if lower is None:
            return Problem(op, declared, expression=expression, variable=variable)
        bounds = [parse(b, where, infinity=True) for b, where in ((lower, "lower"), (upper, "upper"))]
        if any(variable in L.free_symbols(b) for b in bounds):
            raise Refusal(INPUT_INVALID, f"a bound of the integral may not use the variable {variable}")
        return Problem(op, declared, expression=expression, variable=variable, lower=bounds[0], upper=bounds[1])
    point = parse(get("point") if get("point") is not None else "0", "point", infinity=True)
    if variable in L.free_symbols(point):
        raise Refusal(INPUT_INVALID, f"the point may not use the variable {variable}")
    if op == "limit":
        return Problem(op, declared, expression=expression, variable=variable, point=point,
                       direction=_choice(get("direction", "+-"), "direction", DIRECTIONS))
    order = _whole(get("order") if get("order") is not None else 6, "order", 1, MAX_SERIES_ORDER)
    return Problem(op, declared, expression=expression, variable=variable, point=point, order=order)


__all__ = ["DIRECTIONS", "DOMAINS", "MAX_DERIVATIVE_ORDER", "MAX_EQUATIONS", "MAX_INTEGER_DIGITS", "MAX_MATRIX",
           "MAX_NUMBERS", "MAX_SERIES_ORDER", "Problem", "SYMPY_OPS", "read"]
