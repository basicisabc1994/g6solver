# SPDX-License-Identifier: Apache-2.0
"""G6's own checks of an optimiser's answer, in pure Python (owner, 2026-10-20). No OR-Tools needed: each checker is fed
a known-good answer, worked out by hand or by brute force here, and one planted wrong answer it must catch."""
from __future__ import annotations

import itertools

import pytest

from g6.optimisation import checks
from g6.optimisation.envelope import CERTIFICATE_REFUTED
from g6.optimisation.schema import (
    AssignmentProblem,
    CpProblem,
    JobShopProblem,
    KnapsackProblem,
    LinearProblem,
    MaxFlowProblem,
    MinCostFlowProblem,
    RoutingProblem,
)

# A textbook linear programme (Hillier and Lieberman's glass plant): optimum 36 at doors 2, windows 6, with duals
# 0, 1.5 and 1 on the three plants' hours.
PLANTS = LinearProblem.model_validate({
    "variables": {"doors": {}, "windows": {}},
    "constraints": [{"name": "plant1", "terms": {"doors": 1}, "ub": 4},
                    {"name": "plant2", "terms": {"windows": 2}, "ub": 12},
                    {"name": "plant3", "terms": {"doors": 3, "windows": 2}, "ub": 18}],
    "objective": {"doors": 3, "windows": 5}, "sense": "maximise"})
PLANTS_ANSWER = {"doors": 2.0, "windows": 6.0}


def _refuted(check: checks.Check) -> bool:
    return not check.passed and check.code == CERTIFICATE_REFUTED and check.failures


# --- linear programmes: feasibility, the objective, and optimality from the duals -----------------------------------

def test_the_textbook_optimum_is_certified_by_its_duals() -> None:
    check = checks.check_lp(PLANTS, PLANTS_ANSWER, 36.0, [0.0, 1.5, 1.0])
    assert check.passed and check.code == "" and check.certificate == "duals", check.failures
    assert check.details["dual_objective"] == pytest.approx(36.0) and check.tolerance == checks.FEASIBILITY_TOL


@pytest.mark.parametrize(("values", "objective", "duals", "why"), [
    ({"doors": 3.0, "windows": 6.0}, 39.0, [0.0, 1.5, 1.0], "plant3"),            # breaks a constraint
    (PLANTS_ANSWER, 37.0, [0.0, 1.5, 1.0], "objective"),                           # the objective does not recompute
    ({"doors": 2.0, "windows": 5.0}, 31.0, [0.0, 1.5, 1.0], "gap"),               # feasible, but the duals prove 36
    (PLANTS_ANSWER, 36.0, [0.0, 1.5, 0.5], "dual"),                                # duals that prove nothing
    ({"doors": 2.0}, 6.0, [0.0, 1.5, 1.0], "windows"),                             # a variable with no value
])
def test_a_planted_wrong_linear_answer_is_caught(values, objective, duals, why) -> None:
    check = checks.check_lp(PLANTS, values, objective, duals)
    assert _refuted(check) and any(why in f for f in check.failures), check.failures


def test_reduced_costs_that_do_not_match_the_duals_are_caught() -> None:
    assert checks.check_lp(PLANTS, PLANTS_ANSWER, 36.0, [0.0, 1.5, 1.0], {"doors": 0.0, "windows": 0.0}).passed
    check = checks.check_lp(PLANTS, PLANTS_ANSWER, 36.0, [0.0, 1.5, 1.0], {"doors": 0.0, "windows": 2.0})
    assert _refuted(check) and any("reduced cost" in f for f in check.failures)


def test_an_improving_ray_is_checked_against_every_constraint() -> None:
    open_ended = LinearProblem.model_validate({
        "variables": {"x": {}, "y": {}}, "constraints": [{"terms": {"x": 1, "y": -1}, "ub": 1}],
        "objective": {"x": 1, "y": 1}, "sense": "maximise"})
    assert checks.check_ray(open_ended, {"x": 1.0, "y": 1.0}).passed
    assert _refuted(checks.check_ray(open_ended, {"x": 1.0, "y": 0.0}))      # leaves the feasible region
    assert _refuted(checks.check_ray(open_ended, {"x": -1.0, "y": -1.0}))    # breaks the bounds x, y >= 0


# --- integer programmes and constraint models: feasibility, integrality, the objective and the bound ----------------

INTEGER = LinearProblem.model_validate({
    "variables": {"a": {"ub": 10, "integer": True}, "b": {"ub": 10, "integer": True}},
    "constraints": [{"terms": {"a": 2, "b": 3}, "ub": 7.5}], "objective": {"a": 3, "b": 4}, "sense": "maximise"})


def test_an_integer_answer_and_its_bound_are_checked() -> None:
    assert checks.check_mip(INTEGER, {"a": 2.0, "b": 1.0}, 10.0, 10.0).passed
    assert _refuted(checks.check_mip(INTEGER, {"a": 2.5, "b": 0.0}, 7.5, 10.0))      # not an integer
    assert _refuted(checks.check_mip(INTEGER, {"a": 2.0, "b": 1.0}, 10.0, 9.0))      # a bound below its own answer


CP = CpProblem.model_validate({
    "variables": {"x": {"lb": 0, "ub": 3}, "y": {"lb": 0, "ub": 3}, "z": {"lb": 0, "ub": 3}},
    "constraints": [{"terms": {"x": 1, "y": 1, "z": 1}, "lb": 5}], "all_different": [["x", "y", "z"]],
    "objective": {"x": 1, "y": 2}, "sense": "maximise"})


def _cp_optimum() -> int:
    best = None
    for x, y, z in itertools.product(range(4), repeat=3):
        if len({x, y, z}) == 3 and x + y + z >= 5:
            best = max(best or 0, x + 2 * y)
    return best


def test_a_constraint_model_answer_is_checked_exactly() -> None:
    assert _cp_optimum() == 8
    assert checks.check_cp(CP, {"x": 2, "y": 3, "z": 1}, 8.0, 8.0).passed
    assert _refuted(checks.check_cp(CP, {"x": 3, "y": 3, "z": 1}, 9.0, 9.0))        # two values the same
    assert _refuted(checks.check_cp(CP, {"x": 2, "y": 3, "z": 1}, 9.0, 9.0))        # the objective does not recompute


# --- knapsack, scheduling ---------------------------------------------------------------------------------------------

KNAPSACK = KnapsackProblem.model_validate({"values": [60, 100, 120], "weights": [10, 20, 30], "capacities": [50]})


def test_a_knapsack_answer_is_checked() -> None:
    assert checks.check_knapsack(KNAPSACK, [1, 2], 220).passed
    assert _refuted(checks.check_knapsack(KNAPSACK, [0, 1, 2], 280))               # over the capacity
    assert _refuted(checks.check_knapsack(KNAPSACK, [1, 2], 230))                  # the value does not recompute


# The classic three jobs on three machines (the OR-Tools guide's job shop), optimal makespan 11.
JOBS = JobShopProblem.model_validate({"jobs": [
    [{"machine": 0, "duration": 3}, {"machine": 1, "duration": 2}, {"machine": 2, "duration": 2}],
    [{"machine": 0, "duration": 2}, {"machine": 2, "duration": 1}, {"machine": 1, "duration": 4}],
    [{"machine": 1, "duration": 4}, {"machine": 2, "duration": 3}]]})
JOB_STARTS = [[2, 5, 7], [0, 2, 7], [0, 4]]


def test_a_schedule_is_checked_for_overlap_order_and_makespan() -> None:
    assert checks.check_job_shop(JOBS, JOB_STARTS, 11, 11).passed
    assert _refuted(checks.check_job_shop(JOBS, [[1, 5, 7], [0, 2, 7], [0, 4]], 11, 11))   # two jobs on machine 0
    assert _refuted(checks.check_job_shop(JOBS, [[2, 4, 7], [0, 2, 7], [0, 4]], 11, 11))   # task before its forerunner
    assert _refuted(checks.check_job_shop(JOBS, JOB_STARTS, 10, 10))                        # makespan does not recompute


# --- assignment and flows: optimality from the network itself ---------------------------------------------------------

COSTS = AssignmentProblem.model_validate({"costs": [[4, 1, 3], [2, 0, 5], [3, 2, 2]]})


def test_an_assignment_is_checked_and_its_optimality_shown() -> None:
    best = min(sum(COSTS.costs[i][p[i]] for i in range(3)) for p in itertools.permutations(range(3)))
    check = checks.check_assignment(COSTS, [(0, 1), (1, 0), (2, 2)], best)
    assert check.passed and check.certificate == "no_negative_cycle"
    worse = checks.check_assignment(COSTS, [(0, 0), (1, 1), (2, 2)], 6)            # valid, but not the cheapest
    assert _refuted(worse) and any("cheaper" in f for f in worse.failures)
    assert _refuted(checks.check_assignment(COSTS, [(0, 1), (1, 1), (2, 2)], 3))    # a column used twice


# The max-flow network of Cormen et al.: maximum flow 23, cut by {s, v1, v2, v4}.
NETWORK = MaxFlowProblem.model_validate({"source": "s", "sink": "t", "arcs": [
    {"from": "s", "to": "v1", "capacity": 16}, {"from": "s", "to": "v2", "capacity": 13},
    {"from": "v1", "to": "v3", "capacity": 12}, {"from": "v2", "to": "v1", "capacity": 4},
    {"from": "v2", "to": "v4", "capacity": 14}, {"from": "v3", "to": "v2", "capacity": 9},
    {"from": "v3", "to": "t", "capacity": 20}, {"from": "v4", "to": "v3", "capacity": 7},
    {"from": "v4", "to": "t", "capacity": 4}]})
FLOWS = [12, 11, 12, 0, 11, 0, 19, 7, 4]


def test_a_maximum_flow_is_certified_by_a_cut_of_the_same_capacity() -> None:
    check = checks.check_max_flow(NETWORK, FLOWS, 23, ["s", "v1", "v2", "v4"])
    assert check.passed and check.certificate == "min_cut" and check.details["cut_capacity"] == 23
    assert _refuted(checks.check_max_flow(NETWORK, FLOWS, 24, ["s", "v1", "v2", "v4"]))          # wrong value
    assert _refuted(checks.check_max_flow(NETWORK, FLOWS, 23, ["s"]))                             # cut of 29
    leaky = [12, 11, 11, 0, 11, 0, 19, 7, 4]                                                      # v1 keeps one unit
    assert _refuted(checks.check_max_flow(NETWORK, leaky, 23, ["s", "v1", "v2", "v4"]))


SUPPLY = MinCostFlowProblem.model_validate({"supplies": {"a": 2, "d": -2}, "arcs": [
    {"from": "a", "to": "b", "capacity": 2, "cost": 1}, {"from": "a", "to": "c", "capacity": 2, "cost": 3},
    {"from": "b", "to": "d", "capacity": 1, "cost": 1}, {"from": "b", "to": "c", "capacity": 2, "cost": 1},
    {"from": "c", "to": "d", "capacity": 2, "cost": 1}]})


def test_a_minimum_cost_flow_is_certified_by_its_residual_network() -> None:
    check = checks.check_min_cost_flow(SUPPLY, [2, 0, 1, 1, 1], 5)
    assert check.passed and check.certificate == "no_negative_cycle"
    worse = checks.check_min_cost_flow(SUPPLY, [1, 1, 1, 0, 1], 6)                # a, c, d costs 4 a unit, not 3
    assert _refuted(worse) and any("cheaper" in f for f in worse.failures)
    assert _refuted(checks.check_min_cost_flow(SUPPLY, [2, 0, 1, 1, 0], 4))        # d receives one unit, not two


# --- routing ----------------------------------------------------------------------------------------------------------

DISTANCES = [[0, 2, 9, 10, 7], [2, 0, 6, 4, 3], [9, 6, 0, 8, 5], [10, 4, 8, 0, 6], [7, 3, 5, 6, 0]]


def _tour_length(tour: list[int]) -> int:
    return sum(DISTANCES[a][b] for a, b in itertools.pairwise(tour))


def test_a_route_is_checked_for_every_visit_and_its_distance() -> None:
    problem = RoutingProblem.model_validate({"distances": DISTANCES})
    tour = [0, *min(itertools.permutations(range(1, 5)), key=lambda p: _tour_length([0, *p, 0])), 0]
    assert checks.check_routes(problem, [tour], _tour_length(tour)).passed
    assert _refuted(checks.check_routes(problem, [[0, 1, 2, 3, 0]], _tour_length([0, 1, 2, 3, 0])))   # misses 4
    assert _refuted(checks.check_routes(problem, [[0, 1, 2, 1, 3, 4, 0]], 40))                         # 1 twice
    assert _refuted(checks.check_routes(problem, [tour], _tour_length(tour) - 1))                      # distance


def test_routes_are_checked_against_capacities_and_time_windows() -> None:
    loads = RoutingProblem.model_validate({"distances": DISTANCES, "vehicles": 2, "demands": [0, 3, 4, 2, 5],
                                           "capacities": [8, 8]})
    assert checks.check_routes(loads, [[0, 2, 3, 0], [0, 1, 4, 0]], 27 + 12).passed
    assert _refuted(checks.check_routes(loads, [[0, 2, 4, 0], [0, 1, 3, 0]], 21 + 16))          # 9 on a vehicle of 8
    windows = RoutingProblem.model_validate({"distances": DISTANCES,
                                             "time_windows": [[0, 100], [0, 5], [0, 100], [0, 100], [0, 100]]})
    assert checks.check_routes(windows, [[0, 1, 3, 2, 4, 0]], _tour_length([0, 1, 3, 2, 4, 0])).passed
    late = [0, 4, 2, 3, 1, 0]                                                                   # reaches 1 at 24
    assert _refuted(checks.check_routes(windows, [late], _tour_length(late)))
