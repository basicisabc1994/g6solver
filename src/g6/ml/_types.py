# SPDX-License-Identifier: Apache-2.0
"""What every ml extension shares: the table a caller gives, the held-out split the extension makes itself, the checks
run before any score is reported, and the labelled result every op returns.

``_``-prefixed, so it never registers as a component (``g6.core.registry``). Pure Python: nothing here needs an extra.

* :class:`DatasetSpec` is a dataset as the caller gives it: the rows (each a mapping of column to a number, text, a
  boolean or nothing), the target column, the feature columns, the task (classification or regression), the seed and
  the share of rows to hold out. Nothing in it says how to split: the split is the extension's, and the seed drives
  the fits, the folds and the resamples, never which rows are held out.
* :func:`make_split` makes the held-out split, drawn from the rows' own hash (``rows_sha256``), so the same rows always
  give the same held-out rows whatever seed is asked: stratified by class for classification, with rows whose
  features are identical kept on one side, so no held-out row is a copy of a training row. :class:`Split` records the
  strategy, the sizes, the class counts on each side, the hash of the rows it was made from and the hash of the
  held-out rows (by which each scoring of them is counted, ``g6.ml._report.held_out_use``).
* :func:`prepare` runs the shared checks in order and returns the first refusal, or what lets a score be reported:
  the seed (missing, or a ``random_state`` among an estimator's parameters), the columns (missing, of mixed kinds, a
  target with no value), the split's class counts and held-out size, identical rows across the split, target leakage
  (a feature that is the target, a linear or rank copy of it, or one that alone predicts it, leave-one-out) and
  class imbalance, reported with its threshold; under it the headline metric is balanced accuracy.
* :class:`MLResult` is the envelope (``completion_state``, ``warning_card``, ``evidence``, ``request_id``, ``run_id``):
  :func:`answer` gives a ``qualified-draft``, :func:`refuse` a ``blocked-escalated`` refusal with a stable
  ``G6_E_ML_*`` code. Nothing in the family marks its own output ``verified``: a score is a measurement on the
  extension's own held-out rows.
"""
from __future__ import annotations

import bisect
import hashlib
import importlib.util
import json
import math
import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from g6.core.reliability_envelope import WarningCard

#: Stable codes. A refusal carries one of these; a qualified draft's warning card carries one of the card codes.
INPUT_INVALID = "G6_E_ML_INPUT_INVALID"
SEED_MISSING = "G6_E_ML_SEED_MISSING"
LEAKAGE = "G6_E_ML_LEAKAGE"
DUPLICATES_ACROSS_SPLIT = "G6_E_ML_DUPLICATES_ACROSS_SPLIT"
TOO_FEW_HELD_OUT = "G6_E_ML_TOO_FEW_HELD_OUT"
BACKEND_NOT_INSTALLED = "G6_E_ML_BACKEND_NOT_INSTALLED"
UNTRUSTED_CODE = "G6_E_ML_UNTRUSTED_CODE"
UNKNOWN_ESTIMATOR = "G6_E_ML_UNKNOWN_ESTIMATOR"
DEADLINE = "G6_E_ML_DEADLINE"
FIT_FAILED = "G6_E_ML_FIT_FAILED"
TOO_LARGE = "G6_E_ML_TOO_LARGE"
OP_FAILED = "G6_E_ML_OP_FAILED"
NON_FINITE_LOSS = "G6_E_ML_NON_FINITE_LOSS"
NOT_CONVERGED = "G6_E_ML_NOT_CONVERGED"
INPUT_OVERFLOW = "G6_E_ML_INPUT_OVERFLOW"
NON_FINITE_OUTPUT = "G6_E_ML_NON_FINITE_OUTPUT"
NON_FINITE_WEIGHTS = "G6_E_ML_NON_FINITE_WEIGHTS"
#: Card codes of a qualified draft.
MEASURED = "G6_E_ML_MEASURED"
BELOW_BASELINE = "G6_E_ML_BELOW_BASELINE"
PROFILED = "G6_E_ML_PROFILED"
DESCRIBED = "G6_E_ML_DESCRIBED"
PREDICTED = "G6_E_ML_PREDICTED"
POSTERIOR = "G6_E_ML_POSTERIOR"
MISFIT = "G6_E_ML_MODEL_MISFIT"
PRIOR_SENSITIVE = "G6_E_ML_PRIOR_SENSITIVE"
#: The cards whose warning is red: a result not to be used as if it said more.
_RED = (BELOW_BASELINE, MISFIT, PRIOR_SENSITIVE)

#: Bounds of one request, checked before anything is hashed, copied or probed: rows and features, the characters of one
#: text cell and of all text cells (in the rows, and again in the rows to predict), and a classification target's
#: classes. A row holds only the target and the features.
MAX_ROWS = 20_000
MAX_FEATURES = 200
MAX_CELL_CHARS = 10_000
MAX_TEXT_CHARS = 5_000_000
MAX_CLASSES = 100
#: The fewest independent held-out rows a score is reported on: rows with identical features count once, so repeating
#: observations never meets it (G6's own bound: it can only refuse).
MIN_HELD_OUT = 10
#: Under this share of rows in its smallest class a classification target is imbalanced (G6's own threshold: it
#: changes the headline metric to balanced accuracy and adds a warning; it raises no label).
IMBALANCE_SHARE = 0.10
#: A feature whose Pearson or Spearman correlation with a numeric target reaches this, or that alone predicts the
#: target for every row from the others at least this well (accuracy, or R squared), is a suspected leak (G6's own thresholds:
#: they can only refuse).
LEAK_CORRELATION = 0.99
LEAK_SINGLE_FEATURE = 0.99
#: The fewest rows a leakage probe needs before it says anything.
MIN_PROBE_ROWS = 5

Cell = bool | int | float | str | None
Task = Literal["classification", "regression"]
State = Literal["qualified-draft", "blocked-escalated"]

_ACTIONS = {
    INPUT_INVALID: "Correct the input as the reason says and ask again.",
    SEED_MISSING: "Pass seed (an integer) and leave it out of the parameters: every fit, fold and resample is then "
                  "repeatable, and the seed is in the evidence.",
    LEAKAGE: "Remove the feature the reason names, or find out why it carries the answer; a feature known only after "
             "the outcome, or one copied from the target, makes any score meaningless.",
    DUPLICATES_ACROSS_SPLIT: "Identical rows sit on both sides of the split, so the held-out score would be inflated; "
                             "nothing was scored. Report this: the split is meant to keep them together.",
    TOO_FEW_HELD_OUT: "Give more rows, or a larger held_out_fraction: a score on so few held-out rows says too little.",
    BACKEND_NOT_INSTALLED: "Install the extra the reason names (pip install 'g6[...]') and ask again.",
    UNTRUSTED_CODE: "Choose an estimator from the extension's own list (op estimators): G6 never runs a class, a "
                    "pickle or a model file of yours.",
    UNKNOWN_ESTIMATOR: "Choose an estimator and parameters from the extension's own list (op estimators).",
    DEADLINE: "The fit ran past its deadline and was stopped; give fewer rows or a simpler estimator, or a longer "
              "timeout_s.",
    FIT_FAILED: "The fit failed; read the reason, correct the request and ask again.",
    TOO_LARGE: "Send less: the reason names the limit.",
    OP_FAILED: "The op failed in a way it does not expect; read the reason and report it.",
    NON_FINITE_LOSS: "The training loss became infinite or not a number, so training was stopped and nothing was "
                     "scored; lower learning_rate, or use fewer or smaller layers, and ask again.",
    NOT_CONVERGED: "The chains did not agree or carried too few independent draws for the summaries to be trusted; "
                   "nothing is reported as a posterior. Ask for more draws or warm-up, or a simpler model.",
    INPUT_OVERFLOW: "A value is beyond the range of the 32-bit floats the network computes in, so nothing was trained "
                    "or predicted with it: check the row and feature the reason names (a slip of units or typing?), "
                    "or rescale or leave out that feature.",
    NON_FINITE_OUTPUT: "The network's outputs were infinite or not a number for the rows the reason names, though "
                       "every input was in range; nothing was scored or predicted. In training the weights grew too "
                       "large: lower learning_rate, add weight_decay, or use fewer or smaller layers. For a kept "
                       "model, a row to predict lies far outside the rows it was trained on.",
    NON_FINITE_WEIGHTS: "Training left weights that are infinite or not a number, though every loss it measured was "
                        "finite, so nothing was scored or kept; lower learning_rate, add weight_decay, or use fewer or "
                        "smaller layers, and ask again.",
}
_SAID = {
    MEASURED: "This is a measurement on rows the extension held out itself, with a baseline on the same rows. It is "
              "not a guarantee for new data: check that the rows represent the data the model will meet.",
    BELOW_BASELINE: "The model does not clearly beat a baseline that ignores the features, on the same held-out rows. "
                    "Do not use it as if it had learned something.",
    PROFILED: "This describes the table as given, by one stated rule; nothing judged whether the values are right.",
    DESCRIBED: "This is what the extension says about itself; nothing was fitted or measured.",
    PREDICTED: "These are a kept model's predictions for rows it has no answers for; nothing here measured them. Its "
               "held-out score was measured once, when it was trained, on those rows only.",
    POSTERIOR: "This is a posterior under the stated model and prior, on the data given. It is only as good as the "
               "model: read the posterior predictive checks and the prior sensitivity beside it.",
    MISFIT: "The model does not reproduce a feature of the data (a posterior predictive check failed), so its "
            "intervals can be too narrow. Do not rely on them; choose a model that fits the data.",
    PRIOR_SENSITIVE: "The conclusion moves with the prior: a reasonable other prior changes it by more than the "
                     "uncertainty shown. Report it with its prior, or gather more data.",
}


class MLError(ValueError):
    """A refusal raised inside an ml op, carrying its stable code."""

    def __init__(self, code: str, reason: str, evidence: list[dict[str, Any]] | None = None) -> None:
        super().__init__(f"{code}: {reason}")
        self.code, self.reason, self.evidence = code, reason, list(evidence or [])


class MLResult(BaseModel):
    """One op's labelled outcome. Op-specific fields sit beside the envelope's."""

    model_config = ConfigDict(extra="allow", frozen=True)

    operation: str
    code: str = ""
    reason: str = ""
    completion_state: State
    warning_card: dict[str, Any]
    evidence: list[dict[str, Any]]
    request_id: str = ""
    run_id: str = ""

    @model_validator(mode="after")
    def _a_refusal_has_a_code(self) -> MLResult:
        if self.completion_state == "blocked-escalated" and not self.code.startswith("G6_E_"):
            raise ValueError("a refusal needs its stable G6_E_ code")
        return self


def answer(operation: str, message: str, *, card: str = MEASURED, evidence: list[dict[str, Any]] | None = None,
           request_id: str = "", run_id: str = "", **fields: Any) -> MLResult:
    """A ``qualified-draft`` result whose card says what it rests on (:data:`MEASURED`, :data:`BELOW_BASELINE`,
    :data:`PROFILED`, :data:`DESCRIBED`, :data:`PREDICTED`, :data:`POSTERIOR`, :data:`MISFIT` or
    :data:`PRIOR_SENSITIVE`)."""
    if card not in _SAID:
        raise ValueError(f"{card!r} is not a card a qualified draft carries")
    severity = "red" if card in _RED else "amber"
    found = WarningCard(code=card, message=message, severity=severity, suggested_action=_SAID[card]).model_dump()
    return MLResult.model_validate({**fields, "operation": operation, "completion_state": "qualified-draft",
                                    "warning_card": found, "evidence": list(evidence or []),
                                    "request_id": request_id, "run_id": run_id})


def refuse(operation: str, code: str, reason: str, *, evidence: list[dict[str, Any]] | None = None,
           request_id: str = "", run_id: str = "", **fields: Any) -> MLResult:
    """A ``blocked-escalated`` refusal with its stable code and the reason in words."""
    card = WarningCard(code=code, message=reason, severity="blocked",
                       suggested_action=_ACTIONS.get(code, "Read the reason and correct the request.")).model_dump()
    found = [*(evidence or []), {"kind": "refusal", "code": code, "reason": reason}]
    return MLResult.model_validate({**fields, "operation": operation, "code": code, "reason": reason,
                                    "completion_state": "blocked-escalated", "warning_card": card,
                                    "evidence": found, "request_id": request_id, "run_id": run_id})


def missing_extra(extra: str, modules: tuple[str, ...], what: str) -> str:
    """``""`` when every module in ``modules`` can be imported, otherwise what is not installed and how to install it."""
    absent = []
    for name in modules:
        try:
            found = importlib.util.find_spec(name)
        except (ImportError, ValueError):
            found = None
        if found is None:
            absent.append(name)
    if not absent:
        return ""
    return f"{what} is not installed ({', '.join(absent)} not found): pip install 'g6[{extra}]'"


# --- the dataset as given ------------------------------------------------------------------------------------------


def cell_kind(value: Cell) -> str:
    """``missing`` (nothing, or a float NaN), ``boolean``, ``number`` (a finite int or float), ``infinite`` or
    ``text``."""
    if value is None:
        return "missing"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return "missing"
        return "number" if math.isfinite(value) else "infinite"
    return "text"


def column_kind(values: list[Cell]) -> str:
    """One column's kind by one rule: ``empty`` (no value), ``number`` (every value a finite number), ``boolean``,
    ``text``, ``mixed`` (values of more than one kind) or ``infinite`` (a non-finite number among them)."""
    kinds = {cell_kind(v) for v in values} - {"missing"}
    if not kinds:
        return "empty"
    if "infinite" in kinds:
        return "infinite"
    return kinds.pop() if len(kinds) == 1 else "mixed"


class DatasetSpec(BaseModel):
    """A dataset as the caller gives it. The rows are used as given; the split is the extension's own."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rows: list[dict[str, Cell]] = Field(min_length=1, max_length=MAX_ROWS)
    target: str = Field(min_length=1, max_length=100)
    features: list[str] = Field(min_length=1, max_length=MAX_FEATURES)
    task: Task
    seed: int | None = Field(None, ge=0, le=2**32 - 1)
    held_out_fraction: float = Field(0.25, ge=0.1, le=0.5)

    @field_validator("features")
    @classmethod
    def _distinct(cls, features: list[str]) -> list[str]:
        if len(set(features)) != len(features):
            raise ValueError("a feature is named twice")
        if any(not f or len(f) > 100 for f in features):
            raise ValueError("a feature name is empty or longer than 100 characters")
        return features

    def key(self, row: dict[str, Cell]) -> str:
        """The row's features as one canonical string: rows with the same key are identical to a model."""
        return json.dumps([_canonical(row.get(f)) for f in self.features], separators=(",", ":"))

    def rows_sha256(self) -> str:
        return self.digest(self.rows)

    def digest(self, rows: list[dict[str, Cell]]) -> str:
        """The SHA-256 of ``rows`` with the target, the features and the task."""
        body = json.dumps({"rows": rows, "target": self.target, "features": self.features, "task": self.task},
                          sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _canonical(value: Cell) -> Any:
    kind = cell_kind(value)
    if kind == "missing":
        return None
    if kind == "number":
        return ["n", float(value)]  # 1 and 1.0 are the same value to a model
    if kind == "boolean":
        return ["b", bool(value)]
    return ["t", str(value)]


def _label(value: Cell) -> str:
    """A class label as a string key, so 1 and "1" are kept apart only when the caller mixed them, 1 and 1.0 are one
    class, and a whole number keeps every digit (as floats, 2**53 and 2**53 + 1 would be one class)."""
    if cell_kind(value) == "number" and (isinstance(value, int) or value.is_integer()):
        return json.dumps(["n", int(value)])
    return json.dumps(_canonical(value))


# --- the split ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Split:
    """The held-out split the extension made: row indices on each side, and how they were chosen."""

    train: tuple[int, ...]
    held_out: tuple[int, ...]
    fraction: float
    strategy: str
    duplicate_rows: int
    rows_sha256: str
    held_out_sha256: str
    held_out_groups: int = 0
    class_counts: dict[str, dict[str, int]] = field(default_factory=dict)

    def evidence(self) -> dict[str, Any]:
        return {"kind": "ml_split", "made_by": "the extension, from the rows' own hash, never the caller: the same "
                                                "rows always give the same held-out rows, whatever the seed",
                "drawn_from": "rows_sha256", "strategy": self.strategy, "held_out_fraction": self.fraction,
                "train_rows": len(self.train), "held_out_rows": len(self.held_out),
                "held_out_groups": self.held_out_groups,
                "held_out_groups_means": "independent held-out rows: rows with identical features count once",
                "duplicate_rows": self.duplicate_rows, "class_counts": self.class_counts,
                "rows_sha256": self.rows_sha256, "held_out_sha256": self.held_out_sha256}


def make_split(spec: DatasetSpec) -> Split:
    """Split ``spec``'s rows by their own hash: rows whose features are identical form one group, groups are shuffled
    (by a generator seeded with ``rows_sha256``, never the caller's seed) within each class (classification: the class
    of the group's first row) or all together (regression), and whole groups are held out until each class (or the
    whole) reaches its share. The same rows always give the same split."""
    rows_sha = spec.rows_sha256()
    rng = random.Random(int(rows_sha, 16))
    groups: dict[str, list[int]] = {}
    for i, row in enumerate(spec.rows):
        groups.setdefault(spec.key(row), []).append(i)
    duplicates = len(spec.rows) - len(groups)
    strata: dict[str, list[list[int]]] = {}
    for members in groups.values():
        stratum = _label(spec.rows[members[0]].get(spec.target)) if spec.task == "classification" else "all"
        strata.setdefault(stratum, []).append(members)
    held: list[int] = []
    held_groups = 0
    for stratum in sorted(strata):
        chosen = strata[stratum]
        rng.shuffle(chosen)
        size = sum(len(g) for g in chosen)
        want = round(size * spec.held_out_fraction)
        if spec.task == "classification":
            want = max(1, want)
        taken = 0
        for members in chosen[:-1]:  # the last group always stays in training
            if taken >= want:
                break
            held.extend(members)
            taken += len(members)
            held_groups += 1
    held_set = set(held)
    train = tuple(i for i in range(len(spec.rows)) if i not in held_set)
    counts: dict[str, dict[str, int]] = {}
    if spec.task == "classification":
        counts = {"train": _class_counts(spec, train), "held_out": _class_counts(spec, tuple(sorted(held)))}
    strategy = ("stratified by class" if spec.task == "classification" else "random") + \
        ", rows with identical features kept on one side"
    held_out = tuple(sorted(held))
    return Split(train=train, held_out=held_out, fraction=spec.held_out_fraction, strategy=strategy,
                 duplicate_rows=duplicates, rows_sha256=rows_sha,
                 held_out_sha256=spec.digest([spec.rows[i] for i in held_out]), held_out_groups=held_groups,
                 class_counts=counts)


def _class_counts(spec: DatasetSpec, indices: tuple[int, ...]) -> dict[str, int]:
    counter = Counter(_display(spec.rows[i].get(spec.target)) for i in indices)
    return dict(sorted(counter.items()))


def _display(value: Cell) -> str:
    return str(value) if not isinstance(value, float) or not value.is_integer() else str(int(value))


def duplicates_across(spec: DatasetSpec, split: Split) -> int:
    """How many held-out rows have exactly the features of a training row."""
    seen = {spec.key(spec.rows[i]) for i in split.train}
    return sum(1 for i in split.held_out if spec.key(spec.rows[i]) in seen)


# --- leakage probes -------------------------------------------------------------------------------------------------


def _numbers(values: list[Cell]) -> list[float] | None:
    out = []
    for v in values:
        if cell_kind(v) not in ("number", "boolean"):
            return None
        out.append(float(v))
    return out


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy)


def _ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks



def _without(counts: Counter, label: str | None) -> str:
    """The majority class of ``counts`` with one row of ``label`` taken out (ties: the first in sorted order)."""
    best, top = None, -1
    for c in sorted(counts):
        n = counts[c] - (c == label)
        if n > top:
            best, top = c, n
    return best


def _single_feature(spec: DatasetSpec, feature: str) -> dict[str, Any] | None:
    """How well ``feature`` alone predicts the target for every row from the other rows (leave-one-out), by the
    simplest rule (a number: the other rows at the nearest value; text: the other rows with the same value; the
    majority class, or the mean, of those rows), beside a baseline that ignores it. Every row counts, so a small
    held-out part cannot make an honest feature look perfect by chance. ``None`` when too few rows have a value."""
    pairs = [(spec.rows[i].get(feature), spec.rows[i].get(spec.target)) for i in range(len(spec.rows))
             if cell_kind(spec.rows[i].get(feature)) != "missing"]
    if len(pairs) < 2 * MIN_PROBE_ROWS:
        return None
    numeric = all(cell_kind(x) in ("number", "boolean") for x, _ in pairs)
    classify = spec.task == "classification"
    keys = [float(x) if numeric else json.dumps(_canonical(x)) for x, _ in pairs]
    n = len(pairs)
    ordered: list[Any] = []
    if classify:
        labels = [_label(y) for _, y in pairs]
        stats: dict[Any, Counter] = {}
        for k, y in zip(keys, labels):
            stats.setdefault(k, Counter())[y] += 1
        everyone = Counter(labels)
        loo = {c: _without(everyone, c) for c in everyone}   # the majority baseline without each label, once
        ordered = sorted(stats) if numeric else []
        memo: dict[tuple[Any, str], str] = {}

        def guess(k: Any, y: str) -> str:
            if (k, y) not in memo:
                if sum(stats[k].values()) > 1:
                    memo[(k, y)] = _without(stats[k], y)
                elif numeric and len(ordered) > 1:
                    at = bisect.bisect_left(ordered, k)
                    near = [o for o in (ordered[at - 1] if at > 0 else None,
                                        ordered[at + 1] if at + 1 < len(ordered) else None) if o is not None]
                    memo[(k, y)] = _without(stats[min(near, key=lambda o: (abs(o - k), o))], None)
                else:
                    memo[(k, y)] = loo[y]
            return memo[(k, y)]

        score = sum(guess(k, y) == y for k, y in zip(keys, labels)) / n
        baseline = sum(everyone[c] * (loo[c] == c) for c in everyone) / n
        return {"metric": "accuracy", "score": score, "baseline": baseline, "rows": n}
    ys = [float(y) for _, y in pairs]
    sums: dict[Any, list[float]] = {}
    for k, y in zip(keys, ys):
        entry = sums.setdefault(k, [0.0, 0])
        entry[0] += y
        entry[1] += 1
    ordered = sorted(sums) if numeric else []
    total = sum(ys)

    def estimate(k: Any, y: float) -> float:
        s, c = sums[k]
        if c > 1:
            return (s - y) / (c - 1)
        if numeric and len(ordered) > 1:
            at = bisect.bisect_left(ordered, k)
            near = [o for o in (ordered[at - 1] if at > 0 else None,
                                ordered[at + 1] if at + 1 < len(ordered) else None) if o is not None]
            s, c = sums[min(near, key=lambda o: (abs(o - k), o))]
            return s / c
        return (total - y) / (n - 1)

    mean = total / n
    sst = sum((y - mean) ** 2 for y in ys)
    if sst <= 0:
        return None
    sse = sum((estimate(k, y) - y) ** 2 for k, y in zip(keys, ys))
    sbase = sum(((total - y) / (n - 1) - y) ** 2 for y in ys)
    return {"metric": "R squared", "score": 1 - sse / sst, "baseline": 1 - sbase / sst, "rows": n}

def _leakage_findings(spec: DatasetSpec, split: Split) -> list[dict[str, Any]]:
    """Each feature suspected of carrying the target, by three probes in turn: it equals the target on every row
    where it has a value; it moves with a numeric (or two-class numeric) target almost exactly on the training rows
    (Pearson's or Spearman's correlation of :data:`LEAK_CORRELATION` or more); or, alone, it predicts the target for
    every row from the others (leave-one-out) at :data:`LEAK_SINGLE_FEATURE` or better, and better than a baseline
    that ignores it."""
    found: list[dict[str, Any]] = []
    target = [r.get(spec.target) for r in spec.rows]
    classes = {_label(y) for y in target}
    numeric_target = _numbers(target) if spec.task == "regression" or len(classes) == 2 else None
    for feature in spec.features:
        values = [r.get(feature) for r in spec.rows]
        present = [i for i, v in enumerate(values) if cell_kind(v) != "missing"]
        if len(present) < MIN_PROBE_ROWS:
            continue
        if all(_canonical(values[i]) == _canonical(target[i]) for i in present):
            found.append({"feature": feature, "probe": "copy", "measure": 1.0,
                          "said": "is the target: it equals it on every row where it has a value"})
            continue
        train = [i for i in split.train if cell_kind(values[i]) != "missing"]
        if numeric_target is not None and _numbers([values[i] for i in present]) is not None \
                and len(train) >= MIN_PROBE_ROWS:
            xs, ys = [float(values[i]) for i in train], [numeric_target[i] for i in train]
            r, rho = _pearson(xs, ys), _pearson(_ranks(xs), _ranks(ys))
            strongest = max((abs(c) for c in (r, rho) if c is not None), default=0.0)
            if strongest >= LEAK_CORRELATION:
                found.append({"feature": feature, "probe": "correlation", "measure": round(strongest, 6),
                              "pearson": None if r is None else round(r, 6),
                              "spearman": None if rho is None else round(rho, 6),
                              "said": f"moves with the target almost exactly on the training rows (Pearson r = "
                                      f"{'n/a' if r is None else f'{r:.4f}'}, Spearman rho = "
                                      f"{'n/a' if rho is None else f'{rho:.4f}'})"})
                continue
        alone = _single_feature(spec, feature)
        if alone and alone["score"] >= LEAK_SINGLE_FEATURE and alone["score"] > alone["baseline"]:
            found.append({"feature": feature, "probe": "single_feature", "measure": round(alone["score"], 6),
                          **{k: alone[k] for k in ("metric", "baseline", "rows")},
                          "said": f"alone predicts the target for {alone['rows']} rows, each from the others "
                                  f"(leave-one-out {alone['metric']} {alone['score']:.3f}, against "
                                  f"{alone['baseline']:.3f} for a baseline that ignores it)"})
    return found


# --- class balance --------------------------------------------------------------------------------------------------


def class_balance(spec: DatasetSpec, rows: tuple[int, ...] | None = None) -> dict[str, Any]:
    """The class counts of the rows at ``rows`` (every row when ``None``), the share of the smallest class, and
    whether it is under :data:`IMBALANCE_SHARE`. :func:`prepare` counts the training rows only, so the held-out
    labels choose nothing, not even the headline metric."""
    chosen = range(len(spec.rows)) if rows is None else rows
    counts = Counter(_display(spec.rows[i].get(spec.target)) for i in chosen)
    total = sum(counts.values())
    smallest = min(counts.values())
    share = smallest / total
    return {"kind": "ml_class_balance", "counted_on": "every row" if rows is None else "the training rows only",
            "classes": dict(sorted(counts.items())), "minority_share": round(share, 6),
            "threshold": IMBALANCE_SHARE, "imbalanced": share < IMBALANCE_SHARE,
            "headline_metric": "balanced_accuracy" if share < IMBALANCE_SHARE else "accuracy"}


# --- the checks, in order --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Prepared:
    """What the checks let through: the split, the evidence so far, the column kinds and, for classification, the
    class balance."""

    spec: DatasetSpec
    split: Split
    kinds: dict[str, str]
    balance: dict[str, Any] | None
    evidence: list[dict[str, Any]]


#: Parameters that would set randomness a second way: the seed is set once, by ``seed``.
_SEED_PARAMS = ("random_state", "seed", "random_seed")


def _columns(spec: DatasetSpec) -> dict[str, str]:
    names = {c for row in spec.rows for c in row}
    missing = [c for c in (spec.target, *spec.features) if c not in names]
    if missing:
        raise MLError(INPUT_INVALID, f"no row has the column{'s' if len(missing) > 1 else ''} "
                                     f"{', '.join(repr(c) for c in missing)}")
    empty_target = [i for i, row in enumerate(spec.rows) if cell_kind(row.get(spec.target)) == "missing"]
    if empty_target:
        raise MLError(INPUT_INVALID, f"{len(empty_target)} row(s) have no value for the target {spec.target!r} "
                                     f"(first: row {empty_target[0]}); a missing answer is never filled in: remove "
                                     "those rows yourself")
    target_kind = column_kind([r.get(spec.target) for r in spec.rows])
    if spec.task == "regression" and target_kind != "number":
        raise MLError(INPUT_INVALID, f"a regression target must be numbers; {spec.target!r} is {target_kind}")
    if spec.task == "classification":
        decimals = [r.get(spec.target) for r in spec.rows if isinstance(r.get(spec.target), float)
                    and not r.get(spec.target).is_integer()]
        if decimals or target_kind in ("mixed", "infinite"):
            raise MLError(INPUT_INVALID, f"the class labels in {spec.target!r} are {target_kind}"
                                         + (f", with decimal values such as {decimals[0]}" if decimals else "")
                                         + ": is this a regression?")
    kinds = {}
    for f in spec.features:
        kind = column_kind([r.get(f) for r in spec.rows])
        if kind in ("mixed", "infinite", "empty"):
            raise MLError(INPUT_INVALID, f"feature {f!r} is {kind}: "
                                         + ("it holds values of more than one kind (numbers and text); profile the "
                                            "table with adapt_pandas and convert the column" if kind == "mixed" else
                                            "it holds an infinite number" if kind == "infinite" else
                                            "it has no value in any row"))
        kinds[f] = "numeric" if kind in ("number", "boolean") else "categorical"
    return kinds


def bounded(rows: list[dict[str, Cell]], declared: set[str], what: str) -> None:
    """Refuse ``rows`` before anything is hashed, copied or probed: a column not in ``declared``
    (``G6_E_ML_INPUT_INVALID``), a text cell over :data:`MAX_CELL_CHARS` characters or text over
    :data:`MAX_TEXT_CHARS` in all (``G6_E_ML_TOO_LARGE``)."""
    text = 0
    for i, row in enumerate(rows):
        other = next((c for c in row if c not in declared), None)
        if other is not None:
            raise MLError(INPUT_INVALID, f"{what} {i} has the column {other[:60]!r}, which is neither the target nor a "
                                         "feature: send only the declared columns (anything else would be hashed and "
                                         "copied, never used)")
        for column, value in row.items():
            if isinstance(value, str):
                if len(value) > MAX_CELL_CHARS:
                    raise MLError(TOO_LARGE, f"{what} {i}, column {column!r}, holds {len(value):,} characters; a cell "
                                             f"holds at most {MAX_CELL_CHARS:,} characters")
                text += len(value)
        if text > MAX_TEXT_CHARS:
            raise MLError(TOO_LARGE, f"the text cells hold more than {MAX_TEXT_CHARS:,} characters in all (passed at "
                                     f"{what} {i})")


def _bounds(spec: DatasetSpec, predict_rows: list[dict[str, Cell]] | None = None) -> None:
    """The rows' and the rows to predict's columns and text (:func:`bounded`), and the classes' count."""
    bounded(spec.rows, {spec.target, *spec.features}, "row")
    bounded(list(predict_rows or ()), {spec.target, *spec.features}, "row to predict")
    if spec.task == "classification":
        classes = {_label(row.get(spec.target)) for row in spec.rows}
        if len(classes) > MAX_CLASSES:
            raise MLError(TOO_LARGE, f"the target {spec.target!r} has {len(classes):,} classes; at most "
                                     f"{MAX_CLASSES} are classified (is it an identifier, or a regression?)")


def prepare(spec: DatasetSpec, params: dict[str, Any] | None = None,
            predict_rows: list[dict[str, Cell]] | None = None) -> Prepared:
    """Run the family's checks in order and return what lets a score be reported, or raise :class:`MLError` with
    the first refusal: the seed, the bounds (:func:`bounded`, for the rows and the rows to predict, and at most
    :data:`MAX_CLASSES` classes), the columns, the split's classes and size, identical rows across the split, then
    target leakage. Class imbalance is reported, not refused."""
    if spec.seed is None:
        raise MLError(SEED_MISSING, "no seed was given: the fits, folds and resamples would not be repeatable")
    set_twice = [p for p in (params or {}) if p in _SEED_PARAMS]
    if set_twice:
        raise MLError(SEED_MISSING, f"{', '.join(set_twice)} is set among the parameters; randomness is set once, by "
                                    "seed, so the evidence names the one seed every step used")
    _bounds(spec, predict_rows)
    kinds = _columns(spec)
    if spec.task == "classification":
        groups: dict[str, set[str]] = {}
        for row in spec.rows:
            groups.setdefault(_display(row.get(spec.target)), set()).add(spec.key(row))
        if len(groups) < 2:
            raise MLError(INPUT_INVALID, f"the target {spec.target!r} has one class only; there is nothing to "
                                         "classify")
        thin = sorted(c for c, keys in groups.items() if len(keys) < 2)
        if thin:
            raise MLError(INPUT_INVALID, f"class {thin[0]!r} has fewer than 2 distinct rows, so it cannot be both "
                                         "learned and scored" + (f" ({len(thin)} such classes)" if len(thin) > 1
                                                                 else ""))
    split = make_split(spec)
    evidence = [split.evidence()]
    if split.held_out_groups < MIN_HELD_OUT:
        raise MLError(TOO_FEW_HELD_OUT, f"the split holds out {split.held_out_groups} independent rows (rows with "
                                        f"identical features count once; {len(split.held_out)} rows in all), fewer "
                                        f"than {MIN_HELD_OUT}: give more distinct rows (there are "
                                        f"{len(spec.rows) - split.duplicate_rows} of {len(spec.rows)}) or a larger "
                                        "held_out_fraction", evidence)
    if spec.task == "classification":
        missing = sorted(set(split.class_counts["train"]) ^ set(split.class_counts["held_out"]))
        if missing:
            raise MLError(TOO_FEW_HELD_OUT, f"class {missing[0]!r} is not on both sides of the split; give more rows "
                                            "of it", evidence)
    crossing = duplicates_across(spec, split)
    if crossing:
        raise MLError(DUPLICATES_ACROSS_SPLIT, f"{crossing} held-out row(s) have the same features as a training row",
                      evidence)
    findings = _leakage_findings(spec, split)
    leak_evidence = {"kind": "ml_leakage_probes", "features_probed": len(spec.features), "suspected": findings,
                     "thresholds": {"correlation": LEAK_CORRELATION, "single_feature": LEAK_SINGLE_FEATURE}}
    evidence.append(leak_evidence)
    if findings:
        first = findings[0]
        more = f" ({len(findings)} features in all)" if len({f['feature'] for f in findings}) > 1 else ""
        raise MLError(LEAKAGE, f"feature {first['feature']!r} {first['said']}{more}: a score with it would be "
                               "meaningless", evidence)
    balance = class_balance(spec, split.train) if spec.task == "classification" else None
    if balance:
        evidence.append(balance)
    return Prepared(spec=spec, split=split, kinds=kinds, balance=balance, evidence=evidence)


__all__ = ["BACKEND_NOT_INSTALLED", "BELOW_BASELINE", "DEADLINE", "DESCRIBED", "DUPLICATES_ACROSS_SPLIT",
           "FIT_FAILED", "IMBALANCE_SHARE", "INPUT_INVALID", "INPUT_OVERFLOW", "LEAKAGE", "LEAK_CORRELATION",
           "LEAK_SINGLE_FEATURE", "NON_FINITE_OUTPUT", "NON_FINITE_WEIGHTS",
           "MAX_CELL_CHARS", "MAX_CLASSES", "MAX_FEATURES", "MAX_ROWS", "MAX_TEXT_CHARS", "MEASURED", "MIN_HELD_OUT", "MIN_PROBE_ROWS", "MISFIT", "MLError", "MLResult",
           "NON_FINITE_LOSS", "NOT_CONVERGED", "OP_FAILED", "POSTERIOR", "PREDICTED", "PRIOR_SENSITIVE", "PROFILED",
           "Prepared", "SEED_MISSING", "Split", "TOO_FEW_HELD_OUT", "TOO_LARGE", "UNKNOWN_ESTIMATOR", "UNTRUSTED_CODE",
           "Cell", "DatasetSpec", "Task", "answer", "bounded", "cell_kind", "class_balance", "column_kind",
           "duplicates_across",
           "make_split", "missing_extra", "prepare", "refuse"]
