# SPDX-License-Identifier: Apache-2.0
"""The pure-Python engine: semi-naive bottom-up evaluation, one stratum at a time, recording how each tuple was derived.

The strata run in order, so every negated relation is complete before a rule negates it. Within a stratum the first
round applies every rule to everything known; each later round applies the rules again with one body atom of the
stratum's own relations drawn only from the tuples the round before added (the delta), until a round adds nothing. A
round reads only what was known when it began, so every tuple's first derivation uses facts and tuples of a strictly
lower rank: that derivation is kept as its support, which :mod:`g6.datalog.checks` re-checks.

Limits: at most :data:`MAX_TUPLES` tuples in the model (``G6_E_DATALOG_TOO_MANY_TUPLES``) and the caller's deadline
(``G6_E_DATALOG_TIME_LIMIT``), checked while joining, so an exploding join stops instead of running on.
"""
from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from g6.datalog.envelope import TIME_LIMIT, TOO_MANY_TUPLES, Refusal
from g6.datalog.language import Atom, Comparison, Const, Negation, Program, Rule, Value, Var, order_key

#: The most tuples a model may hold, facts included. Read at call time, so a test can lower it.
MAX_TUPLES = 100_000
_TICKS = 4096

Row = tuple[Value, ...]


@dataclass(frozen=True)
class Justification:
    """How a tuple was first derived: from a fact (``rule`` is ``None``) or by rule number ``rule`` under ``bindings``
    (every variable of the rule, anonymous ones included). ``rank`` 0 is a fact; a derived tuple's rank is the round
    that added it, and every tuple its rule used has a lower rank."""

    rule: int | None
    bindings: tuple[tuple[str, Value], ...] = ()
    rank: int = 0


@dataclass
class Model:
    """Every relation's tuples, each tuple's support, and how many rounds ran."""

    relations: dict[str, set[Row]]
    support: dict[tuple[str, Row], Justification] = field(default_factory=dict)
    rounds: int = 0


class _Clock:
    def __init__(self, deadline: float | None) -> None:
        self.deadline = deadline
        self.ticks = 0
        self.now()

    def now(self) -> None:
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise Refusal(TIME_LIMIT, "the pure-Python engine did not finish within the time limit")

    def tick(self) -> None:
        self.ticks += 1
        if self.ticks % _TICKS == 0:
            self.now()


@dataclass(frozen=True)
class _Scan:
    """One positive atom of a plan: the relation, where it reads (``delta`` or ``full``), the positions already bound
    when it runs (each a constant or a variable bound earlier) and the variables it binds."""

    relation: str
    delta: bool
    keys: tuple[tuple[int, Any, bool], ...]       # (position, constant value or variable name, is a variable)
    binds: tuple[tuple[int, str], ...]            # (position, variable name), first occurrence in this atom
    repeats: tuple[tuple[int, int], ...]          # (position, position of the same variable's first occurrence)


def _value(term: Any, binding: dict[str, Value]) -> Value:
    return term.value if isinstance(term, Const) else binding[term.name]


def _holds(comparison: Comparison, binding: dict[str, Value]) -> bool:
    left, right = _value(comparison.left, binding), _value(comparison.right, binding)
    if comparison.op == "=":   # values are texts or integers (never booleans), so 1 and "1" differ
        return left == right
    if comparison.op == "!=":
        return left != right
    a, b = order_key(left), order_key(right)
    return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[comparison.op]


def _plan(rule: Rule, delta_at: int | None) -> list[Any]:
    """The rule's body in the order it is joined: the delta atom first (if any), then the other positive atoms as
    written, each filter (a comparison or a negated atom) as soon as its variables are bound."""
    positives = rule.positives
    order = list(range(len(positives)))
    if delta_at is not None:
        order.remove(delta_at)
        order.insert(0, delta_at)
    filters = [lit for lit in rule.body if not isinstance(lit, Atom)]
    bound: set[str] = set()
    steps: list[Any] = []

    def ready(lit: Any) -> bool:
        terms = lit.atom.args if isinstance(lit, Negation) else (lit.left, lit.right)
        return all(t.name in bound for t in terms if isinstance(t, Var))

    def place_filters() -> None:
        for lit in [f for f in filters if ready(f)]:
            steps.append(lit)
            filters.remove(lit)

    place_filters()
    for k in order:
        atom = positives[k]
        keys, binds, repeats, here = [], [], [], {}
        for pos, term in enumerate(atom.args):
            if isinstance(term, Const):
                keys.append((pos, term.value, False))
            elif term.name in bound:
                keys.append((pos, term.name, True))
            elif term.name in here:
                repeats.append((pos, here[term.name]))
            else:
                binds.append((pos, term.name))
                here[term.name] = pos
        steps.append(_Scan(atom.relation, k == delta_at, tuple(keys), tuple(binds), tuple(repeats)))
        bound |= set(here)
        place_filters()
    steps.extend(filters)  # none, for a safe rule
    return steps


class _Indexes:
    """Tuples of one source (the full relations or a delta) indexed by the positions a scan has bound."""

    def __init__(self, source: dict[str, set[Row]]) -> None:
        self.source = source
        self.built: dict[tuple[str, tuple[int, ...]], dict[tuple, list[Row]]] = {}

    def rows(self, relation: str, positions: tuple[int, ...], key: tuple) -> Any:
        if not positions:
            return self.source.get(relation, ())
        index = self.built.get((relation, positions))
        if index is None:
            index = {}
            for row in self.source.get(relation, ()):
                index.setdefault(tuple(row[p] for p in positions), []).append(row)
            self.built[(relation, positions)] = index
        return index.get(key, ())


def _bindings(steps: list[Any], k: int, binding: dict[str, Value], full: dict[str, set[Row]], indexes: _Indexes,
              delta: _Indexes | None, clock: _Clock) -> Iterator[dict[str, Value]]:
    if k == len(steps):
        yield binding
        return
    step = steps[k]
    if isinstance(step, Comparison):
        if _holds(step, binding):
            yield from _bindings(steps, k + 1, binding, full, indexes, delta, clock)
        return
    if isinstance(step, Negation):
        row = tuple(_value(t, binding) for t in step.atom.args)
        if row not in full[step.atom.relation]:
            yield from _bindings(steps, k + 1, binding, full, indexes, delta, clock)
        return
    source = delta if step.delta else indexes
    positions = tuple(p for p, _, _ in step.keys)
    key = tuple(binding[v] if is_var else v for _, v, is_var in step.keys)
    for row in source.rows(step.relation, positions, key):
        clock.tick()
        if any(row[p] != row[q] for p, q in step.repeats):
            continue
        extended = dict(binding)
        for p, name in step.binds:
            extended[name] = row[p]
        yield from _bindings(steps, k + 1, extended, full, indexes, delta, clock)


def solve(program: Program, *, deadline: float | None = None, max_tuples: int | None = None) -> Model:
    """``program``'s model (its perfect model: the least fixpoint of each stratum in turn) with each tuple's support.
    ``deadline`` is a :func:`time.monotonic` time."""
    cap = MAX_TUPLES if max_tuples is None else max_tuples
    clock = _Clock(deadline)
    full: dict[str, set[Row]] = {name: set(program.facts.get(name, ())) for name in program.arities}
    support = {(name, row): Justification(None) for name, rows in full.items() for row in rows}
    total = len(support)
    if total > cap:
        raise Refusal(TOO_MANY_TUPLES, f"the program has {total} facts, more than the {cap} tuples a model may hold")
    rank = 0
    for stratum in program.order:
        members = set(stratum)
        rules = [(i, rule) for i, rule in enumerate(program.rules) if rule.head.relation in members]
        if not rules:
            continue
        first = [(i, rule, _plan(rule, None)) for i, rule in rules]
        later = [(i, rule, _plan(rule, k)) for i, rule in rules
                 for k, atom in enumerate(rule.positives) if atom.relation in members]
        plans, delta = first, None
        while True:
            rank += 1
            indexes = _Indexes(full)
            delta_index = _Indexes(delta) if delta is not None else None
            new: dict[tuple[str, Row], tuple[int, dict[str, Value]]] = {}
            for i, rule, steps in plans:
                head = rule.head
                for binding in _bindings(steps, 0, {}, full, indexes, delta_index, clock):
                    row = tuple(_value(t, binding) for t in head.args)
                    if row not in full[head.relation] and (head.relation, row) not in new:
                        new[(head.relation, row)] = (i, binding)
                        if total + len(new) > cap:
                            raise Refusal(TOO_MANY_TUPLES, f"the program derives more than the {cap} tuples a model "
                                                           "may hold")
            if not new:
                break
            delta = {name: set() for name in members}
            for (name, row), (i, binding) in new.items():
                full[name].add(row)
                delta[name].add(row)
                support[(name, row)] = Justification(i, tuple(sorted(binding.items(), key=lambda kv: kv[0])), rank)
            total += len(new)
            plans = later
            clock.now()
    return Model(full, support, rank)


__all__ = ["Justification", "MAX_TUPLES", "Model", "solve"]
