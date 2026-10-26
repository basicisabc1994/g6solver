# SPDX-License-Identifier: Apache-2.0
"""AdaptSklearnBlock, the registry and MCP door (``g6_invoke``) to adapt_sklearn: fit and score a scikit-learn
estimator from G6's own list on a held-out split the extension makes itself.

Ops: ``fit_eval``, ``rank`` and ``estimators``, plus ``ops`` and ``help``. ``fit_eval`` and ``rank`` take the rows,
the target, the features, the task and a seed, run the family's checks (``g6.ml._types.prepare``: the seed, the
columns, the split's classes and size, identical rows across the split, target leakage, class imbalance) and refuse
on the first that fails. Then the fit runs out of G6's process (``g6.ml._fit``), given the training rows with their
answers and the held-out rows without theirs, and G6 scores what it predicted (``g6.ml._report``): the headline
metric against a baseline on the same rows, the lift, the train-held-out gap and 95% intervals. ``rank`` (from the
archive's adapt_learning) cross-validates several candidates inside the training rows only, picks the best mean fold
score, and scores that one once on the held-out rows. No model is kept: a model exists only for its call, so no model
file is ever written or loaded. Results are ``qualified-draft`` at best.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.ml import _fit, _metrics, _report, _sources
from g6.ml._types import (
    BACKEND_NOT_INSTALLED,
    DESCRIBED,
    FIT_FAILED,
    INPUT_INVALID,
    MAX_FEATURES,
    MAX_ROWS,
    OP_FAILED,
    Cell,
    DatasetSpec,
    MLError,
    Prepared,
    answer,
    cell_kind,
    missing_extra,
    prepare,
    refuse,
)
from g6.ml.adapt_sklearn import estimators

EXTRA = ("ml", ("sklearn", "numpy"), "scikit-learn")
_OPS = {
    "fit_eval": "fit_eval(rows, target, features, task, seed, estimator, params): fit on the extension's own training "
                "split and score on its held-out rows, against a baseline, with intervals; optional predict_rows",
    "rank": "rank(rows, target, features, task, seed, candidates, folds): cross-validate candidates inside the "
            "training rows only, then score the best once on the held-out rows",
    "estimators": "estimators(task): the estimators G6 fits, their parameters and ranges",
}
_RULES_COMMON = ("held_out", "held_out_from_rows", "grouped", "preprocessing_in_pipeline", "seed", "baseline",
                 "leakage_probes", "too_good", "min_held_out", "interval", "overfit_gap", "limits", "bounds")


class SklearnInput(BaseModel):
    """One fit or ranking. Fields no op has are refused, among them any split, score, label or model file."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["fit_eval", "rank", "estimators", "ops", "help"] = "help"
    rows: list[dict[str, Cell]] = Field(default_factory=list, max_length=MAX_ROWS)
    target: str = Field("", max_length=100)
    features: list[str] = Field(default_factory=list, max_length=MAX_FEATURES)
    task: Literal["classification", "regression"] | None = None
    seed: int | None = Field(None, ge=0, le=2**32 - 1)
    held_out_fraction: float = Field(0.25, ge=0.1, le=0.5)
    estimator: str = Field("", max_length=300)
    params: dict[str, bool | int | float | str | None] = Field(default_factory=dict, max_length=20)
    candidates: list[str] = Field(default_factory=list, max_length=12)
    folds: int = Field(5, ge=2, le=10)
    predict_rows: list[dict[str, Cell]] = Field(default_factory=list, max_length=1000)
    timeout_s: float = Field(120.0, gt=0, le=_fit.MAX_TIMEOUT_S)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _ids(d: SklearnInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


def _spec(d: SklearnInput) -> DatasetSpec:
    if d.task is None:
        raise MLError(INPUT_INVALID, "say the task: classification or regression")
    try:
        return DatasetSpec(rows=d.rows, target=d.target, features=d.features, task=d.task, seed=d.seed,
                           held_out_fraction=d.held_out_fraction)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ()))
        raise MLError(INPUT_INVALID, f"{where or 'the dataset'}: {first.get('msg', 'is not valid')}") from None


def _rules(prepared: Prepared, extra: tuple[str, ...]) -> dict[str, Any]:
    task = ("stratified", "headline_imbalance", "accuracy", "balanced_accuracy") \
        if prepared.spec.task == "classification" else ("r2", "mae", "rmse")
    return _sources.cited((*_RULES_COMMON, *task, *extra))


def _installed() -> None:
    missing = missing_extra(*EXTRA)
    if missing:
        raise MLError(BACKEND_NOT_INSTALLED, missing)


def _fit_evidence(result: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "ml_fit", "estimator": spec["name"], "class": f"{spec['module']}.{spec['class']}",
            "params": spec["params"], "set_by_caller": spec["set_by_caller"], "inputs_scaled": spec["scale"],
            "pipeline": "numbers: training median imputation with a missing-value indicator"
                        + (", then scaling" if spec["scale"] else "") + "; text: its own category for a missing "
                        f"value, one-hot encoded (at most {_fit.MAX_CATEGORIES} per column); then the estimator, "
                        "seeded; every step fitted on the training rows only",
            "one_hot_categories": result.get("categories"), "versions": result.get("versions"),
            "seconds": result.get("seconds"), "warnings": result.get("warnings", []), "limits": result.get("limits"),
            "model_kept": False}


def _fit_eval(d: SklearnInput) -> Any:
    if not d.estimator:
        raise MLError(INPUT_INVALID, "name an estimator from op estimators")
    spec = _spec(d)
    child = estimators.resolve(d.estimator, spec.task, d.params)
    prepared = prepare(spec, d.params, d.predict_rows)
    encoded = _fit.encode(prepared)
    to_predict = _fit.encode_rows(prepared, [dict(r) for r in d.predict_rows])
    _installed()
    result = _fit.run(_fit.job("fit", prepared, encoded, estimator=child, X_predict=to_predict), d.timeout_s)
    measured = _report.measure(prepared, encoded, result["held_out"], result["train"], op="fit_eval")
    message, card = _report.said(f"{d.estimator} fitted", prepared, measured)
    evidence = [*prepared.evidence, _fit_evidence(result, child), {"kind": "ml_scores", **measured},
                _rules(prepared, ())]
    fields: dict[str, Any] = {"estimator": d.estimator, "headline": measured["headline"],
                              "scores": measured["scores"], "split": prepared.split.evidence(),
                              "held_out_use": measured["held_out_use"]}
    if to_predict:
        fields["predictions"] = _report.predictions(encoded, spec.task, result.get("predict", []))
    return answer("fit_eval", message, card=card, evidence=evidence, **fields, **_ids(d))


def _meta_features(prepared: Prepared) -> dict[str, Any]:
    """What the data are like, computed from the rows (the archive's adapt_learning guessed scores from these)."""
    spec = prepared.spec
    numeric = [f for f, k in prepared.kinds.items() if k == "numeric"]
    cells = [r.get(f) for r in spec.rows for f in spec.features]
    missing = sum(1 for v in cells if cell_kind(v) == "missing")
    numbers = [float(r.get(f)) for r in spec.rows for f in numeric if cell_kind(r.get(f)) in ("number", "boolean")]
    return {"kind": "ml_meta_features", "rows": len(spec.rows), "features": len(spec.features),
            "numeric_features": len(numeric), "text_features": len(spec.features) - len(numeric),
            "rows_per_feature": round(len(spec.rows) / len(spec.features), 3),
            "missing_share": round(missing / len(cells), 6) if cells else 0.0,
            "sparsity": round(sum(1 for x in numbers if x == 0) / len(numbers), 6) if numbers else None,
            "sparsity_means": "the share of zeros among the numeric values present",
            "class_balance": prepared.balance, "used_for": "description only: no score is guessed from these"}



def _rank(d: SklearnInput) -> Any:
    spec = _spec(d)
    names = d.candidates or list(estimators.DEFAULT_CANDIDATES[spec.task])
    if len(set(names)) != len(names):
        raise MLError(INPUT_INVALID, "a candidate is named twice")
    if d.params:
        raise MLError(INPUT_INVALID, "rank compares candidates at their own settings; params are for fit_eval")
    children = [estimators.resolve(n, spec.task) for n in names]
    prepared = prepare(spec)
    encoded = _fit.encode(prepared)
    folds, lowered = _fit.folds_for(prepared, encoded, d.folds)
    metric = _report.headline_metric(prepared)
    _installed()
    result = _fit.run(_fit.job("rank", prepared, encoded, candidates=children, folds=folds, metric=metric),
                      d.timeout_s)
    table, excluded = [], []
    for row in result["cv"]:
        if "excluded" in row:
            excluded.append({"name": row["name"], "why": row["excluded"]})
            continue
        scores = row["fold_scores"]
        mean = sum(scores) / len(scores)
        spread = (sum((s - mean) ** 2 for s in scores) / (len(scores) - 1)) ** 0.5 if len(scores) > 1 else 0.0
        table.append({"name": row["name"], "metric": metric, "mean": round(mean, 6), "std": round(spread, 6),
                      "fold_scores": scores, "seconds": row.get("seconds")})
    better = _metrics.HIGHER_IS_BETTER[metric]
    table.sort(key=lambda r: (-r["mean"] if better else r["mean"], names.index(r["name"])))
    if not table:
        raise MLError(FIT_FAILED, "no candidate could be cross-validated: "
                                  + "; ".join(f"{e['name']}: {e['why']}" for e in excluded)[:400])
    if result.get("chosen") != table[0]["name"]:
        raise MLError(FIT_FAILED, f"the fit chose {result.get('chosen')!r}, but the best mean fold score is "
                                  f"{table[0]['name']!r}; nothing is reported from a disagreement")
    chosen = table[0]["name"]
    measured = _report.measure(prepared, encoded, result["held_out"], result["train"], op="rank")
    lead = (f"{len(table)} candidate(s) ranked by {folds}-fold cross-validation inside the training rows only "
            f"(best: {chosen}, mean {_report.metric_name(metric)} {table[0]['mean']:.3f}, sd {table[0]['std']:.3f}"
            + (f"; {len(excluded)} excluded, with why" if excluded else "") + f"); {chosen} refitted")
    message, card = _report.said(lead, prepared, measured)
    if lowered:
        message += f" ({lowered})"
    cv = {"kind": "ml_cross_validation", "on": "the training rows only; the held-out rows chose nothing",
          "metric": metric, "folds": folds, "fold_sizes": result.get("folds"),
          "splitter": ("stratified by class, " if spec.task == "classification" else "") +
                      "grouped by identical rows, shuffled with the seed", "ranking": table, "excluded": excluded,
          "lowered": lowered or None}
    chosen_spec = next(c for c in children if c["name"] == chosen)
    evidence = [*prepared.evidence, _meta_features(prepared), cv, _fit_evidence(result, chosen_spec),
                {"kind": "ml_scores", **measured}, _rules(prepared, ("cv_training_part",))]
    return answer("rank", message, card=card, evidence=evidence, chosen=chosen, ranking=table, excluded=excluded,
                  headline=measured["headline"], scores=measured["scores"], split=prepared.split.evidence(),
                  held_out_use=measured["held_out_use"], **_ids(d))


def _estimators(d: SklearnInput) -> Any:
    listed = [e.describe() for e in estimators.CATALOGUE.values() if d.task is None or d.task in e.classes]
    missing = missing_extra(*EXTRA)
    ready = "scikit-learn is installed" if not missing else missing
    message = f"{len(listed)} estimator(s) G6 fits{f' for {d.task}' if d.task else ''}; {ready}"
    return answer("estimators", message, card=DESCRIBED, estimators=listed, defaults=estimators.DEFAULT_CANDIDATES,
                  evidence=[{"kind": "ml_estimators", "listed": len(listed), "installed": not missing}], **_ids(d))


def _run(d: SklearnInput) -> Any:
    try:
        if d.op == "fit_eval":
            return _fit_eval(d)
        if d.op == "rank":
            return _rank(d)
        return _estimators(d)
    except MLError as exc:
        return refuse(d.op, exc.code, exc.reason, evidence=exc.evidence, **_ids(d))


@block_contract(
    purpose="fit and score a scikit-learn estimator from G6's own list on a held-out split the extension makes "
            "itself, after checks for leakage, identical rows across the split, class imbalance and the seed; or "
            "rank candidates by cross-validation inside the training rows only",
    mitigates_failure_modes=("train_test_leakage", "ill_suited_model"),
    verification_method="held-out rows the fitting process never sees the answers for, scored by G6's own code "
                        "against a baseline on the same rows, with seeded 95% intervals; leakage probes before any fit",
)
@component_maturity(
    "beta",
    reason="fits out of G6's process under a deadline; scores computed by G6 from held-out answers the fit never "
           "saw; built in a vetted area (statistical rules), awaiting review; qualified-draft at best",
)
class AdaptSklearnBlock(AIBlock[SklearnInput, dict, dict]):
    """Fit, rank and score scikit-learn estimators on held-out rows. Never ``verified``."""

    def __init__(self, name: str = "adapt_sklearn", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: SklearnInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": []})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        try:
            result = _run(data)
        except Exception as exc:  # noqa: BLE001 - an error is a refusal with its reason, never a crash or a score
            result = refuse(data.op, OP_FAILED, f"{data.op} failed: {type(exc).__name__}: {exc}"[:500], **_ids(data))
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


__all__ = ["AdaptSklearnBlock", "SklearnInput"]
