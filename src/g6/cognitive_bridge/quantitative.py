# SPDX-License-Identifier: Apache-2.0
"""Bridge evaluator-scored research traces into G6's existing quantitative ML rows."""
from __future__ import annotations

import math
from typing import Any, Sequence

#: Exactly the features passed to MLAcquisitionSelector.predict(...).
ACQUISITION_FEATURES = ("expected_information_bits", "cost", "novelty_preference",
                        "assumed_match_probability")
UTILITY_TARGET = "measured_utility"


def evaluated_trace_rows(history: Sequence[dict[str, Any]]) -> list[dict[str, float]]:
    """Create G6 adapt_sklearn-ready rows, ONLY from externally evaluated outcomes.

    The caller must supply a separate evaluator and its provenance; this
    adapter cannot independently establish that evaluator's correctness.
    Absent or non-finite evidence refuses the whole export rather than inventing
    a success target from the LLM's opinion or its own Bayesian posterior.
    """
    if not history:
        raise ValueError("no observed research episodes")
    result = []
    for row in history:
        if row.get("measured_utility") is None or not row.get("evaluator_ref"):
            raise ValueError("every exported episode needs externally scored utility and evaluator_ref")
        extracted = {name: float(row[name]) for name in ACQUISITION_FEATURES}
        extracted[UTILITY_TARGET] = float(row[UTILITY_TARGET])
        if not all(math.isfinite(v) for v in extracted.values()):
            raise ValueError("non-finite training data")
        result.append(extracted)
    return result


def sklearn_payload(history: Sequence[dict[str, Any]], *, seed: int = 7,
                    estimator: str = "random_forest") -> dict:
    """Build input to G6 adapt_sklearn.fit_eval, which owns split and leakage checks.

    This is a *payload*, not a fitted model. G6 adapt_sklearn intentionally
    discards each fitted model; in-memory MLAcquisitionSelector takes a model
    trained and held by an explicitly trusted Python caller instead.
    """
    return {"op": "fit_eval", "rows": evaluated_trace_rows(history),
            "target": UTILITY_TARGET, "features": list(ACQUISITION_FEATURES),
            "task": "regression", "seed": seed, "estimator": estimator}
