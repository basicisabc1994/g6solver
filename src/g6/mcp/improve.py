# SPDX-License-Identifier: Apache-2.0
"""Starting, stopping and reading the self-improvement loop over MCP: what the ``g6_improve_*`` tools do.

**Start** is a sensitive operation, allowed as any other is (``metaharness.improve`` or ``metaharness`` in
``G6_MCP_SENSITIVE``), and the person then answers in the client's own dialog (``g6.mcp.consent``). There is no terminal
fallback and no agent answer: a decline, a cancel or a client with no dialog starts nothing. It starts the runner as a
process of its own, which outlives the session that asked, and refuses when a runner is alive, no person has enrolled a
domain in the folder or the count of stops cannot be read there. This module never runs the domain's code: only the runner
it starts does, and the dialog names it.
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

A folder left empty is ``improve`` under G6's state folder. Replies carry the library envelope, and a stop or status never
imports the tick, so neither can fail for the reasons a tick can.
"""
from __future__ import annotations

import os
import subprocess
import sys
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
    STOPS_FILE,
    CycleLog,
    ask_to_stop,
    file_digest,
    heartbeat,
    record_stop,
    runner_alive,
    stop_reason,
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


def refused() -> dict | None:
    """The refusal of starting the runner, unless the host allows it."""
    if is_allowed(*SENSITIVE, allowed_sensitive()):
        return None
    return blocked_reply(f"[G6_E_SENSITIVE_COMPONENT_DENIED] {WHO} starts a process that runs on its own; the host must "
                         f"allow it, by adding {WHO} (or metaharness) to {SENSITIVE_ENV} where the server is registered")


def _root(folder: str) -> Path:
    return Path(folder).expanduser().resolve() if folder.strip() else g6_home() / "improve"


def _is_a_loop(root: Path) -> bool:
    """Its settings, its records or a runner's lock: a runner must stay within reach with the first two gone."""
    return (root / SETTINGS_FILE).is_file() or (root / CYCLE_LOG).is_file() or (root / RUNNER_LOCK).is_dir()


def _not_a_loop(root: Path) -> dict:
    return _blocked(NOT_A_FOLDER, f"{root} is not a loop's folder: it has no {SETTINGS_FILE} and no records, so nothing was "
                    "done", f"Name the folder, or create {SETTINGS_FILE} there: see the design's section 11.")


def _log(root: Path) -> CycleLog | None:
    """The folder's records, or ``None`` before any: a ``CycleLog`` makes its folder, so it is opened only when there."""
    return CycleLog(root / CYCLE_LOG) if (root / CYCLE_LOG).is_file() else None


def _cannot_start(root: Path) -> dict | None:
    if not (root / SETTINGS_FILE).is_file():
        return _not_a_loop(root)
    try:
        windows_from(read_settings(root))
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
    enrol = (f'A person enrols it at their own terminal: python -P -m g6.metaharness.loop enrol "{root}" --as "Your name". '
             "An agent cannot do that step.")
    if log is None or log.enrolled() is None:
        return _blocked(NOT_ENROLLED, "no person has enrolled a domain in this folder, so nothing was started", enrol)
    if log.enrolled()["frozen"].get("domain_file") != file_digest(read_settings(root).get("domain_file")):
        return _blocked(FROZEN_CHANGED, "the Python file that declares this domain is not the one a person enrolled, so "
                        "nothing was started and it did not run", enrol)
    return None


def _terms(root: Path, settings: dict[str, Any], standing: dict[str, Any], stops: int) -> str:
    """A digest of all a dialog shows or stands on: the whole settings file, the Python file, who enrolled it, any stop in
    place as ``stop_to_lift`` reads it, which is ``standing``, the count of stops, and any pause. Any write to the stop
    changes that. So does a stop that died between its two writes, which only the count shows."""
    log = _log(root)
    return digest_of({"settings": settings, "domain_file": file_digest(settings.get("domain_file")),
                      "enrolled": log.enrolled(), "stop": standing, "stops": stops, "paused": log.paused()})


def asked(folder: str) -> tuple[Path, str, str] | dict:
    """What the person reads before the runner starts, the folder it is about, and the terms that binds them to; a
    blocked reply when it cannot start, so no dialog is shown for nothing. The terms are read before the question, so a
    change between the two asks again rather than starting on what was not read."""
    root = _root(folder)
    cannot = _cannot_start(root)
    if cannot is not None:
        return cannot
    settings, log, stops = read_settings(root), _log(root), stops_asked(root)
    terms = _terms(root, settings, stop_to_lift(root), stops)
    enrolled = log.enrolled()
    use = [f"{w.provider}: {w.allowance:g} a week{' (an estimate)' if w.estimated else ''}, keeping {w.reserve_share:.0%} for "
           "you until the week's last day" for w in windows_from(settings)]
    question = [f"Start G6's self-improvement loop on {enrolled.get('domain', 'its domain')}?", f"Folder: {root}",
                f"Python file: {settings.get('domain_file')}, as enrolled by {enrolled['by']} (the name they gave).", *use,
                f"Settings digest: {digest_of(settings)}.", RUNS]
    if stop_reason(root):
        question.append(f"Starting lifts the stop now in place ({stop_reason(root)}). Its file stays, and any write to it after "
                        "that stops the loop again.")
    elif stops > (log.started() or {}).get("stops", 0):
        question.append(f"A stop was asked for since the last start and no STOP file stands for it: {STOPS_FILE} in the folder "
                        "says why. Starting goes past it.")
    if log.paused():
        question.append(f"The loop is paused, so it will spend nothing until you lift that: {log.paused()}"[:300])
    return root, "\n".join([*question, NAME]), terms


def _spawn(root: Path) -> Any:
    """The runner as a process of its own, on the same Python: no console, its own group, its output in its folder."""
    detached = ({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                else {"start_new_session": True})
    with open(root / RUNNER_LOG, "ab") as log:
        return subprocess.Popen([sys.executable, "-P", "-m", "g6.metaharness.loop", "run", str(root)], cwd=root,
                                stdin=subprocess.DEVNULL, stdout=log, stderr=log, close_fds=True, **detached)


def _past_opening(root: Path, since: datetime) -> bool:
    """Whether a runner spawned at ``since`` has got past opening its folder. Its lock cannot say: a runner refuses while it
    holds it. Nor can a process number: on Windows a venv's ``python.exe`` is a launcher, and the runner is a child of it."""
    beat = heartbeat(root) or {}
    try:
        return beat.get("state") not in OPENING and datetime.fromisoformat(beat.get("at", "")) >= since
    except (TypeError, ValueError):
        return False


def _watch(root: Path, process: Any) -> dict:
    """Whether the runner got past opening its folder, or ended first, and how. Ended later, it is past what this can see:
    its last heartbeat is what is left."""
    since, deadline = datetime.now().astimezone(), time.monotonic() + START_WAIT
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


def start(root: Path, who: str, shown: str | None = None) -> dict:
    """Record who started it and start the runner. What the person read may have moved while the dialog was up, so
    ``shown``, the terms ``asked`` gave, must still be what stands, and a change asks them again. The start records what
    it stands on, so a restart holds to the same settings and any stop since closes it. It lifts only the ``STOP`` file
    those terms cover, by recording it: any other stop stands, and nothing starts."""
    cannot = _cannot_start(root)
    if cannot is not None:
        return cannot
    settings, stops, agreed = read_settings(root), stops_asked(root), stop_to_lift(root)
    try:
        if stops < 0:
            raise OSError(f"{STOPS_FILE} cannot be read")      # a restart would read two unreadable counts as one
        if shown is not None and _terms(root, settings, agreed, stops) != shown:
            return _blocked(TERMS_CHANGED, "what the person read is no longer what would start, so nothing was started",
                            ASK_AGAIN, running=False, folder=str(root))
        with Coordination(root / ".tick").mutex():
            CycleLog(root / CYCLE_LOG).start(who, at=datetime.now().astimezone().isoformat(), settings=digest_of(settings),
                                             stops=stops, **agreed)
        why = stop_reason(root)
        if why:
            record_stop(root, why, at=datetime.now().astimezone().isoformat())
            return _blocked(START_STOPPED, f"a stop arrived while the start was being recorded, so nothing was started: {why}"
                            [:500], NOT_ASKED, running=False, folder=str(root))
        return {**_watch(root, _spawn(root)), "started_by": who}
    except (OSError, LedgerError, WorkspaceRefused) as exc:
        return _blocked(START_FAILED, f"the runner could not be started: {type(exc).__name__}: {exc}"[:500], running=False,
                        folder=str(root))


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
                     stop=stop_reason(root), started=started, generation=generations["current"],
                     lineage=generations["recent"],
                     digest={"path": str(page), "text": page.read_text(encoding="utf-8")} if page else None,
                     g6_status=STATUS_LINE)


__all__ = ["NOT_ASKED", "asked", "refused", "start", "status", "stop"]
