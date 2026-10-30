# SPDX-License-Identifier: Apache-2.0
"""g6.optimisation's minimise and global_search (merged from the archive's adapt_optimisation and adapt_pygad): a
declarative objective (a JSON expression tree, never code), SciPy's local methods from several starts and differential
evolution, every answer re-evaluated by G6's own interpreter at the returned point and checked against the bounds,
convergence reported, a local minimum called local, qualified-draft at best."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time

import pytest

import g6.optimisation
from g6.core.failure_modes import FailureModeRegistry
from g6.invariants import check_envelope
from g6.mcp import invoke_reply
from g6.optimisation import global_search, minimise, nonlinear
from g6.optimisation.envelope import (BEST_FOUND, CERTIFICATE_REFUTED, INPUT_INVALID, INPUT_TOO_LARGE, LOCAL_OPTIMUM,
                                      NO_SOLUTION, NOT_CONVERGED, SCIPY_UNAVAILABLE)

needs_scipy = pytest.mark.skipif(importlib.util.find_spec("scipy") is None,
                                 reason="SciPy is not installed (pip install 'g6[optim-nonlinear] @ git+https://github.com/basicisabc1994/g6solver')")

TWO_WELLS = ["+", ["^", ["-", ["^", "x", 2], 1], 2], ["*", 0.3, "x"]]   # minima near x = -1.04 and x = 0.96
X = {"x": {"lb": -2, "ub": 2}}


def _values(result) -> dict:  # noqa: ANN001
    return result.solution["values"]


def _search(result) -> dict:  # noqa: ANN001
    return result.evidence[0]["search"]


# -- D1: a local method's answer is called local, and several starts find the better basin ------------------------------

@needs_scipy
def test_one_start_finds_the_near_basin_and_several_find_the_better_one_and_say_the_starts_disagreed() -> None:
    one = minimise(X, TWO_WELLS, x0={"x": 1.0}, starts=1)
    assert one.completion_state == "qualified-draft" and one.warning_card["code"] == LOCAL_OPTIMUM
    assert _values(one)["x"] == pytest.approx(0.96, abs=0.02) and one.optimal is False
    assert "one start cannot show there is no better minimum" in one.warning_card["message"]
    many = minimise(X, TWO_WELLS, x0={"x": 1.0}, starts=6, seed=3)
    assert _values(many)["x"] == pytest.approx(-1.04, abs=0.02) and many.objective < one.objective
    assert _search(many)["distinct_minima"] == 2 and "global_search" in many.warning_card["suggested_action"]
    assert check_envelope(many.model_dump()) == []


@needs_scipy
def test_several_starts_find_the_best_of_four_basins_in_two_dimensions() -> None:
    wells = ["+", ["^", ["-", ["^", "x", 2], 1], 2], ["^", ["-", ["^", "y", 2], 1], 2],
             ["*", 0.3, "x"], ["*", 0.2, "y"]]
    result = minimise({"x": {"lb": -2, "ub": 2}, "y": {"lb": -2, "ub": 2}}, wells, x0={"x": 1.2, "y": 1.2},
                      starts=10, seed=1)
    assert _values(result)["x"] < 0 and _values(result)["y"] < 0 and _search(result)["distinct_minima"] >= 2


# -- a point where the gradient vanishes is called a minimum only when nothing near it is lower -------------------------

XY = {"x": {"lb": -2, "ub": 2}, "y": {"lb": -2, "ub": 2}}


@needs_scipy
@pytest.mark.parametrize(("variables", "expression", "x0"), [
    ({"x": {"lb": -4, "ub": 4}}, ["cos", "x"], {"x": 0}),               # a maximum: the gradient is zero there
    (X, ["-", ["^", "x", 2]], {"x": 0}),                                # a maximum
    (XY, ["-", ["^", "x", 2], ["^", "y", 2]], {"x": 0, "y": 0}),        # a saddle
], ids=["cos", "minus_x_squared", "saddle"])
def test_a_maximum_or_a_saddle_the_search_stopped_at_is_never_called_a_local_minimum(variables, expression,
                                                                                     x0) -> None:
    result = minimise(variables, expression, x0=x0, starts=1)
    assert result.warning_card["code"] != LOCAL_OPTIMUM
    assert result.warning_card["code"] == NOT_CONVERGED and "not shown to be a minimum" in result.warning_card["message"]
    lower = _search(result)["lower_point"]
    assert lower["objective"] < result.objective - 1e-6 and set(lower["values"]) == set(variables)


@needs_scipy
def test_a_plain_bowl_is_still_a_local_minimum_and_a_kink_rests_on_scipys_flag_alone() -> None:
    for variables, bowl in ((X, ["^", ["-", "x", 0.3], 2]), (XY, ["+", ["^", "x", 2], ["*", 3, ["^", ["-", "y", 1], 2]]])):
        result = minimise(variables, bowl, starts=1)
        assert result.warning_card["code"] == LOCAL_OPTIMUM and _search(result).get("lower_point") is None
    kink = minimise(X, ["abs", ["-", "x", 0.5]], method="Nelder-Mead", starts=1)
    assert kink.warning_card["code"] == LOCAL_OPTIMUM and _values(kink)["x"] == pytest.approx(0.5, abs=1e-4)
    assert "SciPy's own success flag is the only basis" in kink.warning_card["message"]


RASTRIGIN = ["+", 20, *[["-", ["^", v, 2], ["*", 10, ["cos", ["*", 6.283185307179586, v]]]] for v in ("x", "y")]]
BOX = {"x": {"lb": -5.12, "ub": 5.12}, "y": {"lb": -5.12, "ub": 5.12}}


@needs_scipy
def test_global_search_finds_the_bottom_of_a_rugged_function_and_calls_it_best_found() -> None:
    result = global_search(BOX, RASTRIGIN, seed=1)
    assert result.completion_state == "qualified-draft" and result.warning_card["code"] == BEST_FOUND
    assert abs(_values(result)["x"]) < 1e-3 and abs(_values(result)["y"]) < 1e-3 and result.objective < 1e-4
    assert result.optimal is False and "not a proven global minimum" in result.warning_card["message"]


@needs_scipy
def test_two_seeds_can_disagree_and_neither_is_called_global() -> None:
    # differential evolution's own test can pass in a local basin: seed 0 stops at f = 0.995, seed 1 at f = 0
    stuck, found = global_search(BOX, RASTRIGIN, seed=0), global_search(BOX, RASTRIGIN, seed=1)
    assert stuck.objective == pytest.approx(0.995, abs=1e-3) and found.objective < 1e-4
    for result in (stuck, found):
        assert result.completion_state == "qualified-draft" and result.warning_card["code"] == BEST_FOUND
        assert "another seed" in result.warning_card["suggested_action"]


# -- ADAPT_PYGAD-02: integer and discrete genes over a general, non-separable objective ----------------------------------

@needs_scipy
def test_global_search_takes_integer_and_discrete_variables_and_a_non_separable_objective() -> None:
    pair = ["+", ["^", ["-", ["*", "a", "b"], 12], 2], ["^", ["-", ["+", "a", "b"], 7], 2]]
    result = global_search({"a": {"ub": 10, "integer": True}, "b": {"ub": 10, "integer": True}}, pair, seed=0)
    assert sorted(_values(result).values()) == [3.0, 4.0] and result.objective == 0
    assert "integer variables at whole numbers" in " ".join(result.check["checked"])
    cost = ["+", ["table", [5.0, 3.0, 4.0], "material"], ["*", 0.5, "material"]]
    chosen = global_search({"material": {"ub": 2, "integer": True}}, cost, seed=0)
    assert _values(chosen)["material"] == 1.0 and chosen.objective == 3.5


@needs_scipy
def test_maximise_reports_the_objective_in_its_own_sign() -> None:
    hill = ["-", 4, ["^", ["-", "x", 1], 2]]
    for result in (minimise(X, hill, "maximise", starts=3), global_search(X, hill, "maximise", seed=0)):
        assert _values(result)["x"] == pytest.approx(1.0, abs=1e-4) and result.objective == pytest.approx(4.0)


# -- ADAPT_OPTIMISATION-01 and ADAPT_PYGAD-01, inverted: an unconverged run is never more than a draft that says so -----

@needs_scipy
def test_a_run_stopped_by_its_budget_is_a_draft_that_says_it_did_not_converge() -> None:
    for result in (minimise(X, TWO_WELLS, max_evaluations=5), global_search(X, TWO_WELLS, max_evaluations=10)):
        assert result.completion_state == "qualified-draft" and result.warning_card["code"] == NOT_CONVERGED
        assert result.solution["converged"] is False and _search(result)["stopped"] == "the evaluation budget"
        assert _search(result)["evaluations"] <= 10 and check_envelope(result.model_dump()) == []
        assert "not shown to be a minimum" in result.warning_card["message"]


@needs_scipy
def test_an_objective_finite_nowhere_in_the_box_is_no_solution_not_a_solver_fault() -> None:
    nowhere = ["log", ["-", "x", 5]]   # log of a negative number at every x in [0, 1]
    for result in (global_search({"x": {"lb": 0, "ub": 1}}, nowhere), minimise({"x": {"lb": 0, "ub": 1}}, nowhere)):
        assert result.completion_state == "blocked-escalated" and result.code == NO_SOLUTION, result.reason
        assert "finite" in result.reason and check_envelope(result.model_dump()) == []


# -- ADAPT_OPTIMISATION-04: every answer re-checked; a backend's word is not taken ---------------------------------------

def _liar(objective, bounds, integer, *, seed, max_evaluations):  # noqa: ANN001, ANN202
    objective([0.5])
    return {"x": [0.5], "fun": -100.0, "converged": True, "message": "trust me", "iterations": 1}


def _outside(objective, bounds, integer, *, seed, max_evaluations):  # noqa: ANN001, ANN202
    return {"x": [7.0], "fun": 0.0, "converged": True, "message": "", "iterations": 1}   # outside the box


def _grid(objective, bounds, integer, *, seed, max_evaluations):  # noqa: ANN001, ANN202
    lo, hi = bounds[0]
    points = [lo + (hi - lo) * i / 400 for i in range(401)]
    best = min(points, key=lambda p: objective([p]))
    return {"x": [best], "fun": objective([best]), "converged": True, "message": "grid of 401", "iterations": 1}


def test_a_registered_backend_is_re_checked_and_one_that_misreports_is_refused(monkeypatch) -> None:
    for name, runner in (("liar", _liar), ("outside", _outside), ("grid", _grid)):
        monkeypatch.setitem(nonlinear.SEARCH_BACKENDS, name, runner)
    lied = global_search(X, TWO_WELLS, backend="liar")
    assert lied.completion_state == "blocked-escalated" and lied.code == CERTIFICATE_REFUTED
    assert "-100" in lied.reason and lied.objective is None
    assert global_search(X, TWO_WELLS, backend="outside").code == CERTIFICATE_REFUTED
    honest = global_search(X, TWO_WELLS, backend="grid")
    assert honest.completion_state == "qualified-draft" and _values(honest)["x"] == pytest.approx(-1.04, abs=0.01)
    assert _search(honest)["backend"] == "grid" and honest.check["passed"] is True
    with pytest.raises(ValueError, match="differential_evolution"):
        nonlinear.register_backend("differential_evolution", _grid)
    assert global_search(X, TWO_WELLS, backend="nope").code == INPUT_INVALID


# -- ADAPT_OPTIMISATION-03: the objective is data, walked by a bounded interpreter, never code ---------------------------

@pytest.mark.parametrize(("expression", "says"), [
    ("x**2 + __import__('os').system('echo hi')", "not a declared variable"),
    (["^", 9, ["^", 9, 9]], "not a finite number"),
    (["exec", "x"], "unknown operation"),
    (["+"], "at least one"),
    (["/", "x"], "two"),
    (True, "a number, a variable name or a list"),
    ({"op": "+"}, "a number, a variable name or a list"),
    (["table", ["a"], "x"], "table"),
    (3.0, "names no variable"),
])
def test_an_expression_that_is_not_the_language_is_refused_and_nothing_runs(expression, says: str) -> None:
    started = time.perf_counter()
    for op in (minimise, global_search):
        result = op(X, expression)
        assert result.completion_state == "blocked-escalated" and result.code == INPUT_INVALID, result.reason
        assert says in result.reason
    assert time.perf_counter() - started < 5


def test_an_expression_past_the_size_limits_is_refused() -> None:
    deep: object = "x"
    for _ in range(nonlinear.MAX_DEPTH + 5):
        deep = ["neg", deep]
    wide = ["+", *(["*", 1, "x"] for _ in range(nonlinear.MAX_NODES))]
    for expression in (deep, wide):
        assert minimise(X, expression).code == INPUT_TOO_LARGE


def test_bounds_are_required_and_minimise_refuses_integer_variables() -> None:
    assert minimise({"x": {"lb": 0}}, ["^", "x", 2]).code == INPUT_INVALID          # no upper bound
    assert minimise({"x": {"ub": 3, "integer": True}}, ["^", "x", 2]).code == INPUT_INVALID
    assert minimise(X, TWO_WELLS, x0={"x": 5}).code == INPUT_INVALID                 # a start outside the box
    assert "global_search" in minimise({"x": {"ub": 3, "integer": True}}, ["^", "x", 2]).reason


# -- the door: MCP, ops, no sensitive op, no file path, and an honest answer without SciPy -------------------------------

def test_both_ops_round_trip_over_mcp_and_take_no_path_or_code() -> None:
    reply = invoke_reply("optimisation", "minimise", json.dumps({"variables": X, "expression": TWO_WELLS}))
    assert check_envelope(reply) == [] and reply["ok"] is True
    assert {"minimise", "global_search"} <= set(invoke_reply("optimisation", "ops")["result"]["ops"])
    for field in ({"data_path": "C:/Windows/win.ini"}, {"code": "import os"}, {"objective_code": "x"}):
        refused = invoke_reply("optimisation", "global_search",
                               json.dumps({"variables": X, "expression": TWO_WELLS, **field}))
        assert refused["ok"] is False and refused["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"
    assert g6.optimisation.__g6_sensitive_ops__ == ()


def test_without_scipy_both_ops_are_blocked_and_say_what_to_install(monkeypatch) -> None:
    for name in [m for m in sys.modules if m == "scipy" or m.startswith("scipy.")]:
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setitem(sys.modules, "scipy", None)
    for op in ("minimise", "global_search"):
        reply = invoke_reply("optimisation", op, json.dumps({"variables": X, "expression": TWO_WELLS}))
        assert reply["completion_state"] == "blocked-escalated" and reply["warning_card"]["code"] == SCIPY_UNAVAILABLE
        assert "pip install 'g6[optim-nonlinear] @ git+https://github.com/basicisabc1994/g6solver'" in reply["warning_card"]["suggested_action"]
        assert check_envelope(reply) == []


def test_importing_the_package_loads_no_scipy(tmp_path) -> None:
    probe = ("import sys, g6.optimisation\n"
             "assert not [m for m in sys.modules if m.split('.')[0] == 'scipy'], 'importing g6.optimisation loaded scipy'\n")
    subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, check=True, timeout=120)


def test_d2_the_package_declares_wrong_optimiser_and_its_extra() -> None:
    assert "wrong_optimiser" in g6.optimisation.__g6_mitigates__
    assert "g6.optimisation" not in FailureModeRegistry.planned()
    assert g6.optimisation.__g6_extras__ == {"optim": ("ortools",), "optim-nonlinear": ("scipy",)}
