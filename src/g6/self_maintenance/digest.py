# SPDX-License-Identifier: Apache-2.0
"""The loop's daily digest: a Markdown file under its folder, one per day, for a person who reviews it daily.

For now this stands in for the weekly digest of design 6.9: it stays local and pushes nothing. It reads plain rows and
knows nothing of what the work is. A page is always under 60 lines, whatever the day held: each list keeps its newest
items and says how many it left out. Text a proposer or a domain wrote is flattened to one bounded line, so it can neither
break the page nor pose as one of its headings.
"""
from __future__ import annotations

import os
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

DIGEST_FOLDER = "digests"
#: The most lines a page may have.
MOST_LINES = 59
_LONGEST = 200
_WORDS = {"adopted": ("adopted", "adopted"), "idle": ("nothing to do", "with nothing to do"),
          "no_winner": ("no winner", "with no winner"), "no_constraint": ("no constraint named", "with no constraint named"),
          "refused": ("refused by the gate", "refused by the gate"), "rolled_back": ("rolled back", "rolled back"),
          "blocked": ("blocked", "blocked"), "error": ("an error", "with an error"),
          "sealed_breach": ("a sealed breach", "with a sealed breach")}


def _flat(text: Any) -> str:
    return " ".join(str(text).split())[:_LONGEST]


def _newest(lines: Sequence[str], most: int) -> list[str]:
    """The last ``most`` lines, newest first, and how many were left out."""
    shown = list(reversed(lines[-most:]))
    return [*shown, *([f"- and {len(lines) - most} more."] if len(lines) > most else [])]


def _ran(domain: str, cycles: Sequence[Mapping[str, Any]]) -> list[str]:
    if not cycles:
        return [f"- No tick ran today on {_flat(domain)}."]
    counted = Counter(c["outcome"] for c in cycles)
    said = ", ".join(f"{n} {_WORDS.get(outcome, (outcome, outcome))[1]}" for outcome, n in counted.most_common())
    return [f"- {len(cycles)} tick{'s' if len(cycles) != 1 else ''} on {_flat(domain)}: {said}."]


def _use(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    if not rows:
        return ["- No reading yet."]
    lines = [f"- {row['provider']}, day {row['day']} of 7: {row['used']:g} used, target {row['target']:g}, ceiling "
             f"{row['ceiling']:g}{' (estimate)' if row['estimated'] else ''}. Account: {_flat(row.get('account', 'not read'))}."
             for row in rows]
    return [*lines[:3], *([f"- and {len(lines) - 3} more."] if len(lines) > 3 else [])]


def _constraint(row: Mapping[str, Any] | None) -> list[str]:
    if row is None:
        return ["- None named yet."]
    return [f"- {_flat(row['constraint'])}, named at tick {row['tick']}."]


def _tried(cycles: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = [f"- Tick {c['tick']}, {_WORDS.get(c['outcome'], (c['outcome'],))[0]}: {_flat(c.get('reason') or 'no reason given')}"
             for c in cycles]
    return _newest(lines, 8) or ["- Nothing today."]


def _failures(cycles: Sequence[Mapping[str, Any]]) -> list[str]:
    causes: Counter[str] = Counter()
    for cycle in cycles:
        causes.update(cycle.get("failure_records") or {})
    if not causes:
        return ["- None today."]
    said = ", ".join(f"{_flat(cause)} {n}" for cause, n in causes.most_common(6))
    return [f"- {sum(causes.values())} read: {said}."]


def _finding(c: Mapping[str, Any]) -> str:
    parts = [f"{word}: {_flat(c[name])}" for word, name in (("regularity", "structure"), ("resource", "resource"),
                                                            ("obstruction", "obstruction")) if c.get(name)]
    built = c.get("composed_from")
    return f"- Tick {c['tick']}. " + "; ".join([*parts, *([f"builds on ticks {', '.join(map(str, built))}"] if built else [])])


def _findings(cycles: Sequence[Mapping[str, Any]]) -> list[str]:
    found = [_finding(c) for c in cycles if any(c.get(name) for name in ("structure", "resource", "obstruction",
                                                                         "composed_from"))]
    return _newest(found, 3) or ["- Nothing recorded today."]


def _generation(row: Mapping[str, Any]) -> str:
    kind = " (a rollback)" if row["kind"] == "rollback" else ""
    line = f"- Generation {row['generation']}, from {row['parent']}{kind}: {_flat(row.get('change') or 'no change recorded')}"
    measured = row.get("measured") or {}
    if measured.get("before") is None or measured.get("after") is None:
        return line
    split = str(measured.get("split", "")).replace("_", "-")
    seconds = (measured.get("not_gated") or {}).get("wall_clock_s")
    timed = (f"; wall clock per case {seconds['before']:.3g} s before, {seconds['after']:.3g} s after, reported and not gated"
             if seconds else "")
    return f"{line}; measured on {measured.get('cases')} {split} cases, {measured['before']:g} before, {measured['after']:g} after{timed}"


def _generations(found: Mapping[str, Any]) -> list[str]:
    recent = found.get("recent") or ()
    shown = [_generation(row) for row in recent[-5:]]
    return [f"- Now at generation {found.get('current', 0)}.", *reversed(shown)]


def _listed(items: Sequence[str]) -> list[str]:
    lines = [f"- {_flat(item)}" for item in items]
    return [*lines[:4], *([f"- and {len(lines) - 4} more."] if len(lines) > 4 else [])] or ["- None."]


def render(day: date, *, domain: str, status: str, cycles: Sequence[Mapping[str, Any]], use: Sequence[Mapping[str, Any]],
           constraint: Mapping[str, Any] | None, generations: Mapping[str, Any], decisions: Sequence[str],
           stops: Sequence[str]) -> str:
    """One day's page. ``cycles`` are that day's; ``constraint`` is the latest cycle that named one, whenever it ran."""
    sections = (("What ran", _ran(domain, cycles)), ("Use against the pacing rule", _use(use)),
                ("The current constraint", _constraint(constraint)), ("What was tried", _tried(cycles)),
                ("Failure records read", _failures(cycles)), ("What proposers found", _findings(cycles)),
                ("Generations", _generations(generations)), ("Decisions for the owner", _listed(decisions)),
                ("Stops and pauses", _listed(stops)))
    lines = [f"# G6 improvement loop: {day.isoformat()}", "", status]
    for heading, body in sections:
        lines += ["", f"## {heading}", *body]
    return "\n".join(lines[:MOST_LINES]) + "\n"


def digest_path(root: str | Path, day: date) -> Path:
    return Path(root) / DIGEST_FOLDER / f"{day.isoformat()}.md"


def write(root: str | Path, day: date, text: str) -> Path:
    """Write one day's page, whole or not at all: a later write of the same day replaces it."""
    path = digest_path(root, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    spare = path.with_suffix(".tmp")
    spare.write_text(text, encoding="utf-8")
    os.replace(spare, path)
    return path


def latest(root: str | Path) -> Path | None:
    """The newest day's page, or ``None`` before the first."""
    return max((Path(root) / DIGEST_FOLDER).glob("*.md"), default=None)


__all__ = ["DIGEST_FOLDER", "MOST_LINES", "digest_path", "latest", "render", "write"]
