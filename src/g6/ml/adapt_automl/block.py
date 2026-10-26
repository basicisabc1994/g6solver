# SPDX-License-Identifier: Apache-2.0
"""AdaptAutoMLBlock, the registry and MCP door (``g6_invoke``) to adapt_automl: a bounded, seeded search over
scikit-learn estimators and their settings, scored on rows the extension holds out itself.

Ops: ``search`` and ``backends``, plus ``ops`` and ``help``. ``search`` runs the family's checks
(``g6.ml._types.prepare``), then hands a registered search backend (``backends.search_backends``; FLAML by default)
the training part with its answers and the held-out rows without theirs, within a time budget and a trial cap. The
backend compares settings by cross-validation inside the training rows only; G6 scores what the chosen one predicted
for the held-out rows (``g6.ml._report``), against a baseline on the same rows, with intervals. Its leaderboard is
reported as the search's own cross-validated scores, never as the result. A backend that is not registered, or not
installed, is refused, never swapped for another. No model is kept. Results are ``qualified-draft`` at best.
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
    answer,
    prepare,
    refuse,
)
from g6.ml.adapt_automl import spaces
from g6.ml.adapt_automl.backends import MARGIN_S, NOT_SHIPPED, SearchJob, SearchOutcome, search_backends

MAX_BUDGET_S = 300.0
MAX_TRIALS = 200
_OPS = {
    "search": "search(rows, target, features, task, seed, backend, candidates, time_budget_s, trials, folds): a "
              "bounded search by cross-validation inside the training rows, the chosen settings scored once on the "
              "extension's held-out rows against a baseline",
    "backends": "backends(): the search backends registered and whether each is installed",
}


class SearchInput(BaseModel):
    """One search. Fields no op has are refused, among them any split, score, label or model file."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["search", "backends", "ops", "help"] = "help"
    rows: list[dict[str, Cell]] = Field(default_factory=list, max_length=MAX_ROWS)
    target: str = Field("", max_length=100)
    features: list[str] = Field(default_factory=list, max_length=MAX_FEATURES)
    task: Literal["classification", "regression"] | None = None
    seed: int | None = Field(None, ge=0, le=2**32 - 1)
    held_out_fraction: float = Field(0.25, ge=0.1, le=0.5)
    backend: str = Field("flaml", max_length=100)
    candidates: list[str] = Field(default_factory=list, max_length=8)
    time_budget_s: float = Field(30.0, gt=0, le=MAX_BUDGET_S)
    trials: int = Field(20, ge=1, le=MAX_TRIALS)
    folds: int = Field(3, ge=2, le=10)
    predict_rows: list[dict[str, Cell]] = Field(default_factory=list, max_length=1000)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _ids(d: SearchInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


def _spec(d: SearchInput) -> DatasetSpec:
    if d.task is None:
        raise MLError(INPUT_INVALID, "say the task: classification or regression")
    try:
        return DatasetSpec(rows=d.rows, target=d.target, features=d.features, task=d.task, seed=d.seed,
                           held_out_fraction=d.held_out_fraction)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ()))
        raise MLError(INPUT_INVALID, f"{where or 'the dataset'}: {first.get('msg', 'is not valid')}") from None


def _backend(name: str) -> Any:
    """The registered backend of that name, or a refusal saying which are registered (and why a known engine is
    not shipped)."""
    found = search_backends.get(name)
    if found is None:
        why = NOT_SHIPPED.get(name.lower().replace("-", "_"))
        listed = ", ".join(search_backends.names()) or "none"
        raise MLError(BACKEND_NOT_INSTALLED, (f"{why}; " if why else f"no search backend named {name[:60]!r}; ")
                      + f"registered: {listed}. Nothing was run, and no other backend was used instead")
    return found


def _ready(backend: Any) -> None:
    missing = backend.available()
    if missing:
        raise MLError(BACKEND_NOT_INSTALLED, f"the {backend.name} backend is not ready: {missing}. Nothing was run, "
                                             "and no other backend was used instead")


def _check(outcome: Any, names: list[str], held: int, train: int, better: bool) -> list[dict[str, Any]]:
    """The backend's outcome, checked before anything is reported from it."""
    if not isinstance(outcome, SearchOutcome):
        raise MLError(FIT_FAILED, f"the backend returned {type(outcome).__name__}, not a search outcome")
    board = [r for r in outcome.leaderboard if isinstance(r, dict) and r.get("name") in names]
    scored = [r for r in board if "excluded" not in r and isinstance(r.get("best_score"), (int, float))]
    if not scored:
        why = "; ".join(f"{r.get('name')}: {r.get('excluded')}" for r in board)[:400]
        raise MLError(FIT_FAILED, "no candidate finished a cross-validated trial" + (f": {why}" if why else ""))
    top = (max if better else min)(scored, key=lambda r: r["best_score"])
    if outcome.chosen != top["name"]:
        raise MLError(FIT_FAILED, f"the backend chose {outcome.chosen!r}, but its own best cross-validated score is "
                                  f"{top['name']!r}; nothing is reported from a disagreement")
    if len(outcome.held_out) != held or len(outcome.train) != train:
        raise MLError(FIT_FAILED, f"the backend returned {len(outcome.held_out)} held-out and {len(outcome.train)} "
                                  f"training predictions for {held} and {train} rows")
    return board


def _search(d: SearchInput) -> Any:
    spec = _spec(d)
    engine = _backend(d.backend.strip().lower())
    names = d.candidates or list(spaces.DEFAULT_CANDIDATES[spec.task])
    if len(set(names)) != len(names):
        raise MLError(INPUT_INVALID, "a candidate is named twice")
    children = spaces.resolve(names, spec.task)
    prepared = prepare(spec, predict_rows=d.predict_rows)
    encoded = _fit.encode(prepared)
    to_predict = _fit.encode_rows(prepared, [dict(r) for r in d.predict_rows])
    folds, lowered = _fit.folds_for(prepared, encoded, d.folds)
    metric = _report.headline_metric(prepared)
    _ready(engine)
    payload = _fit.job("search", prepared, encoded, candidates=children, folds=folds, metric=metric,
                       time_budget_s=d.time_budget_s, trials=d.trials, X_predict=to_predict)
    deadline = min(d.time_budget_s * 1.5 + MARGIN_S, _fit.MAX_TIMEOUT_S)
    outcome = engine.search(SearchJob(payload=payload, timeout_s=deadline))
    better = _metrics.HIGHER_IS_BETTER[metric]
    board = _check(outcome, names, len(prepared.split.held_out), len(prepared.split.train), better)
    measured = _report.measure(prepared, encoded, outcome.held_out, outcome.train, op="search")
    top = next(r for r in board if r["name"] == outcome.chosen)
    tried = sum(int(r.get("trials", 0)) for r in board)
    excluded = [{"name": r["name"], "why": r["excluded"]} for r in board if "excluded" in r]
    lead = (f"the {engine.name} search tried {tried} setting(s) of {len(names)} estimator(s) by {folds}-fold "
            "cross-validation inside the training rows only (ended by its " + (outcome.ended_by or "own end")
            + f"; best: {outcome.chosen}, mean {_report.metric_name(metric)} {top['best_score']:.3f} in the search, "
            "which picked it, so optimistic" + (f"; {len(excluded)} excluded, with why" if excluded else "")
            + f"); {outcome.chosen} refitted with those settings")
    message, card = _report.said(lead, prepared, measured)
    if lowered:
        message += f" ({lowered})"
    searched = {"kind": "ml_search", "backend": engine.name, "on": "the training rows only; the held-out rows chose "
                "nothing", "metric": metric, "folds": folds, "time_budget_s": d.time_budget_s, "trials": d.trials,
                "ended_by": outcome.ended_by, "leaderboard": board, "excluded": excluded,
                "leaderboard_means": "each estimator's best mean fold score in the search: chosen by the search, so "
                                     "optimistic; the headline is the held-out score",
                "chosen": outcome.chosen, "chosen_config": outcome.chosen_config, "lowered": lowered or None,
                "repeatable": "with the same rows, seed and limits, when the trial cap (not the time budget) ends "
                              "the search", **{k: v for k, v in outcome.details.items() if v is not None}}
    evidence = [*prepared.evidence, searched, {"kind": "ml_scores", **measured},
                _sources.cited(_rules(spec.task))]
    fields: dict[str, Any] = {"backend": engine.name, "chosen": outcome.chosen, "chosen_config": outcome.chosen_config,
                              "leaderboard": board, "headline": measured["headline"], "scores": measured["scores"],
                              "split": prepared.split.evidence(), "held_out_use": measured["held_out_use"]}
    if to_predict:
        fields["predictions"] = _report.predictions(encoded, spec.task, outcome.predict)
    return answer("search", message, card=card, evidence=evidence, **fields, **_ids(d))


def _rules(task: str) -> tuple[str, ...]:
    common = ("held_out", "held_out_from_rows", "grouped", "cv_training_part", "search_training_part",
              "preprocessing_in_pipeline", "seed", "baseline", "leakage_probes", "too_good", "min_held_out", "interval",
              "overfit_gap", "search_bounds", "limits", "bounds")
    return (*common, *(("stratified", "headline_imbalance", "accuracy", "balanced_accuracy")
                       if task == "classification" else ("r2", "mae", "rmse")))


def _backends(d: SearchInput) -> Any:
    listed = search_backends.describe()
    ready = [b["name"] for b in listed if not b["available"]]
    said = "; ".join(f"{b['name']}: " + ("installed" if not b["available"] else b["available"]) for b in listed)
    message = f"{len(listed)} search backend(s) registered, {len(ready)} ready ({said})"
    return answer("backends", message, card=DESCRIBED, backends=listed, not_shipped=dict(NOT_SHIPPED),
                  candidates={n: s.describe() for n, s in spaces.SPACES.items()}, defaults=spaces.DEFAULT_CANDIDATES,
                  evidence=[{"kind": "ml_backends", "registered": [b["name"] for b in listed], "ready": ready}],
                  **_ids(d))


def _run(d: SearchInput) -> Any:
    try:
        return _search(d) if d.op == "search" else _backends(d)
    except MLError as exc:
        return refuse(d.op, exc.code, exc.reason, evidence=exc.evidence, **_ids(d))


@block_contract(
    purpose="search scikit-learn estimators and their settings within a time budget and a trial cap, by "
            "cross-validation inside the training rows only, and score the choice once on a held-out split the "
            "extension makes itself, after checks for leakage, identical rows, imbalance and the seed",
    mitigates_failure_modes=("unsystematic_tuning", "silent_fallback", "train_test_leakage"),
    verification_method="held-out rows the search never sees the answers for, scored by G6's own code against a "
                        "baseline on the same rows, with seeded 95% intervals; a missing backend refused by name",
)
@component_maturity(
    "beta",
    reason="FLAML's seeded tuner over scikit-learn estimators out of G6's process, under a deadline; scores computed "
           "by G6; built in a vetted area (statistical rules), awaiting review; qualified-draft at best",
)
class AdaptAutoMLBlock(AIBlock[SearchInput, dict, dict]):
    """Search estimators and settings, and score the choice on held-out rows. Never ``verified``."""

    def __init__(self, name: str = "adapt_automl", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: SearchInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": []})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        try:
            result = _run(data)
        except Exception as exc:  # noqa: BLE001 - an error is a refusal with its reason, never a crash or a score
            result = refuse(data.op, OP_FAILED, f"{data.op} failed: {type(exc).__name__}: {exc}"[:500], **_ids(data))
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


__all__ = ["MAX_BUDGET_S", "MAX_TRIALS", "AdaptAutoMLBlock", "SearchInput"]
