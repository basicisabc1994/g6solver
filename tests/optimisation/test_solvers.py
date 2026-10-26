# SPDX-License-Identifier: Apache-2.0
"""Each op on OR-Tools itself (owner, 2026-10-20), against an answer known independently: a textbook optimum with its
duals, or a brute force here. Every status has its own stable code; nothing is ``verified``; and a wrong answer planted
between the solver and G6's check is caught and blocked. Skipped where OR-Tools is not installed."""
from __future__ import annotations

import itertools
import random

import pytest

pytest.importorskip("ortools")

from g6.invariants import check_envelope  # noqa: E402
from g6.optimisation import (  # noqa: E402
    combinatorial,
    networks,
    programmes,
    solve_assignment,
    solve_cp,
    solve_job_shop,
    solve_knapsack,
    solve_lp,
    solve_max_flow,
    solve_min_cost_flow,
    solve_mip,
    solve_routing,
)
from g6.optimisation.envelope import (  # noqa: E402
    CERTIFICATE_REFUTED,
    INFEASIBLE,
    INPUT_INVALID,
    INPUT_TOO_LARGE,
    MODEL_INVALID,
    NO_SOLUTION,
    NOT_PROVEN_OPTIMAL,
    SOLVER_ANSWER,
    TIME_LIMIT,
    TIME_LIMIT_TOO_LONG,
    UNBOUNDED,
)

PLANTS = {"variables": {"doors": {}, "windows": {}},
          "constraints": [{"name": "plant1", "terms": {"doors": 1}, "ub": 4},
                          {"name": "plant2", "terms": {"windows": 2}, "ub": 12},
                          {"name": "plant3", "terms": {"doors": 3, "windows": 2}, "ub": 18}],
          "objective": {"doors": 3, "windows": 5}, "sense": "maximise"}
DISTANCES = [[0, 2, 9, 10, 7], [2, 0, 6, 4, 3], [9, 6, 0, 8, 5], [10, 4, 8, 0, 6], [7, 3, 5, 6, 0]]
JOBS = [[{"machine": 0, "duration": 3}, {"machine": 1, "duration": 2}, {"machine": 2, "duration": 2}],
        [{"machine": 0, "duration": 2}, {"machine": 2, "duration": 1}, {"machine": 1, "duration": 4}],
        [{"machine": 1, "duration": 4}, {"machine": 2, "duration": 3}]]


def _draft(result, card: str = SOLVER_ANSWER):
    assert result.completion_state == "qualified-draft", (result.code, result.reason)
    assert result.warning_card["code"] == card and result.check["passed"] is True
    assert check_envelope(result.model_dump(mode="json")) == []
    return result


def _blocked(result, code: str):
    assert result.completion_state == "blocked-escalated" and result.code == code, (result.code, result.reason)
    assert result.warning_card["code"] == code and check_envelope(result.model_dump(mode="json")) == []
    return result


# --- linear programmes ------------------------------------------------------------------------------------------------

def test_the_textbook_linear_programme_and_its_duals() -> None:
    r = _draft(solve_lp(**PLANTS, request_id="q", run_id="r"))
    assert r.optimal is True and r.objective == pytest.approx(36) and r.check["certificate"] == "duals"
    assert r.solution["values"] == pytest.approx({"doors": 2, "windows": 6})
    assert r.certificate["duals"] == pytest.approx([0, 1.5, 1], abs=1e-9)
    assert r.certificate["reduced_costs"] == pytest.approx({"doors": 0, "windows": 0}, abs=1e-9)
    assert r.bound == pytest.approx(36) and r.gap == pytest.approx(0, abs=1e-9)
    assert (r.request_id, r.run_id) == ("q", "r") and "GLOP" in r.evidence[0]["engine"]
    assert "duals" in r.warning_card["message"] and "not verified" in r.warning_card["message"]


def test_a_minimisation_with_a_lower_bound_row() -> None:
    r = _draft(solve_lp({"x": {}, "y": {}}, [{"terms": {"x": 1, "y": 1}, "lb": 2}], {"x": 3, "y": 2}))
    assert r.objective == pytest.approx(4) and r.solution["values"] == pytest.approx({"x": 0, "y": 2})
    assert r.certificate["duals"] == pytest.approx([2])


def test_an_infeasible_linear_programme_is_certified_infeasible() -> None:
    r = _blocked(solve_lp({"x": {}, "y": {}}, [{"terms": {"x": 1, "y": 1}, "ub": 1},
                                               {"terms": {"x": 1, "y": 1}, "lb": 2}], {"x": 1}), INFEASIBLE)
    assert r.check["passed"] is True and r.check["certificate"] == "elastic_duals"
    assert r.check["details"]["least_violation"] == pytest.approx(1)


def test_an_unbounded_linear_programme_is_certified_unbounded_not_called_infeasible() -> None:
    """GLOP's presolve cannot tell an unbounded programme from an infeasible one (through its older interface it said
    INFEASIBLE); G6 finds a feasible point and an improving ray and checks both."""
    r = _blocked(solve_lp({"x": {}, "y": {}}, [{"terms": {"x": 1, "y": -1}, "ub": 1}], {"x": 1, "y": 1},
                          sense="maximise"), UNBOUNDED)
    assert r.check["passed"] is True and r.check["certificate"] == "ray"
    ray, point = r.certificate["ray"], r.certificate["feasible_point"]
    assert ray["x"] - ray["y"] <= 1e-9 and ray["x"] + ray["y"] > 0 and point["x"] - point["y"] <= 1 + 1e-6


def test_an_optimum_that_comes_without_duals_is_checked_and_rests_on_the_solvers_bound(monkeypatch) -> None:
    real = programmes._mathopt

    def no_duals(*args, **kwargs):
        raw = real(*args, **kwargs)
        raw.pop("duals", None)
        return raw

    monkeypatch.setattr(programmes, "_mathopt", no_duals)
    r = _draft(solve_lp(**PLANTS))
    assert r.optimal is True and r.check["certificate"] == "" and r.certificate == {}
    assert "rests on the solver's own bound (36)" in r.warning_card["message"]


def test_an_integer_variable_in_a_linear_programme_is_refused() -> None:
    r = _blocked(solve_lp({"x": {"integer": True}}, objective={"x": 1}), INPUT_INVALID)
    assert "solve_mip" in r.reason


@pytest.mark.parametrize(("variables", "constraints", "objective", "word"), [
    ({"x": {}}, [{"terms": {"y": 1}, "ub": 1}], {"x": 1}, "'y'"),
    ({"x": {"lb": 3, "ub": 1}}, [], {"x": 1}, "lb"),
    ({"x": {}}, [{"terms": {"x": 1}}], {"x": 1}, "bound"),
    ({"x": {}}, [], {"z": 1}, "'z'"),
])
def test_a_model_that_does_not_make_sense_is_refused_before_solving(variables, constraints, objective, word) -> None:
    r = _blocked(solve_lp(variables, constraints, objective), INPUT_INVALID)
    assert word in r.reason and r.evidence[0]["external_solver_ran"] is False


# --- integer programmes ----------------------------------------------------------------------------------------------

def test_an_integer_programme_matches_a_brute_force() -> None:
    rng = random.Random(7)
    names = ["a", "b", "c", "d"]
    rows = [{f: rng.randint(1, 9) for f in names} for _ in range(3)]
    caps = [rng.randint(15, 30) for _ in rows]
    gains = {f: rng.randint(1, 12) for f in names}
    best = max(sum(gains[f] * v for f, v in zip(names, point)) for point in itertools.product(range(6), repeat=4)
               if all(sum(row[f] * v for f, v in zip(names, point)) <= cap for row, cap in zip(rows, caps)))
    r = _draft(solve_mip({f: {"ub": 5, "integer": True} for f in names},
                         [{"terms": row, "ub": cap} for row, cap in zip(rows, caps)], gains, sense="maximise"))
    assert r.optimal is True and r.objective == pytest.approx(best) and r.bound == pytest.approx(best)
    assert all(float(v).is_integer() for v in r.solution["values"].values())
    assert "SCIP" in r.evidence[0]["engine"] and "bound" in r.warning_card["message"]


def test_integer_infeasibility_has_its_own_code() -> None:
    _blocked(solve_mip({"a": {"ub": 10, "integer": True}}, [{"terms": {"a": 2}, "lb": 3, "ub": 3}], {"a": 1}),
             INFEASIBLE)


def test_a_feasible_answer_not_proven_optimal_is_a_draft_that_names_the_gap(monkeypatch) -> None:
    real = programmes._mathopt

    def stopped(*args, **kwargs):
        raw = real(*args, **kwargs)
        return {**raw, "status": "FEASIBLE", "limit": "TIME", "bound": 12.0}

    monkeypatch.setattr(programmes, "_mathopt", stopped)
    r = _draft(solve_mip({"a": {"ub": 10, "integer": True}, "b": {"ub": 10, "integer": True}},
                         [{"terms": {"a": 2, "b": 3}, "ub": 7.5}], {"a": 3, "b": 4}, sense="maximise"),
               NOT_PROVEN_OPTIMAL)
    assert r.optimal is False and r.objective == pytest.approx(10) and r.gap == pytest.approx(2)
    assert "12" in r.warning_card["message"] and "gap" in r.warning_card["message"]


def test_a_time_limit_with_no_solution_has_its_own_code(monkeypatch) -> None:
    monkeypatch.setattr(programmes, "_mathopt", lambda *a, **k: {"status": "NO_SOLUTION_FOUND", "limit": "TIME"})
    _blocked(solve_mip({"a": {"ub": 1, "integer": True}}, objective={"a": 1}), TIME_LIMIT)


# --- constraint models -------------------------------------------------------------------------------------------------

def test_a_constraint_model_matches_a_brute_force() -> None:
    domain = range(0, 6)
    best = max(x + 2 * y - z for x, y, z in itertools.product(domain, repeat=3)
               if len({x, y, z}) == 3 and 2 * x + 3 * y + z <= 17 and x + y >= 3)
    r = _draft(solve_cp({v: {"lb": 0, "ub": 5} for v in "xyz"},
                        [{"terms": {"x": 2, "y": 3, "z": 1}, "ub": 17}, {"terms": {"x": 1, "y": 1}, "lb": 3}],
                        {"x": 1, "y": 2, "z": -1}, sense="maximise", all_different=[["x", "y", "z"]]))
    assert r.optimal is True and r.objective == best and "CP-SAT" in r.evidence[0]["engine"]


def test_a_constraint_model_with_no_objective_finds_a_solution_and_claims_no_optimum() -> None:
    r = _draft(solve_cp({v: {"lb": 1, "ub": 3} for v in "abc"}, all_different=[["a", "b", "c"]]))
    assert r.optimal is None and sorted(r.solution["values"].values()) == [1, 2, 3]


def test_an_unsatisfiable_constraint_model_has_its_own_code() -> None:
    _blocked(solve_cp({v: {"lb": 0, "ub": 1} for v in "abc"}, all_different=[["a", "b", "c"]]), INFEASIBLE)


@pytest.mark.parametrize(("variables", "constraints", "word"), [
    ({"x": {"lb": 0}}, [], "upper bound"),
    ({"x": {"lb": 0, "ub": 3}}, [{"terms": {"x": 1.5}, "ub": 2}], "integer"),
    ({"x": {"lb": 0, "ub": 1e300}}, [], "too large"),
    ({"x": {"lb": 0, "ub": 3}}, [{"terms": {"x": 2.0 ** 60}, "ub": 2}], "too large"),
])
def test_a_constraint_model_needs_finite_bounds_and_integer_coefficients(variables, constraints, word) -> None:
    assert word in _blocked(solve_cp(variables, constraints), INPUT_INVALID).reason


def test_a_constraint_model_whose_sums_could_overflow_is_rejected_by_cp_sat_with_its_reason() -> None:
    big = 2 ** 40
    r = _blocked(solve_cp({"x": {"lb": 0, "ub": big}}, [{"terms": {"x": big}, "ub": 5}]), MODEL_INVALID)
    assert "CP-SAT rejected the model" in r.reason and r.evidence[0]["external_solver_ran"] is True


# --- knapsack and job shop ---------------------------------------------------------------------------------------------

def test_a_knapsack_matches_a_brute_force() -> None:
    rng = random.Random(11)
    values = [rng.randint(5, 60) for _ in range(12)]
    weights = [[rng.randint(1, 20) for _ in values], [rng.randint(1, 20) for _ in values]]
    capacities = [60, 55]
    best = max(sum(values[i] for i in pick) for k in range(13) for pick in itertools.combinations(range(12), k)
               if all(sum(row[i] for i in pick) <= cap for row, cap in zip(weights, capacities)))
    r = _draft(solve_knapsack(values, weights, capacities))
    assert r.optimal is True and r.objective == best


def test_the_classic_job_shop_has_makespan_eleven() -> None:
    r = _draft(solve_job_shop(JOBS))
    assert r.optimal is True and r.objective == 11 and r.solution["makespan"] == 11
    assert [len(job) for job in r.solution["schedule"]] == [3, 3, 2]


# --- assignment and flows ----------------------------------------------------------------------------------------------

def test_an_assignment_matches_a_brute_force_square_and_rectangular() -> None:
    rng = random.Random(3)
    square = [[rng.randint(1, 40) for _ in range(5)] for _ in range(5)]
    best = min(sum(square[i][p[i]] for i in range(5)) for p in itertools.permutations(range(5)))
    r = _draft(solve_assignment(square))
    assert r.optimal is True and r.objective == best and r.check["certificate"] == "no_negative_cycle"
    wide = [[rng.randint(1, 40) for _ in range(5)] for _ in range(3)]
    best = min(sum(wide[i][p[i]] for i in range(3)) for p in itertools.permutations(range(5), 3))
    assert _draft(solve_assignment(wide)).objective == best
    tall = [list(col) for col in zip(*wide)]
    assert _draft(solve_assignment(tall)).objective == best


def test_an_assignment_respects_forbidden_pairs_and_reports_when_none_exists() -> None:
    r = _draft(solve_assignment([[1, None], [None, 1]]))
    assert sorted((p["row"], p["col"]) for p in r.solution["pairs"]) == [(0, 0), (1, 1)] and r.objective == 2
    _blocked(solve_assignment([[1, None], [1, None]]), INFEASIBLE)


def test_the_textbook_maximum_flow_and_its_cut() -> None:
    arcs = [("s", "v1", 16), ("s", "v2", 13), ("v1", "v3", 12), ("v2", "v1", 4), ("v2", "v4", 14), ("v3", "v2", 9),
            ("v3", "t", 20), ("v4", "v3", 7), ("v4", "t", 4)]
    r = _draft(solve_max_flow([{"from": a, "to": b, "capacity": c} for a, b, c in arcs], "s", "t"))
    assert r.optimal is True and r.objective == 23 and r.check["certificate"] == "min_cut"
    assert r.check["details"]["cut_capacity"] == 23 and "s" in r.solution["min_cut"] and "t" not in r.solution["min_cut"]


def test_the_or_tools_guide_minimum_cost_flow() -> None:
    starts, ends = [0, 0, 1, 1, 1, 2, 2, 3, 4], [1, 2, 2, 3, 4, 3, 4, 4, 2]
    capacities, costs = [15, 8, 20, 4, 10, 15, 4, 20, 5], [4, 4, 2, 2, 6, 1, 3, 2, 3]
    arcs = [{"from": a, "to": b, "capacity": c, "cost": u} for a, b, c, u in zip(starts, ends, capacities, costs)]
    r = _draft(solve_min_cost_flow(arcs, {0: 20, 3: -5, 4: -15}))
    assert r.optimal is True and r.objective == 150 and r.check["certificate"] == "no_negative_cycle"


def test_flow_inputs_that_cannot_balance_or_cannot_be_met() -> None:
    arcs = [{"from": "a", "to": "b", "capacity": 1, "cost": 1}]
    assert "sum to zero" in _blocked(solve_min_cost_flow(arcs, {"a": 2, "b": -1}), INPUT_INVALID).reason
    _blocked(solve_min_cost_flow(arcs, {"a": 2, "b": -2}), INFEASIBLE)
    assert "differ" in _blocked(solve_max_flow(arcs, "a", "a"), INPUT_INVALID).reason


# --- routing -----------------------------------------------------------------------------------------------------------

def test_a_route_matches_a_brute_force_and_is_never_called_optimal() -> None:
    best = min(sum(DISTANCES[a][b] for a, b in itertools.pairwise((0, *p, 0)))
               for p in itertools.permutations(range(1, 5)))
    r = _draft(solve_routing(DISTANCES, local_search="guided", time_limit=1), NOT_PROVEN_OPTIMAL)
    assert r.objective == best and r.optimal is False and r.bound is None
    assert "no bound" in r.warning_card["message"] and "routing library" in r.evidence[0]["engine"]


def test_routes_with_capacities_and_time_windows_are_checked() -> None:
    loads = _draft(solve_routing(DISTANCES, vehicles=2, demands=[0, 3, 4, 2, 5], capacities=[8, 8], time_limit=1),
                   NOT_PROVEN_OPTIMAL)
    routes = loads.solution["routes"]
    assert sorted(n for route in routes for n in route["nodes"][1:-1]) == [1, 2, 3, 4]
    assert all(route["load"] <= 8 for route in routes)
    windows = [[0, 100], [0, 5], [5, 30], [0, 20], [0, 40]]
    timed = _draft(solve_routing(DISTANCES, vehicles=2, time_windows=windows, time_limit=1), NOT_PROVEN_OPTIMAL)
    for route in timed.solution["routes"]:
        for node, arrival in zip(route["nodes"], route["arrivals"]):
            assert windows[node][0] <= arrival <= windows[node][1]


def test_routing_with_no_solution_says_it_is_not_a_proof_of_none() -> None:
    r = _blocked(solve_routing(DISTANCES, demands=[0, 3, 4, 2, 5], capacities=[8], time_limit=1), NO_SOLUTION)
    assert "not a proof" in r.reason


# --- limits ------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("call", "code"), [
    (lambda: solve_lp(**PLANTS, time_limit=61), TIME_LIMIT_TOO_LONG),
    (lambda: solve_lp({f"x{i}": {} for i in range(5001)}, objective={"x0": 1}), INPUT_TOO_LARGE),
    (lambda: solve_routing([[0] * 201 for _ in range(201)]), INPUT_TOO_LARGE),
    (lambda: solve_assignment([[1] * 101 for _ in range(101)]), INPUT_TOO_LARGE),
    (lambda: solve_job_shop([[{"machine": 0, "duration": 1}]] * 101), INPUT_TOO_LARGE),
    (lambda: solve_knapsack([1] * 1001, [1] * 1001, [5]), INPUT_TOO_LARGE),
])
def test_size_and_time_limits_are_refused_with_their_own_codes(call, code) -> None:
    r = _blocked(call(), code)
    assert r.evidence[0]["external_solver_ran"] is False


# --- a wrong answer planted between the solver and G6's check is caught -----------------------------------------------

def _plant(monkeypatch, module, name, edit) -> None:
    real = getattr(module, name)

    def planted(*args, **kwargs):
        raw = real(*args, **kwargs)
        edit(raw)
        return raw

    monkeypatch.setattr(module, name, planted)


def _bump(values: dict, key: str, by: float) -> None:
    values[key] += by


def _first_pair_takes_the_seconds_column(raw: dict) -> None:
    raw["pairs"][0] = (raw["pairs"][0][0], raw["pairs"][1][1])


def _drop_a_visit(raw: dict) -> None:
    route = raw["routes"][0]
    del route[1]


PLANTED = {
    "lp": (programmes, "_mathopt", lambda raw: _bump(raw["values"], "doors", 1), lambda: solve_lp(**PLANTS)),
    "lp_duals": (programmes, "_mathopt", lambda raw: raw.update(duals=[0.0, 1.5, 0.5]), lambda: solve_lp(**PLANTS)),
    "mip": (programmes, "_mathopt", lambda raw: raw.update(objective=11.0),
            lambda: solve_mip({"a": {"ub": 9, "integer": True}}, [{"terms": {"a": 1}, "ub": 4.5}], {"a": 1},
                              sense="maximise")),
    "cp": (programmes, "_cp_sat", lambda raw: raw["values"].update(x=raw["values"]["y"]),
           lambda: solve_cp({v: {"lb": 0, "ub": 3} for v in "xyz"}, all_different=[["x", "y", "z"]])),
    "knapsack": (combinatorial, "_knapsack", lambda raw: raw.update(chosen=[0, 1, 2]),
                 lambda: solve_knapsack([60, 100, 120], [10, 20, 30], [50])),
    "job_shop": (combinatorial, "_job_shop", lambda raw: raw["starts"][0].__setitem__(1, raw["starts"][0][0]),
                 lambda: solve_job_shop(JOBS)),
    "assignment": (networks, "_assignment", _first_pair_takes_the_seconds_column,
                   lambda: solve_assignment([[4, 1, 3], [2, 0, 5], [3, 2, 2]])),
    "max_flow": (networks, "_max_flow", lambda raw: raw.update(value=raw["value"] + 1),
                 lambda: solve_max_flow([{"from": "s", "to": "t", "capacity": 3}], "s", "t")),
    "min_cost_flow": (networks, "_min_cost_flow", lambda raw: raw.update(flows=[1, 1, 1, 0, 1], cost=6),
                      lambda: solve_min_cost_flow([
                          {"from": "a", "to": "b", "capacity": 2, "cost": 1},
                          {"from": "a", "to": "c", "capacity": 2, "cost": 3},
                          {"from": "b", "to": "d", "capacity": 1, "cost": 1},
                          {"from": "b", "to": "c", "capacity": 2, "cost": 1},
                          {"from": "c", "to": "d", "capacity": 2, "cost": 1}], {"a": 2, "d": -2})),
    "routing": (networks, "_routing", _drop_a_visit, lambda: solve_routing(DISTANCES, time_limit=1)),
}


@pytest.mark.parametrize("case", list(PLANTED))
def test_a_planted_wrong_answer_is_refuted_and_blocked(case: str, monkeypatch) -> None:
    module, name, edit, call = PLANTED[case]
    _plant(monkeypatch, module, name, edit)
    r = _blocked(call(), CERTIFICATE_REFUTED)
    assert r.check["passed"] is False and r.check["failures"] and r.evidence[0]["external_solver_ran"] is True
    assert r.objective is None and r.solution == {} and r.optimal is False
