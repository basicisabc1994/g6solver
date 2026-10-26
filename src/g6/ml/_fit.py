# SPDX-License-Identifier: Apache-2.0
"""Fitting out of G6's process: the job a member builds from a prepared dataset, the bounded child that fits it with
scikit-learn, and what comes back.

``_``-prefixed, so it never registers. The parent side is pure Python; only :func:`child_main` and what it calls import
scikit-learn and NumPy, and they run in a child process.

* :func:`encode` turns a :class:`g6.ml._types.Prepared` into JSON-ready features (numbers as floats or nothing, text
  as text or nothing), the targets (class labels as stable keys, or floats) and the group of each row (rows with
  identical features share one), so folds keep identical rows together as the split does.
* :func:`run` writes a job and runs :data:`SCRIPT`, a fixed script of G6's own, through ``g6.core.sandbox.run_script``:
  a child process with a clean environment, a wall-clock deadline (``timeout_s``), a memory cap (:data:`MEMORY_MB`) and
  the sandbox's network guard, which is no more than :data:`NETWORK_SCOPE` says. The job holds the training rows with
  their targets and the held-out rows without theirs: the child never sees a held-out answer, and G6 scores what it
  predicts (``g6.ml._metrics``).
* In the child, every model is one scikit-learn ``Pipeline``: numbers imputed by their training median (with a
  missing-value indicator) and scaled where the estimator needs it, text imputed as its own category and one-hot
  encoded (at most :data:`MAX_CATEGORIES` per column, the rarer grouped as infrequent), then the estimator, seeded.
  ``fit`` fits it on the training rows and predicts the held-out, training and extra rows; ``rank`` cross-validates
  each candidate inside the training rows (stratified for classification, grouped by identical rows, seeded), picks
  the best mean fold score, then fits that one on all the training rows and predicts. Only modules under
  ``sklearn.`` are imported by name, and only the classes the parent named from its own list.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from g6.ml import _metrics
from g6.ml._types import DEADLINE, FIT_FAILED, INPUT_INVALID, Cell, MLError, Prepared, _label, cell_kind

SCRIPT = "from g6.ml._fit import child_main\nchild_main()\n"
MEMORY_MB = 1536
MAX_CATEGORIES = 50
MAX_TIMEOUT_S = 600.0
JOB, RESULT = "job.json", "result.json"
#: The oldest scikit-learn this code runs on: GroupKFold takes shuffle and random_state from 1.6 (:func:`folds`).
#: Both extras that install scikit-learn declare at least this (tests/ml/test_sklearn.py reads pyproject.toml).
SKLEARN_FLOOR = (1, 6)
#: What the sandbox's network guard covers, in its own terms (``g6.core.sandbox``): the run's evidence says this beside
#: the sandbox's own limits, never "network refused" outright.
NETWORK_SCOPE = ("g6.core.sandbox's guard on Python's own socket class refuses connections to anywhere but this machine "
                 "(loopback is allowed); it is not an operating-system sandbox: code that restores those methods, calls "
                 "the C base class or starts another program is outside it, and the child runs as the same user")


@dataclass(frozen=True)
class Encoded:
    """A prepared dataset as the child takes it."""

    features: list[str]
    numeric: list[int]
    categorical: list[int]
    X: list[list[Any]]
    y: list[Any]
    labels: dict[str, Cell]
    groups: list[int]


def _encode_row(features: list[str], kinds: dict[str, str], row: dict[str, Cell], where: str) -> list[Any]:
    out: list[Any] = []
    for f in features:
        value = row.get(f)
        kind = cell_kind(value)
        if kind == "missing":
            out.append(None)
        elif kinds[f] == "numeric":
            if kind not in ("number", "boolean"):
                raise MLError(INPUT_INVALID, f"{where}: feature {f!r} is numbers in the training rows but holds "
                                             f"{kind} here")
            out.append(float(value))
        else:
            if kind != "text":
                raise MLError(INPUT_INVALID, f"{where}: feature {f!r} is text in the training rows but holds {kind} "
                                             "here")
            out.append(str(value))
    return out


def encode(prepared: Prepared) -> Encoded:
    """Every row's features, target and group, in the spec's row order."""
    spec = prepared.spec
    features = list(spec.features)
    X = [_encode_row(features, prepared.kinds, row, f"row {i}") for i, row in enumerate(spec.rows)]
    labels: dict[str, Cell] = {}
    if spec.task == "classification":
        y: list[Any] = []
        for row in spec.rows:
            key = _label(row.get(spec.target))
            labels.setdefault(key, row.get(spec.target))
            y.append(key)
    else:
        y = [float(row.get(spec.target)) for row in spec.rows]
    ids: dict[str, int] = {}
    groups = [ids.setdefault(spec.key(row), len(ids)) for row in spec.rows]
    return Encoded(features=features, numeric=[i for i, f in enumerate(features) if prepared.kinds[f] == "numeric"],
                   categorical=[i for i, f in enumerate(features) if prepared.kinds[f] == "categorical"],
                   X=X, y=y, labels=labels, groups=groups)


def encode_rows(prepared: Prepared, rows: list[dict[str, Cell]]) -> list[list[Any]]:
    """Rows to predict (bounded by :func:`g6.ml._types.prepare`), encoded as the training rows were; a feature of the
    wrong kind is refused."""
    return encode_with(list(prepared.spec.features), prepared.kinds, rows)


def encode_with(features: list[str], kinds: dict[str, str], rows: list[dict[str, Cell]]) -> list[list[Any]]:
    """Rows to predict, encoded by the family's kind rules for features of ``kinds`` (``numeric`` or ``categorical``,
    as the training rows set them; a kept model's own record, say): a numeric feature takes a number or a boolean, a
    text feature text; anything else, an infinite number among them, is refused ``G6_E_ML_INPUT_INVALID``."""
    return [_encode_row(features, kinds, row, f"row {i} to predict") for i, row in enumerate(rows)]


def job(kind: str, prepared: Prepared, encoded: Encoded, **fields: Any) -> dict[str, Any]:
    """A job for the child: the training rows with their targets, the held-out rows without theirs."""
    train, held = prepared.split.train, prepared.split.held_out
    return {"kind": kind, "task": prepared.spec.task, "seed": prepared.spec.seed, "numeric": encoded.numeric,
            "categorical": encoded.categorical, "max_categories": MAX_CATEGORIES,
            "X_train": [encoded.X[i] for i in train], "y_train": [encoded.y[i] for i in train],
            "groups_train": [encoded.groups[i] for i in train], "X_held_out": [encoded.X[i] for i in held], **fields}


def folds_for(prepared: Prepared, encoded: Encoded, asked: int) -> tuple[int, str]:
    """The folds the training rows allow (each class, or the whole, needs as many distinct rows as folds), and why
    fewer were used than asked, or ``""``."""
    train = prepared.split.train
    if prepared.spec.task == "classification":
        per: dict[Any, set[int]] = {}
        for i in train:
            per.setdefault(encoded.y[i], set()).add(encoded.groups[i])
        room = min(len(g) for g in per.values())
        what = "distinct training rows in its smallest class"
    else:
        room = len({encoded.groups[i] for i in train})
        what = "distinct training rows"
    if room < 2:
        raise MLError(INPUT_INVALID, f"the training part has {room} {what}: too few to cross-validate")
    used = min(asked, room)
    return used, ("" if used == asked else f"folds lowered from {asked} to {used}: there are {room} {what}")


def run(payload: dict[str, Any], timeout_s: float, script: str = SCRIPT, *, files: dict[str, bytes] | None = None,
        keep: tuple[str, ...] = (), memory: str = "address") -> dict[str, Any]:
    """Run ``payload`` in the bounded child with ``script`` (G6's own: :data:`SCRIPT`, or a member's fixed script) and
    return its result, or raise :class:`MLError` (``G6_E_ML_DEADLINE`` when it ran past ``timeout_s``,
    ``G6_E_ML_FIT_FAILED`` otherwise). ``files`` are written beside the job (files G6 itself wrote, never a caller's);
    the files named in ``keep`` that the child wrote come back under ``"files"``. ``memory`` is what the
    :data:`MEMORY_MB` cap counts on POSIX (``g6.core.sandbox.MEMORY_LIMITS``): the address space, or the data segment
    for a child that maps large shared libraries."""
    from g6.core.sandbox import run_script

    started = time.monotonic()
    done = run_script(script, {**(files or {}), JOB: json.dumps(payload)}, timeout_s=timeout_s, mem_mb=MEMORY_MB,
                      max_output_bytes=32 * 1024 * 1024, memory=memory)
    if done.status == "timeout":
        raise MLError(DEADLINE, f"the fit did not finish within {timeout_s:g} s and was stopped")
    raw = done.outputs.get(RESULT)
    if done.status != "ok" or raw is None:
        tail = (done.stderr or "").strip().splitlines()[-3:]
        raise MLError(FIT_FAILED, f"the fit's process ended without a result ({done.status}"
                                  + (f", exit {done.returncode}" if done.returncode is not None else "") + "): "
                                  + (" | ".join(tail) or "no message")[:400])
    result = json.loads(raw)
    if result.get("error"):
        raise MLError(FIT_FAILED, f"the fit failed: {result['error']}"[:500])
    result["seconds_with_start"] = round(time.monotonic() - started, 3)
    if keep:
        result["files"] = {name: done.outputs[name] for name in keep if name in done.outputs}
    result["limits"] = {**done.limits, "network_scope": NETWORK_SCOPE,
                        "process": "a child of G6's own interpreter with a clean environment"}
    return result


# --- the child ------------------------------------------------------------------------------------------------------


def _as_float(X: Any) -> Any:
    import numpy as np

    return np.asarray(X, dtype=float)


def _estimator(spec: dict[str, Any], seed: int) -> Any:
    import importlib

    module, name = str(spec["module"]), str(spec["class"])
    if not module.startswith("sklearn.") or not name.isidentifier():
        raise ValueError(f"only scikit-learn's own estimators are fitted here, not {module}.{name}")
    cls = getattr(importlib.import_module(module), name)
    params = dict(spec.get("params") or {})
    if "random_state" in cls().get_params():
        params["random_state"] = seed
    return cls(**params)


def _pipeline(spec: dict[str, Any], job: dict[str, Any]) -> Any:
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler

    parts = []
    if job["numeric"]:
        steps = [("float", FunctionTransformer(_as_float)),
                 ("impute", SimpleImputer(strategy="median", add_indicator=True))]
        if spec.get("scale"):
            steps.append(("scale", StandardScaler()))
        parts.append(("numbers", Pipeline(steps), job["numeric"]))
    if job["categorical"]:
        parts.append(("text", Pipeline([
            ("impute", SimpleImputer(strategy="constant", fill_value="(missing)", missing_values=None)),
            ("encode", OneHotEncoder(handle_unknown="infrequent_if_exist", max_categories=job["max_categories"],
                                     sparse_output=False))]), job["categorical"]))
    prepare = ColumnTransformer(parts, sparse_threshold=0.0)
    return Pipeline([("prepare", prepare), ("model", _estimator(spec, job["seed"]))])


def _array(rows: list[list[Any]]) -> Any:
    import numpy as np

    out = np.empty((len(rows), len(rows[0]) if rows else 0), dtype=object)
    for i, row in enumerate(rows):
        out[i, :] = row
    return out


def folds(job: dict[str, Any]) -> tuple[Any, Any, list[tuple[Any, Any]]]:
    """In the child: the training rows as arrays, the targets as the estimator takes them, and the job's folds
    (stratified for classification, grouped by identical rows, shuffled with the seed)."""
    import numpy as np
    from sklearn.model_selection import GroupKFold, StratifiedGroupKFold

    X, groups = _array(job["X_train"]), np.asarray(job["groups_train"])
    y = np.asarray(job["y_train"], dtype=object)
    if job["task"] == "classification":
        splitter = StratifiedGroupKFold(n_splits=job["folds"], shuffle=True, random_state=job["seed"])
        y_fit: Any = y.astype(str)
    else:
        splitter = GroupKFold(n_splits=job["folds"], shuffle=True, random_state=job["seed"])
        y_fit = y.astype(float)
    return X, y_fit, list(splitter.split(X, y_fit, groups))


def cross_validate(spec: dict[str, Any], job: dict[str, Any], X: Any, y_fit: Any,
                   splits: list[tuple[Any, Any]]) -> list[float]:
    """In the child: a fresh pipeline fitted on each fold's training rows and scored on its other rows by G6's
    metric; raises when the metric is undefined in a fold."""
    scores = []
    for train, valid in splits:
        model = _pipeline(spec, job).fit(X[train], y_fit[train])
        score = _metrics.metric(job["metric"], _plain(y_fit[valid], job["task"]),
                                _plain(model.predict(X[valid]), job["task"]))
        if score is None:
            raise ValueError(f"{job['metric']} is undefined in a fold (its true values are all equal)")
        scores.append(round(score, 6))
    return scores


def _plain(values: Any, task: str) -> list[Any]:
    return [str(v) for v in values] if task == "classification" else [float(v) for v in values]


def fit_predict(job: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """In the child: one pipeline fitted on all the training rows, and its predictions for the held-out, training
    and extra rows."""
    X, y = _array(job["X_train"]), job["y_train"]
    model = _pipeline(spec, job).fit(X, y)
    out = {"held_out": _plain(model.predict(_array(job["X_held_out"])), job["task"]),
           "train": _plain(model.predict(X), job["task"])}
    if job.get("X_predict"):
        out["predict"] = _plain(model.predict(_array(job["X_predict"])), job["task"])
    encoder = model.named_steps["prepare"].named_transformers_.get("text")
    if encoder is not None:
        categories = encoder.named_steps["encode"].categories_
        out["categories"] = [len(c) for c in categories]
    return out


def _rank(job: dict[str, Any]) -> dict[str, Any]:
    X, y_fit, splits = folds(job)
    table = []
    for spec in job["candidates"]:
        started = time.monotonic()
        row: dict[str, Any] = {"name": spec["name"]}
        try:
            row["fold_scores"] = cross_validate(spec, job, X, y_fit, splits)
        except Exception as exc:  # noqa: BLE001 - a candidate that cannot be validated is excluded, with why
            row = {"name": spec["name"], "excluded": f"{type(exc).__name__}: {exc}"[:300]}
        row["seconds"] = round(time.monotonic() - started, 3)
        table.append(row)
    better = _metrics.HIGHER_IS_BETTER[job["metric"]]
    scored = [r for r in table if "excluded" not in r]
    out: dict[str, Any] = {"cv": table, "folds": [{"train": len(a), "valid": len(b)} for a, b in splits]}
    if not scored:
        return out
    means = {r["name"]: sum(r["fold_scores"]) / len(r["fold_scores"]) for r in scored}
    chosen = max(scored, key=lambda r: means[r["name"]] if better else -means[r["name"]])["name"]
    out["chosen"] = chosen
    out.update(fit_predict(job, next(s for s in job["candidates"] if s["name"] == chosen)))
    return out


def child_main() -> None:
    """Read ``job.json`` from the working folder, fit, and write ``result.json``; an error is written as one."""
    import warnings

    job = json.loads(Path(JOB).read_text(encoding="utf-8"))
    started = time.monotonic()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            result = fit_predict(job, job["estimator"]) if job["kind"] == "fit" else _rank(job)
        except Exception as exc:  # noqa: BLE001 - every failure goes back to the parent as a reason
            result = {"error": f"{type(exc).__name__}: {exc}"[:500]}
    import sklearn

    result["seconds"] = round(time.monotonic() - started, 3)
    result["versions"] = {"scikit-learn": sklearn.__version__}
    result["warnings"] = sorted({f"{w.category.__name__}: {w.message}"[:200] for w in caught})[:10]
    Path(RESULT).write_text(json.dumps(result), encoding="utf-8")


__all__ = ["Encoded", "JOB", "MAX_CATEGORIES", "MAX_TIMEOUT_S", "MEMORY_MB", "NETWORK_SCOPE", "RESULT", "SCRIPT",
           "SKLEARN_FLOOR", "child_main", "cross_validate", "encode", "encode_rows", "encode_with", "fit_predict", "folds",
           "folds_for", "job", "run"]
