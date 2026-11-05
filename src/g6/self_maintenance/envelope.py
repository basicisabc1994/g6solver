# SPDX-License-Identifier: Apache-2.0
"""The loop's records and what halts it (design 6.1, 6.10), which know nothing of what the work is.

``CycleLog`` is the constraint log, a local hash-chained file for now: every cycle that ran, what limited it and what
came of it, plus each enrolment, pause, resumption, start, generation a person took back and provider's limit. Only a
person can enrol a domain, resume a paused loop, take a generation back or start the runner, so the loop cannot lift its own
pause. A ``STOP`` file in the loop's folder halts it at its
next model call, and a stop that cannot be read still stops. Nothing but a person removes that file: a start records the file a
person agreed to lift, and anything else, or any later write, stands, so a stop has no moment in which it can be lost. A
runner ended by a stop needs a person's start again, even with the file gone: the records say the last start was stopped. A
heartbeat is written whole or not at all, and a heartbeat that cannot be written is one missed, never a failed tick.

**Is a stop in force?** One function says, ``stop_in_force``, and every reader acts on it: a start, the runner, a tick,
status and the digest. It reads, in one reading, the ``STOP`` file, the journal of stops and the records' last start with
any stop on record after it, which revokes that start until a person starts again. A stop through the tool is a line in
``stops.log``, written and flushed to disk before its ``STOP`` file, both under ``stop_lock``, which a start holds while
it reads the stop state and writes its row, so no start binds a line the disk may yet lose and a stop that dies between
the two writes still stands for every reader. A start binds the reading whole: the ``STOP`` file it lifts, and the bytes
of ``stops.log`` by digest, so bytes decide, a line cut short included. Anything that cannot be read or used, a file, a
records path that is not a file, a chain that does not check out, a start's lift fields of the wrong shape, is never a
value or a raised error: it is a stop in force, ``G6_E_LOOP_STOP_UNREADABLE``, for every reader alike.

**Is a runner alive?** A runner holds an operating-system lock for as long as it lives, so one that crashed, or a machine
that rebooted, never reads as alive, and no process number can be mistaken for it. Looking writes nothing.

**The failed streak.** Three failed cycles in a row pause the loop. A failed cycle is a fault: an error, a sealed breach
or a step that was blocked. The loop seeks failure and learns from it, so a candidate that lost is a result: no winner, no
constraint, a refusal and a rolled-back adoption each end a streak and never pause. What bounds them is the held-out
split, which allows five evaluations before a person acts, and the runner, which rests when nothing is left to try. A tick
cut off at its own reservation is blocked and on the record, and in no streak: the pacing rule is what bounds those.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, ContextManager
from uuid import uuid4

from g6.core.adaptation_gate import is_automated
from g6.core.ledger import ChainLedger, LedgerError
from g6.workspace._coordination import Coordination
from g6.workspace._envelope import WorkspaceRefused

STOP_FILE = "STOP"
STOPS_FILE = "stops.log"
#: The folder of the lock a stop's writer and a start's reading of the stop state share (``stop_lock``).
STOP_LOCK = ".stops"
#: The longest a stop waits for that lock, in seconds, before it writes without it: a start holds it for milliseconds.
STOP_WAIT = 1.0
#: A stop is in force, and says why.
STOPPED = "G6_E_LOOP_STOPPED"
#: A record that says whether a stop was asked for cannot be read, so a stop is in force: nothing can show it was lifted.
STOP_UNREADABLE = "G6_E_LOOP_STOP_UNREADABLE"
REFUSED_FILE = "refused.log"
DIGEST_LENGTH = 64                      # of a SHA-256, in hex
HEARTBEAT_FILE = "heartbeat.json"
BEAT_TRIES = 5                          # how often to try a swap a reader holds up
BEAT_GAP = 0.02                         # the seconds between those tries
CYCLE_LOG = Path("records", "constraint_log.jsonl")
#: The folder of the lock a runner holds for its whole life.
RUNNER_LOCK = ".runner"
MAX_FAILED_CYCLES = 3
#: Cycles that broke. A wait (a stop, the budget, a pause) is not a cycle at all, so it never counts.
FAILED = ("blocked", "error", "sealed_breach")
#: A cycle that was cut off at what its tick reserved, or at the end of the week it reserved in: blocked, and no fault.
TICK_BUDGET_SPENT = "G6_E_LOOP_TICK_BUDGET_SPENT"
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
    if kind in ("enrolled", "resumed", "started", "demoted"):
        who = str(body.get("by") or "")
        person = [] if who.strip() and not is_automated(who) else [f"{kind}: a person must do this (no empty or auto: name)"]
        return person + (_lift_problems(body) if kind == "started" else [])
    if kind == "limited":
        return [] if body.get("provider") and _on_a_clock(body.get("until")) else ["limited: states a provider and when it ends"]
    return [f"unknown kind {kind!r}"]


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _hex_digest(value: Any) -> bool:
    return isinstance(value, str) and (value == "" or len(value) == DIGEST_LENGTH and all(c in "0123456789abcdef" for c in value))


#: What a start records of the stop state it lifted, and the shape a reading of that state gives each field.
_LIFT_FIELDS = {"lifted": _hex_digest, "lifted_bytes": _whole, "stops": _whole, "stops_sha256": _hex_digest,
                "lifted_mtime_ns": lambda value: isinstance(value, int) and not isinstance(value, bool)}


def _lift_problems(start: Mapping[str, Any]) -> list[str]:
    """What is wrong with the fields a start records of the stop state it lifted: each one present is of the shape a reading
    gives it, or absent. Checked where the row is written and again where it is read, since no stop decision may stand on
    a field it cannot use."""
    return [f"started: {name} is not what a reading of the stop state gives ({type(start[name]).__name__})"
            for name, shape in _LIFT_FIELDS.items() if start.get(name) is not None and not shape(start[name])]


def _on_a_clock(text: Any) -> bool:
    """Whether ``text`` is a moment with a time zone, which is all a meter can compare with ``now``."""
    try:
        return datetime.fromisoformat(text).tzinfo is not None
    except (TypeError, ValueError):
        return False


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
        """The latest generation on record. Only an adoption, a rollback or a person's demotion makes one, and a demotion is a
        row of its own and no cycle: any other cycle's ``head_version`` is the head it began on, which can be a version of a
        person's."""
        return max((body.get("head_version", 0) for kind, body in self._rows()
                    if kind == "demoted" or kind == "cycle" and body["outcome"] in ("adopted", "rolled_back")), default=0)

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

    def demote(self, by: str, **fields: Any) -> dict:
        return self._append("demoted", {"by": by, **fields})

    def start(self, by: str, **fields: Any) -> dict:
        return self._append("started", {"by": by, **fields})

    def started(self) -> dict | None:
        """The last time a person started the runner."""
        return next((body for kind, body in reversed(self._rows()) if kind == "started"), None)

    def stop(self, reason: str, **fields: Any) -> dict:
        return self._append("stopped", {"reason": reason, **fields})

    def limit(self, provider: str, until: str) -> dict:
        return self._append("limited", {"provider": provider, "until": until})

    def limits(self) -> dict[str, datetime]:
        """When each provider's latest limit ends, so that a restart waits it out. No cycle: it never counts in a streak."""
        return {body["provider"]: datetime.fromisoformat(body["until"]) for kind, body in self._rows() if kind == "limited"}

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

    def failed_run(self) -> list[dict]:
        """The failed cycles in a row, oldest first. A tick cut off at its own reservation is not among them, and does not
        break them: it stopped where the loop told it to, which is no fault, and it compared nothing, which is no result."""
        found = []
        for cycle in reversed(self.since_resumed()):
            if cycle["outcome"] in ENDS_A_STREAK:
                break
            if cycle.get("code") != TICK_BUDGET_SPENT:
                found.append(cycle)
        return found[::-1]

    def failed_streak(self) -> int:
        return len(self.failed_run())

    def quiet_streak(self) -> int:
        """How many cycles in a row found nothing to try, since a person last enrolled a domain, lifted a pause or took a
        generation back."""
        streak = 0
        for kind, body in reversed(self._rows()):
            if kind in ("enrolled", "resumed", "demoted") or kind == "cycle" and body["outcome"] not in NOTHING_TO_TRY:
                break
            streak += kind == "cycle"
        return streak


class StopUnreadable(OSError):
    """A file that says whether a stop was asked for cannot be read. No reader takes it for no stop: ``stop_in_force``
    makes it a stop in force, ``STOP_UNREADABLE``."""


@dataclass(frozen=True)
class Stop:
    """Whether a stop is in force: ``code`` is ``STOPPED``, ``STOP_UNREADABLE`` or ``""`` for none, ``reason`` says why.
    ``start`` and ``revoked`` are what it was decided against: the last start on record, and why a stop on record ended
    it (``""`` when none did)."""

    code: str = ""
    reason: str = ""
    start: Mapping[str, Any] | None = field(default=None, compare=False)
    revoked: str = field(default="", compare=False)
    seen: StopSeen | None = field(default=None, compare=False)     # the reading of the stop's files it stood on

    def __bool__(self) -> bool:
        return bool(self.code)


@dataclass(frozen=True)
class StopSeen:
    """One reading of all that says whether a stop was asked for: the ``STOP`` file (``None`` with none), its modification
    time, taken before its bytes, and the journal of stops, ``stops.log`` (empty with none)."""

    stop: bytes | None = None
    stop_mtime_ns: int | None = None
    journal: bytes = b""

    def lift(self) -> dict[str, Any]:
        """The ``STOP`` file as it stands, for a start to record what a person agreed to lift; nothing with no file. No start
        moves or deletes the file: this record is all that lifting is."""
        if self.stop is None:
            return {}
        return {"lifted": hashlib.sha256(self.stop).hexdigest(), "lifted_bytes": len(self.stop),
                "lifted_mtime_ns": self.stop_mtime_ns}

    def binding(self) -> dict[str, Any]:
        """All a start records of this reading, and all a dialog's terms bind: the ``STOP`` file it lifts, and the journal
        by its count and its digest, so a journal rewritten to as many lines is not the one the start saw."""
        return {**self.lift(), "stops": self.journal.count(b"\n"), "stops_sha256": hashlib.sha256(self.journal).hexdigest()}

    def by_file(self, start: Mapping[str, Any] | None) -> str:
        """Why the ``STOP`` file halts the loop run on ``start``, or ``""``. It stands unless it is the very file that start
        lifted: the same digest, length and modification time, since bytes alone cannot say a person wrote again. What was
        added after those bytes is the stop in force, and any other change leaves the whole file standing."""
        if self.stop is None:
            return ""
        held, start = self.stop, start or {}
        lifted = start.get("lifted_bytes", 0)
        if start.get("lifted") and hashlib.sha256(held[:lifted]).hexdigest() == start["lifted"]:
            if len(held) > lifted:
                held = held[lifted:]
            elif self.stop_mtime_ns == start.get("lifted_mtime_ns"):
                return ""
        text = held.decode("utf-8", "replace").strip()
        return f"a STOP file is present: {text.splitlines()[0]}" if text else "a STOP file is present"

    def by_journal(self, start: Mapping[str, Any] | None) -> str:
        """Why the journal halts the loop run on ``start``: its bytes are not the ones that start bound, so a stop was asked
        for since, whether or not its ``STOP`` file was written. Bytes decide, never a count of lines, so a line a crash cut
        short is a stop like any other. With no start on record, any bytes in the journal are a stop no start saw. A start
        from before the digest bound only a count of lines: the journal must hold that many whole lines and nothing after
        them. A start that bound neither has nothing here to compare (the runner refuses such a start, ``runner._gate``)."""
        if start is None:
            changed = bool(self.journal)
        elif start.get("stops_sha256") is not None:
            changed = start["stops_sha256"] != hashlib.sha256(self.journal).hexdigest()
        elif start.get("stops") is not None:
            changed = start["stops"] != self.journal.count(b"\n") or not self.journal.endswith(b"\n") and bool(self.journal)
        else:
            changed = False
        if not changed:
            return ""
        return (f"a stop was asked for and its STOP file was not written: {STOPS_FILE} in the folder says why, and is not "
                "what the last start saw")

    def against(self, start: Mapping[str, Any] | None) -> str:
        """Why a stop is in force for the loop run on ``start``, or ``""``: the ``STOP`` file speaks first."""
        return self.by_file(start) or self.by_journal(start)


def _read_stop(root: str | Path) -> tuple[bytes | None, int | None]:
    path = Path(root) / STOP_FILE
    try:
        written = path.stat().st_mtime_ns     # before the read: a write between them leaves an old time, so the stop stands
        return path.read_bytes(), written
    except (FileNotFoundError, NotADirectoryError):
        return None, None
    except OSError as exc:
        raise StopUnreadable(f"the {STOP_FILE} file cannot be read ({type(exc).__name__})") from exc


def _read_journal(root: str | Path) -> bytes:
    try:
        return (Path(root) / STOPS_FILE).read_bytes()
    except (FileNotFoundError, NotADirectoryError):
        return b""
    except OSError as exc:
        raise StopUnreadable(f"{STOPS_FILE} cannot be read ({type(exc).__name__})") from exc


def _on_record(root: str | Path) -> tuple[dict | None, str]:
    """The last start on record, and why a stop on record ended it (``""`` when none did): of the starts and stops in the
    records, a stop after the last start revokes it until a person starts again. Records that cannot be read or trusted,
    a records path that is not a file, or a start whose lift fields no reading gives raise ``StopUnreadable``: absent is
    only a path with nothing at it."""
    path = Path(root) / CYCLE_LOG
    if not os.path.lexists(path):
        return None, ""
    if not path.is_file():
        raise StopUnreadable(f"the loop's records ({CYCLE_LOG.as_posix()}) are not a file and cannot be read")
    try:
        rows = CycleLog(path)._rows()
    except (OSError, LedgerError) as exc:
        raise StopUnreadable(f"the loop's records, which say what the last start lifted, cannot be read or cannot be trusted "
                             f"({type(exc).__name__})") from exc
    start, revoked = None, ""
    for kind, body in rows:
        if kind == "started":
            start, revoked = body, ""
        elif kind == "stopped" and start is not None:
            revoked = str(body.get("reason") or "a stop on record")
    if start is not None and _lift_problems(start):
        raise StopUnreadable(f"the last start's record of what it lifted is malformed: {_lift_problems(start)[0]}")
    return start, revoked


def records_identity(root: str | Path) -> str:
    """What identifies the records a stop decision reads, for a reader that keeps the start it read: the digest of the
    records' bytes, ``""`` with none. A change of any byte is a new identity. One that cannot be read raises
    ``StopUnreadable``."""
    path = Path(root) / CYCLE_LOG
    if not os.path.lexists(path):
        return ""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise StopUnreadable(f"the loop's records ({CYCLE_LOG.as_posix()}) cannot be read ({type(exc).__name__})") from exc


def stop_seen(root: str | Path) -> StopSeen:
    """One reading of the ``STOP`` file and the journal of stops. A file that cannot be read raises ``StopUnreadable``, so
    no reader can take it for no stop."""
    stop, written = _read_stop(root)
    return StopSeen(stop, written, _read_journal(root))


_ON_RECORD: Any = object()


def stop_in_force(root: str | Path, start: Mapping[str, Any] | None = _ON_RECORD, *, revoked: str = "") -> Stop:
    """Whether a stop is in force on this folder and why: the one answer a start, the runner, a tick, status and the digest
    all act on. It reads, in one reading, the ``STOP`` file, the journal, and the records' last start with any stop after
    it. ``start`` and ``revoked``: the start on record and its revocation as the caller holds them (``None``: no start);
    left out, they are read from the records. Anything that cannot be read or used, any of those files or a start's lift
    fields, is a stop in force, ``STOP_UNREADABLE``, never a value and never a raised error: nothing could show that a
    person lifted it."""
    try:
        seen = stop_seen(root)
        if start is _ON_RECORD:
            start, revoked = _on_record(root)
        elif start is not None and _lift_problems(start):
            raise StopUnreadable(f"the last start's record of what it lifted is malformed: {_lift_problems(start)[0]}")
    except StopUnreadable as exc:
        return Stop(STOP_UNREADABLE, f"{exc}, so a stop is in force until a person can read it")
    why = seen.against(start) or (f"the last start was ended by a stop on record ({revoked})" if revoked else "")
    return Stop(STOPPED if why else "", why, start, revoked, seen)


def stop_reason(root: str | Path) -> str:
    """Why the ``STOP`` file alone halts the loop, for the start on record, or ``""``: the file's part of ``stop_in_force``.
    One that cannot be read, or that records which cannot be read might have lifted, still stops."""
    try:
        stop, written = _read_stop(root)
        start = _on_record(root)[0] if stop is not None else None
    except StopUnreadable as exc:
        return _unreadable(exc.__cause__ or exc)
    return StopSeen(stop, written).by_file(start)


def stop_lock(root: str | Path, wait: float | None = None) -> ContextManager[None]:
    """The lock a stop's writer holds until its journal line and its ``STOP`` file are both on disk, and a start holds while
    it reads the stop state and writes what it lifted, so no start can bind a stop the disk does not yet hold."""
    return Coordination(Path(root) / STOP_LOCK).mutex(wait=wait)


def stop_standing(root: str | Path, start: Mapping[str, Any] | None) -> str:
    """Why this folder is stopped for the loop run on ``start``, or ``""``: ``stop_in_force``'s reason."""
    return stop_in_force(root, start).reason


def _unreadable(exc: BaseException) -> str:
    return f"a STOP file is present and could not be read ({type(exc).__name__})"


def stop_to_lift(root: str | Path) -> dict[str, Any]:
    """The ``STOP`` file's part of what a start binds (``StopSeen.lift``). One that cannot be read raises
    ``StopUnreadable``: an unreadable file is never one with nothing to lift."""
    return StopSeen(*_read_stop(root)).lift()


def file_digest(path: Any) -> str:
    """A file's SHA-256, or ``""`` when there is no file to read: what a person enrolled is pinned by its bytes."""
    try:
        return hashlib.sha256(Path(str(path)).read_bytes()).hexdigest() if path else ""
    except OSError:
        return ""


def stops_asked(root: str | Path) -> int:
    """How many stops the journal ``ask_to_stop`` adds to holds. One that cannot be read raises ``StopUnreadable``: no
    count stands for it."""
    return _read_journal(root).count(b"\n")


def _durably(path: Path, text: str) -> None:
    """Add ``text`` to ``path`` and flush it to disk before returning, so nothing reads or acts on a stop the disk lacks."""
    with open(path, "a", encoding="utf-8") as kept:
        kept.write(text)
        kept.flush()
        os.fsync(kept.fileno())


def ask_to_stop(root: str | Path, reason: str = "") -> str:
    """Add this stop to the ``STOP`` file, making it if need be, so the first reason stands and repeating is safe. It kills
    nothing: the runner reads the file before each model call and ends itself. It returns why the ``STOP`` file stops it.

    Each stop writes a token of its own there, so no two stops leave the same bytes, and that is what keeps a stop: a start
    lifts only the file a person agreed to, so it needs no record to know a new stop when it sees one. A stop that cannot
    change the file raises, because the file it could not change may be lifted.

    The stop is first a line in the journal of stops, flushed to disk, in a file no lock stands in front of, because the
    records can be held for as long as a tick lasts and a stop may never wait. Every reader honours that line
    (``stop_in_force``), so a stop that dies before its ``STOP`` file is written stands all the same, and the line serves a
    restart after a person has removed the file, when no note of the stop reached the records. A line that cannot be
    written is left: the ``STOP`` file still stops, and only that restart loses by it.

    Both writes are made holding ``stop_lock``, released only once both are on disk, so no start reads a line the disk
    may yet lose. A start holds that lock only while it reads the stop state and writes its row, so a stop waits for it
    ``STOP_WAIT`` at most, and a stop that cannot have it writes all the same: a stop never fails for a lock."""
    said = " ".join(reason.split()) or "stopped through g6_improve_stop"
    with contextlib.ExitStack() as held:
        try:
            held.enter_context(stop_lock(root, wait=STOP_WAIT))
        except (WorkspaceRefused, OSError):
            pass
        try:
            _durably(Path(root) / STOPS_FILE, said[:200] + "\n")
        except OSError:
            pass
        _durably(Path(root) / STOP_FILE, f"{said}\n{uuid4().hex}\n")
    return stop_reason(root)


def note_refusal(root: str | Path, request: str, said: str) -> None:
    """Keep what a classifier or a permission refused, first and in a file no lock stands in front of, because a refused
    request is never sent again whether or not its cycle reaches the log. ``request``: a digest, never its text. One that
    cannot be written is left: this process still holds it, and the cycle may yet be written."""
    try:
        with open(Path(root) / REFUSED_FILE, "a", encoding="utf-8") as kept:
            kept.write(f"{request} {' '.join(said.split())}\n")
    except OSError:
        pass


def refused_requests(root: str | Path) -> set[str]:
    """The digest of each request ``note_refusal`` kept. A line a crash cut short is no digest. A file that cannot be read
    raises as records that do not check out do, since nothing could then say what must not be sent."""
    try:
        text = (Path(root) / REFUSED_FILE).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return set()
    except OSError as exc:
        raise LedgerError(f"{REFUSED_FILE} cannot be read ({type(exc).__name__})") from exc
    return {found for found in (line.split(" ", 1)[0] for line in text.splitlines()) if len(found) == DIGEST_LENGTH}


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
    file refuses the swap for as long as it reads, so the swap is tried a few times, and past that this beat is missed."""
    path = Path(root) / HEARTBEAT_FILE
    spare = path.with_suffix(".tmp")
    try:
        spare.write_text(json.dumps(fields, sort_keys=True, ensure_ascii=False), encoding="utf-8")
        for _ in range(BEAT_TRIES):
            try:
                os.replace(spare, path)
                return
            except PermissionError:
                time.sleep(BEAT_GAP)
    except OSError:
        pass


def heartbeat(root: str | Path) -> dict | None:
    try:
        return json.loads((Path(root) / HEARTBEAT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


__all__ = ["CYCLE_LOG", "CycleLog", "ENDS_A_STREAK", "FAILED", "FINDINGS", "MAX_FAILED_CYCLES", "NOTHING_TO_TRY",
           "REFUSED_FILE", "RUNNER_LOCK", "STOPPED", "STOPS_FILE", "STOP_FILE", "STOP_LOCK", "STOP_UNREADABLE", "Stop",
           "StopSeen", "StopUnreadable", "TICK_BUDGET_SPENT", "ask_to_stop", "beat", "file_digest", "heartbeat",
           "note_refusal", "record_stop", "records_identity", "refused_requests", "stop_in_force", "stop_lock", "stop_seen",
           "stop_standing", "runner_alive", "runner_lock", "stop_reason", "stop_to_lift", "stops_asked"]
