# SPDX-License-Identifier: Apache-2.0
"""The optional second engine: PyReason, imported only when an op runs and only when it imports here.

G6 hands PyReason a model it builds itself: a networkx graph, and facts and rules written as PyReason text from G6's
parsed model, with every node, label and variable renamed (``n0``, ``l0``, ``v0``) so that no caller text reaches
PyReason's parsers. It runs one timestep (``reason(timesteps=0)``) with PyReason's default settings (intersection
updates, its inconsistency check on) and reads every final interval back. The two engines agree when every interval is
within :data:`~g6.graph_reasoning.envelope.TOLERANCE` of the other's.

PyReason is only asked about models it evaluates the way G6 does. :func:`outside_fragment` says why a model is not
one, and then only the pure-Python engine runs. Read from PyReason's source (3.7.0, and the 4.0 preview):

* thresholds: for a node rule PyReason counts a clause's groundings over the whole graph, not per head, and re-checks
  thresholds against groundings it has already qualified, so a model with a threshold is not compared;
* bodies whose variables, linked by edge clauses, form a cycle (or, for an edge rule, a path between its two head
  variables, or a clause on one variable twice): PyReason narrows each variable's candidates separately rather than
  joining them, which is exact on a forest of variables and not otherwise;
* an annotation function whose clauses read a non-head variable other than through one edge clause to it, or a label
  a rule derives: PyReason reads such a clause's intervals across the whole graph, and applies every rule in every
  round, so it would read a derived label before it is final;
* a model with no rules, which PyReason does not accept.

PyReason runs only annotation functions registered with it, by name. Its own ``minimum`` and ``maximum`` take the
extreme of per-clause sums of bounds, where G6's take the extreme of the bounds; and its own ``average`` and
``average_lower`` return an interval object where its runner expects two floats, which fails (seen with the 4.0
preview). So G6 registers its own five, compiled by numba, written here and never caller code, as ``g6_average``,
``g6_average_lower``, ``g6_maximum``, ``g6_minimum`` and ``g6_product``: the comparison then tests PyReason's
grounding and fixpoint against G6's, over the same functions.

Importing PyReason is not free of side effects, which is why G6 imports it only on use: it sets ``NUMBA_CACHE_DIR``
for the process to a folder inside its own package, writes its cache there, and on its first import runs a small
example (printing to standard output, which G6 captures) and records that in a file in its package. Its first run in a
process compiles with numba and can take minutes; that time is outside G6's time limit and is reported. PyReason keeps
its program in module globals, so G6 runs it under a lock, one model at a time.
"""
from __future__ import annotations

import contextlib
import importlib
import importlib.metadata
import importlib.util
import io
import re
import sys
import threading
import time
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from g6.graph_reasoning.envelope import ENGINE_UNAVAILABLE, PYREASON_ERROR, TOLERANCE, Refusal
from g6.graph_reasoning.language import UNKNOWN, Atom, Interval, Model, Rule, render_atom, show_interval

_LOCK = threading.Lock()
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_FAILED: dict[str, str] = {}
_OWN = {name: f"g6_{name}" for name in ("average", "average_lower", "maximum", "minimum", "product")}


# --- which models PyReason evaluates as G6 does -------------------------------------------------------------------------

def _forest(rule: Rule) -> str:
    """Why the rule's variables are not a forest under PyReason's narrowing, or ``""``."""
    links: dict[tuple[str, str], None] = {}
    for clause in rule.clauses:
        if len(clause.variables) == 2:
            u, v = clause.variables
            if u == v:
                return f"the clause {clause.label}({u}, {u}) uses one variable twice"
            links[(u, v)] = None
    pinned = rule.kind == "edge"
    if pinned and rule.variables[0] == rule.variables[1]:
        return "its head uses one variable twice"

    def node(v: str) -> str:
        return "<head>" if pinned and v in rule.variables else v

    parent: dict[str, str] = {}

    def root(v: str) -> str:
        while parent.setdefault(v, v) != v:
            v = parent[v]
        return v

    for u, v in links:
        a, b = node(u), node(v)
        if a == b:
            continue          # both head variables of an edge rule: pinned per head, so exact
        ra, rb = root(a), root(b)
        if ra == rb:
            return ("its edge clauses link the variables in a cycle" if not pinned
                    else "its edge clauses link the variables in a cycle, or link its two head variables through "
                         "another")
        parent[ra] = rb
    return ""


def _function_reads(rule: Rule, derived: set[str]) -> str:
    """Why PyReason would feed this rule's annotation function other intervals than G6, or ``""``."""
    heads = set(rule.variables)
    uses: dict[str, int] = {}
    for clause in rule.clauses:
        for v in set(clause.variables):
            uses[v] = uses.get(v, 0) + 1
    for clause in rule.clauses:
        if clause.label in derived:
            return f"its annotation function reads {clause.label}, which a rule derives"
        if len(clause.variables) == 1:
            if clause.variables[0] not in heads:
                return f"its annotation function reads {clause.label} on a variable that is not in the head"
            continue
        u, v = clause.variables
        if not ({u, v} & heads):
            return f"its annotation function reads {clause.label} on an edge with no head variable"
        other = v if u in heads else u
        if other not in heads and uses.get(other, 0) > 1:
            return f"its annotation function reads {clause.label} through {other}, which another clause uses too"
    return ""


def outside_fragment(model: Model) -> list[str]:
    """Why PyReason would evaluate ``model`` differently from G6, one reason per rule; empty when it would not."""
    if not model.rules:
        return ["the model has no rules, which PyReason does not accept"]
    reasons = []
    derived = model.derived
    for i, rule in enumerate(model.rules):
        why = ""
        if any(c.at_least is not None for c in rule.clauses):
            why = ("it has a threshold, which PyReason counts over the whole graph for a node rule and re-checks "
                   "against groundings it has already qualified")
        why = why or _forest(rule)
        if not why and rule.function is not None:
            why = _function_reads(rule, derived)
        if why:
            reasons.append(f"rule {i + 1} (line {rule.line}): {why}")
    return reasons


# --- the translation ----------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Translation:
    """The model as PyReason text: the renamings, the graph, each fact and rule, and the functions to register."""

    nodes: dict[str, str]
    labels: dict[str, str]
    edges: tuple[tuple[str, str], ...]
    facts: tuple[str, ...]
    rules: tuple[str, ...]
    functions: tuple[str, ...] = field(default=())


def _number(value: Any) -> str:
    return repr(float(value))


def _bound(interval: Interval) -> str:
    return f"[{_number(interval[0])}, {_number(interval[1])}]"


def translate(model: Model) -> Translation:
    nodes = {name: f"n{i}" for i, name in enumerate(model.nodes)}
    labels = {name: f"l{i}" for i, name in enumerate(sorted(model.arities))}
    facts = tuple(f"{labels[label]}({', '.join(nodes[n] for n in component)}) : {_bound(value)}"
                  for (label, component), value in model.facts.items())
    rules, functions = [], []
    for rule in model.rules:
        names = {v: f"v{i}" for i, v in enumerate(dict.fromkeys([*rule.variables, *rule.body_variables]))}
        head = f"{labels[rule.head]}({', '.join(names[v] for v in rule.variables)})"
        if rule.function is not None:
            function = _OWN.get(rule.function, rule.function)
            functions.append(function)
            head += f" : {function}"
        else:
            head += f" : {_bound(rule.bound)}"
        body = ", ".join(f"{labels[c.label]}({', '.join(names[v] for v in c.variables)}) : {_bound(c.bound)}"
                         for c in rule.clauses)
        rules.append(f"{head} <- {body}")
    return Translation(nodes, labels, tuple((nodes[s], nodes[t]) for s, t in model.edges), facts, tuple(rules),
                       tuple(dict.fromkeys(functions)))


# --- G6's own annotation functions, compiled for PyReason ---------------------------------------------------------------

def g6_average(annotations, weights):  # noqa: ARG001 -- PyReason's two-argument signature
    lower, upper, count = 0.0, 0.0, 0
    for clause in annotations:
        for bound in clause:
            lower += bound.lower
            upper += bound.upper
            count += 1
    return lower / count, upper / count


def g6_average_lower(annotations, weights):  # noqa: ARG001
    lower, upper, count = 0.0, 0.0, 0
    for clause in annotations:
        for bound in clause:
            lower += bound.lower
            upper = max(upper, bound.upper)
            count += 1
    return lower / count, upper


def g6_maximum(annotations, weights):  # noqa: ARG001
    lower, upper = 0.0, 0.0
    for clause in annotations:
        for bound in clause:
            lower = max(lower, bound.lower)
            upper = max(upper, bound.upper)
    return lower, upper


def g6_minimum(annotations, weights):  # noqa: ARG001
    lower, upper = 1.0, 1.0
    for clause in annotations:
        for bound in clause:
            lower = min(lower, bound.lower)
            upper = min(upper, bound.upper)
    return lower, upper


def g6_product(annotations, weights):  # noqa: ARG001
    lower, upper = 1.0, 1.0
    for clause in annotations:
        for bound in clause:
            lower *= bound.lower
            upper *= bound.upper
    return lower, upper


_COMPILED: dict[str, Any] = {}


def _functions(names: tuple[str, ...]) -> list[Any]:
    for name in names:
        if name not in _COMPILED:
            _COMPILED[name] = importlib.import_module("numba").njit(globals()[name])
    return [_COMPILED[name] for name in names]


# --- loading and running PyReason ---------------------------------------------------------------------------------------

def _captured(seen: list[dict[str, Any]]) -> Any:
    @contextlib.contextmanager
    def capture() -> Any:
        out = io.StringIO()
        with warnings.catch_warnings(record=True) as caught, contextlib.redirect_stdout(out):
            warnings.simplefilter("always")
            try:
                yield
            finally:
                counted: dict[tuple[str, str], int] = {}
                for w in caught:
                    key = (w.category.__name__, _ANSI.sub("", str(w.message))[:300])
                    counted[key] = counted.get(key, 0) + 1
                seen.extend({"category": c, "message": m, "count": n} for (c, m), n in counted.items())
                printed = _ANSI.sub("", out.getvalue()).strip()
                if printed:
                    seen.append({"category": "stdout", "message": printed[:300], "count": 1})
    return capture()


def unavailable_reason() -> str:
    """Why PyReason is not importable here, or ``""`` when it is (importing it if need be)."""
    if "pyreason" in sys.modules:
        return "" if sys.modules["pyreason"] is not None else "PyReason is not importable here"
    if "import" in _FAILED:
        return _FAILED["import"]
    if importlib.util.find_spec("pyreason") is None:
        return "PyReason is not installed here"
    with _LOCK:
        try:
            with _captured([]):
                importlib.import_module("pyreason")
        except Exception as exc:  # noqa: BLE001 -- ImportError, or PyReason failing as it compiles on first import
            message = f"{type(exc).__name__}: {exc}".splitlines()[0][:300]
            _FAILED["import"] = f"PyReason is installed but does not import here ({message})"
            return _FAILED["import"]
    return ""


def available() -> bool:
    return unavailable_reason() == ""


def load() -> Any:
    """The ``pyreason`` module; a refusal with ``G6_E_GRAPH_REASONING_ENGINE_UNAVAILABLE`` when it does not import."""
    why = unavailable_reason()
    if why:
        raise Refusal(ENGINE_UNAVAILABLE, why)
    return sys.modules["pyreason"]


def version() -> str:
    try:
        return importlib.metadata.version("pyreason")
    except importlib.metadata.PackageNotFoundError:
        return str(getattr(load(), "__version__", "unknown"))


def solve(model: Model) -> tuple[dict[Atom, tuple[float, float]], list[dict[str, Any]], float]:
    """Every final interval PyReason gives ``model`` in one timestep (atoms it did not touch are ``[0, 1]``), the
    warnings and output it produced, and the seconds it took. Refused with ``_PYREASON_ERROR`` if it fails."""
    pr = load()
    networkx = importlib.import_module("networkx")
    plan = translate(model)
    back_nodes = {v: k for k, v in plan.nodes.items()}
    back_labels = {v: k for k, v in plan.labels.items()}
    seen: list[dict[str, Any]] = []
    started = time.perf_counter()
    with _LOCK:
        try:
            with _captured(seen):
                graph = networkx.DiGraph()
                graph.add_nodes_from(plan.nodes.values())
                graph.add_edges_from(plan.edges)
                pr.reset()
                pr.reset_rules()
                pr.reset_settings()
                pr.settings.verbose = False
                pr.settings.graph_attribute_parsing = False
                pr.load_graph(graph)
                for function in _functions(plan.functions):
                    pr.add_annotation_function(function)
                for i, text in enumerate(plan.rules):
                    pr.add_rule(pr.Rule(text, f"r{i}"))
                for i, text in enumerate(plan.facts):
                    pr.add_fact(pr.Fact(text, f"f{i}", 0, 0))
                interpretation = pr.reason(timesteps=0)
                found: dict[Atom, tuple[float, float]] = {}
                for name, node in plan.nodes.items():
                    world = interpretation.interpretations_node[node].world
                    for label in world:
                        found[(back_labels[label.get_value()], (name,))] = (world[label].lower, world[label].upper)
                for source, target in plan.edges:
                    world = interpretation.interpretations_edge[(source, target)].world
                    for label in world:
                        found[(back_labels[label.get_value()], (back_nodes[source], back_nodes[target]))] = (
                            world[label].lower, world[label].upper)
                pr.reset()
                pr.reset_rules()
        except Refusal:
            raise
        except Exception as exc:  # noqa: BLE001 -- reported with its type, never raised through g6_invoke
            raise Refusal(PYREASON_ERROR, f"PyReason failed: {type(exc).__name__}: {exc}"[:500]) from None
    return found, seen, round(time.perf_counter() - started, 6)


def compare(model: Model, ours: Mapping[Atom, Interval], theirs: Mapping[Atom, tuple[float, float]]) -> dict[str, Any]:
    """Whether every interval of the two engines is within the tolerance of the other's."""
    differences = []
    atoms = sorted(set(ours) | set(theirs))
    for atom in atoms:
        mine = ours.get(atom, UNKNOWN)
        other = theirs.get(atom, (0.0, 1.0))
        if abs(float(mine[0]) - other[0]) > TOLERANCE or abs(float(mine[1]) - other[1]) > TOLERANCE:
            differences.append(f"{render_atom(atom)}: G6 {show_interval(mine)}, PyReason [{other[0]:.12g}, "
                               f"{other[1]:.12g}]")
    return {"compared": len(atoms), "agreed": not differences, "differences": differences[:10],
            "tolerance": TOLERANCE}


__all__ = ["Translation", "available", "compare", "load", "outside_fragment", "solve", "translate",
           "unavailable_reason", "version"]
