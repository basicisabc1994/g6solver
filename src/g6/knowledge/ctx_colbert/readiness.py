# SPDX-License-Identifier: Apache-2.0
"""What a persistent ColBERT store is pinned to, and whether this machine can run it (design 12.3).

A store's vectors mean something only under the exact model that made them. :class:`ModelPin` names it: the model
and its revision, the hashes of its tokenizer, configuration and weights, the embedding dimension, the truncation,
the precision, the chunker and the store's schema. A store built under one pin is never read under another.

:func:`ready` says ``""`` when the pinned files are in the local cache with the pinned hashes, the platform is one
the measured envelope covers and the declared memory limit can be met; otherwise the reason. It loads nothing and
downloads nothing: a missing file, a hash that differs, an unsupported platform or too little memory is
``unavailable`` with its reason, never an empty result.
"""
from __future__ import annotations

import hashlib
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

#: Platforms the scale row has measured the store on. Empty until the spike's result is published (protocol 05):
#: nothing is called supported before it is measured.
MEASURED_PLATFORMS: frozenset[str] = frozenset()


class ModelPin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    revision: str
    tokenizer_sha256: str
    config_sha256: str
    weights_sha256: str
    dimension: int
    max_tokens: int
    precision: Literal["float16", "float32"]
    chunker: str
    schema_version: str   # the store's schema (named so, since pydantic models have a schema attribute of their own)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


def ready(pin: ModelPin, *, files: Mapping[str, Path] | None = None, platform: str | None = None,
          free_bytes: int | None = None, max_ram_bytes: int | None = None,
          measured: frozenset[str] = MEASURED_PLATFORMS) -> str:
    """``""`` when the pinned model can run here, otherwise why not. ``files`` maps ``"tokenizer"``, ``"config"``
    and ``"weights"`` to their paths in the local cache (``None``: not located, which is not ready)."""
    if files is None:
        return "the pinned model's files were not located in the local cache; nothing is downloaded"
    for role, wanted in (("tokenizer", pin.tokenizer_sha256), ("config", pin.config_sha256),
                         ("weights", pin.weights_sha256)):
        path = files.get(role)
        if path is None:
            return f"the {role} file is missing from the local cache"
        found = file_sha256(Path(path))
        if found is None:
            return f"the {role} file is missing from the local cache"
        if found != wanted:
            return f"the {role} file does not have the pinned hash: another model or revision is cached"
    here = platform or sys.platform
    if here not in measured:
        return (f"the platform {here} is not one the scale row has measured the store on (protocol 05 is not run): "
                "unsupported until it is")
    if max_ram_bytes is not None:
        if type(free_bytes) is not int or free_bytes < 0:   # an unknown input is a reason, never "ready"
            return (f"a memory limit of {max_ram_bytes} bytes is declared and the free memory is not known; not "
                    "ready until it is measured")
        if free_bytes < max_ram_bytes:
            return f"{free_bytes} bytes of memory are free, under the declared limit of {max_ram_bytes}"
    return ""


__all__ = ["MEASURED_PLATFORMS", "ModelPin", "file_sha256", "ready"]
