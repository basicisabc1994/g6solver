# SPDX-License-Identifier: Apache-2.0
"""The loop's records and what halts it (design 6.1, 6.10), which know nothing of what the work is.

``CycleLog`` is the constraint log, a local hash-chained file for now: every cycle that ran, what limited it and what
came of it, plus each enrolment, pause, resumption, start, generation a person took back and provider's limit. Only a
person can enrol a domain, resume a paused loop, take a generation back or start the runner, so the loop cannot lift its own
pause. A canonical id/time stop is reserved and read back before any caller text is rendered. Admission reads that
record independently of the legacy reason, count and ledger note. An incomplete or unreadable reservation refuses every
start and tick. A ``STOP`` file in the loop's folder also halts it at its
next model call, and a stop that cannot be read still stops. Nothing but a person removes that file: a start records the file a
person agreed to lift, and anything else, or any later write, stands, so a stop has no moment in which it can be lost. A
runner ended by a stop needs a person's start again, even with the file gone: the records say the last start was stopped.
A start also records the enrolment its dialog showed, and any other enrolment on record ends it as a stop does. A
heartbeat is written whole or not at all, and a heartbeat that cannot be written is one missed, never a failed tick.

**Is a stop in force?** One function says, ``stop_in_force``, and every reader takes its answer: a start and its dialog,
the runner, a tick, status and the digest. Its reading is a value: it opens the ``STOP`` file, the journal of stops and the
records once each, and the decision, the records' last start with any stop on record after it (which revokes that start
until a person starts again) and the records themselves all come from those bytes, under one digest; no consumer opens
these files of its own. A file is absent only when its open fails with not-found and the folder above it can be listed.
A stop through the tool is a line in
``stops.log``, written and flushed to disk before its ``STOP`` file, both under ``stop_lock``, which a start holds while
it reads the stop state and writes its row, and a start flushes the journal itself before its row binds it, since a stop
that waited out the lock writes without it, so no start binds a line the disk may yet lose and a stop that dies between
the two writes still stands for every reader. A start binds the reading whole: the ``STOP`` file it lifts, and the bytes
of ``stops.log`` by digest, so bytes decide, a line cut short included. Anything that cannot be read or used, a file, a
records path that is not a file, a chain that does not check out, a start's lift fields of the wrong shape, is never a
value or a raised error: it is a stop in force, ``G6_E_LOOP_STOP_UNREADABLE``, for every reader alike. The canonical stops are part of that
reading (``canonical_stops``): one recorded since the last start is a stop in force, and a canonical state that cannot be
read, a stop this process is still writing or could not write among it, is unreadable as any other file is. So is the
enrolment on record, which ends a start that named another (``G6_E_LOOP_ENROLMENT_CHANGED``).

**Does the denial reach every process?** A stop's writes are made holding ``stop_lock``. A stop whose canonical record
does not stand keeps that lock for as long as its request denies the folder, which is as long as its process lives, so
the denial is in the operating system and not only in the memory of the process that was asked. Every path that admits
work decides through ``admission``: beside its reading it asks for that lock, and a lock it cannot have, held by a stop
that is being written or could not be written, is a stop in force, ``G6_E_LOOP_STOP_UNREADABLE``, for a runner already
alive, a restart and the server alike. A start holds the same lock while it reads and writes its row.

**Rows, words and endings.** Every file of rows here, the records, the journal, the ``STOP`` file and the log of refusals,
holds to one rule for where a row ends (``fresh_line``): at its newline, or at the end of the file for a last row with none,
as the ledger reads its own file, so every append starts on a fresh line. Every write makes bytes of text by one rule that
cannot fail (``utf8``), so no caller's words keep a stop from being written. And every way a start ends on a stop, a tick's
guard, its cut-off, the runner's ending and a start a stop met, calls ``end_start``: a stop in force only because a file
cannot be read is written through ``ask_to_stop`` beside the stopped row, so the repair of that file brings no start back.

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
import errno
import hashlib
import json
import os
import threading
import tempfile
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, ContextManager
from uuid import uuid4

from g6.core.adaptation_gate import is_automated
from g6.core.ledger import ChainLedger, LedgerError, digest
from g6.core.storage import plain_file
from g6.workspace._coordination import Coordination
from g6.workspace._envelope import BUSY as HELD_BY_ANOTHER
from g6.workspace._envelope import WorkspaceRefused

STOP_FILE = "STOP"
STOPS_FILE = "stops.log"
#: The folder of the canonical stops, one folder to a stop, and the marker that stands in when that folder cannot be made.
STOP_RECORDS = "stop_records"
STOP_DENIED = "stop.denied"
#: A refusal before even the independent directory can be made also holds this process: each request's own entry.
_STOP_PENDING: dict[object, str | Path] = {}
#: The folder of the lock a stop's writer and a start's reading of the stop state share (``stop_lock``).
STOP_LOCK = ".stops"
#: The longest a stop waits for that lock, in seconds, before it writes without it: a start holds it for milliseconds.
STOP_WAIT = 1.0
#: The longest an admission waits for that lock before it refuses: a stop being written holds it for milliseconds, and
#: one whose record does not stand keeps it for as long as its process lives.
ADMISSION_WAIT = 1.0
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
#: A start that no longer covers what would run: the enrolment on record is not the one its dialog showed.
ENROLMENT_CHANGED = "G6_E_LOOP_ENROLMENT_CHANGED"
# COPY: Why a runner runs nothing more on its start once another enrolment is on record.
NOT_THE_AGREED_ENROLMENT = ("the enrolment on record is not the one the person agreed to at the last start, so nothing "
                            "more runs on that start: g6_improve_start shows them the enrolment that stands "
                            f"({ENROLMENT_CHANGED})")
#: Why a canonical stop state that cannot be read, or a stop still being written, refuses admission.
STOP_STATE_UNREADABLE = "G6_E_LOOP_STOP_STATE_UNREADABLE: stop state is uncertain; admission is denied"
#: Why a canonical stop recorded since a start ends that start.
STOP_CANONICAL = ("G6_E_LOOP_STOP_CANONICAL: a stop was recorded after this start, so it has ended; a new person's start is "
                  "required")
#: The ledger's digest of a value, under the name the consent tools use for it.
digest_of = digest


def _begin_stop(root: str | Path) -> object:
    """Install a process denial before path work, entropy, clocks, rendering or I/O.

    A fresh function object supplies identity without calling a helper or reading
    a clock or randomness source. It is never executed. Dictionary insertion and removal
    use that object's identity; equal folder values do not share an entry.
    """
    def token():
        pass
    _STOP_PENDING[token] = root
    return token


def _finish_stop(token: object) -> None:
    """Completion releases only this request, never another request's folder, and with it any stop lock the request
    kept (``_Held``)."""
    _STOP_PENDING.pop(token, None)
    kept = getattr(token, "holds", None)
    if kept is not None:
        kept.release()


class _Held:
    """The stop lock a stop keeps while its canonical record does not stand: let go when its request is finished, when
    nothing refers to the request any more, or when its process ends, which the operating system sees to."""

    def __init__(self, stack: contextlib.ExitStack) -> None:
        self._stack = stack

    def release(self) -> None:
        stack, self._stack = self._stack, None
        if stack is not None:
            try:
                stack.close()
            except Exception:  # noqa: BLE001 - a lock that cannot be let go is let go with its process
                pass

    def __del__(self) -> None:
        self.release()

_REFUSAL_WRITE_LOCK = threading.Lock()  # also the loop's admission lock, whichever thread calls this writer


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
                "canonical_stops": _hex_digest,
                "lifted_mtime_ns": lambda value: isinstance(value, int) and not isinstance(value, bool)}


def _lift_problems(start: Mapping[str, Any]) -> list[str]:
    """What is wrong with the fields a start records of the stop state it lifted: each is absent, or of the shape a reading
    gives it, and null is no shape. The one check, where the row is written and again where it is read, since no stop
    decision may stand on a field it cannot use."""
    return [f"started: {name} is not what a reading of the stop state gives ({type(start[name]).__name__})"
            for name, shape in _LIFT_FIELDS.items() if name in start and not shape(start[name])]


def _on_a_clock(text: Any) -> bool:
    """Whether ``text`` is a moment with a time zone, which is all a meter can compare with ``now``."""
    try:
        return datetime.fromisoformat(text).tzinfo is not None
    except (TypeError, ValueError):
        return False


def utf8(text: str) -> bytes:
    """Text as the bytes every write here makes of it, by one rule that cannot fail: UTF-8, with what UTF-8 cannot carry (a
    lone surrogate) spelled out with a backslash. So no caller's words keep a stop, its row or a heartbeat from being
    written, and every reader reads back plain UTF-8."""
    return text.encode("utf-8", "backslashreplace")


def writable(value: Any) -> Any:
    """``value`` with every string, keys too, as ``utf8`` writes it, for a row or a heartbeat written as JSON: what UTF-8
    cannot carry is spelled out with a backslash, so a row that repeats a caller's reason, a domain's labels or an error's
    words can always be written."""
    if isinstance(value, str):
        return utf8(value).decode("utf-8")
    if isinstance(value, Mapping):
        return {writable(k): writable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [writable(v) for v in value]
    return value


def utf8_text(text: str) -> str:
    """Keep every storable value unchanged; escape only code points strict UTF-8 cannot hold, as ``utf8`` does.

    Chunks bound each encoding operation to 4096 code points. There is no truncation: long reasons retain their
    existing meaning. Lone surrogates read back as literal backslash escapes, as the stop reply states.
    """
    text = str.__str__(text)  # use the string's value, without calling Python subclasses' rendering methods
    return "".join(utf8(text[i:i + 4096]).decode("utf-8") for i in range(0, len(text), 4096))


def utf8_fields(value: Any) -> Any:
    """The same text policy for names, reasons, notes and nested ledger/reply fields."""
    if isinstance(value, str):
        return utf8_text(value)
    if isinstance(value, dict):
        return {utf8_fields(key): utf8_fields(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [utf8_fields(item) for item in value]
    return value


def fresh_line(held: bytes) -> bytes:
    """Where a row ends, the one rule for the records, the journal of stops and every file of rows here: at its newline, or,
    for a last row with none (a crash cut it short, or a hand wrote it so), at the end of the file, as the ledger reads its
    own file. So every append starts on a fresh line, and this is what it writes first: ``b"\\n"`` after ``held`` when its
    last row has no newline, and nothing for an empty file or a last row that ends in LF or CRLF. No row is ever joined onto
    the one before it."""
    return b"\n" if held and not held.endswith(b"\n") else b""


class CycleLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._chain = ChainLedger(self.path, validate=_rules)

    def reload(self) -> None:
        """Read the file again: another handle may have written it. Its earlier entries must be unchanged."""
        self._chain.reload()

    def head(self) -> str:
        """The hash of the last row as this handle holds the records, which names every row before it (``""`` with none)."""
        return self._chain.head()

    def _rows(self) -> list[tuple[str, dict]]:
        return [(e["kind"], e["body"]) for e in self._chain.entries()]

    def _append(self, kind: str, body: dict) -> dict:
        """Every row this writes: on a fresh line (``fresh_line``), so a last row the file holds with no newline is ended
        before the ledger adds its own, which it writes whole with its newline."""
        body = utf8_fields(body)
        _durably(self.path, "", make=False)
        self._chain.append(kind, str(len(self._rows()) + 1), body)
        return body

    def _write_record(self, kind: str, body: dict, record_id: str | None = None) -> str:
        """Write the identity with the row, on a fresh line as every row is (``_append``), returning it only after the
        append. A caller may allocate it once before writing, so a lost return after an append can still be read back by
        that identity. Bodies keep their meaning."""
        ident = utf8_text(uuid4().hex if record_id is None else record_id)
        _durably(self.path, "", make=False)
        self._chain.append(kind, ident, utf8_fields(body))
        return ident

    def latest_id(self, kind: str) -> str:
        """The latest row's stored identity, including the numeric identities of older logs; no row is empty text."""
        return next((e["id"] for e in reversed(self._chain.entries()) if e["kind"] == kind), "")

    def record(self, kind: str, ident: str) -> dict | None:
        """Read precisely the row written with this identity. An absent row is distinct from unreadable records."""
        try:
            return self._chain.get(kind, utf8_text(ident))
        except KeyError:
            return None

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

    def enrol(self, by: str, frozen: Mapping[str, str], *, record_id: str | None = None, **fields: Any) -> str:
        return self._write_record("enrolled", {"by": by, "frozen": dict(frozen), **fields}, record_id)

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

    def start(self, by: str, *, record_id: str | None = None, **fields: Any) -> str:
        if "enrolment" in fields and "enrolment_id" not in fields:
            fields["enrolment_id"] = enrolment_id(self)
        return self._write_record("started", {"by": by, **fields}, record_id)

    def started(self) -> dict | None:
        """The last time a person started the runner."""
        return next((body for kind, body in reversed(self._rows()) if kind == "started"), None)

    def stop(self, reason: str, *, record_id: str | None = None, **fields: Any) -> str:
        """A stopped row, as ``utf8`` writes text (``writable``): no reason a stop carries keeps the row from being
        written. It returns the identity the row was written with."""
        return self._write_record("stopped", writable({"reason": reason, **fields}), record_id)

    def limit(self, provider: str, until: str, tick: str = "") -> dict:
        """A limit a provider stated, and the tick that met it (``tick``), when one did."""
        return self._append("limited", {"provider": provider, "until": until, **({"tick": tick} if tick else {})})

    def limits(self) -> dict[str, datetime]:
        """When each provider's latest limit ends, so that a restart waits it out. A tick writes every limit it met, in the
        order it met them, which need not be the order they end in, and each stands until the furthest of them ends; a later
        row of another tick, or of none, replaces them. No cycle: it never counts in a streak."""
        found: dict[str, tuple[str, datetime]] = {}
        for kind, body in self._rows():
            if kind == "limited":
                tick, until = body.get("tick") or "", datetime.fromisoformat(body["until"])
                held = found.get(body["provider"])
                if tick and held is not None and held[0] == tick:
                    until = max(until, held[1])
                found[body["provider"]] = (tick, until)
        return {provider: until for provider, (_, until) in found.items()}

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
    """One reading of all a stop decision stands on, and the decision: ``code`` is ``STOPPED``, ``STOP_UNREADABLE`` or
    ``""`` for none, ``reason`` says why. The rest is what it was read from and decided against, so no consumer opens a
    file of its own: ``start`` and ``revoked``, the last start on record and why a stop on record ended it (``""`` when none
    did); ``seen``, the ``STOP`` file, the journal, the canonical stops and the enrolment on record; ``log``, the records parsed from the bytes read, held in memory
    (``None`` when absent, unreadable or kept from a tick's earlier reading); ``records_sha256``, the digest of those bytes
    (``""`` with none); ``digest``, the whole reading's; ``unread``, the files that could not be read; ``records``, the
    records' bytes themselves, which a start's row must be appended onto (``g6.mcp.improve.start``)."""

    code: str = ""
    reason: str = ""
    start: Mapping[str, Any] | None = field(default=None, compare=False)
    revoked: str = field(default="", compare=False)
    seen: StopSeen | None = field(default=None, compare=False)
    log: CycleLog | None = field(default=None, compare=False)
    records_sha256: str = field(default="", compare=False)
    digest: str = field(default="", compare=False)
    unread: tuple[str, ...] = field(default=(), compare=False)
    records: bytes = field(default=b"", compare=False, repr=False)

    def __bool__(self) -> bool:
        return bool(self.code)


@dataclass(frozen=True)
class StopSeen:
    """One reading of all that says whether a stop was asked for: the ``STOP`` file (``None`` with none), its modification
    time, taken before its bytes, and the journal of stops, ``stops.log`` (empty with none), with the identity of the file
    its bytes were read from (``None`` with none), which a start checks is the file it flushed (``flush_journal``). With
    them, what else a start binds: ``canonical``, the digest of the canonical stops on record (``""`` with none; ``None``
    when that state is uncertain, which no start lifts), and ``enrolled``, the enrolment on record by digest and stored
    identity (``None`` when the records were not parsed for this reading)."""

    stop: bytes | None = None
    stop_mtime_ns: int | None = None
    journal: bytes = b""
    journal_file: tuple[int, int] | None = field(default=None, compare=False)
    canonical: str | None = field(default="", compare=False)
    enrolled: tuple[str, str] | None = field(default=None, compare=False)

    def lift(self) -> dict[str, Any]:
        """The ``STOP`` file as it stands, for a start to record what a person agreed to lift; nothing with no file. No start
        moves or deletes the file: this record is all that lifting is."""
        canonical = {"canonical_stops": self.canonical} if self.canonical else {}   # the canonical stops it lifts, if any
        if self.stop is None:
            return canonical
        timed = {} if self.stop_mtime_ns is None else {"lifted_mtime_ns": self.stop_mtime_ns}   # no time read: none bound
        return {**canonical, "lifted": hashlib.sha256(self.stop).hexdigest(), "lifted_bytes": len(self.stop), **timed}

    def binding(self) -> dict[str, Any]:
        """All a start records of this reading, and all a dialog's terms bind: the ``STOP`` file it lifts, and the journal
        by its count and its digest, so a journal rewritten to as many lines is not the one the start saw; the canonical
        stops on record (``lift``); and the enrolment on record, by digest and stored identity, when this reading parsed
        the records."""
        enrolled = {} if self.enrolled is None else {"enrolment": self.enrolled[0], "enrolment_id": self.enrolled[1]}
        return {**self.lift(), "stops": self.journal.count(b"\n"), "stops_sha256": hashlib.sha256(self.journal).hexdigest(),
                **enrolled}

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

    def by_canonical(self, start: Mapping[str, Any] | None) -> str:
        """Why the canonical stops halt the loop run on ``start``: they are not the ones that start recorded, so a stop was
        recorded since. A state that cannot be read is not decided here: the reading calls it unreadable."""
        return STOP_CANONICAL if self.canonical is not None and self.canonical != (start or {}).get("canonical_stops", "") else ""

    def against(self, start: Mapping[str, Any] | None) -> str:
        """Why a stop is in force for the loop run on ``start``, or ``""``: the ``STOP`` file speaks first, then the
        journal, then the canonical stops."""
        return self.by_file(start) or self.by_journal(start) or self.by_canonical(start)

    def by_enrolment(self, start: Mapping[str, Any] | None) -> str:
        """Why the enrolment on record ends the loop run on ``start``, or ``""``: a start records the enrolment its
        dialog showed, by stored identity and body digest, and runs only that one. A start that names none is the runner's
        gate's to refuse (``g6.metaharness.runner``), and a reading that did not parse the records says nothing."""
        agreed = (start or {}).get("enrolment")
        if agreed is None or self.enrolled is None:
            return ""
        return "" if (agreed, (start or {}).get("enrolment_id")) == self.enrolled else NOT_THE_AGREED_ENROLMENT


#: How many times a reading reads a file that changed while it was read before it calls that file unreadable.
READ_TRIES = 3


def _absent(path: Path, root: Path) -> bool:
    """Whether a file the open could not find is truly absent, decided from the entries themselves by ``lstat``, which
    follows no link: nothing at all is at the path, so a link, a junction or any other reparse point there, broken or not,
    is an entry and never absence; and the nearest entry above it, up to the loop's folder, is a folder that can be listed.
    An entry above that cannot be listed, a broken junction among them, shows no absence, nor does a path under a regular
    file (Windows reports it not found), nor an entry that cannot be looked at."""
    try:
        os.lstat(path)
        return False                     # an entry is there, whatever it is: a read of it that failed is no absence
    except FileNotFoundError:
        pass
    except Exception:  # noqa: BLE001 - an entry that cannot be looked at, for any reason, does not show absence
        return False
    folder = path.parent
    while True:
        try:
            os.lstat(folder)
        except FileNotFoundError:
            if folder == root or folder == folder.parent:
                return False
            folder = folder.parent
            continue
        except Exception:  # noqa: BLE001 - as above
            return False
        try:
            with os.scandir(folder):
                return True
        except Exception:  # noqa: BLE001 - a folder that is there but cannot be listed, for any reason, shows nothing
            return False


def _read_file(path: Path, root: Path, *, timed: bool = False) -> tuple[bytes, int | None, tuple[int, int]] | None:
    """A file's bytes, with ``timed`` its modification time taken before them, and the file's identity (its volume and
    file id, from the handle the bytes were read through), or ``None`` when it is absent (``_absent``): absent
    only when the open itself fails with not-found and the folder above is a readable directory. The bytes are read from
    one open handle, whose file is known by the handle itself (``fstat``: on Windows its volume and file index), and they
    stand only when that very file is what stands at the path once they are read, a regular file under this one name and no
    link, holding as many bytes as were read. A file replaced or changed while it was read is read again. Any other failure,
    of whatever kind, raises ``StopUnreadable``."""
    name = path.relative_to(root).as_posix()
    for _ in range(READ_TRIES):
        try:
            written = None
            if timed:
                try:
                    written = path.stat().st_mtime_ns    # before the bytes: a write between leaves an old time, so it stands
                except FileNotFoundError:
                    pass                                  # the open says whether it is there
            with path.open("rb") as handle:
                opened = os.fstat(handle.fileno())        # the file these bytes come from, whatever the path names later
                data = handle.read()
        except FileNotFoundError:
            if _absent(path, root):
                return None
            raise StopUnreadable(f"{name} cannot be read (the folder above it is not one that can be read)") from None
        except Exception as exc:  # noqa: BLE001 - any failure to read is unreadable, never an escape
            raise StopUnreadable(f"{name} cannot be read ({type(exc).__name__})") from exc
        try:
            kept = plain_file(path)                       # a regular file under this one name, no link
            now = path.stat()                             # and the file read is the one that stands there now
        except FileNotFoundError:
            continue
        except Exception as exc:  # noqa: BLE001 - any failure to read is unreadable, never an escape
            raise StopUnreadable(f"{name} cannot be read ({type(exc).__name__})") from exc
        read = (opened.st_dev, opened.st_ino)
        if (opened.st_ino and read == (kept.st_dev, kept.st_ino) == (now.st_dev, now.st_ino)
                and opened.st_size == len(data) == now.st_size):
            return data, written, read
    raise StopUnreadable(f"{name} changed each time it was read")


def _parsed(data: bytes) -> CycleLog:
    """The constraint log as the bytes a reading took, parsed and checked by the ledger itself and held in memory: the
    bytes go to a file of their own for the ledger to read, which is removed once read, so nothing reopens the records."""
    handle, copy = tempfile.mkstemp(prefix="g6-stop-reading-", suffix=".jsonl")
    try:
        with os.fdopen(handle, "wb") as held:
            held.write(data)
        return CycleLog(Path(copy))
    finally:
        os.unlink(copy)


def _start_of(log: CycleLog | None) -> tuple[dict | None, str]:
    """The last start in ``log``, and why a stop after it revoked it (``""`` when none did): of the starts and stops on
    record, a stop after the last start ends it until a person starts again. A start whose lift fields no reading gives
    raises ``StopUnreadable``."""
    start, revoked = None, ""
    for kind, body in (log._rows() if log is not None else ()):
        if kind == "started":
            start, revoked = body, ""
        elif kind == "stopped" and start is not None:
            revoked = str(body.get("reason") or "a stop on record")
    if start is not None and _lift_problems(start):
        raise StopUnreadable(f"the last start's record of what it lifted is malformed: {_lift_problems(start)[0]}")
    return start, revoked


def _canonical_stops_on_disk(root: str | Path, expected: bytes | None = None) -> str | None:
    """Read the canonical stops, or refuse uncertainty. Reading never creates a file.

    An exclusive directory reserves a stop before its record is opened. Missing, partial, malformed or unreadable
    records keep that independent gate shut. Only a complete id/time record may be shown for a new start to lift.
    """
    root = Path(root)
    try:
        try:
            (root / STOP_DENIED).stat()
        except FileNotFoundError:
            pass
        else:
            return None
        folder = root / STOP_RECORDS
        try:
            folder.stat()
        except FileNotFoundError:
            return None if expected is not None else ""
        rows = []
        for record in sorted(folder.iterdir()):
            ident = record.name
            if len(ident) != 32 or any(c not in "0123456789abcdef" for c in ident):
                return None
            if sorted(p.name for p in record.iterdir()) != ["stop.json"]:
                return None
            held = (record / "stop.json").read_bytes()
            value = json.loads(held.decode("ascii"))
            if (not isinstance(value, dict) or set(value) != {"id", "at"} or value["id"] != ident
                    or not _on_a_clock(value["at"])):
                return None
            if datetime.fromisoformat(value["at"]).isoformat() != value["at"]:
                return None
            if held != utf8(json.dumps(value, sort_keys=True, ensure_ascii=True) + "\n"):
                return None
            rows.append(held)
        if expected is not None and expected not in rows:
            return None
        return hashlib.sha256(b"".join(rows)).hexdigest() if rows else None
    except BaseException:
        return None


def canonical_stops(root: str | Path, *, completing: object = None) -> str | None:
    """Every pending or failed request denies its folder, including alternate spellings."""
    try:
        resolved = Path(root).resolve()
        for token, held in tuple(_STOP_PENDING.items()):
            if token is completing:
                continue
            # An unresolved default locator could name this folder. Until its
            # selection completes, no admission may assume that it names another.
            if isinstance(held, str) and not held.strip():
                return None
            locator = Path(held)
            if not locator.is_absolute():
                return None  # failed before its working-directory scope was captured
            try:
                if locator.resolve() == resolved:
                    return None
            except ValueError:
                continue  # an intrinsically invalid locator cannot name a folder
            except OSError as exc:
                if exc.errno in (errno.EINVAL, errno.ENAMETOOLONG) or getattr(exc, "winerror", None) in (123, 206):
                    continue  # the operating system cannot represent this locator
                return None  # an unreadable alias might name this very folder
        return _canonical_stops_on_disk(resolved)
    except BaseException:
        return None


def canonical_stop_reason(root: str | Path, start: Mapping[str, Any] | None) -> str:
    """Why the canonical stops alone refuse whatever runs on ``start``, or ``""``: the canonical part of the one reading's
    decision (``stop_in_force``), from the same rule (``canonical_stops``)."""
    snapshot = canonical_stops(root)
    if snapshot is None:
        return STOP_STATE_UNREADABLE
    return StopSeen(canonical=snapshot).by_canonical(start)


def canonical_stop_record(root: str | Path, ident: str | None) -> bool | None:
    """Read this request's disk identity, never borrowing a timestamp or another stop's note.

    This is a receipt observation. Admission additionally checks every pending or
    failed request through ``canonical_stops``; a receipt cannot clear that gate.
    """
    if not ident:
        return False
    if _canonical_stops_on_disk(root) is None:
        return None
    try:
        held = (Path(root) / STOP_RECORDS / ident / "stop.json").read_bytes()
        return json.loads(held.decode("ascii"))["id"] == ident and _canonical_stops_on_disk(root, held) is not None
    except FileNotFoundError:
        return False
    except BaseException:
        return None


def enrolment_digest(log: CycleLog | None) -> str:
    """The contents of the enrolment on record, or the digest of none. This persisted digest keeps its meaning;
    ``enrolment_id`` distinguishes separate writes of identical contents."""
    return digest_of((log.enrolled() if log is not None else None) or {})


def enrolment_id(log: CycleLog | None) -> str:
    """The stored identity of the current enrolment. Its body digest remains a digest of its contents alone."""
    return log.latest_id("enrolled") if log is not None else ""


def stop_in_force(root: str | Path, *, known: tuple[Any, ...] | None = None) -> Stop:
    """Whether a stop is in force on this folder and why: the one reading every reader takes, a start, its dialog, the
    runner, a tick, status and the digest alike, and nothing else opens these files. It opens the ``STOP`` file, the
    journal and the constraint log once each, and everything else comes from those bytes in memory: the decision, the start
    terms, the last start and any stop after it, and the records themselves (``Stop.log``). ``Stop.digest`` names all of it.
    No argument skips any part of it.

    ``known``: the records' digest, start and revocation a tick kept from an earlier reading, and after them, when it
    kept it too, the enrolment that reading found, which stand only when the
    records' bytes read now have that very digest, so nothing is parsed again for a tick that wrote nothing.

    The canonical stops are read with them (``canonical_stops``): a stop recorded since the last start is a stop in force,
    and a state that cannot be read, a request this process holds pending or failed among it, is unreadable as a file is.
    With no stop in force, an enrolment on record that the last start did not name ends that start with a code of its own
    (``ENROLMENT_CHANGED``). Anything that
    cannot be read or used, any of the three files or a start's lift fields, is a stop in force, ``STOP_UNREADABLE``, never
    a value and never a raised error: nothing could show that a person lifted it."""
    root, unread, log, held = Path(root), {}, None, None
    stop = journal = None
    try:
        stop = _read_file(root / STOP_FILE, root, timed=True)
    except Exception as exc:  # noqa: BLE001 - any failure here is a stop in force, never an escape
        unread[STOP_FILE] = _unread(exc)
    try:
        journal = _read_file(root / STOPS_FILE, root)
    except Exception as exc:  # noqa: BLE001 - as above
        unread[STOPS_FILE] = _unread(exc)
    records_sha256, start, revoked, enrolled = "", None, "", None
    try:
        held = _read_file(root / CYCLE_LOG, root)
        records_sha256 = hashlib.sha256(held[0]).hexdigest() if held is not None else ""
        if known is not None and known[0] == records_sha256:
            start, revoked = known[1], known[2]
            enrolled = known[3] if len(known) > 3 else None
        else:
            if held is not None:
                try:
                    log = _parsed(held[0])
                except Exception as exc:  # noqa: BLE001 - records the ledger cannot take, however it fails
                    raise StopUnreadable(f"the loop's records, which say what the last start lifted, cannot be read or "
                                         f"cannot be trusted ({type(exc).__name__})") from exc
            start, revoked = _start_of(log)
            enrolled = (enrolment_digest(log), enrolment_id(log))
    except Exception as exc:  # noqa: BLE001 - as above
        unread["records"] = _unread(exc)
    records = held[0] if held is not None else b""
    canonical = canonical_stops(root)   # never raises: a state it cannot read, or a stop pending here, is ``None``
    if canonical is None:
        unread[STOP_RECORDS] = STOP_STATE_UNREADABLE
    seen = StopSeen(*(stop[:2] if stop else (None, None)), journal[0] if journal else b"",   # what could be read:
                    journal[2] if journal else None, canonical, enrolled)                     # ``unread`` says the rest
    why = changed = ""
    if not unread:
        try:
            why = seen.against(start) or (f"the last start was ended by a stop on record ({revoked})" if revoked else "")
            changed = "" if why else seen.by_enrolment(start)
        except Exception as exc:  # noqa: BLE001 - a start the decision cannot be made against is no lift
            unread["records"] = f"the last start cannot be read against the stop's files ({type(exc).__name__})"
    whole = digest({"stop": hashlib.sha256(stop[0]).hexdigest() if stop else None, "stop_mtime_ns": stop[1] if stop else None,
                    "journal": hashlib.sha256(journal[0]).hexdigest() if journal else None, "records": records_sha256,
                    "canonical": canonical, "unread": sorted(unread)})
    if unread:
        first = next(iter(unread.values()))
        if set(unread) != {STOP_RECORDS}:   # only an uncertain canonical state leaves the start, which the records hold,
            start, revoked = None, ""       # for the STOP file to be read against (``stop_reason``): a stop all the same
        return Stop(STOP_UNREADABLE, f"{first}, so a stop is in force until a person can read it", start, revoked, seen, log,
                    records_sha256, whole, tuple(unread), records)
    if changed:
        return Stop(ENROLMENT_CHANGED, changed, start, revoked, seen, log, records_sha256, whole, (), records)
    return Stop(STOPPED if why else "", why, start, revoked, seen, log, records_sha256, whole, (), records)


def _unread(exc: BaseException) -> str:
    """What a reading says of a file it could not read: the reason ``StopUnreadable`` gives, or the kind of failure."""
    return str(exc) if isinstance(exc, StopUnreadable) else f"a stop's file could not be read ({type(exc).__name__})"


def stop_seen(root: str | Path) -> StopSeen:
    """The ``STOP`` file and the journal, as ``stop_in_force``'s one full reading read them. One that cannot be read raises
    ``StopUnreadable``, so no reader can take it for no stop."""
    found = stop_in_force(root)
    if STOP_FILE in found.unread or STOPS_FILE in found.unread:
        raise StopUnreadable(found.reason)
    return found.seen


def stop_reason(root: str | Path) -> str:
    """Why the ``STOP`` file alone halts the loop, for the start on record, or ``""``: the file's part of ``stop_in_force``'s
    reading, which it reads no file of its own for. One that cannot be read, or that records which cannot be read might
    have lifted, still stops."""
    found = stop_in_force(root)
    if STOP_FILE in found.unread or found.seen.stop is not None and "records" in found.unread:
        return f"a STOP file is present and could not be read ({found.reason})"
    return found.seen.by_file(found.start)


def stop_lock(root: str | Path, wait: float | None = None) -> ContextManager[None]:
    """The lock a stop's writer holds until its journal line and its ``STOP`` file are both on disk, and a start holds while
    it reads the stop state and writes what it lifted, so no start can bind a stop the disk does not yet hold."""
    return Coordination(Path(root) / STOP_LOCK).mutex(wait=wait)


def _stop_held(root: Path) -> str:
    """Why the stop lock refuses an admission, or ``""``: another holds it past ``ADMISSION_WAIT``, a stop that is being
    written or one that could write no record and keeps it, or it cannot be tried at all. A folder where no stop and no
    start ever took the lock has none to ask for, absent by the reading's own rule (``_absent``), so nothing is written
    there; nor is anything made in a folder that is gone, which the reading itself refuses."""
    try:
        if not root.is_dir() or _absent(root / STOP_LOCK / "mutex", root):
            return ""
        with stop_lock(root, wait=ADMISSION_WAIT):
            return ""
    except WorkspaceRefused as exc:
        if exc.code == HELD_BY_ANOTHER:
            return (f"a stop is being written on this folder, or could not be written and is held by the process that was "
                    f"asked: the stop lock ({STOP_LOCK}) is held")
        return f"the stop lock ({STOP_LOCK}) cannot be tried ({exc.code})"
    except Exception as exc:  # noqa: BLE001 - a lock that cannot be tried, however, admits nothing
        return f"the stop lock ({STOP_LOCK}) cannot be tried ({type(exc).__name__})"


def admission(root: str | Path, reading: Stop) -> Stop:
    """What every path that admits work decides on: a tick's guard and the gate before each of its requests, the
    runner's preflight and gate, a restart's among them, and the start's dialog and the start. ``reading`` is the one
    reading the caller has just taken (``stop_in_force``); this asks for the stop lock beside it (``_stop_held``). A
    stop's durable write is made under that lock, and a stop that could write no record keeps it from before its first
    write for as long as its process lives, so no admission that asks after such a stop began has the lock: it is a stop
    in force, ``STOP_UNREADABLE``, with the rest of the reading as it was read. So the denial of a stop reaches a process
    that shares no memory with the one that was asked. A stop that finished its write is in the reading itself; only a
    request whose own check overlapped that write is admitted on a reading from before it, as one checked a moment
    earlier would be. A caller that holds the stop lock itself, a start under its locks, decides on ``stop_in_force``
    alone."""
    if reading.code == STOP_UNREADABLE:
        return reading
    held = _stop_held(Path(root))
    if not held:
        return reading
    return Stop(STOP_UNREADABLE, f"{held}, so a stop is in force until it is let go", reading.start, reading.revoked,
                reading.seen, reading.log, reading.records_sha256, reading.digest, (*reading.unread, STOP_LOCK),
                reading.records)


def stop_standing(root: str | Path) -> str:
    """Why this folder is stopped, or ``""``: the reason of ``stop_in_force``'s one full reading, the start on record and
    any stop after it among what it read."""
    return stop_in_force(root).reason


def stop_to_lift(root: str | Path) -> dict[str, Any]:
    """The ``STOP`` file's part of what a start binds, and the canonical stops' (``StopSeen.lift``), from
    ``stop_in_force``'s one full reading. One that cannot be read raises ``StopUnreadable``: an unreadable file, or an
    uncertain canonical state, is never one with nothing to lift."""
    found = stop_in_force(root)
    if STOP_FILE in found.unread or STOP_RECORDS in found.unread:
        raise StopUnreadable(found.reason)
    return found.seen.lift()


def what_stops(root: str | Path, start: Mapping[str, Any] | None) -> tuple[str, str]:
    """What stops whatever runs on ``start`` as a code and a reason, from ``stop_in_force``'s one full reading, for the
    callers that hold a start of their own: the stop's files, the journal and the canonical stops decided against that
    start, a stop on record after the start the records hold, anything that cannot be read (no code of its own: the
    reader's stop), or an enrolment on record that is not the one the start agreed to (``ENROLMENT_CHANGED``).
    ``("", "")`` when nothing stops it."""
    found = stop_in_force(root)
    if found.code == STOP_UNREADABLE:
        return "", found.reason
    why = found.seen.against(start) or (f"the last start was ended by a stop on record ({found.revoked})"
                                        if found.revoked else "")
    if why:
        return "", why
    changed = found.seen.by_enrolment(start)
    return (ENROLMENT_CHANGED, changed) if changed else ("", "")


def enrolment_changed(root: str | Path, start: Mapping[str, Any] | None) -> str:
    """Why the enrolment on record is not the one ``start`` agreed to, or ``""``, from ``stop_in_force``'s one full
    reading (``StopSeen.by_enrolment``). A start that names none is the runner's gate's to refuse
    (``g6.metaharness.runner``), and records that do not read are their own rule's: no tick runs on them. It writes
    nothing, so the status can show it."""
    return stop_in_force(root).seen.by_enrolment(start)


def file_digest(path: Any) -> str:
    """A file's SHA-256, or ``""`` when there is no file to read: what a person enrolled is pinned by its bytes."""
    try:
        return hashlib.sha256(Path(str(path)).read_bytes()).hexdigest() if path else ""
    except OSError:
        return ""


def stops_asked(root: str | Path) -> int:
    """How many stops the journal ``ask_to_stop`` adds to holds, from ``stop_in_force``'s one full reading. One that cannot
    be read raises ``StopUnreadable``: no count stands for it."""
    found = stop_in_force(root)
    if STOPS_FILE in found.unread:
        raise StopUnreadable(found.reason)
    return found.seen.journal.count(b"\n")


def _durably(path: Path, text: str, *, make: bool = True) -> None:
    """Add ``text`` to ``path`` as rows of their own, on a fresh line (``fresh_line``) and as ``utf8`` writes it, and flush
    it to disk before returning, so nothing reads or acts on a stop the disk lacks. The one way a row is added to a file
    here: the journal of stops, the ``STOP`` file, the log of refusals, and before each of the records' rows, the end of the
    last one (``CycleLog._append``). ``make``: whether a file not there is made; when it is not, a missing file has no row
    to end and nothing is written. The row is appended through a handle opened to append and nothing else, as text that
    ``utf8`` has already made storable and with LF endings, so the bytes are ``utf8``'s and a write that fails part way
    leaves what it wrote at the end of the file and nowhere else."""
    try:
        last = _last_byte(path)
    except FileNotFoundError:
        if not make:
            return
        last = b""
    added = fresh_line(last).decode("ascii") + utf8(text).decode("utf-8")
    if added:
        with open(path, "a", encoding="utf-8", newline="\n") as kept:
            kept.write(added)
            kept.flush()
            os.fsync(kept.fileno())


def _last_byte(path: Path) -> bytes:
    """The last byte a file holds (none for an empty one), read through a handle that can only read."""
    handle = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        end = os.lseek(handle, 0, os.SEEK_END)
        if not end:
            return b""
        os.lseek(handle, end - 1, os.SEEK_SET)
        return os.read(handle, 1)
    finally:
        os.close(handle)


@contextlib.contextmanager
def flush_journal(root: str | Path, seen: StopSeen) -> Iterator[tuple[int, int] | None]:
    """Flush the journal of stops to disk through a handle of the caller's own, writing no byte, and hold that handle open
    until the caller is done, yielding the flushed file's identity (its volume and file id; ``None`` when ``seen``, a
    reading's, found no journal, and nothing is opened). A start binds that identity with the bytes: the readings it stands
    on must be of this very file, or it writes no row (``g6.mcp.improve.start``). Held open, the file keeps its id from any
    other, and on Windows it cannot be renamed or removed until it is let go. A flush is the file's, whoever wrote it, so
    every line in it when this flushes is on disk.
    A stop that waited ``STOP_WAIT`` for ``stop_lock`` writes its line without it, and that line can be read before its own
    flush is done, so a start flushes the journal before its row binds it (``g6.mcp.improve.start``). Journal lines carry
    no token of their own, so a line a crash took back could be written again, the same bytes, by a later stop, and a start
    that bound it would not see that stop. A journal nothing may open to write, a read-only one among them, is held open
    to read and not flushed: no stop can be writing a line to it now, since a stop opens it as this does, and a read-only
    journal refuses no start. Any other failure raises, and nothing starts."""
    if seen.journal_file is None:
        yield None
        return
    path, binary = Path(root) / STOPS_FILE, getattr(os, "O_BINARY", 0)
    try:
        handle, writable = os.open(path, os.O_WRONLY | binary), True
    except PermissionError:
        handle, writable = os.open(path, os.O_RDONLY | binary), False
    try:
        if writable:
            os.fsync(handle)
        opened = os.fstat(handle)
        yield opened.st_dev, opened.st_ino
    finally:
        os.close(handle)


def _establish_stop(root: Path) -> tuple[list[str], str | None, Path]:
    """Use only G6's own id/time. The directory is the independent gate if any record byte or flush fails."""
    denial = _begin_stop(root)
    durable_id = None
    try:
        root = root.absolute()
        _STOP_PENDING[denial] = root
        root = root.resolve()
        _STOP_PENDING[denial] = root
        folder = root / STOP_RECORDS
        folder.mkdir(exist_ok=True)
        ident = uuid4().hex
        record = folder / ident
        record.mkdir()
        data = utf8(json.dumps({"id": ident, "at": datetime.now().astimezone().isoformat()},
                               sort_keys=True, ensure_ascii=True) + "\n")
        descriptor = os.open(record / "stop.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        try:
            offset = 0
            while offset < len(data):
                written = os.write(descriptor, data[offset:])
                if written <= 0:
                    raise OSError("canonical stop write made no progress")
                offset += written
            os.fsync(descriptor)
            durable_id = ident
        finally:
            os.close(descriptor)
        if (record / "stop.json").read_bytes() != data or _canonical_stops_on_disk(root, data) is None:
            raise OSError("the complete canonical stop did not read back by its chosen bytes")
        _finish_stop(denial)
        return [], ident, root
    except BaseException as exc:
        # The reservation directory remains across restarts. A file marker uses a separate creation primitive
        # if directory creation failed; failure of both markers keeps the process gate shut.
        try:
            denied = os.open(root / STOP_DENIED, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(denied)
        except BaseException:
            # Creating children can fail while an existing count file remains appendable. This independent
            # gate writes only a G6-chosen line, never the caller's reason. A partial line is uncertain too.
            try:
                entry = f"G6_E_LOOP_STOP_CANONICAL_FAILED {uuid4().hex} {datetime.now().astimezone().isoformat()}\n"
                _durably(root / STOPS_FILE, entry)        # a row of its own, flushed to disk, as every journal line is
                if utf8(entry) not in (root / STOPS_FILE).read_bytes():
                    raise OSError("the independent count gate did not read back")
            except BaseException:
                pass
        return [f"G6_E_LOOP_STOP_CANONICAL_FAILED: {type(exc).__name__}"], durable_id, root


def stop_entries(reason: str, ident: str) -> tuple[str, str]:
    """The exact LF-terminated STOP entry and counted line for one written identity. Historical reasons and counts
    keep their meaning; the counted line additionally names its write so read-back cannot borrow another stop."""
    said = " ".join(utf8_text(reason).split()) or "stopped through g6_improve_stop"
    ident = utf8_text(ident)
    return f"{said}\n{ident}\n", f"{said[:200]} {ident}\n"


class StopReceipt(str):
    """Why the folder is stopped, as a stop returns it, with what the stop wrote: its canonical identity, the identity
    of its legacy entries, those entries as written, whether its canonical record stands, and what failed."""

    def __new__(cls, text: str, details: list[str], canonical_id: str | None = None,
                *, record_id: str = "", established: bool = False,
                entries: tuple[str, str] | None = None):
        result = super().__new__(cls, text)
        result.details = "; " + "; ".join(details) if details else ""
        result.canonical_id = canonical_id
        result.record_id = record_id
        result.established = established
        result.entries = entries
        return result

    def __str__(self):
        return super().__str__() + self.details


def ask_to_stop(root: str | Path, reason: str = "", *, record_id: str | None = None) -> str:
    """The Python stop API preserves cancellation; its canonical gate already stands before legacy writes."""
    denial = _begin_stop(root)
    try:
        return _ask_to_stop(root, reason, record_id=record_id, managed=False, _denial=denial)
    finally:
        if getattr(denial, "established", False):
            _finish_stop(denial)


def ask_to_stop_receipt(root: str | Path, reason: str = "", *, record_id: str | None = None,
                        _identity: Any = None) -> str:
    """The managed entry returns its own receipt even for cancellation, without retrying or borrowing identity."""
    denial = _begin_stop(root)
    try:
        return _ask_to_stop(root, reason, record_id=record_id, managed=True, _denial=denial, _identity=_identity)
    finally:
        if getattr(denial, "established", False):
            _finish_stop(denial)


def _ask_to_stop(root: str | Path, reason: str = "", *, record_id: str | None, managed: bool, _denial: Any,
                 _identity: Any = None) -> str:
    """Add this stop to the ``STOP`` file, making it if need be, so the first reason stands and repeating is safe. It kills
    nothing: the runner reads the file before each model call and ends itself. It returns why the folder is stopped, as a
    receipt of what it wrote (``StopReceipt``).

    First, an independent directory reserves the canonical id/time stop. It remains a refusal gate if the canonical
    record cannot be completed. The caller's optional identity and reason never enter that establishing record. Only a
    new dialog may lift complete canonical records; uncertain state cannot be lifted. Failures return stable codes on
    the string receipt and leave the gate shut, so a stop whose canonical record stands does not raise for a legacy
    write that failed: the record is the stop, and the receipt names what is missing.

    Each stop writes a token of its own to the ``STOP`` file, so no two stops leave the same bytes, and that is what keeps
    a stop: a start lifts only the file a person agreed to, so it needs no record to know a new stop when it sees one.

    The stop is first a line in the journal of stops, flushed to disk, in a file no lock stands in front of, because the
    records can be held for as long as a tick lasts and a stop may never wait. Every reader honours that line
    (``stop_in_force``), so a stop that dies before its ``STOP`` file is written stands all the same, and the line serves a
    restart after a person has removed the file, when no note of the stop reached the records. A line that cannot be
    written is left: the ``STOP`` file still stops, and only that restart loses by it.

    Both writes are made holding ``stop_lock``, released only once both are on disk, so no start reads a line the disk
    may yet lose. A start holds that lock only while it reads the stop state and writes its row, so a stop waits for it
    ``STOP_WAIT`` at most, and a stop that cannot have it writes all the same: a stop never fails for a lock. A line
    written so can be read before it is on disk, so a start flushes the journal itself before its row binds it
    (``flush_journal``). Both are rows of their own whatever the file ended with, and in whatever words the caller gave
    (``_durably``), so nothing a caller wrote keeps a stop from being written. The canonical record is written under
    that lock too, and a stop whose canonical record does not stand keeps the lock, with its request (``_Held``), so
    every other process's admission meets it (``admission``). What can wait on that lock, the stop's own reading back of
    what it wrote, and what runs outside this module, the caller's allocator of the legacy identity, run with it let go
    or, for the allocator, only once the canonical record stands."""
    root = Path(root)
    with contextlib.ExitStack() as held:
        locked = False
        try:
            if root.is_dir():       # a folder that is not there is not made for a lock: the stop is refused below
                held.enter_context(stop_lock(root, wait=STOP_WAIT))
                locked = True
        except BaseException as exc:  # a stop never fails for a lock, whatever keeps it from it
            if not managed and not isinstance(exc, Exception):
                raise  # raw Python cancellation propagates: the process denial stands, and nothing was established
        errors, canonical_id, root = _establish_stop(root)
        _STOP_PENDING[_denial] = root  # keep the scope captured before any later working-directory change
        if errors:
            if not locked:                          # it could not be had before: once more, now that nothing stands
                try:
                    if root.is_dir():
                        held.enter_context(stop_lock(root, wait=0))
                except BaseException:  # noqa: BLE001 - another holds it, or it cannot be had: the request still denies here
                    pass
            _denial.holds = _Held(held.pop_all())   # no record stands: the lock is kept for as long as this request denies
            return StopReceipt(STOP_STATE_UNREADABLE.replace("stop state is uncertain; ", ""), errors, canonical_id)
        _denial.established = True  # a cancellation may release this latch, never the durable stop
        ident = canonical_id
        try:
            # The managed caller supplies its existing allocator, but no identifier
            # is drawn until the independent canonical stop has read back.
            ident = (utf8_text(record_id) if record_id is not None and str.__len__(record_id)
                     else (_identity if _identity is not None else uuid4)().hex)
            plain = str.__str__(reason)
            stop_entry, count_entry = stop_entries(plain, ident)
            if utf8_text(plain) != plain:
                errors.append("G6_E_LOOP_STOP_REASON_ESCAPED: reason stored escaped for strict UTF-8")
        except BaseException as exc:
            if not managed and not isinstance(exc, Exception):
                raise  # raw Python cancellation propagates after the independent stop stands
            errors.append(f"G6_E_LOOP_STOP_REASON_FAILED: {type(exc).__name__}")
            stop_entry = f"stopped through g6_improve_stop\n{canonical_id}\n"
            count_entry = f"stopped through g6_improve_stop {canonical_id}\n"
            ident = canonical_id
        try:
            _durably(root / STOPS_FILE, count_entry)
        except BaseException as exc:
            if not managed and not isinstance(exc, Exception):
                raise  # raw Python cancellation propagates after the independent stop stands
            errors.append(f"G6_E_LOOP_STOP_COUNT_FAILED: {type(exc).__name__}")
        try:
            _durably(root / STOP_FILE, stop_entry)
        except BaseException as exc:
            if not managed and not isinstance(exc, Exception):
                raise  # raw Python cancellation propagates after the independent stop stands
            errors.append(f"G6_E_LOOP_STOP_WRITE_FAILED: {type(exc).__name__}")
    try:
        why = stop_reason(root) or canonical_stop_reason(root, None)
    except BaseException as exc:
        if not managed and not isinstance(exc, Exception):
            raise  # raw Python cancellation propagates after the independent stop stands
        why = STOP_STATE_UNREADABLE.replace("stop state is uncertain; ", "")
        errors.append(f"G6_E_LOOP_STOP_READ_FAILED: {type(exc).__name__}")
    return StopReceipt(why, errors, canonical_id, record_id=ident, established=True, entries=(stop_entry, count_entry))


def note_refusal(root: str | Path, request: str, said: str) -> bool:
    """Keep what a classifier or a permission refused, first and in an append-only file, because a refused
    request is never sent again whether or not its cycle reaches the log, or a tick is open at all. ``request``: a digest, never
    its text. It is on the disk before this returns ``True``. What was said is written by the one rule every stop path's text
    is (``utf8``): plain UTF-8, a lone surrogate spelled out with a backslash, so every reader reads the file as UTF-8 and no
    caller's words keep the row from being written. Each row stands on a line of its own: after a row a crash cut short, the new one
    begins with a line break, in the same write. One that cannot be written raises nothing and returns ``False``: the caller
    decides what this process does, since only it still holds the refusal. Every synchronous append and fsync holds the
    admission lock; path and text conversion, like the hook calling this writer, precede that lock."""
    try:
        row = utf8(f"{request} {' '.join(said.split())}\n")
        path = Path(root) / REFUSED_FILE
        # Admission excludes lock-waiting readers and caller hooks; its own durable write stays locked through fsync.
        with _REFUSAL_WRITE_LOCK, open(path, "a+b") as kept:
            kept.seek(0, os.SEEK_END)
            if kept.tell():
                kept.seek(-1, os.SEEK_END)
                row = (b"" if kept.read(1) == b"\n" else b"\n") + row
            kept.seek(0, os.SEEK_END)
            kept.write(row)
            kept.flush()
            os.fsync(kept.fileno())
    except (OSError, ValueError):
        return False
    return True


def refused_requests(root: str | Path) -> set[str]:
    """The digest of each request ``note_refusal`` kept (``refusal_rows``)."""
    return {request for request, _ in refusal_rows(root)}


def refusal_rows(root: str | Path) -> list[tuple[str, str]]:
    """Each request ``note_refusal`` kept with what was said of it, from every whole row, before and after any row a crash
    cut short, which is no digest. A file that cannot be read raises as records that do not check out do, since nothing
    could then say what must not be sent."""
    try:
        held = (Path(root) / REFUSED_FILE).read_bytes()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise LedgerError(f"{REFUSED_FILE} cannot be read ({type(exc).__name__})") from exc
    try:
        text = held.decode("utf-8", "surrogatepass")
    except UnicodeDecodeError:                  # a line a crash cut short mid-character: the digests before it still read
        text = held.decode("utf-8", "replace")
    return [(found, said) for found, _, said in (line.partition(" ") for line in text.split("\n"))
            if len(found) == DIGEST_LENGTH]


def record_stop(root: str | Path, reason: str, *, at: str, record_id: str | None = None) -> str:
    """Note a stop in the records, so a start no longer stands once the ``STOP`` file is gone. The file is what stops the
    loop, so this never gets in its way and never raises: records that do not read, or a tick holding them, leave the note
    for later, and a runner notes its own stop as it ends. It returns why no note could be made (``""`` when one was, or
    none was needed), for whoever ends the run to say. The records are absent only by the reading's own rule
    (``_read_file``): a directory or anything else at their path is records that cannot be read, so the note is not made
    and that is said. ``record_id``: the identity the note is written with, for a caller that reads it back by it."""
    root = Path(root)
    path = root / CYCLE_LOG
    try:
        if _read_file(path, root) is None:
            return ""
        with Coordination(root / ".tick").mutex(wait=0):
            log = CycleLog(path)
            if log.start_in_force():
                log.stop(reason, at=at, record_id=record_id)
    except WorkspaceRefused as exc:
        if exc.code == HELD_BY_ANOTHER:
            return "the records are held by a tick, so the stop is not noted in them"
        return f"the records' lock cannot be taken, so the stop is not noted in them ({exc.code})"
    except Exception as exc:  # noqa: BLE001 - whatever keeps the note from being made is said, never raised
        return f"the records or their lock cannot be read, so the stop is not noted in them ({type(exc).__name__})"
    return ""


def end_start(root: str | Path, code: str, why: str, reading: Stop | None = None) -> str:
    """End, durably, a start that a stop ended, when nothing else would: a stop through the stop's own writer
    (``ask_to_stop``, which needs no tick lock), naming ``code``, written beside any stopped row and whatever became of it.
    Every way a start ends on a stop calls this. A stop in force only because a file cannot be read (``STOP_UNREADABLE``)
    is gone with the repair of that file, and a row the records would not take, or a tick held them for, was never there:
    so that one is written, and a later reading is stopped until a person starts again through the dialog. Any other needs
    nothing more: a stop asked for stands in the stop's own readable files or on record, and this process's own
    (``CANNOT_RECORD``) was written through this same writer when it was made (``ShadowLoop.lost_cycle``). ``reading``: the
    one the ending stands on. Records it could parse that show no start left to end, none on record or one a stop on
    record already ended, need nothing either, so ending again on a folder that stays unreadable adds no line once its
    start is ended on record. It never raises: it returns why the stop could not be written (``""`` when it was, or none
    was needed)."""
    if code != STOP_UNREADABLE:
        return ""
    try:
        start, revoked = _start_of(reading.log) if reading is not None and reading.log is not None else ({}, "")
    except Exception:  # noqa: BLE001 - records whose start cannot be read cannot show it ended: the stop is written
        start, revoked = {}, ""
    if start is None or revoked:
        return ""
    try:
        written = ask_to_stop(root, f"{code}: {why}")
    except Exception as exc:  # noqa: BLE001 - whatever keeps the stop from being written is said, never raised
        return f"the stop could not be written to {STOP_FILE} ({type(exc).__name__})"
    details = getattr(written, "details", "")
    if not getattr(written, "established", False):   # no canonical record stands: only this process still refuses
        return f"the stop could not be written to {STOP_RECORDS}{details}"[:300]
    if "G6_E_LOOP_STOP_WRITE_FAILED" in details:     # the canonical record and the journal hold it; its file does not
        return f"the stop could not be written to {STOP_FILE}{details}"[:300]
    return ""


def runner_lock(root: str | Path, wait: float | None = None) -> ContextManager[None]:
    """The lock a runner holds for its whole life; a second runner on the folder is refused it. Only its holder writes the
    folder's page and heartbeat (``g6.metaharness.runner._ending``, ``beat_holding``)."""
    return Coordination(Path(root) / RUNNER_LOCK).mutex(wait=wait)


def runner_alive(root: str | Path) -> bool:
    """Whether a runner holds this folder: only the lock's refusal for another holder says one does. A folder no runner
    ever held has no lock to look at, absent by the reading's own rule (``_absent``), so nothing is written. A lock that is
    there and cannot be tried, for any other reason, raises: nothing then says whether a runner holds it."""
    root = Path(root)
    if _absent(root / RUNNER_LOCK / "mutex", root):
        return False
    try:
        with Coordination(root / RUNNER_LOCK).mutex(wait=0):
            return False
    except WorkspaceRefused as exc:
        if exc.code == HELD_BY_ANOTHER:
            return True
        raise


def beat_holding(root: str | Path, **fields: Any) -> bool:
    """Write the heartbeat holding the runner lock for that moment, so no beat lands on a live runner's: only the lock's
    holder writes the folder's heartbeat. Nothing is written, and ``False`` is returned, when another holds the lock or it
    cannot be taken, however that fails: a beat missed, never an error."""
    try:
        with runner_lock(root, wait=0):
            beat(root, **fields)
        return True
    except Exception:  # noqa: BLE001 - a beat this process cannot write as the lock's holder is a beat missed
        return False


def beat(root: str | Path, **fields: Any) -> None:
    """Write the heartbeat: a reader sees the last whole one, never half of a new one. On Windows a reader holding the
    file refuses the swap for as long as it reads, so the swap is tried a few times, and past that this beat is missed. Its
    text is written as ``utf8`` writes it (``writable``), so no reason it carries keeps it from being written."""
    path = Path(root) / HEARTBEAT_FILE
    spare = path.with_suffix(".tmp")
    try:
        spare.write_bytes(utf8(json.dumps(writable(fields), sort_keys=True, ensure_ascii=False)))
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


__all__ = ["CYCLE_LOG", "CycleLog", "ENDS_A_STREAK", "ENROLMENT_CHANGED", "FAILED", "FINDINGS", "MAX_FAILED_CYCLES",
           "NOTHING_TO_TRY", "REFUSED_FILE", "RUNNER_LOCK", "STOPPED", "STOPS_FILE", "STOP_FILE", "STOP_LOCK",
           "STOP_UNREADABLE", "Stop", "StopSeen", "StopUnreadable", "TICK_BUDGET_SPENT", "admission", "ask_to_stop", "beat",
           "beat_holding", "end_start", "enrolment_changed", "enrolment_digest", "enrolment_id",
           "file_digest", "flush_journal", "fresh_line",
           "heartbeat",
           "note_refusal", "record_stop", "refused_requests", "stop_entries", "stop_in_force", "stop_lock", "stop_seen",
           "stop_standing", "runner_alive", "runner_lock", "stop_reason", "stop_to_lift", "stops_asked", "utf8",
           "what_stops", "writable"]
