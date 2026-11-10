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

**Is a runner alive?** A runner holds an operating-system lock for as long as it lives, so one that crashed, or a machine
that rebooted, never reads as alive, and no process number can be mistaken for it. Looking writes nothing.

**The failed streak.** Three failed cycles in a row pause the loop. A failed cycle is a fault: an error, a sealed breach
or a step that was blocked. The loop seeks failure and learns from it, so a candidate that lost is a result: no winner, no
constraint, a refusal and a rolled-back adoption each end a streak and never pause. What bounds them is the held-out
split, which allows five evaluations before a person acts, and the runner, which rests when nothing is left to try. A tick
cut off at its own reservation is blocked and on the record, and in no streak: the pacing rule is what bounds those.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import time
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, ContextManager
from uuid import uuid4

from g6.core.adaptation_gate import is_automated
from g6.core.ledger import ChainLedger, LedgerError, digest as digest_of
from g6.workspace._coordination import Coordination
from g6.workspace._envelope import WorkspaceRefused

STOP_FILE = "STOP"
STOPS_FILE = "stops.log"
STOP_RECORDS = "stop_records"
STOP_DENIED = "stop.denied"
# A refusal before even the independent directory can be made also holds this process.
_STOP_PENDING: dict[object, str | Path] = {}

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
    """Completion releases only this request, never another request's folder."""
    _STOP_PENDING.pop(token, None)


def _rules(kind: str, ident: str, body: dict, seen: Mapping[tuple[str, str], dict]) -> list[str]:
    if kind == "cycle":
        known = body.get("outcome") in (*FAILED, *ENDS_A_STREAK)
        return [] if known else [f"cycle: unknown outcome {body.get('outcome')!r}"]
    if kind in ("paused", "stopped"):
        return [] if body.get("reason") else [f"a row of kind {kind} states its reason"]
    if kind in ("enrolled", "resumed", "started", "demoted"):
        who = str(body.get("by") or "")
        return [] if who.strip() and not is_automated(who) else [f"{kind}: a person must do this (no empty or auto: name)"]
    if kind == "limited":
        return [] if body.get("provider") and _on_a_clock(body.get("until")) else ["limited: states a provider and when it ends"]
    return [f"unknown kind {kind!r}"]


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
        body = utf8_fields(body)
        self._chain.append(kind, str(len(self._rows()) + 1), body)
        return body

    def _write_record(self, kind: str, body: dict, record_id: str | None = None) -> str:
        """Write the identity with the row, returning it only after the append. A caller may allocate it once before
        writing, so a lost return after an append can still be read back by that identity. Bodies keep their meaning."""
        ident = utf8_text(uuid4().hex if record_id is None else record_id)
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
        return self._write_record("stopped", {"reason": reason, **fields}, record_id)

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


def utf8_text(text: str) -> str:
    """Keep every storable value unchanged; escape only code points strict UTF-8 cannot hold.

    Chunks bound each encoding operation to 4096 code points. There is no truncation: long reasons retain their
    existing meaning. Lone surrogates read back as literal backslash escapes, as the stop reply states.
    """
    text = str.__str__(text)  # use the string's value, without calling Python subclasses' rendering methods
    return "".join(text[i:i + 4096].encode("utf-8", "backslashreplace").decode("utf-8")
                   for i in range(0, len(text), 4096))


def utf8_fields(value: Any) -> Any:
    """The same text policy for names, reasons, notes and nested ledger/reply fields."""
    if isinstance(value, str):
        return utf8_text(value)
    if isinstance(value, dict):
        return {utf8_fields(key): utf8_fields(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [utf8_fields(item) for item in value]
    return value


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
            if held != (json.dumps(value, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii"):
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
    snapshot = canonical_stops(root)
    if snapshot is None:
        return "G6_E_LOOP_STOP_STATE_UNREADABLE: stop state is uncertain; admission is denied"
    if snapshot != (start or {}).get("canonical_stops", ""):
        return "G6_E_LOOP_STOP_CANONICAL: a stop was recorded after this start, so it has ended; a new person's start is required"
    return ""


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
        data = (json.dumps({"id": ident, "at": datetime.now().astimezone().isoformat()},
                           sort_keys=True, ensure_ascii=True) + "\n").encode("ascii")
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
                with open(root / STOPS_FILE, "a", encoding="ascii", newline="\n") as counted:
                    counted.write(entry)
                    counted.flush()
                    os.fsync(counted.fileno())
                if entry.encode("ascii") not in (root / STOPS_FILE).read_bytes():
                    raise OSError("the independent count gate did not read back")
            except BaseException:
                pass
        return [f"G6_E_LOOP_STOP_CANONICAL_FAILED: {type(exc).__name__}"], durable_id, root


def stop_reason(root: str | Path) -> str:
    """Why the ``STOP`` file in the loop's folder halts it, or ``""``. It stands unless it is the very file the last start
    lifted: the same digest, length and modification time, since bytes alone cannot say a person wrote again. What was added
    after those bytes is the stop in force, and any other change leaves the whole file standing. One that cannot be read, or
    that records which cannot be read might have lifted, still stops."""
    path, log = Path(root) / STOP_FILE, Path(root) / CYCLE_LOG
    try:
        written, held = path.stat().st_mtime_ns, path.read_bytes()
    except (FileNotFoundError, NotADirectoryError):
        return ""
    except OSError as exc:
        return _unreadable(exc)
    try:
        start = (CycleLog(log).started() or {}) if log.is_file() else {}
    except (OSError, LedgerError) as exc:
        return _unreadable(exc)
    lifted = start.get("lifted_bytes", 0)
    if start.get("lifted") and hashlib.sha256(held[:lifted]).hexdigest() == start["lifted"]:
        if len(held) > lifted:
            held = held[lifted:]
        elif written == start.get("lifted_mtime_ns"):
            return ""
    text = held.decode("utf-8", "replace").strip()
    return f"a STOP file is present: {text.splitlines()[0]}" if text else "a STOP file is present"


def stop_standing(root: str | Path, start: Mapping[str, Any] | None) -> str:
    """Why this folder is stopped, for whatever runs on ``start``, the start on record (``what_stops``); ``""`` when
    nothing stops it."""
    return what_stops(root, start)[1]


def what_stops(root: str | Path, start: Mapping[str, Any] | None) -> tuple[str, str]:
    """What stops whatever runs on ``start``, the start on record, as a code and a reason, by one rule that a tick, the
    runner and the status all read: its ``STOP`` file, or a stop counted since that start which died before its file was
    written, which is a stop a person asked for all the same (no code of its own: the reader's stop), or an enrolment on
    record that is not the one the start agreed to (``ENROLMENT_CHANGED``, ``enrolment_changed``). A count that cannot be
    read is no count that start saw, so it stands too. ``("", "")`` when nothing stops it."""
    canonical = canonical_stop_reason(root, start)  # admission never depends on rendering a reason
    try:
        why = stop_reason(root)
    except BaseException:
        why = "G6_E_LOOP_STOP_STATE_UNREADABLE: stop state is uncertain; admission is denied"
    why, asked = why, (start or {}).get("stops")
    if not why:
        counted = stops_asked(root)
        if counted < 0 or asked is not None and counted != asked:
            why = f"a stop was asked for and its STOP file was not written: {STOPS_FILE} in the folder says why"
    why = why or canonical
    if why:
        return "", why
    changed = enrolment_changed(root, start)
    return (ENROLMENT_CHANGED, changed) if changed else ("", "")


def enrolment_digest(log: CycleLog | None) -> str:
    """The contents of the enrolment on record, or the digest of none. This persisted digest keeps its meaning;
    ``enrolment_id`` distinguishes separate writes of identical contents."""
    return digest_of((log.enrolled() if log is not None else None) or {})


def enrolment_id(log: CycleLog | None) -> str:
    """The stored identity of the current enrolment. Its body digest remains a digest of its contents alone."""
    return log.latest_id("enrolled") if log is not None else ""


def enrolment_changed(root: str | Path, start: Mapping[str, Any] | None) -> str:
    """Why the enrolment on record is not the one ``start`` agreed to, or ``""``. A start records the enrolment its dialog
    showed, by stored identity and body digest, and runs only that one: a later enrolment, a changed one or none ends the
    start as a stop does, until a person starts it again. A start that names none is the runner's gate's to refuse
    (``g6.metaharness.runner``), and records that do not read are their own rule's: no tick runs on them. It writes
    nothing, so the status can show it."""
    agreed, path = (start or {}).get("enrolment"), Path(root) / CYCLE_LOG
    if agreed is None:
        return ""
    try:
        log = CycleLog(path) if path.is_file() else None
        now = enrolment_digest(log)
    except (OSError, LedgerError):
        return ""
    return "" if now == agreed and (start or {}).get("enrolment_id") == enrolment_id(log) else NOT_THE_AGREED_ENROLMENT


def _unreadable(exc: Exception) -> str:
    return f"a STOP file is present and could not be read ({type(exc).__name__})"


def stop_to_lift(root: str | Path) -> dict[str, Any]:
    """The ``STOP`` file as it stands, for a start to record what a person agreed to lift, or nothing when there is none
    to read. No start moves or deletes the file: this record is all that lifting is."""
    snapshot = canonical_stops(root)
    if snapshot is None:
        raise ValueError("G6_E_LOOP_STOP_STATE_UNREADABLE: uncertain stop state cannot be lifted")
    canonical = {"canonical_stops": snapshot} if snapshot else {}
    path = Path(root) / STOP_FILE
    try:
        written = path.stat().st_mtime_ns     # before the read: a write between them leaves an old time, so the stop stands
        held = path.read_bytes()
    except (FileNotFoundError, NotADirectoryError):
        return canonical
    except OSError:
        return {}  # unreadable legacy stop state gives a new start nothing to lift
    return {**canonical, "lifted": hashlib.sha256(held).hexdigest(), "lifted_bytes": len(held), "lifted_mtime_ns": written}


def file_digest(path: Any) -> str:
    """A file's SHA-256, or ``""`` when there is no file to read: what a person enrolled is pinned by its bytes."""
    try:
        return hashlib.sha256(Path(str(path)).read_bytes()).hexdigest() if path else ""
    except OSError:
        return ""


def stops_asked(root: str | Path) -> int:
    """How many stops have been asked for on this folder, from the file ``ask_to_stop`` adds to. A start records this, so a
    restart sees any stop after it, even with the ``STOP`` file gone. One that cannot be read is ``-1``, which is no count a
    start ever recorded."""
    try:
        held = (Path(root) / STOPS_FILE).read_bytes()
        return -1 if held and not held.endswith(b"\n") else held.count(b"\n")
    except FileNotFoundError:
        return 0
    except OSError:
        return -1


def stop_entries(reason: str, ident: str) -> tuple[str, str]:
    """The exact LF-terminated STOP entry and counted line for one written identity. Historical reasons and counts
    keep their meaning; the counted line additionally names its write so read-back cannot borrow another stop."""
    said = " ".join(utf8_text(reason).split()) or "stopped through g6_improve_stop"
    ident = utf8_text(ident)
    return f"{said}\n{ident}\n", f"{said[:200]} {ident}\n"


class StopReceipt(str):
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
    nothing: the runner reads the file before each model call and ends itself. It returns why the folder is stopped.

    Each stop writes a token of its own there, so no two stops leave the same bytes, and that is what keeps a stop: a start
    lifts only the file a person agreed to. First, an independent directory reserves the canonical id/time stop. It
    remains a refusal gate if the canonical record cannot be completed. The caller's optional identity and reason never
    enter that establishing record. Only a new dialog may lift complete canonical records; uncertain state cannot be
    lifted. Failures return stable codes on the string receipt and leave the gate shut.

    It is also counted before writing the legacy STOP file, in a file no lock stands in front of, because the records can be held for as long as a
    tick lasts and a stop may never wait. The count serves a restart after a person has removed the file, when no note of
    the stop reached the records, and it is all there is of a stop that dies before its file is written, which a tick and a
    runner read as a stop (``stop_standing``). A failed legacy count does not affect the canonical gate."""
    root = Path(root)
    errors, canonical_id, root = _establish_stop(root)
    _STOP_PENDING[_denial] = root  # keep the scope captured before any later working-directory change
    if errors:
        return StopReceipt("G6_E_LOOP_STOP_STATE_UNREADABLE: admission is denied", errors, canonical_id)
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
        with open(Path(root) / STOPS_FILE, "a", encoding="utf-8", newline="\n") as stops:
            stops.write(count_entry)
    except BaseException as exc:
        if not managed and not isinstance(exc, Exception):
            raise  # raw Python cancellation propagates after the independent stop stands
        errors.append(f"G6_E_LOOP_STOP_COUNT_FAILED: {type(exc).__name__}")
    try:
        with open(root / STOP_FILE, "a", encoding="utf-8", newline="\n") as stop:
            stop.write(stop_entry)
    except BaseException as exc:
        if not managed and not isinstance(exc, Exception):
            raise  # raw Python cancellation propagates after the independent stop stands
        errors.append(f"G6_E_LOOP_STOP_WRITE_FAILED: {type(exc).__name__}")
    try:
        why = stop_reason(root) or canonical_stop_reason(root, None)
    except BaseException as exc:
        if not managed and not isinstance(exc, Exception):
            raise  # raw Python cancellation propagates after the independent stop stands
        why = "G6_E_LOOP_STOP_STATE_UNREADABLE: admission is denied"
        errors.append(f"G6_E_LOOP_STOP_READ_FAILED: {type(exc).__name__}")
    return StopReceipt(why, errors, canonical_id, record_id=ident, established=True, entries=(stop_entry, count_entry))


def note_refusal(root: str | Path, request: str, said: str) -> None:
    """Keep what a classifier or a permission refused, first and in a file no lock stands in front of, because a refused
    request is never sent again whether or not its cycle reaches the log. ``request``: a digest, never its text. One that
    cannot be written is left: this process still holds it, and the cycle may yet be written."""
    try:
        with open(Path(root) / REFUSED_FILE, "a", encoding="utf-8") as kept:
            kept.write(f"{request} {utf8_text(' '.join(said.split()))}\n")
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


def record_stop(root: str | Path, reason: str, *, at: str, record_id: str | None = None) -> str | None:
    """Note a stop in the records, so a start no longer stands once the ``STOP`` file is gone. The file is what stops the
    loop, so this never gets in its way: records that do not read, or a tick holding them, leave the note for later, and
    a runner notes its own stop as it ends."""
    path = Path(root) / CYCLE_LOG
    try:
        if not path.is_file():
            return
        with Coordination(Path(root) / ".tick").mutex(wait=0):
            log = CycleLog(path)
            if log.start_in_force():
                return log.stop(utf8_text(reason), at=at, record_id=record_id)
    except BaseException:
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
        spare.write_text(json.dumps(utf8_fields(fields), sort_keys=True, ensure_ascii=False), encoding="utf-8")
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
           "NOTHING_TO_TRY", "REFUSED_FILE", "RUNNER_LOCK", "STOPS_FILE", "STOP_FILE", "TICK_BUDGET_SPENT", "ask_to_stop",
           "beat", "enrolment_changed", "enrolment_digest", "enrolment_id", "file_digest", "heartbeat", "note_refusal", "record_stop",
           "refused_requests", "stop_standing", "runner_alive", "runner_lock", "stop_reason", "stop_to_lift", "stops_asked", "stop_entries",
           "what_stops"]
