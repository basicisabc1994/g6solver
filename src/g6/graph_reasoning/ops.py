# SPDX-License-Identifier: Apache-2.0
"""The four ops of g6.graph_reasoning, each returning one labelled
:class:`~g6.graph_reasoning.schema.GraphReasoningResult`.

* :func:`reason`: the final interpretation, every atom whose interval is not the unknown ``[0, 1]`` (or those of the
  labels named), bounded;
* :func:`query`: the interval of one label on one node or edge, with the steps that set its lower and upper bounds;
* :func:`explain`: why that interval holds, as its derivation tree down to the facts, or why it stayed unknown;
* :func:`check_model`: the parse, safety, stratification and size report, and whether PyReason could cross-check the
  model, with nothing evaluated.

The first three evaluate the same way. The pure-Python engine always runs, since its histories are the certificates;
G6's check (:mod:`g6.graph_reasoning.checks`) confirms the interpretation before any answer is built from it. With
``engine="auto"`` PyReason runs too when it imports here and the model is one it evaluates as G6 does
(:func:`g6.graph_reasoning.pyreason_engine.outside_fragment`); ``engine="pyreason"`` requires both. A time limit (at
most 60 seconds) covers the engine and the check; PyReason's own run cannot be interrupted in process, so its time is
reported instead.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from typing import Any

from g6 import __version__
from g6.graph_reasoning import checks, engine, pyreason_engine
from g6.graph_reasoning.envelope import (
    ANSWER_TOO_LARGE,
    CONFLICT,
    ENGINE_ERROR,
    INPUT_INVALID,
    NOT_COMPARABLE,
    TIME_LIMIT_TOO_LONG,
    UNKNOWN_LABEL,
    Refusal,
    finish,
)
from g6.graph_reasoning.language import (
    FUNCTION_MEANING,
    UNKNOWN,
    Atom,
    Interval,
    Model,
    as_floats,
    parse_atom,
    parse_model,
    render_atom,
    render_rule,
    show,
    show_interval,
)
from g6.graph_reasoning.schema import GraphReasoningResult

DEFAULT_TIME_LIMIT_S = 10.0
MAX_TIME_LIMIT_S = 60.0
#: The most atoms ``reason`` returns in one reply.
MAX_ANSWER_ATOMS = 10_000
#: The most nodes of a derivation tree: for ``query`` and for ``explain``.
MAX_QUERY_NODES = 25
MAX_EXPLAIN_NODES = 1_000
ENGINES = ("auto", "pyreason", "python")


def _deadline(seconds: Any) -> float:
    if isinstance(seconds, bool) or not isinstance(seconds, int | float) or not math.isfinite(seconds) or seconds <= 0:
        raise Refusal(INPUT_INVALID, f"time_limit must be a positive number of seconds, not {seconds!r}")
    if seconds > MAX_TIME_LIMIT_S:
        raise Refusal(TIME_LIMIT_TOO_LONG, f"time_limit {seconds:g} s is above the cap of {MAX_TIME_LIMIT_S:g} s")
    return time.monotonic() + float(seconds)


def _summary(model: Model) -> dict[str, Any]:
    return {"nodes": len(model.nodes), "edges": len(model.edges), "facts": model.fact_count,
            "atoms_given": len(model.facts), "rules": len(model.rules), "labels": dict(sorted(model.arities.items())),
            "strata": [list(s) for s in model.order]}


def _run(operation: str, work: Callable[[dict[str, Any]], dict[str, Any]], request_id: str,
         run_id: str) -> GraphReasoningResult:
    """``work``'s outcome labelled by :func:`~g6.graph_reasoning.envelope.finish`. ``work`` fills ``state`` as it goes,
    so a refusal keeps what had run. Nothing raises through the surface."""
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


def _read(state: dict[str, Any], text: str, graph: Mapping[str, Any] | None,
          facts: list[Mapping[str, Any]] | None) -> Model:
    model = parse_model(text, graph=graph, facts=facts)
    if not model.facts and not model.rules:  # an answer of nothing would hide a model that never arrived
        raise Refusal(INPUT_INVALID, "the model is empty: give facts or rules in program, or facts as JSON")
    state["model"] = _summary(model)
    return model


def _step_view(model: Model, step: engine.Step) -> dict[str, Any]:
    if step.rule is None:
        return {"by": "fact", "where": list(step.where), "value": as_floats(step.value)}
    rule = model.rules[step.rule]
    view = {"by": "rule", "rule": step.rule + 1, "line": rule.line, "text": render_rule(rule), "rank": step.rank,
            "value": as_floats(step.value), "bindings": dict(step.head)}
    if rule.function is not None:
        view["function"] = f"{rule.function}: {FUNCTION_MEANING[rule.function]}"
    return view


def _conflict(model: Model, solution: engine.Solution) -> dict[str, Any]:
    conflict = solution.conflict
    shown = render_atom(conflict.atom)
    tree, _, _ = _derivation(model, solution, conflict.atom, MAX_QUERY_NODES)
    step = _step_view(model, conflict.step)
    step["from"] = [{"atom": render_atom(a), "bounds": as_floats(b)} for a, b in conflict.step.sources]
    return {"atom": shown, "before": as_floats(conflict.before), "met": as_floats(conflict.step.value),
            "between": [{"side": "the interval it had", "derivation": tree}, {"side": "the step that met it", **step}],
            "pyreason_would": "PyReason, with its inconsistency check on (its default), would set this atom to "
                              "[0, 1] and freeze it for the timestep, and carry on; G6 reports the conflict instead"}


def _evaluate(state: dict[str, Any], model: Model, engine_name: str, deadline: float) -> engine.Solution | None:
    """The checked solution, or ``None`` when the check failed or the engines disagreed (``finish`` then blocks)."""
    if engine_name not in ENGINES:
        raise Refusal(INPUT_INVALID, f"engine must be one of {', '.join(ENGINES)}, not {engine_name!r}")
    outside = pyreason_engine.outside_fragment(model)
    if engine_name == "pyreason":
        if outside:
            raise Refusal(NOT_COMPARABLE, "PyReason evaluates this model differently from G6, so it cannot confirm "
                                          "the answer: " + "; ".join(outside[:3]), outside=outside)
        pyreason_engine.load()
    with_pyreason = engine_name == "pyreason"
    if engine_name == "auto":
        why = "the model is outside what PyReason evaluates as G6 does" if outside else (
            pyreason_engine.unavailable_reason())
        with_pyreason = not why
        if why:
            state["one_engine_why"] = why
            state["outside"] = outside
    elif engine_name == "python":
        state["one_engine_why"] = "engine='python' was asked for"
    solution = engine.solve(model, deadline=deadline)
    state["engines"] = [{"name": "pure_python", "version": __version__, "rounds": solution.rounds,
                         "groundings": solution.groundings}]
    state["solution"] = {"atoms": sum(1 for b in solution.bounds.values() if b != UNKNOWN),
                         "rounds": solution.rounds, "groundings": solution.groundings, "strata": solution.strata}
    if solution.conflict is not None:
        state["check"] = checks.check_conflict(model, solution.bounds, solution.history, solution.conflict,
                                               deadline=deadline)
        state["conflict"] = _conflict(model, solution)
        atom = render_atom(solution.conflict.atom)
        raise Refusal(CONFLICT, f"{atom} would be empty: it was {show_interval(solution.conflict.before)}, and "
                                f"{_step_view(model, solution.conflict.step)['by']} "
                                f"{_who(model, solution.conflict.step)} gives "
                                f"{show_interval(solution.conflict.step.value)}, which shares nothing with it")
    check = checks.check_solution(model, solution.bounds, solution.history, deadline=deadline)
    state["check"] = check
    if not check.passed:
        return None
    if with_pyreason:
        theirs, seen, seconds = pyreason_engine.solve(model)
        state["engines"].append({"name": "pyreason", "version": pyreason_engine.version(), "seconds": seconds})
        state["pyreason_warnings"] = seen
        state["agreement"] = pyreason_engine.compare(model, solution.bounds, theirs)
        if not state["agreement"]["agreed"]:
            return None
    return solution


def _who(model: Model, step: engine.Step) -> str:
    if step.rule is None:
        return f"({', '.join(step.where)})"
    rule = model.rules[step.rule]
    return f"{step.rule + 1} (line {rule.line}) under {dict(step.head)}"


def _status(bound: Interval) -> str:
    if bound == UNKNOWN:
        return "unknown"
    if bound[0] == bound[1] == 1:
        return "true"
    if bound[0] == bound[1] == 0:
        return "false"
    return "between"


def _atom_view(atom: Atom, bound: Interval) -> dict[str, Any]:
    label, component = atom
    view: dict[str, Any] = {"atom": render_atom(atom), "label": label}
    view |= {"node": component[0]} if len(component) == 1 else {"edge": list(component)}
    return view | {"bounds": as_floats(bound), "exact": [show(bound[0]), show(bound[1])], "status": _status(bound)}


def _derivation(model: Model, solution: engine.Solution, atom: Atom, budget: int) -> tuple[dict, int, bool]:
    """The derivation of ``atom``'s interval, at most ``budget`` nodes, depth first without recursion. Each node shows
    the atom's interval as it stood then, and the earliest steps that set its lower and its upper bound; a rule step
    shows its sources, each as it stood before the step's rank. A cut node says ``truncated``."""
    holder: list[dict] = []
    stack: list[tuple[Atom, float, list[dict]]] = [(atom, math.inf, holder)]
    used, cut = 0, False
    while stack:
        current, limit, into = stack.pop()
        steps = [s for s in solution.history.get(current, []) if s.rank < limit]
        bound = UNKNOWN
        for s in steps:
            bound = (max(bound[0], s.value[0]), min(bound[1], s.value[1]))
        node: dict[str, Any] = {"atom": render_atom(current), "bounds": as_floats(bound)}
        into.append(node)
        if used >= budget:
            node["truncated"] = cut = True
            continue
        used += 1
        if not steps:
            node["unknown"] = True
            continue
        chosen: dict[int, list[str]] = {}
        if bound[0] > 0:
            chosen.setdefault(next(i for i, s in enumerate(steps) if s.value[0] == bound[0]), []).append("lower")
        if bound[1] < 1:
            chosen.setdefault(next(i for i, s in enumerate(steps) if s.value[1] == bound[1]), []).append("upper")
        node["by"] = []
        for i in sorted(chosen):
            step = steps[i]
            view = {"sets": chosen[i], **_step_view(model, step)}
            node["by"].append(view)
            if step.rule is not None:
                view["from"] = []
                for source, _ in reversed(step.sources):
                    stack.append((source, step.rank, view["from"]))
    return holder[0], used, cut


def _with_solution(operation: str, program: str, graph: Any, facts: Any, engine_name: str, time_limit: Any,
                   prepare: Callable[[Model], Any], build: Callable[[dict[str, Any], Model, engine.Solution, Any], None],
                   request_id: str, run_id: str) -> GraphReasoningResult:
    """Read the model, ``prepare`` what the op asks about (refused before anything runs), evaluate, then ``build`` the
    answer from the checked solution."""
    def work(state: dict[str, Any]) -> dict[str, Any]:
        deadline = _deadline(time_limit)
        model = _read(state, program, graph, facts)
        target = prepare(model)
        solution = _evaluate(state, model, engine_name, deadline)
        if solution is not None:
            build(state, model, solution, target)
        return state

    return _run(operation, work, request_id, run_id)


def reason(program: str = "", *, graph: Mapping[str, Any] | None = None, facts: list[Mapping[str, Any]] | None = None,
           labels: list[str] | None = None, engine: str = "auto", time_limit: float = DEFAULT_TIME_LIMIT_S,
           request_id: str = "", run_id: str = "") -> GraphReasoningResult:
    """The final interpretation: every atom whose interval is not ``[0, 1]``, or only those of the ``labels`` named."""
    def prepare(model: Model) -> list[str] | None:
        if not labels:
            return None
        unknown = [name for name in labels if name not in model.arities]
        if unknown:
            raise Refusal(UNKNOWN_LABEL, f"{', '.join(unknown)} {'is' if len(unknown) == 1 else 'are'} not a label of "
                                         "the model: no fact or rule gives or reads it")
        return list(labels)

    def build(state: dict[str, Any], model: Model, solution: engine.Solution, wanted: list[str] | None) -> None:
        atoms = [(a, b) for a, b in sorted(solution.bounds.items())
                 if b != UNKNOWN and (wanted is None or a[0] in wanted)]
        if len(atoms) > MAX_ANSWER_ATOMS:
            raise Refusal(ANSWER_TOO_LARGE, f"{len(atoms)} atoms to return, more than the {MAX_ANSWER_ATOMS} one reply "
                                            "carries")
        state["interpretation"] = [_atom_view(a, b) for a, b in atoms]

    return _with_solution("reason", program, graph, facts, engine, time_limit, prepare, build, request_id, run_id)


def query(program: str = "", atom: str = "", *, graph: Mapping[str, Any] | None = None,
          facts: list[Mapping[str, Any]] | None = None, engine: str = "auto",
          time_limit: float = DEFAULT_TIME_LIMIT_S, request_id: str = "", run_id: str = "") -> GraphReasoningResult:
    """The interval of ``atom`` (``popular(alice)`` or ``friend(alice, bob)``), with the steps that set it."""
    def build(state: dict[str, Any], model: Model, solution: engine.Solution, target: Atom) -> None:
        bound = solution.bounds.get(target, UNKNOWN)
        tree, _, cut = _derivation(model, solution, target, MAX_QUERY_NODES)
        state["answer"] = {**_atom_view(target, bound), "derivation": tree, "truncated": cut}

    return _with_solution("query", program, graph, facts, engine, time_limit, lambda model: parse_atom(atom, model),
                          build, request_id, run_id)


def explain(program: str = "", atom: str = "", *, graph: Mapping[str, Any] | None = None,
            facts: list[Mapping[str, Any]] | None = None, engine: str = "auto",
            time_limit: float = DEFAULT_TIME_LIMIT_S, request_id: str = "", run_id: str = "") -> GraphReasoningResult:
    """Why ``atom``'s interval holds: its derivation tree down to the facts, or why it stayed ``[0, 1]``."""
    def build(state: dict[str, Any], model: Model, solution: engine.Solution, target: Atom) -> None:
        bound = solution.bounds.get(target, UNKNOWN)
        shown = render_atom(target)
        if bound == UNKNOWN:
            state["explanation"] = {
                **_atom_view(target, bound), "derivation": {"atom": shown, "bounds": [0.0, 1.0], "unknown": True},
                "because": f"no fact gives {shown} and no rule fired for it, and G6's check shows the interpretation "
                           "is closed (applying every rule once more tightens nothing), so it stays [0, 1]: unknown.",
            }
            return
        tree, used, cut = _derivation(model, solution, target, MAX_EXPLAIN_NODES)
        state["explanation"] = {**_atom_view(target, bound), "derivation": tree, "nodes": used, "truncated": cut}

    return _with_solution("explain", program, graph, facts, engine, time_limit, lambda model: parse_atom(atom, model),
                          build, request_id, run_id)


def check_model(program: str = "", *, graph: Mapping[str, Any] | None = None,
                facts: list[Mapping[str, Any]] | None = None, request_id: str = "",
                run_id: str = "") -> GraphReasoningResult:
    """Read ``program`` (and any JSON graph and facts) and check it, with nothing evaluated."""
    def work(state: dict[str, Any]) -> dict[str, Any]:
        model = _read(state, program, graph, facts)
        given = {atom[0] for atom in model.facts}
        heads = model.derived
        described = {}
        for name in sorted(model.arities):
            by = [what for what, has in (("facts", name in given), ("rules", name in heads)) if has]
            described[name] = {"arity": model.arities[name], "kind": "node" if model.arities[name] == 1 else "edge",
                               "stratum": model.strata[name], "given_by": " and ".join(by),
                               "recursive": name in model.recursive}
        outside = pyreason_engine.outside_fragment(model)
        state["report"] = {
            **_summary(model), "labels": described,
            "rules": [{"rule": i + 1, "line": r.line, "kind": r.kind, "text": render_rule(r),
                       "function": r.function, "thresholds": [c.label for c in r.clauses if c.at_least is not None]}
                      for i, r in enumerate(model.rules)],
            "pyreason_comparable": not outside, "outside_pyreason_fragment": outside,
        }
        return state

    return _run("check", work, request_id, run_id)


__all__ = ["DEFAULT_TIME_LIMIT_S", "ENGINES", "MAX_ANSWER_ATOMS", "MAX_EXPLAIN_NODES", "MAX_QUERY_NODES",
           "MAX_TIME_LIMIT_S", "check_model", "explain", "query", "reason"]
