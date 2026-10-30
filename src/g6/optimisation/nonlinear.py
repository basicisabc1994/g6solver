# SPDX-License-Identifier: Apache-2.0
"""``minimise`` and ``global_search``: a box-bounded, non-linear objective searched with SciPy, every answer re-checked.

Merged from the archive's ``adapt_optimisation`` and ``adapt_pygad`` (B01). Both called every run that returned
``verified``, converged or not (ADAPT_OPTIMISATION-01, ADAPT_PYGAD-01); the first evaluated caller expressions with
``eval`` after an allow-list that let ``9**9**9`` run unbounded (-03) and re-checked no answer (-04); the second posed
only benchmarks and separable sums (ADAPT_PYGAD-02). Here:

* the objective is data: a JSON expression tree, walked by G6's own interpreter. A number is a constant, a string
  names a declared variable, and a list is ``[operation, argument, ...]``: ``+``, ``*``, ``min`` and ``max`` (one or
  more arguments), ``-`` (one or two), ``/`` and ``^`` (two), ``neg``, ``abs``, ``sqrt``, ``exp``, ``log``, ``sin``,
  ``cos``, ``tan``, ``tanh`` and ``atan`` (one), and ``["table", [c0, c1, ...], index]``, the constant an integer
  variable picks (a discrete choice). Arithmetic is in floating point, so nothing grows without bound; at most
  :data:`MAX_NODES` nodes, :data:`MAX_DEPTH` deep, over at most :data:`MAX_VARIABLES` variables. A constant part that
  is not a finite number is refused, and so is anything else;
* every variable has finite bounds (``lb`` is 0 unless given); an integer variable is a whole number within them;
* ``minimise`` runs a SciPy local method (L-BFGS-B, Nelder-Mead or Powell) from several starts (the first at ``x0``
  when given, the rest drawn from the box with ``seed``), and calls what it finds a local minimum: never global. It
  counts the different minima the starts found;
* ``global_search`` runs differential evolution (integer variables included), or a backend registered with
  :func:`register_backend`, and calls what it finds the best point found;
* the answer is re-evaluated at the returned point by the interpreter, not taken from the search; its bounds and whole
  numbers are checked; ``minimise`` also checks the projected gradient (the first-order condition) on a smooth
  expression, and looks around the point (:func:`lower_neighbour`: the gradient's own neighbours, then a short step
  along each axis and in seeded random directions, inside the box): a lower point there, a zero gradient at a maximum
  or a saddle say, makes the answer ``G6_E_OPTIM_NOT_CONVERGED``, "not shown to be a minimum", with that point in the
  evidence. On an expression that is not smooth no gradient is checked, and the card says SciPy's own success flag is
  the only basis for the word minimum. A search whose value at its point is not what G6 computes there, or whose point
  is outside the box, is refused with ``G6_E_OPTIM_CERTIFICATE_REFUTED``;
* each search counts its evaluations and stops at ``max_evaluations`` or ``time_limit`` (at most 60 seconds), keeping
  the best point it saw; the convergence flag, the stop and the count are in the result.

The best state is ``qualified-draft``: a search's answer is never proven optimal. SciPy is imported only when an op
runs (``pip install 'g6[optim-nonlinear] @ git+https://github.com/basicisabc1994/g6solver'``); without it each op is ``G6_E_OPTIM_SCIPY_UNAVAILABLE``. Not ported: the
hand-written metaheuristics, the unchecked multi-objective search, the function registry, the history store, the
planner, the production gate, pyMetaheuristic and pyMultiobjective (imported, never used), PyGAD's populations,
replays, presets and sessions, and its CSV-reading hyperparameter objective.
"""
from __future__ import annotations

import importlib
import math
import operator
import random
import re
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import Field, model_validator

from g6.optimisation.checks import FEASIBILITY_TOL, INTEGRALITY_TOL, OPTIMALITY_TOL, Check, _done, _scaled
from g6.optimisation.engine import DEFAULT_TIME_LIMIT_S, at_most, parse, run, time_limit
from g6.optimisation.envelope import INPUT_INVALID, INPUT_TOO_LARGE, NO_SOLUTION, SCIPY_UNAVAILABLE, SOLVER_ERROR, Refusal
from g6.optimisation.schema import STRUCTURAL_LIMIT, Name, Sense, Variable, _Data

MAX_NODES = 2_000
MAX_DEPTH = 50
MAX_VARIABLES = 50
MAX_TABLE = 1_000
MAX_STARTS = 50
DEFAULT_STARTS = 5
DEFAULT_EVALUATIONS = 20_000
MAX_EVALUATIONS = 200_000
#: The projected gradient's largest component may be this much, times ``max(1, |objective|)``, at a local minimum.
GRADIENT_TOL = 1e-3
#: How far :func:`lower_neighbour` steps from a local method's answer, as a share of each variable's range, and in how
#: many seeded random directions besides the axes.
PROBE_REACH = 1e-3
PROBE_DIRECTIONS = 8
METHODS = ("L-BFGS-B", "Nelder-Mead", "Powell")
DEFAULT_BACKEND = "differential_evolution"
_PENALTY = 1e150   # what a search sees where the objective is not a finite number (its square still fits a float)
_TIGHT = {"L-BFGS-B": {}, "Nelder-Mead": {"xatol": 1e-10, "fatol": 1e-12}, "Powell": {"xtol": 1e-10, "ftol": 1e-12}}
_BUDGET = {"L-BFGS-B": "maxfun", "Nelder-Mead": "maxfev", "Powell": "maxfev"}
_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")

_UNARY: dict[str, Callable[[float], float]] = {
    "neg": operator.neg, "abs": abs, "sqrt": math.sqrt, "exp": math.exp, "log": math.log, "sin": math.sin,
    "cos": math.cos, "tan": math.tan, "tanh": math.tanh, "atan": math.atan}
_BINARY: dict[str, Callable[[float, float], float]] = {"/": operator.truediv, "^": math.pow}
_NARY: dict[str, Callable[[Sequence[float]], float]] = {"+": math.fsum, "*": math.prod, "min": min, "max": max}
_NOT_SMOOTH = frozenset({"abs", "min", "max", "table"})

Node = tuple  # ("const", value) | ("var", index) | ("table", constants, child) | (operation, child, ...)
#: ``run(objective, bounds, integer, *, seed, max_evaluations) -> {x, fun, converged, message, iterations[, version]}``
SearchBackend = Callable[..., Mapping[str, Any]]


class SearchProblem(_Data):
    """The variables of a search: each with finite bounds; an integer one with a whole number between them."""

    variables: dict[Name, Variable] = Field(min_length=1, max_length=STRUCTURAL_LIMIT)
    sense: Sense = "minimise"

    @property
    def maximise(self) -> bool:
        return self.sense in ("maximise", "maximize")

    @model_validator(mode="after")
    def _boxed(self) -> SearchProblem:
        for name, var in self.variables.items():
            if var.lb is None or var.ub is None:
                raise ValueError(f"variable {name!r} needs a finite lb and ub: a search runs inside a box")
            if var.lb > var.ub:
                raise ValueError(f"variable {name!r} has lb {var.lb:g} above ub {var.ub:g}")
            if var.integer and math.ceil(var.lb) > math.floor(var.ub):
                raise ValueError(f"integer variable {name!r} has no whole number between {var.lb:g} and {var.ub:g}")
        return self


# -- the expression language -------------------------------------------------------------------------------------------

class _Parser:
    def __init__(self, names: list[str]) -> None:
        self.index = {name: i for i, name in enumerate(names)}
        self.nodes = 0
        self.smooth = True

    def node(self, raw: Any, where: str, depth: int) -> tuple[Node, bool]:
        """The parsed node and whether it names a variable."""
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise Refusal(INPUT_TOO_LARGE, f"the expression has more than {MAX_NODES} nodes")
        if depth > MAX_DEPTH:
            raise Refusal(INPUT_TOO_LARGE, f"the expression is nested more than {MAX_DEPTH} deep (at {where})")
        if isinstance(raw, bool) or not isinstance(raw, int | float | str | list):
            raise Refusal(INPUT_INVALID, f"{where}: each part of the expression is a number, a variable name or a list "
                                         f"[operation, argument, ...], not {type(raw).__name__}")
        if isinstance(raw, int | float):
            if not math.isfinite(raw):
                raise Refusal(INPUT_INVALID, f"{where}: a constant must be a finite number")
            return ("const", float(raw)), False
        if isinstance(raw, str):
            if raw not in self.index:
                raise Refusal(INPUT_INVALID, f"{where}: {raw[:60]!r} is not a declared variable (a string names a "
                                             "variable; nothing in the expression is run as code)")
            return ("var", self.index[raw]), True
        if not raw or not isinstance(raw[0], str):
            raise Refusal(INPUT_INVALID, f"{where}: a list starts with the name of its operation")
        head, args = raw[0], raw[1:]
        if head == "table":
            return self._table(args, where, depth)
        if head not in _UNARY and head not in _BINARY and head not in _NARY and head != "-":
            raise Refusal(INPUT_INVALID, f"{where}: unknown operation {head[:30]!r}; use one of "
                                         + ", ".join([*_NARY, "-", *_BINARY, *_UNARY, "table"]))
        if head in _NARY and not args:
            raise Refusal(INPUT_INVALID, f"{where}: {head!r} takes at least one argument")
        if head in _BINARY and len(args) != 2:
            raise Refusal(INPUT_INVALID, f"{where}: {head!r} takes two arguments")
        if head in _UNARY and len(args) != 1:
            raise Refusal(INPUT_INVALID, f"{where}: {head!r} takes one argument")
        if head == "-" and len(args) not in (1, 2):
            raise Refusal(INPUT_INVALID, f"{where}: '-' takes one argument (negation) or two")
        self.smooth = self.smooth and head not in _NOT_SMOOTH
        parsed = [self.node(arg, f"{where}[{i + 1}]", depth + 1) for i, arg in enumerate(args)]
        children = tuple(self._constant_checked(child, has, f"{where}[{i + 1}]")
                         for i, (child, has) in enumerate(parsed))
        return (head, *children), any(has for _, has in parsed)

    def _table(self, args: list, where: str, depth: int) -> tuple[Node, bool]:
        if (len(args) != 2 or not isinstance(args[0], list) or not 1 <= len(args[0]) <= MAX_TABLE
                or not all(isinstance(c, int | float) and not isinstance(c, bool) and math.isfinite(c)
                           for c in args[0])):
            raise Refusal(INPUT_INVALID, f"{where}: a table is ['table', [constant, ...], index], with 1 to "
                                         f"{MAX_TABLE} finite constants")
        self.smooth = False
        self.nodes += len(args[0])
        child, has = self.node(args[1], f"{where}[2]", depth + 1)
        return ("table", tuple(float(c) for c in args[0]), child), has

    @staticmethod
    def _constant_checked(child: Node, has_variable: bool, where: str) -> Node:
        if not has_variable and child[0] != "const":
            value = interpret(child, ())
            if not math.isfinite(value):
                raise Refusal(INPUT_INVALID, f"{where}: this constant part is not a finite number (it overflows or is "
                                             "undefined)")
            return ("const", value)
        return child


def parse_expression(raw: Any, names: Sequence[str]) -> tuple[Node, dict[str, Any]]:
    """``raw`` read into G6's expression tree over ``names``; a :class:`Refusal` naming the first thing wrong."""
    parser = _Parser(list(names))
    tree, has = parser.node(raw, "expression", 0)
    tree = parser._constant_checked(tree, has, "expression")
    if not has:
        raise Refusal(INPUT_INVALID, "the expression names no variable, so there is nothing to search")
    return tree, {"nodes": parser.nodes, "smooth": parser.smooth}


def _apply(head: str, values: Sequence[float]) -> float:
    if head in _NARY:
        return float(_NARY[head](values))
    if head == "-":
        return -values[0] if len(values) == 1 else values[0] - values[1]
    if head in _BINARY:
        return float(_BINARY[head](values[0], values[1]))
    return float(_UNARY[head](values[0]))


def _pick(table: Sequence[float], index: float) -> float:
    whole = round(index) if math.isfinite(index) else -1
    return table[whole] if abs(index - whole) <= INTEGRALITY_TOL and 0 <= whole < len(table) else math.nan


def interpret(node: Node, point: Sequence[float]) -> float:
    """The objective's value at ``point`` (values in variable order), read straight off the tree: G6's check. Where it
    is undefined or overflows, ``nan`` or ``inf``."""
    try:
        head = node[0]
        if head == "const":
            return node[1]
        if head == "var":
            return float(point[node[1]])
        if head == "table":
            return _pick(node[1], interpret(node[2], point))
        return _apply(head, [interpret(child, point) for child in node[1:]])
    except (ArithmeticError, ValueError):
        return math.nan


def _compiled(node: Node) -> Callable[[Sequence[float]], float]:
    """The same tree as nested closures: the search's evaluator, a separate code path from :func:`interpret`."""
    head = node[0]
    if head == "const":
        value = node[1]
        return lambda point: value
    if head == "var":
        at = node[1]
        return lambda point: float(point[at])
    if head == "table":
        table, inner = node[1], _compiled(node[2])
        return lambda point: _pick(table, inner(point))
    parts = [_compiled(child) for child in node[1:]]
    return lambda point: _apply(head, [part(point) for part in parts])


def _safe(fn: Callable[[Sequence[float]], float]) -> Callable[[Sequence[float]], float]:
    def evaluate(point: Sequence[float]) -> float:
        try:
            return fn(point)
        except (ArithmeticError, ValueError):
            return math.nan
    return evaluate


# -- running a search --------------------------------------------------------------------------------------------------

class _Stop(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Objective:
    """What a search calls: the compiled objective in the search's sense, counted, bounded in evaluations and time,
    remembering the best point it was shown."""

    def __init__(self, tree: Node, sign: float, limit: int, deadline: float) -> None:
        self._fn, self._sign, self._limit, self._deadline = _safe(_compiled(tree)), sign, limit, deadline
        self.count = 0
        self.best: tuple[float, list[float]] | None = None

    def __call__(self, x: Sequence[float]) -> float:
        if self.count >= self._limit:
            raise _Stop("the evaluation budget")
        if time.perf_counter() > self._deadline:
            raise _Stop("the time limit")
        self.count += 1
        point = [float(v) for v in x]
        value = self._sign * self._fn(point)
        if not math.isfinite(value):
            return _PENALTY
        if self.best is None or value < self.best[0]:
            self.best = (value, point)
        return value


def _scipy() -> tuple[Any, str]:
    try:
        return importlib.import_module("scipy.optimize"), str(importlib.import_module("scipy").__version__)
    except ImportError as exc:
        raise Refusal(SCIPY_UNAVAILABLE, f"SciPy is not importable here ({exc}); install it with "
                                         "pip install 'g6[optim-nonlinear] @ git+https://github.com/basicisabc1994/g6solver'") from None


def _differential_evolution(objective: Callable[[Sequence[float]], float], bounds: list[tuple[float, float]],
                            integer: list[bool], *, seed: int, max_evaluations: int) -> dict[str, Any]:
    optimize, version = _scipy()
    popsize = 15
    found = optimize.differential_evolution(
        objective, bounds, integrality=integer if any(integer) else None, rng=seed, popsize=popsize,
        maxiter=max(1, max_evaluations // (popsize * len(bounds)) - 1), polish=not any(integer), updating="immediate",
        workers=1)
    return {"x": [float(v) for v in found.x], "fun": float(found.fun), "converged": bool(found.success),
            "message": str(found.message), "iterations": int(found.nit), "version": version}


#: ``global_search``'s backends by name; differential evolution is the default and cannot be replaced.
SEARCH_BACKENDS: dict[str, SearchBackend] = {DEFAULT_BACKEND: _differential_evolution}


def register_backend(name: str, backend: SearchBackend) -> None:
    """Make ``backend`` a ``global_search`` backend under ``name``. Its answer is re-checked like any other."""
    if name == DEFAULT_BACKEND:
        raise ValueError(f"{DEFAULT_BACKEND} is the default backend and cannot be replaced")
    if not _NAME.fullmatch(name) or not callable(backend):
        raise ValueError("a backend needs a name of lower-case letters, digits and underscores, and a callable")
    SEARCH_BACKENDS[name] = backend


def _prepare(variables: Mapping[str, Any], expression: Any, sense: str) -> tuple[SearchProblem, Node, dict[str, Any]]:
    at_most(len(variables or {}), MAX_VARIABLES, "variables")
    problem = parse(SearchProblem, {"variables": variables, "sense": sense})
    tree, info = parse_expression(expression, list(problem.variables))
    return problem, tree, info


def _whole(value: Any, low: int, high: int, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise Refusal(INPUT_INVALID, f"{what} must be a whole number from {low} to {high}, not {value!r}")
    return value


def check_point(problem: SearchProblem, tree: Node, point: Sequence[float], reported: float, *,
                gradient: bool) -> Check:
    """G6's check of a search's answer: the point within its bounds (integers whole), the objective re-evaluated there
    by :func:`interpret` and compared with what the search reported, and, when ``gradient``, the projected gradient
    by central differences (recorded as ``first_order``)."""
    names = list(problem.variables)
    checked = [f"each of the {len(names)} variable(s) within its bounds"]
    failures: list[str] = []
    if len(point) != len(names):
        return _done(checked, [f"the search returned {len(point)} value(s) for {len(names)} variable(s)"])
    if any(v.integer for v in problem.variables.values()):
        checked.append("integer variables at whole numbers")
    for x, (name, var) in zip(point, problem.variables.items()):
        if not math.isfinite(x):
            failures.append(f"{name!r} has no finite value")
            continue
        if x < var.lb - FEASIBILITY_TOL * _scaled(var.lb) or x > var.ub + FEASIBILITY_TOL * _scaled(var.ub):
            failures.append(f"{name!r} = {x:g} is outside its bounds [{var.lb:g}, {var.ub:g}]")
        if var.integer and abs(x - round(x)) > INTEGRALITY_TOL:
            failures.append(f"integer variable {name!r} = {x:g} is not a whole number")
    value = interpret(tree, point)
    checked.append("the objective re-evaluated at the point by G6's own interpreter and compared with the search's "
                   "value")
    details: dict[str, Any] = {"objective": value, "reported": reported}
    if not math.isfinite(value):
        failures.append("the objective is not a finite number at the point")
    elif not math.isfinite(reported) or abs(value - reported) > OPTIMALITY_TOL * _scaled(value, reported):
        failures.append(f"the search reports {reported:g}, but the objective at its point is {value:g}")
    if gradient and not failures:
        norm = _projected_gradient(problem, tree, list(point))
        details |= {"gradient_norm": norm,
                    "first_order": math.isfinite(norm) and norm <= GRADIENT_TOL * max(1.0, abs(value))}
        checked.append("the projected gradient at the point, by central differences (the first-order condition)")
    return _done(checked, failures, tolerance=FEASIBILITY_TOL, **details)


def _projected_gradient(problem: SearchProblem, tree: Node, point: list[float]) -> float:
    sign = -1.0 if problem.maximise else 1.0
    largest = 0.0
    for i, var in enumerate(problem.variables.values()):
        step = 1e-6 * max(1.0, abs(point[i]))
        up, down = min(var.ub, point[i] + step), max(var.lb, point[i] - step)
        if up == down:
            continue
        hi, lo = list(point), list(point)
        hi[i], lo[i] = up, down
        slope = sign * (interpret(tree, hi) - interpret(tree, lo)) / (up - down)
        if not math.isfinite(slope):
            return math.inf
        at_lower = point[i] - var.lb <= FEASIBILITY_TOL * _scaled(var.lb)
        at_upper = var.ub - point[i] <= FEASIBILITY_TOL * _scaled(var.ub)
        if (at_lower and slope > 0) or (at_upper and slope < 0):
            continue   # the bound holds it there
        largest = max(largest, abs(slope))
    return largest


def lower_neighbour(problem: SearchProblem, tree: Node, point: Sequence[float], *,
                    seed: int = 0) -> tuple[int, dict[str, Any] | None]:
    """G6's look around a local method's answer, before anything calls it a minimum: the objective at the +-step
    neighbours the projected gradient uses, at +-:data:`PROBE_REACH` of each variable's range along each axis, and at
    :data:`PROBE_DIRECTIONS` seeded random directions that far, each point kept inside the box. Returns how many points
    were tried, and the best of them (``{values, objective}``, the objective in its own sign) when it is lower, in the
    search's sense, by more than ``OPTIMALITY_TOL`` times ``max(1, |objective|)``; else ``None``."""
    sign = -1.0 if problem.maximise else 1.0
    variables, here = list(problem.variables.values()), list(point)
    value = sign * interpret(tree, here)
    reach = [PROBE_REACH * (v.ub - v.lb) for v in variables]
    probes: list[list[float]] = []
    for i, x in enumerate(here):
        step = 1e-6 * max(1.0, abs(x))
        for delta in (step, -step, reach[i], -reach[i]):
            probes.append([*here[:i], x + delta, *here[i + 1:]])
    draw = random.Random(seed)
    for _ in range(PROBE_DIRECTIONS):
        direction = [draw.gauss(0.0, 1.0) for _ in variables]
        norm = math.sqrt(math.fsum(d * d for d in direction)) or 1.0
        probes.append([x + r * d / norm for x, r, d in zip(here, reach, direction)])
    tried, best = 0, None
    for probe in probes:
        inside = [min(v.ub, max(v.lb, x)) for x, v in zip(probe, variables)]
        if inside == here:
            continue
        tried += 1
        found = sign * interpret(tree, inside)
        if math.isfinite(found) and (best is None or found < best[0]):
            best = (found, inside)
    if best is None or not math.isfinite(value) or best[0] >= value - OPTIMALITY_TOL * _scaled(value):
        return tried, None
    return tried, {"values": dict(zip(problem.variables, best[1])), "objective": sign * best[0]}


def _same_minimum(a: tuple[float, list[float]], b: tuple[float, list[float]], widths: list[float]) -> bool:
    close = abs(a[0] - b[0]) <= 1e-6 * max(1.0, abs(a[0]))
    return close and all(abs(x - y) <= 1e-3 * max(w, 1e-12) for x, y, w in zip(a[1], b[1], widths))


def minimise(variables: Mapping[str, Any], expression: Any, sense: str = "minimise", *, method: str = "L-BFGS-B",
             starts: int = DEFAULT_STARTS, seed: int = 0, x0: Mapping[str, float] | None = None,
             max_evaluations: int = DEFAULT_EVALUATIONS, time_limit: float = DEFAULT_TIME_LIMIT_S,
             request_id: str = "", run_id: str = ""):  # noqa: ANN201 - OptimResult, labelled by finish
    """A local minimum of ``expression`` (maximum with ``sense="maximise"``) over the box, from several starts; see
    the module docstring. Never more than a local minimum, and never ``verified``."""
    seconds = time_limit

    def work() -> dict[str, Any]:
        problem, tree, info = _prepare(variables, expression, sense)
        if method not in METHODS:
            raise Refusal(INPUT_INVALID, f"method must be one of {', '.join(METHODS)}, not {method!r}")
        integers = [name for name, var in problem.variables.items() if var.integer]
        if integers:
            raise Refusal(INPUT_INVALID, f"minimise searches continuous variables, and {integers[0]!r} is an integer; "
                                         "use global_search for integer variables")
        count = _whole(starts, 1, MAX_STARTS, "starts")
        budget = _whole(max_evaluations, 1, MAX_EVALUATIONS, "max_evaluations")
        deadline = time.perf_counter() + _limit(seconds)
        names, box = list(problem.variables), [(v.lb, v.ub) for v in problem.variables.values()]
        points = _starts(problem, x0, count, _whole(seed, 0, 2**32 - 1, "seed"))
        optimize, version = _scipy()
        sign = -1.0 if problem.maximise else 1.0
        objective = _Objective(tree, sign, budget, deadline)
        runs: list[dict[str, Any]] = []
        stopped = ""
        for start in points:
            try:
                found = optimize.minimize(objective, start, method=method, bounds=box,
                                          options={**_TIGHT[method], _BUDGET[method]: budget})
            except _Stop as stop:
                stopped = stop.reason
                break
            runs.append({"start": dict(zip(names, start)), "x": [float(v) for v in found.x],
                         "reported": sign * float(found.fun), "converged": bool(found.success),
                         "message": str(found.message)})
        if stopped and objective.best is not None:
            runs.append({"start": None, "x": objective.best[1], "reported": sign * objective.best[0],
                         "converged": False, "message": f"stopped at {stopped}"})
        scored = [(sign * interpret(tree, r["x"]), r) for r in runs]
        scored = [(v, r) for v, r in scored if math.isfinite(v)]
        if not scored:
            raise Refusal(NO_SOLUTION, "the search found no point where the objective is a finite number")
        best_value, best = min(scored, key=lambda item: item[0])
        widths = [hi - lo for lo, hi in box]
        minima: list[tuple[float, list[float]]] = []
        for value, r in scored:
            if r["converged"] and not any(_same_minimum((value, r["x"]), m, widths) for m in minima):
                minima.append((value, r["x"]))
        check = check_point(problem, tree, best["x"], best["reported"], gradient=info["smooth"])
        tried, lower = lower_neighbour(problem, tree, best["x"], seed=seed) if check.passed else (0, None)
        search = {"kind": "local", "method": method, "starts": len(points), "converged_starts":
                  sum(r["converged"] for r in runs if r["start"] is not None),
                  "agreeing_starts": sum(1 for v, r in scored if r["start"] is not None
                                         and abs(v - best_value) <= 1e-6 * max(1.0, abs(best_value))),
                  "distinct_minima": len(minima), "converged": best["converged"],
                  "first_order": check.details.get("first_order"), "gradient_norm": check.details.get("gradient_norm"),
                  "neighbours_tried": tried, "lower_point": lower,
                  "evaluations": objective.count, "seed": seed, "stopped": stopped, "smooth": info["smooth"],
                  "reported_objective": best["reported"], "runs": runs[:MAX_STARTS + 1]}
        return _answer(problem, info, best["x"], best["message"], version, check, search)

    return run("minimise", f"SciPy minimize ({method})", work, request_id=request_id, run_id=run_id)


def global_search(variables: Mapping[str, Any], expression: Any, sense: str = "minimise", *,
                  backend: str = DEFAULT_BACKEND, seed: int = 0, max_evaluations: int = DEFAULT_EVALUATIONS,
                  time_limit: float = DEFAULT_TIME_LIMIT_S, request_id: str = "",
                  run_id: str = ""):  # noqa: ANN201 - OptimResult, labelled by finish
    """The best point of ``expression`` a global search finds over the box (integer variables included); see the
    module docstring. Never a proven global optimum, and never ``verified``."""
    seconds = time_limit
    engine = "SciPy differential_evolution" if backend == DEFAULT_BACKEND else f"registered backend {backend!r}"

    def work() -> dict[str, Any]:
        problem, tree, info = _prepare(variables, expression, sense)
        runner = SEARCH_BACKENDS.get(backend)
        if runner is None:
            raise Refusal(INPUT_INVALID, f"no search backend named {str(backend)[:60]!r}; use "
                                         + " or ".join(sorted(SEARCH_BACKENDS)))
        budget = _whole(max_evaluations, 1, MAX_EVALUATIONS, "max_evaluations")
        deadline = time.perf_counter() + _limit(seconds)
        sign = -1.0 if problem.maximise else 1.0
        objective = _Objective(tree, sign, budget, deadline)
        box = [(v.lb, v.ub) for v in problem.variables.values()]
        stopped, version = "", "registered" if backend != DEFAULT_BACKEND else ""
        try:
            answer = runner(objective, box, [v.integer for v in problem.variables.values()], seed=seed,
                            max_evaluations=budget)
        except _Stop as stop:
            if objective.best is None:
                raise Refusal(NO_SOLUTION, f"the search stopped at {stop.reason} before it found any point where the "
                                           "objective is a finite number") from None
            stopped, point, reported = stop.reason, objective.best[1], sign * objective.best[0]
            converged, message, iterations = False, f"stopped at {stop.reason}", None
            version = version or _scipy()[1]
        else:
            try:
                answer = dict(answer)
                point, reported = [float(v) for v in answer["x"]], sign * float(answer["fun"])
                converged, message = bool(answer.get("converged")), str(answer.get("message", ""))
                iterations, version = answer.get("iterations"), str(answer.get("version") or version)
            except (KeyError, TypeError, ValueError) as exc:
                raise Refusal(SOLVER_ERROR, f"the backend's answer is not {{x, fun, converged}}: {exc}"[:300]) from None
            if objective.count and objective.best is None:   # what it returned is its penalty, not an answer
                raise Refusal(NO_SOLUTION, f"the search evaluated {objective.count} point(s) and the objective was a "
                                           "finite number at none of them")
        check = check_point(problem, tree, point, reported, gradient=False)
        search = {"kind": "global", "backend": backend, "converged": converged, "evaluations": objective.count,
                  "generations": iterations, "seed": seed, "stopped": stopped, "reported_objective": reported}
        return _answer(problem, info, point, message, version, check, search)

    return run("global_search", engine, work, request_id=request_id, run_id=run_id)


def _limit(seconds: float) -> float:
    return time_limit(seconds)


def _starts(problem: SearchProblem, x0: Mapping[str, float] | None, count: int, seed: int) -> list[list[float]]:
    points: list[list[float]] = []
    if x0 is not None:
        unknown = sorted(set(x0) - set(problem.variables))
        missing = [name for name in problem.variables if name not in x0]
        if unknown or missing:
            raise Refusal(INPUT_INVALID, f"x0 must give each variable once: unknown {unknown}, missing {missing}")
        start = []
        for name, var in problem.variables.items():
            value = x0[name]
            if isinstance(value, bool) or not isinstance(value, int | float) or not var.lb <= value <= var.ub:
                raise Refusal(INPUT_INVALID, f"x0 puts {name!r} at {value!r}, outside its bounds "
                                             f"[{var.lb:g}, {var.ub:g}]")
            start.append(float(value))
        points.append(start)
    draw = random.Random(seed)
    while len(points) < count:
        points.append([draw.uniform(v.lb, v.ub) for v in problem.variables.values()])
    return points


def _answer(problem: SearchProblem, info: dict[str, Any], point: list[float], message: str, version: str, check: Check,
            search: dict[str, Any]) -> dict[str, Any]:
    names = list(problem.variables)
    values = {name: (float(round(x)) if var.integer and math.isfinite(x) else x)
              for x, (name, var) in zip(point, problem.variables.items())}
    return {"engine_version": version, "status": message, "optimal": False, "objective": check.details.get("objective"),
            "solution": {"values": values, "converged": bool(search["converged"])}, "check": check,
            "problem": {"variables": len(names), "integer": sum(v.integer for v in problem.variables.values()),
                        "expression_nodes": info["nodes"], "smooth": info["smooth"], "sense": problem.sense,
                        "bounds": {n: [v.lb, v.ub] for n, v in problem.variables.items()}},
            "search": search}


__all__ = ["DEFAULT_BACKEND", "GRADIENT_TOL", "MAX_DEPTH", "MAX_EVALUATIONS", "MAX_NODES", "MAX_STARTS",
           "MAX_VARIABLES", "METHODS", "PROBE_DIRECTIONS", "PROBE_REACH", "SEARCH_BACKENDS", "SearchProblem",
           "check_point", "global_search", "interpret", "lower_neighbour", "minimise", "parse_expression",
           "register_backend"]
