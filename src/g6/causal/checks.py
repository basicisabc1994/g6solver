# SPDX-License-Identifier: Apache-2.0
"""What G6 checks in g6.causal, apart from DoWhy: where the two engines must agree, G6's verdict on each refuter, and
G6's own recomputation of a linear-regression estimate.

* Agreement: on d-separation, on the backdoor paths and whether each is open, and on identification: whether the effect
  is identifiable, whether there is a directed path, whether a backdoor set exists, every backdoor set either engine
  finds valid by the other's check, and every instrument and frontdoor set DoWhy names valid by G6's criterion. DoWhy
  names only parents of the treatment as instruments and tests the frontdoor's first condition by d-separation, so G6
  may find more of either; that alone is not a disagreement, but an instrument or set of DoWhy's that fails G6's
  criterion is.
* Refutation: a tolerance from the estimate's own confidence interval, its half-width. The placebo effect (the treatment
  shuffled) must lie within it of zero; the estimate with a random common cause added, and on random subsets of the
  rows, must lie within it of the estimate. DoWhy's p-values are kept as DoWhy reports them; G6's verdict does not use
  them.
* Recomputation: for ``backdoor.linear_regression``, ordinary least squares of the outcome on an intercept, the
  treatment and the adjustment set DoWhy used, solved by G6 with ``numpy.linalg.lstsq`` from the rows as given, never
  through DoWhy. The treatment's coefficient must match DoWhy's estimate within one part in a million of the larger of the two
  and the confidence interval's half-width.
"""
from __future__ import annotations

import importlib
import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from g6.causal import criteria
from g6.causal.graph import CausalGraph

RECOMPUTE_RELATIVE = 1e-6
_FLOOR = 1e-12
#: A placebo or a random common cause changes what the estimator sees, so an effect that comes back equal to the
#: estimate (to one part in 10**12) means the change never reached the estimator. DoWhy 0.14 does this with
#: frontdoor.two_stage_regression: it copies the estimator with only the outer estimand changed, and the two stages
#: keep the real treatment and their own adjustment sets.
UNCHANGED_MEANS_UNTESTED = frozenset({"placebo_treatment_refuter", "random_common_cause"})
_SAME = 1e-12
REFUTERS = {
    "placebo_treatment_refuter": "the placebo (the treatment shuffled across rows)",
    "random_common_cause": "a random common cause added",
    "data_subset_refuter": "random 80% subsets of the rows",
}


def _agreement(compared: list[str], differences: list[str], **extra: Any) -> dict[str, Any]:
    return {"compared": compared, "agreed": not differences, "differences": differences[:10], **extra}


def compare_d_separation(mine: bool, theirs: bool) -> dict[str, Any]:
    differences = [] if mine == theirs else [f"G6 finds them {'' if mine else 'not '}d-separated, DoWhy "
                                             f"{'' if theirs else 'not '}d-separated"]
    return _agreement(["whether they are d-separated"], differences)


def compare_backdoor_paths(mine: Sequence[Mapping[str, Any]], theirs: Sequence[Sequence[str]],
                           theirs_open: Mapping[tuple[str, ...], bool]) -> dict[str, Any]:
    differences = []
    own = {tuple(p["path"]): p for p in mine}
    other = {tuple(p) for p in theirs}
    for path in sorted(set(own) - other):
        differences.append(f"G6 lists the backdoor path {own[path]['rendered']}, DoWhy does not")
    for path in sorted(other - set(own)):
        differences.append(f"DoWhy lists the backdoor path {' - '.join(path)}, G6 does not")
    for path in sorted(set(own) & other):
        if own[path]["open"] != theirs_open[path]:
            differences.append(f"G6 finds {own[path]['rendered']} {'open' if own[path]['open'] else 'blocked'}, "
                               f"DoWhy {'open' if theirs_open[path] else 'blocked'}")
    return _agreement(["the backdoor paths", "whether each is open"], differences)


def compare_identification(graph: CausalGraph, treatment: str, outcome: str, mine: Mapping[str, Any],
                           theirs: Mapping[str, Any], dowhy_valid: Callable[[list[str]], bool], *,
                           parts: Sequence[str] = ("identifiable", "backdoor", "iv", "frontdoor")) -> dict[str, Any]:
    """G6's :func:`~g6.causal.criteria.identify` answer against DoWhy's (``dowhy_engine.summarise``)."""
    compared, differences = [], []
    if "identifiable" in parts:
        compared += ["whether the effect is identifiable", "whether there is a directed path"]
        if bool(mine["identifiable"]) != bool(theirs["identifiable"]):
            differences.append(f"G6 finds the effect {'identifiable' if mine['identifiable'] else 'not identifiable'}"
                               f" ({mine.get('route') or 'by no route'}), DoWhy "
                               f"{'identifiable' if theirs['identifiable'] else 'not identifiable'}")
        if (mine.get("route") == "no_directed_path") != bool(theirs["no_directed_path"]):
            differences.append("G6 and DoWhy disagree on whether there is a directed path from the treatment to the "
                               "outcome")
    if "backdoor" in parts and not theirs["no_directed_path"] and mine.get("route") != "no_directed_path":
        compared += ["whether a backdoor set exists", "every backdoor set, by both engines' checks"]
        own_sets = (mine.get("backdoor") or {}).get("sets", [])
        if bool(own_sets) != bool(theirs["backdoor_found"]):
            mine_says = f"the backdoor sets {own_sets}" if own_sets else "no backdoor set"
            theirs_says = f"finds {theirs['backdoor_sets']}" if theirs["backdoor_found"] else "none"
            differences.append(f"G6 finds {mine_says}, DoWhy {theirs_says}")
        for chosen in theirs["backdoor_sets"]:
            if set(chosen) & graph.unobserved or not criteria.is_backdoor_set(graph, treatment, outcome, chosen):
                differences.append(f"DoWhy's backdoor set {chosen} fails G6's backdoor criterion")
        for chosen in own_sets:
            if not dowhy_valid(chosen):
                differences.append(f"G6's backdoor set {chosen} fails DoWhy's check")
    if "iv" in parts:
        compared.append("each instrument DoWhy names, by G6's criterion")
        for name in theirs["instruments"]:
            if not criteria.is_instrument(graph, name, treatment, outcome):
                why = "it is unobserved" if name in graph.unobserved else "it fails G6's criterion"
                differences.append(f"DoWhy names {name} an instrument, but {why}")
    if "frontdoor" in parts and theirs["frontdoor"]:
        compared.append("DoWhy's frontdoor set, by G6's criterion")
        if not criteria.is_frontdoor_set(graph, treatment, outcome, theirs["frontdoor"]):
            differences.append(f"DoWhy's frontdoor set {theirs['frontdoor']} fails G6's frontdoor criterion")
    return _agreement(compared, differences, dowhy=dict(theirs))


def judge_refutation(refuter: str, estimate: float, half_width: float, new_effect: Any,
                     dowhy_result: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """G6's verdict on one refuter's result: within the confidence interval's half-width, or not."""
    tolerance = max(abs(half_width), _FLOOR * max(1.0, abs(estimate)))
    new = _number(new_effect)
    new = float("nan") if new is None else new
    out: dict[str, Any] = {"refuter": refuter, "what": REFUTERS.get(refuter, refuter), "estimate": estimate,
                           "new_effect": new if math.isfinite(new) else None, "tolerance": tolerance, "ran": True}
    if not math.isfinite(new):
        return out | {"tested": False, "passed": None, "moved": None,
                      "verdict": f"{refuter}: DoWhy gave no finite effect, so it did not test the estimate"}
    if refuter in UNCHANGED_MEANS_UNTESTED and abs(new - estimate) <= _SAME * max(1.0, abs(estimate)):
        return out | {"tested": False, "passed": None, "moved": 0.0,
                      "verdict": f"{refuter}: the effect came back exactly the estimate ({new:.6g}), so its change "
                                 "never reached the estimator and it did not test the estimate"}
    if refuter == "placebo_treatment_refuter":
        moved = abs(new)
        verdict = (f"placebo: with the treatment shuffled the effect is {new:.4g}, "
                   f"{'within' if moved <= tolerance else 'beyond'} {tolerance:.3g} of zero (the half-width of the "
                   "estimate's confidence interval)")
    else:
        moved = abs(new - estimate)
        verdict = (f"{refuter}: with {REFUTERS.get(refuter, refuter)} the estimate moves from {estimate:.4g} to "
                   f"{new:.4g}, by {moved:.3g}, {'within' if moved <= tolerance else 'beyond'} {tolerance:.3g} (the "
                   "half-width of the estimate's confidence interval)")
    if dowhy_result:
        out["dowhy_p_value"] = _number(dowhy_result.get("p_value"))
        out["dowhy_significant"] = bool(dowhy_result.get("is_statistically_significant"))
    return out | {"tested": True, "passed": moved <= tolerance, "moved": moved, "verdict": verdict}


def not_run(refuter: str, estimate: float, error: BaseException) -> dict[str, Any]:
    """A refuter DoWhy could not run on this estimate: reported with its error, neither a pass nor a failure."""
    why = f"{type(error).__name__}: {error}"[:300]
    return {"refuter": refuter, "what": REFUTERS.get(refuter, refuter), "estimate": estimate, "new_effect": None,
            "tolerance": None, "ran": False, "tested": False, "passed": None, "moved": None, "error": why,
            "verdict": f"{refuter}: DoWhy could not run it on this estimate ({why}), so it did not test the "
                       "estimate"}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def design(columns: Mapping[str, Sequence[float]], treatment: str, outcome: str,
           adjustment: Sequence[str]) -> tuple[Any, Any]:
    """The least-squares design matrix (intercept, treatment, adjustment set) and the outcome, as numpy arrays."""
    numpy = importlib.import_module("numpy")
    rows = len(columns[outcome])
    x = numpy.column_stack([numpy.ones(rows), *(numpy.asarray(columns[n], dtype=float)
                                                for n in (treatment, *adjustment))])
    return x, numpy.asarray(columns[outcome], dtype=float)


def full_rank(columns: Mapping[str, Sequence[float]], treatment: str, outcome: str, adjustment: Sequence[str]) -> bool:
    numpy = importlib.import_module("numpy")
    x, _ = design(columns, treatment, outcome, adjustment)
    return int(numpy.linalg.matrix_rank(x)) == x.shape[1]


def recompute_linear(columns: Mapping[str, Sequence[float]], treatment: str, outcome: str,
                     adjustment: Sequence[str], dowhy_value: float, half_width: float) -> dict[str, Any]:
    """G6's own least-squares estimate of the treatment's coefficient, against DoWhy's."""
    numpy = importlib.import_module("numpy")
    x, y = design(columns, treatment, outcome, adjustment)
    coefficients = numpy.linalg.lstsq(x, y, rcond=None)[0]
    mine = float(coefficients[1])
    difference = abs(mine - dowhy_value)
    tolerance = max(RECOMPUTE_RELATIVE * max(abs(mine), abs(dowhy_value), abs(half_width)), _FLOOR)
    terms = " + ".join([treatment, *adjustment])
    return {"g6": mine, "dowhy": dowhy_value, "difference": difference, "tolerance": tolerance,
            "matched": difference <= tolerance, "model": f"{outcome} ~ 1 + {terms}",
            "adjustment_set": list(adjustment),
            "how": "ordinary least squares solved by G6 with numpy.linalg.lstsq from the rows as given, not by DoWhy"}


__all__ = ["RECOMPUTE_RELATIVE", "REFUTERS", "UNCHANGED_MEANS_UNTESTED", "compare_backdoor_paths",
           "compare_d_separation", "compare_identification", "design", "full_rank", "judge_refutation", "not_run",
           "recompute_linear"]
