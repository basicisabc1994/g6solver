# SPDX-License-Identifier: Apache-2.0
"""What a measurement row froze, read and compared before any result is scored.

A row's protocol file holds one JSON block: its thresholds and, when the row has labels, the label file and its
sha256. Each harness pins the digest of its row's thresholds in its own code. :func:`check` reads the block, and
refuses (``SystemExit``) when the thresholds are not the pinned ones or the label file is not the one the protocol
pins; what it returns goes into every result, so a result names the thresholds and labels it was scored against.
:func:`problem` is the same comparison as a reason, for a caller that reports instead of exiting. A measure is a
finite number of its type before any bar is applied (:func:`finite`): a missing value or ``NaN`` passes nothing.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def block(protocol: Path) -> dict[str, Any]:
    """The protocol file's JSON block."""
    text = Path(protocol).read_text(encoding="utf-8")
    return json.loads(text[text.index("```json") + 7:text.rindex("```")])


def digest(value: Any) -> str:
    """The sha256 of ``value`` as canonical JSON."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def problem(protocol: Path, thresholds_sha256: str, labels: Path | None = None) -> str:
    """Why the protocol's frozen data may not be used (``""``: it may): its thresholds are not the ones this harness
    pins, or the label file is not the one the protocol pins."""
    try:
        found = block(protocol)
        thresholds = found["thresholds"]
    except (OSError, ValueError, KeyError):
        return "the protocol's JSON block could not be read"
    if digest(thresholds) != thresholds_sha256:
        return ("the thresholds in the protocol file are not the frozen ones this harness pins "
                f"({digest(thresholds)[:12]} against {thresholds_sha256[:12]}); a changed threshold retires the protocol")
    if labels is not None:
        pinned = (found.get("labels") or {}).get("sha256")
        try:
            actual = hashlib.sha256(Path(labels).read_bytes()).hexdigest()
        except OSError:
            return "the label file could not be read"
        if not pinned or actual != pinned:
            return f"the label file is not the frozen one the protocol pins ({actual[:12]} against {str(pinned)[:12]})"
    return ""


def check(protocol: Path, thresholds_sha256: str, labels: Path | None = None) -> dict[str, Any]:
    """The row's frozen data for a result to record, or ``SystemExit`` with why it may not be used."""
    why = problem(protocol, thresholds_sha256, labels)
    if why:
        raise SystemExit(f"{why}; nothing was scored")
    found = block(protocol)
    return {"row": found.get("row", ""), "thresholds": found["thresholds"], "thresholds_sha256": thresholds_sha256,
            "labels_sha256": (found.get("labels") or {}).get("sha256", "")}


def finite(value: Any) -> bool:
    """Whether ``value`` is a measure a bar can be applied to: a number, not a truth value, not ``NaN``, not infinite."""
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def independent(labelled_by: Any) -> bool:
    """Whether a label set says it was written independently of what it scores. Anything else is a development
    cohort, and a development result admits nothing."""
    said = str(labelled_by or "").casefold()
    return "independent" in said and "not independent" not in said


__all__ = ["ROOT", "block", "check", "digest", "finite", "independent", "problem"]
