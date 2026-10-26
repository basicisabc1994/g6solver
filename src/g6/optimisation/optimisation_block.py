# SPDX-License-Identifier: Apache-2.0
"""OptimisationBlock: the registry and MCP door (``g6_invoke``) to g6.optimisation's solvers.

Each op calls one public function of the package and returns its labelled result unchanged, with ``component`` and
``op``; the envelope (``completion_state``, ``warning_card``, ``evidence``, ``request_id``, ``run_id``) comes from
:func:`g6.optimisation.envelope.finish`, so a refusal (a model that does not fit together, a size or time limit, no
OR-Tools or SciPy here) is a ``blocked-escalated`` result, never a raised error. Unknown ops and unknown fields are refused by the
typed input (``G6_E_INPUT_VALIDATION``), so no field can carry code: the earlier version's ``run_code`` is not an op.
No op runs code, writes a file or reaches the network, so none is sensitive.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.core.ai_block import AIBlock
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.optimisation.combinatorial import solve_job_shop, solve_knapsack
from g6.optimisation.engine import DEFAULT_TIME_LIMIT_S
from g6.optimisation.networks import solve_assignment, solve_max_flow, solve_min_cost_flow, solve_routing
from g6.optimisation.nonlinear import (DEFAULT_BACKEND, DEFAULT_EVALUATIONS, DEFAULT_STARTS, MAX_EVALUATIONS,
                                       MAX_STARTS, global_search, minimise)
from g6.optimisation.programmes import solve_cp, solve_lp, solve_mip
from g6.optimisation.schema import (
    STRUCTURAL_LIMIT,
    Arc,
    Count,
    LinearConstraint,
    Name,
    NodeId,
    Number,
    Sense,
    Task,
    Variable,
)

_LINEAR = ("variables={name: {lb: 0 unless given, ub: none unless given, integer: false}}, constraints=[{name, "
           "terms: {variable: coefficient}, lb, ub}], objective={variable: coefficient}, sense=minimise|maximise, "
           "time_limit (seconds, at most 60)")
_OPS: dict[str, str] = {
    "solve_lp": f"solve_lp({_LINEAR}): a linear programme on GLOP; an optimum is shown by its duals (they bound "
                "every feasible point's objective, and the answer meets that bound), and no optimum is shown "
                "infeasible (the least-violation programme's duals bound its violation above zero) or unbounded (a "
                "feasible point and an improving ray), each checked by G6",
    "solve_mip": f"solve_mip({_LINEAR}): a mixed-integer programme on SCIP; the answer checked, with the solver's "
                 "bound and gap",
    "solve_cp": "solve_cp(variables with finite integer bounds, constraints and objective with integer coefficients, "
                "all_different=[[variable, ...]], sense, time_limit): a constraint model on CP-SAT, checked exactly; "
                "with no objective, any solution",
    "solve_knapsack": "solve_knapsack(values=[...], weights=[...] or [[...] per dimension], capacities=[per dimension], "
                      "time_limit): the most valuable items that fit, checked",
    "solve_job_shop": "solve_job_shop(jobs=[[{machine, duration}, ...] per job], time_limit): each job's tasks in "
                      "order, one task at a time per machine, the makespan minimised and checked",
    "solve_assignment": "solve_assignment(costs=[[cost or null per column] per row]): rows paired with columns at "
                        "least cost; optimality shown by the residual network",
    "solve_max_flow": "solve_max_flow(arcs=[{from, to, capacity}], source, sink): the largest flow, shown maximal by a "
                      "cut of the same capacity",
    "solve_min_cost_flow": "solve_min_cost_flow(arcs=[{from, to, capacity, cost}], supplies={node: supply, negative "
                           "for demand}): every supply met at least cost; optimality shown by the residual network",
    "solve_routing": "solve_routing(distances=[[...]], vehicles, depot, demands, capacities=[per vehicle], "
                     "time_windows=[[open, close] per node], travel_times, local_search=descent|guided, time_limit): "
                     "routes that visit every node once, checked, never called optimal without proof",
    "minimise": "minimise(variables={name: {lb: 0 unless given, ub}}, expression=[operation, argument, ...] (a JSON "
                "tree: numbers, variable names, + * - / ^ min max neg abs sqrt exp log sin cos tan tanh atan table), "
                "sense, method=L-BFGS-B|Nelder-Mead|Powell, starts (at most 50), seed, x0={name: value}, "
                "max_evaluations, time_limit): a SciPy local method from several starts; the point re-evaluated by "
                "G6, its bounds and gradient checked; called a local minimum, never the best",
    "global_search": "global_search(variables={name: {lb, ub, integer}}, expression, sense, backend, seed, "
                     "max_evaluations, time_limit): differential evolution over the box, integer variables included; "
                     "the point re-evaluated by G6 and its bounds checked; the best point found, never proven global",
}


class OptimisationInput(BaseModel):
    """One op's parameters. Fields an op does not use are ignored by it; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["solve_lp", "solve_mip", "solve_cp", "solve_knapsack", "solve_job_shop", "solve_assignment",
                "solve_max_flow", "solve_min_cost_flow", "solve_routing", "minimise", "global_search", "ops",
                "help"] = "help"
    variables: dict[Name, Variable] = Field(default_factory=dict, max_length=STRUCTURAL_LIMIT)
    constraints: list[LinearConstraint] = Field(default_factory=list, max_length=STRUCTURAL_LIMIT)
    objective: dict[Name, Number] = Field(default_factory=dict, max_length=STRUCTURAL_LIMIT)
    sense: Sense = "minimise"
    all_different: list[list[Name]] = Field(default_factory=list, max_length=STRUCTURAL_LIMIT)
    values: list[Count] = Field(default_factory=list, max_length=STRUCTURAL_LIMIT)
    weights: list[Count] | list[list[Count]] = Field(default_factory=list, max_length=STRUCTURAL_LIMIT)
    capacities: list[Count] | None = Field(None, max_length=STRUCTURAL_LIMIT)
    jobs: list[list[Task]] = Field(default_factory=list, max_length=STRUCTURAL_LIMIT)
    costs: list[list[int | None]] = Field(default_factory=list, max_length=STRUCTURAL_LIMIT)
    arcs: list[Arc] = Field(default_factory=list, max_length=STRUCTURAL_LIMIT)
    source: NodeId | None = None
    sink: NodeId | None = None
    supplies: dict[NodeId, int] = Field(default_factory=dict, max_length=STRUCTURAL_LIMIT)
    distances: list[list[Count]] = Field(default_factory=list, max_length=STRUCTURAL_LIMIT)
    travel_times: list[list[Count]] | None = Field(None, max_length=STRUCTURAL_LIMIT)
    vehicles: int = Field(1, ge=1)
    depot: int = Field(0, ge=0)
    demands: list[Count] | None = Field(None, max_length=STRUCTURAL_LIMIT)
    time_windows: list[tuple[Count, Count]] | None = Field(None, max_length=STRUCTURAL_LIMIT)
    local_search: Literal["descent", "guided"] = "descent"
    time_limit: float = Field(DEFAULT_TIME_LIMIT_S, gt=0, allow_inf_nan=False)
    # minimise and global_search: the objective is a JSON expression tree, read by G6's interpreter, never run
    expression: Any = None
    method: Literal["L-BFGS-B", "Nelder-Mead", "Powell"] = "L-BFGS-B"
    starts: int = Field(DEFAULT_STARTS, ge=1, le=MAX_STARTS)
    seed: int = Field(0, ge=0, le=2**32 - 1)
    x0: dict[Name, Number] | None = Field(None, max_length=STRUCTURAL_LIMIT)
    max_evaluations: int = Field(DEFAULT_EVALUATIONS, ge=1, le=MAX_EVALUATIONS)
    backend: str = Field(DEFAULT_BACKEND, max_length=64)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _ids(d: OptimisationInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


def _linear(d: OptimisationInput) -> tuple[Any, ...]:
    return d.variables, d.constraints, d.objective, d.sense


_RUN: dict[str, Callable[[OptimisationInput], Any]] = {
    "solve_lp": lambda d: solve_lp(*_linear(d), time_limit=d.time_limit, **_ids(d)),
    "solve_mip": lambda d: solve_mip(*_linear(d), time_limit=d.time_limit, **_ids(d)),
    "solve_cp": lambda d: solve_cp(*_linear(d), all_different=d.all_different, time_limit=d.time_limit, **_ids(d)),
    "solve_knapsack": lambda d: solve_knapsack(d.values, d.weights, d.capacities or [], time_limit=d.time_limit,
                                               **_ids(d)),
    "solve_job_shop": lambda d: solve_job_shop(d.jobs, time_limit=d.time_limit, **_ids(d)),
    "solve_assignment": lambda d: solve_assignment(d.costs, **_ids(d)),
    "solve_max_flow": lambda d: solve_max_flow(d.arcs, d.source, d.sink, **_ids(d)),
    "solve_min_cost_flow": lambda d: solve_min_cost_flow(d.arcs, d.supplies, **_ids(d)),
    "solve_routing": lambda d: solve_routing(d.distances, vehicles=d.vehicles, depot=d.depot, demands=d.demands,
                                             capacities=d.capacities, time_windows=d.time_windows,
                                             travel_times=d.travel_times, local_search=d.local_search,
                                             time_limit=d.time_limit, **_ids(d)),
    "minimise": lambda d: minimise(d.variables, d.expression, d.sense, method=d.method, starts=d.starts, seed=d.seed,
                                   x0=d.x0, max_evaluations=d.max_evaluations, time_limit=d.time_limit, **_ids(d)),
    "global_search": lambda d: global_search(d.variables, d.expression, d.sense, backend=d.backend, seed=d.seed,
                                             max_evaluations=d.max_evaluations, time_limit=d.time_limit, **_ids(d)),
}


@component_maturity(
    "beta",
    reason="OR-Tools and SciPy in process; every answer checked in pure Python before it is reported; qualified-draft "
           "at best",
)
class OptimisationBlock(AIBlock[OptimisationInput, dict, dict]):
    """Solve an optimisation problem on OR-Tools or search one with SciPy, and say what the answer rests on. Never
    ``verified``."""

    def __init__(self, name: str = "optimisation", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: OptimisationInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        result = _RUN[data.op](data)
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


__all__ = ["OptimisationBlock", "OptimisationInput"]
