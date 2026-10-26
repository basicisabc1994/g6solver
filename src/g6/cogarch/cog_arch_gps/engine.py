# SPDX-License-Identifier: Apache-2.0
"""Means-ends analysis over STRIPS operators, and the replay that checks a plan against them.

:func:`search` is the General Problem Solver's means-ends analysis (Newell and Simon; in the form of Norvig's
*Paradigms of Artificial Intelligence Programming*, chapter 4): to reach a goal, pick an operator that adds it,
reach that operator's preconditions first, then apply it. Two of that chapter's corrections are kept, which the
archive's engine lacked (GPS-02): every conjunction of goals is checked again once each has been reached, so a goal
undone by a later step fails the conjunction instead of passing it; and a conjunction that fails is tried again in
other orders, the order given first. Goals are an ordered tuple, never a set, so the same problem gives the same plan
in every process (GPS-03). Every search is bounded by depth, nodes, goal orders and time, and a cap reached is
raised as :class:`Stopped` naming it.

:func:`replay` is the check the search does not control: it applies the plan step by step to the initial state using
only the declared operators, and says at which step a precondition failed, which operator is unknown, or which goals
do not hold at the end and which step last deleted each.
"""
from __future__ import annotations

import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import islice, permutations
from typing import Any

#: The clock the time limit reads (a test may replace it).
_clock = time.monotonic


@dataclass(frozen=True)
class Operator:
    """One STRIPS operator: what must hold before it, what it adds and what it deletes."""

    name: str
    preconditions: tuple[str, ...] = ()
    add_list: tuple[str, ...] = ()
    delete_list: tuple[str, ...] = ()


def operators(items: Iterable[Mapping[str, Any] | Any]) -> tuple[Operator, ...]:
    """Operators from dicts (or objects) with ``name``, ``preconditions``, ``add_list`` and ``delete_list``."""
    out = []
    for item in items:
        get = item.get if isinstance(item, Mapping) else (lambda key, default=(), it=item: getattr(it, key, default))
        out.append(Operator(str(get("name", "")), tuple(get("preconditions", ())), tuple(get("add_list", ())),
                            tuple(get("delete_list", ()))))
    return tuple(out)


class Stopped(Exception):  # noqa: N818 - a stop, not an error: the cap is the answer
    """The search reached one of its caps."""

    def __init__(self, cap: str, limit: float, nodes: int) -> None:
        super().__init__(f"{cap} {limit:g} reached")
        self.cap, self.limit, self.nodes = cap, limit, nodes


@dataclass(frozen=True)
class Found:
    """What a search returns: a plan and the state it leads to (``None`` for both when it found none), with how much
    it searched and whether any cap cut a branch short."""

    plan: tuple[str, ...] | None
    state: frozenset[str] | None
    nodes: int
    orders_tried: int
    cut: str = ""


@dataclass
class _Search:
    operators: tuple[Operator, ...]
    max_depth: int
    max_nodes: int
    max_orders: int
    deadline: float
    time_limit: float
    nodes: int = 0
    orders_tried: int = 0
    cut: str = ""
    _adders: dict[str, list[Operator]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for op in self.operators:
            for fact in op.add_list:
                self._adders.setdefault(fact, []).append(op)

    def _tick(self) -> None:
        self.nodes += 1
        if self.nodes > self.max_nodes:
            raise Stopped("max_nodes", self.max_nodes, self.nodes)
        if _clock() > self.deadline:
            raise Stopped("time_limit_s", self.time_limit, self.nodes)

    def achieve_all(self, state: frozenset[str], goals: tuple[str, ...], stack: tuple[str, ...],
                    depth: int) -> tuple[frozenset[str], tuple[str, ...]] | None:
        """Reach every goal, then check every goal still holds (the final check); try other orders if not."""
        orders = _orders(goals)
        for order in islice(orders, self.max_orders):
            if depth == 0:
                self.orders_tried += 1
            got = self._each(state, order, stack, depth)
            if got is not None and all(g in got[0] for g in goals):
                return got
        if next(orders, None) is not None:
            self.cut = self.cut or "max_orders"
        return None

    def _each(self, state: frozenset[str], goals: tuple[str, ...], stack: tuple[str, ...],
              depth: int) -> tuple[frozenset[str], tuple[str, ...]] | None:
        plan: tuple[str, ...] = ()
        for goal in goals:
            got = self.achieve(state, goal, stack, depth)
            if got is None:
                return None
            state, plan = got[0], plan + got[1]
        return state, plan

    def achieve(self, state: frozenset[str], goal: str, stack: tuple[str, ...],
                depth: int) -> tuple[frozenset[str], tuple[str, ...]] | None:
        """Reach one goal: an operator that adds it, its preconditions reached first (best first by how many hold)."""
        self._tick()
        if goal in state:
            return state, ()
        if goal in stack:        # a circular goal: this branch fails, the next operator is tried
            return None
        if depth >= self.max_depth:
            self.cut = self.cut or "max_depth"
            return None
        candidates = sorted(self._adders.get(goal, ()), key=lambda op: -sum(p in state for p in op.preconditions))
        for op in candidates:
            got = self.achieve_all(state, op.preconditions, (*stack, goal), depth + 1)
            if got is None or not all(p in got[0] for p in op.preconditions):
                continue
            after = (got[0] - set(op.delete_list)) | set(op.add_list)
            return frozenset(after), (*got[1], op.name)
        return None


def _orders(goals: tuple[str, ...]) -> Iterator[tuple[str, ...]]:
    """The goals in the order given, then every other order (lazily: never more than are asked for)."""
    return permutations(goals) if len(goals) > 1 else iter((goals,))


def search(initial: Iterable[str], goals: Sequence[str], ops: Sequence[Operator], *, max_depth: int = 50,
           max_nodes: int = 20_000, max_orders: int = 24, time_limit_s: float = 10.0) -> Found:
    """A plan that reaches every goal in ``goals`` from ``initial`` with ``ops``, by means-ends analysis.

    Raises :class:`Stopped` when ``max_nodes`` or ``time_limit_s`` is reached; a branch cut at ``max_depth`` or a
    conjunction with more orders than ``max_orders`` is named in :attr:`Found.cut` when no plan was found."""
    run = _Search(tuple(ops), max_depth, max_nodes, max_orders, _clock() + time_limit_s, time_limit_s)
    got = run.achieve_all(frozenset(initial), tuple(dict.fromkeys(goals)), (), 0)
    if got is None:
        return Found(None, None, run.nodes, run.orders_tried, run.cut)
    return Found(got[1], got[0], run.nodes, run.orders_tried)


@dataclass(frozen=True)
class Replay:
    """A plan applied step by step to the initial state with the declared operators."""

    ok: bool
    steps: tuple[dict[str, Any], ...]
    final_state: tuple[str, ...]
    failed_at: int | None = None
    reason: str = ""
    missing: tuple[str, ...] = ()
    unknown_operator: str = ""
    goals_missing: tuple[str, ...] = ()
    clobbered_by: Mapping[str, int] = field(default_factory=dict)


def replay(initial: Iterable[str], goals: Sequence[str], ops: Sequence[Operator], plan: Sequence[str]) -> Replay:
    """Apply ``plan`` to ``initial``: each step's operator must be declared and its preconditions must hold; at the end
    every goal must hold. Independent of :func:`search`: it reads only the operators and the plan."""
    by_name = {op.name: op for op in ops}
    state = set(initial)
    steps: list[dict[str, Any]] = []
    last_deleted: dict[str, int] = {}
    for index, name in enumerate(plan, start=1):
        op = by_name.get(name)
        if op is None:
            return Replay(False, tuple(steps), tuple(sorted(state)), index,
                          f"step {index}: {name!r} is not one of the declared operators", unknown_operator=name)
        missing = tuple(p for p in op.preconditions if p not in state)
        if missing:
            return Replay(False, tuple(steps), tuple(sorted(state)), index,
                          f"step {index}: {name} needs {', '.join(missing)}, which does not hold", missing=missing)
        deleted = sorted(f for f in set(op.delete_list) if f in state and f not in op.add_list)
        added = sorted(f for f in set(op.add_list) if f not in state)
        state = (state - set(op.delete_list)) | set(op.add_list)
        for fact in deleted:
            last_deleted[fact] = index
        steps.append({"step": index, "operator": name, "added": added, "deleted": deleted})
    unmet = tuple(g for g in dict.fromkeys(goals) if g not in state)
    if unmet:
        clobbered = {g: last_deleted[g] for g in unmet if g in last_deleted}
        return Replay(False, tuple(steps), tuple(sorted(state)), max(clobbered.values()) if clobbered else None,
                      f"the plan ends without {', '.join(unmet)}", goals_missing=unmet, clobbered_by=clobbered)
    return Replay(True, tuple(steps), tuple(sorted(state)))


__all__ = ["Found", "Operator", "Replay", "Stopped", "operators", "replay", "search"]
