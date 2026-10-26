# SPDX-License-Identifier: Apache-2.0
"""The models adapt_pytorch keeps: weights G6's child wrote, stored by G6 under ``G6_HOME`` and named by an id G6
issued.

A kept model is two files in ``state_path("ml", "adapt_pytorch", "models")``: ``<id>.pt`` (the weights, as torch saved
them) and ``<id>.json`` (the layers, the encoding, the task, the SHA-256 of the weights and the held-out score measured
when it was trained). Nothing a caller names is ever opened: a model is asked for by its 32-character id, and its
weights are passed to the child only when their hash is the one G6 recorded; the child reads them with
``torch.load(..., weights_only=True)``. At most :data:`MAX_KEPT` are kept, and none is ever removed to make room: a
full store refuses another (:func:`room`), and :func:`forget` removes one by its id when the person asks.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

from g6.core.storage import state_path
from g6.ml._types import INPUT_INVALID, TOO_LARGE, UNTRUSTED_CODE, MLError

FOLDER = ("ml", "adapt_pytorch", "models")
MAX_KEPT = 20
_ID = re.compile(r"[0-9a-f]{32}")


def _folder() -> Path:
    return state_path(*FOLDER)


def room() -> None:
    """Refuse with ``G6_E_ML_TOO_LARGE`` when :data:`MAX_KEPT` models are kept already: none is ever removed to make
    room for another."""
    count = len(listed())
    if count >= MAX_KEPT:
        raise MLError(TOO_LARGE, f"{count} models are kept, at most {MAX_KEPT}, and none is ever removed to make room: "
                                 "remove one with op forget and its model_id (op models lists them), or train without "
                                 "keep")


def keep(weights: bytes, meta: dict[str, Any]) -> str:
    """Store ``weights`` and ``meta`` and return the new id, or refuse when the store is full (:func:`room`)."""
    room()
    folder = _folder()
    folder.mkdir(parents=True, exist_ok=True)
    model_id = uuid.uuid4().hex
    (folder / f"{model_id}.pt").write_bytes(weights)
    record = {**meta, "model_id": model_id, "weights_sha256": hashlib.sha256(weights).hexdigest(),
              "kept_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="microseconds")}
    (folder / f"{model_id}.json").write_text(json.dumps(record), encoding="utf-8")
    return model_id


def _files(model_id: str) -> tuple[Path, Path]:
    """A kept model's record and weights files, or a refusal: an id G6 did not issue or no longer keeps."""
    if not _ID.fullmatch(model_id or ""):
        raise MLError(INPUT_INVALID, "model_id must be the 32-character id train_eval returned with keep (op models "
                                     "lists them); a path or a file is never opened")
    folder = _folder()
    meta_path, weights_path = folder / f"{model_id}.json", folder / f"{model_id}.pt"
    if not meta_path.is_file() or not weights_path.is_file():
        raise MLError(INPUT_INVALID, f"no kept model {model_id} (op models lists them; at most {MAX_KEPT} are kept)")
    return meta_path, weights_path


def forget(model_id: str) -> dict[str, Any]:
    """Delete one kept model, its record and its weights, and check both files are gone."""
    paths = _files(model_id)
    for path in paths:
        path.unlink()
    return {"model_id": model_id, "deleted": [p.name for p in paths], "gone": not any(p.exists() for p in paths),
            "remaining": len(listed())}


def load(model_id: str) -> tuple[bytes, dict[str, Any]]:
    """A kept model's weights and record, or a refusal: an id G6 did not issue, or weights changed since G6 wrote
    them."""
    meta_path, weights_path = _files(model_id)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    weights = weights_path.read_bytes()
    if hashlib.sha256(weights).hexdigest() != meta.get("weights_sha256"):
        raise MLError(UNTRUSTED_CODE, f"the weights of {model_id} are not the ones G6 wrote (their SHA-256 differs "
                                      "from its record), so they are never loaded")
    return weights, meta


def listed() -> list[dict[str, Any]]:
    """Every kept model's record, newest first, without its encoding."""
    folder = _folder()
    if not folder.is_dir():
        return []
    out = []
    for path in folder.glob("*.json"):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({k: meta.get(k) for k in ("model_id", "kept_at", "task", "features", "layers", "headline",
                                              "rows_sha256")})
    return sorted(out, key=lambda r: (r["kept_at"] or "", r["model_id"]), reverse=True)


__all__ = ["FOLDER", "MAX_KEPT", "forget", "keep", "listed", "load", "room"]
