# SPDX-License-Identifier: Apache-2.0
"""G6's repair log: ``repairs/repairs.jsonl`` under the state folder, append-only, codes and ids only (plan revision 8,
the repair loop).

``g6.mcp.repair`` writes one ``refused`` row for each repair block it adds to a reply, and one ``next`` row saying how
that repair ended: the next call of the same tool in the session was refused again (``not``) or not (``resolved``), or
the block sent the agent to the person (``person``). No argument and no word of text is ever written.
:func:`repair_stats` reads it (resolution rate by code) for the self-improvement loop; nothing reads it automatically
yet.
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

LOG = ("repairs", "repairs.jsonl")
#: The only fields a row may hold.
FIELDS = frozenset({"event", "id", "session", "tool", "code", "fix_by", "by", "outcome"})
_LOCK = threading.Lock()


@contextmanager
def _process_lock(path: Path):
    """All writers of this log share one OS lock, released if the process exits."""
    import os

    with path.open("a+b") as lock:
        if os.name == "nt":
            import msvcrt

            if lock.seek(0, 2) == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def append(row: dict[str, Any]) -> None:
    """Append one row of codes and ids (fields outside ``FIELDS`` are dropped); a failure to write is ignored, so the
    log never touches the call it records."""
    from g6.core.storage import state_path

    try:
        path = state_path(*LOG)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({k: v for k, v in row.items() if k in FIELDS}, sort_keys=True) + "\n"
        with _LOCK, _process_lock(path.with_suffix(".lock")):
            with path.open("a", encoding="utf-8", newline="\n") as out:
                out.write(line)
    except (OSError, ValueError):
        pass


def repair_stats(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Resolution by code: refused, resolved, not, person, and the rate resolved / (resolved + not), ``None`` before
    any repair the agent made has ended."""
    from g6.core.storage import state_path

    path = path or state_path(*LOG)
    out: dict[str, dict[str, Any]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get("code"), str):
                continue
            stats = out.setdefault(row["code"], {"refused": 0, "resolved": 0, "not": 0, "person": 0})
            if row.get("event") == "refused":
                stats["refused"] += 1
            elif row.get("event") == "next" and row.get("outcome") in ("resolved", "not", "person"):
                stats[row["outcome"]] += 1
        except Exception:  # noqa: BLE001 - one malformed row, including recursion errors, never stops the reader
            continue
    for stats in out.values():
        ended = stats["resolved"] + stats["not"]
        stats["rate"] = round(stats["resolved"] / ended, 3) if ended else None
    return dict(sorted(out.items()))


__all__ = ["FIELDS", "LOG", "append", "repair_stats"]
