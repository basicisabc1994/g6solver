# SPDX-License-Identifier: Apache-2.0
"""Two reference planners for a point body. Every plan either returns goes through ``check_plan`` before it leaves.

* :func:`plan_astar`: A* over a grid of cells in 2-D (rule ``a_star``). A cell is blocked when any part of it comes
  within reach of an obstacle (the obstacle's radius plus the body's radius and clearance) or leaves the workspace
  less the body's radius, so an obstacle's whole footprint is blocked, not the one cell at its centre. Moves go to
  the eight neighbours, a diagonal one only when the two cells beside it are free too, so no move cuts a corner.
* :func:`plan_rrt`: a rapidly exploring random tree in 2-D or 3-D (rule ``rrt``), drawn from ``random.Random(seed)``
  so a plan can be repeated. Each new edge is checked whole before it is kept, and the goal itself is the last
  waypoint, reached by an edge that is checked too.

Both time the path at the caller's ``speed``. Their output is a reference: a planner that finds nothing has not shown
that no path exists, and a path it finds is only as good as the check that follows it.
"""
from __future__ import annotations

import datetime as _dt
import heapq
import math
import random
from typing import Any

from g6.embodied._motion import geometry as geo
from g6.embodied._motion.checks import check_plan, point_segment_clear
from g6.embodied._motion.envelope import CANNOT_ASSESS, INPUT_INVALID, NO_PATH, MotionResult, refuse
from g6.embodied._motion.model import Goal, Plan, PointBody, Waypoint, World, range_problems, range_reason
from g6.embodied._motion.sources import cited

#: The most cells one A* grid may have (a bound on the work, not on a robot).
MAX_CELLS = 250_000
#: The most iterations one RRT may run.
MAX_ITERATIONS = 5000


def _timed(points: list[list[float]], speed: float) -> Plan:
    t, out = 0.0, [Waypoint(q=points[0], t=0.0)]
    for a, b in zip(points, points[1:]):
        t += geo.distance(a, b) / speed
        out.append(Waypoint(q=b, t=t))
    return Plan(waypoints=out)


def _point_problems(world: World, body: Any, start: list[float], goal: Goal) -> str:
    if not isinstance(body, PointBody):
        return "the reference planners plan for a point body; check an arm's plan with check_plan"
    if len(start) != world.dimensions:
        return f"start has {len(start)} coordinates in a {world.dimensions}-D world"
    if goal.q is None or len(goal.q) != world.dimensions:
        return f"the goal must be a position q with {world.dimensions} coordinates"
    return ""


def _beyond_range(op: str, world: World, body: PointBody, start: list[float], goal: Goal,
                  ids: dict[str, str]) -> MotionResult | None:
    """The same refusal ``check_plan`` gives for a length or coordinate beyond the range the checks are made for,
    before a planner's own edge checks could meet it."""
    beyond = range_problems(Plan(waypoints=[Waypoint(q=list(start))]), world, body, goal)
    if not beyond:
        return None
    return refuse(op, CANNOT_ASSESS, range_reason(beyond), verdict="cannot_assess", beyond_range=beyond, **ids)


def _with_plan(result: MotionResult, plan: Plan, planner: dict[str, Any]) -> MotionResult:
    data = result.model_dump(mode="json")
    data.update(plan=plan.model_dump(mode="json"), planner=planner)
    data["evidence"] = [*data["evidence"], {"kind": "planner", **planner}]
    return MotionResult.model_validate(data)


def plan_astar(world: World, body: PointBody, start: list[float], goal: Goal, cell_size: float, speed: float, *,
               request_id: str = "", run_id: str = "", today: _dt.date | None = None) -> MotionResult:
    """A reference A* path from ``start`` to ``goal.q`` for a point body in a 2-D world, checked by ``check_plan``."""
    op, ids = "plan_astar", {"request_id": request_id, "run_id": run_id}
    problem = _point_problems(world, body, start, goal) or (
        "" if world.dimensions == 2 else "A* plans in a 2-D world here; use plan_rrt in 3-D")
    if problem:
        return refuse(op, INPUT_INVALID, problem, **ids)
    if beyond := _beyond_range(op, world, body, start, goal, ids):
        return beyond
    lo, hi = world.lower, world.upper
    nx, ny = (max(1, math.ceil((hi[k] - lo[k]) / cell_size)) for k in (0, 1))
    if nx * ny > MAX_CELLS:
        return refuse(op, INPUT_INVALID, f"a cell_size of {cell_size} m makes {nx * ny} cells, more than {MAX_CELLS}; "
                                         "use a larger cell_size", **ids)
    reach = [(ob.centre, ob.radius + body.radius + body.clearance) for ob in world.obstacles]
    inner_lo = [v + body.radius + geo.TOLERANCE for v in lo]
    inner_hi = [v - body.radius - geo.TOLERANCE for v in hi]
    known: dict[tuple[int, int], bool] = {}

    def box(c: tuple[int, int]) -> tuple[float, float, float, float]:
        x0, y0 = lo[0] + c[0] * cell_size, lo[1] + c[1] * cell_size
        return x0, min(x0 + cell_size, hi[0]), y0, min(y0 + cell_size, hi[1])

    def free(c: tuple[int, int]) -> bool:
        if not (0 <= c[0] < nx and 0 <= c[1] < ny):
            return False
        if c not in known:
            x0, x1, y0, y1 = box(c)
            ok = x0 >= inner_lo[0] and x1 <= inner_hi[0] and y0 >= inner_lo[1] and y1 <= inner_hi[1]
            for centre, r in reach if ok else ():
                nearest = (min(max(centre[0], x0), x1), min(max(centre[1], y0), y1))
                if geo.distance(nearest, centre) < r + geo.TOLERANCE:
                    ok = False
                    break
            known[c] = ok
        return known[c]

    def middle(c: tuple[int, int]) -> list[float]:
        x0, x1, y0, y1 = box(c)
        return [(x0 + x1) / 2, (y0 + y1) / 2]

    def cell_of(p: list[float]) -> tuple[int, int]:
        return (min(nx - 1, max(0, math.floor((p[0] - lo[0]) / cell_size))),
                min(ny - 1, max(0, math.floor((p[1] - lo[1]) / cell_size))))

    first, last = cell_of(start), cell_of(goal.q)
    for name, c in (("start", first), ("goal", last)):
        if not free(c):
            return refuse(op, NO_PATH, f"the {name}'s cell is blocked at a cell_size of {cell_size} m (part of it is "
                                       "within reach of an obstacle or the workspace edge); try a smaller cell_size",
                          **ids)
    cost = {first: 0.0}
    came: dict[tuple[int, int], tuple[int, int] | None] = {first: None}
    frontier = [(geo.distance(middle(first), middle(last)), 0.0, first)]
    expanded = 0
    while frontier:
        _, g, c = heapq.heappop(frontier)
        if g > cost.get(c, math.inf):
            continue
        expanded += 1
        if c == last:
            break
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                n = (c[0] + di, c[1] + dj)
                if (di, dj) == (0, 0) or not free(n):
                    continue
                if di and dj and not (free((c[0] + di, c[1])) and free((c[0], c[1] + dj))):
                    continue
                step = g + geo.distance(middle(c), middle(n))
                if step < cost.get(n, math.inf):
                    cost[n], came[n] = step, c
                    heapq.heappush(frontier, (step + geo.distance(middle(n), middle(last)), step, n))
    if last not in came:
        return refuse(op, NO_PATH, f"A* found no path at a cell_size of {cell_size} m ({expanded} cells expanded); a "
                                   "finer grid may find one", **ids)
    cells, c = [], last
    while c is not None:
        cells.append(c)
        c = came[c]
    points = [list(start)] + [middle(c) for c in reversed(cells)] + [list(goal.q)]
    points = [p for k, p in enumerate(points) if k == 0 or p != points[k - 1]]
    plan = _timed(points, speed)
    planner = {"planner": "a_star", "cell_size": cell_size, "cells": nx * ny, "expanded": expanded,
               "rules": cited(("a_star", "bounds"))}
    return _with_plan(check_plan(plan, world, body, goal, operation=op, today=today, **ids), plan, planner)


def plan_rrt(world: World, body: PointBody, start: list[float], goal: Goal, step: float, seed: int, speed: float, *,
             max_iterations: int = 1000, goal_bias: float = 0.1, request_id: str = "", run_id: str = "",
             today: _dt.date | None = None) -> MotionResult:
    """A reference RRT path from ``start`` to ``goal.q`` for a point body in 2-D or 3-D, checked by ``check_plan``.
    ``seed`` makes it repeatable; ``max_iterations`` and ``goal_bias`` tune the search and never a limit."""
    op, ids = "plan_rrt", {"request_id": request_id, "run_id": run_id}
    problem = _point_problems(world, body, start, goal)
    if not problem and not 1 <= max_iterations <= MAX_ITERATIONS:
        problem = f"max_iterations must be between 1 and {MAX_ITERATIONS}"
    if problem:
        return refuse(op, INPUT_INVALID, problem, **ids)
    if beyond := _beyond_range(op, world, body, start, goal, ids):
        return beyond
    lo = [v + body.radius for v in world.lower]
    hi = [v - body.radius for v in world.upper]
    if any(a >= b for a, b in zip(lo, hi)):
        return refuse(op, INPUT_INVALID, "the body, its radius included, does not fit inside the workspace", **ids)
    target, origin = list(goal.q or ()), list(start)
    if not point_segment_clear(origin, origin, body, world):
        return refuse(op, NO_PATH, "the start is already within the body's clearance of an obstacle, or outside the "
                                   "workspace", **ids)
    rng = random.Random(seed)
    nodes, parent = [origin], [-1]
    found = 0 if point_segment_clear(origin, target, body, world) else None
    iterations = 0
    while found is None and iterations < max_iterations:
        iterations += 1
        sample = target if rng.random() < goal_bias else [rng.uniform(a, b) for a, b in zip(lo, hi)]
        near = min(range(len(nodes)), key=lambda k: geo.distance(nodes[k], sample))
        gap = geo.distance(nodes[near], sample)
        if gap == 0.0:
            continue
        new = geo.lerp(nodes[near], sample, min(step, gap) / gap)
        if not point_segment_clear(nodes[near], new, body, world):
            continue
        nodes.append(new)
        parent.append(near)
        if geo.distance(new, target) <= step and point_segment_clear(new, target, body, world):
            found = len(nodes) - 1
    if found is None:
        return refuse(op, NO_PATH, f"the RRT found no path in {iterations} iterations (seed {seed}, step {step} m); "
                                   "this is not evidence that none exists", **ids)
    branch, k = [], found
    while k != -1:
        branch.append(nodes[k])
        k = parent[k]
    points = branch[::-1]
    if points[-1] != target:
        points.append(target)
    plan = _timed(points, speed)
    planner = {"planner": "rrt", "seed": seed, "step": step, "iterations": iterations, "tree_size": len(nodes),
               "goal_bias": goal_bias, "rules": cited(("rrt", "bounds"))}
    return _with_plan(check_plan(plan, world, body, goal, operation=op, today=today, **ids), plan, planner)


__all__ = ["MAX_CELLS", "MAX_ITERATIONS", "plan_astar", "plan_rrt"]
