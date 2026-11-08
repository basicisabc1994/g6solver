# SPDX-License-Identifier: Apache-2.0
"""Starting, stopping and reading the self-improvement loop over MCP: what the ``g6_improve_*`` tools do.

**Start** is a sensitive operation, allowed as any other is (``metaharness.improve`` or ``metaharness`` in
``G6_MCP_SENSITIVE``), and the person then answers in the client's own dialog (``g6.mcp.consent``). There is no terminal
fallback and no agent answer: a decline, a cancel or a client with no dialog starts nothing. It starts the runner as a
process of its own, which outlives the session that asked, and refuses when a runner is alive, no person has enrolled a
domain in the folder or the count of stops cannot be read there. This module never runs the domain's code: only the runner
it starts does, and the dialog names it.
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

import json
import os
import site
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from uuid import uuid4

import g6
from g6.core.door import SENSITIVE_ENV, allowed_sensitive, blocked_reply, is_allowed
from g6.core.ledger import LedgerError, digest as digest_of
from g6.core.storage import g6_home
from g6.invariants import ERROR_CODE_PATTERN
from g6.mcp.consent import question as _question
from g6.metaharness.api import _blocked, _envelope
from g6.self_maintenance import digest
from g6.self_maintenance.envelope import (
    CYCLE_LOG,
    FINDINGS,
    RUNNER_LOCK,
    STOP_FILE,
    STOPS_FILE,
    CycleLog,
    ask_to_stop,
    enrolment_digest,
    enrolment_id,
    file_digest,
    heartbeat,
    record_stop,
    runner_alive,
    stop_reason,
    stop_standing,
    stop_to_lift,
    stops_asked,
)
from g6.self_maintenance.pacing import SETTINGS_FILE, read_settings, windows_from
from g6.workspace._coordination import Coordination
from g6.workspace._envelope import WorkspaceRefused

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


def _not_a_loop(root: Path) -> dict:
    return _blocked(NOT_A_FOLDER, f"{root} is not a loop's folder: it has no {SETTINGS_FILE} and no records, so nothing was "
                    "done", f"Name the folder, or create {SETTINGS_FILE} there: see the design's section 11.")


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


def _cannot_start(root: Path) -> dict | None:
    if not (root / SETTINGS_FILE).is_file():
        return _not_a_loop(root)
    try:
        settings = read_settings(root)
        windows_from(settings)
        _pinned_python(settings)
    except ValueError as exc:
        return _blocked(BAD_SETTINGS, f"{exc}"[:400], f"Fix {SETTINGS_FILE} in {root}; nothing was started.")
    if runner_alive(root):
        return _blocked(RUNNER_ALIVE, "a runner is already alive on this folder, so nothing was started",
                        "Read g6_improve_status. If you have just stopped it, it ends once it sees the STOP file: at its next "
                        "model call, or within a minute while it waits.")
    try:
        log = _log(root)
    except LedgerError as exc:
        return _blocked(RECORDS_UNREADABLE, f"the loop's records cannot be trusted: {exc}"[:400],
                        "A person inspects the folder; nothing was started.")
    if stops_asked(root) < 0:
        return _blocked(RECORDS_UNREADABLE, "the count of stops cannot be read, so a restart would have nothing to stand on "
                        "and nothing was started", f"A person frees {STOPS_FILE} in {root}.")
    if log is None or log.enrolled() is None:
        return _blocked(NOT_ENROLLED, "no person has enrolled a domain in this folder, so nothing was started", ENROL)
    if log.enrolled()["frozen"].get("domain_file") != file_digest(read_settings(root).get("domain_file")):
        return _blocked(FROZEN_CHANGED, "the Python file that declares this domain is not the one a person enrolled, so "
                        "nothing was started and it did not run", ENROL)
    return None


#: The fields of a start's binding that the start records, and the ``STOP`` file's, as ``stop_to_lift`` names them.
SETTINGS_DIGEST = "Settings digest"
STOPS_COUNTED = "Stops counted"
ENROLMENT = "Enrolment on record"
LIFTS = {"lifted": "STOP file SHA-256", "lifted_bytes": "STOP file bytes", "lifted_mtime_ns": "STOP file modified (ns)"}


def _terms(root: Path) -> dict[str, Any]:
    """The binding of a start: all its dialog shows and the start stands on, read once, as the fields the dialog shows
    (``g6.mcp.consent.question``). That is the folder, the Python file by path and digest, the enrolment on record, each
    provider's allowance, the interpreter, the whole settings file by digest, the count of stops and the count the last
    start saw, any stop in place with the ``STOP`` file it would lift (``stop_to_lift``), and any pause. Any write to the
    stop changes it, and so does a stop that died between its two writes, which only the count shows."""
    settings, log, stops = read_settings(root), _log(root), stops_asked(root)
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
        SETTINGS_DIGEST: digest_of(settings), STOPS_COUNTED: stops,
        "Stops the last start counted": ((log.started() if log is not None else None) or {}).get("stops", 0)}
    why, standing = stop_reason(root), stop_to_lift(root)
    if why:
        bound["Stop now in place"] = why
    bound |= {LIFTS[name]: value for name, value in standing.items()}
    paused = log.paused() if log is not None else ""
    if paused:
        bound["Paused because"] = paused
    return bound


def asked(folder: str) -> tuple[Path, str, dict[str, Any]] | dict:
    """What the person reads before the runner starts, the folder it is about, and the binding it is built from
    (``_terms``); a blocked reply when it cannot start, so no dialog is shown for nothing. The dialog is built from that
    one read, so what the person reads is what the start stands on."""
    root = _root(folder)
    cannot = _cannot_start(root)
    if cannot is not None:
        return cannot
    try:
        bound = _terms(root)
    except (ValueError, LedgerError) as exc:
        return _blocked(RECORDS_UNREADABLE, f"the loop's records cannot be trusted: {exc}"[:400],
                        "A person inspects the folder; nothing was started.")
    said = [RUNS]
    if "Stop now in place" in bound:
        said.append("Starting lifts the stop now in place. Its file stays, and any write to it after that stops the loop "
                    "again.")
    elif bound[STOPS_COUNTED] > bound["Stops the last start counted"]:
        said.append(f"A stop was asked for since the last start and no STOP file stands for it: {STOPS_FILE} in the folder "
                    "says why. Starting goes past it.")
    if "Paused because" in bound:
        said.append("The loop is paused, so it will spend nothing until you lift that.")
    return root, _question(f"Start G6's self-improvement loop on {bound['Domain']}?", bound, said, close=NAME), bound


def _spawn(root: Path, settings: dict[str, Any]) -> Any:
    """The runner as a process of its own: no console, its own group, its output in its folder. On the Python the settings the
    person agreed to pin, isolated, since ``-P`` leaves ``PYTHONPATH`` standing and that puts a working tree ahead of any
    install. With none pinned, on this server's Python, as the server's own environment has it. Either way it reads its
    cached bytecode from a folder made empty for it, and writes none, so only the source the envelope pins can run: a
    ``.pyc`` planted under ``__pycache__`` beside a pinned file would run in its place. These are flags, as ``-I`` reads no
    ``PYTHON*`` setting."""
    detached = ({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                else {"start_new_session": True})
    with open(root / RUNNER_LOG, "ab") as log:
        return subprocess.Popen([*_interpreter(settings), "-m", "g6.metaharness.loop", "run", str(root)],
                                cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=log, close_fds=True,
                                env=_environment(), **detached)


def _interpreter(settings: dict[str, Any]) -> list[str]:
    """How the runner's Python starts, for the runner and for a person's act alike (``_spawn``). ``-P`` and ``-I`` both
    keep Python from adding a path of its own: the folder it starts in, which is the loop's, for ``-m`` and ``-c``."""
    python = _pinned_python(settings)
    cache = tempfile.mkdtemp(prefix="g6-loop-no-bytecode-")
    return [python or sys.executable, "-I" if python else "-P", "-B", "-X", f"pycache_prefix={cache}"]


def _environment() -> dict[str, str]:
    """The environment a G6 child starts in, for the runner and for a person's act alike: this server's, with its import
    path fixed here, before the child starts (``_import_path``)."""
    return {**os.environ, "PYTHONPATH": _import_path()}


def _import_path() -> str:
    r"""The ``PYTHONPATH`` of a G6 child, so it imports G6 from where this server's ``g6`` was loaded: each entry of the
    server's own ``PYTHONPATH`` that stands on its own (``_stands_alone``), in its order, behind the folder that holds the
    server's ``g6`` when neither those entries nor this Python's own site folders hold it already (a working tree, or an
    editable install). It holds no entry Python would read against where the child stands, which is the loop's folder a
    caller named, on its drive: an empty or a relative entry, ``\x`` or ``C:x``. A ``g6`` there would run in place of the
    server's. A site folder is never put first, where it would stand in front of the standard library. A pinned
    interpreter starts isolated (``-I``), reads none of it, and imports the G6 installed for it."""
    given = [entry for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep) if _stands_alone(entry)]
    home = Path(g6.__file__).resolve().parent.parent
    sites = [*site.getsitepackages(), site.getusersitepackages()]
    reached = any(os.path.normcase(os.path.realpath(entry)) == os.path.normcase(str(home)) for entry in [*given, *sites])
    return os.pathsep.join(given if reached else [str(home), *given])


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
            return _blocked(START_FAILED, f"the runner ended as it started, with exit code {code}: {said}"[:500], running=False,
                            return_code=code, folder=str(root))
        if _past_opening(root, since):
            return _envelope("qualified-draft", STARTED, f"the runner is alive on {root}", running=True, folder=str(root),
                             next_action="g6_improve_status reads it; the daily digest is under its folder; "
                                         "g6_improve_stop stops it.")
        time.sleep(0.1)
    return _envelope("qualified-draft", STARTED, f"the runner was started on {root} and has not opened its folder yet",
                     running=False, folder=str(root), next_action="Read g6_improve_status in a minute.")


def _not_what_was_read(root: Path) -> dict:
    return _blocked(TERMS_CHANGED, "what the person read is no longer what would start, so nothing was started", ASK_AGAIN,
                    running=False, folder=str(root))


def _still_read(root: Path, shown: dict[str, Any]) -> bool:
    """Whether what a person read before a start (``_terms``) still stands: a folder that no longer reads has moved."""
    try:
        return _terms(root) == shown
    except (OSError, ValueError, LedgerError):
        return False


def start(root: Path, who: str, shown: dict[str, Any] | None = None) -> dict:
    """Record who started it and start the runner, on the binding the person agreed to: ``shown``, which ``asked`` built
    the dialog from, or with none (a Python caller, with no dialog), what stands now, recorded as read. What the person
    read may have moved while the dialog was up, so the start reads it again first, before anything else it checks, so a
    change to what they read is named as one (``TERMS_CHANGED``) whatever else changed with it; again before it waits
    for the folder's lock, and again holding the lock, immediately before it records, and any difference asks them
    again. It records the binding's settings digest, count of stops and ``STOP`` file, never ones read afresh, so a
    restart holds to what was agreed and any stop since closes it. It lifts only the ``STOP`` file the binding names, by
    recording it: any other stop stands, and nothing starts. Once it has tried to record, its reply is decided by what
    the folder holds of that start, read back (``_start_as_recorded``), whichever step failed."""
    if shown is not None and not _still_read(root, shown):
        return _not_what_was_read(root)
    cannot = _cannot_start(root)
    if cannot is not None:
        return cannot
    ident = ""
    try:
        now = _terms(root)
        if shown is not None and now != shown:
            return _not_what_was_read(root)
        bound = now if shown is None else shown
        if bound[STOPS_COUNTED] < 0:
            raise OSError(f"{STOPS_FILE} cannot be read")      # a restart would read two unreadable counts as one
        with Coordination(root / ".tick").mutex():
            settings = read_settings(root)
            if digest_of(settings) != bound[SETTINGS_DIGEST] or (shown is not None and _terms(root) != bound):
                return _not_what_was_read(root)
            ident = uuid4().hex                         # the row's identity, separate from its time
            CycleLog(root / CYCLE_LOG).start(who, record_id=ident, at=datetime.now().astimezone().isoformat(),
                                             settings=bound[SETTINGS_DIGEST], stops=bound[STOPS_COUNTED],
                                             enrolment=bound[ENROLMENT],
                                             enrolment_id=bound["Enrolment record id"],
                                             **{name: bound[label] for name, label in LIFTS.items() if label in bound})
        why = stop_reason(root)
        if why:
            record_stop(root, why, at=datetime.now().astimezone().isoformat())
            reply = _blocked(START_STOPPED, f"a stop arrived while the start was being recorded, so nothing was started: "
                             f"{why}"[:500], NOT_ASKED, running=False, folder=str(root))
        else:
            spawned = datetime.now().astimezone()          # before the spawn: a runner can beat before this process looks
            reply = {**_watch(root, _spawn(root, settings), spawned), "started_by": who}
    except (OSError, LedgerError, WorkspaceRefused, ValueError) as exc:
        reply = _blocked(START_FAILED, f"the runner could not be started: {type(exc).__name__}: {exc}"[:500],
                         running=False, folder=str(root))
    return _start_as_recorded(root, ident, reply) if ident else reply


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
    log = _log(root)
    started = (log.started() if log is not None else None) or {}
    ours = (log is not None and log.start_in_force() and log.record("started", ident) is not None
            and log.latest_id("started") == ident)
    return {"the start": bool(ours) and not stop_standing(root, started)}


def _start_as_recorded(root: Path, ident: str, reply: dict[str, Any]) -> dict[str, Any]:
    """The reply to a start that tried to record, as the folder holds it now: ``recorded`` says whether the start written
    with ``ident`` stands, read back (``_start_on_record``). One on record whose runner did not start says so, since the next
    runner on the folder carries on it until a stop; records that do not read say that, and claim nothing either way."""
    try:
        found = _start_on_record(root, ident)
    except (OSError, LedgerError) as exc:
        if reply["warning_card"]["code"] == STARTED:
            return {**reply, "recorded": None}
        return _restated(reply, f"{reply['message']}; the folder's records do not read back ({type(exc).__name__}), so "
                         "whether the start is on record is not known", "g6_improve_status says what the folder's "
                         "records hold.", recorded=None)
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


def _stop_file_size(root: Path) -> int | None:
    """The ``STOP`` file's length, ``-1`` with no file, or ``None`` when it cannot be read."""
    try:
        return (root / STOP_FILE).stat().st_size
    except (FileNotFoundError, NotADirectoryError):
        return -1
    except OSError:
        return None


def _stop_before(root: Path) -> dict[str, Any]:
    """What stands before a stop, for its read-back to compare with (``_stop_on_record``): the ``STOP`` file's length, the
    count of stops, and the start in force, if the records read (``records``)."""
    try:
        log = _log(root)
        started, records = (log.started() if log is not None and log.start_in_force() else None), True
    except (OSError, LedgerError):
        started, records = None, False
    return {"size": _stop_file_size(root), "stops": stops_asked(root), "started": started, "records": records}


def _stop_on_record(root: Path, before: dict[str, Any], ident: str) -> dict[str, bool | None]:
    """What the folder holds of a stop, read back afresh against what stood ``before`` it (``_stop_before``), each part on
    its own: its line in the ``STOP`` file (the file grew), its count in ``stops.log`` (the count grew), and its note in
    the constraint log (the row written with ``ident``). The stop itself stands when its line does, or when
    the start it found in force no longer stands, by G6's own rule for what stops a start (``stop_standing``), which a
    runner reads too. A part that cannot be read, now or before, is not known (``None``), never on record or not. It
    does not raise."""
    size, stops = _stop_file_size(root), stops_asked(root)
    line = None if size is None or before["size"] is None else size > before["size"]
    count = None if stops < 0 or before["stops"] < 0 else stops > before["stops"]
    started = before["started"]
    try:
        log = _log(root)
        note = log is not None and log.record("stopped", ident) is not None
        in_force = log is not None and log.start_in_force()
        ended = True if note else (started is not None and (not in_force or bool(stop_standing(root, started)))
                                   if before["records"] else None)          # its note is written only to end a start
    except (OSError, LedgerError):
        note = ended = None
    stands = True if line or ended else None if line is None or ended is None else False
    return {"the stop": stands, "its line in the STOP file": line, "its count in stops.log": count,
            "its note in the constraint log": note}


def stop(folder: str, reason: str = "") -> dict:
    """Write the stop and say whether the runner has gone, as the folder holds the stop once it is written, read back
    (``_stop_on_record``), whichever of its writes failed: a stop is asked when it is on record, and failed when it is not
    or cannot be read, since a stop asked again is safe and one taken for standing that does not is not."""
    root = _root(folder)
    if not _is_a_loop(root):
        return _not_a_loop(root)
    before = _stop_before(root)
    ident = uuid4().hex                         # the row's identity, separate from its time
    failed, why = None, ""
    try:
        why = ask_to_stop(root, reason)
    except OSError as exc:
        failed = exc
    why = why or (stop_standing(root, before["started"]) if before["started"] else "")
    if why:
        record_stop(root, why, at=datetime.now().astimezone().isoformat(), record_id=ident)
    running = runner_alive(root)
    found = _stop_on_record(root, before, ident)
    raised = f"; writing it raised {type(failed).__name__}: {failed}" if failed is not None else ""
    if found["the stop"] is not True:
        return _blocked(STOP_FAILED, f"the stop is not known to stand; {_record_said(found)}{raised}"[:400],
                        running=running, folder=str(root), recorded=found)
    said = ("the runner has been asked to stop and is still alive: it ends once it sees the STOP file, at its next model "
            "call, or within a minute while it waits" if running else "no runner is alive")
    return _envelope("qualified-draft", STOP_ASKED, f"{said}; {_record_said(found)}{raised}"[:500], running=running,
                     stopped=not running, stop=why, folder=str(root), recorded=found,
                     next_action="g6_improve_status says when it has gone." if running else
                     "g6_improve_start starts it again, and lifts the stop once the person agrees.")


def _shown(cycle: dict[str, Any]) -> dict[str, Any]:
    return {name: cycle[name] for name in ("tick", "at", "outcome", "code", "reason", "constraint", "head_version", "budget",
                                           *FINDINGS) if name in cycle}


def status(folder: str) -> dict:
    """Where the loop stands, from its files. It writes nothing, and a record that does not read is said, not raised. A
    pause is what holds the ticks until a person lifts it (``runner.held``): the pause on record, or the cycles that
    hold them when their pause could not be written."""
    from g6.metaharness.loop import STATUS_LINE
    from g6.metaharness.runner import held as held_by
    from g6.metaharness.shadow import lineage

    root = _root(folder)
    if not _is_a_loop(root):
        return _not_a_loop(root)
    beat, running = heartbeat(root) or {}, runner_alive(root)
    cycles, paused, started, seen, unreadable = [], "", None, 0, ""
    try:
        log = _log(root)
        if log is not None:
            cycles, paused, started, seen = log.cycles(), held_by(log), log.started(), log.generation()
    except LedgerError as exc:
        unreadable = f"the loop's records cannot be trusted: {exc}"[:400]
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
                     stop=stop_standing(root, started), started=started, generation=generations["current"],
                     lineage=generations["recent"],
                     digest={"path": str(page), "text": page.read_text(encoding="utf-8")} if page else None,
                     g6_status=STATUS_LINE)


__all__ = ["NOT_ASKED", "act", "asked", "asked_act", "refused", "start", "status", "stop"]
