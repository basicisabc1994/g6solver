# SPDX-License-Identifier: Apache-2.0
"""G6's own check of a Datalog model, in pure Python, run before any answer is reported.

It takes the program, the model exactly as it will be reported and each tuple's support, and checks three things, with
its own matcher and its own comparison order (no code shared with the engine that built the model):

* **closure**: every fact of the program is in the model, and applying every rule once more to the model (each
  negated atom read against the model) adds nothing. A missing tuple fails here;
* **support**: every tuple of the model has a derivation: it is a fact of the program, or its rule, under the bindings
  given, derives exactly it from tuples of the model with a lower rank, its comparisons holding. The ranks make the
  derivations well-founded, so tuples that only support each other fail. An extra tuple fails here;
* **negation**: each negated atom a derivation relies on names a relation of a strictly lower stratum, and is absent
  from the model. A negation read against the wrong facts fails here.

Together they show the model is the program's perfect model, stratum by stratum: support puts every tuple in it
(induction on rank), closure puts it around every tuple the rules derive, and negation reads each lower stratum only
once it is complete (which closure shows). So a tuple absent from a model that passes is not derivable.
"""
from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from g6.datalog.envelope import CERTIFICATE_REFUTED, TIME_LIMIT, Refusal
from g6.datalog.language import Atom, Const, Negation, Program, Rule, render_tuple, render_value

CHECKED = ("closure", "support", "negation")
_SHOWN = 5
_TICKS = 4096


@dataclass(frozen=True)
class Check:
    """What G6 checked of one model, and what it found. ``passed`` only when no failure was found."""

    checked: tuple[str, ...]
    failures: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.failures

    @property
    def code(self) -> str:
        """``G6_E_DATALOG_CERTIFICATE_REFUTED`` when the check failed, else empty."""
        return "" if self.passed else CERTIFICATE_REFUTED

    def as_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "checked": list(self.checked), "failures": list(self.failures),
                "details": self.details}


class _Deadline:
    def __init__(self, deadline: float | None) -> None:
        self.deadline = deadline
        self.ticks = 0
        self.tick(force=True)

    def tick(self, force: bool = False) -> None:
        self.ticks += 1
        if (force or self.ticks % _TICKS == 0) and self.deadline is not None and time.monotonic() > self.deadline:
            raise Refusal(TIME_LIMIT, "G6's check of the model did not finish within the time limit")


def _rank(value: Any) -> tuple[int, Any]:
    return (0, value) if isinstance(value, int) else (1, value)


def _compare(op: str, left: Any, right: Any) -> bool:
    if op == "=":
        return type(left) is type(right) and left == right
    if op == "!=":
        return not (type(left) is type(right) and left == right)
    a, b = _rank(left), _rank(right)
    return a < b if op == "<" else a <= b if op == "<=" else a > b if op == ">" else a >= b


def _ground(terms: tuple, binding: Mapping[str, Any]) -> tuple:
    return tuple(t.value if isinstance(t, Const) else binding[t.name] for t in terms)


class _Lookup:
    def __init__(self, model: Mapping[str, set[tuple]]) -> None:
        self.model = model
        self.cache: dict[tuple[str, tuple[int, ...]], dict[tuple, list[tuple]]] = {}

    def rows(self, relation: str, pattern: list[tuple[int, Any]]) -> Any:
        if not pattern:
            return self.model[relation]
        positions = tuple(p for p, _ in pattern)
        index = self.cache.get((relation, positions))
        if index is None:
            index = {}
            for row in self.model[relation]:
                index.setdefault(tuple(row[p] for p in positions), []).append(row)
            self.cache[(relation, positions)] = index
        return index.get(tuple(v for _, v in pattern), ())


def _matches(rule: Rule, lookup: _Lookup, clock: _Deadline) -> Iterator[dict[str, Any]]:
    """Every binding under which the rule's body holds in the model: its positive atoms joined in the order written,
    then its comparisons and negations tested."""
    atoms = rule.positives

    def join(k: int, binding: dict[str, Any]) -> Iterator[dict[str, Any]]:
        if k == len(atoms):
            yield binding
            return
        atom = atoms[k]
        pattern = [(p, t.value if isinstance(t, Const) else binding[t.name]) for p, t in enumerate(atom.args)
                   if isinstance(t, Const) or t.name in binding]
        for row in lookup.rows(atom.relation, pattern):
            clock.tick()
            extended, ok = dict(binding), True
            for t, value in zip(atom.args, row):
                if isinstance(t, Const):
                    continue
                if t.name in extended and extended[t.name] != value:
                    ok = False
                    break
                extended[t.name] = value
            if ok:
                yield from join(k + 1, extended)

    for binding in join(0, {}):
        if all(_compare(c.op, _ground((c.left,), binding)[0], _ground((c.right,), binding)[0])
               for c in rule.comparisons) and all(_ground(a.args, binding) not in lookup.model[a.relation]
                                                  for a in rule.negations):
            yield binding


def _rule_label(program: Program, index: int) -> str:
    rule = program.rules[index]
    return f"rule {index + 1} (line {rule.line})"


def _variables(rule: Rule) -> set[str]:
    terms = [*rule.head.args]
    for lit in rule.body:
        if isinstance(lit, Atom):
            terms += lit.args
        elif isinstance(lit, Negation):
            terms += lit.atom.args
        else:
            terms += [lit.left, lit.right]
    return {t.name for t in terms if not isinstance(t, Const)}


def check_model(program: Program, relations: Mapping[str, Any], support: Mapping[tuple[str, tuple], Any], *,
                deadline: float | None = None) -> Check:
    """Closure, support and negation for ``relations`` (each relation's tuples) and ``support`` (each tuple's
    :class:`~g6.datalog.seminaive.Justification`). ``deadline`` is a :func:`time.monotonic` time."""
    clock = _Deadline(deadline)
    found: dict[str, list[str]] = {kind: [] for kind in CHECKED}
    for name in sorted(set(relations) - set(program.arities)):
        found["support"].append(f"the model has {name}, which is not a relation of the program")
    model: dict[str, set[tuple]] = {}
    for name, arity in program.arities.items():
        if name not in relations:
            found["closure"].append(f"the model has no {name} relation")
        rows = set(relations.get(name, ()))
        for row in sorted(rows, key=lambda r: [_rank(v) for v in r]):
            if len(row) != arity:
                found["support"].append(f"{name} has the row {list(row)} of {len(row)} values, not {arity}")
                rows.discard(row)
        model[name] = rows
    lookup = _Lookup(model)
    # closure: the facts are there, and one more application of every rule adds nothing
    for name, rows in program.facts.items():
        for row in rows:
            if row not in model[name]:
                found["closure"].append(f"the fact {render_tuple(name, row)} is missing from the model")
    for i, rule in enumerate(program.rules):
        for binding in _matches(rule, lookup, clock):
            row = _ground(rule.head.args, binding)
            if row not in model[rule.head.relation]:
                found["closure"].append(f"{_rule_label(program, i)} derives {render_tuple(rule.head.relation, row)} "
                                        "from the model, which the model lacks")
    # support and negation: every tuple derives from the facts, through tuples of a lower rank
    derivations = 0
    for name, rows in model.items():
        for row in rows:
            clock.tick()
            shown = render_tuple(name, row)
            step = support.get((name, row))
            if step is None:
                found["support"].append(f"{shown} has no derivation")
                continue
            if step.rule is None:
                if row not in program.facts.get(name, ()):
                    found["support"].append(f"{shown} is given as a fact, but the program has no such fact")
                continue
            derivations += 1
            _check_step(program, model, support, name, row, step, found)
    failures = [f"{kind}: {message}" for kind in CHECKED for message in found[kind][:_SHOWN]]
    details = {"tuples": sum(len(rows) for rows in model.values()), "facts": program.fact_count,
               "rules": len(program.rules), "derivations": derivations}
    details |= {f"{kind}_failures": len(found[kind]) for kind in CHECKED if len(found[kind]) > _SHOWN}
    return Check(CHECKED, tuple(failures), details)


def _check_step(program: Program, model: Mapping[str, set[tuple]], support: Mapping[tuple[str, tuple], Any],
                name: str, row: tuple, step: Any, found: dict[str, list[str]]) -> None:
    shown = render_tuple(name, row)
    if not isinstance(step.rule, int) or not 0 <= step.rule < len(program.rules):
        found["support"].append(f"{shown} names rule {step.rule!r}, which the program does not have")
        return
    rule, label = program.rules[step.rule], _rule_label(program, step.rule)
    binding = dict(step.bindings)
    unbound = sorted(_variables(rule) - set(binding))
    if unbound:
        found["support"].append(f"{shown}: {label} leaves {', '.join(unbound)} unbound")
        return
    head = _ground(rule.head.args, binding)
    if rule.head.relation != name or head != row:
        found["support"].append(f"{shown}: {label} under its bindings derives "
                                f"{render_tuple(rule.head.relation, head)}, not {shown}")
        return
    for atom in rule.positives:
        used = _ground(atom.args, binding)
        if used not in model[atom.relation]:
            found["support"].append(f"{shown}: {label} uses {render_tuple(atom.relation, used)}, which is not in "
                                    "the model")
            continue
        earlier = support.get((atom.relation, used))
        if earlier is not None and not earlier.rank < step.rank:
            found["support"].append(f"{shown}: {label} uses {render_tuple(atom.relation, used)}, which was not "
                                    "derived before it")
    for comparison in rule.comparisons:
        left, right = _ground((comparison.left,), binding)[0], _ground((comparison.right,), binding)[0]
        if not _compare(comparison.op, left, right):
            found["support"].append(f"{shown}: {label} needs {render_value(left)} {comparison.op} "
                                    f"{render_value(right)}, which does not hold")
    for atom in rule.negations:
        negated = _ground(atom.args, binding)
        if not program.strata[atom.relation] < program.strata[name]:
            found["negation"].append(f"{shown}: {label} negates {atom.relation}, which is not in a lower stratum")
        if negated in model[atom.relation]:
            found["negation"].append(f"{shown}: {label} needs not {render_tuple(atom.relation, negated)}, but "
                                     f"{render_tuple(atom.relation, negated)} is in the model")


__all__ = ["CHECKED", "Check", "check_model"]
