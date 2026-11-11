# SPDX-License-Identifier: Apache-2.0
"""Frozen measurement files: one canonical digest, a write that refuses to change a frozen file, and retirement.

A bar, a task list or a policy tuned after its run is the failure every measured row of the 0.3 design guards against
(sections 5 and 14.4). Each row's manifest keeps its own fields; what they share is here: the digest of a file is the
digest of its parsed JSON in canonical form (so spacing is not identity), ``freeze`` writes a file once and refuses a
second, different one, and ``retire`` keeps a replaced file beside its successor with the reason.

Standard library only. A validation script loads this file by path (``validation`` is not a package).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class FrozenError(ValueError):
    """A frozen file would change, or does not match the digest that binds it."""


def canonical(value: Any) -> bytes:
    """The one byte form a value is hashed in: sorted keys, no spacing, UTF-8, no NaN."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def read(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def file_digest(path: Path) -> str:
    """The digest of the JSON a file holds."""
    return digest(read(path))


def render(value: Any) -> str:
    """How a frozen file is written: indented, keys in the order given, a final newline."""
    return json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def freeze(path: Path, value: Any) -> str:
    """Write ``value`` at ``path`` and answer its digest. A file already there with other content is never
    overwritten: retire it first, with the reason."""
    path = Path(path)
    if path.exists():
        if file_digest(path) != digest(value):
            raise FrozenError(f"{path.name} is frozen with other content; retire it, with the reason, before a new one")
        return digest(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(value), encoding="utf-8", newline="\n")
    return digest(value)


def bound(path: Path, expected: str, *, what: str = "") -> Any:
    """The JSON at ``path`` when its digest is ``expected``; ``FrozenError`` when the file changed or is absent."""
    path = Path(path)
    if not path.is_file():
        raise FrozenError(f"{what or path.name}: {path.name} is absent")
    value = read(path)
    found = digest(value)
    if found != expected:
        raise FrozenError(f"{what or path.name}: {path.name} has digest {found}, and {expected} is the one bound")
    return value


def retire(path: Path, reason: str) -> Path:
    """Move a frozen file aside as ``<name>.retired-<digest prefix>.json`` with the reason beside it, and answer where
    it went. Nothing is deleted: a retired gate stays readable next to the one that replaced it."""
    path = Path(path)
    if len(reason.strip()) < 20:
        raise FrozenError("a retirement gives its reason in at least 20 characters")
    value = read(path)
    kept = path.with_name(f"{path.stem}.retired-{digest(value)[:12]}{path.suffix}")
    if kept.exists():
        raise FrozenError(f"{kept.name} already exists")
    kept.write_text(render({"retired": value, "reason": reason.strip(), "digest": digest(value)}), encoding="utf-8",
                    newline="\n")
    path.unlink()
    return kept
