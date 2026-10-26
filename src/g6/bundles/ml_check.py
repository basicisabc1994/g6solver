# SPDX-License-Identifier: Apache-2.0
"""Bundle: ML result check — is a model's reported score real, and is its test set clean?

Works offline, with no AI model. Paste the training data and the labelled test data (CSV) and name the target column.
Then paste the model's predictions for the test rows, or a Python script that makes them. A script runs out of process
under ``g6.core.sandbox`` and is given ``train.csv`` and ``test_features.csv`` (the test rows WITHOUT the target column),
so it cannot read the answers; it writes ``predictions.csv``. G6 then:

- looks for leakage: test rows that also appear in the training data, columns that equal or perfectly predict the
  target in the training data, and identifier columns whose values the two share;
- scores the predictions itself against the held-back labels (accuracy and macro F1 for a class target, MAE and RMSE
  for a numeric one), with exact binomial accuracy intervals, bootstrap intervals for other metrics and a paired
  baseline gate (a numeric model must also have its whole MAE interval below the training mean's MAE); at most
  100,000 test rows are scored;
- compares any score you claim with its own.

It cannot establish that the test set represents future data or that the model holds up anywhere else, so the best
label is ``qualified-draft``.
"""
from __future__ import annotations

import csv
import io
import math
import random
import re
from collections import Counter, defaultdict
from typing import Any

from g6.bundles import Bundle, BundleResult, Field, weakest

RESAMPLES = 1000
_ID_SHARE = 0.98          # a column whose values are this unique is treated as an identifier
_CLAIM = re.compile(r"\b(accuracy|acc|macro[- ]?f1|f1|mae|rmse)\b\s*(?:[:=]|of|is|was)?\s*(\d+(?:\.\d+)?)\s*(%)?",
                    re.IGNORECASE)
_SCRIPT_HELP = ("Read train.csv and test_features.csv from the current folder; write predictions.csv with one "
                "prediction per test row, in order. The script never sees the test labels.")


def _table(text: str) -> tuple[list[str], list[dict[str, str]]]:
    reader = csv.DictReader(io.StringIO(text.strip()))
    header = [h.strip() for h in reader.fieldnames or []]
    rows = [{(k or "").strip(): (v or "").strip() for k, v in row.items() if k is not None} for row in reader]
    return header, rows


def _number(value: str) -> float | None:
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _column(text: str, names: tuple[str, ...]) -> list[str]:
    """One prediction per line (a header naming the column is dropped); the first CSV field of each line."""
    lines = [row[0].strip() for row in csv.reader(io.StringIO(text.strip())) if row and row[0].strip()]
    return lines[1:] if lines and lines[0].lower() in names else lines


def _identifier(column: str, values: list[str]) -> bool:
    """Unique per row, and named like an identifier or not numbers (a numeric measurement is unique too)."""
    unique = len(values) >= 20 and len(set(values)) >= _ID_SHARE * len(values)
    named = re.search(r"(?:^|[_\s-])(?:id|uuid|key)$|id$", column, re.IGNORECASE) is not None
    return unique and (named or any(_number(v) is None for v in values))


def _leaks(train: list[dict[str, str]], test: list[dict[str, str]], features: list[str], target: str) -> list[dict]:
    rows = []
    ids = [c for c in features if _identifier(c, [r[c] for r in train])]
    for c in ids:
        shared = {r[c] for r in train} & {r[c] for r in test}
        if shared:
            rows.append(("identifier shared", f"{len(shared)} value(s) of identifier column {c!r} appear in both "
                         "train and test", "Split by this identifier so no entity is in both."))
    plain = [c for c in features if c not in ids]
    seen = {tuple(r[c] for c in plain) for r in train}
    duplicated = sum(tuple(r[c] for c in plain) in seen for r in test) if plain else 0
    if duplicated:
        rows.append(("duplicate rows", f"{duplicated} of {len(test)} test row(s) also appear in the training data",
                     "Remove them from the test set (or de-duplicate before splitting) and score again."))
    labels = [r[target] for r in train]
    for c in plain:
        values = [r[c] for r in train]
        if sum(v == y for v, y in zip(values, labels)) >= 0.99 * len(train):
            rows.append(("target copy", f"column {c!r} equals the target in the training data",
                         "Drop it: it is the answer under another name."))
            continue
        groups: dict[str, set[str]] = defaultdict(set)
        for v, y in zip(values, labels):
            groups[v].add(y)
        if (len(set(labels)) > 1 and 2 <= len(groups) <= len(train) / 2
                and all(len(ys) == 1 for ys in groups.values())):
            rows.append(("perfect predictor", f"column {c!r} alone determines the target in the training data",
                         "Check it is known before the outcome; a field recorded after the outcome is a leak."))
    return [{"check": name, "result": "problem", "detail": detail, "what to do": advice, "label": "blocked-escalated"}
            for name, detail, advice in rows]


def _classification(y: list[str], p: list[str], idx: list[int]) -> dict[str, float]:
    from g6.reasoning.metrics import score
    ys, ps = [y[i] for i in idx], [p[i] for i in idx]
    return {"accuracy": score("accuracy", ps, ys), "f1": score("f1_macro", ps, ys)}


def _regression(y: list[float], p: list[float], idx: list[int]) -> dict[str, float]:
    from g6.reasoning.metrics import score
    ys, ps = [y[i] for i in idx], [p[i] for i in idx]
    return {"mae": score("mae", ps, ys), "rmse": score("rmse", ps, ys)}

def _intervals(metric, y, p) -> dict[str, tuple[float, float, float]]:
    """Exact binomial 95% interval for accuracy; seeded bootstrap for other metrics.

    Large sets use fewer resamples, never under 200, to bound bootstrap work.
    """
    point = metric(y, p, list(range(len(y))))
    resamples = min(RESAMPLES, max(200, 2_000_000 // len(y)))
    rng = random.Random(0)
    draws = [metric(y, p, [rng.randrange(len(y)) for _ in y]) for _ in range(resamples)]
    out = {}
    for name, value in point.items():
        ordered = sorted(d[name] for d in draws)
        out[name] = (value, ordered[int(0.025 * resamples)], ordered[int(0.975 * resamples) - 1])
    if "accuracy" in out:
        from g6.reasoning.metrics import exact_interval
        low, high = exact_interval(sum(a == b for a, b in zip(y, p)), len(y))
        out["accuracy"] = (point["accuracy"], low, high)
    return out


def _beats_baseline(y, predictions, baseline, regression):
    """Paired held-out comparison; bounded per-case utility for numeric errors."""
    from g6.reasoning.compare import Arm, Case, compare
    def scorer(label):
        if regression:
            return lambda answer: 1 / (1 + abs(float(answer) - label))
        return lambda answer: float(answer == label)
    cases = [Case(str(i), scorer(label)) for i, label in enumerate(y)]
    result = compare(Arm("baseline", lambda i: (str(baseline[int(i)]), 0)),
                     Arm("candidate", lambda i: (str(predictions[int(i)]), 0)), cases)
    return result.promoted


def _claims(text: str) -> dict[str, tuple[float, float]]:
    """Each claimed metric with its rounding tolerance: half a unit of the claim's last written digit."""
    found = {}
    for name, value, percent in _CLAIM.findall(text or ""):
        key = {"acc": "accuracy", "macro f1": "f1", "macro-f1": "f1", "macrof1": "f1"}.get(name.lower(), name.lower())
        number, unit = float(value), 10.0 ** -len(value.partition(".")[2])
        if percent or (key in ("accuracy", "f1") and number > 1):
            number, unit = number / 100, unit / 100
        found[key] = (number, unit / 2 + 1e-9)
    return found


def _predictions(inputs: dict[str, str], train_text: str, test_header: list[str], test: list[dict[str, str]],
                 target: str) -> tuple[list[str] | None, dict | None]:
    """The predictions, from the pasted column or the sandboxed script; or a problem row."""
    names = ("prediction", "predictions", "predicted", target.lower())
    script = inputs.get("script", "").strip()
    if not script:
        pasted = inputs.get("predictions", "").strip()
        return (_column(pasted, names), None) if pasted else (None, {
            "check": "predictions", "result": "problem", "detail": "no predictions and no script were given",
            "what to do": "Paste the model's predictions, or a script that makes them. " + _SCRIPT_HELP,
            "label": "blocked-escalated"})
    from g6.core.sandbox import isolation_refused, run_script

    features = io.StringIO()
    writer = csv.DictWriter(features, [c for c in test_header if c != target], extrasaction="ignore")
    writer.writeheader()
    writer.writerows(test)
    run = run_script(script, {"train.csv": train_text, "test_features.csv": features.getvalue()},
                     timeout_s=120, mem_mb=2048)
    made = run.outputs.get("predictions.csv")
    if isolation_refused(run):   # it never ran: say why and what the person can do, never "the script failed"
        return None, {"check": "script", "result": "not run", "detail": run.stderr[:600],
                      "what to do": "Paste the predictions instead, or allow scripts to run on this computer without a "
                                    "boundary: answer the question at the top of this page (asked on first run), or "
                                    "run g6 setup.", "label": "blocked-escalated"}
    if run.status != "ok" or made is None:
        why = run.stderr.strip().splitlines()[-1:] or [run.status]
        return None, {"check": "script", "result": "problem",
                      "detail": f"the script ended {run.status} without predictions.csv: {why[0][:300]}",
                      "what to do": _SCRIPT_HELP, "label": "blocked-escalated"}
    return _column(made.decode("utf-8", "replace"), names), None


def run(inputs: dict[str, str], llm: Any = None) -> BundleResult:
    target = inputs.get("target", "").strip()
    train_text, test_text = inputs.get("train", ""), inputs.get("test", "")
    train_header, train = _table(train_text)
    test_header, test = _table(test_text)
    if not (target and train and test):
        return BundleResult("blocked-escalated", "Paste the training data and the test data (CSV with a header row) "
                                                 "and name the target column.", code="G6_E_INPUT_VALIDATION")
    if target not in train_header or target not in test_header:
        return BundleResult("blocked-escalated", f"The target column {target!r} must be in both headers.",
                            code="G6_E_INPUT_VALIDATION")
    features = [c for c in train_header if c != target and c in test_header]
    rows = _leaks(train, test, features, target)
    predictions, problem = _predictions(inputs, train_text, test_header, test, target)
    if problem:
        rows.append(problem)
    elif len(predictions) != len(test):
        rows.append({"check": "predictions", "result": "problem",
                     "detail": f"{len(predictions)} prediction(s) for {len(test)} test row(s)",
                     "what to do": "Give exactly one prediction per test row, in the test file's order.",
                     "label": "blocked-escalated"})
        predictions = None
    labels = [r[target] for r in test]
    from g6.reasoning.metrics import MAX_VALUES
    if predictions is not None and len(labels) > MAX_VALUES:
        rows.append({"check": "scoring", "result": "problem",
                     "detail": f"the test set has {len(labels):,} test rows; G6 scores at most {MAX_VALUES:,}",
                     "what to do": f"Score a random sample of at most {MAX_VALUES:,} test rows, or split the test "
                                   "set and check each part.", "label": "blocked-escalated"})
        predictions = None
    numeric = [_number(v) for v in labels + [r[target] for r in train]]
    regression = all(v is not None for v in numeric) and len(set(numeric)) > 20
    scores: dict[str, tuple[float, float, float]] = {}
    try:
        if predictions is not None:
            if regression:
                p = [_number(v) for v in predictions]
                if any(v is None for v in p):
                    rows.append({"check": "predictions", "result": "problem", "detail": "a prediction is not a number",
                                 "what to do": "A numeric target needs numeric predictions.", "label": "blocked-escalated"})
                else:
                    y = [float(v) for v in labels]
                    scores = _intervals(_regression, y, p)
                    mean = sum(float(r[target]) for r in train) / len(train)
                    base = _regression(y, [mean] * len(y), list(range(len(y))))["mae"]
                    # the paired gate counts rows won; a few huge misses can still lose on error, so MAE must win too
                    beats = _beats_baseline(y, p, [mean] * len(y), True) and scores["mae"][2] < base
                    rows.append({"check": "baseline", "result": "ok" if beats else "problem",
                                 "detail": f"predicting the training mean gives MAE {base:.4g}",
                                 "what to do": "—" if beats else "The model is not shown to beat predicting the mean.",
                                 "label": "qualified-draft"})
            else:
                scores = _intervals(_classification, labels, predictions)
                majority = Counter(r[target] for r in train).most_common(1)[0][0]
                base = sum(v == majority for v in labels) / len(labels)
                beats = _beats_baseline(labels, predictions, [majority] * len(labels), False)
                rows.append({"check": "baseline", "result": "ok" if beats else "problem",
                             "detail": f"always predicting {majority!r} (the training majority) scores accuracy {base:.3f}",
                             "what to do": "—" if beats else "The model is not shown to beat the majority-class baseline.",
                             "label": "qualified-draft"})
    except Exception:
        scores = {}
        rows.append({"check": "scoring", "result": "problem", "detail": "metric or paired comparison failed",
                     "what to do": "Check the inputs and scoring dependency, then run again.",
                     "label": "blocked-escalated"})
    for name, (value, low, high) in scores.items():
        rows.append({"check": f"{name} (recomputed)", "result": "ok", "detail": f"{value:.4g} (95% interval "
                     f"{low:.4g} to {high:.4g}, {len(labels)} test rows)", "what to do": "—", "label": "qualified-draft"})
    for name, (claimed, tolerance) in _claims(inputs.get("claims", "")).items():
        if name not in scores:
            rows.append({"check": f"claimed {name}", "result": "not checked", "detail": f"claimed {claimed:.4g}",
                         "what to do": "G6 could not compute this metric for this target.", "label": "blocked-escalated"})
            continue
        # a claim about this test set is a fact about these predictions, so only rounding separates it from the value
        value = scores[name][0]
        close = abs(claimed - value) <= tolerance
        rows.append({"check": f"claimed {name}", "result": "ok" if close else "conflict",
                     "detail": f"claimed {claimed:.4g}; G6 recomputed {value:.4g}",
                     "what to do": "—" if close else "The claimed score does not match the predictions and labels.",
                     "label": "qualified-draft" if close else "blocked-escalated"})
    problems = sum(r["label"] == "blocked-escalated" for r in rows)
    state = weakest([r["label"] for r in rows]) if rows else "qualified-draft"
    summary = f"{problems} problem(s) found." if problems else "No leakage found and the scores were recomputed."
    if any(r["check"] == "baseline" and r["result"] == "problem" for r in rows):
        summary += " The model is not shown to beat a simple baseline."
    return BundleResult(
        state, summary, rows,
        ["Scores are recomputed from the predictions and the test labels you pasted; nothing the model or its author "
         "reported is trusted.",
         "No leakage found means none of these checks fired, not that the data is clean. A clean test set still may "
         "not represent the data the model will meet."],
        code="G6_E_ML_CHECK_PROBLEM" if problems else "")


BUNDLE = Bundle(
    id="ml-check",
    title="ML result check",
    summary="Check a model's reported score: leakage between train and test, and the score recomputed from the labels.",
    fields=(
        Field("train", "Training data (CSV with a header row)", placeholder="age,plan,churned\n34,basic,no\n51,pro,yes"),
        Field("test", "Test data with its labels (CSV, same columns)", placeholder="age,plan,churned\n29,basic,no"),
        Field("target", "Target column", kind="text", placeholder="churned"),
        Field("predictions", "Predictions for the test rows (one per line, in order)",
              help="Leave empty if you paste a script below."),
        Field("script", "Or: a Python script that makes the predictions", help=_SCRIPT_HELP),
        Field("claims", "Scores you were told (optional)", kind="text", placeholder="accuracy 0.93, F1 0.90"),
    ),
    run=run,
    runs_untrusted_code=True,
)
