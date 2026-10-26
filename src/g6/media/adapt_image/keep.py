# SPDX-License-Identifier: Apache-2.0
"""Keep a picture: write its bytes into G6's media folder, inspect the file from disk, and return the inspected artifact
or the refusal (with the file removed). Every op of adapt_image that makes a picture, and every paid backend, ends
here."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from g6.media._inspect import inspect_image
from g6.media._types import (
    MediaError,
    MediaResult,
    Provenance,
    answer,
    discard,
    media_artifact,
    media_file,
    refuse,
    write_media,
)

#: Each format a picture may be written in: the file suffix and the name Pillow reads it by.
FORMATS = {"png": (".png", "PNG"), "jpeg": (".jpg", "JPEG"), "webp": (".webp", "WEBP")}


def digest(recipe: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def keep(operation: str, data: bytes, *, name: str, fmt: str, width: int | None, height: int | None,
         overwrite: bool, provenance: Provenance, ids: dict[str, str], message: str,
         evidence: list[dict[str, Any]] | None = None, **fields: Any) -> MediaResult:
    """``data`` as the file ``name`` (with ``fmt``'s suffix) in the media folder, inspected against ``width``,
    ``height`` (``None``: not compared) and ``fmt``; the artifact, or the refusal with the failed check's code."""
    suffix, pillow_name = FORMATS[fmt]
    try:
        path = media_file(name, suffix)
        written = write_media(path, data, overwrite=overwrite)
    except MediaError as exc:
        return refuse(operation, exc.code, exc.reason, evidence=evidence, **ids)
    found = inspect_image(path, width=width, height=height, formats=(pillow_name,))
    if not found.passed:
        removed = discard(path)
        return refuse(operation, found.failure.code, found.failure.detail,
                      evidence=[found.evidence(), *(evidence or []), {"kind": "file", "removed": removed}], **ids)
    requested = {k: v for k, v in (("width", width), ("height", height), ("format", fmt)) if v is not None}
    artifact = media_artifact(path, found, provenance, requested)
    return answer(operation, message, artifact=artifact, evidence=evidence, written=written, **fields, **ids)


__all__ = ["FORMATS", "digest", "keep"]
