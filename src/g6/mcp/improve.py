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
interpreter compares, and it records nothing if any of it moved meanwhile.
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
from g6.mcp.consent import question as _question
from g6.metaharness.api import _blocked, _envelope
from g6.self_maintenance import digest
from g6.self_maintenance.envelope import (
    CYCLE_LOG,
    FINDINGS,
    RUNNER_LOCK,
    STOPS_FILE,
    CycleLog,
    ask_to_stop,
    enrolment_digest,
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


def _pinned_python(settings: dict[str, Any]) -> str:
    """The interpreter a folder pins its runner to, or ``""`` with none named. A ``ValueError`` says what is wrong with it."""
    if "python" not in settings:
        return ""
    python = settings["python"]
    if not isinstance(python, str) or not Path(python).is_absolute() or not Path(python).is_file():
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
                                cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=log, close_fds=True, **detached)


def _interpreter(settings: dict[str, Any]) -> list[str]:
    """How the runner's Python starts, for the runner and for a person's act alike (``_spawn``)."""
    python = _pinned_python(settings)
    cache = tempfile.mkdtemp(prefix="g6-loop-no-bytecode-")
    return [python or sys.executable, "-I" if python else "-P", "-B", "-X", f"pycache_prefix={cache}"]


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


def start(root: Path, who: str, shown: dict[str, Any] | None = None) -> dict:
    """Record who started it and start the runner, on the binding the person agreed to: ``shown``, which ``asked`` built
    the dialog from, or with none (a Python caller, with no dialog), what stands now, recorded as read. What the person
    read may have moved while the dialog was up, so the start reads it again before it waits for the folder's lock, and
    again holding the lock, immediately before it records, and any difference asks them again. It records the binding's settings digest, count of stops and ``STOP`` file, never ones
    read afresh, so a restart holds to what was agreed and any stop since closes it. It lifts only the ``STOP`` file the
    binding names, by recording it: any other stop stands, and nothing starts."""
    cannot = _cannot_start(root)
    if cannot is not None:
        return cannot
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
            CycleLog(root / CYCLE_LOG).start(who, at=datetime.now().astimezone().isoformat(), settings=bound[SETTINGS_DIGEST],
                                             stops=bound[STOPS_COUNTED], enrolment=bound[ENROLMENT],
                                             **{name: bound[label] for name, label in LIFTS.items() if label in bound})
        why = stop_reason(root)
        if why:
            record_stop(root, why, at=datetime.now().astimezone().isoformat())
            return _blocked(START_STOPPED, f"a stop arrived while the start was being recorded, so nothing was started: {why}"
                            [:500], NOT_ASKED, running=False, folder=str(root))
        spawned = datetime.now().astimezone()              # before the spawn: a runner can beat before this process looks
        return {**_watch(root, _spawn(root, settings), spawned), "started_by": who}
    except (OSError, LedgerError, WorkspaceRefused, ValueError) as exc:
        return _blocked(START_FAILED, f"the runner could not be started: {type(exc).__name__}: {exc}"[:500], running=False,
                        folder=str(root))


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
    from g6.metaharness.runner import person_terms

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
        if person_terms(act_name, root, generation=generation) != bound:
            return _blocked(TERMS_CHANGED, "the folder changed while its question was being read, so nothing was asked or "
                            "recorded", CHANGED_SINCE, folder=str(root))
    except (ValueError, LedgerError) as exc:
        return _blocked(RECORDS_UNREADABLE, f"the loop's records cannot be trusted: {exc}"[:400],
                        "A person inspects the folder; nothing was asked or recorded.")
    return root, shown, bound


def act(act_name: str, root: Path, who: str, shown: dict[str, Any], generation: int | None = None) -> dict:
    """Do what the person agreed to, on the binding their dialog was built from (``shown``), if it still stands: in a
    process like the runner's (``_run_act``), which reads it again under the folder's lock before it runs or records
    anything, and runs only the bytes it names."""
    from g6.metaharness.runner import SETTINGS_DIGEST as BOUND_SETTINGS, person_terms

    cannot = _cannot_act(act_name, root, generation)
    if cannot is not None:
        return cannot
    try:
        settings = read_settings(root)
        if (not isinstance(shown, dict) or digest_of(settings) != shown.get(BOUND_SETTINGS)
                or person_terms(act_name, root, generation=generation) != shown):
            return _blocked(TERMS_CHANGED, "what the person read is no longer what stands, so nothing ran or was recorded",
                            CHANGED_SINCE, folder=str(root))
    except (ValueError, LedgerError) as exc:
        return _blocked(RECORDS_UNREADABLE, f"the loop's records cannot be trusted: {exc}"[:400], CHANGED_SINCE)
    request = {"act": act_name, "folder": str(root), "who": who, "binding": shown, "generation": generation}
    return {**_run_act(root, settings, request), "by": who}


def _run_act(root: Path, settings: dict[str, Any], request: dict[str, Any]) -> dict:
    """The act in its own process, on the interpreter the runner uses: the request on its input, its reply as the last
    line of its output. It never shares this server's input or output, and one that outlives ``ACT_WAIT`` is left to finish."""
    hidden = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    command = [*_interpreter(settings), "-c", "import sys; from g6.metaharness.runner import act_main; sys.exit(act_main())"]
    try:
        process = subprocess.Popen(command, cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", errors="replace", close_fds=True, **hidden)
        out, err = process.communicate(json.dumps(request), timeout=ACT_WAIT)
    except subprocess.TimeoutExpired:
        return _blocked(ACT_FAILED, f"the act did not finish in {ACT_WAIT:g} seconds; it was left to finish",
                        "g6_improve_status says what the folder's records hold.", folder=str(root))
    except OSError as exc:
        return _blocked(ACT_FAILED, f"the act could not be started: {type(exc).__name__}: {exc}"[:500],
                        "Nothing was recorded.", folder=str(root))
    return _read_act(process.returncode, out, err, act_name=request.get("act"))


#: What each person's act replies when it did what it was asked, and nothing else does.
ACT_DONE = {"enrol": "G6_E_LOOP_ENROLLED", "resume": "G6_E_LOOP_RESUMED", "demote": "G6_E_LOOP_DEMOTED"}
#: The shape of a person's act's reply as ``g6.metaharness.api._envelope`` writes it: each field the one reader reads or
#: passes on as the envelope every surface carries, with the JSON type it must have, and the card's. ``completion_state``,
#: the card and its ``code`` must be there; any other must have its type when it is there, and a ``code`` or a
#: ``reliability_label`` beside them must say the same. A reply is recognised by this shape alone; anything else in it is
#: passed on as it is and read nowhere.
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
    if reply.get("code", said) != said or reply.get("reliability_label", state) != state:
        return None, "a reply whose code or state says two different things"
    return reply, f"a reply of state {state[:80]!r} and code {said[:80]!r}"


def _read_act(code: int | None, out: str, err: str = "", *, act_name: str | None = None) -> dict:
    """The one reader of a person's act as its process ended (``g6.metaharness.runner.act_main``). It did what it was
    asked only on exit status 0 with a recognised reply (``_recognised``) that is a qualified draft whose code is that
    act's own (``ACT_DONE``; any of them when no act is named). It refused only on exit status 1 with a recognised blocked
    reply that has a G6 code, which is passed on as it is. Anything else is ``ACT_FAILED`` and names the exit status and
    what was found, and nothing in it raises: a reply that does not read or is not the act's shape, one that claims more
    than a qualified draft, a success with a failing status, and a status never seen before, of any type."""
    reply, found = _recognised(out)
    if reply is not None and type(code) is int:
        state, said = reply["completion_state"], reply["warning_card"]["code"]
        done = [value for name, value in ACT_DONE.items() if act_name is None or name == act_name]
        if code == 0 and state == "qualified-draft" and said in done:
            return reply
        if code == 1 and state == "blocked-escalated" and said.startswith("G6_E_"):
            return reply
    tail = next((text for text in (err, out) if isinstance(text, str) and text), "")
    return _blocked(ACT_FAILED, f"the act ended with exit status {code!r} and {found}, which is neither the act done nor "
                    f"its refusal, so nothing it did is claimed: {tail[-300:]}"[:500],
                    "g6_improve_status says what the folder's records hold.", exit_status=code,
                    reply_state=reply["completion_state"] if reply is not None else None)


def stop(folder: str, reason: str = "") -> dict:
    """Write the stop and say whether the runner has gone."""
    root = _root(folder)
    if not _is_a_loop(root):
        return _not_a_loop(root)
    try:
        why = ask_to_stop(root, reason)
    except OSError as exc:
        return _blocked(STOP_FAILED, f"the stop could not be written: {type(exc).__name__}: {exc}"[:400],
                        running=runner_alive(root), folder=str(root))
    record_stop(root, why, at=datetime.now().astimezone().isoformat())
    running = runner_alive(root)
    said = ("the runner has been asked to stop and is still alive: it ends once it sees the STOP file, at its next model "
            "call, or within a minute while it waits" if running else "the STOP file is in place and no runner is alive")
    return _envelope("qualified-draft", STOP_ASKED, said, running=running, stopped=not running, stop=why, folder=str(root),
                     next_action="g6_improve_status says when it has gone." if running else
                     "g6_improve_start starts it again, and lifts the stop once the person agrees.")


def _shown(cycle: dict[str, Any]) -> dict[str, Any]:
    return {name: cycle[name] for name in ("tick", "at", "outcome", "code", "reason", "constraint", "head_version", "budget",
                                           *FINDINGS) if name in cycle}


def status(folder: str) -> dict:
    """Where the loop stands, from its files. It writes nothing, and a record that does not read is said, not raised."""
    from g6.metaharness.loop import STATUS_LINE
    from g6.metaharness.shadow import lineage

    root = _root(folder)
    if not _is_a_loop(root):
        return _not_a_loop(root)
    beat, running = heartbeat(root) or {}, runner_alive(root)
    cycles, paused, started, seen, unreadable = [], "", None, 0, ""
    try:
        log = _log(root)
        if log is not None:
            cycles, paused, started, seen = log.cycles(), log.paused(), log.started(), log.generation()
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
