# SPDX-License-Identifier: Apache-2.0
"""Knapsack on OR-Tools' knapsack solver (branch and bound, any number of weight dimensions) and the job shop on
CP-SAT's interval variables; each answer checked by :mod:`g6.optimisation.checks` before it is reported."""
from __future__ import annotations

from typing import Any

from g6.optimisation import checks
from g6.optimisation.engine import DEFAULT_TIME_LIMIT_S, at_most, ortools, parse, run, version
from g6.optimisation.engine import time_limit as _seconds
from g6.optimisation.envelope import INFEASIBLE, MODEL_INVALID
from g6.optimisation.programmes import _gap_closed, _no_answer
from g6.optimisation.schema import JobShopProblem, KnapsackProblem, OptimResult

MAX_ITEMS = 1_000
MAX_DIMENSIONS = 10
MAX_JOBS = 100
MAX_TASKS = 2_000
KNAPSACK = "OR-Tools knapsack solver (branch and bound)"
JOB_SHOP = "OR-Tools CP-SAT (interval scheduling)"


def _knapsack(problem: KnapsackProblem, seconds: float) -> dict[str, Any]:
    """One knapsack solve: whether it is proven optimal, the chosen items and their value."""
    knapsack = ortools("ortools.algorithms.python.knapsack_solver")
    solver = knapsack.KnapsackSolver(knapsack.SolverType.KNAPSACK_MULTIDIMENSION_BRANCH_AND_BOUND_SOLVER, "g6")
    solver.init(list(problem.values), [list(row) for row in problem.weights], list(problem.capacities))
    solver.set_time_limit(seconds)
    value = solver.solve()
    return {"status": "OPTIMAL" if solver.is_solution_optimal() else "FEASIBLE", "value": int(value),
            "chosen": [i for i in range(len(problem.values)) if solver.best_solution_contains(i)]}


def _pack(problem: KnapsackProblem, seconds: float) -> dict[str, Any]:
    at_most(len(problem.values), MAX_ITEMS, "items")
    at_most(len(problem.capacities), MAX_DIMENSIONS, "weight dimensions")
    summary = {"items": len(problem.values), "dimensions": len(problem.capacities), "time_limit_s": seconds}
    raw = _knapsack(problem, seconds)
    check = checks.check_knapsack(problem, raw["chosen"], raw["value"])
    return {"engine_version": version(), "status": raw["status"], "problem": summary, "objective": raw["value"],
            "optimal": raw["status"] == "OPTIMAL", "check": check,
            "solution": {"chosen": raw["chosen"], "loads": check.details.get("loads", [])}}


def _job_shop(problem: JobShopProblem, seconds: float) -> dict[str, Any]:
    """One CP-SAT solve of the job shop: a start per task, the makespan and the bound."""
    cp_model = ortools("ortools.sat.python.cp_model")
    model = cp_model.CpModel()
    horizon = sum(task.duration for job in problem.jobs for task in job)
    starts, ends, on_machine = [], [], {}
    for j, job in enumerate(problem.jobs):
        starts.append([])
        ends.append([])
        for t, task in enumerate(job):
            start = model.new_int_var(0, horizon, f"start_{j}_{t}")
            end = model.new_int_var(0, horizon, f"end_{j}_{t}")
            on_machine.setdefault(task.machine, []).append(
                model.new_interval_var(start, task.duration, end, f"task_{j}_{t}"))
            if t:
                model.add(start >= ends[j][-1])
            starts[j].append(start)
            ends[j].append(end)
    for intervals in on_machine.values():
        model.add_no_overlap(intervals)
    makespan = model.new_int_var(0, horizon, "makespan")
    model.add_max_equality(makespan, [job[-1] for job in ends])
    model.minimize(makespan)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = seconds
    status = solver.solve(model)
    raw: dict[str, Any] = {"status": solver.status_name(status)}
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raw |= {"starts": [[int(solver.value(s)) for s in job] for job in starts],
                "makespan": int(solver.value(makespan)), "bound": float(solver.best_objective_bound)}
    elif status == cp_model.MODEL_INVALID:
        raw["detail"] = model.validate()
    return raw


def _schedule(problem: JobShopProblem, seconds: float) -> dict[str, Any]:
    tasks = sum(len(job) for job in problem.jobs)
    at_most(len(problem.jobs), MAX_JOBS, "jobs")
    at_most(tasks, MAX_TASKS, "tasks")
    summary = {"jobs": len(problem.jobs), "tasks": tasks,
               "machines": len({task.machine for job in problem.jobs for task in job}), "time_limit_s": seconds}
    raw = _job_shop(problem, seconds)
    out: dict[str, Any] = {"engine_version": version(), "status": raw["status"], "problem": summary}
    if raw["status"] == "INFEASIBLE":
        return out | {"code": INFEASIBLE, "reason": "CP-SAT found the schedule infeasible, which a job shop with no "
                                                    "deadlines cannot be; report it"}
    if raw["status"] == "MODEL_INVALID":
        return out | {"code": MODEL_INVALID, "reason": f"CP-SAT rejected the model: {raw.get('detail', '')}"}
    if "starts" not in raw:
        return out | _no_answer(raw, "CP-SAT")
    schedule = [[{"machine": task.machine, "start": start, "end": start + task.duration}
                 for task, start in zip(job, times)] for job, times in zip(problem.jobs, raw["starts"])]
    return out | {"objective": raw["makespan"], "bound": raw["bound"],
                  "optimal": raw["status"] == "OPTIMAL" and _gap_closed(raw["makespan"], raw["bound"]),
                  "check": checks.check_job_shop(problem, raw["starts"], raw["makespan"], raw["bound"]),
                  "solution": {"schedule": schedule, "makespan": raw["makespan"]}}


def solve_knapsack(values: list[int], weights: list[int] | list[list[int]], capacities: list[int], *,
                   time_limit: float = DEFAULT_TIME_LIMIT_S, request_id: str = "", run_id: str = "") -> OptimResult:
    """Choose items of most total value within every capacity (one weight list per dimension); checked."""
    data = {"values": values, "weights": weights, "capacities": capacities}
    return run("solve_knapsack", KNAPSACK, lambda: _pack(parse(KnapsackProblem, data), _seconds(time_limit)),
               request_id=request_id, run_id=run_id)


def solve_job_shop(jobs: list[list[Any]], *, time_limit: float = DEFAULT_TIME_LIMIT_S, request_id: str = "",
                   run_id: str = "") -> OptimResult:
    """Schedule each job's tasks in order, one task at a time per machine, to finish all as early as possible;
    checked."""
    return run("solve_job_shop", JOB_SHOP,
               lambda: _schedule(parse(JobShopProblem, {"jobs": jobs}), _seconds(time_limit)),
               request_id=request_id, run_id=run_id)


__all__ = ["JOB_SHOP", "KNAPSACK", "MAX_DIMENSIONS", "MAX_ITEMS", "MAX_JOBS", "MAX_TASKS", "solve_job_shop",
           "solve_knapsack"]
