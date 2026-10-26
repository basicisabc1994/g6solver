# SPDX-License-Identifier: Apache-2.0
"""Bytes the family keeps for a later stage: a fetched PDF waiting for the converter, under
``$G6_HOME/knowledge/fetched/<sha256>``. ``_``-prefixed, so it never registers as a component.

A kept body is named by the SHA-256 of its bytes, written once and atomically, and read back only when its bytes still
have that hash: :func:`kept` returns ``None`` for one that is not there and raises :class:`KeptError` for one whose
bytes changed, never other bytes under that name. Bytes kept from a fetch also keep where and when they were fetched
(``<sha256>.json`` beside them: the address after redirects, the address asked for, and the time), so a later stage
dates and addresses them by their fetch, never by an address a caller names (:func:`origins`).
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from g6.core.storage import state_path
from g6.knowledge._types import instant, now, sha256_hex

_HEX = re.compile(r"[0-9a-f]{64}")
_FIELDS = ("url", "requested", "retrieved_at")


class KeptError(ValueError):
    """A name that is not a SHA-256, or kept bytes that no longer have their hash."""


class KeptOriginError(KeptError):
    """An address that is not where the kept bytes were fetched from."""


def kept_path(sha256: str) -> Path:
    """Where the bytes named ``sha256`` are kept."""
    if not _HEX.fullmatch(sha256):
        raise KeptError(f"{sha256!r} is not the SHA-256 of kept bytes (64 lower-case hex digits)")
    return state_path("knowledge", "fetched", sha256)


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix=".partial-", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as f:
            f.write(data)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def _origins_path(sha256: str) -> Path:
    path = kept_path(sha256)
    return path.with_name(path.name + ".json")


def keep(data: bytes, *, url: str = "", requested: str = "", retrieved_at: str = "") -> str:
    """Keep ``data`` and return its SHA-256, its name. With ``url`` (the address the bytes came from, after
    redirects), also record that fetch: ``requested`` is the address asked for (``url`` when not given) and
    ``retrieved_at`` when (now when not given)."""
    sha = sha256_hex(data)
    path = kept_path(sha)
    if not (path.is_file() and sha256_hex(path.read_bytes()) == sha):
        _write(path, data)
    if url:
        fetch = {"url": url, "requested": requested or url, "retrieved_at": retrieved_at or now()}
        try:
            earlier = origins(sha)
        except KeptError:
            earlier = []  # an unreadable record is replaced by this fetch, never added to
        known = [o for o in earlier if (o["url"], o["requested"]) != (fetch["url"], fetch["requested"])]
        _write(_origins_path(sha), json.dumps([*known, fetch], indent=1).encode("utf-8"))
    return sha


def origins(sha256: str) -> list[dict[str, str]]:
    """Where and when the bytes kept as ``sha256`` were fetched, the latest last; ``[]`` when no fetch is on record.
    Raises :class:`KeptError` for a record that cannot be read."""
    path = _origins_path(sha256)
    if not path.is_file():
        return []
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        found = None
    if not isinstance(found, list) or not all(
            isinstance(o, dict) and all(isinstance(o.get(k), str) and o[k] for k in _FIELDS)
            and instant(o["retrieved_at"]) is not None for o in found):
        raise KeptError(f"the record of where the bytes kept as {sha256} were fetched from cannot be read")
    return [{k: o[k] for k in _FIELDS} for o in found]


def kept(sha256: str) -> bytes | None:
    """The bytes kept as ``sha256``, ``None`` when none are; :class:`KeptError` when they no longer have that hash."""
    path = kept_path(sha256)
    if not path.is_file():
        return None
    data = path.read_bytes()
    if sha256_hex(data) != sha256:
        raise KeptError(f"the bytes kept as {sha256} were changed after they were kept")
    return data


__all__ = ["KeptError", "KeptOriginError", "keep", "kept", "kept_path", "origins"]