# SPDX-License-Identifier: Apache-2.0
"""G6's own check of a graph-reasoning interpretation, in pure Python, run before any answer is reported.

It takes the model, the interpretation exactly as it will be reported (each atom's interval) and each atom's history
of steps, and checks three things, with its own matcher (variable by variable, where the engine joins clause by clause)
and its own annotation functions, sharing no code with the engine that built the answer:

* **closure**: applying every rule once more to the interpretation tightens nothing. For every head grounding where a
  rule fires, the head's interval already lies within the rule's interval. A missing tightening fails here;
* **support**: every interval other than the unknown ``[0, 1]`` is the intersection of its history, and every step of
  the history is a fact the model has, or a rule application whose witnesses fit the graph, whose clauses held at the
  intervals the step recorded, whose thresholds those witnesses meet, and whose interval is the rule's (for an
  annotation function: computed from every qualified atom, at their final intervals, since those labels are final
  first). Each recorded interval must have been established by steps of a strictly lower rank, so no interval can
  rest on itself, directly or in a circle. An extra or circular interval fails here;
* **soundness**: every interval is within ``[0, 1]`` and not empty, and every atom is a label of the model on a node
  or an edge of the graph.

Together they show the interpretation is the model's least fixpoint: support builds every interval from the facts
(induction on rank), so none is tighter than the rules force, and closure shows the rules force nothing more. A
conflict is checked the same way (:func:`check_conflict`): the atom's interval is its history's intersection, the step
is a valid application, and the two have nothing in common.
"""
from __future__ import annotations

import bisect
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from g6.graph_reasoning.envelope import TIME_LIMIT, Refusal
from g6.graph_reasoning.language import Model, Rule, render_atom, show_interval

CHECKED = ("closure", "support", "soundness")
_SHOWN = 5
_TICKS = 2048
_FULL = (Fraction(0), Fraction(1))


@dataclass(frozen=True)
class Check:
    """What G6 checked of one interpretation, and what it found. ``passed`` only when no failure was found."""

    checked: tuple[str, ...]
    failures: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.failures

    def as_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "checked": list(self.checked), "failures": list(self.failures),
                "details": self.details}


class _Clock:
    def __init__(self, deadline: float | None) -> None:
        self.deadline = deadline
        self.ticks = 0
        self.tick(force=True)

    def tick(self, force: bool = False) -> None:
        self.ticks += 1
        if (force or self.ticks % _TICKS == 0) and self.deadline is not None and time.monotonic() > self.deadline:
            raise Refusal(TIME_LIMIT, "G6's check of the interpretation did not finish within the time limit")


def _inside(inner: tuple, outer: tuple) -> bool:
    """``inner`` lies within ``outer``."""
    return outer[0] <= inner[0] and inner[1] <= outer[1]


def _meet(a: tuple, b: tuple) -> tuple:
    return (a[0] if a[0] >= b[0] else b[0], a[1] if a[1] <= b[1] else b[1])


def _value(function: str, bounds: list[tuple]) -> tuple:
    """The annotation function over ``bounds``, written again here so the check does not reuse the engine's."""
    count = Fraction(len(bounds))
    if function == "average":
        return Fraction(sum(b[0] for b in bounds)) / count, Fraction(sum(b[1] for b in bounds)) / count
    if function == "average_lower":
        return Fraction(sum(b[0] for b in bounds)) / count, sorted(b[1] for b in bounds)[-1]
    if function == "maximum":
        return sorted(b[0] for b in bounds)[-1], sorted(b[1] for b in bounds)[-1]
    if function == "minimum":
        return sorted(b[0] for b in bounds)[0], sorted(b[1] for b in bounds)[0]
    if function == "product":
        low, high = Fraction(1), Fraction(1)
        for b in bounds:
            low, high = low * b[0], high * b[1]
        return low, high
    raise ValueError(function)


class _Matcher:
    """Groundings of a rule, one variable at a time: the head's variables first, then each variable reachable by an
    edge clause from one already bound (its candidates the neighbours of every bound end), then any other over every
    node."""

    def __init__(self, model: Model, clock: _Clock) -> None:
        self.nodes = model.nodes
        self.edges = set(model.edges)
        self.succ: dict[str, set[str]] = {}
        self.pred: dict[str, set[str]] = {}
        for s, t in model.edges:
            self.succ.setdefault(s, set()).add(t)
            self.pred.setdefault(t, set()).add(s)
        self.clock = clock

    def links(self, rule: Rule) -> list[tuple[str, str]]:
        found = [c.variables for c in rule.clauses if len(c.variables) == 2]
        if rule.kind == "edge":
            found.append(rule.variables)
        return found

    def order(self, rule: Rule) -> list[str]:
        variables = list(dict.fromkeys([*rule.variables, *(v for c in rule.clauses for v in c.variables)]))
        links = self.links(rule)
        placed: list[str] = []
        while len(placed) < len(variables):
            rest = [v for v in variables if v not in placed]
            if len(placed) < len(set(rule.variables)):
                pick = next(v for v in rule.variables if v not in placed)
            else:
                near = [v for v in rest if any((a == v and b in placed) or (b == v and a in placed) for a, b in links)]
                pick = near[0] if near else sorted(rest)[0]
            placed.append(pick)
        return placed

    def candidates(self, var: str, binding: dict[str, str], links: list[tuple[str, str]]) -> Any:
        pool: set[str] | None = None
        for a, b in links:
            if a == var and b in binding and b != var:
                pool = self.pred.get(binding[b], set()) if pool is None else pool & self.pred.get(binding[b], set())
            elif b == var and a in binding and a != var:
                pool = self.succ.get(binding[a], set()) if pool is None else pool & self.succ.get(binding[a], set())
        return sorted(pool) if pool is not None else self.nodes

    def groundings(self, rule: Rule) -> Iterator[dict[str, str]]:
        order, links = self.order(rule), self.links(rule)
        binding: dict[str, str] = {}

        def extend(k: int) -> Iterator[dict[str, str]]:
            if k == len(order):
                if all((binding[a], binding[b]) in self.edges for a, b in links):
                    yield dict(binding)
                return
            var = order[k]
            for node in self.candidates(var, binding, links):
                self.clock.tick()
                binding[var] = node
                yield from extend(k + 1)
            binding.pop(var, None)

        yield from extend(0)


@dataclass
class _Head:
    """What the check found for one head grounding of one rule under the interpretation."""

    groundings: list[dict[str, str]]
    atoms: list[list[tuple]]
    held: list[list[bool]]

    def qualified(self, i: int) -> list[tuple]:
        return list(dict.fromkeys(a[i] for a, h in zip(self.atoms, self.held) if all(h)))

    def candidates(self, i: int) -> list[tuple]:
        return list(dict.fromkeys(a[i] for a, h in zip(self.atoms, self.held)
                                  if all(x for j, x in enumerate(h) if j != i)))


def _heads(matcher: _Matcher, rule: Rule, bound_of: Any) -> dict[tuple, _Head]:
    found: dict[tuple, _Head] = {}
    for g in matcher.groundings(rule):
        atoms = [(c.label, tuple(g[v] for v in c.variables)) for c in rule.clauses]
        held = [_inside(bound_of(a), c.bound) for a, c in zip(atoms, rule.clauses)]
        entry = found.setdefault(tuple(g[v] for v in rule.variables), _Head([], [], []))
        entry.groundings.append(g)
        entry.atoms.append(atoms)
        entry.held.append(held)
    return found


def _fires(rule: Rule, head: _Head) -> bool:
    for i, clause in enumerate(rule.clauses):
        qualified = len(head.qualified(i))
        if clause.at_least is None:
            if qualified < 1:
                return False
        elif not clause.percent:
            if qualified < clause.at_least:
                return False
        elif qualified * 100 < clause.at_least * len(head.candidates(i)) or qualified < 1:
            return False
    return True


def _rule_value(rule: Rule, head: _Head, bound_of: Any) -> tuple:
    if rule.function is None:
        return rule.bound
    return _value(rule.function, [bound_of(a) for i in range(len(rule.clauses)) for a in head.qualified(i)])


class _Session:
    def __init__(self, model: Model, bounds: Mapping, history: Mapping, deadline: float | None) -> None:
        self.model = model
        self.bounds = bounds
        self.history = history
        self.clock = _Clock(deadline)
        self.matcher = _Matcher(model, self.clock)
        self.found: dict[str, list[str]] = {kind: [] for kind in CHECKED}
        self.nodes = set(model.nodes)
        self.edges = set(model.edges)
        self.heads_cache: dict[int, dict[tuple, _Head]] = {}
        self.as_of: dict[tuple, tuple[list[int], list[tuple]]] = {}
        for atom, steps in history.items():
            ranks, running, current = [], [], _FULL
            for step in steps:
                current = _meet(current, step.value)
                ranks.append(step.rank)
                running.append(current)
            self.as_of[atom] = (ranks, running)

    def bound_of(self, atom: tuple) -> tuple:
        return self.bounds.get(atom, _FULL)

    def before(self, atom: tuple, rank: int) -> tuple:
        """The atom's interval from the steps of rank below ``rank``."""
        ranks, running = self.as_of.get(atom, ([], []))
        k = bisect.bisect_left(ranks, rank)
        return running[k - 1] if k else _FULL

    def heads(self, index: int) -> dict[tuple, _Head]:
        if index not in self.heads_cache:
            self.heads_cache[index] = _heads(self.matcher, self.model.rules[index], self.bound_of)
        return self.heads_cache[index]

    def fail(self, kind: str, message: str) -> None:
        self.found[kind].append(message)

    # --- soundness ----------------------------------------------------------------------------------------------------

    def soundness(self) -> None:
        for atom, bound in self.bounds.items():
            self.clock.tick()
            shown = render_atom(atom) if isinstance(atom, tuple) and len(atom) == 2 else repr(atom)
            label, component = atom
            if label not in self.model.arities:
                self.fail("soundness", f"{shown} is not a label of the model")
            elif len(component) != self.model.arities[label]:
                self.fail("soundness", f"{shown} has {len(component)} arguments, not {self.model.arities[label]}")
            elif len(component) == 1 and component[0] not in self.nodes:
                self.fail("soundness", f"{shown}: {component[0]} is not a node of the graph")
            elif len(component) == 2 and tuple(component) not in self.edges:
                self.fail("soundness", f"{shown}: {component[0]} -> {component[1]} is not an edge of the graph")
            low, high = bound
            if not (0 <= low <= 1 and 0 <= high <= 1):
                self.fail("soundness", f"{shown} is {show_interval(bound)}, outside [0, 1]")
            elif low > high:
                self.fail("soundness", f"{shown} is {show_interval(bound)}, which is empty")

    # --- closure ------------------------------------------------------------------------------------------------------

    def closure(self) -> None:
        for atom, value in self.model.facts.items():
            if not _inside(self.bound_of(atom), value):
                self.fail("closure", f"the fact {render_atom(atom)} : {show_interval(value)} is not within the "
                                     f"interpretation's {show_interval(self.bound_of(atom))}")
        for index, rule in enumerate(self.model.rules):
            for head, entry in self.heads(index).items():
                if not _fires(rule, entry):
                    continue
                value = _rule_value(rule, entry, self.bound_of)
                atom = (rule.head, head)
                if not _inside(self.bound_of(atom), value):
                    self.fail("closure", f"rule {index + 1} (line {rule.line}) fires for {render_atom(atom)} with "
                                         f"{show_interval(value)}, which would tighten its "
                                         f"{show_interval(self.bound_of(atom))}")

    # --- support ------------------------------------------------------------------------------------------------------

    def support(self) -> int:
        applications = 0
        for atom, bound in self.bounds.items():
            self.clock.tick()
            shown = render_atom(atom)
            steps = self.history.get(atom, [])
            if not steps:
                if bound != _FULL:
                    self.fail("support", f"{shown} is {show_interval(bound)} but has no step that gives it")
                continue
            met = _FULL
            for step in steps:
                met = _meet(met, step.value)
            if met != bound:
                self.fail("support", f"{shown} is {show_interval(bound)}, but its history gives {show_interval(met)}")
            last = -1
            for step in steps:
                if step.rank < last:
                    self.fail("support", f"{shown}: its history is not in rank order")
                last = step.rank
                if step.rule is None:
                    self.fact_step(atom, step)
                else:
                    applications += 1
                    self.rule_step(atom, step)
        for atom in self.history:
            if atom not in self.bounds:
                self.fail("support", f"{render_atom(atom)} has a history but no interval")
        return applications

    def fact_step(self, atom: tuple, step: Any) -> None:
        if step.rank != 0:
            self.fail("support", f"{render_atom(atom)}: a fact is given at rank 0, not {step.rank}")
        if self.model.facts.get(atom) != step.value:
            self.fail("support", f"{render_atom(atom)} is given as the fact {show_interval(step.value)}, but the model "
                                 "has no such fact")

    def rule_step(self, atom: tuple, step: Any) -> None:
        shown = render_atom(atom)
        if not isinstance(step.rule, int) or not 0 <= step.rule < len(self.model.rules):
            self.fail("support", f"{shown} names rule {step.rule!r}, which the model does not have")
            return
        rule = self.model.rules[step.rule]
        where = f"rule {step.rule + 1} (line {rule.line})"
        head = dict(step.head)
        if rule.head != atom[0] or set(head) != set(rule.variables) or \
                tuple(head[v] for v in rule.variables) != tuple(atom[1]):
            self.fail("support", f"{shown}: {where} under the head bindings {head} does not give {shown}")
            return
        if rule.kind == "edge" and tuple(atom[1]) not in self.edges:
            self.fail("support", f"{shown}: {where} labels an edge the graph does not have")
            return
        if not step.witnesses:
            self.fail("support", f"{shown}: {where} records no grounding its body held under")
            return
        recorded = dict(step.sources)
        clause_atoms: list[list[tuple]] = [[] for _ in rule.clauses]
        for witness in step.witnesses:
            g = dict(witness)
            if set(g) != set(rule.body_variables) or any(g[v] != head[v] for v in rule.variables):
                self.fail("support", f"{shown}: {where} records a grounding {g} that does not bind its variables to "
                                     "the head")
                return
            for i, clause in enumerate(rule.clauses):
                used = (clause.label, tuple(g[v] for v in clause.variables))
                if len(used[1]) == 2 and used[1] not in self.edges:
                    self.fail("support", f"{shown}: {where} reads {render_atom(used)}, but {used[1][0]} -> "
                                         f"{used[1][1]} is not an edge of the graph")
                    return
                if used not in recorded:
                    self.fail("support", f"{shown}: {where} reads {render_atom(used)} but records no interval for it")
                    return
                if not _inside(recorded[used], clause.bound):
                    self.fail("support", f"{shown}: {where} needs {render_atom(used)} within "
                                         f"{show_interval(clause.bound)}, but it was {show_interval(recorded[used])}, "
                                         "so its body did not hold")
                if used not in clause_atoms[i]:
                    clause_atoms[i].append(used)
        for used, interval in recorded.items():
            earlier = self.before(used, step.rank)
            if not _inside(earlier, interval):
                self.fail("support", f"{shown}: {where} at rank {step.rank} reads {render_atom(used)} at "
                                     f"{show_interval(interval)}, which nothing established before rank {step.rank} "
                                     f"(it was {show_interval(earlier)})")
        self.thresholds(atom, rule, where, clause_atoms, step)
        self.rule_value(atom, rule, where, clause_atoms, recorded, step)

    def thresholds(self, atom: tuple, rule: Rule, where: str, clause_atoms: list, step: Any) -> None:
        entry = None
        for i, clause in enumerate(rule.clauses):
            if clause.at_least is None:
                continue
            have = len(clause_atoms[i])
            if not clause.percent:
                if have < clause.at_least:
                    self.fail("support", f"{render_atom(atom)}: {where} needs at least {int(clause.at_least)} "
                                         f"qualified {clause.label} atoms, and its groundings give {have}")
                continue
            entry = entry or self.heads(step.rule).get(tuple(atom[1]))
            total = len(entry.candidates(i)) if entry else 0
            if have * 100 < clause.at_least * total:
                self.fail("support", f"{render_atom(atom)}: {where} needs at least {clause.at_least}% of the {total} "
                                     f"candidate {clause.label} atoms, and its groundings give {have}")

    def rule_value(self, atom: tuple, rule: Rule, where: str, clause_atoms: list, recorded: dict, step: Any) -> None:
        if rule.function is None:
            if step.value != rule.bound:
                self.fail("support", f"{render_atom(atom)}: {where} gives {show_interval(rule.bound)}, not "
                                     f"{show_interval(step.value)}")
            return
        for clause in rule.clauses:
            if not self.model.strata[clause.label] < self.model.strata[rule.head]:
                self.fail("support", f"{render_atom(atom)}: {where} reads {clause.label}, which is not final before "
                                     f"{rule.head}")
        entry = self.heads(step.rule).get(tuple(atom[1]))
        complete = [entry.qualified(i) for i in range(len(rule.clauses))] if entry else []
        if [sorted(found) for found in clause_atoms] != [sorted(found) for found in complete]:
            self.fail("support", f"{render_atom(atom)}: {where} applies {rule.function} to "
                                 f"{sum(len(c) for c in clause_atoms)} atoms, but "
                                 f"{sum(len(c) for c in complete)} qualify once their labels are final")
            return
        for used, interval in recorded.items():
            if interval != self.bound_of(used):
                self.fail("support", f"{render_atom(atom)}: {where} read {render_atom(used)} at "
                                     f"{show_interval(interval)}, not at its final {show_interval(self.bound_of(used))}")
                return
        expected = _value(rule.function, [recorded[a] for found in clause_atoms for a in found])
        if expected != step.value:
            self.fail("support", f"{render_atom(atom)}: {where} gives {show_interval(expected)} by {rule.function}, "
                                 f"not {show_interval(step.value)}")

    def result(self, extra: dict[str, Any]) -> Check:
        failures = [f"{kind}: {message}" for kind in CHECKED for message in self.found[kind][:_SHOWN]]
        details = {"atoms": len(self.bounds), "rules": len(self.model.rules), **extra}
        details |= {f"{kind}_failures": len(self.found[kind]) for kind in CHECKED if len(self.found[kind]) > _SHOWN}
        return Check(CHECKED, tuple(failures), details)


def check_solution(model: Model, bounds: Mapping, history: Mapping, *, deadline: float | None = None) -> Check:
    """Closure, support and soundness for ``bounds`` (each atom's interval) and ``history`` (each atom's steps, as the
    engine's :class:`~g6.graph_reasoning.engine.Step`). ``deadline`` is a :func:`time.monotonic` time."""
    session = _Session(model, bounds, history, deadline)
    session.soundness()
    session.closure()
    applications = session.support()
    return session.result({"applications": applications})


def check_conflict(model: Model, bounds: Mapping, history: Mapping, conflict: Any, *,
                   deadline: float | None = None) -> Check:
    """The conflict the engine reported is real: the atom's interval is its history's intersection, the step that met
    it is a valid application at intervals established before it, and the two share nothing. The interpretation up to
    the conflict must be supported and sound too; closure is not asked of it, since the fixpoint stopped."""
    session = _Session(model, bounds, history, deadline)
    session.soundness()
    applications = session.support()
    atom, before, step = conflict.atom, tuple(conflict.before), conflict.step
    if session.bound_of(atom) != before:
        session.fail("support", f"{render_atom(atom)}: the conflict starts from {show_interval(before)}, but its "
                                f"history gives {show_interval(session.bound_of(atom))}")
    meet = _meet(before, step.value)
    if meet[0] <= meet[1]:
        session.fail("support", f"{render_atom(atom)}: {show_interval(before)} and {show_interval(step.value)} meet in "
                                f"{show_interval(meet)}, which is not empty, so there is no conflict")
    if step.rule is None:
        session.fact_step(atom, step)
    else:
        session.rule_step(atom, step)
    return session.result({"applications": applications, "conflict": render_atom(atom)})


__all__ = ["CHECKED", "Check", "check_conflict", "check_solution"]
