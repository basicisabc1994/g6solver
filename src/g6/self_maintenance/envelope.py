# SPDX-License-Identifier: Apache-2.0
"""The loop's records and what halts it (design 6.1, 6.10), which know nothing of what the work is.

``CycleLog`` is the constraint log, a local hash-chained file for now: every cycle that ran, what limited it and what
came of it, plus each enrolment, pause, resumption and start. Only a person can enrol a domain, resume a paused loop or
start the runner, so the loop cannot lift its own pause. A ``STOP`` file in the loop's folder halts it at its next tick and
removing the file resumes it; a stop that cannot be read still stops. A runner ended by a stop needs a person's start
again, even with the file gone: the records say the last start was stopped. A heartbeat is written whole or not at all, and
a heartbeat that cannot be written is one missed, never a failed tick.

**Is a runner alive?** A runner holds an operating-system lock for as long as it lives, so one that crashed, or a machine
that rebooted, never reads as alive, and no process number can be mistaken for it. Looking writes nothing.

**The failed streak.** Three failed cycles in a row pause the loop. A failed cycle is a fault: an error, a sealed breach
or a step that was blocked. The loop seeks failure and learns from it, so a candidate that lost is a result: no winner, no
constraint, a refusal and a rolled-back adoption each end a streak and never pause. What bounds them is the held-out
split, which allows five evaluations before a person acts, and the runner, which rests when nothing is left to try.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ContextManager

from g6.core.adaptation_gate import is_automated
from g6.core.ledger import ChainLedger, LedgerError
from g6.workspace._coordination import Coordination
from g6.workspace._envelope import WorkspaceRefused

STOP_FILE = "STOP"
HEARTBEAT_FILE = "heartbeat.json"
CYCLE_LOG = Path("records", "constraint_log.jsonl")
#: The folder of the lock a runner holds for its whole life.
RUNNER_LOCK = ".runner"
MAX_FAILED_CYCLES = 3
#: Cycles that broke. A wait (a stop, the budget, a pause) is not a cycle at all, so it never counts.
FAILED = ("blocked", "error", "sealed_breach")
#: Cycles that ran and reached a result, whichever way it went.
ENDS_A_STREAK = ("adopted", "idle", "refused", "no_winner", "no_constraint", "rolled_back")
#: Results that found nothing to try, which the runner rests on: a refusal or a rollback did try something.
NOTHING_TO_TRY = ("idle", "no_winner", "no_constraint")
#: What a cycle keeps of what a proposer found, a general research record: a regularity found in the failing cases, what
#: can be exploited, why it does not hold generally, and the earlier cycles it builds on. Empty when it supplied none.
FINDINGS = ("structure", "resource", "obstruction", "composed_from")


def _rules(kind: str, ident: str, body: dict, seen: Mapping[tuple[str, str], dict]) -> list[str]:
    if kind == "cycle":
        known = body.get("outcome") in (*FAILED, *ENDS_A_STREAK)
        return [] if known else [f"cycle: unknown outcome {body.get('outcome')!r}"]
    if kind in ("paused", "stopped"):
        return [] if body.get("reason") else [f"a row of kind {kind} states its reason"]
    if kind in ("enrolled", "resumed", "started"):
        who = str(body.get("by") or "")
        return [] if who.strip() and not is_automated(who) else [f"{kind}: a person must do this (no empty or auto: name)"]
    return [f"unknown kind {kind!r}"]


class CycleLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._chain = ChainLedger(self.path, validate=_rules)

    def reload(self) -> None:
        """Read the file again: another handle may have written it. Its earlier entries must be unchanged."""
        self._chain.reload()

    def _rows(self) -> list[tuple[str, dict]]:
        return [(e["kind"], e["body"]) for e in self._chain.entries()]

    def _append(self, kind: str, body: dict) -> dict:
        self._chain.append(kind, str(len(self._rows()) + 1), body)
        return body

    def cycles(self) -> list[dict]:
        return [body for kind, body in self._rows() if kind == "cycle"]

    def cycle(self, row: Mapping[str, Any]) -> dict:
        empty = {"structure": "", "resource": "", "obstruction": "", "composed_from": []}
        return self._append("cycle", {"tick": len(self.cycles()) + 1, **empty, **row})

    def generation(self) -> int:
        """The latest generation a cycle made. Only an adoption or a rollback makes one: any other cycle's ``head_version``
        is the head it began on, which can be a version of a person's."""
        return max((c["head_version"] for c in self.cycles() if c["outcome"] in ("adopted", "rolled_back")), default=0)

    def findings(self, last: int = 8) -> list[dict]:
        """The latest cycles that recorded a finding, each with its tick and what was found: what a next tick reads."""
        found = [c for c in self.cycles() if any(c.get(name) for name in FINDINGS)]
        return [{"tick": c["tick"], **{name: c[name] for name in FINDINGS}} for c in found[-last:]]

    def enrol(self, by: str, frozen: Mapping[str, str], **fields: Any) -> dict:
        return self._append("enrolled", {"by": by, "frozen": dict(frozen), **fields})

    def enrolments(self) -> list[dict]:
        return [body for kind, body in self._rows() if kind == "enrolled"]

    def enrolled(self) -> dict | None:
        rows = self.enrolments()
        return rows[-1] if rows else None

    def pause(self, reason: str) -> dict:
        return self._append("paused", {"reason": reason})

    def resume(self, by: str) -> dict:
        return self._append("resumed", {"by": by})

    def start(self, by: str, **fields: Any) -> dict:
        return self._append("started", {"by": by, **fields})

    def started(self) -> dict | None:
        """The last time a person started the runner."""
        return next((body for kind, body in reversed(self._rows()) if kind == "started"), None)

    def stop(self, reason: str, **fields: Any) -> dict:
        return self._append("stopped", {"reason": reason, **fields})

    def start_in_force(self) -> bool:
        """Whether a person's start stands: of the starts and stops on record, the last is a start."""
        return next((kind == "started" for kind, _ in reversed(self._rows()) if kind in ("started", "stopped")), False)

    def paused(self) -> str:
        """Why the loop is paused, or ``""`` when it is not."""
        reason = ""
        for kind, body in self._rows():
            reason = body["reason"] if kind == "paused" else "" if kind == "resumed" else reason
        return reason

    def since_resumed(self) -> list[dict]:
        """The cycles since a person last lifted a pause."""
        rows = self._rows()
        start = max((i for i, (kind, _) in enumerate(rows) if kind == "resumed"), default=-1) + 1
        return [body for kind, body in rows[start:] if kind == "cycle"]

    def failed_streak(self) -> int:
        streak = 0
        for cycle in reversed(self.since_resumed()):
            if cycle["outcome"] in ENDS_A_STREAK:
                break
            streak += 1
        return streak

    def quiet_streak(self) -> int:
        """How many cycles in a row found nothing to try, since a person last enrolled a domain or lifted a pause."""
        streak = 0
        for kind, body in reversed(self._rows()):
            if kind in ("enrolled", "resumed") or kind == "cycle" and body["outcome"] not in NOTHING_TO_TRY:
                break
            streak += kind == "cycle"
        return streak


def stop_reason(root: str | Path) -> str:
    """Why the ``STOP`` file in the loop's folder halts it, or ``""``. One that cannot be read still stops."""
    path = Path(root) / STOP_FILE
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError as exc:
        return f"a STOP file is present and could not be read ({type(exc).__name__})"
    return f"a STOP file is present: {text.splitlines()[0]}" if text else "a STOP file is present"


def file_digest(path: Any) -> str:
    """A file's SHA-256, or ``""`` when there is no file to read: what a person enrolled is pinned by its bytes."""
    try:
        return hashlib.sha256(Path(str(path)).read_bytes()).hexdigest() if path else ""
    except OSError:
        return ""


def ask_to_stop(root: str | Path, reason: str = "") -> str:
    """Write the ``STOP`` file, or leave one that is there, so the first reason stands and repeating is safe. It kills
    nothing: the runner reads the file between ticks and ends itself. It returns why the folder is stopped."""
    path = Path(root) / STOP_FILE
    if not path.exists():
        path.write_text((" ".join(reason.split()) or "stopped through g6_improve_stop") + "\n", encoding="utf-8")
    return stop_reason(root)


def record_stop(root: str | Path, reason: str, *, at: str) -> None:
    """Note a stop in the records, so a start no longer stands once the ``STOP`` file is gone. The file is what stops the
    loop, so this never gets in its way: records that do not read, or a tick holding them, leave the note for later, and
    a runner notes its own stop as it ends."""
    path = Path(root) / CYCLE_LOG
    if not path.is_file():
        return
    try:
        with Coordination(Path(root) / ".tick").mutex(wait=0):
            log = CycleLog(path)
            if log.start_in_force():
                log.stop(reason, at=at)
    except (OSError, LedgerError, WorkspaceRefused):
        pass


def runner_lock(root: str | Path) -> ContextManager[None]:
    """The lock a runner holds for its whole life; a second runner on the folder is refused it."""
    return Coordination(Path(root) / RUNNER_LOCK).mutex()


def runner_alive(root: str | Path) -> bool:
    """Whether a runner holds this folder. A folder no runner ever held has no lock to look at, so nothing is written."""
    if not (Path(root) / RUNNER_LOCK / "mutex").is_file():
        return False
    try:
        with Coordination(Path(root) / RUNNER_LOCK).mutex(wait=0):
            return False
    except WorkspaceRefused:
        return True


def beat(root: str | Path, **fields: Any) -> None:
    """Write the heartbeat: a reader sees the last whole one, never half of a new one. On Windows a reader holding the
    file can refuse the swap, which only misses this beat."""
    path = Path(root) / HEARTBEAT_FILE
    spare = path.with_suffix(".tmp")
    try:
        spare.write_text(json.dumps(fields, sort_keys=True, ensure_ascii=False), encoding="utf-8")
        os.replace(spare, path)
    except OSError:
        pass


def heartbeat(root: str | Path) -> dict | None:
    try:
        return json.loads((Path(root) / HEARTBEAT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


__all__ = ["CYCLE_LOG", "CycleLog", "ENDS_A_STREAK", "FAILED", "FINDINGS", "MAX_FAILED_CYCLES", "NOTHING_TO_TRY",
           "RUNNER_LOCK", "STOP_FILE", "ask_to_stop", "beat", "file_digest", "heartbeat", "record_stop", "runner_alive",
           "runner_lock", "stop_reason"]
