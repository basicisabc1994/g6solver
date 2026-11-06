# SPDX-License-Identifier: Apache-2.0
"""Starting, stopping and reading the self-improvement loop over MCP: what the ``g6_improve_*`` tools do.

**Start** is a sensitive operation, allowed as any other is (``metaharness.improve`` or ``metaharness`` in
``G6_MCP_SENSITIVE``), and the person then answers in the client's own dialog (``g6.mcp.consent``). There is no terminal
fallback and no agent answer: a decline, a cancel or a client with no dialog starts nothing. It starts the runner as a
process of its own, which outlives the session that asked, and refuses when a runner is alive, no person has enrolled a
domain in the folder or the stop state cannot be read there (``G6_E_LOOP_STOP_UNREADABLE``). This module never runs the
domain's code: only the runner it starts does, and the dialog names it. Whether a stop is in force is
``stop_in_force``'s answer here as everywhere, and every refusal of a dialog or a start is made from such a reading: an
unreadable one is its code, and any stop in force is named in it (``stop_code``).
An agreement binds what the dialog showed: if any of it moved while the dialog was up, nothing starts and the person is
asked again. The start records the settings it agreed to, and the runner holds to them on every restart, so a changed
settings file needs a new agreement even with no stop in between. It never moves or deletes a ``STOP`` file. It records the
file the person agreed to lift, by digest, length and modification time, and anything else stands, so a stop it was not shown
survives it, whatever became of any count or record.

**Stop** always works and asks nobody: it adds to the local ``STOP`` file, which is safe to repeat and works when the runner
is already dead. It kills nothing and touches no container, so it reports whether the runner has stopped yet. A tick under
way sees the file at its next model call, so a request already sent comes back first and nothing after it goes out. It also
ends the person's start, so nothing but a new agreement brings the runner back, not a restart and not the file's removal.

**Status** only reads: its files, no lock it could hold up and no model. What it shows of each provider's use is the
runner's last heartbeat, with the time of that heartbeat, because opening the ledger would write to it.

A folder left empty is the one beside G6's state folder, ``~/.g6-improve`` by default. Replies carry the library envelope, and
a stop or status never imports the tick, so neither can fail for the reasons a tick can.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from g6.core.door import SENSITIVE_ENV, allowed_sensitive, blocked_reply, is_allowed
from g6.core.ledger import LedgerError, digest as digest_of
from g6.core.storage import g6_home
from g6.metaharness.api import _blocked, _envelope
from g6.self_maintenance import digest
from g6.self_maintenance.envelope import (
    CYCLE_LOG,
    FINDINGS,
    RUNNER_LOCK,
    STOP_FILE,
    STOP_UNREADABLE,
    STOPS_FILE,
    CycleLog,
    Stop,
    ask_to_stop,
    end_start,
    file_digest,
    flush_journal,
    fresh_line,
    heartbeat,
    record_stop,
    runner_alive,
    stop_in_force,
    stop_lock,
)
from g6.self_maintenance.pacing import SETTINGS_FILE, read_settings, windows_from
from g6.workspace._coordination import Coordination

#: What ``G6_MCP_SENSITIVE`` lists for starting the runner: ``metaharness.improve``, or ``metaharness``.
SENSITIVE = ("metaharness", "improve")
WHO = "metaharness.improve"
NOT_A_FOLDER = "G6_E_LOOP_NOT_A_FOLDER"
BAD_SETTINGS = "G6_E_LOOP_BAD_SETTINGS"
RUNNER_ALIVE = "G6_E_LOOP_RUNNER_ALIVE"
NOT_ENROLLED = "G6_E_LOOP_NOT_ENROLLED"
FROZEN_CHANGED = "G6_E_LOOP_FROZEN_CHANGED"
RECORDS_UNREADABLE = "G6_E_LOOP_RECORDS_UNREADABLE"
START_FAILED = "G6_E_LOOP_START_FAILED"
START_STOPPED = "G6_E_LOOP_START_STOPPED"
TERMS_CHANGED = "G6_E_LOOP_TERMS_CHANGED"
STARTED = "G6_E_LOOP_STARTED"
STOP_ASKED = "G6_E_LOOP_STOP_ASKED"
STOP_FAILED = "G6_E_LOOP_STOP_FAILED"
STATUS = "G6_E_LOOP_STATUS"
PAUSED = "G6_E_LOOP_PAUSED"
RUNNER_LOG = "runner.log"
#: How long to watch a new runner open its folder: importing G6 takes seconds.
START_WAIT = 45.0
#: The heartbeats of a runner not yet past opening its folder.
OPENING = ("starting", "not-running")
RECENT = 8
# COPY: Said in every dialog: the name is a claim, and what declining does.
NAME = ("Type your name to agree; it is recorded as you give it and is not authenticated. To refuse, decline: nothing "
        "starts.")
# COPY: What the person allows.
RUNS = ("It runs as a process of its own, which outlives this session, until a STOP file appears: g6_improve_stop writes "
        "one. It uses your subscriptions within the pacing rule, the Python file above runs on this computer as you, and "
        "what it adopts holds in its own workspace only: it pushes nothing and starts no engineer.")
NOT_ASKED = ("Nothing started. The person decided in the client's dialog, or no dialog could be shown: ask the person "
             "whether they want this before calling the tool again, and offer no other way to agree.")
ASK_AGAIN = ("Nothing started and nothing was recorded. Call g6_improve_start again: the person reads what stands now, in "
             "the client's dialog, and agrees to that or not.")


def refused() -> dict | None:
    """The refusal of starting the runner, unless the host allows it."""
    if is_allowed(*SENSITIVE, allowed_sensitive()):
        return None
    return blocked_reply(f"[G6_E_SENSITIVE_COMPONENT_DENIED] {WHO} starts a process that runs on its own; the host must "
                         f"allow it, by adding {WHO} (or metaharness) to {SENSITIVE_ENV} where the server is registered")


def _root(folder: str) -> Path:
    """The folder named, or the default: beside G6's state folder and not in it, since the server, the completion loop and
    test runs all write there, and nothing but the loop writes here. A loop already in the earlier default stays where it
    is, so that a stop with no folder named still reaches its runner."""
    if folder.strip():
        return Path(folder).expanduser().resolve()
    home = g6_home()
    default, earlier = home.with_name(f"{home.name}-improve"), home / "improve"
    return earlier if _is_a_loop(earlier) and not _is_a_loop(default) else default


def _is_a_loop(root: Path) -> bool:
    """Its settings, its records or a runner's lock: a runner must stay within reach with the first two gone."""
    return (root / SETTINGS_FILE).is_file() or (root / CYCLE_LOG).is_file() or (root / RUNNER_LOCK).is_dir()


def _unlooped(root: Path) -> tuple[str, str]:
    """What is said of a folder that is not a loop's: why nothing was done, and what to do."""
    return (f"{root} is not a loop's folder: it has no {SETTINGS_FILE} and no records, so nothing was done",
            f"Name the folder, or create {SETTINGS_FILE} there: see the design's section 11.")


def _not_a_loop(root: Path) -> dict:
    return _blocked(NOT_A_FOLDER, *_unlooped(root))


def _pinned_python(settings: dict[str, Any]) -> str:
    """The interpreter a folder pins its runner to, or ``""`` with none named. A ``ValueError`` says what is wrong with it."""
    if "python" not in settings:
        return ""
    python = settings["python"]
    if not isinstance(python, str) or not Path(python).is_absolute() or not Path(python).is_file():
        raise ValueError(f"{SETTINGS_FILE}: python must be the full path of an interpreter, not {python!r}")
    return python


class _BadSettings(ValueError):
    """The folder's settings cannot be read or used, however that failed: settings to fix (``BAD_SETTINGS``)."""


def _settings(root: Path) -> dict[str, Any] | None:
    """The folder's settings, read and checked, as a dialog shows them and a start records and runs on them, or ``None``
    with no settings file, which is no loop's folder. Settings that cannot be read or used, however that fails, the file's
    own check included, raise ``_BadSettings``, which the one handler of the dialog and the start refuses (``_failed``)."""
    try:
        if not (root / SETTINGS_FILE).is_file():
            return None
        settings = read_settings(root)
        windows_from(settings)
        _pinned_python(settings)
        return settings
    except Exception as exc:  # noqa: BLE001 - settings that cannot be read or used, however, are settings to fix
        raise _BadSettings(str(exc) or type(exc).__name__) from exc


def _refusal(root: Path, reading: Stop, code: str = STOP_UNREADABLE, why: str = "", then: str = "", **fields: Any) -> dict:
    """Every refusal of the dialog and the start, made from the reading it stands on (``stop_in_force``), so none says a stop
    in other words or hides one: when that reading could not be made the refusal is ``G6_E_LOOP_STOP_UNREADABLE``, whatever
    else refused, and otherwise its own ``code``. Either way it names any stop in force (``stop_code``)."""
    if reading.code == STOP_UNREADABLE:
        code, why = STOP_UNREADABLE, f"{reading.reason}; nothing was started"[:400]
        then = (f"A person makes {STOP_FILE}, {STOPS_FILE} and the records in {root} readable again; until then a stop is "
                "in force.")
        fields = {"running": False, "folder": str(root), **fields}
    return _blocked(code, why, then, **fields, **({"stop_code": reading.code} if reading else {}))


def _failed(root: Path, exc: Exception) -> dict:
    """The refusal of a dialog or a start that failed, however it failed, from a reading taken now (``_refusal``): settings
    that cannot be read or used are settings to fix, and anything else is a start that failed."""
    reading = stop_in_force(root)
    if isinstance(exc, _BadSettings):
        return _refusal(root, reading, BAD_SETTINGS, f"{exc}"[:400], f"Fix {SETTINGS_FILE} in {root}; nothing was started.")
    return _refusal(root, reading, START_FAILED, f"the runner could not be started: {type(exc).__name__}: {exc}"[:500],
                    running=False, folder=str(root))


def _cannot_start(root: Path, reading: Stop) -> tuple[dict | None, dict[str, Any]]:
    """Why nothing may start on ``root``, or ``None``, from ``reading``, the one reading of the stop's files and the records
    (``stop_in_force``), and then from the settings, read once (``_settings``), which come back with it: what is checked here
    is what a start records and runs on. This opens none of the reading's files. The reading comes first, so a stop that
    cannot be read is never said as anything else. A stop that can be read is what a start lifts, so it refuses nothing
    here, and every refusal is ``_refusal``'s, which names it, so it is never hidden either. Settings that cannot be read or
    used raise ``_BadSettings``."""
    if reading.code == STOP_UNREADABLE:
        return _refusal(root, reading), {}
    settings = _settings(root)
    if settings is None:
        return _refusal(root, reading, NOT_A_FOLDER, *_unlooped(root)), {}
    if runner_alive(root):
        return _refusal(root, reading, RUNNER_ALIVE, "a runner is already alive on this folder, so nothing was started",
                        "Read g6_improve_status. If you have just stopped it, it ends once it sees the STOP file: at its "
                        "next model call, or within a minute while it waits."), settings
    enrol = (f'A person enrols it at their own terminal: python -P -m g6.metaharness.loop enrol "{root}" --as "Your name". '
             "An agent cannot do that step.")
    enrolled = reading.log.enrolled() if reading.log is not None else None
    if enrolled is None:
        return _refusal(root, reading, NOT_ENROLLED, "no person has enrolled a domain in this folder, so nothing was started",
                        enrol), settings
    if enrolled["frozen"].get("domain_file") != file_digest(settings.get("domain_file")):
        return _refusal(root, reading, FROZEN_CHANGED, "the Python file that declares this domain is not the one a person "
                        "enrolled, so nothing was started and it did not run", enrol), settings
    return None, settings


def _terms(root: Path, settings: dict[str, Any], reading: Stop) -> str:
    """A digest of all a dialog shows or stands on: the whole settings file, the Python file, and ``reading`` whole, its
    digest, which names every byte of the ``STOP`` file, the journal and the records it was read from, so who enrolled,
    any pause, the last start and any stop after it are in it without a field listed. Any write to any of them changes
    it."""
    return digest_of({"settings": settings, "domain_file": file_digest(settings.get("domain_file")),
                      "reading": reading.digest})


#: How many times a dialog is made again when what it reads moves while it is being made.
DIALOG_TRIES = 3


def asked(folder: str) -> tuple[Path, str, str] | dict:
    """What the person reads before the runner starts, the folder it is about, and the terms that binds them to; a
    blocked reply when it cannot start, so no dialog is shown for nothing. Everything it says comes from one reading
    (``stop_in_force``) and the settings, and it is shown only if all it stands on, checked again once it is made, gives
    the same terms: a change while it was made makes it again, and settings or a reading that cannot be used then refuse
    as they would before it. Every statement that touches a file runs inside its one handler, and every refusal is
    ``_refusal``'s."""
    root = Path(folder)                     # the folder as given, for a refusal before it is found
    try:
        root = _root(folder)
        for _ in range(DIALOG_TRIES):
            reading = stop_in_force(root)
            cannot, _ = _cannot_start(root, reading)
            if cannot is not None:
                return cannot
            made = _dialog(root, reading)
            again = stop_in_force(root)
            cannot, settings = _cannot_start(root, again)
            if cannot is not None:
                return cannot
            if _terms(root, settings, again) == made[2]:
                return made
        return _refusal(root, again, TERMS_CHANGED, "what the dialog stands on kept changing while it was made, so nothing "
                        "was asked", ASK_AGAIN, running=False, folder=str(root))
    except Exception as exc:  # noqa: BLE001 - a dialog that fails, however, asks nothing and says why
        return _failed(root, exc)


def _dialog(root: Path, reading: Stop) -> tuple[Path, str, str]:
    """The question and its terms, from ``reading`` and the settings alone, read and checked once (``_settings``)."""
    settings, log, seen, started = _settings(root), reading.log, reading.seen, reading.start
    if settings is None:
        raise _BadSettings(f"{SETTINGS_FILE} is no longer in {root}")
    terms = _terms(root, settings, reading)
    enrolled = log.enrolled()
    use = [f"{w.provider}: {w.allowance:g} a week{' (an estimate)' if w.estimated else ''}, keeping {w.reserve_share:.0%} for "
           "you until the week's last day" for w in windows_from(settings)]
    python = _pinned_python(settings)
    question = [f"Start G6's self-improvement loop on {enrolled.get('domain', 'its domain')}?", f"Folder: {root}",
                f"Python file: {settings.get('domain_file')}, as enrolled by {enrolled['by']} (the name they gave).", *use,
                f"It runs on {python}." if python else ("It runs on the Python of this server, so any edit to that install "
                                                        "stops its ticks until a person enrols again."),
                f"Settings digest: {digest_of(settings)}.", RUNS]
    if seen.by_file(started):                       # what the start would lift is what stands against the last one
        question.append(f"Starting lifts the stop now in place ({seen.by_file(started)}). Its file stays, and any write to it "
                        "after that stops the loop again.")
    elif seen.by_journal(started):
        question.append(f"A stop was asked for since the last start and no STOP file stands for it: {STOPS_FILE} in the folder "
                        "says why. Starting goes past it.")
    if reading.revoked:
        question.append(f"The last start was ended by a stop on record ({reading.revoked}). Starting goes past it."[:300])
    if log.paused():
        question.append(f"The loop is paused, so it will spend nothing until you lift that: {log.paused()}"[:300])
    return root, "\n".join([*question, NAME]), terms


def _spawn(root: Path, settings: dict[str, Any]) -> Any:
    """The runner as a process of its own: no console, its own group, its output in its folder. On the Python the settings the
    person agreed to pin, isolated, since ``-P`` leaves ``PYTHONPATH`` standing and that puts a working tree ahead of any
    install. With none pinned, on this server's Python, as the server's own environment has it. Either way it reads its
    cached bytecode from a folder made empty for it, and writes none, so only the source the envelope pins can run: a
    ``.pyc`` planted under ``__pycache__`` beside a pinned file would run in its place. These are flags, as ``-I`` reads no
    ``PYTHON*`` setting."""
    detached = ({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                else {"start_new_session": True})
    python = _pinned_python(settings)
    cache = tempfile.mkdtemp(prefix="g6-loop-no-bytecode-")
    with open(root / RUNNER_LOG, "ab") as log:
        return subprocess.Popen([python or sys.executable, "-I" if python else "-P", "-B", "-X", f"pycache_prefix={cache}",
                                 "-m", "g6.metaharness.loop", "run", str(root)],
                                cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=log, close_fds=True, **detached)


def _past_opening(root: Path, since: datetime) -> bool:
    """Whether a runner spawned at ``since`` has got past opening its folder. Its lock cannot say: a runner refuses while it
    holds it. Nor can a process number: on Windows a venv's ``python.exe`` is a launcher, and the runner is a child of it."""
    beat = heartbeat(root) or {}
    try:
        return beat.get("state") not in OPENING and datetime.fromisoformat(beat.get("at", "")) >= since
    except (TypeError, ValueError):
        return False


def _watch(root: Path, process: Any, since: datetime) -> dict:
    """Whether the runner spawned at ``since`` got past opening its folder, or ended first, and how. Ended later, it is past
    what this can see: its last heartbeat is what is left."""
    deadline = time.monotonic() + START_WAIT
    while time.monotonic() < deadline:
        code = process.poll()
        if code is not None:
            said = (heartbeat(root) or {}).get("reason") or f"see {RUNNER_LOG} in the folder"
            return _refusal(root, stop_in_force(root), START_FAILED,
                            f"the runner ended as it started, with exit code {code}: {said}"[:500], running=False,
                            return_code=code, folder=str(root))
        if _past_opening(root, since):
            return _envelope("qualified-draft", STARTED, f"the runner is alive on {root}", running=True, folder=str(root),
                             next_action="g6_improve_status reads it; the daily digest is under its folder; "
                                         "g6_improve_stop stops it.")
        time.sleep(0.1)
    return _envelope("qualified-draft", STARTED, f"the runner was started on {root} and has not opened its folder yet",
                     running=False, folder=str(root), next_action="Read g6_improve_status in a minute.")


def _appended_onto(landed: Stop, approved: Stop) -> bool:
    """Whether the records as they stand now are the approved reading's bytes, exactly, and one row after them, started on
    a fresh line by the one rule its writer holds to (``fresh_line``)."""
    added, ending = landed.records[len(approved.records):], fresh_line(approved.records)
    row = added[len(ending):]
    return ("records" not in landed.unread and landed.records.startswith(approved.records) and added.startswith(ending)
            and row.endswith(b"\n") and row.count(b"\n") == 1)


def _revoke(root: Path, writer: CycleLog) -> None:
    """End a start whose row did not land where it was approved: a stop row after it, by the writer that wrote it, and a
    ``STOP`` file when even that will not take, so nothing runs on it either way."""
    why = "the start's row did not land on the records the person's terms bind, so it starts nothing"
    try:
        writer.stop(why, at=datetime.now().astimezone().isoformat())
    except Exception:  # noqa: BLE001 - whatever keeps the row from being written, the STOP file stands in for it
        try:
            ask_to_stop(root, why)
        except Exception:  # noqa: BLE001 - nothing more can be written; the start's caller is refused all the same
            pass


#: Why a start whose records moved under it starts nothing.
MOVED = ("what the person's terms bind changed between the reading they were made on and the start's row, so nothing was "
         "started")


def start(root: Path, who: str, shown: str | None = None) -> dict:
    """Record who started it and start the runner. What the person read may have moved while the dialog was up, so
    ``shown``, the terms ``asked`` gave, must still be what stands, and a change asks them again. The start records what
    it stands on, so a restart holds to the same settings and any stop since closes it. It lifts only the stop state those
    terms cover, by recording it: any other stop stands, and nothing starts. It records and runs on the settings it read
    before its locks, which the terms must match, so a file changed after is one the runner's gate refuses. Under the tick
    lock and ``stop_lock`` it checks everything again on a fresh reading (``stop_in_force``), refuses if that reading is not
    the one the dialog showed, its whole digest being in the terms, and writes its row: no stop can land between the reading and the row, and a start
    refused there writes nothing. The row binds only what is on disk: the start flushes the journal itself first
    (``flush_journal``), since a stop that waited out the lock writes its line without it. The row goes onto the approved
    records themselves: once the writer has opened them and the journal is flushed, a reading must find exactly the
    approved bytes, or no row is written, and the ledger appends only onto the head it opened, so a crash or a failed write
    after the append can leave a start only on approved bytes. A stop that lands after the reading is in nothing the row
    binds, so it stands, and the start says so once its locks are let go. Once the row is written a reading must find those bytes
    with that one row after them, and on every way out of the append, an exception included, anything else that landed is
    revoked by a stop row (a ``STOP`` file when that will not take) and nothing is spawned. Every statement that touches a
    file runs inside its one handler, and every refusal is ``_refusal``'s, made from the reading it stands on: a reading
    that cannot be made, anywhere, starts nothing and says so as any reader does."""
    try:
        cannot, settings = _cannot_start(root, stop_in_force(root))   # the settings it records and runs on
        if cannot is not None:
            return cannot
        with Coordination(root / ".tick").mutex(), stop_lock(root):
            reading = stop_in_force(root)
            cannot, _ = _cannot_start(root, reading)                  # checked again: settings that cannot be used refuse
            if cannot is not None:
                return cannot
            if shown is not None and _terms(root, settings, reading) != shown:
                return _refusal(root, reading, TERMS_CHANGED, "what the person read is no longer what would start, so "
                                "nothing was started", ASK_AGAIN, running=False, folder=str(root))
            writer = CycleLog(root / CYCLE_LOG)
            with flush_journal(root, reading.seen) as flushed:   # on disk, and held open until the row is written
                held = stop_in_force(root)       # once the writer has opened the records and the journal is on disk
                if (held.code == STOP_UNREADABLE or held.records != reading.records   # not the approved bytes, or
                        or flushed is not None                                    # bound lines in a file other than the
                        and not reading.seen.journal_file == flushed == held.seen.journal_file):   # one flushed and held
                    return _refusal(root, held, TERMS_CHANGED, MOVED, ASK_AGAIN, running=False, folder=str(root))
                try:
                    writer.start(who, at=datetime.now().astimezone().isoformat(), settings=digest_of(settings),
                                 **reading.seen.binding())
                except BaseException:            # every way out of the append: whatever landed starts nothing
                    if stop_in_force(root).records != reading.records:
                        _revoke(root, writer)
                    raise
            if not _appended_onto(stop_in_force(root), reading):
                _revoke(root, writer)            # the row did not land on the approved records: it starts nothing
                return _refusal(root, stop_in_force(root), TERMS_CHANGED, MOVED, ASK_AGAIN, running=False,
                                folder=str(root))
        halted = stop_in_force(root)
        if halted:
            record_stop(root, halted.reason, at=datetime.now().astimezone().isoformat())
            end_start(root, halted.code, halted.reason, halted)      # a stop a repair can lift, written beside the note
            return _refusal(root, halted, START_STOPPED, f"a stop arrived while the start was being recorded, so nothing "
                            f"was started: {halted.reason}"[:500], NOT_ASKED, running=False, folder=str(root))
        spawned = datetime.now().astimezone()              # before the spawn: a runner can beat before this process looks
        return {**_watch(root, _spawn(root, settings), spawned), "started_by": who}
    except Exception as exc:  # noqa: BLE001 - a start that fails, however, starts nothing and says why
        return _failed(root, exc)


def _looped(root: Path) -> bool:
    """Whether a stop is written in ``root``: a loop's folder (``_is_a_loop``), or one where that cannot be told, however the
    check fails, since a check that cannot be made never keeps a stop from being written."""
    try:
        return _is_a_loop(root)
    except Exception:  # noqa: BLE001 - a folder that cannot be checked may be a loop's: the stop is written there
        return True


def stop(folder: str, reason: str = "") -> dict:
    """Write the stop and say whether the runner has gone. Every statement that touches a file runs inside its one handler,
    as in the dialog and the start, and a check that cannot be made never keeps the stop from being written: a folder whose
    check fails is taken for a loop's (``_looped``). A folder that cannot be found, or a stop that cannot be written, says
    so (``STOP_FAILED``)."""
    root = Path(folder)                     # the folder as given, for a reply before it is found
    try:
        root = _root(folder)
        if not _looped(root):
            return _not_a_loop(root)
        why = ask_to_stop(root, reason)
        record_stop(root, why, at=datetime.now().astimezone().isoformat())
        running, unread = _running(root)
        said = (f"the STOP file is in place, and whether a runner is alive cannot be read ({unread}): one alive ends once it "
                "sees the STOP file" if running is None else
                "the runner has been asked to stop and is still alive: it ends once it sees the STOP file, at its next model "
                "call, or within a minute while it waits" if running else "the STOP file is in place and no runner is alive")
        return _envelope("qualified-draft", STOP_ASKED, said, running=running, stopped=running is False, stop=why,
                         folder=str(root), next_action="g6_improve_start starts it again, and lifts the stop once the "
                         "person agrees." if running is False else "g6_improve_status says when it has gone.")
    except Exception as exc:  # noqa: BLE001 - a stop that could not be written says so, whatever stopped it
        return _blocked(STOP_FAILED, f"the stop could not be written: {type(exc).__name__}: {exc}"[:400],
                        running=_running(root)[0], folder=str(root))


def _running(root: Path) -> tuple[bool | None, str]:
    """Whether a runner is alive on ``root``, or ``None`` and why when its lock cannot be tried: that is never read as a
    runner alive, nor as none (``runner_alive``)."""
    try:
        return runner_alive(root), ""
    except Exception as exc:  # noqa: BLE001 - a lock that cannot be tried says nothing either way
        return None, f"{type(exc).__name__}: {exc}"[:200]


def _shown(cycle: dict[str, Any]) -> dict[str, Any]:
    return {name: cycle[name] for name in ("tick", "at", "outcome", "code", "reason", "constraint", "head_version", "budget",
                                           *FINDINGS) if name in cycle}


def status(folder: str) -> dict:
    """Where the loop stands, from its files. It writes nothing. The stop comes first: once the folder is found, the one
    reading (``stop_in_force``) is taken before anything else status reads, and every reply reports it. Every statement
    that touches a file runs inside its one handler, so whatever else cannot be read or used, however it fails, a field of
    the records among them, is said beside the stop (``_unread_status``), never raised."""
    root, halted = Path(folder), None      # the folder as given, for a reply before it is found
    try:
        from g6.metaharness.loop import STATUS_LINE
        from g6.metaharness.shadow import lineage

        root = _root(folder)
        if not _is_a_loop(root):
            return _not_a_loop(root)
        halted = stop_in_force(root)       # first: the one reading of the stop's files and the records, opened no more
        beat, running = heartbeat(root) or {}, runner_alive(root)
        cycles, paused, started, seen, unreadable = [], "", None, 0, ""
        log = halted.log
        if log is not None:
            cycles, paused, started, seen = log.cycles(), log.paused(), log.started(), log.generation()
        elif "records" in halted.unread:
            unreadable = f"the loop's records cannot be read or cannot be trusted: {halted.reason}"[:400]
        try:
            generations = lineage(root / "workspace", last=RECENT)
        except LedgerError as exc:
            generations = {"current": None, "recent": []}
            unreadable = unreadable or f"the chain of generations cannot be trusted: {exc}"[:400]
        if generations["current"] is not None and seen > generations["current"]:
            unreadable = unreadable or f"the log saw generation {seen} and the chain of adoptions is at {generations['current']}"
        held = beat.get("pause") if (beat.get("pause") or {}).get("code") != PAUSED else None
        pause = ({"code": PAUSED, "reason": paused} if paused else
                 {"code": RECORDS_UNREADABLE, "reason": unreadable} if unreadable else held)
        named = [c for c in cycles if c.get("constraint")]
        page = digest.latest(root)
        return _envelope("qualified-draft", STATUS, f"a runner is {'alive' if running else 'not alive'} on {root}",
                         running=running, folder=str(root), last_heartbeat=beat or None,
                         use=beat.get("use", []), use_as_of=beat.get("at", ""),
                         constraint={"name": named[-1]["constraint"], "basis": named[-1].get("basis", ""),
                                     "tick": named[-1]["tick"]} if named else None,
                         last_cycle=_shown(cycles[-1]) if cycles else None, cycles=len(cycles), pause=pause,
                         stop=halted.reason, stop_code=halted.code, started=started, generation=generations["current"],
                         lineage=generations["recent"],
                         digest={"path": str(page), "text": page.read_text(encoding="utf-8")} if page else None,
                         g6_status=STATUS_LINE)
    except Exception as exc:  # noqa: BLE001 - whatever status cannot read or use past the stop is said, never raised
        return _unread_status(root, halted, exc)


def _unread_status(root: Path, halted: Stop | None, exc: Exception) -> dict:
    """Status when something it reads past the stop cannot be read or used, however that failed: the stop, from the one
    reading (taken now when none was yet), and what could not be read, said as records that cannot be trusted
    (``RECORDS_UNREADABLE``); nothing else is guessed, whether a runner is alive among it (``None``)."""
    halted = stop_in_force(root) if halted is None else halted
    why = f"status could not read all it reports past the stop: {type(exc).__name__}: {exc}"[:400]
    try:
        from g6.metaharness.loop import STATUS_LINE as said
    except Exception:  # noqa: BLE001 - the status line is the loop's; with no loop to import, none is said
        said = ""
    return _envelope("qualified-draft", STATUS, f"the stop on {root} is read; the rest could not all be read",
                     running=None, folder=str(root), last_heartbeat=None, use=[], use_as_of="", constraint=None,
                     last_cycle=None, cycles=None, pause={"code": RECORDS_UNREADABLE, "reason": why}, stop=halted.reason,
                     stop_code=halted.code, started=None, generation=None, lineage=[], digest=None, g6_status=said)


__all__ = ["NOT_ASKED", "asked", "refused", "start", "status", "stop"]
