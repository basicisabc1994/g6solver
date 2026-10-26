# SPDX-License-Identifier: Apache-2.0
"""The second engine: Z3's own Datalog engine (``z3.Fixedpoint`` with ``engine=datalog``), in process.

Z3 is imported only when this engine runs (``pip install 'g6[formal]'``). The program is encoded over a finite sort:
every constant of the facts and rules gets a code, its place in the comparison order (integers by value below texts by
code point), as a bit-vector just wide enough for them all. So ``=`` and ``!=`` compare codes and ``<``, ``<=``, ``>``,
``>=`` are unsigned bit-vector comparisons that order the codes as the constants are ordered. Every relation is
registered, every fact added and every rule given as written, negations included (Z3's engine stratifies them itself).
Each relation is then queried with a fresh variable per column, and Z3's answer, a disjunction of one conjunction of
column equalities per tuple, is read back into tuples. An answer of any other shape is refused
(``G6_E_DATALOG_SOLVER_ERROR``), never guessed at.
"""
from __future__ import annotations

import importlib
import time
from typing import Any

from g6.datalog.envelope import INSTALL_Z3, SOLVER_ERROR, SOLVER_UNAVAILABLE, TIME_LIMIT, Refusal
from g6.datalog.language import Atom, Comparison, Const, Negation, Program, Value

Row = tuple[Value, ...]


def _z3() -> Any:
    try:
        return importlib.import_module("z3")
    except Exception as exc:  # noqa: BLE001 - ImportError, or the native library failing to load
        raise Refusal(SOLVER_UNAVAILABLE, f"z3-solver is not importable here ({type(exc).__name__}); install it "
                                          f"with {INSTALL_Z3}") from None


def available() -> bool:
    try:
        _z3()
    except Refusal:
        return False
    return True


def version() -> str:
    try:
        return str(_z3().get_version_string())
    except Refusal:
        return ""


def _remaining_ms(deadline: float | None) -> int | None:
    if deadline is None:
        return None
    left = deadline - time.monotonic()
    if left <= 0:
        raise Refusal(TIME_LIMIT, "Z3 did not finish within the time limit")
    return max(1, int(left * 1000))


def solve(program: Program, *, deadline: float | None = None) -> dict[str, set[Row]]:
    """Every relation of ``program`` as Z3's Datalog engine derives it. ``deadline`` is a :func:`time.monotonic` time."""
    z3 = _z3()
    _remaining_ms(deadline)
    try:
        return _solve(z3, program, deadline)
    except z3.Z3Exception as exc:
        raise Refusal(SOLVER_ERROR, f"Z3 failed: {exc}"[:500]) from None


def _solve(z3: Any, program: Program, deadline: float | None) -> dict[str, set[Row]]:
    constants = program.constants()
    code = {value: i for i, value in enumerate(constants)}
    ctx = z3.Context()
    sort = z3.BitVecSort(max(1, (len(constants) - 1).bit_length()), ctx)
    fp = z3.Fixedpoint(ctx=ctx)
    fp.set(engine="datalog")
    names = sorted(program.arities)
    relation = {name: z3.Function(f"r{i}", *([sort] * program.arities[name]), z3.BoolSort(ctx))
                for i, name in enumerate(names)}
    fp.register_relation(*relation.values())
    pool: list[Any] = []

    def variable(k: int) -> Any:
        while len(pool) <= k:  # named and declared in one order, so Z3's answers number them by column
            pool.append(z3.BitVec(f"v{len(pool):05d}", sort))
            fp.declare_var(pool[-1])
        return pool[k]

    def value(v: Value) -> Any:
        return z3.BitVecVal(code[v], sort)

    for name, rows in program.facts.items():
        for row in rows:
            fp.fact(relation[name](*[value(v) for v in row]))
    compare = {"=": lambda a, b: a == b, "!=": lambda a, b: a != b, "<": z3.ULT, "<=": z3.ULE, ">": z3.UGT,
               ">=": z3.UGE}
    for rule in program.rules:
        slots: dict[str, Any] = {}

        def term(t: Any) -> Any:
            if isinstance(t, Const):
                return value(t.value)
            if t.name not in slots:
                slots[t.name] = variable(len(slots))
            return slots[t.name]

        def atom(a: Atom) -> Any:
            return relation[a.relation](*[term(t) for t in a.args])

        body = []
        for lit in rule.body:
            if isinstance(lit, Negation):
                body.append(z3.Not(atom(lit.atom)))
            elif isinstance(lit, Comparison):
                body.append(compare[lit.op](term(lit.left), term(lit.right)))
            else:
                body.append(atom(lit))
        fp.rule(atom(rule.head), body)
    out: dict[str, set[Row]] = {}
    for name in names:
        arity = program.arities[name]
        if (ms := _remaining_ms(deadline)) is not None:
            fp.set("timeout", ms)
        answer = fp.query(relation[name](*[variable(k) for k in range(arity)]))
        if answer == z3.unknown:
            why = fp.reason_unknown()
            if "timeout" in why or "canceled" in why:
                raise Refusal(TIME_LIMIT, "Z3 did not finish within the time limit")
            raise Refusal(SOLVER_ERROR, f"Z3 could not decide {name}: {why}"[:500])
        out[name] = _rows(z3, fp.get_answer(), arity, constants, name)
    return out


def _rows(z3: Any, answer: Any, arity: int, constants: list[Value], name: str) -> set[Row]:
    def shape(part: Any) -> Refusal:
        return Refusal(SOLVER_ERROR, f"Z3's answer for {name} has a shape G6 does not read: {part.sexpr()[:200]}")

    if z3.is_false(answer):
        return set()
    if z3.is_true(answer):
        if arity == 0:
            return {()}
        raise shape(answer)
    rows = set()
    for disjunct in (answer.children() if z3.is_or(answer) else [answer]):
        row: list[Value | None] = [None] * arity
        for part in (disjunct.children() if z3.is_and(disjunct) else [disjunct]):
            if not z3.is_eq(part):
                raise shape(part)
            left, right = part.children()
            var, val = (left, right) if z3.is_var(left) else (right, left)
            if not (z3.is_var(var) and z3.is_bv_value(val)):
                raise shape(part)
            k, n = z3.get_var_index(var), val.as_long()
            if not 0 <= k < arity or row[k] is not None or n >= len(constants):
                raise shape(part)
            row[k] = constants[n]
        if any(v is None for v in row):
            raise shape(disjunct)
        rows.add(tuple(row))  # type: ignore[arg-type]
    return rows


__all__ = ["available", "solve", "version"]
