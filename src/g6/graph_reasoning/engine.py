# SPDX-License-Identifier: Apache-2.0
"""The pure-Python engine of g6.graph_reasoning: one static fixpoint over exact rational intervals.

Every atom starts at its facts (intersected) or at the unknown ``[0, 1]``. The strata run in order; within a stratum,
each round computes, from the intervals as they stood at the end of the previous round, every firing of every rule
whose head is in the stratum (after the first round, only for head groundings that read an atom that changed in the
round before, since the others give the steps they gave then), then intersects each head's interval with the rule's
interval. A round that changes nothing ends the stratum. A rule's interval is its
fixed bound, or its annotation function over the intervals of every qualified atom of every clause (see
:mod:`g6.graph_reasoning.language` for what qualifies and when a rule fires).

Intervals only tighten, every fixed bound and fact is one of finitely many values, and an annotation function only
reads labels of lower strata, which are final when it runs, so the fixpoint is reached in finitely many rounds and does
not depend on the order rules or facts are written. A tightening that would leave an atom's interval empty is a
conflict: the engine stops and reports the atom, its interval before and the step that emptied it. It never resets the
atom or chooses a side.

For every atom the engine records each step that tightened it (:class:`Step`): the fact, or the rule, its head
bindings, the full groundings it used (the witnesses) and the intervals of the body atoms they read when it fired.
That history is the certificate :mod:`g6.graph_reasoning.checks` checks; this module shares no matching code with it.
"""
from __future__ import annotations

import math
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from fractions import Fraction

from g6.graph_reasoning.envelope import INPUT_TOO_LARGE, TIME_LIMIT, TOO_MANY_GROUNDINGS, Refusal
from g6.graph_reasoning.language import UNKNOWN, Atom, Interval, Model, Rule

#: The most groundings (assignments of a rule's variables to nodes that fit the edges) G6 evaluates in one call, over
#: every rule. Read at call time, so a test can lower it.
MAX_GROUNDINGS = 1_000_000
#: A safety net only: the fixpoint is finite, and the time limit stops it first.
MAX_ROUNDS = 100_000
_TICKS = 2048

Binding = tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Step:
    """One tightening of one atom. ``rule`` is ``None`` for a fact (then ``where`` says where it was written)."""

    rank: int
    value: Interval
    rule: int | None = None
    head: Binding = ()
    witnesses: tuple[Binding, ...] = ()
    sources: tuple[tuple[Atom, Interval], ...] = ()
    where: tuple[str, ...] = ()


@dataclass(frozen=True)
class Conflict:
    """A step that would have left ``atom`` empty: its interval ``before`` and the ``step``'s value share nothing."""

    atom: Atom
    before: Interval
    step: Step


@dataclass
class Solution:
    """The interpretation (every atom whose interval was set), each atom's history, the rounds run, the groundings
    evaluated, and the conflict that stopped the engine, if any."""

    bounds: dict[Atom, Interval]
    history: dict[Atom, list[Step]]
    rounds: int = 0
    groundings: int = 0
    conflict: Conflict | None = None
    strata: list[dict] = field(default_factory=list)


class _Clock:
    def __init__(self, deadline: float | None) -> None:
        self.deadline = deadline
        self.ticks = 0
        self.tick(force=True)

    def tick(self, force: bool = False) -> None:
        self.ticks += 1
        if (force or self.ticks % _TICKS == 0) and self.deadline is not None and time.monotonic() > self.deadline:
            raise Refusal(TIME_LIMIT, "the engine did not reach its fixpoint within the time limit")


class _Graph:
    def __init__(self, model: Model) -> None:
        self.nodes = model.nodes
        self.edges = model.edges
        self.edge_set = set(model.edges)
        self.out: dict[str, list[str]] = {}
        self.into: dict[str, list[str]] = {}
        for source, target in model.edges:
            self.out.setdefault(source, []).append(target)
            self.into.setdefault(target, []).append(source)


def _structure(graph: _Graph, rule: Rule, room: int, clock: _Clock) -> list[tuple[str, ...]]:
    """Every grounding of ``rule`` that fits the graph, as node tuples in the order of ``rule.body_variables``: joined
    clause by clause over the edge clauses, then every node for each variable only node clauses use, then (for an
    edge rule) only those whose head is an edge. Refused past ``room`` groundings."""
    partial: list[dict[str, str]] = [{}]

    def keep(found: list[dict[str, str]]) -> list[dict[str, str]]:
        if len(found) > room:
            raise Refusal(TOO_MANY_GROUNDINGS, f"the rule at line {rule.line} has more than {room} groundings left "
                                               f"of the {MAX_GROUNDINGS} G6 evaluates in one call")
        return found

    for clause in rule.clauses:
        if len(clause.variables) != 2:
            continue
        u, v = clause.variables
        joined: list[dict[str, str]] = []
        for binding in partial:
            clock.tick()
            if u in binding and v in binding:
                if (binding[u], binding[v]) in graph.edge_set:
                    joined.append(binding)
            elif u in binding:
                joined += [{**binding, v: t} for t in graph.out.get(binding[u], ())]
            elif v in binding:
                joined += [{**binding, u: s} for s in graph.into.get(binding[v], ())]
            elif u == v:
                joined += [{**binding, u: s} for s, t in graph.edges if s == t]
            else:
                joined += [{**binding, u: s, v: t} for s, t in graph.edges]
            keep(joined)
        partial = joined
    for variable in rule.body_variables:
        if partial and variable not in partial[0]:
            partial = keep([{**binding, variable: n} for binding in partial for n in graph.nodes])
    if rule.kind == "edge":
        x, y = rule.variables
        partial = [b for b in partial if (b[x], b[y]) in graph.edge_set]
    order = rule.body_variables
    return [tuple(b[v] for v in order) for b in partial]


def _within(interval: Interval, bound: Interval) -> bool:
    return bound[0] <= interval[0] and interval[1] <= bound[1]


def annotate(function: str, intervals: list[Interval]) -> Interval:
    """The annotation function ``function`` over ``intervals`` (every qualified atom of every clause), exactly."""
    lowers, uppers = [i[0] for i in intervals], [i[1] for i in intervals]
    if function == "average":
        return sum(lowers, Fraction(0)) / len(lowers), sum(uppers, Fraction(0)) / len(uppers)
    if function == "average_lower":
        return sum(lowers, Fraction(0)) / len(lowers), max(uppers)
    if function == "maximum":
        return max(lowers), max(uppers)
    if function == "minimum":
        return min(lowers), min(uppers)
    if function == "product":
        return math.prod(lowers, start=Fraction(1)), math.prod(uppers, start=Fraction(1))
    raise ValueError(f"unknown annotation function {function!r}")


@dataclass
class _Groups:
    """A rule's groundings grouped by head grounding, and the head groundings that read each atom."""

    clause_at: list[tuple[int, ...]]
    by_head: dict[tuple[str, ...], list[tuple[str, ...]]]
    readers: dict[Atom, set[tuple[str, ...]]]

    def atoms(self, rule: Rule, grounding: tuple[str, ...]) -> list[Atom]:
        return [(c.label, tuple(grounding[p] for p in where)) for c, where in zip(rule.clauses, self.clause_at)]


def _group(rule: Rule, structure: list[tuple[str, ...]], clock: _Clock) -> _Groups:
    at = {v: i for i, v in enumerate(rule.body_variables)}
    head_at = [at[v] for v in rule.variables]
    groups = _Groups([tuple(at[v] for v in c.variables) for c in rule.clauses], {}, {})
    for grounding in structure:
        clock.tick()
        head = tuple(grounding[p] for p in head_at)
        groups.by_head.setdefault(head, []).append(grounding)
        for atom in groups.atoms(rule, grounding):
            groups.readers.setdefault(atom, set()).add(head)
    return groups


def _fire(rule_index: int, rule: Rule, groups: _Groups, heads: list[tuple[str, ...]], bounds: dict[Atom, Interval],
          rank: int, clock: _Clock) -> Iterator[tuple[Atom, Step]]:
    """Every firing of ``rule`` under ``bounds`` among ``heads`` (in order): one step per head grounding whose clauses
    all meet their thresholds."""
    order = rule.body_variables
    for head in heads:
        entries = []
        for grounding in groups.by_head[head]:
            clock.tick()
            atoms = groups.atoms(rule, grounding)
            entries.append((grounding, atoms, [_within(bounds.get(a, UNKNOWN), c.bound)
                                               for a, c in zip(atoms, rule.clauses)]))
        full = [e for e in entries if all(e[2])]
        if not full:
            continue
        need: list[int] = []
        for i, clause in enumerate(rule.clauses):
            qualified = {e[1][i] for e in full}
            if clause.at_least is None:
                need.append(1)
            elif not clause.percent:
                need.append(int(clause.at_least))
            else:
                candidates = {e[1][i] for e in entries if all(h for j, h in enumerate(e[2]) if j != i)}
                need.append(math.ceil(clause.at_least * len(candidates) / 100))
            if len(qualified) < need[-1]:
                break
        else:
            if rule.function is not None:
                chosen = full
                intervals = [bounds.get(atom, UNKNOWN) for i in range(len(rule.clauses))
                             for atom in dict.fromkeys(e[1][i] for e in full)]
                value = annotate(rule.function, intervals)
            else:
                chosen = _witnesses(full, need)
                value = rule.bound
            sources = tuple((a, bounds.get(a, UNKNOWN)) for a in dict.fromkeys(a for e in chosen for a in e[1]))
            witnesses = tuple(tuple(zip(order, e[0])) for e in chosen)
            yield (rule.head, head), Step(rank, value, rule_index, tuple(zip(rule.variables, head)), witnesses,
                                          sources)


def _witnesses(full: list, need: list[int]) -> list:
    """The fewest groundings, in order, that give each clause as many distinct qualified atoms as it needs."""
    chosen = [full[0]]
    for i, wanted in enumerate(need):
        have = {e[1][i] for e in chosen}
        for entry in full:
            if len(have) >= wanted:
                break
            if entry[1][i] not in have:      # an entry already chosen has its atoms in have, so none repeats
                chosen.append(entry)
                have.add(entry[1][i])
    return chosen


def solve(model: Model, *, deadline: float | None = None) -> Solution:
    """The fixpoint of ``model``, with each atom's history; stops at the first conflict. ``deadline`` is a
    :func:`time.monotonic` time."""
    clock = _Clock(deadline)
    bounds: dict[Atom, Interval] = dict(model.facts)
    history: dict[Atom, list[Step]] = {atom: [Step(0, value, where=model.fact_where[atom])]
                                       for atom, value in model.facts.items()}
    graph = _Graph(model)
    grouped: list[_Groups] = []
    used = 0
    for rule in model.rules:
        structure = _structure(graph, rule, MAX_GROUNDINGS - used, clock)
        used += len(structure)
        grouped.append(_group(rule, structure, clock))
    solution = Solution(bounds, history, 0, used)
    rank = 0
    for level in model.order:
        labels = set(level)
        members = [i for i, rule in enumerate(model.rules) if rule.head in labels]
        if not members:
            continue
        changed: set[Atom] | None = None      # None: the stratum's first round, which evaluates every head grounding
        first = rank + 1
        while True:
            rank += 1
            if rank > MAX_ROUNDS:
                raise Refusal(INPUT_TOO_LARGE, f"the fixpoint ran past {MAX_ROUNDS} rounds")
            steps: list[tuple[Atom, Step]] = []
            for i in members:
                groups = grouped[i]
                # a head grounding none of whose atoms changed last round gives the step it gave then
                heads = sorted(groups.by_head) if changed is None else sorted(
                    {h for atom in changed for h in groups.readers.get(atom, ())})
                steps += _fire(i, model.rules[i], groups, heads, bounds, rank, clock)
            changed = set()
            for atom, step in steps:
                before = bounds.get(atom, UNKNOWN)
                after = (max(before[0], step.value[0]), min(before[1], step.value[1]))
                if after[0] > after[1]:
                    solution.rounds = rank
                    solution.conflict = Conflict(atom, before, step)
                    return solution
                if after != before:
                    bounds[atom] = after
                    history.setdefault(atom, []).append(step)
                    changed.add(atom)
            if not changed:
                break
        solution.strata.append({"labels": list(level), "rounds": [first, rank]})
    solution.rounds = rank
    return solution


__all__ = ["MAX_GROUNDINGS", "MAX_ROUNDS", "Conflict", "Solution", "Step", "annotate", "solve"]
