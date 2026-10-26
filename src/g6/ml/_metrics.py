# SPDX-License-Identifier: Apache-2.0
"""The family's scores, computed by G6's own code from the true values and the predictions, never taken from a
library's or a caller's report.

``_``-prefixed, so it never registers. Pure Python, so a score is the same with or without an extra, and the code that
fits a model is not the code that scores it.

* :func:`scores` gives every metric of a task: accuracy and balanced accuracy for classification (labels compared as
  given), R squared, mean absolute error and root mean squared error for regression; each as its source defines it
  (``g6.ml._sources``).
* :func:`baseline` predicts what a model that ignores the features would: the most frequent class of the training
  rows, or the mean of their targets.
* :func:`compare` sets a model's held-out score against the baseline's on the same rows, with the lift, the
  train-held-out gap and a 95% percentile interval for both the score and the lift from seeded resamples of the
  held-out rows by group (rows with identical features are drawn together: a cluster bootstrap), so a small held-out
  set shows how little it can say, and repeating rows neither narrows the interval nor adds an independent row.
"""
from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Sequence
from typing import Any

CLASSIFICATION = ("accuracy", "balanced_accuracy")
REGRESSION = ("r2", "mae", "rmse")
#: Whether a larger value of the metric is better.
HIGHER_IS_BETTER = {"accuracy": True, "balanced_accuracy": True, "r2": True, "mae": False, "rmse": False}
RESAMPLES = 1000
#: Resamples times held-out rows, at most: the interval's pure-Python work in G6's own process is bounded, and a large
#: held-out set gets fewer resamples, said in the evidence (G6's own bound: fewer resamples, never a label).
MAX_RESAMPLE_WORK = 5_000_000
LEVEL = 0.95
#: A train score this far above the held-out one adds a note (G6's own threshold: a note, never a label).
GAP_NOTE = 0.10


def accuracy(truth: Sequence[Any], pred: Sequence[Any]) -> float:
    """The share of predictions equal to the true value."""
    return sum(t == p for t, p in zip(truth, pred, strict=True)) / len(truth)


def balanced_accuracy(truth: Sequence[Any], pred: Sequence[Any]) -> float:
    """The mean, over the classes present in ``truth``, of the share of each class predicted as itself (recall)."""
    hits: Counter = Counter()
    totals = Counter(truth)
    for t, p in zip(truth, pred, strict=True):
        if t == p:
            hits[t] += 1
    return sum(hits[c] / n for c, n in totals.items()) / len(totals)


def r2(truth: Sequence[float], pred: Sequence[float]) -> float | None:
    """One less the residual sum of squares over the total sum of squares about the mean; ``None`` when the true
    values are all equal (it is then undefined). Equality is tested exactly: the float mean of equal values can be
    off by a rounding, which would leave a tiny positive total and a meaningless score."""
    if all(t == truth[0] for t in truth):
        return None
    mean = math.fsum(truth) / len(truth)
    total = math.fsum((t - mean) ** 2 for t in truth)
    if total <= 0:  # unequal values whose squared spread underflows: too small to divide by
        return None
    return 1 - math.fsum((t - p) ** 2 for t, p in zip(truth, pred, strict=True)) / total


def mae(truth: Sequence[float], pred: Sequence[float]) -> float:
    """The mean absolute difference."""
    return math.fsum(abs(t - p) for t, p in zip(truth, pred, strict=True)) / len(truth)


def rmse(truth: Sequence[float], pred: Sequence[float]) -> float:
    """The square root of the mean squared difference."""
    return math.sqrt(math.fsum((t - p) ** 2 for t, p in zip(truth, pred, strict=True)) / len(truth))


_FUNCTIONS = {"accuracy": accuracy, "balanced_accuracy": balanced_accuracy, "r2": r2, "mae": mae, "rmse": rmse}


def metric(name: str, truth: Sequence[Any], pred: Sequence[Any]) -> float | None:
    return _FUNCTIONS[name](truth, pred)


def scores(task: str, truth: Sequence[Any], pred: Sequence[Any]) -> dict[str, float | None]:
    """Every metric of ``task`` for these predictions."""
    names = CLASSIFICATION if task == "classification" else REGRESSION
    return {name: _round(metric(name, truth, pred)) for name in names}


def baseline(task: str, train_truth: Sequence[Any], n: int) -> list[Any]:
    """``n`` predictions of a model that ignores the features: the most frequent training class (the first in sorted
    order among ties) or the mean training target."""
    if task == "classification":
        counts = Counter(train_truth)
        top = max(sorted(counts, key=str), key=lambda c: counts[c])
        return [top] * n
    return [math.fsum(train_truth) / len(train_truth)] * n


def lift(name: str, model: float, base: float) -> float:
    """How much better the model is than the baseline, in the metric's own units (positive is better)."""
    return model - base if HIGHER_IS_BETTER[name] else base - model


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    at = (len(ordered) - 1) * q
    low, high = math.floor(at), math.ceil(at)
    return ordered[low] + (ordered[high] - ordered[low]) * (at - low)


def _clusters(n: int, groups: Sequence[Any] | None) -> list[list[int]]:
    """The positions of each group, in order of first appearance; each row its own group when ``groups`` is None."""
    if groups is None:
        return [[i] for i in range(n)]
    if len(groups) != n:
        raise ValueError(f"{len(groups)} groups for {n} rows")
    members: dict[Any, list[int]] = {}
    for i, g in enumerate(groups):
        members.setdefault(g, []).append(i)
    return list(members.values())


def compare(task: str, name: str, truth: Sequence[Any], pred: Sequence[Any], base: Sequence[Any],
            train_truth: Sequence[Any] | None = None, train_pred: Sequence[Any] | None = None, *,
            seed: int, groups: Sequence[Any] | None = None, resamples: int = RESAMPLES) -> dict[str, Any]:
    """The headline: the model's held-out ``name`` against the baseline's on the same rows, the lift, the train score
    and the gap, and a :data:`LEVEL` percentile interval for the score and the lift from ``resamples`` resamples of the
    held-out rows drawn with ``seed``, by group: each resample draws as many groups as there are, with replacement, and
    takes every row of each (``groups``, one per row: rows with identical features share one). ``held_out_groups`` is
    the number of independent rows. ``clearly_better`` is true only when the lift's whole interval is above zero."""
    model = metric(name, truth, pred)
    reference = metric(name, truth, base)
    clusters = _clusters(len(truth), groups)
    out: dict[str, Any] = {"metric": name, "held_out_rows": len(truth), "held_out_groups": len(clusters),
                           "held_out": _round(model), "baseline": _round(reference),
                           "higher_is_better": HIGHER_IS_BETTER[name]}
    if model is None or reference is None:
        out.update(lift=None, clearly_better=False, interval=None,
                   note=f"{name} is undefined on these held-out rows (their true values are all equal)")
        return out
    out["lift"] = _round(lift(name, model, reference))
    if train_truth is not None and train_pred is not None:
        train = metric(name, train_truth, train_pred)
        out["train"] = _round(train)
        out["gap"] = None if train is None else _round(lift(name, train, model))
    rng = random.Random(seed)
    k = len(clusters)
    asked, resamples = resamples, max(1, min(resamples, MAX_RESAMPLE_WORK // max(1, len(truth))))
    found, lifts = [], []
    for _ in range(resamples):
        picks = [i for _ in range(k) for i in clusters[rng.randrange(k)]]
        t = [truth[i] for i in picks]
        m, b = metric(name, t, [pred[i] for i in picks]), metric(name, t, [base[i] for i in picks])
        if m is None or b is None:
            continue
        found.append(m)
        lifts.append(lift(name, m, b))
    if len(found) < resamples // 2:
        out.update(interval=None, clearly_better=False, note="too few resamples had a defined score for an interval")
        return out
    tail = (1 - LEVEL) / 2
    out["interval"] = {"level": LEVEL, "method": "percentile bootstrap of the held-out rows by group (rows with "
                                                 "identical features drawn together: a cluster bootstrap)",
                       "groups": k, "resamples": len(found), "resamples_asked": asked, "seed": seed,
                       "low": _round(_percentile(found, tail)), "high": _round(_percentile(found, 1 - tail)),
                       "lift_low": _round(_percentile(lifts, tail)), "lift_high": _round(_percentile(lifts, 1 - tail))}
    if resamples < asked:
        out["interval"]["lowered"] = (f"{resamples} resamples, not {asked}: resamples times held-out rows is capped at "
                                      f"{MAX_RESAMPLE_WORK:,} so the interval's work stays bounded")
    out["clearly_better"] = out["interval"]["lift_low"] > 0
    return out


__all__ = ["CLASSIFICATION", "GAP_NOTE", "HIGHER_IS_BETTER", "LEVEL", "MAX_RESAMPLE_WORK", "REGRESSION", "RESAMPLES",
           "accuracy", "balanced_accuracy",
           "baseline", "compare", "lift", "mae", "metric", "r2", "rmse", "scores"]
