# SPDX-License-Identifier: Apache-2.0
"""Check a dogfooding log against the beta bar: python -m validation.dogfood.check log.jsonl

Exit 0 when every bar is met, 1 when a bar is not met, 2 when the input is invalid.
This is n=1 dogfooding, not a study.
"""
from __future__ import annotations

import sys
from pathlib import Path

from pydantic import ValidationError

from validation.dogfood.schema import Entry

MIN_TASKS = 10
TOP_N = 3
MIN_HITS = 7
MIN_SHARE = 0.70


def _first_rank(entry: Entry) -> int | None:
    """The task's rank: the first call, in order, that recorded one."""
    return next((c.rank for c in entry.calls if c.rank is not None), None)


def load(path: Path) -> list[Entry]:
    entries: list[Entry] = []
    seen: set[str] = set()
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            entry = Entry.model_validate_json(line, strict=True)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first["loc"])
            raise ValueError(f"line {n}: {where}: {first['msg']}") from None
        if entry.task_id in seen:
            raise ValueError(f"line {n}: task_id {entry.task_id!r} appears twice")
        seen.add(entry.task_id)
        entries.append(entry)
    return entries


def evaluate(entries: list[Entry]) -> tuple[bool, list[str]]:
    total = len(entries)
    hits = sum(1 for e in entries if (r := _first_rank(e)) is not None and r <= TOP_N)
    absent = [e.task_id for e in entries if e.reroute_to_absent_tool]
    unasked = [e.task_id for e in entries if not e.overrides_asked_person]
    share = hits / total if total else 0.0
    bars = [
        (total >= MIN_TASKS, f"tasks logged: {total} (need at least {MIN_TASKS})"),
        (
            hits >= MIN_HITS and share >= MIN_SHARE,
            f"action in top {TOP_N}: {hits} of {total}, {share:.0%} "
            f"(need at least {MIN_HITS} and {MIN_SHARE:.0%}, over all tasks)",
        ),
        (not absent, f"reroutes to an absent tool or op: {len(absent)} (need 0)"
         + (f" in {', '.join(absent)}" if absent else "")),
        (not unasked, f"overrides that did not ask the person: {len(unasked)} (need 0)"
         + (f" in {', '.join(unasked)}" if unasked else "")),
    ]
    lines = [f"[{'MET' if ok else 'NOT MET'}] {text}" for ok, text in bars]
    return all(ok for ok, _ in bars), lines


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m validation.dogfood.check log.jsonl")
        return 2
    try:
        entries = load(Path(args[0]))
    except (OSError, ValueError) as exc:
        print(f"invalid input: {exc}")
        return 2
    ok, lines = evaluate(entries)
    print("Dogfooding log (n=1 dogfooding, not a study)")
    print("\n".join(lines))
    print("Bar met." if ok else "Bar NOT MET.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
