# SPDX-License-Identifier: Apache-2.0
"""The crisis and helpline directory: numbers a held page prints, each with its source, retrieval date, the SHA-256 of
the retrieved page and the exact clause (``data/directory.json``; the sources are in ``data/source_register.json``).

:func:`lookup` gives a region's entries (every region's when none is stated), emergency numbers first, each with its
age in days, and the directory's own age. Past :data:`STALE_AFTER_DAYS` (G6's own choice) it says plainly that the
numbers may have changed. A region the directory does not cover is told so and is never handed another country's line
as its own (JOB_PSYCHOLOGIST-02). Nothing here is labelled verified: the numbers are as printed on the day retrieved.
"""
from __future__ import annotations

import datetime as dt
import json
from importlib import resources
from typing import Any

#: Past this many days since retrieval the directory says the numbers may have changed. G6's own choice: no source
#: sets it.
STALE_AFTER_DAYS = 180
_LOCAL = "If anyone is in danger right now, call the local emergency number."


def load() -> dict[str, Any]:
    """The committed directory, read as package data."""
    text = resources.files("g6.jobs._care").joinpath("data/directory.json").read_text(encoding="utf-8")
    return json.loads(text)


def _region(data: dict[str, Any], region: str | None) -> str | None:
    if region is None or not region.strip():
        return None
    code = region.strip().upper()
    return data.get("aliases", {}).get(code, code)


def emergency_first(entry: dict[str, Any]) -> bool:
    """emergency_first: True for an entry that goes before every other, an emergency number, as the government
    directory lists it first. :func:`lookup` sorts by it, and the crisis response opens with the entry it picks."""
    return entry["kind"] == "emergency"


def too_old(age_days: int) -> bool:
    """directory_age: True past :data:`STALE_AFTER_DAYS` since retrieval, when the numbers may have changed."""
    return age_days > STALE_AFTER_DAYS


def lookup(region: str | None = None, *, today: dt.date | None = None) -> dict[str, Any]:
    """The entries for ``region`` (all regions when it is ``None``), emergency numbers first, each with ``age_days``;
    ``age_days`` and ``stale`` for the directory as a whole, and a ``note`` in plain words."""
    data = load()
    today = today or dt.datetime.now(dt.timezone.utc).date()
    code = _region(data, region)
    covered = None if code is None else code in data["regions"]
    chosen = [e for e in data["entries"] if code is None or not covered or e["region"] == code]
    chosen.sort(key=lambda e: not emergency_first(e))  # stable: emergency numbers first, the rest as listed
    entries = [{**e, "age_days": (today - dt.date.fromisoformat(e["retrieved"])).days} for e in chosen]
    age = max((e["age_days"] for e in entries), default=0)
    stale = bool(too_old(age))
    notes = []
    if covered is False:
        notes.append(f"This directory does not cover {code}: the entries below are for other regions and are not "
                     f"{code}'s own lines. {_LOCAL}")
    elif code is None or not any(e["kind"] == "emergency" for e in entries):
        notes.append(f"{_LOCAL} No region was stated, so nobody's own emergency number is known."
                     if code is None else _LOCAL)
    notes.append(f"The numbers were retrieved on {min((e['retrieved'] for e in entries), default='an unknown date')} "
                 f"and are {age} days old.")
    if stale:
        notes.append(f"That is more than {STALE_AFTER_DAYS} days: the numbers may have changed. Check each one with "
                     "its service before relying on it.")
    return {"region": code, "region_covered": covered, "entries": entries, "age_days": age, "stale": stale,
            "stale_after_days": STALE_AFTER_DAYS, "stale_after_source": data.get("stale_after_source", "g6"),
            "note": " ".join(notes)}


__all__ = ["STALE_AFTER_DAYS", "emergency_first", "load", "lookup", "too_old"]
