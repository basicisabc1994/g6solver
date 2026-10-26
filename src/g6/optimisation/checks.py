# SPDX-License-Identifier: Apache-2.0
"""G6's own check of an optimiser's answer, in pure Python, run before the answer is reported.

Each checker takes the problem and the answer exactly as it will be reported, and returns a :class:`Check`: what was
checked, every failure found (the first few, in words), the tolerance and, where the answer's optimality can be shown
without trusting the solver, the certificate that shows it:

* linear programmes: every constraint within the tolerance, the objective recomputed, and optimality from the duals
  (the reduced costs recomputed as ``c - A^T y``, dual feasibility, and no duality gap). Infeasibility is shown by the
  duals of the least-violation programme (:func:`elastic`), unboundedness by a feasible point and an improving ray;
* integer programmes and constraint models: feasibility (integers exact), the objective recomputed and the solver's
  bound on the right side of its own answer. Optimality rests on that bound, which is not re-derived here;
* assignment and minimum-cost flow: a valid matching or a flow that keeps capacities and balances, its cost
  recomputed, and optimality from the residual network having no negative cycle (Bellman-Ford);
* maximum flow: capacities and conservation, and a cut whose capacity equals the flow, which proves it maximal;
* routing: every node visited once, each route from and back to the depot, loads within capacity, the earliest arrival
  at each node within its window, and the distance recomputed;
* job shop: each job's tasks in order, no two tasks at once on a machine, and the makespan recomputed;
* knapsack: every capacity kept and the value recomputed.

Continuous values are compared within :data:`FEASIBILITY_TOL`, scaled by the size of the numbers compared; integers
exactly.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from g6.optimisation.envelope import CERTIFICATE_REFUTED
from g6.optimisation.schema import (
    AssignmentProblem,
    CpProblem,
    JobShopProblem,
    KnapsackProblem,
    LinearConstraint,
    LinearProblem,
    MaxFlowProblem,
    MinCostFlowProblem,
    RoutingProblem,
    Variable,
)

#: A continuous value may miss a bound by this much, times ``max(1, the size of the numbers compared)``.
FEASIBILITY_TOL = 1e-6
#: The same, for an objective recomputed, a reduced cost, a duality gap or a bound.
OPTIMALITY_TOL = 1e-6
#: How far an integer variable's value may be from the nearest integer.
INTEGRALITY_TOL = 1e-6
_SHOWN = 5


@dataclass(frozen=True)
class Check:
    """What G6 checked of one answer, and what it found. ``passed`` only when no failure was found."""

    checked: tuple[str, ...]
    failures: tuple[str, ...] = ()
    tolerance: float | None = None
    certificate: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.failures

    @property
    def code(self) -> str:
        """``G6_E_OPTIM_CERTIFICATE_REFUTED`` when the check failed, else empty."""
        return "" if self.passed else CERTIFICATE_REFUTED

    def as_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "checked": list(self.checked), "failures": list(self.failures),
                "tolerance": self.tolerance, "certificate": self.certificate if self.passed else "",
                "details": self.details}


def _done(checked: list[str], failures: list[str], *, tolerance: float | None = None, certificate: str = "",
          **details: Any) -> Check:
    if len(failures) > _SHOWN:
        details["failures_found"] = len(failures)
    return Check(tuple(checked), tuple(failures[:_SHOWN]), tolerance, certificate, details)


def _scaled(*sizes: float) -> float:
    return max([1.0, *(abs(s) for s in sizes if s is not None)])


def _each(count: int, noun: str, plural: str = "") -> list[str]:
    """``["the constraint"]``, ``["each of the 3 constraints"]``, or nothing to say when there are none."""
    return [] if count == 0 else [f"the {noun}"] if count == 1 else [f"each of the {count} {plural or noun + 's'}"]


# --- linear and integer programmes -------------------------------------------------------------------------------------

def _feasibility(problem: LinearProblem, values: dict[str, float]) -> list[str]:
    failures = []
    for name, var in problem.variables.items():
        x = values.get(name)
        if not isinstance(x, int | float) or not math.isfinite(x):
            failures.append(f"variable {name!r} has no finite value")
            continue
        if var.lb is not None and x < var.lb - FEASIBILITY_TOL * _scaled(var.lb):
            failures.append(f"{name!r} = {x:g} is below its lb {var.lb:g}")
        if var.ub is not None and x > var.ub + FEASIBILITY_TOL * _scaled(var.ub):
            failures.append(f"{name!r} = {x:g} is above its ub {var.ub:g}")
        if var.integer and abs(x - round(x)) > INTEGRALITY_TOL:
            failures.append(f"{name!r} = {x:g} is not an integer")
    if failures:
        return failures
    for i, row in enumerate(problem.constraints):
        parts = [a * values[n] for n, a in row.terms.items()]
        activity = math.fsum(parts)
        tol = FEASIBILITY_TOL * _scaled(math.fsum(abs(p) for p in parts), row.lb, row.ub)
        if row.lb is not None and activity < row.lb - tol:
            failures.append(f"{problem.label(i)} comes to {activity:g}, below its lb {row.lb:g}")
        if row.ub is not None and activity > row.ub + tol:
            failures.append(f"{problem.label(i)} comes to {activity:g}, above its ub {row.ub:g}")
    return failures


def _objective_failures(recomputed: float, reported: float | None) -> list[str]:
    if reported is None or not math.isfinite(reported):
        return ["the solver reported no objective value"]
    if abs(recomputed - reported) > OPTIMALITY_TOL * _scaled(recomputed):
        return [f"the objective recomputes to {recomputed:g}, not the reported {reported:g}"]
    return []


def _bound_failures(maximise: bool, objective: float | None, bound: float | None) -> list[str]:
    if objective is None or bound is None or not math.isfinite(bound):
        return []
    slack = OPTIMALITY_TOL * _scaled(objective)
    if maximise and bound < objective - slack:
        return [f"the solver's bound {bound:g} is below its own answer {objective:g}, impossible when maximising"]
    if not maximise and bound > objective + slack:
        return [f"the solver's bound {bound:g} is above its own answer {objective:g}, impossible when minimising"]
    return []


def _linear_checked(problem: LinearProblem, objective: bool = True) -> list[str]:
    integers = " and integer where declared" if any(v.integer for v in problem.variables.values()) else ""
    found = [f"{line} within its bounds{integers}" for line in _each(len(problem.variables), "variable")]
    return found + _each(len(problem.constraints), "constraint") + (["the objective recomputed"] if objective else [])


def check_feasible(problem: LinearProblem, values: dict[str, float]) -> Check:
    """``values`` meets every bound and constraint of ``problem`` (no objective)."""
    return _done(_linear_checked(problem, objective=False), _feasibility(problem, values), tolerance=FEASIBILITY_TOL)


def check_mip(problem: LinearProblem, values: dict[str, float], objective: float | None,
              bound: float | None = None) -> Check:
    """Feasibility (integrality included), the objective recomputed, and the bound on the right side of it."""
    failures = _feasibility(problem, values)
    recomputed = None
    if not failures:
        recomputed = math.fsum(a * values[n] for n, a in problem.objective.items())
        failures += _objective_failures(recomputed, objective)
        failures += _bound_failures(problem.maximise, objective, bound)
    checked = _linear_checked(problem) + (["the bound on the right side of the answer"] if bound is not None else [])
    return _done(checked, failures, tolerance=FEASIBILITY_TOL, recomputed_objective=recomputed)


def check_lp(problem: LinearProblem, values: dict[str, float], objective: float | None, duals: list[float],
             reduced_costs: dict[str, float] | None = None) -> Check:
    """An optimum of a linear programme, certified by its duals ``y`` (one per constraint, in the solver's sign).

    In the minimising form (a maximisation's objective, duals and reduced costs negated) the reduced costs are
    ``d = c - A^T y``; a positive dual or reduced cost must press on a finite lower bound and a negative one on a finite
    upper bound; and then the dual objective ``sum(y_i * bound_i) + sum(d_j * bound_j)`` is a lower bound on every
    feasible point, so a primal objective equal to it is optimal.
    """
    failures = _feasibility(problem, values)
    if len(duals) != len(problem.constraints):
        failures.append(f"{len(duals)} duals for {len(problem.constraints)} constraints")
    recomputed = dual_objective = None
    if not failures:
        recomputed = math.fsum(a * values[n] for n, a in problem.objective.items())
        failures += _objective_failures(recomputed, objective)
    if not failures:
        sign = -1.0 if problem.maximise else 1.0
        cost = {n: sign * problem.objective.get(n, 0.0) for n in problem.variables}
        y = [sign * float(d) for d in duals]
        d = dict(cost)
        for yi, row in zip(y, problem.constraints):
            for n, a in row.terms.items():
                d[n] -= yi * a
        zero = OPTIMALITY_TOL * _scaled(*cost.values(), *y)
        for n, given in (reduced_costs or {}).items():
            if n in d and abs(sign * given - d[n]) > zero:
                failures.append(f"the solver's reduced cost for {n!r} ({given:g}) is not c - A^T y ({sign * d[n]:g})")
        parts: list[float] = []
        pressing = [(problem.label(i), yi, row.lb, row.ub) for i, (yi, row) in enumerate(zip(y, problem.constraints))]
        pressing += [(f"variable {n!r}", d[n], var.lb, var.ub) for n, var in problem.variables.items()]
        for label, value, lb, ub in pressing:
            if value > zero:
                if lb is None:
                    failures.append(f"dual infeasible: {label} has the dual value {sign * value:g}, which presses on "
                                    "a lower bound it does not have")
                else:
                    parts.append(value * lb)
            elif value < -zero:
                if ub is None:
                    failures.append(f"dual infeasible: {label} has the dual value {sign * value:g}, which presses on "
                                    "an upper bound it does not have")
                else:
                    parts.append(value * ub)
        if not failures:
            dual = math.fsum(parts)
            gap = sign * recomputed - dual
            dual_objective = sign * dual
            if abs(gap) > OPTIMALITY_TOL * _scaled(recomputed, dual, math.fsum(abs(p) for p in parts)):
                failures.append(f"duality gap {abs(gap):g}: the duals bound the optimum at {dual_objective:g}, not "
                                f"{recomputed:g}")
    checked = _linear_checked(problem) + ["the reduced costs recomputed as c - A^T y", "dual feasibility",
                                          "no duality gap"]
    return _done(checked, failures, tolerance=FEASIBILITY_TOL, certificate="duals", recomputed_objective=recomputed,
                 dual_objective=dual_objective)


def elastic(problem: LinearProblem) -> LinearProblem:
    """The least-violation programme: constraint ``i`` may be missed by ``over:i`` or ``under:i`` (both at least 0), and
    their sum is minimised. Its optimum is the least total violation; above zero, ``problem`` is infeasible."""
    variables = {f"x:{n}": Variable(lb=v.lb, ub=v.ub) for n, v in problem.variables.items()}
    rows, objective = [], {}
    for i, row in enumerate(problem.constraints):
        variables[f"over:{i}"] = variables[f"under:{i}"] = Variable()
        objective[f"over:{i}"] = objective[f"under:{i}"] = 1.0
        terms = {f"x:{n}": a for n, a in row.terms.items()} | {f"over:{i}": -1.0, f"under:{i}": 1.0}
        rows.append(LinearConstraint(name=row.name, terms=terms, lb=row.lb, ub=row.ub))
    return LinearProblem(variables=variables, constraints=rows, objective=objective, sense="minimise")


def check_infeasible(problem: LinearProblem, values: dict[str, float], objective: float | None,
                     duals: list[float]) -> Check:
    """``problem`` has no feasible point: the least-violation programme's optimum, certified by its duals, exceeds what
    the feasibility tolerance would forgive on every constraint together."""
    inner = check_lp(elastic(problem), values, objective, duals)
    least = inner.details.get("dual_objective")
    forgiven = FEASIBILITY_TOL * math.fsum(_scaled(row.lb, row.ub) for row in problem.constraints)
    failures = list(inner.failures)
    if not failures and not (least is not None and least > forgiven):
        failures.append(f"the least total violation is {least:g}, within the tolerance, so the constraints can be met")
    return _done(["the least-violation programme's optimum, bounded from below by its duals", "a total violation "
                  "above the tolerance"], failures, tolerance=FEASIBILITY_TOL, certificate="elastic_duals",
                 least_violation=least)


def ray_problem(problem: LinearProblem) -> LinearProblem:
    """The directions ``d`` that keep every constraint however far one goes (the recession cone), within the box
    ``[-1, 1]``, optimising the same objective. A strictly improving optimum is an improving ray."""
    variables = {n: Variable(lb=0.0 if v.lb is not None else -1.0, ub=0.0 if v.ub is not None else 1.0)
                 for n, v in problem.variables.items()}
    rows = [LinearConstraint(name=row.name, terms=dict(row.terms), lb=0.0 if row.lb is not None else None,
                             ub=0.0 if row.ub is not None else None) for row in problem.constraints]
    return LinearProblem(variables=variables, constraints=rows, objective=dict(problem.objective), sense=problem.sense)


def check_ray(problem: LinearProblem, direction: dict[str, float]) -> Check:
    """``direction`` keeps every bound and constraint of ``problem`` however far one moves along it, and improves the
    objective."""
    failures = []
    for name, var in problem.variables.items():
        d = direction.get(name, 0.0)
        if var.lb is not None and d < -FEASIBILITY_TOL:
            failures.append(f"the ray lowers {name!r} ({d:g}) past its lb {var.lb:g}")
        if var.ub is not None and d > FEASIBILITY_TOL:
            failures.append(f"the ray raises {name!r} ({d:g}) past its ub {var.ub:g}")
    for i, row in enumerate(problem.constraints):
        change = math.fsum(a * direction.get(n, 0.0) for n, a in row.terms.items())
        if row.lb is not None and change < -FEASIBILITY_TOL:
            failures.append(f"the ray takes {problem.label(i)} below its lb")
        if row.ub is not None and change > FEASIBILITY_TOL:
            failures.append(f"the ray takes {problem.label(i)} above its ub")
    gain = math.fsum(a * direction.get(n, 0.0) for n, a in problem.objective.items())
    if (gain if problem.maximise else -gain) <= OPTIMALITY_TOL:
        failures.append(f"the ray does not improve the objective (it changes it by {gain:g} a step)")
    return _done(["the ray keeps every bound", "the ray keeps every constraint", "the ray improves the objective"],
                 failures, tolerance=FEASIBILITY_TOL, certificate="ray", gain_per_step=gain)


def check_unbounded(problem: LinearProblem, point: dict[str, float], direction: dict[str, float]) -> Check:
    """``problem`` is unbounded: ``point`` is feasible and ``direction`` is an improving ray from it."""
    feasible, ray = check_feasible(problem, point), check_ray(problem, direction)
    return _done(["a feasible point", *ray.checked], [*feasible.failures, *ray.failures], tolerance=FEASIBILITY_TOL,
                 certificate="ray", gain_per_step=ray.details["gain_per_step"])


# --- constraint models, knapsack, job shop -----------------------------------------------------------------------------

def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) or isinstance(value, float) and value.is_integer()


def check_cp(problem: CpProblem, values: dict[str, int], objective: float | None, bound: float | None = None) -> Check:
    """Exactly, in integers: every variable within its bounds, every constraint, every all-different group, the
    objective recomputed and the bound on the right side of it."""
    failures = []
    for name, var in problem.variables.items():
        x = values.get(name)
        if not _integer(x):
            failures.append(f"variable {name!r} has no integer value ({x!r})")
        elif not math.ceil(var.lb) <= x <= math.floor(var.ub):
            failures.append(f"{name!r} = {int(x)} is outside [{var.lb:g}, {var.ub:g}]")
    recomputed = None
    if not failures:
        ints = {n: int(x) for n, x in values.items()}
        for i, row in enumerate(problem.constraints):
            activity = sum(int(a) * ints[n] for n, a in row.terms.items())
            if (row.lb is not None and activity < row.lb) or (row.ub is not None and activity > row.ub):
                failures.append(f"{problem.label(i)} comes to {activity}, outside [{row.lb}, {row.ub}]")
        for group in problem.all_different:
            taken = [ints[n] for n in group]
            if len(set(taken)) != len(taken):
                failures.append(f"all_different {group} has repeated values {taken}")
        if problem.objective:
            recomputed = sum(int(a) * ints[n] for n, a in problem.objective.items())
            failures += _objective_failures(recomputed, objective)
            failures += _bound_failures(problem.maximise, objective, bound)
    checked = [f"{line} an integer within its bounds" for line in _each(len(problem.variables), "variable")]
    checked += _each(len(problem.constraints), "constraint") + _each(len(problem.all_different), "all-different group")
    checked += ["the objective recomputed", "the bound on the right side of the answer"] if problem.objective else []
    return _done(checked, failures, tolerance=0.0, recomputed_objective=recomputed)


def check_knapsack(problem: KnapsackProblem, chosen: list[int], value: int) -> Check:
    """The chosen items fit every capacity, and their value recomputes."""
    failures = []
    if len(set(chosen)) != len(chosen) or any(not 0 <= i < len(problem.values) for i in chosen):
        failures.append(f"the chosen items {chosen} repeat an item or name one that does not exist")
    loads = []
    if not failures:
        for d, (row, capacity) in enumerate(zip(problem.weights, problem.capacities)):
            load = sum(row[i] for i in chosen)
            loads.append(load)
            if load > capacity:
                failures.append(f"weight dimension {d} carries {load}, over its capacity {capacity}")
        total = sum(problem.values[i] for i in chosen)
        if total != value:
            failures.append(f"the value recomputes to {total}, not the reported {value}")
    return _done([*_each(len(problem.capacities), "capacity", "capacities"), "the value recomputed"], failures,
                 tolerance=0.0, loads=loads)


def check_job_shop(problem: JobShopProblem, starts: list[list[int]], makespan: int, bound: float | None = None) -> Check:
    """Each job's tasks in order, no machine running two tasks at once, the makespan recomputed, and the bound at or
    below it."""
    failures = []
    if [len(job) for job in starts] != [len(job) for job in problem.jobs]:
        return _done(["the schedule's shape"], ["the schedule does not give one start per task"], tolerance=0.0)
    busy: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    end = 0
    for j, (job, times) in enumerate(zip(problem.jobs, starts)):
        ready = 0
        for t, (task, start) in enumerate(zip(job, times)):
            if not _integer(start) or start < 0:
                failures.append(f"job {j} task {t} starts at {start!r}, not a time from 0")
                continue
            if start < ready:
                failures.append(f"job {j} task {t} starts at {start}, before the task before it ends at {ready}")
            ready = int(start) + task.duration
            end = max(end, ready)
            if task.duration:
                busy[task.machine].append((int(start), ready, f"job {j} task {t}"))
    for machine, slots in busy.items():
        slots.sort()
        for (_, first_end, first), (second_start, _, second) in zip(slots, slots[1:]):
            if second_start < first_end:
                failures.append(f"machine {machine!r} runs {first} and {second} at once")
    if end != makespan:
        failures.append(f"the makespan recomputes to {end}, not the reported {makespan}")
    failures += _bound_failures(False, makespan, bound)
    return _done(["each job's tasks in order", "no machine running two tasks at once", "the makespan recomputed"],
                 failures, tolerance=0.0, recomputed_makespan=end)


# --- networks ----------------------------------------------------------------------------------------------------------

def _negative_cycle(arcs: list[tuple[Any, Any, int, int, int]]) -> bool:
    """Whether the residual network of a flow has a negative-cost cycle: Bellman-Ford from a virtual source joined to
    every node at cost 0. Each arc is ``(tail, head, capacity, cost, flow)``; costs are integers, so this is exact."""
    edges = []
    for tail, head, capacity, cost, flow in arcs:
        if flow < capacity:
            edges.append((tail, head, cost))
        if flow > 0:
            edges.append((head, tail, -cost))
    nodes = {n for edge in edges for n in edge[:2]}
    distance = dict.fromkeys(nodes, 0)
    for _ in range(len(nodes) + 1):
        changed = False
        for tail, head, cost in edges:
            if distance[tail] + cost < distance[head]:
                distance[head] = distance[tail] + cost
                changed = True
        if not changed:
            return False
    return True


def check_assignment(problem: AssignmentProblem, pairs: list[tuple[int, int]], cost: int) -> Check:
    """A valid assignment (no row or column twice, only allowed pairs, every row or every column covered), its cost
    recomputed, and its optimality from the residual network of the matching having no negative cycle."""
    rows, cols = len(problem.costs), len(problem.costs[0])
    failures = []
    pairs = [(int(i), int(j)) for i, j in pairs]
    if any(not (0 <= i < rows and 0 <= j < cols) for i, j in pairs):
        failures.append(f"the pairs {pairs} name a row or column that does not exist")
    elif len({i for i, _ in pairs}) != len(pairs) or len({j for _, j in pairs}) != len(pairs):
        failures.append("a row or a column is assigned twice")
    elif len(pairs) != min(rows, cols):
        failures.append(f"{len(pairs)} pairs, not the {min(rows, cols)} that cover the shorter side")
    elif any(problem.costs[i][j] is None for i, j in pairs):
        failures.append("a pair that is not allowed is used")
    if not failures:
        total = sum(problem.costs[i][j] for i, j in pairs)
        if total != cost:
            failures.append(f"the cost recomputes to {total}, not the reported {cost}")
        used, rows_used, cols_used = set(pairs), {i for i, _ in pairs}, {j for _, j in pairs}
        arcs = [("source", ("row", i), 1, 0, int(i in rows_used)) for i in range(rows)]
        arcs += [(("row", i), ("col", j), 1, c, int((i, j) in used))
                 for i, row in enumerate(problem.costs) for j, c in enumerate(row) if c is not None]
        arcs += [(("col", j), "sink", 1, 0, int(j in cols_used)) for j in range(cols)]
        if _negative_cycle(arcs):
            failures.append("a cheaper assignment exists: the residual network has a negative cycle")
    return _done(["no row or column assigned twice, only allowed pairs", "the cost recomputed",
                  "no negative cycle in the residual network"], failures, tolerance=0.0,
                 certificate="no_negative_cycle")


def _balance(problem: MaxFlowProblem | MinCostFlowProblem, flows: list[int]) -> tuple[list[str], dict[str, int]]:
    failures = []
    if len(flows) != len(problem.arcs):
        return [f"{len(flows)} flows for {len(problem.arcs)} arcs"], {}
    net: dict[str, int] = defaultdict(int)
    for k, (arc, flow) in enumerate(zip(problem.arcs, flows)):
        if not _integer(flow) or not 0 <= flow <= arc.capacity:
            failures.append(f"arc {k} ({arc.tail} to {arc.head}) carries {flow!r}, outside [0, {arc.capacity}]")
            continue
        net[arc.tail] -= int(flow)
        net[arc.head] += int(flow)
    return failures, net


def check_max_flow(problem: MaxFlowProblem, flows: list[int], value: int, source_side: list[str]) -> Check:
    """Capacities and conservation kept, ``value`` leaving the source and reaching the sink, and the cut
    ``source_side`` (with the source, without the sink) of exactly that capacity, which proves no flow is larger."""
    failures, net = _balance(problem, flows)
    if not failures:
        for node in problem.nodes:
            want = -value if node == problem.source else value if node == problem.sink else 0
            if net[node] != want:
                failures.append(f"node {node!r} takes in {net[node]} net, not {want}")
    side = set(source_side)
    cut = sum(arc.capacity for arc in problem.arcs if arc.tail in side and arc.head not in side)
    if problem.source not in side or problem.sink in side:
        failures.append("the cut must hold the source and not the sink")
    elif cut != value:
        failures.append(f"the cut has capacity {cut}, not the flow's {value}, so it does not prove the flow maximal")
    return _done(["each arc's flow within its capacity", "conservation at every node", "the flow's value",
                  "a cut whose capacity equals the flow"], failures, tolerance=0.0, certificate="min_cut",
                 cut_capacity=cut)


def check_min_cost_flow(problem: MinCostFlowProblem, flows: list[int], cost: int) -> Check:
    """Capacities kept, every node's supply or demand met exactly, the cost recomputed, and optimality from the
    residual network having no negative cycle."""
    failures, net = _balance(problem, flows)
    if not failures:
        for node in problem.nodes:
            want = -problem.supplies.get(node, 0)
            if net[node] != want:
                failures.append(f"node {node!r} takes in {net[node]} net, not {want}")
        total = sum(arc.cost * int(flow) for arc, flow in zip(problem.arcs, flows))
        if total != cost:
            failures.append(f"the cost recomputes to {total}, not the reported {cost}")
        if _negative_cycle([(a.tail, a.head, a.capacity, a.cost, int(f)) for a, f in zip(problem.arcs, flows)]):
            failures.append("a cheaper flow exists: the residual network has a negative cycle")
    return _done(["each arc's flow within its capacity", "every supply and demand met", "the cost recomputed",
                  "no negative cycle in the residual network"], failures, tolerance=0.0,
                 certificate="no_negative_cycle")


def check_routes(problem: RoutingProblem, routes: list[list[int]], distance: int) -> Check:
    """One route per vehicle from and back to the depot, every other node visited exactly once, loads within capacity,
    the earliest arrival at each node within its window (waiting allowed), and the distance recomputed. The details
    carry each route's distance, load and earliest arrivals."""
    n, depot = len(problem.distances), problem.depot
    if len(routes) != problem.vehicles:
        return _done(["one route per vehicle"], [f"{len(routes)} routes for {problem.vehicles} vehicles"],
                     tolerance=0.0)
    failures = []
    seen: dict[int, int] = defaultdict(int)
    lengths, loads, arrivals = [], [], []
    for v, route in enumerate(routes):
        if len(route) < 2 or route[0] != depot or route[-1] != depot:
            failures.append(f"route {v} does not leave from and return to the depot {depot}")
            continue
        inner = route[1:-1]
        if any(not (_integer(node) and 0 <= node < n) or node == depot for node in inner):
            failures.append(f"route {v} visits a node that is not a customer: {inner}")
            continue
        for node in inner:
            seen[int(node)] += 1
        legs = list(zip(route, route[1:]))
        lengths.append(sum(problem.distances[a][b] for a, b in legs))
        if problem.demands is not None:
            load = sum(problem.demands[node] for node in inner)
            loads.append(load)
            if load > problem.capacities[v]:
                failures.append(f"route {v} carries {load}, over vehicle {v}'s capacity {problem.capacities[v]}")
        if problem.time_windows is not None:
            clock = problem.time_windows[depot][0]
            times = [clock]
            for a, b in legs:
                clock = max(clock + problem.times[a][b], problem.time_windows[b][0] if b != depot else 0)
                times.append(clock)
                if clock > problem.time_windows[b][1]:
                    failures.append(f"route {v} reaches node {b} at {clock} at the earliest, after its window closes "
                                    f"at {problem.time_windows[b][1]}")
            arrivals.append(times)
    for node in range(n):
        if node != depot and seen.get(node, 0) != 1:
            failures.append(f"node {node} is visited {seen.get(node, 0)} times, not once")
    if not failures and sum(lengths) != distance:
        failures.append(f"the distance recomputes to {sum(lengths)}, not the reported {distance}")
    checked = ["one route per vehicle from and back to the depot", "every node visited exactly once",
               "the distance recomputed"]
    checked += ["each vehicle's load within its capacity"] if problem.demands is not None else []
    checked += ["the earliest arrival at each node within its window"] if problem.time_windows is not None else []
    return _done(checked, failures, tolerance=0.0, distances=lengths, loads=loads, arrivals=arrivals)


__all__ = ["Check", "FEASIBILITY_TOL", "INTEGRALITY_TOL", "OPTIMALITY_TOL", "check_assignment", "check_cp",
           "check_feasible", "check_infeasible", "check_job_shop", "check_knapsack", "check_lp", "check_max_flow",
           "check_min_cost_flow", "check_mip", "check_ray", "check_routes", "check_unbounded", "elastic",
           "ray_problem"]
