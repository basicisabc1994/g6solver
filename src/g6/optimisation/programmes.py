# SPDX-License-Identifier: Apache-2.0
"""Linear programmes on GLOP and mixed-integer programmes on SCIP, both through OR-Tools' MathOpt, and constraint models
on CP-SAT; each answer checked by :mod:`g6.optimisation.checks` before it is reported.

MathOpt rather than the older ``pywraplp`` interface: through ``pywraplp``, GLOP's presolve reported an unbounded
programme as INFEASIBLE, while MathOpt says it cannot tell (``INFEASIBLE_OR_UNBOUNDED``). Either way G6 decides: when
GLOP finds no optimum, G6 solves the least-violation programme and checks its duals (infeasible), or checks a feasible
point and an improving ray (unbounded). A linear optimum is checked against its duals; an integer optimum's
feasibility and objective are checked, and its optimality rests on the solver's bound, reported with the gap.
"""
from __future__ import annotations

import math
from datetime import timedelta
from typing import Any

from g6.optimisation import checks
from g6.optimisation.engine import DEFAULT_TIME_LIMIT_S, at_most, ortools, parse, run, version
from g6.optimisation.engine import time_limit as _seconds
from g6.optimisation.envelope import (
    CERTIFICATE_REFUTED,
    INFEASIBLE,
    INFEASIBLE_OR_UNBOUNDED,
    INPUT_INVALID,
    MODEL_INVALID,
    NO_SOLUTION,
    SOLVER_ERROR,
    TIME_LIMIT,
    UNBOUNDED,
    Refusal,
)
from g6.optimisation.schema import CpProblem, LinearProblem, OptimResult

MAX_VARIABLES = 5_000
MAX_CONSTRAINTS = 5_000
MAX_NONZEROS = 100_000
GLOP = "OR-Tools GLOP (through MathOpt)"
SCIP = "OR-Tools SCIP (through MathOpt)"
CP_SAT = "OR-Tools CP-SAT"
_NO_OPTIMUM = ("INFEASIBLE", "UNBOUNDED", "INFEASIBLE_OR_UNBOUNDED")


def _sized(problem: LinearProblem, seconds: float) -> dict[str, Any]:
    nonzeros = sum(len(row.terms) for row in problem.constraints)
    at_most(len(problem.variables), MAX_VARIABLES, "variables")
    at_most(len(problem.constraints), MAX_CONSTRAINTS, "constraints")
    at_most(nonzeros, MAX_NONZEROS, "nonzero coefficients")
    return {"variables": len(problem.variables), "constraints": len(problem.constraints), "nonzeros": nonzeros,
            "integer_variables": sum(v.integer for v in problem.variables.values()), "sense": problem.sense,
            "time_limit_s": seconds}


def _inf(bound: float | None, side: int) -> float:
    return side * math.inf if bound is None else bound


def _mathopt(problem: LinearProblem, solver: str, seconds: float) -> dict[str, Any]:
    """One MathOpt solve: the termination, the bounds, the values and (for GLOP) the duals and reduced costs."""
    mathopt = ortools("ortools.math_opt.python.mathopt")
    model = mathopt.Model(name="g6")
    var = {name: model.add_variable(lb=_inf(v.lb, -1), ub=_inf(v.ub, 1), is_integer=v.integer, name=name)
           for name, v in problem.variables.items()}
    rows = []
    for i, row in enumerate(problem.constraints):
        constraint = model.add_linear_constraint(lb=_inf(row.lb, -1), ub=_inf(row.ub, 1), name=row.name or f"c{i}")
        for name, coefficient in row.terms.items():
            constraint.set_coefficient(var[name], coefficient)
        rows.append(constraint)
    model.objective.is_maximize = problem.maximise
    for name, coefficient in problem.objective.items():
        model.objective.set_linear_coefficient(var[name], coefficient)
    result = mathopt.solve(model, getattr(mathopt.SolverType, solver),
                           params=mathopt.SolveParameters(time_limit=timedelta(seconds=seconds)))
    termination = result.termination
    raw: dict[str, Any] = {"status": termination.reason.name, "limit": termination.limit.name if termination.limit
                           else "", "detail": termination.detail, "bound": termination.objective_bounds.dual_bound}
    if result.has_primal_feasible_solution():
        values = result.variable_values()
        raw |= {"values": {name: float(values[v]) for name, v in var.items()},
                "objective": float(result.objective_value())}
    if solver == "GLOP" and result.has_dual_feasible_solution():
        duals, reduced = result.dual_values(), result.reduced_costs()
        raw |= {"duals": [float(duals[r]) for r in rows],
                "reduced_costs": {name: float(reduced[v]) for name, v in var.items()}}
    return raw


def _no_answer(raw: dict[str, Any], engine: str) -> dict[str, Any]:
    """The outcome of a solve that ended with no answer to check and no claim about feasibility."""
    status = raw["status"]
    if status in ("NO_SOLUTION_FOUND", "UNKNOWN") and raw.get("limit", "TIME") == "TIME":
        return {"code": TIME_LIMIT, "reason": f"{engine} stopped at the time limit with no answer ({status}); that is "
                                              "not a proof that none exists"}
    if status == "NO_SOLUTION_FOUND":
        return {"code": NO_SOLUTION, "reason": f"{engine} stopped ({raw.get('limit')}) with no answer; that is not a "
                                               "proof that none exists"}
    return {"code": SOLVER_ERROR, "reason": f"{engine} ended with {status}: {raw.get('detail', '')}".strip(": ")}


def _classify(problem: LinearProblem, said: str, seconds: float) -> dict[str, Any]:
    """GLOP found no optimum (``said``). Decide, with certificates G6 checks, whether ``problem`` is infeasible or
    unbounded; when neither can be shown, report the solver's word as its own, uncertified."""
    note = "" if said in ("INFEASIBLE", "UNBOUNDED") else f" (the solver said {said})"
    least = _mathopt(checks.elastic(problem), "GLOP", seconds)
    claimed = {"INFEASIBLE": INFEASIBLE, "UNBOUNDED": UNBOUNDED}.get(said, INFEASIBLE_OR_UNBOUNDED)
    uncertified = {"code": claimed, "reason": f"GLOP reported {said}, and G6 could not confirm it with a check of "
                                              "its own"}
    if least["status"] != "OPTIMAL" or "duals" not in least:
        return uncertified
    infeasible = checks.check_infeasible(problem, least["values"], least["objective"], least["duals"])
    if infeasible.passed:
        return {"code": INFEASIBLE, "check": infeasible,
                "reason": f"no point meets every constraint: the least total violation is "
                          f"{infeasible.details['least_violation']:g}, shown by the duals of the least-violation "
                          f"programme, which G6 checked{note}"}
    point = {name: least["values"][f"x:{name}"] for name in problem.variables}
    if not checks.check_feasible(problem, point).passed:
        return uncertified
    ray = _mathopt(checks.ray_problem(problem), "GLOP", seconds)
    if ray["status"] == "OPTIMAL" and "values" in ray:
        unbounded = checks.check_unbounded(problem, point, ray["values"])
        if unbounded.passed:
            side = "upper" if problem.maximise else "lower"
            return {"code": UNBOUNDED, "check": unbounded,
                    "certificate": {"feasible_point": point, "ray": ray["values"]},
                    "reason": f"the objective has no {side} limit: G6 checked a feasible point and a direction that "
                              f"keeps every constraint while the objective improves without end{note}"}
    return {"code": CERTIFICATE_REFUTED, "certificate": {"feasible_point": point},
            "reason": f"GLOP reported {said}, but G6 found a feasible point and no improving ray, so an optimum exists"}


def _linear(problem: LinearProblem, seconds: float) -> dict[str, Any]:
    if any(v.integer for v in problem.variables.values()):
        raise Refusal(INPUT_INVALID, "solve_lp solves continuous programmes and would drop integrality; use "
                                     "solve_mip for integer variables")
    summary = _sized(problem, seconds)
    raw = _mathopt(problem, "GLOP", seconds)
    out: dict[str, Any] = {"engine_version": version(), "status": raw["status"], "problem": summary}
    if raw["status"] in _NO_OPTIMUM:
        return out | _classify(problem, raw["status"], seconds)
    if "values" not in raw:
        return out | _no_answer(raw, "GLOP")
    values, objective, bound = raw["values"], raw["objective"], raw.get("bound")
    answer = {"objective": objective, "bound": bound, "solution": {"values": values}}
    if raw["status"] == "OPTIMAL" and "duals" in raw:
        check = checks.check_lp(problem, values, objective, raw["duals"], raw.get("reduced_costs"))
        return out | answer | {"optimal": True, "check": check,
                               "certificate": {"duals": raw["duals"], "reduced_costs": raw.get("reduced_costs", {})}}
    # No duals to certify it (or no optimum claimed): the answer is checked, and any optimality rests on the bound.
    optimal = raw["status"] == "OPTIMAL" and _gap_closed(objective, bound)
    return out | answer | {"optimal": optimal, "check": checks.check_mip(problem, values, objective, bound)}


def _gap_closed(objective: float, bound: float | None) -> bool:
    return bound is not None and math.isfinite(bound) and abs(objective - bound) <= checks.OPTIMALITY_TOL * max(
        1.0, abs(objective))


def _integer(problem: LinearProblem, seconds: float) -> dict[str, Any]:
    summary = _sized(problem, seconds)
    raw = _mathopt(problem, "GSCIP", seconds)
    out: dict[str, Any] = {"engine_version": version(), "status": raw["status"], "problem": summary}
    status = raw["status"]
    if status == "INFEASIBLE":
        return out | {"code": INFEASIBLE, "reason": "SCIP reports that no point meets every constraint (its proof, "
                                                    "which G6 does not re-check)"}
    if status == "UNBOUNDED":
        return out | {"code": UNBOUNDED, "reason": "SCIP reports that the objective improves without end (its "
                                                   "proof, which G6 does not re-check)"}
    if status == "INFEASIBLE_OR_UNBOUNDED":
        return out | {"code": INFEASIBLE_OR_UNBOUNDED,
                      "reason": "SCIP could not tell whether the model is infeasible or unbounded"}
    if "values" not in raw:
        return out | _no_answer(raw, "SCIP")
    values = {name: float(round(x)) if problem.variables[name].integer and abs(x - round(x)) <= checks.INTEGRALITY_TOL
              else x for name, x in raw["values"].items()}
    objective, bound = raw["objective"], raw.get("bound")
    check = checks.check_mip(problem, values, objective, bound)
    optimal = status == "OPTIMAL" and _gap_closed(objective, bound)
    return out | {"objective": objective, "bound": bound, "solution": {"values": values}, "optimal": optimal,
                  "check": check}


def _cp_sat(problem: CpProblem, seconds: float) -> dict[str, Any]:
    """One CP-SAT solve: the status, the values, the objective and the bound."""
    cp_model = ortools("ortools.sat.python.cp_model")
    model = cp_model.CpModel()
    var = {name: model.new_int_var(math.ceil(v.lb), math.floor(v.ub), name) for name, v in problem.variables.items()}

    def linear(terms: dict[str, float]) -> Any:
        names = list(terms)
        return cp_model.LinearExpr.weighted_sum([var[n] for n in names], [int(terms[n]) for n in names])

    for row in problem.constraints:
        expression = linear(row.terms)
        if row.lb is not None:
            model.add(expression >= math.ceil(row.lb))
        if row.ub is not None:
            model.add(expression <= math.floor(row.ub))
    for group in problem.all_different:
        model.add_all_different([var[n] for n in group])
    if problem.objective:
        (model.maximize if problem.maximise else model.minimize)(linear(problem.objective))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = seconds
    status = solver.solve(model)
    raw: dict[str, Any] = {"status": solver.status_name(status)}
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raw["values"] = {name: int(solver.value(v)) for name, v in var.items()}
        if problem.objective:
            raw |= {"objective": float(solver.objective_value), "bound": float(solver.best_objective_bound)}
    elif status == cp_model.MODEL_INVALID:
        raw["detail"] = model.validate()
    return raw


def _constraint(problem: CpProblem, seconds: float) -> dict[str, Any]:
    summary = _sized(problem, seconds) | {"all_different": len(problem.all_different)}
    at_most(sum(len(g) for g in problem.all_different), MAX_VARIABLES, "variables across all_different groups")
    for name, v in problem.variables.items():
        if math.ceil(v.lb) > math.floor(v.ub):
            raise Refusal(INFEASIBLE, f"no integer lies within the bounds of {name!r} ([{v.lb:g}, {v.ub:g}])")
    raw = _cp_sat(problem, seconds)
    out: dict[str, Any] = {"engine_version": version(), "status": raw["status"], "problem": summary}
    status = raw["status"]
    if status == "INFEASIBLE":
        return out | {"code": INFEASIBLE, "reason": "CP-SAT proved that no assignment meets every constraint (its "
                                                    "proof, which G6 does not re-check)"}
    if status == "MODEL_INVALID":
        return out | {"code": MODEL_INVALID, "reason": f"CP-SAT rejected the model: {raw.get('detail', '')}"}
    if "values" not in raw:
        return out | _no_answer(raw, "CP-SAT")
    objective, bound = raw.get("objective"), raw.get("bound")
    check = checks.check_cp(problem, raw["values"], objective, bound)
    optimal = None if not problem.objective else status == "OPTIMAL" and _gap_closed(objective, bound)
    return out | {"objective": objective, "bound": bound, "solution": {"values": raw["values"]}, "optimal": optimal,
                  "check": check}


def solve_lp(variables: dict[str, Any], constraints: list[Any] = (), objective: dict[str, float] | None = None,
             sense: str = "minimise", *, time_limit: float = DEFAULT_TIME_LIMIT_S, request_id: str = "",
             run_id: str = "") -> OptimResult:
    """A linear programme on GLOP: an optimum whose duals bound every feasible point's objective at the answer's, or
    infeasibility or unboundedness shown by a check G6 runs."""
    data = {"variables": variables, "constraints": list(constraints), "objective": objective or {}, "sense": sense}
    return run("solve_lp", GLOP, lambda: _linear(parse(LinearProblem, data), _seconds(time_limit)),
               request_id=request_id, run_id=run_id)


def solve_mip(variables: dict[str, Any], constraints: list[Any] = (), objective: dict[str, float] | None = None,
              sense: str = "minimise", *, time_limit: float = DEFAULT_TIME_LIMIT_S, request_id: str = "",
              run_id: str = "") -> OptimResult:
    """A mixed-integer programme on SCIP: a checked answer with the solver's bound and gap."""
    data = {"variables": variables, "constraints": list(constraints), "objective": objective or {}, "sense": sense}
    return run("solve_mip", SCIP, lambda: _integer(parse(LinearProblem, data), _seconds(time_limit)),
               request_id=request_id, run_id=run_id)


def solve_cp(variables: dict[str, Any], constraints: list[Any] = (), objective: dict[str, float] | None = None,
             sense: str = "minimise", *, all_different: list[list[str]] = (), time_limit: float = DEFAULT_TIME_LIMIT_S,
             request_id: str = "", run_id: str = "") -> OptimResult:
    """A constraint model on CP-SAT: integer variables, linear constraints, all-different groups, an optional linear
    objective; checked exactly."""
    data = {"variables": variables, "constraints": list(constraints), "objective": objective or {}, "sense": sense,
            "all_different": [list(g) for g in all_different]}
    return run("solve_cp", CP_SAT, lambda: _constraint(parse(CpProblem, data), _seconds(time_limit)),
               request_id=request_id, run_id=run_id)


__all__ = ["CP_SAT", "GLOP", "MAX_CONSTRAINTS", "MAX_NONZEROS", "MAX_VARIABLES", "SCIP", "solve_cp", "solve_lp",
           "solve_mip"]
