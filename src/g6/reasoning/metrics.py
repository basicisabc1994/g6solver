# SPDX-License-Identifier: Apache-2.0
"""Computed metrics with explicit thresholds; invalid inputs never become zero loss."""
import math
from collections import Counter

DIRECTIONS = {name: "higher" for name in ("accuracy", "exact_match", "precision_macro", "recall_macro", "f1_macro",
                                         "precision_micro", "recall_micro", "f1_micro", "precision_weighted",
                                         "recall_weighted", "f1_weighted")}
DIRECTIONS.update({name: "lower" for name in ("mse", "mae", "rmse")})
#: The most values one score or interval is computed over.
MAX_VALUES = 100_000


def _pairs(predictions, truth):
    if not predictions or len(predictions) != len(truth) or len(truth) > MAX_VALUES:
        raise ValueError("[G6_E_METRIC_INPUT] equal nonempty lists of at most 100000 values required")
    return list(zip(predictions, truth))


def _number(value):
    if isinstance(value, bool):
        raise ValueError("[G6_E_METRIC_INPUT] booleans are not numeric measurements")
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("[G6_E_METRIC_INPUT] numeric measurements required") from None
    if not math.isfinite(number):
        raise ValueError("[G6_E_METRIC_INPUT] finite measurements required")
    return number


def score(name: str, predictions: list, truth: list) -> float:
    pairs = _pairs(predictions, truth)
    if name not in DIRECTIONS:
        raise ValueError("[G6_E_METRIC_INPUT] unknown metric")
    if name in ("mse", "mae", "rmse"):
        errors = [_number(p) - _number(y) for p, y in pairs]
        value = math.fsum(abs(e) if name == "mae" else e * e for e in errors) / len(pairs)
        value = math.sqrt(value) if name == "rmse" else value
    else:
        if any(not isinstance(v, (str, int, float, bool)) or isinstance(v, float) and not math.isfinite(v)
               for pair in pairs for v in pair):
            raise ValueError("[G6_E_METRIC_INPUT] finite scalar labels required")
        if name in ("accuracy", "exact_match", "precision_micro", "recall_micro", "f1_micro"):
            return sum(p == y for p, y in pairs) / len(pairs)
        kind, average = name.split("_")
        support = Counter(truth)
        predicted = Counter(predictions)
        correct = Counter(y for p, y in pairs if p == y)
        values = []
        for label in set(truth) | set(predictions):
            tp = correct[label]
            fp = predicted[label] - tp
            fn = support[label] - tp
            numerator, denominator = ((tp, tp + fp) if kind == "precision" else
                                      (tp, tp + fn) if kind == "recall" else (2 * tp, 2 * tp + fp + fn))
            weight = support[label] if average == "weighted" else 1
            if not weight:
                continue
            if not denominator:
                raise ValueError("[G6_E_METRIC_INPUT] undefined per-class metric denominator")
            values.append((numerator / denominator, weight))
        value = math.fsum(v * w for v, w in values) / sum(w for _, w in values)
    if not math.isfinite(value):
        raise ValueError("[G6_E_METRIC_INPUT] metric overflow")
    return value


def exact_interval(successes: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Two-sided exact binomial interval by tail inversion (70 bisections).

    Binomial sampling is assumed; it says nothing about how cases were selected.
    At n=1 the boundaries are alpha/2 and 1-alpha/2, not a zero-width interval.
    """
    if type(n) is not int or type(successes) is not int or not 0 <= successes <= n or not 1 <= n <= MAX_VALUES:
        raise ValueError("[G6_E_METRIC_INPUT] valid success count and sample size required")
    if not 0 < alpha < 1:
        raise ValueError("[G6_E_METRIC_INPUT] alpha must be between zero and one")

    def tail(p, start, stop):
        return math.fsum(math.exp(math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
                                 + k * math.log(p) + (n - k) * math.log1p(-p)) for k in range(start, stop))

    def root(start, stop, increasing):
        low, high = 0.0, 1.0
        for _ in range(70):
            mid = (low + high) / 2
            if mid == low or mid == high:
                break
            below = tail(mid, start, stop) < alpha / 2
            if below == increasing:
                low = mid
            else:
                high = mid
        return (low + high) / 2
    return (0.0 if successes == 0 else root(successes, n + 1, True),
            1.0 if successes == n else root(0, successes + 1, False))


def evaluate(predictions: list, truth: list, thresholds: dict[str, float]) -> dict:
    """One threshold per named metric; no default threshold, weighted score or baseline claim."""
    try:
        if not thresholds:
            raise ValueError("no metrics requested")
        rows = {}
        for name, threshold in thresholds.items():
            threshold = _number(threshold)
            value = score(name, predictions, truth)
            interval = exact_interval(sum(p == y for p, y in zip(predictions, truth)), len(truth)) \
                if name in ("accuracy", "exact_match") else None
            rows[name] = {"value": value, "direction": DIRECTIONS[name], "threshold": threshold,
                          "passed": value <= threshold if DIRECTIONS[name] == "lower" else value >= threshold,
                          "interval": interval, "interval_method": "exact_binomial" if interval else "not_available"}
    except Exception:
        return {"metrics": {}, "completion_state": "blocked-escalated", "request_id": "", "run_id": "",
                "warning_card": {"code": "G6_E_METRIC_INPUT", "message": "metric input or computation failed"},
                "evidence": {"provenance": "no_score"}}
    return {"metrics": rows, "n": len(truth), "completion_state": "qualified-draft", "warning_card": None,
            "request_id": "", "run_id": "", "evidence": {"provenance": "computed", "baseline_checked": False}}
