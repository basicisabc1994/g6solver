# SPDX-License-Identifier: Apache-2.0
"""What a fitting member reports: the held-out measurement G6 computes itself from what the child predicted, in
words, with the card it earns.

``_``-prefixed, so it never registers. Pure Python.

:func:`measure` scores the child's predictions against the true values the child never saw: the headline metric
(balanced accuracy for an imbalanced classification, accuracy otherwise, R squared for regression) against the
baseline on the same held-out rows, with the lift, the train-held-out gap and a 95% interval for both
(``g6.ml._metrics.compare``), and every metric of the task for the model, the training rows and the baseline.
:func:`said` puts it in one sentence and picks the card: :data:`g6.ml._types.MEASURED` only when the lift's whole
interval is above zero, :data:`g6.ml._types.BELOW_BASELINE` otherwise. Both are ``qualified-draft``: a measurement on
rows the extension held out itself, never ``verified``. A held-out headline at or above
:data:`g6.ml._types.LEAK_SINGLE_FEATURE` is no measurement at all: features can carry the target between them (a
total and its parts) where the one-feature probes see nothing, so it is refused with ``G6_E_ML_LEAKAGE``.

:func:`held_out_use` records each scoring of a held-out set under ``G6_HOME`` (the set's hash, the op and the time,
nothing of the rows) and says how many times those held-out rows were scored before. Every scored reply says it, as a
caution: a result kept after several tries on the same held-out rows is optimistic. It is never a gate.
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
from typing import Any

from g6.core.storage import open_db, state_path
from g6.ml import _metrics
from g6.ml._fit import Encoded
from g6.ml._types import BELOW_BASELINE, FIT_FAILED, LEAK_SINGLE_FEATURE, LEAKAGE, MEASURED, MLError, Prepared

#: Where each scoring of a held-out set is recorded, under ``G6_HOME``.
SCORINGS = ("ml", "held_out_scorings.sqlite3")


def headline_metric(prepared: Prepared) -> str:
    if prepared.spec.task == "regression":
        return "r2"
    return prepared.balance["headline_metric"] if prepared.balance else "accuracy"


def held_out_use(prepared: Prepared, op: str) -> dict[str, Any]:
    """Record that ``op`` scored these held-out rows and say how many times they were scored before (by op), or why
    that could not be counted. A caution, never a gate: no count refuses a result or changes its card."""
    digest = prepared.split.held_out_sha256
    out: dict[str, Any] = {"kind": "ml_held_out_use", "held_out_sha256": digest, "op": op, "scored_before": None,
                           "earlier_ops": {}, "kept": "the held-out set's hash, the op and the time, under G6_HOME; "
                                                   "nothing of the rows",
                           "gate": "a caution, never a gate: no count refuses a result or changes its card"}
    try:
        conn = open_db(state_path(*SCORINGS))
        try:
            with conn:
                conn.execute("CREATE TABLE IF NOT EXISTS scorings (held_out_sha256 TEXT NOT NULL, op TEXT NOT NULL, "
                             "at TEXT NOT NULL)")
                conn.execute("CREATE INDEX IF NOT EXISTS scorings_by_set ON scorings (held_out_sha256)")
                earlier = conn.execute("SELECT op, COUNT(*) FROM scorings WHERE held_out_sha256 = ? GROUP BY op "
                                       "ORDER BY op", (digest,)).fetchall()
                conn.execute("INSERT INTO scorings VALUES (?, ?, ?)",
                             (digest, op, _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")))
        finally:
            conn.close()
    except (OSError, ValueError, sqlite3.Error) as exc:
        out["not_counted"] = f"{type(exc).__name__}: {exc}"[:200]
        return out
    out["earlier_ops"] = {row[0]: row[1] for row in earlier}
    out["scored_before"] = sum(out["earlier_ops"].values())
    return out


def _caution(use: dict[str, Any]) -> str:
    if use.get("scored_before") is None:
        return f"earlier scorings of these held-out rows could not be counted ({use.get('not_counted', 'no record')})"
    n = use["scored_before"]
    text = f"these held-out rows were scored {n} time{'' if n == 1 else 's'} before"
    if n >= 1:  # at least one earlier scoring (the threshold, stated as one)
        ops = ", ".join(f"{op} {k}" for op, k in use["earlier_ops"].items())
        text += f" ({ops}), so a result kept after several tries on them is optimistic"
    return text


def measure(prepared: Prepared, encoded: Encoded, held_pred: list[Any], train_pred: list[Any], *,
            op: str) -> dict[str, Any]:
    """The headline and every metric, from the true values of the rows the child was given no answer for, and the
    record of this scoring (:func:`held_out_use`)."""
    split, task = prepared.split, prepared.spec.task
    held_truth = [encoded.y[i] for i in split.held_out]
    train_truth = [encoded.y[i] for i in split.train]
    if len(held_pred) != len(held_truth) or len(train_pred) != len(train_truth):
        raise MLError(FIT_FAILED, f"the fit returned {len(held_pred)} held-out and {len(train_pred)} training "
                                  f"predictions for {len(held_truth)} and {len(train_truth)} rows")
    base = _metrics.baseline(task, train_truth, len(held_truth))
    name = headline_metric(prepared)
    headline = _metrics.compare(task, name, held_truth, held_pred, base, train_truth, train_pred,
                                seed=prepared.spec.seed, groups=[encoded.groups[i] for i in split.held_out])
    shown = (lambda v: encoded.labels.get(v, v)) if task == "classification" else (lambda v: round(v, 6))
    headline["baseline_predicts"] = shown(base[0]) if base else None
    return {"headline": headline,
            "scores": {"held_out": _metrics.scores(task, held_truth, held_pred),
                       "train": _metrics.scores(task, train_truth, train_pred),
                       "baseline_on_held_out": _metrics.scores(task, held_truth, base)},
            "scored_by": "g6.ml._metrics, in G6's process, from true values the fitting process never saw",
            "held_out_use": held_out_use(prepared, op)}


_NAMES = {"accuracy": "accuracy", "balanced_accuracy": "balanced accuracy", "r2": "R squared",
          "mae": "mean absolute error", "rmse": "root mean squared error"}


def metric_name(name: str) -> str:
    """A metric's name in words."""
    return _NAMES.get(name, name.replace("_", " "))


def _f(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _too_good(prepared: Prepared, measured: dict[str, Any]) -> None:
    """Refuse a held-out headline at or above :data:`LEAK_SINGLE_FEATURE`: features together may carry the target."""
    h = measured["headline"]
    if h.get("held_out") is None or not h["higher_is_better"] or h["held_out"] < LEAK_SINGLE_FEATURE:
        return
    features = prepared.spec.features
    named = ", ".join(repr(f) for f in features[:10]) + (f" and {len(features) - 10} more" if len(features) > 10
                                                          else "")
    raise MLError(LEAKAGE, f"the features together predict the target almost exactly on the held-out rows "
                           f"({metric_name(h['metric'])} {h['held_out']:.3f}, at or above {LEAK_SINGLE_FEATURE}) though "
                           "no one feature does: they may carry the target between them (a total and its parts, or "
                           f"fields filled in from the answer); look at {named}. Nothing is reported as a measurement; "
                           f"{_caution(measured.get('held_out_use') or {})}",
                  [*prepared.evidence, {"kind": "ml_scores", **measured}])


def _gap_note(headline: dict[str, Any]) -> str:
    """The note a training-row gap adds (rule ``overfit_gap``): a gap of :data:`_metrics.GAP_NOTE` or more, for a
    higher-is-better headline; otherwise nothing."""
    gap = headline.get("gap")
    if gap is not None and gap >= _metrics.GAP_NOTE and headline["higher_is_better"]:
        return f", {gap:.3f} higher, so it fits its training rows far better than new ones"
    return ""


def said(what: str, prepared: Prepared, measured: dict[str, Any]) -> tuple[str, str]:
    """The result's message and its card code; a held-out headline too good to be a measurement is refused."""
    _too_good(prepared, measured)
    h, split = measured["headline"], prepared.split
    name = metric_name(h["metric"])
    distinct = h.get("held_out_groups", len(split.held_out))
    text = (f"{what}, scored on {len(split.held_out)} held-out rows"
            + (f" ({distinct} distinct)" if distinct != len(split.held_out) else "")
            + f" the extension drew from the rows' own hash, not the seed ({len(split.train)} training rows; seed "
            f"{prepared.spec.seed} for the fits and resamples): "
            f"{name} {_f(h['held_out'])} against {_f(h['baseline'])} for a baseline that ignores the features")
    interval = h.get("interval")
    if h.get("lift") is not None:
        text += f" (lift {h['lift']:+.3f}"
        text += (f", 95% interval {interval['lift_low']:+.3f} to {interval['lift_high']:+.3f})" if interval else ")")
    if h.get("train") is not None:
        text += f"; on its training rows {_f(h['train'])}"
        text += _gap_note(h)
    if prepared.balance and prepared.balance["imbalanced"]:
        text += (f"; the classes are imbalanced (the smallest holds {prepared.balance['minority_share']:.1%} of "
                 "the training rows), so the headline is balanced accuracy")
    if h.get("note"):
        text += f"; {h['note']}"
    if measured.get("held_out_use"):
        text += f"; {_caution(measured['held_out_use'])}"
    if h.get("clearly_better"):
        return text + ". It beats the baseline on these rows.", MEASURED
    return text + ". It does not clearly beat the baseline on these rows.", BELOW_BASELINE


def predictions(encoded: Encoded, task: str, values: list[Any]) -> list[dict[str, Any]]:
    """Predictions for the rows to predict, as the caller's own label values (or numbers)."""
    shown = (lambda v: encoded.labels.get(v, v)) if task == "classification" else (lambda v: round(float(v), 6))
    return [{"row": i, "prediction": shown(v)} for i, v in enumerate(values)]


__all__ = ["SCORINGS", "headline_metric", "held_out_use", "measure", "metric_name", "predictions", "said"]
