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
**Enrol, resume and demote** are a person's too, asked the same way, with the same allow rule and no other way to agree: the
dialog names the folder, the Python file by digest and what the act does. Each act makes its binding once, from one read
of all its dialog shows and it stands on (``g6.metaharness.runner.person_terms``, ``_terms`` for a start), and its dialog
is written from that binding alone (``g6.mcp.consent.question``), so every field it binds is shown. The binding travels
with the act, which reads it again holding the folder's lock, immediately before it runs or records anything, and runs
only the bytes it names. An act runs in a process like the runner's, so an enrolment freezes what a tick on that
interpreter compares, and it records nothing if any of it moved meanwhile. Both start in the loop's folder on this server's
own import path, fixed here with no entry read against where the child stands, and with no path of Python's own, so a
``g6`` in the folder a caller names never runs in place of the server's; the act's binding names the G6 it runs, by
digest, and the act refuses any other.
An agreement binds what the dialog showed: if any of it moved while the dialog was up, nothing starts and the person is
asked again. The start records the settings it agreed to, and the runner holds to them on every restart, so a changed
settings file needs a new agreement even with no stop in between. It records the enrolment it showed too, by digest, and
the runner runs only that one: a later or changed enrolment ends the start as a stop does, and the status shows that stop.
It never moves or deletes a ``STOP`` file. It records the
file the person agreed to lift, by digest, length and modification time, and anything else stands, so a stop it was not shown
survives it, whatever became of any count or record.

**Stop** asks nobody: it reserves a refusal gate and reads back a G6 id/time record before rendering caller text.
Admission reads that record independently of the separately stored reason; uncertain state refuses starts and ticks.
The reply's ``canonical_stop`` reads that gate separately from the legacy ``recorded`` parts. It also adds to the local
``STOP`` file, which is safe to repeat and works when the runner
is already dead. It kills nothing and touches no container, so it reports whether the runner has stopped yet. A tick under
way sees the file at its next model call, so a request already sent comes back first and nothing after it goes out. It also
ends the person's start, so nothing but a new agreement brings the runner back, not a restart and not the file's removal.

**Status** only reads: its files, no lock it could hold up and no model. What it shows of each provider's use is the
runner's last heartbeat, with the time of that heartbeat, because opening the ledger would write to it.

A folder left empty is the one beside G6's state folder, ``~/.g6-improve`` by default. Replies carry the library envelope, and
a stop or status never imports the tick, so neither can fail for the reasons a tick can.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import g6
from g6.core.door import SENSITIVE_ENV, allowed_sensitive, blocked_reply, is_allowed
from g6.core.ledger import LedgerError, digest as digest_of
from g6.core.storage import g6_home
from g6.core.reliability_envelope import envelope_fields, warning_card
from g6.invariants import ERROR_CODE_PATTERN
from g6.mcp.consent import question as _question
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
    StopUnreadable,
    _begin_stop,
    _finish_stop,
    _STOP_PENDING,
    admission,
    ask_to_stop_receipt as ask_to_stop,
    canonical_stop_record,
    canonical_stops,
    end_start,
    enrolment_digest,
    enrolment_id,
    file_digest,
    flush_journal,
    fresh_line,
    heartbeat,
    record_stop,
    runner_alive,
    stop_in_force,
    stop_lock,
    stop_reason,
    stop_entries,
    utf8_text,
    what_stops,
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
NOT_PAUSED = "G6_E_LOOP_NOT_PAUSED"
ACT_FAILED = "G6_E_LOOP_ACT_FAILED"
#: The longest a person's act may take in its process before the reply says to read the status instead. It is not killed.
ACT_WAIT = 300.0
#: COPY: Said in the dialog of a person's act on the folder.
NAME_RECORDED = ("Type your name to agree; it is recorded as you give it and is not authenticated. To refuse, decline: "
                 "nothing is recorded.")
ENROL = "A person enrols it with g6_improve_enrol, which asks them in the client's dialog; an agent cannot answer for them."
CHANGED_SINCE = ("Nothing was recorded. Call the tool again: the person reads what stands now, in the client's dialog, and "
                 "agrees to that or not.")


def refused() -> dict | None:
    """The refusal of starting the runner, unless the host allows it."""
    if is_allowed(*SENSITIVE, allowed_sensitive()):
        return None
    return blocked_reply(f"[G6_E_SENSITIVE_COMPONENT_DENIED] {WHO} starts a process that runs on its own; the host must "
                         f"allow it, by adding {WHO} (or metaharness) to {SENSITIVE_ENV} where the server is registered")


def _root(folder: str, *, stop_request: bool = False) -> Path:
    """The folder named, or the default: beside G6's state folder and not in it, since the server, the completion loop and
    test runs all write there, and nothing but the loop writes here. A loop already in the earlier default stays where it
    is, so that a stop with no folder named still reaches its runner. A stop leaves an unreadable default selection
    unresolved and denied, rather than silently choosing a different folder."""
    if folder.strip():
        try:
            return Path(folder).expanduser().resolve()
        except (OSError, ValueError):
            return Path(folder)  # malformed locators are refused by _is_a_loop without touching their state
    home = g6_home()
    default, earlier = home.with_name(f"{home.name}-improve"), home / "improve"
    return earlier if _is_a_loop(earlier, raise_errors=stop_request) and not _is_a_loop(
        default, raise_errors=stop_request) else default


def _is_a_loop(root: Path, *, raise_errors: bool = False) -> bool:
    """Its settings, its records or a runner's lock: a runner must stay within reach with the first two gone."""
    try:
        return (root / SETTINGS_FILE).is_file() or (root / CYCLE_LOG).is_file() or (root / RUNNER_LOCK).is_dir()
    except (OSError, ValueError):
        if raise_errors:
            raise
        return False


def _unlooped(root: Path) -> tuple[str, str]:
    """What is said of a folder that is not a loop's: why nothing was done, and what to do."""
    root = utf8_text(str(root))
    return (f"{root} is not a loop's folder: it has no {SETTINGS_FILE} and no records, so nothing was done",
            f"Name the folder, or create {SETTINGS_FILE} there: see the design's section 11.")


def _not_a_loop(root: Path) -> dict:
    return _blocked(NOT_A_FOLDER, *_unlooped(root))


def _log(root: Path) -> CycleLog | None:
    """The folder's records, or ``None`` before any: a ``CycleLog`` makes its folder, so it is opened only when there."""
    return CycleLog(root / CYCLE_LOG) if (root / CYCLE_LOG).is_file() else None


def _stands_alone(entry: str, windows: bool = os.name == "nt") -> bool:
    r"""Whether a path names one place wherever a process stands: the one rule for each path a G6 child starts with, its
    interpreter (``_pinned_python``) and its import path (``_import_path``). On Windows only a drive or a UNC share with a
    root does (``C:\x``, ``\\server\share\x``, ``\\?\C:\x``): ``\x`` is read against the drive the process stands on,
    ``C:x`` against its folder on that drive, and an empty entry, ``.`` and any other relative path against its folder.
    Elsewhere only a root does. It is read from ``PureWindowsPath``'s drive and root, which Python 3.12 and 3.13 parse
    alike, never from a predicate whose answer moved between them: ``os.path.isabs`` takes ``\x`` on 3.12, and
    ``PurePath.is_absolute`` takes ``\\server`` on 3.13."""
    if windows:
        path = PureWindowsPath(entry)
        return bool(path.drive and path.root)
    return bool(PurePosixPath(entry).root)


def _pinned_python(settings: dict[str, Any]) -> str:
    """The interpreter a folder pins its runner to, or ``""`` with none named. A ``ValueError`` says what is wrong with it."""
    if "python" not in settings:
        return ""
    python = settings["python"]
    if not isinstance(python, str) or not _stands_alone(python) or not Path(python).is_file():
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


def _refusal(root: Path, reading: Stop, code: str = STOP_UNREADABLE, why: str = "", then: str = "", *,
             read_back: Callable[[dict], dict] | None = None, **fields: Any) -> dict:
    """Every refusal of the dialog and the start, made from the reading it stands on (``stop_in_force``), so none says a stop
    in other words or hides one: when that reading could not be made the refusal is ``G6_E_LOOP_STOP_UNREADABLE``, whatever
    else refused, and otherwise its own ``code``. Either way it names any stop in force (``stop_code``). ``read_back``:
    what a start that had tried to record hands in, so the refusal says what the folder holds of it."""
    if reading.code == STOP_UNREADABLE:
        code, why = STOP_UNREADABLE, f"{reading.reason}; nothing was started"[:400]
        then = (f"A person makes {STOP_FILE}, {STOPS_FILE}, the stop records and the records in {root} readable again; until "
                "then a stop is in force.")
        fields = {"running": False, "folder": str(root), **fields}
    reply = _blocked(code, why, then, **fields, **({"stop_code": reading.code} if reading else {}))
    return reply if read_back is None else read_back(reply)


def _failed(root: Path, exc: Exception, read_back: Callable[[dict], dict] | None = None) -> dict:
    """The refusal of a dialog or a start that failed, however it failed, from a reading taken now (``_refusal``): settings
    that cannot be read or used are settings to fix, and anything else is a start that failed. ``read_back``: what a start
    hands in, so the reply says what the folder holds of the row it had tried to write (``_start_as_recorded``)."""
    reading = stop_in_force(root)
    if isinstance(exc, _BadSettings):
        return _refusal(root, reading, BAD_SETTINGS, f"{exc}"[:400], f"Fix {SETTINGS_FILE} in {root}; nothing was started.",
                        read_back=read_back)
    return _refusal(root, reading, START_FAILED, f"the runner could not be started: {type(exc).__name__}: {exc}"[:500],
                    read_back=read_back, running=False, folder=str(root))


def _cannot_start(root: Path, reading: Stop) -> tuple[dict | None, dict[str, Any]]:
    """Why nothing may start on ``root``, or ``None``, from ``reading``, the one reading of the stop's files and the records
    (``stop_in_force``), and then from the settings, read once (``_settings``), which come back with it: what is checked here
    is what a start records and runs on. This opens none of the reading's files. The reading comes first, so a stop that
    cannot be read, a canonical stop state that is uncertain among it, is never said as anything else. A stop that can be
    read is what a start lifts, so it refuses nothing here, and every refusal is ``_refusal``'s, which names it, so it is
    never hidden either. Settings that cannot be read or used raise ``_BadSettings``."""
    if reading.code == STOP_UNREADABLE:
        return _refusal(root, reading), {}
    settings = _settings(root)
    if settings is None:
        return _refusal(root, reading, NOT_A_FOLDER, *_unlooped(root)), {}
    if runner_alive(root):
        return _refusal(root, reading, RUNNER_ALIVE, "a runner is already alive on this folder, so nothing was started",
                        "Read g6_improve_status. If you have just stopped it, it ends once it sees the STOP file: at its "
                        "next model call, or within a minute while it waits."), settings
    enrolled = reading.log.enrolled() if reading.log is not None else None
    if enrolled is None:
        return _refusal(root, reading, NOT_ENROLLED, "no person has enrolled a domain in this folder, so nothing was started",
                        ENROL), settings
    if enrolled["frozen"].get("domain_file") != file_digest(settings.get("domain_file")):
        return _refusal(root, reading, FROZEN_CHANGED, "the Python file that declares this domain is not the one a person "
                        "enrolled, so nothing was started and it did not run", ENROL), settings
    return None, settings


#: The fields of a start's binding that the start records, and the ``STOP`` file's, as ``stop_to_lift`` names them.
SETTINGS_DIGEST = "Settings digest"
STOPS_COUNTED = "Stops counted"
ENROLMENT = "Enrolment on record"
LIFTS = {"lifted": "STOP file SHA-256", "lifted_bytes": "STOP file bytes", "lifted_mtime_ns": "STOP file modified (ns)"}


class _StartTerms(dict):
    """Public terms plus the same read's raw canonical snapshot; the digest is also visible in the terms."""
    def __init__(self, fields, canonical, active):
        super().__init__(fields)
        self.canonical_stops = canonical
        self.active_stop = active


#: The field of a start's binding that names the whole reading it was made on (``Stop.digest``): every byte of the
#: ``STOP`` file, the journal, the canonical stops and the records.
READING = "Stop state and records digest"


def _terms(root: Path, settings: dict[str, Any] | None = None, reading: Stop | None = None) -> dict[str, Any]:
    """The binding of a start: all its dialog shows and the start stands on, as the fields the dialog shows
    (``g6.mcp.consent.question``), from one reading (``stop_in_force``; ``reading``, or one taken now) and the settings
    (``settings``, or read now). That is the folder, the Python file by path and digest, the enrolment on record, each
    provider's allowance, the interpreter, the whole settings file by digest, the count of stops and the count the last
    start saw, any stop in place with the ``STOP`` file and the canonical stops it would lift (``StopSeen.lift``), any
    pause, and the reading whole, by its digest (``READING``), which names every byte of the ``STOP`` file, the journal,
    the canonical stops and the records it was read from, so any write to any of them changes it. A reading that could
    not be made binds nothing: it raises ``StopUnreadable``."""
    reading = stop_in_force(root) if reading is None else reading
    if reading.code == STOP_UNREADABLE:
        raise StopUnreadable(reading.reason)
    settings = read_settings(root) if settings is None else settings
    log, seen, started = reading.log, reading.seen, reading.start or {}
    enrolled = (log.enrolled() if log is not None else None) or {}
    python = _pinned_python(settings)
    bound: dict[str, Any] = {
        "Folder": str(root), "Domain": str(enrolled.get("domain", "its domain")),
        "Python file": str(settings.get("domain_file")), "Python file SHA-256": file_digest(settings.get("domain_file")),
        "Enrolled by": f"{enrolled.get('by')} (the name they gave)", ENROLMENT: enrolment_digest(log),
        "Enrolment record id": enrolment_id(log),
        "Use": "; ".join(f"{w.provider}: {w.allowance:g} a week{' (an estimate)' if w.estimated else ''}, keeping "
                         f"{w.reserve_share:.0%} for you until the week's last day" for w in windows_from(settings)),
        "Runs on": python or ("the Python of this server, so any edit to that install stops its ticks until a person "
                              "enrols again"),
        SETTINGS_DIGEST: digest_of(settings), STOPS_COUNTED: seen.journal.count(b"\n"),
        "Stops the last start counted": started.get("stops", 0), READING: reading.digest}
    why, standing = seen.by_file(reading.start), seen.lift()
    canonical = standing.pop("canonical_stops", "")
    active = bool(why or seen.by_canonical(reading.start))
    if canonical:
        current = why or ("a canonical stop is in place" if active else "no unlifted stop; canonical history is retained")
        bound["Stop now in place"] = f"{current}; canonical stop records SHA-256: {canonical}"
    elif why:
        bound["Stop now in place"] = why
    bound |= {LIFTS[name]: value for name, value in standing.items()}
    paused = log.paused() if log is not None else ""
    if paused:
        bound["Paused because"] = paused
    return _StartTerms(bound, canonical, active)


#: How many times a dialog is made again when what it reads moves while it is being made.
DIALOG_TRIES = 3


def asked(folder: str) -> tuple[Path, str, dict[str, Any]] | dict:
    """What the person reads before the runner starts, the folder it is about, and the binding it is built from
    (``_terms``); a blocked reply when it cannot start, so no dialog is shown for nothing. Everything it says comes from one
    reading (``stop_in_force``) and the settings, and it is shown only if all it stands on, checked again once it is made,
    gives the same binding: a change while it was made makes it again, and settings or a reading that cannot be used then
    refuse as they would before it. Every statement that touches a file runs inside its one handler, and every refusal is
    ``_refusal``'s."""
    root = Path(folder)                     # the folder as given, for a refusal before it is found
    try:
        root = _root(folder)
        for _ in range(DIALOG_TRIES):
            reading = admission(root, stop_in_force(root))
            cannot, _ = _cannot_start(root, reading)
            if cannot is not None:
                return cannot
            made = _dialog(root, reading)
            again = admission(root, stop_in_force(root))
            cannot, settings = _cannot_start(root, again)
            if cannot is not None:
                return cannot
            if _terms(root, settings, again) == made[2]:
                return made
        return _refusal(root, again, TERMS_CHANGED, "what the dialog stands on kept changing while it was made, so nothing "
                        "was asked", ASK_AGAIN, running=False, folder=str(root))
    except Exception as exc:  # noqa: BLE001 - a dialog that fails, however, asks nothing and says why
        return _failed(root, exc)


def _dialog(root: Path, reading: Stop) -> tuple[Path, str, dict[str, Any]]:
    """The question and its binding, from ``reading`` and the settings alone, read and checked once (``_settings``): the
    dialog is written from that binding by the one function every dialog is (``g6.mcp.consent.question``), so every field
    it binds is shown."""
    settings = _settings(root)
    if settings is None:
        raise _BadSettings(f"{SETTINGS_FILE} is no longer in {root}")
    bound = _terms(root, settings, reading)
    said, seen, started = [RUNS], reading.seen, reading.start
    if seen.by_file(started):                       # what the start would lift is what stands against the last one
        said.append("Starting lifts the stop now in place. Its file stays, and any write to it after that stops the loop "
                    "again.")
    elif seen.by_journal(started):
        said.append(f"A stop was asked for since the last start and no STOP file stands for it: {STOPS_FILE} in the folder "
                    "says why. Starting goes past it.")
    if seen.by_canonical(started):
        said.append("Starting lifts only the canonical stop records shown; any new stop record stops the loop again.")
    if reading.revoked:
        said.append("The last start was ended by a stop on record. Starting goes past it.")
    if "Paused because" in bound:
        said.append("The loop is paused, so it will spend nothing until you lift that.")
    return root, _question(f"Start G6's self-improvement loop on {bound['Domain']}?", bound, said, close=NAME), bound


def _spawn(root: Path, settings: dict[str, Any]) -> Any:
    """The runner on the agreed interpreter, isolated, with G6's import roots as arguments and the one checked child
    environment. The empty bytecode cache and no-write flag keep source edits visible on every start."""
    detached = ({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                else {"start_new_session": True})
    with open(root / RUNNER_LOG, "ab") as log:
        return subprocess.Popen([*_interpreter(settings), "-m", "g6.metaharness.loop", "run", str(root)],
                                cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=log, close_fds=True,
                                env=_environment(), **detached)


# The only bootstrap for both launchers. Python has completed isolated startup before it reads these roots. They are
# a JSON list argument, never a path-list string. Insert them after the standard library and before installed packages;
# even a server installed in a site folder must not put that folder ahead of the standard library.
_BOOTSTRAP = (
    "import sys, json, runpy, site; "
    "roots = json.loads(sys.argv.pop(1)); "
    "at = next((i for i, p in enumerate(sys.path) if p in site.getsitepackages()), len(sys.path)); "
    "sys.path[at:at] = roots; "
    "mode = sys.argv.pop(1); target = sys.argv.pop(1); sys.argv[0] = target; "
    "runpy.run_module(target, run_name='__main__', alter_sys=True) if mode == '-m' "
    "else exec(compile(target, '<g6 child>', 'exec'))"
)


def _interpreter(settings: dict[str, Any]) -> list[str]:
    """Both loop children start isolated. A pinned interpreter keeps its own installed G6; otherwise the roots name
    the G6 this server loaded. Startup settings never come from the parent environment."""
    python = _pinned_python(settings)
    cache = tempfile.mkdtemp(prefix="g6-loop-no-bytecode-")
    roots = [] if python else _import_path()
    return [python or sys.executable, "-I", "-B", "-X", f"pycache_prefix={cache}",
            "-c", _BOOTSTRAP, json.dumps(roots)]


def _environment_path(value: str, *, must_exist: bool = True) -> str | None:
    """A local directory named independently of the child's working folder, or nothing. No network lookup, control
    character or missing directory is admitted. The parent fixes links before the child starts."""
    if not value or any(ord(char) < 32 for char in value) or not _stands_alone(value):
        return None
    if os.name == "nt" and PureWindowsPath(value).drive.startswith("\\\\"):
        return None
    try:
        path = Path(value).resolve()
        return str(path) if not must_exist or path.is_dir() else None
    except (OSError, ValueError):
        return None


def _environment() -> dict[str, str]:
    """Build the whole loop-child environment from an allow-list. Each inherited name is a checked local directory:
    system folders for Windows startup, home/config folders for host tools, and temporary folders. G6 sets its own
    absolute state root and search path, with its interpreter directory first. No Python setting or unlisted variable
    is inherited. Provider secrets and arbitrary host settings must not become startup authority."""
    env: dict[str, str] = {}
    for name in ("SYSTEMROOT", "WINDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP"):
        value = _environment_path(os.environ.get(name, ""))
        if value is not None:
            env[name] = value
    home = _environment_path(str(g6_home().absolute()), must_exist=False)
    if home is None:
        raise ValueError("G6_E_LOOP_CHILD_ENV: the state root must be an absolute local path")
    env["G6_HOME"] = home
    temp = _environment_path(tempfile.gettempdir())
    if temp is None:
        raise ValueError("G6_E_LOOP_CHILD_ENV: no absolute temporary directory")
    env.setdefault("TEMP", temp)
    env.setdefault("TMP", temp)
    paths = [str(Path(sys.executable).parent), *os.environ.get("PATH", os.defpath).split(os.pathsep)]
    checked = [_environment_path(entry) for entry in paths]
    env["PATH"] = os.pathsep.join(dict.fromkeys(entry for entry in checked if entry is not None and os.pathsep not in entry))
    return env


def _import_path() -> list[str]:
    """The exact absolute sys.path root of the server's G6, passed as a list argument after isolated startup. Parent import
    settings supply no extra roots, and a separator in this folder's name stays inside this one entry."""
    home = str(Path(g6.__file__).resolve().parent.parent)
    if not _stands_alone(home):
        raise ValueError("G6_E_LOOP_IMPORT_PATH: the server's G6 root is not absolute")
    return [home]


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


def _still_read(root: Path, shown: dict[str, Any], reading: Stop | None = None) -> bool:
    """Whether what a person read before a start (``_terms``) still stands, on ``reading`` or on one taken now: a folder
    that no longer reads has moved."""
    try:
        return _terms(root, None, reading) == shown
    except Exception:  # noqa: BLE001 - a binding that cannot be made again, however, is one that moved
        return False


def _appended_onto(landed: Stop, approved: Stop) -> bool:
    """Whether the records as they stand now are the approved reading's bytes, exactly, and one row after them, started on
    a fresh line by the one rule its writer holds to (``fresh_line``)."""
    added, ending = landed.records[len(approved.records):], fresh_line(approved.records)
    row = added[len(ending):]
    return ("records" not in landed.unread and landed.records.startswith(approved.records) and added.startswith(ending)
            and row.endswith(b"\n") and row.count(b"\n") == 1)


def _revoke(root: Path, writer: CycleLog, waits: bool = True) -> bool:
    """End a start whose row did not land where it was approved, or that a reading taken after its row found stopped: a
    stop row after it, by the writer that wrote it, and a stop through the stop's own writer when even that will not take,
    so nothing runs on it either way. It returns whether the row was written. ``waits``: whether the stop's own writer
    may be called, which waits on the stop lock: under the start's locks it may not, so there only the row is written,
    durably, and the start calls this again once its locks are let go if the row would not take."""
    why = "the start's row did not land on the records the person's terms bind, or a stop met it, so it starts nothing"
    try:
        writer.stop(why, at=datetime.now().astimezone().isoformat())
        return True
    except Exception:  # noqa: BLE001 - whatever keeps the row from being written, the stop's own writer stands in for it
        if waits:
            try:
                ask_to_stop(root, why)
            except Exception:  # noqa: BLE001 - nothing more can be written; the start's caller is refused all the same
                pass
        return False


#: Why a start whose records moved under it starts nothing.
MOVED = ("what the person's terms bind changed between the reading they were made on and the start's row, so nothing was "
         "started")
#: Why a start whose binding is no longer what the person read starts nothing.
NOT_AS_READ = "what the person read is no longer what would start, so nothing was started"


def start(root: Path, who: str, shown: dict[str, Any] | None = None) -> dict:
    """Record who started it and start the runner, on the binding the person agreed to: ``shown``, which ``asked`` built
    the dialog from, or with none (a Python caller, with no dialog), what stands now, recorded as read. What the person
    read may have moved while the dialog was up, so the start reads it again first, before anything else it checks but a
    stop that cannot be read, so a change to what they read is named as one (``TERMS_CHANGED``) whatever else changed
    with it, and a change asks them again. The start records what it stands on, so a restart holds to the same settings
    and enrolment and any stop since closes it. It lifts only the stop state that binding covers, by recording it: any
    other stop stands, and nothing starts. It records and runs on the settings it read before its locks, which the binding
    must match, so a file changed after is one the runner's gate refuses. Under the tick lock and ``stop_lock`` it checks
    everything again on a fresh reading (``stop_in_force``), refuses if that reading is not the one the dialog showed, its
    whole digest being in the binding, and writes its row: no stop can land between the reading and the row, and a start
    refused there writes nothing. The row binds only what is on disk: the start flushes the journal itself first
    (``flush_journal``), since a stop that waited out the lock writes its line without it. The row goes onto the approved
    records themselves: once the writer has opened them and the journal is flushed, a reading must find exactly the
    approved bytes, or no row is written, and the ledger appends only onto the head it opened, so a crash or a failed write
    after the append can leave a start only on approved bytes. A stop that lands after the reading is in nothing the row
    binds, so it stands, and the start says so once its locks are let go. Once the row is written every reading the start
    takes is decided whole: the reading of what landed, under both locks, must find those bytes with that one row after
    them and no stop in force, of any code, one that cannot be read among them; anything else is revoked by a stop row
    written under the locks (the stop's own writer, which waits on the stop lock, once they are let go), the start is
    refused on that reading itself, and nothing is spawned, whatever is repaired a moment later. So it is on every way
    out of the append, an exception included, and for the reading taken once the locks are let go (``admission``). Every statement that
    touches a file runs inside its one handler, and every refusal is ``_refusal``'s, made from the reading it stands on: a
    reading that cannot be made, anywhere, starts nothing and says so as any reader does. Once it has tried to record,
    its reply says what the folder holds of that start, read back (``_start_as_recorded``), whichever step failed."""
    ident, ended, fits, noted = "", None, True, True   # the row's identity once it tries to write; what ended it after
    try:
        who = utf8_text(who)
        reading = admission(root, stop_in_force(root))
        if shown is not None and reading.code != STOP_UNREADABLE and not _still_read(root, shown, reading):
            return _refusal(root, reading, TERMS_CHANGED, NOT_AS_READ, ASK_AGAIN, running=False, folder=str(root))
        cannot, settings = _cannot_start(root, reading)                # the settings it records and runs on
        if cannot is not None and shown is not None and reading.code != STOP_UNREADABLE and not _still_read(root, shown):
            cannot = _refusal(root, reading, TERMS_CHANGED, NOT_AS_READ, ASK_AGAIN, running=False, folder=str(root))
        if cannot is not None:                   # a binding that moved meanwhile is named as one, whatever else refused
            return cannot
        with Coordination(root / ".tick").mutex(), stop_lock(root):
            reading = stop_in_force(root)
            cannot, _ = _cannot_start(root, reading)                  # checked again: settings that cannot be used refuse
            if cannot is not None:
                return cannot
            if shown is not None and _terms(root, settings, reading) != shown:
                return _refusal(root, reading, TERMS_CHANGED, NOT_AS_READ, ASK_AGAIN, running=False, folder=str(root))
            writer = CycleLog(root / CYCLE_LOG)
            with flush_journal(root, reading.seen) as flushed:   # on disk, and held open until the row is written
                held = stop_in_force(root)       # once the writer has opened the records and the journal is on disk
                if (held.code == STOP_UNREADABLE or held.records != reading.records   # not the approved bytes, or
                        or flushed is not None                                    # bound lines in a file other than the
                        and not reading.seen.journal_file == flushed == held.seen.journal_file):   # one flushed and held
                    return _refusal(root, held, TERMS_CHANGED, MOVED, ASK_AGAIN, running=False, folder=str(root))
                ident = uuid4().hex              # the row's identity, separate from its time
                try:
                    writer.start(who, record_id=ident, at=datetime.now().astimezone().isoformat(),
                                 settings=digest_of(settings), **reading.seen.binding())
                except BaseException:            # every way out of the append: whatever landed starts nothing
                    failed = stop_in_force(root)
                    if failed.code == STOP_UNREADABLE or failed.records != reading.records:
                        _revoke(root, writer, False)
                    raise
            landed = stop_in_force(root)         # one reading of what landed, decided whole, under both locks
            fits = _appended_onto(landed, reading)
            if landed or not fits:               # a stop of any code in it, or a row not on the approved records
                noted = _revoke(root, writer, False)             # the row that revokes it, durably, under the locks
                ended = landed
        if ended is not None:                    # refused on that reading itself, whatever is repaired after it
            if not noted:
                _revoke(root, writer)            # the stop's own writer waits on the stop lock: only with it let go
            end_start(root, ended.code, ended.reason, ended)         # and a stop a repair can lift is written beside it
            code, why, then = ((TERMS_CHANGED, MOVED, ASK_AGAIN) if not fits else
                               (START_STOPPED, f"a stop met the start as it was being recorded, so nothing was started: "
                                f"{ended.reason}"[:500], NOT_ASKED))
            now = stop_in_force(root)            # the revocation is in it: a refusal names the stop in force
            return _start_as_recorded(root, ident, _refusal(root, ended if ended or not now else now, code, why, then,
                                                            running=False, folder=str(root)))
        halted = admission(root, stop_in_force(root))
        if halted:
            record_stop(root, halted.reason, at=datetime.now().astimezone().isoformat())
            end_start(root, halted.code, halted.reason, halted)      # a stop a repair can lift, written beside the note
            return _start_as_recorded(root, ident, _refusal(
                root, halted, START_STOPPED, f"a stop arrived while the start was being recorded, so nothing was started: "
                f"{halted.reason}"[:500], NOT_ASKED, running=False, folder=str(root)))
        spawned = datetime.now().astimezone()              # before the spawn: a runner can beat before this process looks
        return _start_as_recorded(root, ident, {**_watch(root, _spawn(root, settings), spawned), "started_by": who})
    except Exception as exc:  # noqa: BLE001 - a start that fails, however, starts nothing and says why
        return _failed(root, exc, lambda reply: _start_as_recorded(root, ident, reply))


#: The keys every reply's envelope carries, beside what a reply adds of its own.
_ENVELOPE_KEYS = frozenset(_blocked("G6_E_LOOP_STATUS", "", "-")) | {"recorded"}


def _restated(reply: dict[str, Any], message: str, next_action: str, **more: Any) -> dict[str, Any]:
    """``reply`` with its message and next action said again, its state, code and own fields kept."""
    own = {name: value for name, value in reply.items() if name not in _ENVELOPE_KEYS}
    return _envelope(reply["completion_state"], reply["warning_card"]["code"], message[:500], next_action=next_action,
                     **own, **more)


def _start_on_record(root: Path, ident: str) -> dict[str, bool]:
    """Whether the start written with ``ident`` stands, read back afresh: the start on record is that one, it is in
    force, and nothing stops it by G6's own rule (``stop_standing``). ``OSError`` or ``LedgerError`` when the records do
    not read."""
    found = stop_in_force(root)
    if "records" in found.unread:
        raise LedgerError(found.reason)
    log = found.log
    ours = (log is not None and log.start_in_force() and log.record("started", ident) is not None
            and log.latest_id("started") == ident)
    return {"the start": bool(ours) and not found}


def _start_as_recorded(root: Path, ident: str, reply: dict[str, Any]) -> dict[str, Any]:
    """The reply to a start that tried to record, as the folder holds it now: ``recorded`` says whether the start written
    with ``ident`` stands, read back (``_start_on_record``). One on record whose runner did not start says so, since the next
    runner on the folder carries on it until a stop; records that do not read say that, and claim nothing either way. A
    start that never tried to record (no ``ident``) is its reply as it is."""
    if not ident or not isinstance(reply.get("warning_card"), dict):
        return reply
    try:
        found = _start_on_record(root, ident)
    except (OSError, LedgerError) as exc:
        if reply["warning_card"]["code"] == STARTED:
            return {**reply, "recorded": None}
        return _restated(reply, f"{reply['message']}; the folder's records do not read back ({type(exc).__name__}), so "
                         "whether the start is on record is not known", "g6_improve_status says what the folder's "
                         "records hold.", recorded=None)
    except Exception:  # noqa: BLE001 - a read-back that fails, however, claims nothing and keeps the reply
        return {**reply, "recorded": None}
    if found["the start"] and reply["warning_card"]["code"] != STARTED:
        return _restated(reply, f"{reply['message']}. The start is on record, so the next runner on this folder carries on "
                         "it until a stop", "g6_improve_stop ends the start; g6_improve_status shows it.", recorded=found)
    return {**reply, "recorded": found}


def _cannot_act(act_name: str, root: Path, generation: int | None) -> dict | None:
    """Why a person's act cannot be put to them on this folder as it stands, so no dialog is shown for nothing. It runs none
    of the folder's code."""
    from g6.metaharness.runner import held
    from g6.metaharness.shadow import NOT_DEMOTABLE, lineage, restores

    if not (root / SETTINGS_FILE).is_file():
        return _not_a_loop(root)
    try:
        settings = read_settings(root)
        windows_from(settings)
        _pinned_python(settings)
        path = settings.get("domain_file")
        if not path or not Path(str(path)).is_file():
            raise ValueError(f"{SETTINGS_FILE} needs domain_file: the Python file that defines LOOP, not {path!r}")
    except ValueError as exc:
        return _blocked(BAD_SETTINGS, f"{exc}"[:400], f"Fix {SETTINGS_FILE} in {root}; nothing was asked or recorded.")
    try:
        log = _log(root)
        current = lineage(root / "workspace")["current"] if act_name == "demote" else 0
        restored = restores(root / "workspace") if act_name == "demote" else None
    except LedgerError as exc:
        return _blocked(RECORDS_UNREADABLE, f"the loop's records cannot be trusted: {exc}"[:400],
                        "A person inspects the folder; nothing was asked or recorded.")
    if act_name == "enrol":
        return None
    if log is None or log.enrolled() is None:
        return _blocked(NOT_ENROLLED, "no person has enrolled a domain in this folder, so there is nothing to act on", ENROL)
    if log.enrolled()["frozen"].get("domain_file") != file_digest(settings.get("domain_file")):
        return _blocked(FROZEN_CHANGED, "the Python file that declares this domain is not the one a person enrolled, so "
                        "nothing was asked and it did not run", ENROL)
    if act_name == "resume" and not held(log):
        return _blocked(NOT_PAUSED, "the loop is not paused, so there is nothing to lift", "g6_improve_status says where "
                        "it stands.")
    if act_name == "demote" and generation != current:
        return _blocked(NOT_DEMOTABLE, f"the head is generation {current}, not {generation}, so nothing was asked",
                        "Read g6_improve_status and name the generation that stands.")
    if act_name == "demote" and restored is None:
        return _blocked(NOT_DEMOTABLE, f"generation {current} stands for what a person confirmed: nothing adopted stands to "
                        "take back, so nothing was asked", "g6_improve_status shows the lineage.")
    return None


#: COPY: What each person's act does, said in its dialog after the fields it binds.
DOES = {
    "enrol": ["The Python file runs on this computer as you: now, to read what it declares, and in every tick once the loop "
              "is started.",
              "Enrolling freezes what judges this domain as the file declares it now: its judge, its cases and its held-out "
              "split. Every change the loop tries is measured against them, and only a new enrolment changes them. It "
              "starts nothing: g6_improve_start asks for that."],
    "resume": ["Lifting it lets the ticks run again within the pacing rule, from the generation that stands. A stop in "
               "place stays: only g6_improve_start lifts one.",
               "The Python file runs on this computer as you, to read what it declares."],
    "demote": ["The loop goes back to the profile of the generation named above and runs on from there; the change can be "
               "proposed again.",
               "The Python file runs on this computer as you, to read what it declares."],
}


def asked_act(act_name: str, folder: str, generation: int | None = None) -> tuple[Path, str, dict[str, Any]] | dict:
    """What the person reads before ``act_name`` (``enrol``, ``resume`` or ``demote``), the folder, and the binding it is
    built from (``g6.metaharness.runner.person_terms``), read once; a blocked reply when it cannot be put to them. The
    folder is read again before the dialog is shown, and a binding that moved meanwhile asks nothing."""
    from g6.metaharness.runner import moved, person_terms

    root = _root(folder)
    cannot = _cannot_act(act_name, root, generation)
    if cannot is not None:
        return cannot
    try:
        bound = person_terms(act_name, root, generation=generation)
        asks = {"enrol": "Enrol a domain for G6's self-improvement loop?",
                "resume": f"Lift the pause on G6's self-improvement loop on {bound.get('Domain')}?",
                "demote": f"Take generation {generation} of G6's self-improvement loop on {bound.get('Domain')} back?"}
        shown = _question(asks[act_name], bound, DOES[act_name], close=NAME_RECORDED)
        if moved(act_name, root, bound, generation=generation):
            return _blocked(TERMS_CHANGED, "the folder changed while its question was being read, so nothing was asked or "
                            "recorded", CHANGED_SINCE, folder=str(root))
    except (ValueError, LedgerError) as exc:
        return _blocked(RECORDS_UNREADABLE, f"the loop's records cannot be trusted: {exc}"[:400],
                        "A person inspects the folder; nothing was asked or recorded.")
    return root, shown, bound


def act(act_name: str, root: Path, who: str, shown: dict[str, Any], generation: int | None = None) -> dict:
    """Do what the person agreed to, on the binding their dialog was built from (``shown``), if it still stands: in a
    process like the runner's (``_run_act``), which reads it again under the folder's lock before it runs or records
    anything, and runs only the bytes it names. What they agreed to is compared first (``runner.moved``), so any change
    to what they read is ``TERMS_CHANGED`` and nothing is recorded, whatever else changed with it; only then is the folder
    checked for what the binding does not cover (``_cannot_act``)."""
    from g6.metaharness.runner import SETTINGS_DIGEST as BOUND_SETTINGS, moved

    who = utf8_text(who)
    changed = moved(act_name, root, shown, generation=generation)
    try:
        settings = read_settings(root)
    except ValueError:
        changed = changed or [f"{SETTINGS_FILE}, which no longer reads"]
    else:
        if not changed and digest_of(settings) != shown.get(BOUND_SETTINGS):
            changed = [BOUND_SETTINGS]
    if changed:
        return _blocked(TERMS_CHANGED, f"what the person read is no longer what stands ({', '.join(changed)}), so nothing "
                        "ran or was recorded"[:500], CHANGED_SINCE, folder=str(root))
    cannot = _cannot_act(act_name, root, generation)
    if cannot is not None:
        return cannot
    request = {"act": act_name, "folder": str(root), "who": who, "binding": shown, "generation": generation}
    return {**_run_act(root, settings, request), "by": who}


def _run_act(root: Path, settings: dict[str, Any], request: dict[str, Any]) -> dict:
    """The act in its own process, on the interpreter and the import path the runner uses (``_interpreter``,
    ``_environment``): the request on its input, its reply as the last line of its output. It never shares this server's
    input or output, and one that outlives ``ACT_WAIT`` is left to finish. Starting the process and talking to it fail
    apart. Only a process that never started may say nothing was recorded, and only when the folder, read back, agrees
    (``_read_back``). Any failure once it started (the pipe, its output, the wait, a timeout) goes to the one reader
    (``_read_act``), which reads the folder back as it does for a reply it does not recognise."""
    hidden = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    if request.get("act") == "enrol":
        request.setdefault("record_id", uuid4().hex)
    command = [*_interpreter(settings), "-c", "import sys; from g6.metaharness.runner import act_main; sys.exit(act_main())"]
    try:
        process = subprocess.Popen(command, cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", errors="replace", close_fds=True, env=_environment(),
                                   **hidden)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        held, read = _read_back(request, ended=True)       # it never started, so nothing of it is still to come
        agrees = held is not None and not any(held.values())
        return _blocked(ACT_FAILED, f"the act's process could not be started ({type(exc).__name__}: {exc})"
                        f"{', so nothing of it ran' if agrees else ''}; {read}"[:500], "Nothing was recorded." if agrees else
                        "g6_improve_status says what the folder's records hold.", folder=str(root), recorded=held)
    out, err, failed = "", "", ""
    try:
        out, err = process.communicate(json.dumps(request), timeout=ACT_WAIT)
    except subprocess.TimeoutExpired:
        failed = f"the act did not finish in {ACT_WAIT:g} seconds and was left to finish"
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        failed = f"talking to the act's process failed once it had started ({type(exc).__name__}: {exc})"
    return _read_act(process.returncode, out, err, act_name=request.get("act"), request=request, failed=failed)


def _on_record(request: dict[str, Any]) -> dict[str, bool]:
    """What the folder holds of a person's act, read back (``g6.metaharness.runner.on_record``)."""
    from g6.metaharness.runner import on_record

    return on_record(request)


def _be(parts: list[str]) -> str:
    return "is" if len(parts) == 1 else "are"


def _record_said(held: dict[str, bool | None]) -> str:
    """The sentence that says what a read-back found (``held``): what is on record, what is not, and what is not known.
    Nothing here decides a part: each is as it was read."""
    on = [part for part, kept in held.items() if kept]
    off = [part for part, kept in held.items() if kept is False]
    unknown = [part for part, kept in held.items() if kept is None]
    if not on and not unknown:
        return "read back from its folder, none of it is on record"
    said = [f"{', '.join(on)} {_be(on)} on record"] if on else []
    said += [f"{', '.join(off)} {_be(off)} not" + ("" if on else " on record")] if off else []
    said += [f"whether {', '.join(unknown)} {_be(unknown)} on record is not known"] if unknown else []
    return "read back from its folder, " + (f"{', '.join(said[:-1])} and {said[-1]}" if len(said) > 1 else said[0])


def _read_back(request: dict[str, Any], *, ended: bool) -> tuple[dict[str, bool | None] | None, str]:
    """What the folder holds of a person's act, read back afresh (``_on_record``), and the sentence that says it. While its
    process may still be running (``ended`` false: no exit status was read), a part not on record yet may still be
    written, so it is not known. Records that do not read back are not known at all. It does not raise."""
    try:
        found = _on_record(request)
    except (OSError, ValueError, LedgerError, KeyError, TypeError) as exc:
        return None, f"its folder's records do not read back ({type(exc).__name__}), so what of it counts is not known"
    held = {part: True if kept else False if ended else None for part, kept in found.items()}
    said = _record_said(held)
    return held, said + (": its process may still be running" if None in held.values() else "")


#: What each person's act replies when it did what it was asked, and nothing else does.
ACT_DONE = {"enrol": "G6_E_LOOP_ENROLLED", "resume": "G6_E_LOOP_RESUMED", "demote": "G6_E_LOOP_DEMOTED"}
#: The shape of a person's act's reply as ``g6.metaharness.api._envelope`` writes it: each field the one reader reads or
#: passes on as the envelope every surface carries, with the JSON type it must have, and the card's. ``completion_state``,
#: the card and its ``code`` must be there; any other must have its type when it is there, and a ``code`` or a
#: ``reliability_label`` beside them must say the same. The card's code must be a G6 code by G6's own rule, its envelope
#: checker's (``g6.invariants.ERROR_CODE_PATTERN``, matched as ``check_envelope`` matches it). A reply is recognised by
#: this shape alone; anything else in it is passed on as it is and read nowhere.
REPLY_SHAPE: dict[str, type] = {"completion_state": str, "reliability_label": str, "warning_card": dict, "code": str,
                                "message": str, "next_action": str, "evidence": list, "request_id": str, "run_id": str}
CARD_SHAPE: dict[str, type] = {"code": str, "message": str, "suggested_action": str}
_NEEDED = ("completion_state", "warning_card")


def _kind(value: Any) -> str:
    """What a value read from a reply is, by its JSON type alone and never its contents, which may be anything."""
    for kind, said in ((bool, "a boolean"), (int, "a number"), (float, "a number"), (str, "a string"), (list, "a list"),
                       (dict, "an object")):
        if isinstance(value, kind):
            return said
    return "null" if value is None else type(value).__name__


def _recognised(out: Any) -> tuple[dict[str, Any] | None, str]:
    """The reply on the last line of an act's output when it has the shape the act writes (``REPLY_SHAPE``), and what was
    found, in words: its state and code, or the first way it is not that shape. Whatever it is given, it does not raise:
    each value is read only once its type is known, and a line that does not decode, however deep, is one that does not
    read."""
    lines = [line for line in out.splitlines() if line.strip()] if isinstance(out, str) else []
    if not lines:
        return None, "no reply"
    try:
        reply = json.loads(lines[-1])
    except (ValueError, RecursionError):
        return None, "a last line that does not read as JSON"
    if not isinstance(reply, dict):
        return None, f"a reply that is {_kind(reply)}, not an object"
    for name in _NEEDED:
        if name not in reply:
            return None, f"a reply with no {name}"
    for name, kind in REPLY_SHAPE.items():
        if name in reply and not isinstance(reply[name], kind):
            return None, f"a reply whose {name} is {_kind(reply[name])}"
    card = reply["warning_card"]
    if "code" not in card:
        return None, "a reply whose warning_card has no code"
    for name, kind in CARD_SHAPE.items():
        if name in card and not isinstance(card[name], kind):
            return None, f"a reply whose warning_card.{name} is {_kind(card[name])}"
    state, said = reply["completion_state"], card["code"]
    if not ERROR_CODE_PATTERN.match(said):
        return None, f"a reply whose warning_card.code {said[:80]!r} is not a G6 code"
    if reply.get("code", said) != said or reply.get("reliability_label", state) != state:
        return None, "a reply whose code or state says two different things"
    return reply, f"a reply of state {state[:80]!r} and code {said[:80]!r}"


def _read_act(code: int | None, out: str, err: str = "", *, act_name: str | None = None,
              request: dict[str, Any] | None = None, failed: str = "") -> dict:
    """The one reader of a person's act once its process started (``g6.metaharness.runner.act_main``). It did what it was
    asked only on exit status 0 with a recognised reply (``_recognised``) that is a qualified draft whose code is that
    act's own (``ACT_DONE``; any of them when no act is named). It refused only on exit status 1 with a recognised blocked
    reply, which is passed on as it is: recognised, its code is a G6 code by G6's own rule, and the act read its folder
    back before it wrote it (``runner.as_recorded``). Anything else is ``ACT_FAILED`` and names the exit status and what
    was found, or what ``failed`` says went wrong talking to the process, and nothing in it raises: a reply that does not
    read or is not the act's shape, a code that is not a G6 code, one that claims more than a qualified draft, a success
    with a failing status, a status never seen before, of any type, a broken pipe and a timeout. A process that ended so
    may have ended between two of its writes, so with its ``request`` the reply says what the folder holds of the act,
    read back (``_read_back``, ``recorded``); with none it claims nothing it did."""
    reply, found = _recognised(out)
    if reply is not None and type(code) is int:
        state, said = reply["completion_state"], reply["warning_card"]["code"]
        done = [value for name, value in ACT_DONE.items() if act_name is None or name == act_name]
        if code == 0 and state == "qualified-draft" and said in done:
            return reply
        if code == 1 and state == "blocked-escalated":
            return reply
    tail = next((text for text in (err, out) if isinstance(text, str) and text), "")
    held, read = None, "so nothing it did is claimed"
    if request is not None:
        held, read = _read_back(request, ended=type(code) is int)
    what = (f"{failed}, with exit status {code!r}" if failed else f"the act ended with exit status {code!r} and {found}, "
            "which is neither the act done nor its refusal")
    extra = {"recorded": held} if request is not None else {}
    return _blocked(ACT_FAILED, f"{what}, {read}{': ' + tail[-300:] if tail else ''}"[:500],
                    "g6_improve_status says what the folder's records hold.", exit_status=code,
                    reply_state=reply["completion_state"] if reply is not None else None, **extra)


def _stop_entry_on_record(root: Path, name: str, entry: str | None) -> bool | None:
    """Read the complete bytes written with this stop's identity. Partial text, an empty file and another stop's bytes
    are not its entry. An unreadable file is unknown, never a successful write."""
    try:
        held = (root / name).read_bytes()
        return entry is not None and entry.encode("utf-8") in held
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return None


def _stop_before(root: Path) -> dict[str, Any]:
    """The start a stop is ending, if the records read. File size and the count of historical lines cannot identify
    this stop's writes: those are read by their complete bytes and written identity. File absence is read separately,
    so a newly created empty file can stop the folder without borrowing an old unchanged file's effect."""
    try:
        log = _log(root)
        started, records = (log.started() if log is not None and log.start_in_force() else None), True
    except Exception:  # noqa: BLE001 - records that do not read, however that fails, never keep a stop from being written
        started, records = None, False
    try:
        (root / STOP_FILE).read_bytes()
    except (FileNotFoundError, NotADirectoryError):
        missing = True
    except Exception:  # noqa: BLE001 - as above: what cannot be read is not known
        missing = None
    else:
        missing = False
    return {"started": started, "records": records, "missing": missing}


def _stop_on_record(root: Path, before: dict[str, Any], ident: str, reason: str,
                    canonical_id: str | None = "", *,
                    entries: tuple[str, str] | None = None) -> dict[str, bool | None]:
    """Read each part independently; one unreadable part never erases another receipt."""
    try:
        stop_entry, count_entry = entries if entries is not None else (
            stop_entries(reason, ident) if ident else (None, None))
    except BaseException:
        rendered = False
        stop_entry = count_entry = None
    else:
        rendered = True
    try:
        line = _stop_entry_on_record(root, STOP_FILE, stop_entry) if rendered else None
    except BaseException:
        line = None
    try:
        count = _stop_entry_on_record(root, STOPS_FILE, count_entry) if rendered else None
    except BaseException:
        count = None
    started = before["started"]
    try:
        own = (True if canonical_id == "" else canonical_stop_record(root, canonical_id))
    except BaseException:
        own = None
    try:
        log = _log(root)
        note = canonical_id is not None and log is not None and log.record("stopped", ident) is not None
        in_force = log is not None and log.start_in_force()
        if note:
            ended = bool(note)
        elif not before["records"] or own is None:
            ended = None
        else:
            ended = started is not None and own and (not in_force or bool(what_stops(root, started)[1]))
    except BaseException:
        note = ended = None
    try:
        appeared = (bool(stop_reason(root)) if before["missing"] is True and line is not None else
                    False if before["missing"] is False else None)
    except BaseException:
        appeared = None
    stands = (False if canonical_id is None else
              True if line or ended or appeared else None if line is None or ended is None or appeared is None else False)
    return {"the stop": stands, "its line in the STOP file": line, "its count in stops.log": count,
            "its note in the constraint log": note}


def _stop_reply(state: str, code: str, message: str, *, canonical_id: str | None,
                next_action: str = "", **extra: Any) -> dict:
    """Use the library's envelope builders without reading fresh entropy after a stop.

    A known canonical identity also names this operation's envelope. If no identity
    could be drawn, the library's empty-id defaults say so without inventing one.
    """
    card = warning_card(code, message, next_action or None)
    fields = envelope_fields(reliability_label=state, warning_card=card,
                             request_id=(canonical_id or "")[:12], run_id=canonical_id or "", evidence=[])
    if next_action:
        fields["recommended_next_steps"] = [next_action]
    return {**fields, "code": code, "message": message, "next_action": next_action, **extra}


def _stop_read_back(root: Path, before: dict[str, Any], ident: str, reason: str, why: str,
                    canonical_id: str | None, denial: object) -> tuple[dict, bool | None, str | None]:
    """The post-write receipt boundary, including the note's reads and runner metadata. The note itself is written
    by the stop, beside its call of the stop's own writer, as every ending of a start on a stop is.

    Canonical establishment has its own guarded read-back before caller text.
    Here every observation is independent. An unreadable runner is unknown,
    never described as absent, and every outcome retains the canonical identity.
    """
    try:
        running = runner_alive(root)
    except BaseException:
        running = None
    try:
        found = _stop_on_record(root, before, ident, reason, canonical_id,
                                entries=getattr(why, "entries", None))
    except BaseException:
        found = {"the stop": None, "its line in the STOP file": None, "its count in stops.log": None,
                 "its note in the constraint log": None}
    try:
        snapshot = canonical_stops(root, completing=denial)
    except BaseException:
        snapshot = None
    return found, running, snapshot


def _looped(root: Path) -> bool:
    """Whether a stop is written in ``root``: a loop's folder (``_is_a_loop``), or one where that cannot be told, however the
    check fails, since a check that cannot be made never keeps a stop from being written."""
    try:
        return _is_a_loop(root, raise_errors=True)
    except Exception:  # noqa: BLE001 - a folder that cannot be checked may be a loop's: the stop is written there
        return True


def stop(folder: str, reason: str = "") -> dict:
    """Deny first, establish the canonical stop, then report independently read parts. The denial is the first thing it
    does, before the folder is found or anything else that can fail (``_begin_stop``), and it is let go only once this
    stop's canonical record stands. Every statement runs inside its one handler, as in the dialog and the start, and a
    check that cannot be made never keeps the stop from being written: a folder whose check fails is taken for a loop's
    (``_looped``). A folder that cannot be found, or anything else that escapes the stop's own receipt, says so
    (``STOP_FAILED``), with the denial still in force."""
    root, running = folder, None            # the folder as given, for a reply before it is found
    try:
        denial = _begin_stop(folder)
        root = _root(folder, stop_request=True)
        _STOP_PENDING[denial] = root
        if not _looped(root):
            return _not_a_loop(root)
        before = _stop_before(root)
        failed, why = None, ""
        try:
            why = ask_to_stop(root, reason, _identity=uuid4)
        except OSError as exc:
            failed = exc
        canonical_id = getattr(why, "canonical_id", None)
        ident = getattr(why, "record_id", "")
        if why and canonical_id is not None and ident:
            try:
                record_stop(root, why, at=datetime.now().astimezone().isoformat(), record_id=ident)
            except BaseException:
                pass  # the note is claimed only by the independent read in the receipt
        found, running, snapshot = _stop_read_back(root, before, ident, reason, why, canonical_id, denial)
        raised = f"; writing it raised {type(failed).__name__}: {failed}" if failed is not None else ""
        raised = utf8_text(raised) + getattr(why, "details", "")
        if before["started"] and found["its note in the constraint log"] is not True:
            raised += "; G6_E_LOOP_STOP_NOTE_FAILED: the stop note did not read back"
        if running is None:
            raised += "; G6_E_LOOP_STOP_RUNNER_UNREADABLE: runner liveness is unknown"
        if found["the stop"] is not True:
            reply = _stop_reply("blocked-escalated", STOP_FAILED,
                                f"the stop is not known to stand; {_record_said(found)}{raised}"[:400],
                                canonical_id=canonical_id, running=running, folder=utf8_text(str(root)), recorded=found,
                                canonical_stop=snapshot, canonical_stop_id=canonical_id or None)
        else:
            said = ("the runner has been asked to stop and is still alive: it ends once it sees the STOP file, at its next "
                    "model call, or within a minute while it waits" if running else
                    "no runner is alive" if running is False else "runner liveness is unknown")
            reply = _stop_reply("qualified-draft", STOP_ASKED, f"{said}; {_record_said(found)}{raised}"[:500],
                                canonical_id=canonical_id, running=running,
                                stopped=None if running is None else not running, stop=why, folder=utf8_text(str(root)),
                                recorded=found, canonical_stop=snapshot, canonical_stop_id=canonical_id or None,
                                next_action="g6_improve_status says when it has gone." if running is not False else
                                "g6_improve_start starts it again, and lifts the stop once the person agrees.")
        if getattr(why, "established", False):
            _finish_stop(denial)
        return reply
    except Exception as exc:  # noqa: BLE001 - a stop that could not be written says so, whatever stopped it
        return _blocked(STOP_FAILED, f"the stop could not be written: {type(exc).__name__}: {exc}"[:400],
                        running=_running(root)[0] if running is None else running, folder=utf8_text(str(root)),
                        recorded=None)


def _running(root: Any) -> tuple[bool | None, str]:
    """Whether a runner is alive on ``root``, or ``None`` and why when its lock cannot be tried: that is never read as a
    runner alive, nor as none (``runner_alive``)."""
    try:
        return runner_alive(Path(root)), ""
    except Exception as exc:  # noqa: BLE001 - a lock that cannot be tried says nothing either way
        return None, f"{type(exc).__name__}: {exc}"[:200]


def _shown(cycle: dict[str, Any]) -> dict[str, Any]:
    return {name: cycle[name] for name in ("tick", "at", "outcome", "code", "reason", "constraint", "head_version", "budget",
                                           *FINDINGS) if name in cycle}


def status(folder: str) -> dict:
    """Where the loop stands, from its files. It writes nothing. The stop comes first: once the folder is found, the one
    reading (``stop_in_force``) is taken before anything else status reads, and every reply reports it, a changed
    enrolment that ended the start among it. Every statement that touches a file runs inside its one handler, so whatever
    else cannot be read or used, however it fails, a field of the records among them, is said beside the stop
    (``_unread_status``), never raised. A pause is what holds the ticks until a person lifts it (``runner.held``): the
    pause on record, or the cycles that hold them when their pause could not be written."""
    root, halted = Path(folder), None      # the folder as given, for a reply before it is found
    try:
        from g6.metaharness.loop import STATUS_LINE
        from g6.metaharness.runner import held as held_by
        from g6.metaharness.shadow import lineage

        root = _root(folder)
        if not _is_a_loop(root):
            return _not_a_loop(root)
        halted = stop_in_force(root)       # first: the one reading of the stop's files and the records, opened no more
        beat, running = heartbeat(root) or {}, runner_alive(root)
        cycles, paused, started, seen, unreadable = [], "", None, 0, ""
        log = halted.log
        if log is not None:
            cycles, paused, started, seen = log.cycles(), held_by(log), log.started(), log.generation()
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


__all__ = ["NOT_ASKED", "act", "asked", "asked_act", "refused", "start", "status", "stop"]
