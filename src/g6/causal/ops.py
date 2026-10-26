# SPDX-License-Identifier: Apache-2.0
"""The graph ops of g6.causal, each returning one labelled :class:`~g6.causal.schema.CausalResult`.

* :func:`d_separated`: whether a set of nodes d-separates two others, and an open path when it does not;
* :func:`backdoor_paths`: every backdoor path from the treatment to the outcome, each open or blocked;
* :func:`adjustment_sets`: every minimal set of observed variables that meets the backdoor criterion;
* :func:`instruments`: every observed instrument;
* :func:`identify`: whether the effect is identifiable by the backdoor, frontdoor or instrumental-variable criterion,
  with its estimand, or why not.

The pure-Python algorithms (:mod:`g6.causal.criteria`) always run. With ``engine="auto"`` DoWhy runs too when it is
installed, and ``engine="dowhy"`` requires it; the two must agree (:mod:`g6.causal.checks`), or the answer is refused
with ``G6_E_CAUSAL_ENGINES_DISAGREE``. ``engine="python"`` runs the pure-Python algorithms alone. A time limit (at most
:data:`MAX_TIME_LIMIT_S` seconds) covers the work; it is checked as the searches go and between engines, and a DoWhy
call that has started is not interrupted.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable
from typing import Any

from g6 import __version__
from g6.causal import checks, criteria, dowhy_engine
from g6.causal.envelope import (
    ENGINE_ERROR,
    INPUT_INVALID,
    TIME_LIMIT,
    TIME_LIMIT_TOO_LONG,
    UNKNOWN_NODE,
    Refusal,
    finish,
)
from g6.causal.graph import CausalGraph, read_graph
from g6.causal.schema import CausalResult

DEFAULT_TIME_LIMIT_S = 60.0
MAX_TIME_LIMIT_S = 600.0
ENGINES = ("auto", "dowhy", "python")


def deadline_for(seconds: Any) -> float:
    """A :func:`time.perf_counter` deadline ``seconds`` from now (monotonic, and finer than ``time.monotonic`` on
    Windows); a refusal for a limit that is not usable."""
    if isinstance(seconds, bool) or not isinstance(seconds, int | float) or not math.isfinite(seconds) or seconds <= 0:
        raise Refusal(INPUT_INVALID, f"time_limit must be a positive number of seconds, not {seconds!r}")
    if seconds > MAX_TIME_LIMIT_S:
        raise Refusal(TIME_LIMIT_TOO_LONG, f"time_limit {seconds:g} s is above the cap of {MAX_TIME_LIMIT_S:g} s")
    return time.perf_counter() + float(seconds)


def check_time(deadline: float, after: str) -> None:
    if time.perf_counter() > deadline:
        raise Refusal(TIME_LIMIT, f"the time limit passed after {after}")


def run(operation: str, work: Callable[[dict[str, Any]], dict[str, Any]], request_id: str,
        run_id: str) -> CausalResult:
    """``work``'s outcome labelled by :func:`~g6.causal.envelope.finish`. ``work`` fills ``state`` as it goes, so a
    refusal keeps what had run. Nothing raises through the surface."""
    state: dict[str, Any] = {}
    started = time.perf_counter()
    try:
        outcome = work(state)
    except Refusal as refusal:
        outcome = {**state, "code": refusal.code, "reason": refusal.reason, **refusal.keep}
    except Exception as exc:  # noqa: BLE001 -- reported with its type, never raised through g6_invoke
        outcome = {**state, "code": ENGINE_ERROR, "reason": f"{type(exc).__name__}: {exc}"[:500]}
    return finish(operation, elapsed_s=round(time.perf_counter() - started, 6), request_id=request_id, run_id=run_id,
                  **outcome)


def read(state: dict[str, Any], spec: Any) -> CausalGraph:
    graph = read_graph(spec)
    state["graph"] = {"nodes": len(graph.nodes), "edges": len(graph.edges),
                      "unobserved": [n for n in graph.nodes if n in graph.unobserved]}
    return graph


def node_set(graph: CausalGraph, names: Any, role: str, *, allow_empty: bool = False) -> list[str]:
    """``names`` (one name or a list) checked against ``graph``: known, distinct, and at least one unless allowed."""
    listed = ([names] if names else []) if isinstance(names, str) else list(names or [])
    if not listed and not allow_empty:
        raise Refusal(INPUT_INVALID, f"{role} needs at least one node")
    for name in listed:
        if not isinstance(name, str) or name not in graph.nodes:
            known = ", ".join(graph.nodes[:20]) + (", ..." if len(graph.nodes) > 20 else "")
            raise Refusal(UNKNOWN_NODE, f"{role} names {name!r}, which is not one of the graph's nodes ({known})")
    return list(dict.fromkeys(listed))


def effect_nodes(graph: CausalGraph, treatment: Any, outcome: Any) -> tuple[str, str]:
    """The treatment and outcome, each one known, observed node, and different."""
    t = node_set(graph, treatment, "treatment")
    y = node_set(graph, outcome, "outcome")
    if len(t) != 1 or len(y) != 1:
        raise Refusal(INPUT_INVALID, "give one treatment and one outcome, each a single node name")
    if t[0] == y[0]:
        raise Refusal(INPUT_INVALID, f"the treatment and the outcome must differ; both are {t[0]!r}")
    for role, name in (("treatment", t[0]), ("outcome", y[0])):
        if name in graph.unobserved:
            raise Refusal(INPUT_INVALID, f"the {role} {name!r} is marked unobserved; an effect on or of a variable "
                                         "nobody observes cannot be estimated from data")
    return t[0], y[0]


def _pure(state: dict[str, Any]) -> None:
    state["engines"] = [{"name": "pure_python", "version": __version__}]


def _second(state: dict[str, Any], engine: str, deadline: float, compare: Callable[[], dict[str, Any]]) -> None:
    """Run DoWhy's side when ``engine`` asks for it (or allows it and DoWhy is here), recording the agreement."""
    if engine not in ENGINES:
        raise Refusal(INPUT_INVALID, f"engine must be one of {', '.join(ENGINES)}, not {engine!r}")
    if engine == "python":
        state["one_engine_why"] = "engine='python' was asked for"
        return
    check_time(deadline, "the pure-Python algorithms")
    seen: list[dict[str, Any]] = []
    state["warnings_seen"] = seen
    with dowhy_engine.session(seen=seen, logged=state.setdefault("dowhy_log", [])):
        try:
            dowhy_engine.load()
        except Refusal:
            if engine == "dowhy":
                raise
            state["one_engine_why"] = "DoWhy is not installed here"
            return
        state["engines"].append({"name": "dowhy", "version": dowhy_engine.version()})
        state["agreement"] = compare()
    check_time(deadline, "DoWhy's check")


def _ops(name: str, work: Callable[[dict[str, Any], float], dict[str, Any]], time_limit: float, request_id: str,
         run_id: str) -> CausalResult:
    def wrapped(state: dict[str, Any]) -> dict[str, Any]:
        deadline = deadline_for(time_limit)
        _pure(state)
        work(state, deadline)
        return state

    return run(name, wrapped, request_id, run_id)


def d_separated(graph: Any, x: Any, y: Any, given: Iterable[str] | None = None, *, engine: str = "auto",
                time_limit: float = DEFAULT_TIME_LIMIT_S, request_id: str = "", run_id: str = "") -> CausalResult:
    """Whether ``given`` d-separates ``x`` from ``y`` (each a node or a list of nodes), with an open path if not."""
    def work(state: dict[str, Any], deadline: float) -> dict[str, Any]:
        g = read(state, graph)
        xs, ys = node_set(g, x, "x"), node_set(g, y, "y")
        zs = node_set(g, given, "given", allow_empty=True)
        for a, b, la, lb in ((xs, ys, "x", "y"), (xs, zs, "x", "given"), (ys, zs, "y", "given")):
            if set(a) & set(b):
                raise Refusal(INPUT_INVALID, f"{la} and {lb} must not share nodes; both name "
                                             f"{', '.join(sorted(set(a) & set(b)))}")
        separated = criteria.d_separated(g, xs, ys, zs)
        path = None if separated else criteria.open_path(g, xs, ys, zs, deadline=deadline)
        answer = {"x": xs, "y": ys, "given": zs, "separated": separated,
                  "open_path": criteria.render_path(g, path) if path else None}
        _second(state, engine, deadline,
                lambda: checks.compare_d_separation(separated, dowhy_engine.d_separated(g, xs, ys, zs)))
        state["answer"] = answer
        return state

    return _ops("d_separated", work, time_limit, request_id, run_id)


def backdoor_paths(graph: Any, treatment: Any, outcome: Any, given: Iterable[str] | None = None, *,
                   engine: str = "auto", time_limit: float = DEFAULT_TIME_LIMIT_S, request_id: str = "",
                   run_id: str = "") -> CausalResult:
    """Every backdoor path from ``treatment`` to ``outcome``, each open or blocked given ``given``."""
    def work(state: dict[str, Any], deadline: float) -> dict[str, Any]:
        g = read(state, graph)
        t, y = effect_nodes(g, treatment, outcome)
        zs = node_set(g, given, "given", allow_empty=True)
        if {t, y} & set(zs):
            raise Refusal(INPUT_INVALID, "given must not hold the treatment or the outcome")
        paths = criteria.backdoor_paths(g, t, y, zs, deadline=deadline)

        def compare() -> dict[str, Any]:
            theirs = dowhy_engine.backdoor_paths(g, t, y)
            status = {tuple(p): not dowhy_engine.is_blocked(g, p, zs) for p in theirs}
            return checks.compare_backdoor_paths(paths, theirs, status)

        _second(state, engine, deadline, compare)
        state["answer"] = {"treatment": t, "outcome": y, "given": zs, "count": len(paths),
                           "open": sum(1 for p in paths if p["open"]), "paths": paths}
        return state

    return _ops("backdoor_paths", work, time_limit, request_id, run_id)


def _dowhy_identification(g: CausalGraph, t: str, y: str, mine: dict[str, Any],
                          parts: tuple[str, ...]) -> dict[str, Any]:
    theirs = dowhy_engine.identification(g, t, y)
    return checks.compare_identification(g, t, y, mine, theirs,
                                         lambda s: dowhy_engine.valid_backdoor(g, t, y, s), parts=parts)


def adjustment_sets(graph: Any, treatment: Any, outcome: Any, *, engine: str = "auto",
                    time_limit: float = DEFAULT_TIME_LIMIT_S, request_id: str = "", run_id: str = "") -> CausalResult:
    """Every minimal set of observed variables that meets the backdoor criterion for ``treatment`` on ``outcome``."""
    def work(state: dict[str, Any], deadline: float) -> dict[str, Any]:
        g = read(state, graph)
        t, y = effect_nodes(g, treatment, outcome)
        sets = criteria.adjustment_sets(g, t, y, deadline=deadline)
        mine = {"identifiable": bool(sets), "route": "backdoor" if sets else None,
                "backdoor": {"sets": sets} if sets else None}
        if not criteria.has_directed_path(g, t, y):
            mine["route"] = "no_directed_path"
        _second(state, engine, deadline, lambda: _dowhy_identification(g, t, y, mine, ("backdoor",)))
        state["answer"] = {"treatment": t, "outcome": y, "sets": sets, "none_needed": sets == [[]],
                           "estimands": [criteria.backdoor_estimand(t, y, s) for s in sets]}
        return state

    return _ops("adjustment_sets", work, time_limit, request_id, run_id)


def instruments(graph: Any, treatment: Any, outcome: Any, *, engine: str = "auto",
                time_limit: float = DEFAULT_TIME_LIMIT_S, request_id: str = "", run_id: str = "") -> CausalResult:
    """Every observed instrument for the effect of ``treatment`` on ``outcome``."""
    def work(state: dict[str, Any], deadline: float) -> dict[str, Any]:
        g = read(state, graph)
        t, y = effect_nodes(g, treatment, outcome)
        found = criteria.instruments(g, t, y)

        def compare() -> dict[str, Any]:
            theirs = {"instruments": dowhy_engine.instruments(g, t, y), "frontdoor": [], "no_directed_path": False}
            return checks.compare_identification(g, t, y, {}, theirs, lambda s: True, parts=("iv",))

        _second(state, engine, deadline, compare)
        state["answer"] = {"treatment": t, "outcome": y, "instruments": found}
        return state

    return _ops("instruments", work, time_limit, request_id, run_id)


def identify(graph: Any, treatment: Any, outcome: Any, *, engine: str = "auto",
             time_limit: float = DEFAULT_TIME_LIMIT_S, request_id: str = "", run_id: str = "") -> CausalResult:
    """Whether the effect of ``treatment`` on ``outcome`` is identifiable from ``graph``, and its estimand."""
    def work(state: dict[str, Any], deadline: float) -> dict[str, Any]:
        g = read(state, graph)
        t, y = effect_nodes(g, treatment, outcome)
        mine = criteria.identify(g, t, y, deadline=deadline)
        _second(state, engine, deadline,
                lambda: _dowhy_identification(g, t, y, mine, ("identifiable", "backdoor", "iv", "frontdoor")))
        state["answer"] = mine
        return state

    return _ops("identify", work, time_limit, request_id, run_id)


__all__ = ["DEFAULT_TIME_LIMIT_S", "ENGINES", "MAX_TIME_LIMIT_S", "adjustment_sets", "backdoor_paths", "check_time",
           "d_separated", "deadline_for", "effect_nodes", "identify", "instruments", "node_set", "read", "run"]
