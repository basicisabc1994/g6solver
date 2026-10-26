# SPDX-License-Identifier: Apache-2.0
"""Assignment, maximum flow and minimum-cost flow on ``ortools.graph``, and vehicle routing on OR-Tools' routing
library; each answer checked by :mod:`g6.optimisation.checks` before it is reported.

The graph algorithms are exact, and G6 shows their optimality itself: a cut of the same capacity for a maximum flow, no
negative cycle in the residual network for an assignment or a minimum-cost flow. The routing library searches
(cheapest-arc start, then local search) and proves nothing, so its routes are checked for validity and reported as not
proven optimal, with no bound.
"""
from __future__ import annotations

from typing import Any

from g6.optimisation import checks
from g6.optimisation.engine import DEFAULT_TIME_LIMIT_S, at_most, ortools, parse, run, version
from g6.optimisation.engine import time_limit as _seconds
from g6.optimisation.envelope import INFEASIBLE, MODEL_INVALID, NO_SOLUTION, SOLVER_ERROR, TIME_LIMIT
from g6.optimisation.schema import (
    AssignmentProblem,
    MaxFlowProblem,
    MinCostFlowProblem,
    OptimResult,
    RoutingProblem,
)

MAX_SIDE = 100
MAX_NODES = 500
MAX_ARCS = 5_000
MAX_ROUTING_NODES = 200
MAX_VEHICLES = 50
ASSIGNMENT = "OR-Tools linear sum assignment"
MAX_FLOW = "OR-Tools max flow"
MIN_COST_FLOW = "OR-Tools min cost flow"
ROUTING = "OR-Tools routing library"


def _graph_failure(status: str, engine: str) -> dict[str, Any]:
    if status == "INFEASIBLE":
        return {"code": INFEASIBLE, "reason": f"{engine} found no answer that meets every requirement (its proof, "
                                              "which G6 does not re-check)"}
    return {"code": MODEL_INVALID, "reason": f"{engine} could not solve the input ({status}); costs or capacities "
                                             "may be too large"}


def _assignment(problem: AssignmentProblem) -> dict[str, Any]:
    """One linear sum assignment: the solver needs a square matrix, so the longer side is assigned against the shorter
    side padded with free dummy rows; the dummies are dropped from the answer."""
    lsa = ortools("ortools.graph.python.linear_sum_assignment")
    costs = problem.costs
    transposed = len(costs) > len(costs[0])
    matrix = [list(col) for col in zip(*costs)] if transposed else [list(row) for row in costs]
    real, side = len(matrix), len(matrix[0])
    solver = lsa.SimpleLinearSumAssignment()
    for i in range(side):
        for j in range(side):
            cost = matrix[i][j] if i < real else 0
            if cost is not None:
                solver.add_arc_with_cost(i, j, cost)
    status = solver.solve()
    raw: dict[str, Any] = {"status": status.name}
    if status == solver.OPTIMAL:
        pairs = [(i, solver.right_mate(i)) for i in range(real)]
        pairs = sorted((j, i) for i, j in pairs) if transposed else pairs
        raw |= {"pairs": pairs, "cost": sum(costs[i][j] for i, j in pairs)}
    return raw


def _assign(problem: AssignmentProblem) -> dict[str, Any]:
    at_most(max(len(problem.costs), len(problem.costs[0])), MAX_SIDE, "rows or columns")
    summary = {"rows": len(problem.costs), "columns": len(problem.costs[0]),
               "forbidden_pairs": sum(c is None for row in problem.costs for c in row)}
    raw = _assignment(problem)
    out: dict[str, Any] = {"engine_version": version(), "status": raw["status"], "problem": summary}
    if "pairs" not in raw:
        return out | _graph_failure(raw["status"], ASSIGNMENT)
    pairs = [{"row": i, "col": j, "cost": problem.costs[i][j]} for i, j in raw["pairs"]]
    return out | {"objective": raw["cost"], "optimal": True, "solution": {"pairs": pairs},
                  "check": checks.check_assignment(problem, raw["pairs"], raw["cost"])}


def _numbered(problem: MaxFlowProblem | MinCostFlowProblem) -> dict[str, int]:
    return {node: i for i, node in enumerate(problem.nodes)}


def _network_size(problem: MaxFlowProblem | MinCostFlowProblem) -> dict[str, Any]:
    at_most(len(problem.nodes), MAX_NODES, "nodes")
    at_most(len(problem.arcs), MAX_ARCS, "arcs")
    return {"nodes": len(problem.nodes), "arcs": len(problem.arcs)}


def _max_flow(problem: MaxFlowProblem) -> dict[str, Any]:
    """One maximum-flow solve: each arc's flow, the value and the source side of a minimum cut."""
    max_flow = ortools("ortools.graph.python.max_flow")
    index = _numbered(problem)
    names = list(index)
    solver = max_flow.SimpleMaxFlow()
    for arc in problem.arcs:
        solver.add_arc_with_capacity(index[arc.tail], index[arc.head], arc.capacity)
    status = solver.solve(index[problem.source], index[problem.sink])
    raw: dict[str, Any] = {"status": status.name}
    if status == solver.OPTIMAL:
        raw |= {"value": int(solver.optimal_flow()), "flows": [int(solver.flow(k)) for k in range(len(problem.arcs))],
                "source_side": [names[i] for i in solver.get_source_side_min_cut()]}
    return raw


def _flow_most(problem: MaxFlowProblem) -> dict[str, Any]:
    summary = _network_size(problem)
    raw = _max_flow(problem)
    out: dict[str, Any] = {"engine_version": version(), "status": raw["status"], "problem": summary}
    if "flows" not in raw:
        return out | _graph_failure(raw["status"], MAX_FLOW)
    check = checks.check_max_flow(problem, raw["flows"], raw["value"], raw["source_side"])
    return out | {"objective": raw["value"], "optimal": True, "check": check,
                  "solution": {"value": raw["value"], "flows": raw["flows"], "min_cut": raw["source_side"]}}


def _min_cost_flow(problem: MinCostFlowProblem) -> dict[str, Any]:
    """One minimum-cost flow solve: each arc's flow and the total cost."""
    min_cost_flow = ortools("ortools.graph.python.min_cost_flow")
    index = _numbered(problem)
    solver = min_cost_flow.SimpleMinCostFlow()
    for arc in problem.arcs:
        solver.add_arc_with_capacity_and_unit_cost(index[arc.tail], index[arc.head], arc.capacity, arc.cost)
    for node, supply in problem.supplies.items():
        if node in index:
            solver.set_node_supply(index[node], supply)
    status = solver.solve()
    raw: dict[str, Any] = {"status": status.name}
    if status == solver.OPTIMAL:
        raw |= {"cost": int(solver.optimal_cost()), "flows": [int(solver.flow(k)) for k in range(len(problem.arcs))]}
    return raw


def _flow_cheapest(problem: MinCostFlowProblem) -> dict[str, Any]:
    summary = _network_size(problem)
    raw = _min_cost_flow(problem)
    out: dict[str, Any] = {"engine_version": version(), "status": raw["status"], "problem": summary}
    if "flows" not in raw:
        return out | _graph_failure(raw["status"], MIN_COST_FLOW)
    return out | {"objective": raw["cost"], "optimal": True, "solution": {"flows": raw["flows"]},
                  "check": checks.check_min_cost_flow(problem, raw["flows"], raw["cost"])}


def _routing(problem: RoutingProblem, seconds: float) -> dict[str, Any]:
    """One routing search: the status, each vehicle's route as node numbers, and the total distance."""
    pywrapcp = ortools("ortools.constraint_solver.pywrapcp")
    enums = ortools("ortools.constraint_solver.routing_enums_pb2")
    n, depot = len(problem.distances), problem.depot
    manager = pywrapcp.RoutingIndexManager(n, problem.vehicles, depot)
    routing = pywrapcp.RoutingModel(manager)
    distances, times = problem.distances, problem.times

    def leg(i: int, j: int) -> int:
        return distances[manager.IndexToNode(i)][manager.IndexToNode(j)]

    def travel(i: int, j: int) -> int:
        return times[manager.IndexToNode(i)][manager.IndexToNode(j)]

    def demand(i: int) -> int:
        return problem.demands[manager.IndexToNode(i)]

    routing.SetArcCostEvaluatorOfAllVehicles(routing.RegisterTransitCallback(leg))
    if problem.demands is not None:
        routing.AddDimensionWithVehicleCapacity(routing.RegisterUnaryTransitCallback(demand), 0,
                                                list(problem.capacities), True, "Load")
    if problem.time_windows is not None:
        horizon = max(end for _, end in problem.time_windows)
        routing.AddDimension(routing.RegisterTransitCallback(travel), horizon, horizon, False, "Time")
        clock = routing.GetDimensionOrDie("Time")
        for node, (opens, closes) in enumerate(problem.time_windows):
            if node != depot:
                clock.CumulVar(manager.NodeToIndex(node)).SetRange(opens, closes)
        opens, closes = problem.time_windows[depot]
        for v in range(problem.vehicles):
            clock.CumulVar(routing.Start(v)).SetRange(opens, closes)
            clock.CumulVar(routing.End(v)).SetRange(opens, closes)
    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = enums.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    if problem.local_search == "guided":
        params.local_search_metaheuristic = enums.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromMilliseconds(int(seconds * 1000))
    solution = routing.SolveWithParameters(params)
    raw: dict[str, Any] = {"status": enums.RoutingSearchStatus.Value.Name(routing.status())}
    if solution is not None:
        routes = []
        for v in range(problem.vehicles):
            index, route = routing.Start(v), []
            while not routing.IsEnd(index):
                route.append(manager.IndexToNode(index))
                index = solution.Value(routing.NextVar(index))
            routes.append([*route, manager.IndexToNode(index)])
        raw |= {"routes": routes, "distance": int(solution.ObjectiveValue())}
    return raw


_ROUTING_FAILURES = {
    "ROUTING_INFEASIBLE": (INFEASIBLE, "the routing library proved that no set of routes meets every requirement"),
    "ROUTING_FAIL": (NO_SOLUTION, "the routing library's search ended without a solution; that is not a proof that "
                                  "none exists"),
    "ROUTING_FAIL_TIMEOUT": (TIME_LIMIT, "the routing library found no solution within the time limit; that is not "
                                         "a proof that none exists"),
    "ROUTING_INVALID": (MODEL_INVALID, "the routing library rejected the model"),
}


def _route(problem: RoutingProblem, seconds: float) -> dict[str, Any]:
    at_most(len(problem.distances), MAX_ROUTING_NODES, "nodes")
    at_most(problem.vehicles, MAX_VEHICLES, "vehicles")
    summary = {"nodes": len(problem.distances), "vehicles": problem.vehicles, "local_search": problem.local_search,
               "capacities": problem.demands is not None, "time_windows": problem.time_windows is not None,
               "time_limit_s": seconds}
    raw = _routing(problem, seconds)
    out: dict[str, Any] = {"engine_version": version(), "status": raw["status"], "problem": summary}
    if "routes" not in raw:
        code, reason = _ROUTING_FAILURES.get(raw["status"], (SOLVER_ERROR, f"the routing library ended with "
                                                                           f"{raw['status']}"))
        return out | {"code": code, "reason": reason}
    check = checks.check_routes(problem, raw["routes"], raw["distance"])
    details = check.details
    routes = []
    for v, nodes in enumerate(raw["routes"]):
        route: dict[str, Any] = {"vehicle": v, "nodes": nodes}
        for key, values in (("distance", details.get("distances")), ("load", details.get("loads")),
                            ("arrivals", details.get("arrivals"))):
            if values and v < len(values):
                route[key] = values[v]
        routes.append(route)
    return out | {"objective": raw["distance"], "optimal": raw["status"] == "ROUTING_OPTIMAL", "check": check,
                  "solution": {"routes": routes, "distance": raw["distance"]}}


def solve_assignment(costs: list[list[int | None]], *, request_id: str = "", run_id: str = "") -> OptimResult:
    """Pair rows with columns at least total cost (``None`` forbids a pair); checked, its optimality shown by the
    residual network."""
    return run("solve_assignment", ASSIGNMENT, lambda: _assign(parse(AssignmentProblem, {"costs": costs})),
               request_id=request_id, run_id=run_id)


def solve_max_flow(arcs: list[Any], source: Any, sink: Any, *, request_id: str = "", run_id: str = "") -> OptimResult:
    """The most that can flow from ``source`` to ``sink``; checked, its maximality shown by a cut of equal capacity."""
    data = {"arcs": list(arcs), "source": source, "sink": sink}
    return run("solve_max_flow", MAX_FLOW, lambda: _flow_most(parse(MaxFlowProblem, data)), request_id=request_id,
               run_id=run_id)


def solve_min_cost_flow(arcs: list[Any], supplies: dict[Any, int], *, request_id: str = "",
                        run_id: str = "") -> OptimResult:
    """Meet every supply and demand at least cost; checked, its optimality shown by the residual network."""
    data = {"arcs": list(arcs), "supplies": supplies}
    return run("solve_min_cost_flow", MIN_COST_FLOW, lambda: _flow_cheapest(parse(MinCostFlowProblem, data)),
               request_id=request_id, run_id=run_id)


def solve_routing(distances: list[list[int]], *, vehicles: int = 1, depot: int = 0, demands: list[int] | None = None,
                  capacities: list[int] | None = None, time_windows: list[Any] | None = None,
                  travel_times: list[list[int]] | None = None, local_search: str = "descent",
                  time_limit: float = DEFAULT_TIME_LIMIT_S, request_id: str = "", run_id: str = "") -> OptimResult:
    """Routes from a depot that visit every node once, within capacities and time windows if given; checked, and
    never called optimal unless the routing library proves it."""
    data = {"distances": distances, "vehicles": vehicles, "depot": depot, "demands": demands,
            "capacities": capacities, "time_windows": time_windows, "travel_times": travel_times,
            "local_search": local_search}
    return run("solve_routing", ROUTING, lambda: _route(parse(RoutingProblem, data), _seconds(time_limit)),
               request_id=request_id, run_id=run_id)


__all__ = ["ASSIGNMENT", "MAX_ARCS", "MAX_FLOW", "MAX_NODES", "MAX_ROUTING_NODES", "MAX_SIDE", "MAX_VEHICLES",
           "MIN_COST_FLOW", "ROUTING", "solve_assignment", "solve_max_flow", "solve_min_cost_flow", "solve_routing"]
