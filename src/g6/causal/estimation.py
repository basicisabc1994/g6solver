# SPDX-License-Identifier: Apache-2.0
"""``estimate_effect``: a causal effect estimated with DoWhy from a graph and inline rows, then tried three ways.

In order, each step refusing with its own code:

1. the input: the method, the number of refutation simulations and the seed; the graph; the treatment and outcome
   (known, observed, different); the rows, inline JSON only, never a path (finite numbers or true/false, the same
   columns in every row, one column per observed node and none for an unobserved one, at least :data:`MIN_ROWS` and at
   most :data:`MAX_ROWS` rows and :data:`MAX_COLUMNS` columns), and names DoWhy writes its own columns under refused;
2. G6's pure-Python identification: the method's route (backdoor, instrumental variable or frontdoor) must identify
   the effect (``G6_E_CAUSAL_NOT_IDENTIFIABLE``, or ``METHOD_NOT_APPLICABLE`` naming the methods that fit);
3. DoWhy (``pip install 'g6[causal]'``): ``CausalModel.identify_effect`` must agree with G6 (``ENGINES_DISAGREE``),
   then ``estimate_effect`` with no effect modifiers (so linear regression fits the treatment plus the adjustment set
   alone), its confidence interval at DoWhy's 95% (analytic, or :data:`CI_BOOTSTRAP` bootstrap resamples);
4. for linear regression, G6's own least-squares fit must match (``CERTIFICATE_REFUTED``);
5. unless the caller turns it off, DoWhy's placebo, random-common-cause and data-subset refuters, each on a fresh copy
   of the rows (DoWhy's estimators add columns to the data they are given, which a refuter would otherwise reuse) and
   with a fixed seed, judged by G6 against the confidence interval's half-width (``REFUTED``, keeping every number).
   A refuter DoWhy cannot run on this estimate, or whose change never reaches the estimator, is reported as untested
   (``REFUTATION_INCOMPLETE``), never as a pass.

The time limit (at most 600 seconds) is checked between steps; a DoWhy call that has started is not interrupted, and
the size limits bound each one. numpy's global generator is seeded for DoWhy's bootstrap and restored afterwards. The
best state is ``qualified-draft``: the estimate rests on the graph, which data cannot confirm.
"""
from __future__ import annotations

import importlib
import math
import time
from collections.abc import Mapping
from typing import Any

from g6 import __version__
from g6.causal import checks, criteria, dowhy_engine
from g6.causal.envelope import (
    ENGINE_ERROR,
    INPUT_INVALID,
    INPUT_TOO_LARGE,
    METHOD_NOT_APPLICABLE,
    NOT_IDENTIFIABLE,
    UNKNOWN_NODE,
    Refusal,
)
from g6.causal.graph import CausalGraph
from g6.causal.ops import check_time, deadline_for, effect_nodes, read, run
from g6.causal.schema import CausalResult

METHODS = {
    "backdoor.linear_regression": "backdoor",
    "backdoor.propensity_score_stratification": "backdoor",
    "backdoor.propensity_score_weighting": "backdoor",
    "iv.instrumental_variable": "iv",
    "frontdoor.two_stage_regression": "frontdoor",
}
LINEAR = "backdoor.linear_regression"
BINARY = frozenset({"backdoor.propensity_score_stratification", "backdoor.propensity_score_weighting"})
ROUTES = {"backdoor": "the backdoor criterion", "iv": "the instrumental-variable criterion",
          "frontdoor": "the frontdoor criterion"}
MAX_ROWS = 10_000
MIN_ROWS = 20
MAX_COLUMNS = 50
DEFAULT_SIMULATIONS = 20
MAX_SIMULATIONS = 100
CI_BOOTSTRAP = 100
CONFIDENCE_LEVEL = 0.95
DEFAULT_ESTIMATE_TIME_LIMIT_S = 120.0
#: Columns DoWhy's estimators and refuters write into the data; a node of the same name would be overwritten.
RESERVED = frozenset({
    "placebo", "w_random", "propensity_score", "strata", "dbar", "d_y", "dbar_y", "dummy_outcome", "ips_weight",
    "tips_weight", "cips_weight", "ips_normalized_weight", "tips_normalized_weight", "cips_normalized_weight",
    "ips_stabilized_weight", "tips_stabilized_weight", "cips_stabilized_weight",
})
REFUTER_RUNS = (("placebo_treatment_refuter", {"placebo_type": "permute"}), ("random_common_cause", {}),
                ("data_subset_refuter", {"subset_fraction": 0.8}))


def _settings(method: Any, simulations: Any, seed: Any) -> None:
    if method not in METHODS:
        raise Refusal(INPUT_INVALID, f"method must be one of {', '.join(METHODS)}, not {method!r}")
    if isinstance(simulations, bool) or not isinstance(simulations, int) or simulations < 1:
        raise Refusal(INPUT_INVALID, f"simulations must be a whole number of at least 1, not {simulations!r}")
    if simulations > MAX_SIMULATIONS:
        raise Refusal(INPUT_TOO_LARGE, f"simulations={simulations}, more than the {MAX_SIMULATIONS} a refuter runs")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**31:
        raise Refusal(INPUT_INVALID, f"seed must be a whole number from 0 to 2**31 - 1, not {seed!r}")


def _reserved(graph: CausalGraph) -> None:
    for name in graph.nodes:
        if name in RESERVED or name.startswith("placebo_"):
            raise Refusal(INPUT_INVALID, f"the node name {name!r} is one DoWhy writes its own column under; rename the "
                                         "node and its column")


def _value(row: int, column: str, value: Any) -> tuple[float, str]:
    if isinstance(value, bool):
        return float(value), "true/false"
    if isinstance(value, int | float):
        try:
            number = float(value)
        except OverflowError:
            number = math.inf
        if math.isfinite(number):
            return number, "number"
    raise Refusal(INPUT_INVALID, f"row {row}, column {column!r}: {value!r} is not a finite number or true/false; data "
                                 "takes numbers and true/false only")


def _columns(graph: CausalGraph, data: Any, treatment: str) -> tuple[dict[str, list[float]], dict[str, str]]:
    """The rows read into one list of floats per column, and each column's kind (number or true/false)."""
    if not isinstance(data, list) or not data:
        raise Refusal(INPUT_INVALID, "data must be a non-empty list of rows, each an object {column: value}; G6 reads "
                                     "inline rows only, never a file")
    if len(data) > MAX_ROWS:
        raise Refusal(INPUT_TOO_LARGE, f"{len(data)} rows, more than the {MAX_ROWS} G6 estimates from")
    if not isinstance(data[0], Mapping):
        raise Refusal(INPUT_INVALID, "row 0 is not an object {column: value}")
    names = list(data[0])
    if len(names) > MAX_COLUMNS:
        raise Refusal(INPUT_TOO_LARGE, f"{len(names)} columns, more than the {MAX_COLUMNS} G6 estimates from")
    for name in names:
        if name not in graph.nodes:
            raise Refusal(UNKNOWN_NODE, f"data has the column {name!r}, which is not a node of the graph")
        if name in graph.unobserved:
            raise Refusal(INPUT_INVALID, f"data has a column for {name!r}, which the graph marks unobserved: mark it "
                                         "observed, or leave the column out")
    missing = [n for n in graph.observed if n not in names]
    if missing:
        raise Refusal(INPUT_INVALID, f"the graph's observed node {missing[0]!r} has no column in data: add the column, "
                                     "or mark the node unobserved ([observed=no])")
    columns: dict[str, list[float]] = {n: [] for n in names}
    kinds: dict[str, set[str]] = {n: set() for n in names}
    expected = set(names)
    for i, row in enumerate(data):
        if not isinstance(row, Mapping) or set(row) != expected:
            got = sorted(row) if isinstance(row, Mapping) else type(row).__name__
            raise Refusal(INPUT_INVALID, f"row {i} has the columns {got}, row 0 has {sorted(names)}: every row needs "
                                         "the same columns")
        for name in names:
            number, kind = _value(i, name, row[name])
            columns[name].append(number)
            kinds[name].add(kind)
    for name, kind in kinds.items():
        if len(kind) > 1:
            raise Refusal(INPUT_INVALID, f"column {name!r} mixes true/false with numbers; give one kind per column")
    smallest = max(MIN_ROWS, len(names) + 2)
    if len(data) < smallest:
        raise Refusal(INPUT_INVALID, f"{len(data)} rows; estimation needs at least {smallest} here")
    if len(set(columns[treatment])) < 2:
        raise Refusal(INPUT_INVALID, f"the treatment {treatment!r} takes one value in every row, so its effect cannot "
                                     "be estimated")
    return columns, {n: kinds[n].pop() for n in names}


def _route(found: Mapping[str, Any], method: str, treatment: str, outcome: str) -> str:
    if found["route"] == "no_directed_path":
        raise Refusal(METHOD_NOT_APPLICABLE, f"there is no directed path from {treatment} to {outcome} in the graph, "
                                             "so the graph itself says the effect is zero; there is nothing to "
                                             "estimate")
    if not found["identifiable"]:
        raise Refusal(NOT_IDENTIFIABLE, f"the effect of {treatment} on {outcome} is not identifiable from this graph: "
                                        f"{found['reason']}")
    route = METHODS[method]
    if found[route] is None:
        fits = [r for r in ROUTES if found[r] is not None]
        methods = [m for m, r in METHODS.items() if r in fits]
        raise Refusal(METHOD_NOT_APPLICABLE, f"{method} needs {ROUTES[route]}, which this graph does not meet for "
                                             f"{treatment} on {outcome}; it identifies the effect by "
                                             f"{' and '.join(ROUTES[r] for r in fits)}: use {', '.join(methods)}")
    return route


def _dowhy_estimate(model: Any, estimand: Any, method: str) -> Any:
    """DoWhy's estimate (the seam a test plants a wrong answer at)."""
    return model.estimate_effect(estimand, method_name=method, confidence_intervals=True, effect_modifiers=[],
                                 method_params={"num_simulations": CI_BOOTSTRAP}, target_units="ate")


def _dowhy_refute(model: Any, estimand: Any, estimate: Any, refuter: str, simulations: int, seed: int,
                  extra: Mapping[str, Any]) -> Any:
    numpy = importlib.import_module("numpy")
    return model.refute_estimate(estimand, estimate, method_name=refuter, show_progress_bar=False,
                                 num_simulations=simulations, n_jobs=1, random_state=numpy.random.RandomState(seed),
                                 **extra)


def _interval(estimate: Any) -> list[float]:
    numpy = importlib.import_module("numpy")
    try:
        bounds = [float(b) for b in numpy.ravel(numpy.asarray(estimate.get_confidence_intervals(), dtype=float))]
    except (TypeError, ValueError) as exc:
        raise Refusal(ENGINE_ERROR, f"DoWhy gave no usable confidence interval ({exc})"[:300]) from None
    if len(bounds) != 2 or not all(math.isfinite(b) for b in bounds):
        raise Refusal(ENGINE_ERROR, f"DoWhy gave no usable confidence interval ({bounds})")
    return sorted(bounds)


def _used(theirs: Mapping[str, Any], route: str) -> list[str] | None:
    if route == "backdoor":
        return theirs["default_backdoor"]
    if route == "iv":
        return theirs["instruments"] or None
    return theirs["frontdoor"] or None


def estimate_effect(graph: Any, data: Any, treatment: Any, outcome: Any, method: str = LINEAR, *,
                    refute: bool = True, simulations: int = DEFAULT_SIMULATIONS, seed: int = 0,
                    time_limit: float = DEFAULT_ESTIMATE_TIME_LIMIT_S, request_id: str = "",
                    run_id: str = "") -> CausalResult:
    """The effect of ``treatment`` on ``outcome`` estimated with DoWhy by ``method`` from ``graph`` and the inline
    ``data`` rows, cross-checked, recomputed for linear regression and refuted unless ``refute`` is false."""
    def work(state: dict[str, Any]) -> dict[str, Any]:
        deadline = deadline_for(time_limit)
        _settings(method, simulations, seed)
        g = read(state, graph)
        t, y = effect_nodes(g, treatment, outcome)
        _reserved(g)
        columns, kinds = _columns(g, data, t)
        state["data"] = {"rows": len(columns[t]), "columns": list(columns), "kinds": kinds}
        if method in BINARY and not set(columns[t]) <= {0.0, 1.0}:
            raise Refusal(INPUT_INVALID, f"{method} needs a binary treatment (0 and 1, or true and false); "
                                         f"{t!r} takes other values")
        state.update(method=method, refute=bool(refute), engines=[{"name": "pure_python", "version": __version__}],
                     refutations=[], steps=[])
        check_time(deadline, "reading the input")
        found = criteria.identify(g, t, y, deadline=deadline)
        route = _route(found, method, t, y)
        check_time(deadline, "G6's identification")
        seen: list[dict[str, Any]] = []
        state["warnings_seen"], state["dowhy_log"] = seen, []
        with dowhy_engine.session(seed, seen=seen, logged=state["dowhy_log"]):
            _with_dowhy(state, g, columns, t, y, route, found, deadline)
        return state

    def _with_dowhy(state: dict[str, Any], g: CausalGraph, columns: dict[str, list[float]], t: str, y: str,
                    route: str, found: Mapping[str, Any], deadline: float) -> None:
        def step(name: str, since: float) -> None:
            state["steps"].append({"step": name, "seconds": round(time.perf_counter() - since, 3)})

        dowhy_engine.load()
        state["engines"].append({"name": "dowhy", "version": dowhy_engine.version()})
        pandas = importlib.import_module("pandas")
        frame = pandas.DataFrame({n: columns[n] for n in g.observed})
        frame[t] = frame[t].astype(bool) if method in BINARY else frame[t].astype(float)
        since = time.perf_counter()
        model = dowhy_engine.model(g, frame.copy(), t, y)
        estimand = model.identify_effect()
        theirs = dowhy_engine.summarise(estimand)
        agreement = checks.compare_identification(g, t, y, found, theirs,
                                                  lambda s: dowhy_engine.valid_backdoor(g, t, y, s))
        used = _used(theirs, route)
        if used is None and agreement["agreed"]:
            agreement["agreed"] = False
            agreement["differences"].append(f"G6 identifies the effect by {ROUTES[route]}, DoWhy does not, so DoWhy "
                                            f"cannot estimate it by {method}")
        state["agreement"] = agreement
        step("identification", since)
        if not agreement["agreed"]:
            return
        if method == LINEAR and not checks.full_rank(columns, t, y, used):
            others = ", ".join(used) or "a constant"
            raise Refusal(INPUT_INVALID, f"in this data {t} is a linear combination of {others}, so least squares "
                                         "cannot separate its effect")
        check_time(deadline, "the identification")
        since = time.perf_counter()
        estimate = _dowhy_estimate(model, estimand, method)
        value = float(estimate.value)
        interval = _interval(estimate)
        half = (interval[1] - interval[0]) / 2
        step("estimate", since)
        if route == "backdoor":
            estimand_text = criteria.backdoor_estimand(t, y, used)
        elif route == "frontdoor":
            estimand_text = criteria.frontdoor_estimand(t, y, used)
        elif len(used) == 1:
            estimand_text = criteria.iv_estimand(t, y, used[0])
        else:
            estimand_text = (f"two-stage least squares on the instruments {', '.join(used)}, which gives the effect of "
                             f"{t} on {y} if that effect is the same for every unit (linear and homogeneous)")
        state["estimate"], state["confidence_interval"] = value, interval
        state["answer"] = {
            "treatment": t, "outcome": y, "method": method, "route": route, "estimand": estimand_text,
            {"backdoor": "adjustment_set", "iv": "instruments", "frontdoor": "mediators"}[route]: used,
            "effect": f"the change in the mean of {y} when {t} is set from 0 to 1, averaged over the rows",
            "confidence_level": CONFIDENCE_LEVEL, "rows": len(columns[t]), "seed": seed,
            "simulations": simulations if refute else 0, "identification": found["reason"],
        }
        state["tolerance"] = {"refutation": half, "recomputation_relative": checks.RECOMPUTE_RELATIVE}
        if method == LINEAR:
            state["recomputation"] = checks.recompute_linear(columns, t, y, used, value, half)
            if not state["recomputation"]["matched"]:
                return
        if not refute:
            return
        for k, (name, extra) in enumerate(REFUTER_RUNS):
            check_time(deadline, "the estimate" if k == 0 else f"the {REFUTER_RUNS[k - 1][0]}")
            since = time.perf_counter()
            fresh = dowhy_engine.model(g, frame.copy(), t, y)
            try:
                result = _dowhy_refute(fresh, estimand, estimate, name, simulations, seed + 1 + k, extra)
            except Exception as exc:  # noqa: BLE001 -- DoWhy failing to run a refuter is reported, never a pass
                state["refutations"].append(checks.not_run(name, value, exc))
            else:
                state["refutations"].append(checks.judge_refutation(name, value, half, result.new_effect,
                                                                    getattr(result, "refutation_result", None)))
            step(name, since)

    return run("estimate_effect", work, request_id, run_id)


__all__ = ["BINARY", "CI_BOOTSTRAP", "CONFIDENCE_LEVEL", "DEFAULT_ESTIMATE_TIME_LIMIT_S", "DEFAULT_SIMULATIONS",
           "LINEAR", "MAX_COLUMNS", "MAX_ROWS", "MAX_SIMULATIONS", "METHODS", "MIN_ROWS", "RESERVED", "REFUTER_RUNS",
           "estimate_effect"]
