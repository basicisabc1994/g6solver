# SPDX-License-Identifier: Apache-2.0
"""Finite-trace LTL: ``parse_ltl``, ``evaluate_ltl`` and ``ltl_to_str`` (audit 012 §11, kept and hardened).

Syntax, loosest binding first: ``φ | ψ``; ``φ & ψ``; ``φ U ψ`` and ``φ R ψ`` (one per level; parenthesise to chain);
``!φ``, ``G(φ)``, ``F(φ)``, ``X(φ)``, ``(φ)`` and atoms (identifiers). ``G``, ``F``, ``X``, ``U`` and ``R`` are
operators and cannot be atoms.

Semantics on a finite trace (a list of ``{proposition: bool}``; a missing proposition is false), unchanged from the
archive: past the last position an atom is false, ``G`` and ``R`` are true, and ``F``, ``X`` and ``U`` are false.

Hardened: the evaluator works bottom-up over (subformula, position), so its cost is the formula's size times the
trace's length (the archive's recursion was exponential in the nesting of temporal operators); a formula longer than
``MAX_FORMULA_CHARS`` or nested deeper than ``MAX_DEPTH`` is a ``ValueError``, not a ``RecursionError``; a trace value
that is not a bool is refused instead of being read for its truthiness. The archive's SMV, SMT and Lean printers are
not here: ``g6.csf.smt`` encodes a property against an agent's model, and nothing else needs them.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Union

MAX_FORMULA_CHARS = 10_000
MAX_DEPTH = 100
OPERATORS = frozenset({"G", "F", "X", "U", "R"})


@dataclass(frozen=True)
class LTLAtom:
    """An atomic proposition."""

    predicate: str


@dataclass(frozen=True)
class LTLNot:
    inner: LTLFormula


@dataclass(frozen=True)
class LTLAnd:
    left: LTLFormula
    right: LTLFormula


@dataclass(frozen=True)
class LTLOr:
    left: LTLFormula
    right: LTLFormula


@dataclass(frozen=True)
class LTLNext:
    """``X(φ)``: φ holds at the next position."""

    inner: LTLFormula


@dataclass(frozen=True)
class LTLAlways:
    """``G(φ)``: φ holds at every position from here to the end."""

    inner: LTLFormula


@dataclass(frozen=True)
class LTLEventually:
    """``F(φ)``: φ holds at some position from here to the end."""

    inner: LTLFormula


@dataclass(frozen=True)
class LTLUntil:
    """``φ U ψ``: ψ holds at some position, and φ at every position before it."""

    left: LTLFormula
    right: LTLFormula


@dataclass(frozen=True)
class LTLRelease:
    """``φ R ψ``: ψ holds up to and including the first position where φ holds, or to the end if φ never does."""

    left: LTLFormula
    right: LTLFormula


LTLFormula = Union[LTLAtom, LTLNot, LTLAnd, LTLOr, LTLNext, LTLAlways, LTLEventually, LTLUntil, LTLRelease]
_UNARY = (LTLNot, LTLNext, LTLAlways, LTLEventually)
_BINARY = (LTLAnd, LTLOr, LTLUntil, LTLRelease)
_TOKEN = re.compile(r"\s*(?:([()&|!])|([A-Za-z_][A-Za-z0-9_]*))")


def _tokens(text: str) -> list[str]:
    tokens: list[str] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None:
            if text[pos:].strip():
                raise ValueError(f"unexpected character {text[pos:].lstrip()[0]!r} at position {pos}")
            break
        tokens.append(match.group(1) or match.group(2))
        pos = match.end()
    return tokens


class _Parser:
    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.i = 0
        self.depth = 0

    def peek(self) -> str | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self, expected: str | None = None) -> str:
        tok = self.peek()
        if tok is None or (expected is not None and tok != expected):
            raise ValueError(f"expected {expected or 'more input'!r}, got {tok or 'end of formula'!r}")
        self.i += 1
        return tok

    def disjunction(self) -> LTLFormula:
        node = self.conjunction()
        while self.peek() == "|":
            self.take()
            node = LTLOr(node, self.conjunction())
        return node

    def conjunction(self) -> LTLFormula:
        node = self.binary()
        while self.peek() == "&":
            self.take()
            node = LTLAnd(node, self.binary())
        return node

    def binary(self) -> LTLFormula:
        node = self.unary()
        if self.peek() == "U":
            self.take()
            return LTLUntil(node, self.unary())
        if self.peek() == "R":
            self.take()
            return LTLRelease(node, self.unary())
        return node

    def unary(self) -> LTLFormula:
        tok = self.take()
        if tok in ("!", "(", "G", "F", "X"):
            self.depth += 1
            if self.depth > MAX_DEPTH:
                raise ValueError(f"the formula is nested deeper than {MAX_DEPTH}")
            if tok == "!":
                node: LTLFormula = LTLNot(self.unary())
            else:
                if tok != "(":
                    self.take("(")
                inner = self.disjunction()
                self.take(")")
                node = {"(": inner, "G": LTLAlways(inner), "F": LTLEventually(inner), "X": LTLNext(inner)}[tok]
            self.depth -= 1
            return node
        if tok in OPERATORS or not (tok[0].isalpha() or tok[0] == "_"):
            raise ValueError(f"expected an atom, got {tok!r}")
        return LTLAtom(tok)


def parse_ltl(text: str) -> LTLFormula:
    """The formula ``text`` as an AST; ``ValueError`` when it is not one."""
    if not isinstance(text, str):
        raise ValueError(f"an LTL formula is a string, got {type(text).__name__}")
    if len(text) > MAX_FORMULA_CHARS:
        raise ValueError(f"the formula is longer than {MAX_FORMULA_CHARS} characters")
    parser = _Parser(_tokens(text))
    node = parser.disjunction()
    if parser.peek() is not None:
        raise ValueError(f"unexpected {parser.peek()!r} after a complete formula")
    return node


def _children(node: LTLFormula) -> tuple[LTLFormula, ...]:
    if isinstance(node, LTLAtom):
        return ()
    if isinstance(node, _UNARY):
        return (node.inner,)
    if isinstance(node, _BINARY):
        return (node.left, node.right)
    raise TypeError(f"not an LTL formula: {type(node).__name__}")


def _nodes(formula: LTLFormula) -> list[LTLFormula]:
    """Every distinct subformula of ``formula``, children before parents (``formula`` last). Iterative."""
    order: list[LTLFormula] = []
    seen: set[LTLFormula] = set()
    stack: list[tuple[LTLFormula, bool]] = [(formula, False)]
    while stack:
        node, expanded = stack.pop()
        if node in seen:
            continue
        if expanded:
            seen.add(node)
            order.append(node)
        else:
            stack.append((node, True))
            stack.extend((child, False) for child in _children(node))
    return order


def _past_end(order: list[LTLFormula]) -> dict[LTLFormula, bool]:
    """Each subformula's value past the last position of a trace."""
    value: dict[LTLFormula, bool] = {}
    for node in order:
        if isinstance(node, LTLNot):
            value[node] = not value[node.inner]
        elif isinstance(node, LTLAnd):
            value[node] = value[node.left] and value[node.right]
        elif isinstance(node, LTLOr):
            value[node] = value[node.left] or value[node.right]
        else:  # atoms, F, X and U are false past the end; G and R are true
            value[node] = isinstance(node, LTLAlways | LTLRelease)
    return value


def _proposition(state: Mapping[str, bool], name: str, pos: int) -> bool:
    value = state.get(name, False)
    if not isinstance(value, bool):
        raise ValueError(f"trace position {pos}: proposition {name!r} is {value!r}, not a bool")
    return value


def _table(formula: LTLFormula, trace: Sequence[Mapping[str, bool]]) -> dict[LTLFormula, list[bool]]:
    """Each subformula's value at every position of ``trace``, computed bottom-up from the end of the trace."""
    order = _nodes(formula)
    past = _past_end(order)
    n = len(trace)
    rows: dict[LTLFormula, list[bool]] = {}
    for node in order:
        if isinstance(node, LTLAtom):
            rows[node] = [_proposition(state, node.predicate, pos) for pos, state in enumerate(trace)]
        elif isinstance(node, LTLNot):
            rows[node] = [not v for v in rows[node.inner]]
        elif isinstance(node, LTLAnd | LTLOr):
            pick = all if isinstance(node, LTLAnd) else any
            rows[node] = [pick(pair) for pair in zip(rows[node.left], rows[node.right], strict=True)]
        elif isinstance(node, LTLNext):
            rows[node] = rows[node.inner][1:] + [past[node.inner]]
        else:
            row = [False] * n
            later = past[node]
            for t in range(n - 1, -1, -1):
                if isinstance(node, LTLAlways):
                    later = rows[node.inner][t] and later
                elif isinstance(node, LTLEventually):
                    later = rows[node.inner][t] or later
                elif isinstance(node, LTLUntil):
                    later = rows[node.right][t] or (rows[node.left][t] and later)
                else:  # LTLRelease
                    later = rows[node.right][t] and (rows[node.left][t] or later)
                row[t] = later
            rows[node] = row
    return rows


def evaluate_ltl(formula: LTLFormula, trace: Sequence[Mapping[str, bool]], pos: int = 0) -> bool:
    """Whether ``formula`` holds at position ``pos`` of the finite ``trace``."""
    if isinstance(trace, str | bytes | Mapping) or not isinstance(trace, Sequence):
        raise ValueError("a trace is a list of {proposition: bool} mappings")
    for i, state in enumerate(trace):
        if not isinstance(state, Mapping):
            raise ValueError(f"trace position {i} is a {type(state).__name__}, not a mapping")
    if isinstance(pos, bool) or not isinstance(pos, int) or pos < 0:
        raise ValueError(f"pos must be a non-negative integer, got {pos!r}")
    if pos >= len(trace):
        order = _nodes(formula)
        return _past_end(order)[formula]
    return _table(formula, trace)[formula][pos]


def ltl_to_str(formula: LTLFormula) -> str:
    """``formula`` written in the syntax :func:`parse_ltl` reads back to the same AST."""
    if isinstance(formula, LTLAtom):
        return formula.predicate
    if isinstance(formula, LTLNot):
        return f"!{ltl_to_str(formula.inner)}"
    for kind, prefix in ((LTLAlways, "G"), (LTLEventually, "F"), (LTLNext, "X")):
        if isinstance(formula, kind):
            return f"{prefix}({ltl_to_str(formula.inner)})"
    for kind, op in ((LTLAnd, "&"), (LTLOr, "|"), (LTLUntil, "U"), (LTLRelease, "R")):
        if isinstance(formula, kind):
            return f"({ltl_to_str(formula.left)} {op} {ltl_to_str(formula.right)})"
    raise TypeError(f"not an LTL formula: {type(formula).__name__}")


__all__ = ["MAX_DEPTH", "MAX_FORMULA_CHARS", "OPERATORS", "LTLAlways", "LTLAnd", "LTLAtom", "LTLEventually",
           "LTLFormula", "LTLNext", "LTLNot", "LTLOr", "LTLRelease", "LTLUntil", "evaluate_ltl", "ltl_to_str",
           "parse_ltl"]
