# SPDX-License-Identifier: Apache-2.0
"""The graph criteria of g6.causal, exact and in pure Python: nothing here needs DoWhy.

* :func:`d_separated` decides d-separation by moralising the ancestral graph of the nodes involved (Lauritzen's
  criterion), and :func:`open_path` finds a path that the conditioning set leaves open, judged node by node;
* :func:`backdoor_paths` lists every path from the treatment to the outcome that starts with an arrow into the
  treatment, each open or blocked, with what blocks it;
* :func:`adjustment_sets` lists every minimal set of observed variables that meets the backdoor criterion: no
  descendant of the treatment, and every backdoor path blocked. Every subset of the candidates is tried, smallest first;
  the candidates are the observed ancestors of the treatment and outcome that are not descendants of the treatment,
  which loses no minimal set (a minimal separator lies within the ancestors of what it separates);
* :func:`instruments` lists the observed variables that are instruments by the graphical criterion: not a descendant of
  the treatment, d-connected to it, and d-separated from the outcome once the treatment's outgoing edges are removed;
* :func:`frontdoor_sets` lists every minimal set of observed mediators that meets Pearl's frontdoor criterion;
* :func:`identify` says whether the effect is identifiable by one of these routes, and writes out its estimand, or says
  why not. It checks these three criteria only, not the complete do-calculus, and says so.

The set searches are exhaustive, so they refuse more than :data:`MAX_CANDIDATES` candidates, and path listing refuses
more than :data:`MAX_PATHS` paths, each with ``G6_E_CAUSAL_INPUT_TOO_LARGE``; a deadline, when given, is checked as they
go (``G6_E_CAUSAL_TIME_LIMIT``). The functions assume names the caller has checked are in the graph.
"""
from __future__ import annotations

import itertools
import time
from collections.abc import Iterable
from typing import Any

from g6.causal.envelope import INPUT_TOO_LARGE, TIME_LIMIT, Refusal
from g6.causal.graph import CausalGraph

MAX_CANDIDATES = 16
MAX_PATHS = 1_000
MAX_EXPANSIONS = 200_000


def _tick(deadline: float | None) -> None:
    """``deadline`` is a :func:`time.perf_counter` time."""
    if deadline is not None and time.perf_counter() > deadline:
        raise Refusal(TIME_LIMIT, "the graph search did not finish within the time limit")


def d_separated(graph: CausalGraph, xs: Iterable[str], ys: Iterable[str], given: Iterable[str] = ()) -> bool:
    """Whether ``given`` d-separates ``xs`` from ``ys`` in ``graph`` (the three sets disjoint)."""
    xs, ys, given = set(xs), set(ys), set(given)
    keep = xs | ys | given
    keep |= graph.ancestors(keep)
    around: dict[str, set[str]] = {n: set() for n in keep}
    for child in keep:
        parents = sorted(graph.parents(child))  # a kept node's parents are its ancestors, so they are kept
        for parent in parents:
            around[parent].add(child)
            around[child].add(parent)
        for a, b in itertools.combinations(parents, 2):  # marry the parents
            around[a].add(b)
            around[b].add(a)
    seen, stack = set(xs), list(xs)
    while stack:
        for nxt in around[stack.pop()]:
            if nxt in ys:
                return False
            if nxt not in given and nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return True


def _collider(graph: CausalGraph, before: str, node: str, after: str) -> bool:
    return node in graph.children(before) and node in graph.children(after)


def _passes(graph: CausalGraph, before: str, node: str, after: str, given: set[str], opens: set[str]) -> bool:
    """Whether a path passes ``node`` (between ``before`` and ``after``) given ``given``; ``opens`` is ``given`` and its
    ancestors, the nodes that open a collider."""
    if _collider(graph, before, node, after):
        return node in opens
    return node not in given


def path_status(graph: CausalGraph, path: list[str], given: Iterable[str] = ()) -> dict[str, Any]:
    """Whether ``path`` is open given ``given``, its colliders, and the nodes that block it."""
    given = set(given)
    opens = given | graph.ancestors(given)
    colliders, blocked_by = [], []
    for before, node, after in zip(path, path[1:], path[2:]):
        if _collider(graph, before, node, after):
            colliders.append(node)
        if not _passes(graph, before, node, after, given, opens):
            blocked_by.append(node)
    return {"open": not blocked_by, "colliders": colliders, "blocked_by": blocked_by}


def render_path(graph: CausalGraph, path: list[str]) -> str:
    """``path`` written with its arrows, such as ``T <- Z -> Y``."""
    out = [path[0]]
    for a, b in zip(path, path[1:]):
        out.append(f"-> {b}" if b in graph.children(a) else f"<- {b}")
    return " ".join(out)


def _neighbours(graph: CausalGraph, node: str) -> list[str]:
    return sorted(graph.parents(node) | graph.children(node))


def open_path(graph: CausalGraph, xs: Iterable[str], ys: Iterable[str], given: Iterable[str] = (), *,
              deadline: float | None = None) -> list[str] | None:
    """A path from ``xs`` to ``ys`` that ``given`` leaves open, or ``None`` when there is none (or none within
    :data:`MAX_EXPANSIONS` steps of the search, which d-separation then answers alone)."""
    xs, ys, given = set(xs), set(ys), set(given)
    if d_separated(graph, xs, ys, given):
        return None
    opens = given | graph.ancestors(given)
    expansions = 0
    for start in sorted(xs):
        stack: list[list[str]] = [[start]]
        while stack:
            path = stack.pop()
            expansions += 1
            if expansions > MAX_EXPANSIONS:
                return None
            if expansions % 1000 == 0:
                _tick(deadline)
            for nxt in reversed(_neighbours(graph, path[-1])):
                if nxt in path or nxt in xs:
                    continue
                if len(path) >= 2 and not _passes(graph, path[-2], path[-1], nxt, given, opens):
                    continue
                if nxt in ys:
                    return [*path, nxt]
                stack.append([*path, nxt])
    return None


def backdoor_paths(graph: CausalGraph, treatment: str, outcome: str, given: Iterable[str] = (), *,
                   deadline: float | None = None) -> list[dict[str, Any]]:
    """Every path from ``treatment`` to ``outcome`` that starts with an arrow into ``treatment``, shortest first, each
    with whether ``given`` leaves it open."""
    given = set(given)
    found: list[list[str]] = []
    stack = [[treatment, parent] for parent in sorted(graph.parents(treatment), reverse=True)]
    steps = 0
    while stack:
        path = stack.pop()
        steps += 1
        if steps % 1000 == 0:
            _tick(deadline)
        if steps > MAX_EXPANSIONS:
            raise Refusal(INPUT_TOO_LARGE, f"the search for backdoor paths from {treatment} to {outcome} took more "
                                           f"than {MAX_EXPANSIONS} steps; the graph has too many paths to list")
        if path[-1] == outcome:
            found.append(path)
            if len(found) > MAX_PATHS:
                raise Refusal(INPUT_TOO_LARGE, f"more than {MAX_PATHS} backdoor paths from {treatment} to {outcome}; "
                                               "G6 lists at most that many")
            continue
        for nxt in reversed(_neighbours(graph, path[-1])):
            if nxt not in path and nxt != treatment:
                stack.append([*path, nxt])
    found.sort(key=lambda p: (len(p), p))
    return [{"path": p, "rendered": render_path(graph, p), **path_status(graph, p, given)} for p in found]


def is_backdoor_set(graph: CausalGraph, treatment: str, outcome: str, adjustment: Iterable[str]) -> bool:
    """Whether ``adjustment`` meets the backdoor criterion for the effect of ``treatment`` on ``outcome``."""
    adjustment = set(adjustment)
    if {treatment, outcome} & adjustment or adjustment & graph.descendants({treatment}):
        return False
    return d_separated(graph.without_edges_out_of({treatment}), {treatment}, {outcome}, adjustment)


def _pool_or_refuse(pool: list[str], what: str) -> list[str]:
    if len(pool) > MAX_CANDIDATES:
        raise Refusal(INPUT_TOO_LARGE, f"{len(pool)} candidate variables for {what}, more than the {MAX_CANDIDATES} "
                                       "G6 searches exhaustively; split the graph or mark what cannot matter")
    return pool


def _minimal_sets(pool: list[str], valid: Any, deadline: float | None, smallest: int) -> list[list[str]]:
    found: list[list[str]] = []
    for size in range(smallest, len(pool) + 1):
        for combo in itertools.combinations(pool, size):
            _tick(deadline)
            chosen = set(combo)
            if any(set(f) <= chosen for f in found):
                continue
            if valid(chosen):
                found.append(list(combo))
    return found


def adjustment_sets(graph: CausalGraph, treatment: str, outcome: str, *,
                    deadline: float | None = None) -> list[list[str]]:
    """Every minimal set of observed variables that meets the backdoor criterion, smallest first (``[[]]`` when
    nothing needs adjusting, ``[]`` when no observed set will do)."""
    below = graph.descendants({treatment})
    around = graph.ancestors({treatment, outcome})
    pool = _pool_or_refuse(sorted(n for n in graph.observed if n not in (treatment, outcome) and n not in below
                                  and n in around), "an adjustment set")
    cut = graph.without_edges_out_of({treatment})
    return _minimal_sets(pool, lambda s: d_separated(cut, {treatment}, {outcome}, s), deadline, 0)


def is_instrument(graph: CausalGraph, candidate: str, treatment: str, outcome: str) -> bool:
    """Whether the observed ``candidate`` is an instrument for the effect of ``treatment`` on ``outcome``."""
    if candidate in (treatment, outcome) or candidate in graph.unobserved:
        return False
    if candidate in graph.descendants({treatment}):
        return False
    if d_separated(graph, {candidate}, {treatment}):
        return False
    return d_separated(graph.without_edges_out_of({treatment}), {candidate}, {outcome})


def instruments(graph: CausalGraph, treatment: str, outcome: str) -> list[str]:
    """Every observed instrument for the effect of ``treatment`` on ``outcome``, by name."""
    return sorted(n for n in graph.observed if is_instrument(graph, n, treatment, outcome))


def has_directed_path(graph: CausalGraph, start: str, end: str, avoiding: Iterable[str] = ()) -> bool:
    avoid = set(avoiding)
    seen, stack = {start}, [start]
    while stack:
        for nxt in graph.children(stack.pop()):
            if nxt == end:
                return True
            if nxt not in seen and nxt not in avoid:
                seen.add(nxt)
                stack.append(nxt)
    return False


def is_frontdoor_set(graph: CausalGraph, treatment: str, outcome: str, mediators: Iterable[str]) -> bool:
    """Whether ``mediators`` meets Pearl's frontdoor criterion: every directed path from ``treatment`` to ``outcome``
    passes through it, no backdoor path from ``treatment`` to it is open, and ``treatment`` blocks every backdoor path
    from it to ``outcome``. Every mediator must be observed."""
    mediators = set(mediators)
    if not mediators or {treatment, outcome} & mediators or mediators & graph.unobserved:
        return False
    if not has_directed_path(graph, treatment, outcome) or has_directed_path(graph, treatment, outcome, mediators):
        return False
    if not d_separated(graph.without_edges_out_of({treatment}), {treatment}, mediators):
        return False
    return d_separated(graph.without_edges_out_of(mediators), mediators, {outcome}, {treatment})


def frontdoor_sets(graph: CausalGraph, treatment: str, outcome: str, *,
                   deadline: float | None = None) -> list[list[str]]:
    """Every minimal set of observed mediators that meets the frontdoor criterion, smallest first."""
    below, after = graph.descendants({treatment}), graph.descendants({outcome})
    pool = _pool_or_refuse(sorted(n for n in graph.observed if n in below and n not in after
                                  and n not in (treatment, outcome)), "a frontdoor set")
    return _minimal_sets(pool, lambda s: is_frontdoor_set(graph, treatment, outcome, s), deadline, 1)


def backdoor_estimand(treatment: str, outcome: str, adjustment: list[str]) -> str:
    if not adjustment:
        return f"E[{outcome} | do({treatment}=t)] = E[{outcome} | {treatment}=t]"
    names = ", ".join(adjustment)
    return (f"E[{outcome} | do({treatment}=t)] = sum over {names} of E[{outcome} | {treatment}=t, {names}] * "
            f"P({names})")


def frontdoor_estimand(treatment: str, outcome: str, mediators: list[str]) -> str:
    names = ", ".join(mediators)
    return (f"E[{outcome} | do({treatment}=t)] = sum over {names} of P({names} | {treatment}=t) * sum over t' of "
            f"E[{outcome} | {names}, {treatment}=t'] * P({treatment}=t')")


def iv_estimand(treatment: str, outcome: str, instrument: str) -> str:
    return (f"the effect of {treatment} on {outcome} = Cov({outcome}, {instrument}) / Cov({treatment}, {instrument}) "
            f"(the Wald ratio), if {treatment}'s effect on {outcome} is the same for every unit (linear and "
            f"homogeneous); otherwise {instrument} identifies only a local effect, for the units whose {treatment} "
            f"responds to {instrument}, and only if that response is monotone")


def _unblockable(graph: CausalGraph, treatment: str, outcome: str, deadline: float | None) -> list[dict[str, Any]]:
    """Backdoor paths whose every node between the ends is an unobserved non-collider: no observed set blocks them."""
    try:
        paths = backdoor_paths(graph, treatment, outcome, deadline=deadline)
    except Refusal as refusal:
        if refusal.code == TIME_LIMIT:
            raise
        return []
    return [p for p in paths if not p["colliders"] and all(n in graph.unobserved for n in p["path"][1:-1])]


def identify(graph: CausalGraph, treatment: str, outcome: str, *, deadline: float | None = None) -> dict[str, Any]:
    """Whether the effect of ``treatment`` on ``outcome`` is identifiable by the backdoor, frontdoor or
    instrumental-variable criterion (in that order of preference), with the estimand written out, or why not."""
    base: dict[str, Any] = {"treatment": treatment, "outcome": outcome, "backdoor": None, "frontdoor": None,
                            "iv": None}
    if not has_directed_path(graph, treatment, outcome):
        return base | {"identifiable": True, "route": "no_directed_path",
                       "estimand": f"P({outcome} | do({treatment}=t)) = P({outcome})",
                       "reason": f"there is no directed path from {treatment} to {outcome}, so the graph says "
                                 f"{treatment} has no effect on {outcome}"}
    sets = adjustment_sets(graph, treatment, outcome, deadline=deadline)
    if sets:
        base["backdoor"] = {"sets": sets, "chosen": sets[0],
                            "estimand": backdoor_estimand(treatment, outcome, sets[0])}
    mediators = frontdoor_sets(graph, treatment, outcome, deadline=deadline)
    if mediators:
        base["frontdoor"] = {"sets": mediators, "chosen": mediators[0],
                             "estimand": frontdoor_estimand(treatment, outcome, mediators[0])}
    found = instruments(graph, treatment, outcome)
    if found:
        base["iv"] = {"instruments": found, "estimand": iv_estimand(treatment, outcome, found[0])}
    if base["backdoor"] is not None:
        chosen = base["backdoor"]["chosen"]
        why = (f"adjusting for {', '.join(chosen)} blocks every backdoor path from {treatment} to {outcome}, and none "
               f"of it descends from {treatment}" if chosen
               else f"no backdoor path from {treatment} to {outcome} is open, so nothing needs adjusting")
        return base | {"identifiable": True, "route": "backdoor", "estimand": base["backdoor"]["estimand"],
                       "reason": why}
    if base["frontdoor"] is not None:
        names = ", ".join(base["frontdoor"]["chosen"])
        return base | {"identifiable": True, "route": "frontdoor", "estimand": base["frontdoor"]["estimand"],
                       "reason": f"no observed set blocks the backdoor paths, but {names} carries every directed path "
                                 f"from {treatment} to {outcome}, is not confounded with {treatment}, and {treatment} "
                                 f"blocks its backdoor paths to {outcome}"}
    if base["iv"] is not None:
        first = base["iv"]["instruments"][0]
        return base | {"identifiable": True, "route": "iv", "estimand": base["iv"]["estimand"],
                       "reason": f"no observed set blocks the backdoor paths and no frontdoor set exists, but {first} "
                                 f"moves {treatment} and reaches {outcome} only through it"}
    examples = _unblockable(graph, treatment, outcome, deadline)
    if examples:
        first = examples[0]
        hidden = ", ".join(first["path"][1:-1])
        open_note = f" (such as {first['rendered']}, open through the unobserved {hidden})"
    else:
        open_note = ""
    reason = (f"no set of observed variables blocks every backdoor path from {treatment} to {outcome}{open_note}, no "
              "set of observed mediators meets the frontdoor criterion, and no observed variable is an instrument. "
              "G6 checks these three criteria, not the complete do-calculus, so another identification may exist")
    return base | {"identifiable": False, "route": None, "estimand": None, "reason": reason}


__all__ = ["MAX_CANDIDATES", "MAX_EXPANSIONS", "MAX_PATHS", "adjustment_sets", "backdoor_estimand", "backdoor_paths",
           "d_separated", "frontdoor_estimand", "frontdoor_sets", "has_directed_path", "identify", "instruments",
           "is_backdoor_set", "is_frontdoor_set", "is_instrument", "iv_estimand", "open_path", "path_status",
           "render_path"]
