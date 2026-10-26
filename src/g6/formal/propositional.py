# SPDX-License-Identifier: Apache-2.0
"""A pure-Python DPLL SAT solver for Boolean formulas (audit 017 §7: kept; stdlib only, no external solver).

Syntax: atoms are identifiers; ``TRUE`` and ``FALSE``; ``NOT a`` / ``~a`` / ``!a``; ``a AND b`` / ``a & b``;
``a OR b`` / ``a | b``; ``a IMPLIES b`` / ``a -> b`` / ``a => b``; ``a IFF b`` / ``a <-> b``; the function forms
``AND(a, b)``, ``OR(a, b)``, ``IMPLIES(a, b)``, ``IFF(a, b)``; parentheses. Operator words are case-insensitive, atoms
are not. Loosest first: IFF, IMPLIES (right-associative), OR, AND, NOT.

The formula is parsed to a tree, put in CNF by distribution and decided by DPLL with unit propagation and pure-literal
elimination. Distribution can grow exponentially, so a formula whose CNF would pass ``MAX_CLAUSES`` clauses is refused
with :class:`FormulaTooLarge` rather than left to exhaust memory. ``dpll_solve`` is the bare procedure;
``check_propositional`` is the same decision as a labelled result whose evidence says it is pure Python (FM-013).
"""
from __future__ import annotations

import re
import time
from collections.abc import Iterable, Sequence
from functools import reduce
from typing import Any

from g6 import __version__
from g6.formal.envelope import INPUT_INVALID, INPUT_TOO_LARGE, PARSE_ERROR, Refusal, apply_envelope
from g6.formal.schema import FormalResult, ProofArtifact, SolverCheck

MAX_CHARS = 10_000
MAX_CLAUSES = 100_000

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_WORDS = {"AND": "and", "OR": "or", "NOT": "not", "IMPLIES": "implies", "IFF": "iff", "TRUE": "true", "FALSE": "false"}
_SYMBOLS = (("<->", "iff"), ("->", "implies"), ("=>", "implies"), ("&", "and"), ("|", "or"), ("~", "not"),
            ("!", "not"), ("(", "("), (")", ")"), (",", ","))
_BINARY = ("and", "or", "implies", "iff")

Clause = frozenset  # of (atom, polarity) pairs


class FormulaTooLarge(ValueError):
    """The formula is past the size bounds of this pure-Python path."""


def _tokens(formula: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(formula):
        if formula[i].isspace():
            i += 1
            continue
        for symbol, kind in _SYMBOLS:
            if formula.startswith(symbol, i):
                out.append((kind, symbol))
                i += len(symbol)
                break
        else:
            match = _IDENT.match(formula, i)
            if match is None:
                raise ValueError(f"unexpected character {formula[i]!r} at position {i}")
            word = match.group()
            out.append((_WORDS.get(word.upper(), "atom"), word))
            i = match.end()
    out.append(("end", ""))
    return out


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self, ahead: int = 0) -> str:
        return self.tokens[min(self.pos + ahead, len(self.tokens) - 1)][0]

    def take(self, kind: str | None = None) -> tuple[str, str]:
        token = self.tokens[self.pos]
        if kind is not None and token[0] != kind:
            raise ValueError(f"expected {kind!r}, found {token[1] or 'the end of the formula'!r}")
        self.pos += 1
        return token

    def parse(self) -> tuple:
        if self.peek() == "end":
            raise ValueError("the formula is empty")
        node = self.iff()
        if self.peek() != "end":
            raise ValueError(f"unexpected {self.tokens[self.pos][1]!r} at token {self.pos}")
        return node

    def iff(self) -> tuple:
        node = self.implies()
        while self.peek() == "iff":
            self.take()
            node = ("iff", node, self.implies())
        return node

    def implies(self) -> tuple:
        node = self.or_()
        if self.peek() == "implies":
            self.take()
            return ("implies", node, self.implies())
        return node

    def or_(self) -> tuple:
        node = self.and_()
        while self.peek() == "or":
            self.take()
            node = ("or", node, self.and_())
        return node

    def and_(self) -> tuple:
        node = self.not_()
        while self.peek() == "and":
            self.take()
            node = ("and", node, self.not_())
        return node

    def not_(self) -> tuple:
        if self.peek() == "not":
            self.take()
            return ("not", self.not_())
        return self.primary()

    def primary(self) -> tuple:
        kind, text = self.tokens[self.pos]
        if kind in ("true", "false"):
            self.take()
            return ("const", kind == "true")
        if kind == "atom":
            self.take()
            return ("atom", text)
        if kind in _BINARY and self.peek(1) == "(":
            self.take()
            self.take("(")
            left = self.iff()
            self.take(",")
            right = self.iff()
            self.take(")")
            return (kind, left, right)
        if kind == "(":
            self.take()
            node = self.iff()
            self.take(")")
            return node
        raise ValueError(f"expected an atom or a sub-formula, found {text or 'the end of the formula'!r}")


def parse(formula: str) -> tuple:
    """The formula's tree. ``ValueError`` when it cannot be read, ``FormulaTooLarge`` past the bounds."""
    if not isinstance(formula, str):
        raise ValueError("a formula is a string")
    if len(formula) > MAX_CHARS:
        raise FormulaTooLarge(f"the formula is longer than {MAX_CHARS} characters")
    try:
        return _Parser(_tokens(formula)).parse()
    except RecursionError:
        raise FormulaTooLarge("the formula is nested too deeply") from None


def to_text(node: tuple) -> str:
    """The canonical text of a tree, in function form; ``parse(to_text(t)) == t``."""
    kind = node[0]
    if kind == "const":
        return "TRUE" if node[1] else "FALSE"
    if kind == "atom":
        return node[1]
    if kind == "not":
        return f"NOT({to_text(node[1])})"
    return f"{kind.upper()}({to_text(node[1])}, {to_text(node[2])})"


def _atoms(node: tuple, found: set[str]) -> set[str]:
    if node[0] == "atom":
        found.add(node[1])
    elif node[0] != "const":
        for child in node[1:]:
            _atoms(child, found)
    return found


def _and(x: list[Clause], y: list[Clause]) -> list[Clause]:
    if len(x) + len(y) > MAX_CLAUSES:
        raise FormulaTooLarge(f"the formula's CNF has more than {MAX_CLAUSES} clauses")
    return x + y


def _or(x: list[Clause], y: list[Clause]) -> list[Clause]:
    if len(x) * len(y) > MAX_CLAUSES:
        raise FormulaTooLarge(f"the formula's CNF has more than {MAX_CLAUSES} clauses")
    out = []
    for cx in x:
        for cy in y:
            clause = cx | cy
            if not any((atom, not polarity) in clause for atom, polarity in clause):  # drop tautologies
                out.append(clause)
    return out


def _cnf(node: tuple, positive: bool = True) -> list[Clause]:
    """CNF of ``node`` (of its negation when ``positive`` is False). ``[]`` is true, ``[frozenset()]`` false."""
    kind = node[0]
    if kind == "const":
        return [] if node[1] == positive else [frozenset()]
    if kind == "atom":
        return [frozenset({(node[1], positive)})]
    if kind == "not":
        return _cnf(node[1], not positive)
    a, b = node[1], node[2]
    if kind == "implies":  # a -> b is ~a | b; its negation is a & ~b
        return _or(_cnf(a, False), _cnf(b, True)) if positive else _and(_cnf(a, True), _cnf(b, False))
    if kind == "iff":  # (~a | b) & (a | ~b); its negation is (a | b) & (~a | ~b)
        return _and(_or(_cnf(a, not positive), _cnf(b, True)), _or(_cnf(a, positive), _cnf(b, False)))
    left, right = _cnf(a, positive), _cnf(b, positive)
    return _and(left, right) if (kind == "and") == positive else _or(left, right)


def _dpll(clauses: list[Clause], assignment: dict[str, bool]) -> dict[str, bool] | None:
    while True:
        remaining = []
        for clause in clauses:
            if any(assignment.get(atom) == polarity for atom, polarity in clause):
                continue
            open_literals = frozenset(lit for lit in clause if lit[0] not in assignment)
            if not open_literals:
                return None
            remaining.append(open_literals)
        clauses = remaining
        if not clauses:
            return assignment
        units = [next(iter(c)) for c in clauses if len(c) == 1]
        if units:
            for atom, polarity in units:
                if assignment.setdefault(atom, polarity) != polarity:
                    return None
            continue
        seen: dict[str, set[bool]] = {}
        for clause in clauses:
            for atom, polarity in clause:
                seen.setdefault(atom, set()).add(polarity)
        pure = {atom: next(iter(p)) for atom, p in seen.items() if len(p) == 1}
        if not pure:
            break
        assignment.update(pure)
    atom = min(seen)
    for value in (True, False):
        found = _dpll(clauses, {**assignment, atom: value})
        if found is not None:
            return found
    return None


def _solve(tree: tuple, variables: Iterable[str]) -> dict[str, bool] | None:
    names = set(variables)
    bad = sorted(repr(n) for n in names if not isinstance(n, str) or not _IDENT.fullmatch(n) or n.upper() in _WORDS)
    if bad:
        raise ValueError(f"not atom names: {', '.join(bad)}")
    try:
        names |= _atoms(tree, set())
        model = _dpll(_cnf(tree), {})
    except RecursionError:
        raise FormulaTooLarge("the formula is too deeply nested for the pure-Python DPLL") from None
    if model is None:
        return None
    return {name: model.get(name, True) for name in sorted(names)}  # an unconstrained atom can take either value


def dpll_solve(formula: str, *, variables: Iterable[str] = ()) -> dict[str, bool] | None:
    """A satisfying assignment of ``formula`` over its atoms and ``variables``, or ``None`` when it has none.

    The bare decision procedure, with no label: ``check_propositional`` returns the same decision as a result.
    Raises ``ValueError`` for a formula it cannot read, and ``FormulaTooLarge`` (a ``ValueError``) past the bounds.
    """
    return _solve(parse(formula), variables)


def _read(text: Any, what: str) -> tuple:
    try:
        return parse(text)
    except FormulaTooLarge as exc:
        raise Refusal(INPUT_TOO_LARGE, f"{what}: {exc}") from None
    except ValueError as exc:
        raise Refusal(PARSE_ERROR, f"{what}: {exc}") from None


def _fold(trees: Sequence[tuple]) -> tuple:
    return reduce(lambda acc, tree: ("and", acc, tree), trees[1:], trees[0])


def decide(query: str) -> str:
    """``"sat"`` or ``"unsat"`` for one canonical query (used by :func:`g6.formal.recheck`)."""
    try:
        return "unsat" if _solve(_read(query, "the query"), ()) is None else "sat"
    except FormulaTooLarge as exc:
        raise Refusal(INPUT_TOO_LARGE, str(exc)) from None


def artifact_queries(artifact: ProofArtifact) -> tuple[str, tuple[str, ...]]:
    """The statement and query an artifact's assumptions give, refusing fields that are not canonical."""
    if artifact.question != "satisfiable" or artifact.goal or artifact.sorts or not artifact.assumptions:
        raise Refusal(PARSE_ERROR, "a propositional artifact asks only whether its assumptions are satisfiable")
    trees = [_read(a, "an assumption") for a in artifact.assumptions]
    if tuple(to_text(t) for t in trees) != artifact.assumptions:
        raise Refusal(PARSE_ERROR, "the artifact's assumptions are not in canonical form")
    query = to_text(_fold(trees))
    return query, (query,)


def check_propositional(formula: str, variables: Iterable[str] = (), assertions: Sequence[str] = (), *,
                        request_id: str = "", run_id: str = "") -> FormalResult:
    """Is ``formula`` ∧ every assertion satisfiable? Decided by the pure-Python DPLL, and labelled as such."""
    started = time.monotonic()
    try:
        if isinstance(assertions, str):
            raise Refusal(INPUT_INVALID, "assertions must be a list of formulas, not one string")
        # Each input is parsed on its own and the trees are joined, so text in one cannot regroup another.
        trees = [_read(formula, "the formula"), *(_read(a, f"assertion {i}") for i, a in enumerate(assertions, 1))]
        assumptions = tuple(to_text(t) for t in trees)
        query = to_text(_fold(trees))
        try:
            model = _solve(_fold(trees), variables)
        except FormulaTooLarge as exc:
            raise Refusal(INPUT_TOO_LARGE, str(exc)) from None
        except ValueError as exc:
            raise Refusal(INPUT_INVALID, str(exc)) from None
        artifact = ProofArtifact(operation="check_sat", question="satisfiable", statement=query,
                                 assumptions=assumptions, solver="propositional", solver_version=f"g6 {__version__}",
                                 checks=(SolverCheck(query=query, result="unsat" if model is None else "sat"),))
        result = FormalResult(operation="check_sat", backend="propositional", satisfiable=model is not None,
                              model=model or {}, artifact=artifact)
    except Refusal as refusal:
        result = FormalResult(operation="check_sat", backend="propositional", code=refusal.code, reason=refusal.reason)
    result = result.model_copy(update={"elapsed_s": round(time.monotonic() - started, 4)})
    return apply_envelope(result, backend="propositional", operation="check_sat", request_id=request_id, run_id=run_id)


__all__ = ["MAX_CHARS", "MAX_CLAUSES", "FormulaTooLarge", "artifact_queries", "check_propositional", "decide",
           "dpll_solve", "parse", "to_text"]
