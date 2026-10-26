# SPDX-License-Identifier: Apache-2.0
"""The four ops of g6.datalog, each returning one labelled :class:`~g6.datalog.schema.DatalogResult`.

* :func:`evaluate`: the program's derived relations (those a rule defines, or the ones named), bounded;
* :func:`query`: the answers to a goal (or to each query in the program), one per distinct binding of its named
  variables, each with its derivation;
* :func:`explain`: why a ground tuple holds, as its derivation tree down to the facts, or that it is not derivable,
  which G6's check of the model shows;
* :func:`check_program`: the parse, arity, safety and stratification report, with nothing evaluated.

The first three evaluate the same way. The pure-Python engine always runs, since its derivations are the certificates.
With ``engine="auto"`` Z3's Datalog engine runs too when it is installed, and ``engine="z3"`` requires it; the two must
derive the same relations, tuple for tuple. Then G6's check (:mod:`g6.datalog.checks`) confirms the model before any
answer is built from it. A time limit (at most 60 seconds) covers the engines and the check together.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from typing import Any

from g6 import __version__
from g6.datalog import checks, seminaive, z3_engine
from g6.datalog.envelope import (
    ANSWER_TOO_LARGE,
    ENGINE_ERROR,
    INPUT_INVALID,
    SOLVER_UNAVAILABLE,
    TIME_LIMIT_TOO_LONG,
    UNKNOWN_RELATION,
    Refusal,
    finish,
)
from g6.datalog.language import (
    Atom,
    Comparison,
    Const,
    Negation,
    Program,
    Value,
    Var,
    order_key,
    parse_goal,
    parse_program,
    render_atom,
    render_rule,
    render_tuple,
    render_value,
)
from g6.datalog.schema import DatalogResult

DEFAULT_TIME_LIMIT_S = 10.0
MAX_TIME_LIMIT_S = 60.0
#: The most tuples ``evaluate`` returns, and the most answers ``query`` returns, in one reply.
MAX_ANSWER_TUPLES = 10_000
#: The most nodes of one derivation tree: per answer of ``query``, in all for one reply, and for ``explain``.
MAX_ANSWER_NODES = 25
MAX_REPLY_NODES = 20_000
MAX_EXPLAIN_NODES = 1_000
ENGINES = ("auto", "z3", "python")

Row = tuple[Value, ...]


def _row_key(row: Row) -> list[tuple]:
    return [order_key(v) for v in row]


def _deadline(seconds: Any) -> float:
    if isinstance(seconds, bool) or not isinstance(seconds, int | float) or not math.isfinite(seconds) or seconds <= 0:
        raise Refusal(INPUT_INVALID, f"time_limit must be a positive number of seconds, not {seconds!r}")
    if seconds > MAX_TIME_LIMIT_S:
        raise Refusal(TIME_LIMIT_TOO_LONG, f"time_limit {seconds:g} s is above the cap of {MAX_TIME_LIMIT_S:g} s")
    return time.monotonic() + float(seconds)


def _summary(program: Program) -> dict[str, Any]:
    return {"facts": program.fact_count, "rules": len(program.rules), "queries": len(program.queries),
            "relations": dict(sorted(program.arities.items())), "strata": [list(s) for s in program.order],
            "constants": len(program.constants())}


def _run(operation: str, work: Callable[[dict[str, Any]], dict[str, Any]], request_id: str,
         run_id: str) -> DatalogResult:
    """``work``'s outcome labelled by :func:`~g6.datalog.envelope.finish`. ``work`` fills ``state`` as it goes, so a
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


def _read(state: dict[str, Any], text: str, facts: Mapping[str, Any] | None) -> Program:
    program = parse_program(text, facts)
    if not program.facts and not program.rules:  # an answer of nothing would hide a program that never arrived
        raise Refusal(INPUT_INVALID, "the program is empty: give facts or rules in program, or facts as JSON")
    state["program"] = _summary(program)
    state["undefined"] = program.undefined
    return program


def _agreement(python: Mapping[str, set[Row]], z3: Mapping[str, set[Row]]) -> dict[str, Any]:
    differences = []
    for name in sorted(set(python) | set(z3)):
        mine, theirs = python.get(name, set()), z3.get(name, set())
        if mine == theirs:
            continue
        parts = []
        for rows, who in ((mine - theirs, "the pure-Python engine"), (theirs - mine, "Z3")):
            if rows:
                example = render_tuple(name, sorted(rows, key=_row_key)[0])
                parts.append(f"{len(rows)} only from {who}, such as {example}")
        differences.append(f"{name}: " + "; ".join(parts))
    return {"compared": sorted(set(python) | set(z3)), "agreed": not differences, "differences": differences[:10],
            "tuples": sum(len(rows) for rows in python.values())}


def _model(state: dict[str, Any], program: Program, engine: str, deadline: float) -> seminaive.Model | None:
    """The checked model, or ``None`` when the engines disagreed or the check failed (``finish`` then blocks it)."""
    if engine not in ENGINES:
        raise Refusal(INPUT_INVALID, f"engine must be one of {', '.join(ENGINES)}, not {engine!r}")
    with_z3 = engine == "z3" or (engine == "auto" and z3_engine.available())
    if engine == "z3" and not z3_engine.available():
        raise Refusal(SOLVER_UNAVAILABLE, "engine='z3' was asked for, but z3-solver is not importable here; install "
                                          "it with pip install 'g6[formal]'")
    model = seminaive.solve(program, deadline=deadline)
    state["engines"] = [{"name": "pure_python", "version": __version__, "rounds": model.rounds}]
    state["model"] = {"tuples": sum(len(rows) for rows in model.relations.values()),
                      "relations": {name: len(rows) for name, rows in sorted(model.relations.items())}}
    if with_z3:
        derived = z3_engine.solve(program, deadline=deadline)
        state["engines"].append({"name": "z3", "version": z3_engine.version()})
        state["agreement"] = _agreement(model.relations, derived)
    else:
        state["one_engine_why"] = ("Z3 is not installed here" if engine == "auto"
                                   else "engine='python' was asked for")
    check = checks.check_model(program, model.relations, model.support, deadline=deadline)
    state["check"] = check
    agreed = state.get("agreement", {"agreed": True})["agreed"]
    return model if agreed and check.passed else None


def _derivation(program: Program, model: seminaive.Model, relation: str, row: Row, budget: int) -> tuple[dict, int,
                                                                                                         bool]:
    """The derivation tree of one tuple, at most ``budget`` nodes expanded (depth first, without recursion), the nodes
    used, and whether any part was cut. A cut node keeps its atom and says ``truncated``: explain it to go on."""
    holder: list[dict] = []
    stack: list[tuple[str, Row, list[dict]]] = [(relation, row, holder)]
    used = 0
    while stack:
        name, values, into = stack.pop()
        node: dict[str, Any] = {"atom": render_tuple(name, values)}
        into.append(node)
        if used >= budget:
            node["truncated"] = True
            continue
        used += 1
        step = model.support[(name, values)]
        if step.rule is None:
            node["by"] = "fact"
            continue
        rule = program.rules[step.rule]
        binding = dict(step.bindings)
        conditions = []
        for lit in rule.body:
            if isinstance(lit, Comparison):
                left, right = _ground((lit.left, lit.right), binding)
                conditions.append(f"{render_value(left)} {lit.op} {render_value(right)}")
            elif isinstance(lit, Negation):
                conditions.append("not " + render_tuple(lit.atom.relation, _ground(lit.atom.args, binding)))
        node |= {"by": "rule", "rule": step.rule + 1, "line": rule.line, "text": render_rule(rule),
                 "bindings": {k: v for k, v in step.bindings if not k.startswith("_#")}}
        if conditions:
            node["conditions"] = conditions
        node["from"] = []
        for atom in reversed(rule.positives):
            stack.append((atom.relation, _ground(atom.args, binding), node["from"]))
    truncated = any(n.get("truncated") for n in _walk(holder[0]))
    return holder[0], used, truncated


def _ground(terms: tuple, binding: Mapping[str, Value]) -> Row:
    return tuple(t.value if isinstance(t, Const) else binding[t.name] for t in terms)


def _walk(node: dict) -> list[dict]:
    found, stack = [], [node]
    while stack:
        current = stack.pop()
        found.append(current)
        stack.extend(current.get("from", []))
    return found


def evaluate(program: str = "", *, facts: Mapping[str, Any] | None = None, relations: list[str] | None = None,
             engine: str = "auto", time_limit: float = DEFAULT_TIME_LIMIT_S, request_id: str = "",
             run_id: str = "") -> DatalogResult:
    """The relations ``program`` derives: by default every relation a rule defines, or those ``relations`` names."""
    def work(state: dict[str, Any]) -> dict[str, Any]:
        deadline = _deadline(time_limit)
        parsed = _read(state, program, facts)
        names = list(relations) if relations else sorted({rule.head.relation for rule in parsed.rules})
        unknown = [name for name in names if name not in parsed.arities]
        if unknown:
            raise Refusal(UNKNOWN_RELATION, f"{', '.join(unknown)} {'is' if len(unknown) == 1 else 'are'} not a "
                                            "relation of the program: no fact or rule uses it")
        model = _model(state, parsed, engine, deadline)
        if model is None:
            return state
        total = sum(len(model.relations[name]) for name in names)
        if total > MAX_ANSWER_TUPLES:
            raise Refusal(ANSWER_TOO_LARGE, f"{total} tuples to return, more than the {MAX_ANSWER_TUPLES} one reply "
                                            "carries")
        state["relations"] = {name: [list(row) for row in sorted(model.relations[name], key=_row_key)]
                              for name in names}
        return state

    return _run("evaluate", work, request_id, run_id)


def _answers(goal: Atom, rows: set[Row]) -> list[tuple[dict[str, Value], Row]]:
    """Each distinct binding of the goal's named variables, with the first tuple (in order) that gives it."""
    named = list(dict.fromkeys(t.name for t in goal.args if isinstance(t, Var) and not t.anonymous))
    found: dict[tuple, tuple[dict[str, Value], Row]] = {}
    for row in sorted(rows, key=_row_key):
        binding: dict[str, Value] = {}
        ok = True
        for term, value in zip(goal.args, row):
            if isinstance(term, Const):
                ok = term.value == value
            elif not term.anonymous:
                ok = binding.setdefault(term.name, value) == value
            if not ok:
                break
        if ok:
            key = tuple((type(binding[n]).__name__, binding[n]) for n in named)
            found.setdefault(key, ({n: binding[n] for n in named}, row))
    return list(found.values())


def query(program: str = "", goal: str = "", *, facts: Mapping[str, Any] | None = None, engine: str = "auto",
          time_limit: float = DEFAULT_TIME_LIMIT_S, request_id: str = "", run_id: str = "") -> DatalogResult:
    """The answers to ``goal`` (or, with no goal, to each query written in the program), each with its derivation."""
    def work(state: dict[str, Any]) -> dict[str, Any]:
        deadline = _deadline(time_limit)
        parsed = _read(state, program, facts)
        goals = [parse_goal(goal, parsed)] if goal.strip() else list(parsed.queries)
        if not goals:
            raise Refusal(INPUT_INVALID, "give a goal: the query field, or a query in the program such as "
                                         "ancestor(alice, X)?")
        model = _model(state, parsed, engine, deadline)
        if model is None:
            return state
        found = [(g, _answers(g, model.relations[g.relation])) for g in goals]
        total = sum(len(answers) for _, answers in found)
        if total > MAX_ANSWER_TUPLES:
            raise Refusal(ANSWER_TOO_LARGE, f"{total} answers to return, more than the {MAX_ANSWER_TUPLES} one reply "
                                            "carries")
        left, out = MAX_REPLY_NODES, []
        for g, answers in found:
            listed = []
            for binding, row in answers:
                tree, used, _ = _derivation(parsed, model, g.relation, row, min(MAX_ANSWER_NODES, left))
                left -= used
                listed.append({"bindings": binding, "tuple": list(row), "derivation": tree})
            out.append({"goal": render_atom(g), "count": len(listed), "answers": listed})
        state["queries"] = out
        return state

    return _run("query", work, request_id, run_id)


def explain(program: str = "", goal: str = "", *, facts: Mapping[str, Any] | None = None, engine: str = "auto",
            time_limit: float = DEFAULT_TIME_LIMIT_S, request_id: str = "", run_id: str = "") -> DatalogResult:
    """Why the ground atom ``goal`` holds (its derivation tree), or that it is not derivable."""
    def work(state: dict[str, Any]) -> dict[str, Any]:
        deadline = _deadline(time_limit)
        parsed = _read(state, program, facts)
        if not goal.strip() and len(parsed.queries) == 1:
            target = parsed.queries[0]
        else:
            target = parse_goal(goal, parsed)
        if target.variables:
            raise Refusal(INPUT_INVALID, f"explain takes a ground atom, such as ancestor(alice, carol); "
                                         f"{render_atom(target)} has variables, so use query for it")
        row = tuple(t.value for t in target.args)
        model = _model(state, parsed, engine, deadline)
        if model is None:
            return state
        shown = render_tuple(target.relation, row)
        if row in model.relations[target.relation]:
            tree, used, cut = _derivation(parsed, model, target.relation, row, MAX_EXPLAIN_NODES)
            state["explanation"] = {"goal": shown, "derivable": True, "derivation": tree, "nodes": used,
                                    "truncated": cut}
        else:
            state["explanation"] = {
                "goal": shown, "derivable": False,
                "because": f"{shown} is not in the program's model, and G6's check shows that model is closed "
                           "(applying every rule once more adds nothing) and supported (every tuple in it derives "
                           "from the facts), so nothing derives it.",
            }
        return state

    return _run("explain", work, request_id, run_id)


def check_program(program: str = "", *, facts: Mapping[str, Any] | None = None, request_id: str = "",
                  run_id: str = "") -> DatalogResult:
    """Parse ``program`` and check its arities, safety and stratification, with nothing evaluated."""
    def work(state: dict[str, Any]) -> dict[str, Any]:
        parsed = _read(state, program, facts)
        heads = {rule.head.relation for rule in parsed.rules}
        described = {}
        for name in sorted(parsed.arities):
            by = [what for what, has in (("facts", name in parsed.facts), ("rules", name in heads)) if has]
            described[name] = {"arity": parsed.arities[name], "stratum": parsed.strata[name],
                               "defined_by": " and ".join(by) or "nothing", "recursive": name in parsed.recursive}
        state["report"] = {
            "relations": described, "strata": [list(s) for s in parsed.order], "facts": parsed.fact_count,
            "rules": len(parsed.rules), "queries": [render_atom(q) for q in parsed.queries],
            "negation": any(rule.negations for rule in parsed.rules), "undefined": parsed.undefined,
        }
        return state

    return _run("check", work, request_id, run_id)


__all__ = ["DEFAULT_TIME_LIMIT_S", "ENGINES", "MAX_ANSWER_TUPLES", "MAX_EXPLAIN_NODES", "MAX_TIME_LIMIT_S",
           "check_program", "evaluate", "explain", "parse_program", "query"]
