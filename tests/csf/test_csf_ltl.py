# SPDX-License-Identifier: Apache-2.0
"""g6.csf.ltl: the parser, the bottom-up evaluator and the printer (audit 012 §11: kept and hardened).

The evaluator was rewritten to cost |formula| x len(trace); ``_archive_evaluate`` below is the archive's recursive
semantics, re-implemented here as an independent oracle, and the two must agree everywhere.
"""
from __future__ import annotations

import random
import time

import pytest

from g6.csf.ltl import (
    MAX_DEPTH,
    MAX_FORMULA_CHARS,
    LTLAlways,
    LTLAnd,
    LTLAtom,
    LTLEventually,
    LTLNext,
    LTLNot,
    LTLOr,
    LTLRelease,
    LTLUntil,
    evaluate_ltl,
    ltl_to_str,
    parse_ltl,
)


def _archive_evaluate(f, trace, pos=0):
    """The earlier version's finite-trace semantics, recursively: the reference the port must match."""
    if pos >= len(trace):
        if isinstance(f, LTLNot):
            return not _archive_evaluate(f.inner, trace, pos)
        if isinstance(f, LTLAnd):
            return _archive_evaluate(f.left, trace, pos) and _archive_evaluate(f.right, trace, pos)
        if isinstance(f, LTLOr):
            return _archive_evaluate(f.left, trace, pos) or _archive_evaluate(f.right, trace, pos)
        return isinstance(f, LTLAlways | LTLRelease)
    if isinstance(f, LTLAtom):
        return trace[pos].get(f.predicate, False)
    if isinstance(f, LTLNot):
        return not _archive_evaluate(f.inner, trace, pos)
    if isinstance(f, LTLAnd):
        return _archive_evaluate(f.left, trace, pos) and _archive_evaluate(f.right, trace, pos)
    if isinstance(f, LTLOr):
        return _archive_evaluate(f.left, trace, pos) or _archive_evaluate(f.right, trace, pos)
    if isinstance(f, LTLNext):
        return _archive_evaluate(f.inner, trace, pos + 1)
    if isinstance(f, LTLAlways):
        return all(_archive_evaluate(f.inner, trace, i) for i in range(pos, len(trace)))
    if isinstance(f, LTLEventually):
        return any(_archive_evaluate(f.inner, trace, i) for i in range(pos, len(trace)))
    if isinstance(f, LTLUntil):
        for j in range(pos, len(trace)):
            if _archive_evaluate(f.right, trace, j):
                return True
            if not _archive_evaluate(f.left, trace, j):
                return False
        return False
    for j in range(pos, len(trace)):  # LTLRelease
        if not _archive_evaluate(f.right, trace, j):
            return False
        if _archive_evaluate(f.left, trace, j):
            return True
    return True


def random_formula(rng: random.Random, atoms: list[str], depth: int):
    """A random formula over ``atoms`` (also used by the SMT tests)."""
    if depth == 0 or rng.random() < 0.25:
        return LTLAtom(rng.choice(atoms))
    kind = rng.choice([LTLNot, LTLNext, LTLAlways, LTLEventually, LTLAnd, LTLOr, LTLUntil, LTLRelease])
    if kind in (LTLNot, LTLNext, LTLAlways, LTLEventually):
        return kind(random_formula(rng, atoms, depth - 1))
    return kind(random_formula(rng, atoms, depth - 1), random_formula(rng, atoms, depth - 1))


def test_parser_reads_every_operator_with_the_documented_precedence() -> None:
    assert parse_ltl("G(safe) & F(done)") == LTLAnd(LTLAlways(LTLAtom("safe")), LTLEventually(LTLAtom("done")))
    assert parse_ltl("a | b & c") == LTLOr(LTLAtom("a"), LTLAnd(LTLAtom("b"), LTLAtom("c")))
    assert parse_ltl("!a U X(b)") == LTLUntil(LTLNot(LTLAtom("a")), LTLNext(LTLAtom("b")))
    assert parse_ltl("a R b") == LTLRelease(LTLAtom("a"), LTLAtom("b"))
    assert parse_ltl(" ( a | b ) & c ") == LTLAnd(LTLOr(LTLAtom("a"), LTLAtom("b")), LTLAtom("c"))


@pytest.mark.parametrize("text", ["", "a + b", "G(", "a b", "G", "a & U", "U", "X a", "()", "a U b U c", "1a"])
def test_malformed_formulas_are_value_errors(text: str) -> None:
    with pytest.raises(ValueError):
        parse_ltl(text)


def test_deep_or_long_formulas_are_value_errors_not_recursion_errors() -> None:
    """Hardened: the archive's recursive descent raised RecursionError on deep input."""
    for text in ("!" * (MAX_DEPTH + 1) + "a", "(" * (MAX_DEPTH + 1) + "a" + ")" * (MAX_DEPTH + 1),
                 "G(" * 600 + "a" + ")" * 600, " & ".join(["a"] * MAX_FORMULA_CHARS)):
        with pytest.raises(ValueError):
            parse_ltl(text)
    assert parse_ltl("!" * MAX_DEPTH + "a") is not None  # survival: the limit itself is accepted


def test_printer_round_trips_random_formulas() -> None:
    rng = random.Random(3)
    for _ in range(300):
        formula = random_formula(rng, ["a", "b", "c"], 4)
        assert parse_ltl(ltl_to_str(formula)) == formula


def test_evaluator_agrees_with_the_archive_semantics_everywhere() -> None:
    """Survival control for the rewrite: same value as the archive's recursion on 600 random formulas and traces, at
    every position including past the end."""
    rng = random.Random(7)
    for _ in range(600):
        formula = random_formula(rng, ["a", "b", "c"], 3)
        trace = [{p: rng.random() < 0.5 for p in "abc" if rng.random() < 0.8} for _ in range(rng.randint(0, 5))]
        for pos in range(len(trace) + 2):
            assert evaluate_ltl(formula, trace, pos) == _archive_evaluate(formula, trace, pos), (formula, trace, pos)


def test_the_archive_cases_still_hold() -> None:
    assert evaluate_ltl(LTLAtom("safe"), [{}]) is False
    assert evaluate_ltl(LTLNext(LTLAtom("a")), [{"a": True}]) is False
    assert evaluate_ltl(LTLNext(LTLNot(LTLAtom("a"))), [{"a": True}]) is True  # X at the end reads past the end
    assert evaluate_ltl(LTLAlways(LTLAtom("safe")), []) is True
    assert evaluate_ltl(LTLEventually(LTLAtom("done")), []) is False
    trace = [{"running": True}, {"running": True}, {"done": True}]
    assert evaluate_ltl(parse_ltl("running U done"), trace) is True
    assert evaluate_ltl(parse_ltl("a R b"), [{"a": False, "b": True}, {"a": True, "b": True}]) is True


def test_trace_values_must_be_bools() -> None:
    """Hardened: the archive returned whatever the trace held, so {"a": "no"} read as true."""
    with pytest.raises(ValueError):
        evaluate_ltl(LTLAtom("a"), [{"a": "no"}])
    with pytest.raises(ValueError):
        evaluate_ltl(LTLAtom("a"), "a")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        evaluate_ltl(LTLAtom("a"), [{"a": True}], -1)
    with pytest.raises(TypeError):
        evaluate_ltl("G(a)", [{"a": True}])  # type: ignore[arg-type]


def test_nested_temporal_operators_cost_size_times_length() -> None:
    """Hardened: the archive's recursion was exponential in the nesting depth (here about 300^6 calls)."""
    formula = parse_ltl("G(F(G(F(G(F(a))))))")
    trace = [{"a": i % 7 == 0} for i in range(300)]
    started = time.perf_counter()
    assert evaluate_ltl(formula, trace) is False
    assert time.perf_counter() - started < 2.0
