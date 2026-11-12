# SPDX-License-Identifier: Apache-2.0
"""Bridge evaluator-scored research traces into G6's existing quantitative ML rows."""
from __future__ import annotations

from typing import Any, Sequence

from .codes import FrozenMap, unclaimed
from .snapshot import publish, number, snapshot, text

#: Exactly the features passed to MLAcquisitionSelector.predict(...).
ACQUISITION_FEATURES = ("expected_information_bits", "cost", "novelty_preference",
                        "assumed_match_probability")
UTILITY_TARGET = "measured_utility"


def evaluated_trace_rows(history: Sequence[dict[str, Any]]) -> list[dict[str, float]]:
    """Create G6 adapt_sklearn-ready rows, ONLY from externally evaluated outcomes.

    The caller must supply a separate evaluator and its provenance; this
    adapter cannot independently establish that evaluator's correctness.
    Absent or non-finite evidence refuses the whole export rather than inventing
    a success target from the LLM's opinion or its own Bayesian posterior. Each
    feature and the target is a number as the trace holds it (``snapshot.number``):
    a flag, text or null is refused, never converted.
    """
    history = snapshot(history, where="history")
    if not history:
        raise ValueError("no observed research episodes")
    result = []
    for index, row in enumerate(history):
        if type(row) is not FrozenMap:
            raise TypeError("every episode of a history is a mapping")
        if row.get("measured_utility") is None or not row.get("evaluator_ref"):
            raise ValueError("every exported episode needs externally scored utility and evaluator_ref")
        result.append({name: float(number(row[name], where=f"history[{index}].{name}"))  # a number, as a float
                       for name in (*ACQUISITION_FEATURES, UTILITY_TARGET)})
    return publish(unclaimed(result, where="the trace rows"), where="evaluated_trace_rows")


def sklearn_payload(history: Sequence[dict[str, Any]], *, seed: int = 7,
                    estimator: str = "random_forest") -> dict:
    """Build input to G6 adapt_sklearn.fit_eval, which owns split and leakage checks.

    This is a *payload*, not a fitted model. G6 adapt_sklearn intentionally
    discards each fitted model; in-memory MLAcquisitionSelector takes a model
    trained and held by an explicitly trusted Python caller instead.
    """
    history = snapshot(history, where="history")
    seed = snapshot(seed, where="seed")
    estimator = text(estimator, where="estimator")
    return publish(unclaimed({"op": "fit_eval", "rows": evaluated_trace_rows(history),
                      "target": UTILITY_TARGET, "features": list(ACQUISITION_FEATURES),
                      "task": "regression", "seed": number(seed, where="seed"), "estimator": estimator},
                     where="a payload"), where="sklearn_payload")
