# SPDX-License-Identifier: Apache-2.0
"""G6's own fixed script for adapt_pytorch's child process: build the network from the resolved layer list, train it
or load G6's own kept weights, and write what it predicts. It runs only in the bounded child (``g6.ml._fit.run``) and
is the only code here that imports torch.

Training is seeded (``torch.manual_seed``, a seeded generator for the minibatch order, deterministic algorithms), in
minibatches, and stops the moment a loss is infinite or not a number. With patience on, the validation part of the
training rows decides when to stop and which epoch's weights are kept. After training every weight must be finite (the
last step can break them with no loss left to show it), and every output, a class's logits or a regression value,
must be finite before anything is decoded; either is written back as its own refusal. The child is never given a
held-out answer.
Kept weights are read back only with ``torch.load(..., weights_only=True)`` from the file G6 wrote beside the job.

The child is capped on its data segment, not its address space (:data:`MEMORY`, ``g6.core.sandbox.MEMORY_LIMITS``):
importing PyTorch's CUDA build maps libtorch and cuBLAS, far more address space than the family's cap, though little
of it is ever used. A torch that still cannot be loaded is written back as a named error, never a crash.
"""
from __future__ import annotations

import copy
import json
import math
import time
from pathlib import Path
from typing import Any

SCRIPT = "from g6.ml.adapt_pytorch.torch_child import child_main\nchild_main()\n"
JOB, RESULT, WEIGHTS = "job.json", "result.json", "model.pt"
#: What the family's memory cap (``g6.ml._fit.MEMORY_MB``) counts in this child on POSIX.
MEMORY = "data"
#: The oldest PyTorch the torch extra declares (tests/ml/test_pytorch.py reads pyproject.toml): before 2.6.0,
#: ``torch.load(weights_only=True)``, which reads kept weights back, could be made to run code (CVE-2025-32434).
TORCH_FLOOR = (2, 6)
#: Gradient norms below or above these are reported (the archive's thresholds; G6's own, a note only).
VANISHING, EXPLODING = 1e-7, 1e3


def network(torch: Any, layers: list[dict[str, Any]], inputs: int, outputs: int) -> Any:
    nn = torch.nn
    act = {"relu": nn.ReLU, "tanh": nn.Tanh, "sigmoid": nn.Sigmoid, "gelu": nn.GELU, "elu": nn.ELU,
           "leaky_relu": nn.LeakyReLU}
    parts, width = [], inputs
    for layer in layers:
        if layer["type"] == "dense":
            parts.append(nn.Linear(width, layer["units"]))
            width = layer["units"]
            if layer["activation"] != "linear":
                parts.append(act[layer["activation"]]())
        elif layer["type"] == "dropout":
            parts.append(nn.Dropout(layer["rate"]))
        else:
            raise ValueError(f"not a curated layer: {layer['type']!r}")
    parts.append(nn.Linear(width, outputs))
    return nn.Sequential(*parts)


def _norms(model: Any) -> dict[str, float]:
    return {name: float(p.grad.norm()) for name, p in model.named_parameters() if p.grad is not None}


def _predict(torch: Any, model: Any, rows: list[list[float]], classify: bool) -> tuple[list[Any], int]:
    """The model's predictions for ``rows``, and how many rows had an output (a logit, or the value) that is infinite
    or not a number: then nothing is decoded."""
    if not rows:
        return [], 0
    with torch.no_grad():
        out = model(torch.tensor(rows, dtype=torch.float32))
    broken = int((~torch.isfinite(out)).any(dim=1).sum())
    if broken:
        return [], broken
    if classify:
        return [int(v) for v in out.argmax(dim=1)], 0
    return [float(v) for v in out.squeeze(1)], 0


def _broken_output(part: str, rows: int, of: int, history: list[dict[str, Any]]) -> dict[str, Any]:
    return {"non_finite_output": {"part": part, "rows": rows, "of": of}, "history": history}


def _stop(epoch: int, batch: int, loss: float, history: list[dict[str, Any]]) -> dict[str, Any]:
    return {"non_finite": {"epoch": epoch, "batch": batch, "loss": str(loss)}, "history": history}


def train(job: dict[str, Any]) -> dict[str, Any]:
    import torch

    torch.manual_seed(job["seed"])
    torch.use_deterministic_algorithms(True)
    classify = job["task"] == "classification"
    X = torch.tensor(job["X_train"], dtype=torch.float32)
    y = torch.tensor(job["y_train"], dtype=torch.long if classify else torch.float32)
    valid = torch.tensor(job["valid"], dtype=torch.long)
    keep = torch.ones(len(X), dtype=torch.bool)
    keep[valid] = False
    fit = torch.arange(len(X))[keep]
    model = network(torch, job["layers"], X.shape[1], job["outputs"])
    loss_fn = torch.nn.CrossEntropyLoss() if classify else torch.nn.MSELoss()
    settings = {"lr": job["learning_rate"], "weight_decay": job["weight_decay"]}
    opt = {"adam": torch.optim.Adam, "adamw": torch.optim.AdamW,
           "sgd": lambda p, **kw: torch.optim.SGD(p, momentum=0.9, **kw)}[job["optimiser"]](model.parameters(), **settings)
    order_rng = torch.Generator().manual_seed(job["seed"])

    def loss_of(rows: Any) -> Any:
        out = model(X[rows])
        return loss_fn(out if classify else out.squeeze(1), y[rows])

    history: list[dict[str, Any]] = []
    first: dict[str, float] = {}
    best: tuple[float, Any, int] = (math.inf, None, 0)
    waited, stopped_early, last = 0, False, {}
    for epoch in range(1, job["epochs"] + 1):
        model.train()
        order = fit[torch.randperm(len(fit), generator=order_rng)]
        total = 0.0
        for batch, start in enumerate(range(0, len(order), job["batch_size"]), 1):
            rows = order[start:start + job["batch_size"]]
            opt.zero_grad()
            loss = loss_of(rows)
            if not torch.isfinite(loss):
                return _stop(epoch, batch, float(loss), history)
            loss.backward()
            last = _norms(model)
            first = first or last
            opt.step()
            total += float(loss) * len(rows)
        entry: dict[str, Any] = {"epoch": epoch, "train_loss": round(total / len(fit), 6)}
        if len(valid):
            model.eval()
            with torch.no_grad():
                held = float(loss_of(valid))
            if not math.isfinite(held):
                return _stop(epoch, 0, held, history)
            entry["valid_loss"] = round(held, 6)
            if held < best[0]:
                best, waited = (held, copy.deepcopy(model.state_dict()), epoch), 0
            else:
                waited += 1
        history.append(entry)
        if len(valid) and job["patience"] and waited >= job["patience"]:
            stopped_early = True
            break
    if best[1] is not None:
        model.load_state_dict(best[1])
    model.eval()
    broken = [name for name, p in model.named_parameters() if not bool(torch.isfinite(p).all())]
    if broken:
        return {"non_finite_weights": broken[:10], "history": history}
    out = {"history": history, "stopped_early": stopped_early, "best_epoch": best[2] or len(history),
           "gradient_norms": {"first_step": first, "last_step": last},
           "parameters": sum(p.numel() for p in model.parameters())}
    for part, rows in (("held_out", job["X_held_out"]), ("train", job["X_train"]), ("predict", job.get("X_predict"))):
        out[part], bad = _predict(torch, model, rows or [], classify)
        if bad:
            return _broken_output(part, bad, len(rows), history)
    if job.get("keep"):
        torch.save(model.state_dict(), WEIGHTS)
    return out


def predict(job: dict[str, Any]) -> dict[str, Any]:
    import torch

    model = network(torch, job["layers"], job["inputs"], job["outputs"])
    model.load_state_dict(torch.load(WEIGHTS, weights_only=True, map_location="cpu"), strict=True)
    model.eval()
    values, bad = _predict(torch, model, job["X_predict"], job["task"] == "classification")
    return _broken_output("predict", bad, len(job["X_predict"]), []) if bad else {"predict": values}


def child_main() -> None:
    """Read ``job.json`` from the working folder, train or predict, and write ``result.json``; an error is written as
    one."""
    import warnings

    job = json.loads(Path(JOB).read_text(encoding="utf-8"))
    started = time.monotonic()
    versions: dict[str, str] = {}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            import torch

            versions["torch"] = torch.__version__
        except Exception as exc:  # noqa: BLE001 - a torch the cap or the install stops is a named refusal
            result = {"error": f"torch could not be loaded in the bounded child (its memory cap is "
                               f"{int(job.get('memory_mb') or 0):,} MB): {type(exc).__name__}: {exc}"[:500]}
        else:
            try:
                result = train(job) if job["kind"] == "train" else predict(job)
            except Exception as exc:  # noqa: BLE001 - every failure goes back to the parent as a reason
                result = {"error": f"{type(exc).__name__}: {exc}"[:500]}

    result["seconds"] = round(time.monotonic() - started, 3)
    result["versions"] = versions
    result["warnings"] = sorted({f"{w.category.__name__}: {w.message}"[:200] for w in caught})[:10]
    Path(RESULT).write_text(json.dumps(result), encoding="utf-8")


__all__ = ["EXPLODING", "MEMORY", "SCRIPT", "TORCH_FLOOR", "VANISHING", "WEIGHTS", "child_main", "network", "predict",
           "train"]
