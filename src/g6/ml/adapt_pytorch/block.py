# SPDX-License-Identifier: Apache-2.0
"""AdaptPytorchBlock, the registry and MCP door (``g6_invoke``) to adapt_pytorch: train a small network from G6's
curated layer list on a held-out split the extension makes itself, and score it in G6's process.

Ops: ``train_eval``, ``predict``, ``models``, ``forget`` and ``layers``, plus ``ops`` and ``help``; ``forget`` deletes a
kept model, so it is sensitive (:data:`SENSITIVE_OPS`). ``train_eval`` runs the family's
checks (``g6.ml._types.prepare``: the seed, the columns, the split, identical rows across it, target leakage, class
imbalance), resolves the layers (``layers.resolve``: anything that could carry code is refused first), encodes the
rows from the fitting rows only (``encoding``), and trains in G6's own child process (``torch_child``), which is given
the training rows with their answers and the held-out rows without theirs. G6 scores what it predicted
(``g6.ml._report``): the held-out headline against a baseline on the same rows, never the training score. A loss that
is infinite or not a number stops training and is refused. With ``keep`` the weights are stored under ``G6_HOME``
(``kept``) and ``predict`` uses them by the id G6 gave; a full store refuses ``keep`` before training, and nothing is
ever removed but by ``forget``. Results are ``qualified-draft`` at best.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.ml import _fit, _report, _sources
from g6.ml._types import (
    BACKEND_NOT_INSTALLED,
    DESCRIBED,
    FIT_FAILED,
    INPUT_INVALID,
    MAX_FEATURES,
    MAX_ROWS,
    NON_FINITE_LOSS,
    NON_FINITE_OUTPUT,
    NON_FINITE_WEIGHTS,
    OP_FAILED,
    PREDICTED,
    TOO_LARGE,
    Cell,
    DatasetSpec,
    MLError,
    answer,
    bounded,
    missing_extra,
    prepare,
    refuse,
)
from g6.ml.adapt_pytorch import encoding, kept, layers, torch_child

EXTRA = ("torch", ("torch",), "PyTorch")
_OPS = {
    "train_eval": "train_eval(rows, target, features, task, seed, layers, epochs, batch_size, learning_rate, "
                  "patience): train a network from the curated layers on the extension's own training split and score "
                  "it on its held-out rows against a baseline; optional predict_rows and keep",
    "predict": "predict(model_id, predict_rows): predictions of a model train_eval kept",
    "models": "models(): the models kept under G6_HOME, newest first",
    "forget": "forget(model_id): delete one kept model, its weights and its record (sensitive)",
    "layers": "layers(): the curated layer types, their settings and what is refused as code",
}
#: forget deletes stored data, as knowledge_indexer's remove and ctx_ace's forget do; keep writes only G6's own state.
SENSITIVE_OPS: tuple[str, ...] = ("forget",)
_PARTS = {"held_out": "held-out rows", "train": "training rows", "predict": "rows to predict"}
_RULES = ("held_out", "held_out_from_rows", "grouped", "preprocessing_in_pipeline", "seed", "baseline",
          "leakage_probes", "too_good", "min_held_out", "interval", "overfit_gap", "bounds")


class PytorchInput(BaseModel):
    """One training run, or a prediction from a kept model. Fields no op has are refused, among them any split, score,
    label, model file or path."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["train_eval", "predict", "models", "forget", "layers", "ops", "help"] = "help"
    rows: list[dict[str, Cell]] = Field(default_factory=list, max_length=MAX_ROWS)
    target: str = Field("", max_length=100)
    features: list[str] = Field(default_factory=list, max_length=MAX_FEATURES)
    task: Literal["classification", "regression"] | None = None
    seed: int | None = Field(None, ge=0, le=2**31 - 1)
    held_out_fraction: float = Field(0.25, ge=0.1, le=0.5)
    layers: list[dict[str, bool | int | float | str | None]] = Field(default_factory=list, max_length=20)
    epochs: int = Field(200, ge=1, le=500)
    batch_size: int = Field(32, ge=1, le=4096)
    learning_rate: float = Field(0.001, gt=0, le=1.0)
    weight_decay: float = Field(0.0, ge=0, le=1.0)
    optimiser: Literal["adam", "adamw", "sgd"] = "adam"
    patience: int = Field(20, ge=0, le=100)
    predict_rows: list[dict[str, Cell]] = Field(default_factory=list, max_length=1000)
    keep: bool = False
    model_id: str = Field("", max_length=300)
    timeout_s: float = Field(180.0, gt=0, le=_fit.MAX_TIMEOUT_S)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _ids(d: PytorchInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


def _installed() -> None:
    missing = missing_extra(*EXTRA)
    if missing:
        raise MLError(BACKEND_NOT_INSTALLED, missing)


def _spec(d: PytorchInput) -> DatasetSpec:
    if d.task is None:
        raise MLError(INPUT_INVALID, "say the task: classification or regression")
    try:
        return DatasetSpec(rows=d.rows, target=d.target, features=d.features, task=d.task, seed=d.seed,
                           held_out_fraction=d.held_out_fraction)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ()))
        raise MLError(INPUT_INVALID, f"{where or 'the dataset'}: {first.get('msg', 'is not valid')}") from None


def _gradients(norms: dict[str, dict[str, float]]) -> dict[str, Any]:
    flat = [(when, name, v) for when, found in norms.items() for name, v in found.items()]
    return {"kind": "nn_gradients", "norms": norms,
            "vanishing": [f"{n} at the {w.replace('_', ' ')} ({v:.2e})" for w, n, v in flat
                          if v < torch_child.VANISHING],
            "exploding": [f"{n} at the {w.replace('_', ' ')} ({v:.2e})" for w, n, v in flat
                          if v > torch_child.EXPLODING],
            "thresholds": {"vanishing_below": torch_child.VANISHING, "exploding_above": torch_child.EXPLODING},
            "effect": "a note only"}


def _train_eval(d: PytorchInput) -> Any:
    if d.keep:
        kept.room()
    chosen = layers.resolve(d.layers)
    spec = _spec(d)
    prepared = prepare(spec, None, d.predict_rows)
    encoded = _fit.encode(prepared)
    to_predict = _fit.encode_rows(prepared, d.predict_rows)
    valid = encoding.validation(prepared, encoded, spec.seed) if d.patience else []
    train = prepared.split.train
    aside = set(valid)
    fit_rows = [i for pos, i in enumerate(train) if pos not in aside]
    enc = encoding.fit(prepared, fit_rows)
    y_train = encoding.targets(prepared, encoded, enc, fit_rows)
    outputs = len(enc.classes) if spec.task == "classification" else 1
    weights = layers.parameters(chosen, enc.width(), outputs)
    if weights > layers.MAX_PARAMETERS:
        raise MLError(TOO_LARGE, f"the network would hold {weights:,} weights; at most {layers.MAX_PARAMETERS:,}")
    encoding.bounded(enc, len(train) + len(prepared.split.held_out) + len(d.predict_rows))
    _installed()
    job = {"kind": "train", "task": spec.task, "seed": spec.seed, "layers": chosen, "outputs": outputs,
           "optimiser": d.optimiser, "learning_rate": d.learning_rate, "weight_decay": d.weight_decay,
           "epochs": d.epochs, "batch_size": d.batch_size, "patience": d.patience, "valid": valid,
           "X_train": [enc.row(encoded.X[i], f"row {i}") for i in train], "y_train": y_train,
           "X_held_out": [enc.row(encoded.X[i], f"row {i}") for i in prepared.split.held_out],
           "X_predict": [enc.row(r, f"row {k} to predict") for k, r in enumerate(to_predict)], "keep": d.keep,
           "memory_mb": _fit.MEMORY_MB}
    result = _fit.run(job, d.timeout_s, script=torch_child.SCRIPT, keep=(torch_child.WEIGHTS,) if d.keep else (),
                      memory=torch_child.MEMORY)
    history = result.get("history", [])
    if result.get("non_finite"):
        where = result["non_finite"]
        raise MLError(NON_FINITE_LOSS, f"the training loss became {where['loss']} at epoch {where['epoch']}"
                                       + (f", minibatch {where['batch']}" if where["batch"] else "")
                                       + "; training was stopped and nothing was scored",
                      [*prepared.evidence, {"kind": "nn_training", "history": history[-20:],
                                            "stopped": "non-finite loss"}])
    if result.get("non_finite_weights"):
        names = ", ".join(result["non_finite_weights"])
        raise MLError(NON_FINITE_WEIGHTS, f"after {len(history)} epoch(s) the weights {names} were infinite or not a "
                                          "number, though every loss measured was finite (a last step broke them); "
                                          "nothing was scored or kept",
                      [*prepared.evidence, {"kind": "nn_training", "history": history[-20:],
                                            "stopped": "non-finite weights"}])
    if result.get("non_finite_output"):
        where = result["non_finite_output"]
        raise MLError(NON_FINITE_OUTPUT, f"the trained network's outputs for {where['rows']} of the {where['of']} "
                                         f"{_PARTS.get(where['part'], 'rows')} were infinite or not a number in its "
                                         "32-bit floats, though every input was in range and every weight finite; "
                                         "nothing was scored",
                      [*prepared.evidence, {"kind": "nn_training", "history": history[-20:],
                                            "stopped": "non-finite outputs"}])
    held = encoding.decode(enc, spec.task, result["held_out"])
    measured = _report.measure(prepared, encoded, held, encoding.decode(enc, spec.task, result["train"]),
                               op="train_eval")
    stop = (f"stopped early at epoch {len(history)}, the weights of epoch {result['best_epoch']} kept" if
            result.get("stopped_early") else f"{len(history)} epochs" + (
                f", the weights of epoch {result['best_epoch']} kept" if valid else ""))
    message, card = _report.said(f"a network of {result.get('parameters', weights):,} weights trained ({stop})",
                                 prepared, measured)
    grads = _gradients(result.get("gradient_norms", {}))
    if grads["vanishing"] or grads["exploding"]:
        message += " Gradients: " + "; ".join([*(f"vanishing {x}" for x in grads["vanishing"][:2]),
                                               *(f"exploding {x}" for x in grads["exploding"][:2])]) + "."
    training = {"kind": "nn_training", "layers": chosen, "inputs": enc.width(), "outputs": outputs,
                "parameters": result.get("parameters", weights), "optimiser": d.optimiser,
                "learning_rate": d.learning_rate, "weight_decay": d.weight_decay, "batch_size": d.batch_size,
                "epochs_asked": d.epochs, "epochs_run": len(history), "patience": d.patience,
                "validation_rows": len(valid), "fitting_rows": len(fit_rows),
                "validation_from": "the training rows only, whole groups of identical rows, drawn with the seed; the "
                                   "held-out rows never decide when to stop",
                "stopped_early": bool(result.get("stopped_early")), "best_epoch": result.get("best_epoch"),
                "history": history, "encoding": "fitted on the fitting rows only (medians, means, standard "
                                                "deviations and categories)",
                "seed": spec.seed, "seeded": "torch.manual_seed, a seeded minibatch order, deterministic algorithms",
                "versions": result.get("versions"), "seconds": result.get("seconds"),
                "warnings": result.get("warnings", []), "limits": result.get("limits"), "model_kept": False}
    fields: dict[str, Any] = {"headline": measured["headline"], "scores": measured["scores"],
                              "split": prepared.split.evidence(), "held_out_use": measured["held_out_use"],
                              "history": history}
    if d.predict_rows:
        fields["predictions"] = _report.predictions(encoded, spec.task,
                                                    encoding.decode(enc, spec.task, result.get("predict", [])))
    if d.keep:
        saved = result.get("files", {}).get(torch_child.WEIGHTS)
        if saved is None:
            raise MLError(FIT_FAILED, "the training process wrote no weights to keep")
        model_id = kept.keep(saved, {"task": spec.task, "features": list(spec.features), "layers": chosen,
                                     "inputs": enc.width(), "outputs": outputs, "encoding": enc.to_json(),
                                     "labels": encoded.labels, "headline": measured["headline"],
                                     "rows_sha256": prepared.split.rows_sha256})
        training.update(model_kept=True, model_id=model_id)
        fields["model_id"] = model_id
        message += f" Kept as {model_id}."
    evidence = [*prepared.evidence, training, grads, {"kind": "ml_scores", **measured},
                _sources.cited((*_RULES, *(("stratified", "headline_imbalance", "accuracy", "balanced_accuracy")
                                           if spec.task == "classification" else ("r2", "mae", "rmse"))))]
    if d.predict_rows:
        evidence.append({"kind": "nn_predict_rows", **enc.imputed(to_predict)})
    return answer("train_eval", message, card=card, evidence=evidence, **fields, **_ids(d))


def _predict(d: PytorchInput) -> Any:
    if not d.predict_rows:
        raise MLError(INPUT_INVALID, "give predict_rows")
    weights, meta = kept.load(d.model_id)
    bounded(d.predict_rows, set(meta["features"]), "row to predict")
    enc = encoding.Encoding.from_json(meta["encoding"])
    encoding.bounded(enc, len(d.predict_rows))
    to_predict = _fit.encode_with(enc.features, enc.kinds(), d.predict_rows)
    rows = [enc.row(r, f"row {k} to predict") for k, r in enumerate(to_predict)]
    _installed()
    job = {"kind": "predict", "task": meta["task"], "layers": meta["layers"], "inputs": meta["inputs"],
           "outputs": meta["outputs"], "X_predict": rows, "memory_mb": _fit.MEMORY_MB}
    result = _fit.run(job, d.timeout_s, script=torch_child.SCRIPT, files={torch_child.WEIGHTS: weights},
                      memory=torch_child.MEMORY)
    if result.get("non_finite_output"):
        where = result["non_finite_output"]
        raise MLError(NON_FINITE_OUTPUT, f"kept model {d.model_id}'s outputs for {where['rows']} of the {where['of']} "
                                         "rows to predict were infinite or not a number in its 32-bit floats, though "
                                         "every input was in range: a row far outside the rows it was trained on? "
                                         "Nothing was predicted")
    values = encoding.decode(enc, meta["task"], result["predict"])
    labels = meta.get("labels") or {}
    shown = [labels.get(v, v) if meta["task"] == "classification" else round(v, 6) for v in values]
    message = (f"{len(shown)} prediction(s) from kept model {d.model_id}, whose held-out "
               f"{_report.metric_name(meta['headline']['metric'])} was {meta['headline']['held_out']} against "
               f"{meta['headline']['baseline']} for the baseline when it was trained")
    evidence = [{"kind": "nn_predict", "model_id": d.model_id, "weights_sha256": meta["weights_sha256"],
                 "loaded": "G6's own file, its hash checked, read with torch.load(weights_only=True) in the child",
                 "kept_at": meta.get("kept_at"), "rows_sha256": meta.get("rows_sha256"),
                 "versions": result.get("versions"), "limits": result.get("limits")},
                {"kind": "nn_predict_rows", **enc.imputed(to_predict)}]
    return answer("predict", message, card=PREDICTED, evidence=evidence, model_id=d.model_id,
                  predictions=[{"row": i, "prediction": p} for i, p in enumerate(shown)], **_ids(d))


def _models(d: PytorchInput) -> Any:
    found = kept.listed()
    return answer("models", f"{len(found)} model(s) kept (at most {kept.MAX_KEPT})", card=DESCRIBED, models=found,
                  evidence=[{"kind": "nn_models", "kept": len(found), "folder": "/".join(kept.FOLDER)}], **_ids(d))


def _forget(d: PytorchInput) -> Any:
    done = kept.forget(d.model_id)
    if not done["gone"]:
        raise MLError(OP_FAILED, f"kept model {d.model_id}'s files are still there after they were deleted")
    return answer("forget", f"kept model {d.model_id} forgotten: its weights and record were deleted from G6_HOME and "
                            f"are gone; {done['remaining']} model(s) remain (at most {kept.MAX_KEPT})", card=DESCRIBED,
                  forgotten=d.model_id, evidence=[{"kind": "nn_forget", **done,
                                                   "folder": "/".join(kept.FOLDER)}], **_ids(d))


def _layers(d: PytorchInput) -> Any:
    missing = missing_extra(*EXTRA)
    return answer("layers", f"{len(layers.LAYERS)} layer types built; "
                            + ("PyTorch is installed" if not missing else missing), card=DESCRIBED,
                  **layers.describe(), evidence=[{"kind": "nn_layers", "installed": not missing}], **_ids(d))


def _run(d: PytorchInput) -> Any:
    try:
        if d.op == "train_eval":
            return _train_eval(d)
        if d.op == "predict":
            return _predict(d)
        if d.op == "models":
            return _models(d)
        if d.op == "forget":
            return _forget(d)
        return _layers(d)
    except MLError as exc:
        return refuse(d.op, exc.code, exc.reason, evidence=exc.evidence, **_ids(d))


@block_contract(
    purpose="train a small neural network from G6's curated layer list on a held-out split the extension makes "
            "itself, out of G6's process, seeded, stopping on a non-finite loss, and score it in G6's process "
            "against a baseline on the same held-out rows",
    mitigates_failure_modes=("training_loop_bug",),
    verification_method="held-out rows the training process never sees the answers for, scored by G6's own code "
                        "against a baseline with seeded 95% intervals; a non-finite loss stops training",
)
@component_maturity(
    "beta",
    reason="trains out of G6's process under a deadline; scores computed by G6 from held-out answers the training "
           "never saw; qualified-draft at best",
)
class AdaptPytorchBlock(AIBlock[PytorchInput, dict, dict]):
    """Train, keep and use small networks; score them on held-out rows. Never ``verified``."""

    def __init__(self, name: str = "adapt_pytorch", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: PytorchInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        try:
            result = _run(data)
        except Exception as exc:  # noqa: BLE001 - an error is a refusal with its reason, never a crash or a score
            result = refuse(data.op, OP_FAILED, f"{data.op} failed: {type(exc).__name__}: {exc}"[:500], **_ids(data))
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


__all__ = ["SENSITIVE_OPS", "AdaptPytorchBlock", "PytorchInput"]
