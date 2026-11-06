# SPDX-License-Identifier: Apache-2.0
"""The self-improvement loop with nobody at the keyboard: ``python -P -B -X pycache_prefix=<an empty folder> -m
g6.metaharness.loop run <folder>``, which reads and writes no cached bytecode, so only the source the envelope pins runs.

The runner repeats one pass: stop check, tick, then wait until the next tick could be admitted or the next digest is due.
No sleep is longer than ``NAP``, so a waiting runner sees a ``STOP`` within a minute, and a tick meets it at its next model
call. Time comes in through ``now`` and ``sleep``, so a week can pass in a test. Everything it needs to carry on is in the folder's records, so it restarts safely:
a crashed tick is charged its worst case, a rest is read back from the log, and only one runner can hold a folder.

**Who may start it.** It runs only on a start that stands: of the starts and stops on record, the last is a start a person
agreed to in the client's dialog (``g6_improve_start``). So ``run`` from a shell starts nothing of its own, and it is
refused before any of the folder's code runs. A scheduled restart carries on a start until a stop ends it, and a stop ends
it for good, even if its file is later removed: a start records how many stops had been asked for, and any stop since
closes it, whether or not the records could be written at the time. This is a check and not a boundary: a process with the
owner's rights can reach this code, or write the records, which are hash-chained and not signed.

**What it never does.** It starts no engineer, pushes nothing, and spends only within the pacing rule. A pause, a changed
frozen item, records that do not check out or a spent held-out split stop the ticks until a person acts, and the runner
stays up, spends nothing and says so. A failed tick is recorded and the run goes on, so three in a row pause as they would
anywhere. Three in a row that could not be recorded end the run as a stop does, since nothing could show what they cost
(``ShadowLoop.lost_cycle``).

**Nothing to try.** A tick that found nothing to try was still paid for, so the runner rests: ``FIRST_REST`` after the
first, doubling to ``LONGEST_REST``, and the digest says so. A person enrolling or lifting a pause ends the rest.

**A folder** holds ``settings.json``: ``windows`` (``g6.self_maintenance.pacing``), ``read_account`` (false leaves the
meter on its own count), ``python`` (the interpreter ``g6_improve_start`` runs this on, where one is pinned) and
``domain_file``, a Python file the person owns, as ``G6_METAHARNESS`` names one. It lies
outside the folder, since the loop never writes what judges it, and defines ``LOOP = {"domain": ..., "proposer": ...}``,
with ``method``, ``seed``, ``audit_per_tick`` and ``eval_calls`` where they differ from ``LoopSettings``. All of a folder's
calls are billed to one provider, so with more than one window ``provider`` names the one they go to, and nothing runs
without it. Nothing else is read from it, so neither file can reach ``person_free_adoption_outside_shadow``. Enrolling pins
its digest, so an edit is a changed frozen item, and ``g6_improve_start`` refuses it before it could run.

Enrolling, lifting a pause and taking a generation back are a person's, given only in the client's dialog through
``g6_improve_enrol``, ``g6_improve_resume`` and ``g6_improve_demote`` (``g6.mcp.improve``). Each runs here (``act``), on the
interpreter the runner uses, bound to what the dialog showed. ``enrol``, ``resume`` and ``demote`` on the command line
record nothing and say so: there is no terminal fallback and no agent answer.

Early alpha. Individual capabilities have been tested; general application-level improvements have not yet been
established.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
import sys
import time
from collections.abc import Callable, Sequence
from ctypes import wintypes
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from g6.core import stop
from g6.core.ledger import LedgerError, digest as digest_of
from g6.metaharness.api import _blocked
from g6.metaharness.loop import (
    BUDGET_WAIT,
    BUSY,
    CANNOT_RECORD,
    CYCLE_FAILED,
    FROZEN_CHANGED,
    STATUS_LINE,
    STOPPED,
    LoopSettings,
    ShadowLoop,
    must_stop,
)
from g6.metaharness import reflex
from g6.metaharness.schema import CONSENT_DIALOG_ONLY
from g6.metaharness.shadow import lineage
from g6.self_maintenance import digest
from g6.self_maintenance.envelope import (
    CYCLE_LOG,
    ENDS_A_STREAK,
    CycleLog,
    beat,
    file_digest,
    record_stop,
    runner_alive,
    runner_lock,
    stop_reason,
    stops_asked,
)
from g6.self_maintenance.pacing import SETTINGS_FILE, read_settings, windows_from
from g6.self_maintenance.usage import AccountReader
from g6.workspace._envelope import WorkspaceRefused

NAP = 60.0                              # the longest sleep, in seconds: a STOP is seen within this
LEAST_NAP = 1.0                         # the shortest, so nothing here can spin
FIRST_REST = timedelta(minutes=15)      # after the first tick that found nothing to try
LONGEST_REST = timedelta(hours=24)
AGAIN = timedelta(minutes=5)            # how soon to look again when only a person can move things on
BUSY_AGAIN = timedelta(seconds=30)
CANNOT_OPEN = "G6_E_LOOP_CANNOT_OPEN"
NOT_STARTED = "G6_E_LOOP_NOT_STARTED"
SETTINGS_CHANGED = "G6_E_LOOP_SETTINGS_CHANGED"
CACHED_BYTECODE = "G6_E_LOOP_CACHED_BYTECODE"
#: What a tick is refused for that passes on its own: anything else with no cycle waits for a person.
_WAITS = (BUDGET_WAIT, BUSY, STOPPED, stop.STOPPED)
#: What a domain file may set beside its domain and proposer.
_TUNING = ("method", "seed", "audit_per_tick", "eval_calls", "provider")
_MODULE = "g6_loop_domain"


def _local_now() -> datetime:
    return datetime.now().astimezone()


def _in_job() -> bool | None:
    """Whether this process is in a Windows job, whose end ends it however it is detached, or ``None`` where that cannot be
    asked. A run from a venv's ``python.exe`` is: that program is a launcher, and ending it alone ends this process."""
    if os.name != "nt":
        return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.IsProcessInJob.argtypes = (wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL))
    found = wintypes.BOOL()
    return bool(found.value) if kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, ctypes.byref(found)) else None


class DomainChanged(ValueError):
    """The Python file a folder names is not the one a person enrolled."""


def _domain_file(path: Any, pinned: str | None = None) -> dict[str, Any]:
    """What a domain file declares. With ``pinned`` its bytes are read once, checked, and run as they were read, so
    nothing can swap them between the check and the run. ``None`` is a person enrolling it: what they accept is what it
    is now. The pin holds this file, not what it imports."""
    if not path or not Path(str(path)).is_file():
        raise ValueError(f"{SETTINGS_FILE} needs domain_file: the Python file that defines LOOP, not {path!r}")
    source = Path(str(path)).read_bytes()
    if pinned is not None and hashlib.sha256(source).hexdigest() != pinned:
        raise DomainChanged(f"{path} is not the file a person enrolled, so it did not run. A person checks it and enrols "
                            "again")
    spec = importlib.util.spec_from_file_location(_MODULE, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    exec(compile(source, str(path), "exec", dont_inherit=True), module.__dict__)
    declared = getattr(module, "LOOP", None)
    if not isinstance(declared, dict) or "domain" not in declared or "proposer" not in declared:
        raise ValueError(f"{path} must define LOOP = {{'domain': ..., 'proposer': ...}}")
    return declared


def _pinned(root: Path) -> str:
    """The digest of the domain file a person enrolled, from the records and never from the file; ``""`` with none."""
    path = root / CYCLE_LOG
    enrolled = CycleLog(path).enrolled() if path.is_file() else None
    return (enrolled or {}).get("frozen", {}).get("domain_file") or ""


def open_loop(root: Path, *, now: Callable[[], datetime] = _local_now, pinned: str | None = None,
              settings: dict[str, Any] | None = None) -> tuple[ShadowLoop, AccountReader | None]:
    """The loop a folder declares, and what reads its account, when the settings leave that on. ``settings``: what a
    caller already read and checked, so a later write to the file is not what runs."""
    settings = read_settings(root) if settings is None else settings
    windows = windows_from(settings)
    declared = _domain_file(settings.get("domain_file"), pinned)
    reader = AccountReader(windows) if settings.get("read_account", True) is not False else None
    tuned = LoopSettings(windows=windows, **{name: declared[name] for name in _TUNING if name in declared})
    return ShadowLoop(declared["domain"], root, tuned, proposer=declared["proposer"], now=now, read_account=reader,
                      read_now=reader.read_now if reader is not None else None, domain_file=settings["domain_file"]), reader


class Runner:
    def __init__(self, loop: ShadowLoop, *, now: Callable[[], datetime] = _local_now,
                 sleep: Callable[[float], None] = time.sleep, nap: float = NAP, reader: Any = None) -> None:
        self.loop, self.now, self.sleep, self.nap, self.reader = loop, now, sleep, nap, reader
        self.last: dict[str, Any] = {}              # the last tick's reply
        self.not_before: datetime | None = None     # what that reply asked for
        self.day: date | None = None                # the day the digest last covered
        loop.beat_fields = {"pid": os.getpid(), "in_job": _in_job()}

    def run(self) -> int:
        while True:
            try:
                wait = self.step()
            except Exception as exc:  # noqa: BLE001 - the bookkeeping around a tick broke: the next pass tries again
                self._note(state="error", code=CYCLE_FAILED, reason=f"{type(exc).__name__}: {exc}"[:500])
                wait = self.nap
            if wait is None:
                return 0
            self.sleep(wait)

    def _note(self, **fields: Any) -> None:
        """A heartbeat that needs nothing else to work."""
        beat(self.loop.root, **self.loop.beat_fields, **fields, domain=self.loop.domain.name, at=self.now().isoformat())

    def _end(self, now: datetime, halted: str) -> None:
        """The last page and heartbeat, and the stop in the records. None of them may keep a stop from ending the run."""
        record_stop(self.loop.root, halted, at=now.isoformat())
        try:
            self._digest(now, halted)
            self._beat(now, "stopped", now, reason=halted)
        except Exception:  # noqa: BLE001 - see above: the stop wins
            self._note(state="stopped", code=STOPPED, reason=halted)

    def step(self) -> float | None:
        """One pass. The seconds to sleep, or ``None`` when a stop ended the run."""
        now = self.now()
        halted = self.loop.stopped_by()
        if halted:
            self._end(now, halted)
            return None
        if self.day != now.date():
            self._digest(now)               # before the tick, so the page of the day that ended holds that day and no more
        due, resting = self._due(now)
        if now >= due:
            self._tick(now)
            now = self.now()
            due, resting = self._due(now)
            self._digest(now)
        self._beat(now, "resting" if resting else "waiting", due)
        return min(self.nap, max(LEAST_NAP, (due - now).total_seconds()))

    def _rest(self) -> tuple[int, datetime | None]:
        """How many cycles in a row found nothing to try, and when the rest they earned ends."""
        found = self.loop.quiet()
        quiet, last = found.get("quiet", 0), found.get("last_at", "")
        if not quiet or not last:
            return quiet, None
        return quiet, datetime.fromisoformat(last) + min(FIRST_REST * 2 ** min(quiet - 1, 16), LONGEST_REST)

    def _due(self, now: datetime) -> tuple[datetime, bool]:
        """When the next tick may run, and whether a rest is what it waits for."""
        rest = self._rest()[1]
        asked = self.not_before or now
        return (rest, True) if rest is not None and rest > asked else (asked, False)

    def _tick(self, now: datetime) -> None:
        if self.reader is not None:
            self.reader.refresh(now)        # here and never inside a tick, which starts no process
        try:
            self.last = self.loop.tick()
        except Exception as exc:  # noqa: BLE001 - an unattended run records the failure and lives to its next tick
            self.last = self._fault(exc)
        self.not_before = self._after(self.last, self.now())

    def _fault(self, exc: Exception) -> dict[str, Any]:
        try:
            return self.loop.fault(exc)
        except Exception as second:  # noqa: BLE001 - it never reached the log, so the log can never count it
            return self.loop.lost_cycle(second)

    def _after(self, reply: dict[str, Any], now: datetime) -> datetime:
        """When the tick after ``reply`` may run. A cycle that reached a result is followed at once, for the budget and
        the rest to bound. A budget wait lasts until a ceiling can rise, or a reported limit ends if that is sooner."""
        code = reply["warning_card"]["code"]
        if code == STOPPED:
            return now                      # the next pass reads the stop and ends the run
        if code == BUDGET_WAIT:
            room = self.loop.meter.next_room(now)
            told = reply.get("wait_until")
            return min(datetime.fromisoformat(told), room) if told else room
        if code == BUSY:
            return now + BUSY_AGAIN
        return now if reply.get("outcome") in ENDS_A_STREAK else now + AGAIN

    def _use(self, now: datetime) -> list[dict[str, Any]]:
        """This week's use for each provider, with what its account figure rests on."""
        said = getattr(self.reader, "said", {})
        rows = [self.loop.meter.reading(provider, now) for provider in self.loop.meter.windows]
        return [{**{k: v for k, v in asdict(row).items() if k in ("provider", "day", "used", "target", "ceiling", "estimated")},
                 "account": said.get(row.provider, "not read: the meter counts model calls")} for row in rows]

    def _pause(self) -> dict[str, str] | None:
        """What holds the ticks until a person acts, whatever its kind."""
        paused = self.loop.log.paused()
        code = self.last.get("warning_card", {}).get("code", "")
        if self.last.get("outcome") or not code or code in _WAITS:
            return {"code": "G6_E_LOOP_PAUSED", "reason": f"the loop is paused: {paused}"} if paused else None
        return {"code": code, "reason": self.last.get("message", "")}

    def _beat(self, now: datetime, state: str, due: datetime, **said: Any) -> None:
        code = self.last.get("warning_card", {}).get("code", "")
        self.loop.beat_fields["use"] = self._use(now)         # so a heartbeat written during a long tick keeps it
        self.loop._beat(state, code, **{"reason": self.last.get("message", ""), "next_tick": due.isoformat(),
                                        "pause": self._pause(), **said})

    def _decisions(self) -> list[str]:
        """What only a person can settle."""
        found = []
        pause = self._pause()
        if pause is not None and self.loop.log.paused():
            found.append("The loop is paused. Read why below, then lift it with g6_improve_resume, which asks you in the "
                         "client's dialog.")
        elif pause is not None:
            found.append(f"{pause['reason']}. {self.last.get('next_action', '')}")
        return [*found, *(f"The allowance for {w.provider} is an estimate: put the real figure in {SETTINGS_FILE} when there "
                          "is one. A runner alive keeps what it read, and a restart is refused until a new g6_improve_start."
                          for w in self.loop.meter.windows.values() if w.estimated)]

    def _stops(self, now: datetime, halted: str, today: Sequence[dict[str, Any]]) -> list[str]:
        found = [f"The runner ended: {halted}."] if halted else []
        found += [f"A classifier or a permission stopped a request at tick {c['tick']} ({c['classifier_stop']['said']}). It is "
                  "not sent again." for c in today if c.get("classifier_stop")]
        pause = self._pause()
        if pause is not None:
            found.append(pause["reason"])
        quiet, until = self._rest()
        if until is not None and until > now:
            found.append(f"Backing off: {quiet} tick{'s' if quiet != 1 else ''} in a row found nothing to try, so nothing more "
                         f"is spent on it. The next try is at {until.isoformat(timespec='minutes')}.")
        code = self.last.get("warning_card", {}).get("code")
        if code == BUDGET_WAIT:
            found.append(f"Waiting for the budget: {self.last.get('message', '')}")
        if code in (STOPPED, stop.STOPPED) and "charged" in self.last:
            found.append(self.last["message"])
        return found

    def _page(self, day: date, now: datetime, halted: str) -> str:
        cycles = self.loop.log.cycles()
        named = [c for c in cycles if c.get("constraint")]
        today = [c for c in cycles if c.get("at") and datetime.fromisoformat(c["at"]).astimezone(now.tzinfo).date() == day]
        try:
            generations = lineage(self.loop.root / "workspace", last=5)
        except ValueError as exc:
            generations = {"current": "unreadable", "recent": []}
            halted = f"{halted} The generations could not be read: {exc}"[:200].strip()
        # read fresh: a row's own diagnosis opened that cycle, so it cannot know how that cycle ended
        return digest.render(day, domain=self.loop.domain.name, status=STATUS_LINE, cycles=today, use=self._use(now),
                             constraint=named[-1] if named else None, generations=generations,
                             decisions=self._decisions(), stops=self._stops(now, halted, today),
                             reflex=reflex.read(cycles) if any(c.get("reflex") for c in cycles) else None)

    def _digest(self, now: datetime, halted: str = "") -> None:
        """Today's page, and the day just ended, whole, when the date has moved on. A pass closes a day before its tick,
        and only a tick that began on that day can still add to it. A page that cannot be written is left for the next
        pass: it never ends the run."""
        day = now.date()
        try:
            if self.day is not None and self.day != day:
                digest.write(self.loop.root, self.day, self._page(self.day, now, ""))
            digest.write(self.loop.root, day, self._page(day, now, halted))
        except OSError:
            return
        self.day = day


def _gate(root: Path, settings: dict[str, Any]) -> tuple[str, str]:
    """The code and reason that refuse ``settings`` a run on this folder, both empty when a person's start covers it. It
    reads the records and runs nothing."""
    path = root / CYCLE_LOG
    try:
        log = CycleLog(path) if path.is_file() else None
        start = log.started() if log is not None and log.start_in_force() else None
    except LedgerError as exc:
        return NOT_STARTED, f"the loop's records cannot be trusted, so nothing ran: {exc}"[:400]
    if start is None:
        return NOT_STARTED, ("no start stands on this folder, so nothing ran. Only a person starts the loop, in the client's "
                             "dialog, through g6_improve_start; after that a restart carries on until a stop")
    if start.get("stops") != stops_asked(root):
        return NOT_STARTED, ("a stop has been asked for since the last start, so nothing ran: only a new g6_improve_start "
                             "undoes that")
    if start.get("settings") != digest_of(settings):
        return SETTINGS_CHANGED, (f"{SETTINGS_FILE} is not what the person agreed to at the last start, so nothing ran: "
                                  "g6_improve_start shows them what stands now")
    return "", ""


def _not_started(root: Path) -> str:
    """Why no person's start covers this folder as it stands, or ``""`` when one does."""
    return _gate(root, read_settings(root))[1]


def _refuse(root: Path, code: str, why: str, now: Callable[[], datetime]) -> int:
    print(why, file=sys.stderr)
    beat(root, state="not-running", code=code, reason=why, pid=os.getpid(), in_job=_in_job(), at=now().isoformat())
    return 1


def _launch_risk() -> str:
    """Why cached bytecode could run in this process in place of the source the envelope pins, or ``""``. Python runs a
    ``.pyc`` under ``__pycache__`` whose header matches its source, whatever its code, so the runner must read its cache
    from a folder that held none and write none (``g6.mcp.improve._spawn`` starts it so)."""
    prefix = sys.pycache_prefix
    if not sys.dont_write_bytecode or prefix is None:
        return (f"this runner could read cached bytecode in place of G6's source, so nothing ran: start it as python -B -X "
                f"pycache_prefix=<an empty folder> -P -m g6.metaharness.loop run <folder>, as g6_improve_start does "
                f"({CACHED_BYTECODE})")
    if any(Path(prefix).rglob("*.pyc")):
        return f"the folder this runner reads cached bytecode from holds some, so nothing ran: {prefix} ({CACHED_BYTECODE})"
    return ""


def run(root: Path, *, now: Callable[[], datetime] = _local_now, sleep: Callable[[float], None] = time.sleep,
        unsafe: str = "") -> int:
    """Hold the folder and run until a stop. A stop already there ends it before anything is opened, so a restart after
    a reboot honours it, and a folder with no start in force is refused before any of its code runs. ``unsafe``: why this
    process could run something other than the pinned source (``_launch_risk``), which refuses it once the folder is open,
    so its records say why."""
    if not root.is_dir():
        print(f"{root} is not a folder", file=sys.stderr)
        return 1
    halted = stop_reason(root)
    if halted:
        record_stop(root, halted, at=now().isoformat())
        return 0
    if not runner_alive(root):              # before the lock, so a run that dies at once still leaves word that it ran
        beat(root, state="starting", pid=os.getpid(), in_job=_in_job(), at=now().isoformat())
    try:
        with runner_lock(root):
            try:
                settings = read_settings(root)
                code, why = _gate(root, settings)
                if code:
                    return _refuse(root, code, why, now)
                loop, reader = open_loop(root, now=now, pinned=_pinned(root), settings=settings)
            except DomainChanged as exc:
                return _refuse(root, FROZEN_CHANGED, str(exc)[:500], now)
            except Exception as exc:  # noqa: BLE001 - the domain file is the person's own code: say why, and leave word
                return _refuse(root, CANNOT_OPEN, f"The loop could not be opened: {type(exc).__name__}: {exc}"[:500], now)
            if unsafe:
                return _refuse(root, CACHED_BYTECODE, unsafe[:500], now)
            return Runner(loop, now=now, sleep=sleep, reader=reader).run()
    except WorkspaceRefused:
        print("A runner is already alive on this folder; this one did nothing.", file=sys.stderr)
        return 1


#: A person's acts on a folder, each given only in the client's dialog, through its tool (``g6.mcp.improve``).
DIALOG = {"enrol": ("Enrolment", "g6_improve_enrol"), "resume": ("Lifting a pause", "g6_improve_resume"),
          "demote": ("Taking a generation back", "g6_improve_demote")}
#: The code ``g6_improve_start`` gives the same fact: what the person read no longer stands.
TERMS_CHANGED = "G6_E_LOOP_TERMS_CHANGED"


def _dialog_only(act_name: str) -> int:
    """The command line's answer to a person's act: it records nothing, and says where the act is done."""
    what, tool = DIALOG[act_name]
    print(f"{what} is done from a client session over MCP: {tool} asks the person in the client's dialog, and nothing "
          f"else records it. Nothing was recorded ({CONSENT_DIALOG_ONLY}).", file=sys.stderr)
    return 1


def enrol(root: Path, who: str = "") -> int:
    return _dialog_only("enrol")


def resume(root: Path, who: str = "") -> int:
    return _dialog_only("resume")


def demote(root: Path, generation: int = 0, who: str = "") -> int:
    return _dialog_only("demote")


def held(log: CycleLog | None) -> str:
    """What holds the ticks until a person lifts it: the pause on record, or what the cycles themselves say."""
    return (log.paused() or must_stop(log)) if log is not None else ""


def person_terms(act_name: str, root: Path, *, generation: int | None = None) -> str:
    """A digest of all a person's dialog for ``act_name`` shows or stands on, read from the folder as it is now: the whole
    settings file, the Python file's digest, the enrolment on record, what holds the ticks for a pause, and the chain for a
    generation taken back. ``g6.mcp.improve`` binds the dialog to it, and the act checks it again under the folder's
    lock, so a change in between records nothing."""
    settings = read_settings(root)
    path = root / CYCLE_LOG
    log = CycleLog(path) if path.is_file() else None
    seen: dict[str, Any] = {"act": act_name, "settings": settings, "domain_file": file_digest(settings.get("domain_file")),
                            "enrolled": log.enrolled() if log is not None else None}
    if act_name == "resume":
        seen |= {"held": held(log), "cycles": len(log.cycles()) if log is not None else 0}
    if act_name == "demote":
        seen |= {"generation": generation, "lineage": lineage(root / "workspace")}
    return digest_of(seen)


def act(request: dict[str, Any]) -> dict[str, Any]:
    """A person's act that the client's dialog agreed to (``g6.mcp.improve``), in a process like the runner's, so what an
    enrolment freezes is what a tick on that interpreter compares. It runs only the Python file the dialog named, by
    digest (an enrolment's) or the one on record, and records nothing if anything the person read moved since. Like the
    start, this is a check and not a boundary: a process with the owner's rights can reach this code."""
    name, root, who, shown = request["act"], Path(request["folder"]), str(request["who"]), str(request["shown"])
    generation = request.get("generation")
    try:
        settings = read_settings(root)
        if digest_of(settings) != request["settings"]:
            return _blocked(TERMS_CHANGED, f"{SETTINGS_FILE} changed after the person read it, so nothing ran or was "
                            "recorded", "Call the tool again: the person reads what stands now.")
        loop, _ = open_loop(root, pinned=request["domain_file"] if name == "enrol" else _pinned(root), settings=settings)
    except DomainChanged as exc:
        return _blocked(FROZEN_CHANGED, f"{exc}: nothing was recorded"[:500], "Call the tool again: the person reads what "
                        "stands now.")
    except Exception as exc:  # noqa: BLE001 - the domain file is the person's own code: say why
        return _blocked(CANNOT_OPEN, f"The loop could not be opened: {type(exc).__name__}: {exc}"[:500])

    def unchanged(frozen: dict[str, Any] | None = None) -> dict[str, Any] | None:
        try:
            same = person_terms(name, root, generation=generation) == shown
        except (ValueError, LedgerError):
            same = False
        if same and (frozen is None or frozen.get("domain_file") == request["domain_file"]):
            return None
        return _blocked(TERMS_CHANGED, "what the person read is no longer what stands, so nothing was recorded",
                        "Call the tool again: the person reads what stands now, in the client's dialog.")

    try:
        if name == "enrol":
            return loop.enrol(who, last_check=unchanged)
        if name == "resume":
            return loop.resume(who, last_check=unchanged)
        return loop.demote(int(generation), who, last_check=unchanged)
    except Exception as exc:  # noqa: BLE001 - a write that failed: nothing it began counts, and the person may ask again
        return _blocked(CANNOT_RECORD, f"the act could not be recorded: {type(exc).__name__}: {exc}"[:500],
                        "Nothing counts. Free the folder, then call the tool again: the person agrees again.")


def act_main() -> int:
    """The entry ``g6.mcp.improve`` starts: the request on standard input, the reply as the last line of standard output.
    It refuses, as ``run`` does, where cached bytecode could run in place of G6's source."""
    unsafe = _launch_risk()
    try:
        request = json.loads(sys.stdin.read())
        reply = _blocked(CACHED_BYTECODE, unsafe[:500]) if unsafe else act(request)
    except (ValueError, KeyError, TypeError) as exc:
        reply = _blocked(CANNOT_OPEN, f"the request could not be read: {type(exc).__name__}")
    sys.stdout.write(json.dumps(reply, default=str) + "\n")
    return 1 if reply["completion_state"] == "blocked-escalated" else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -P -m g6.metaharness.loop",
                                     description="Run G6's self-improvement loop on a folder, or act on it as a person.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="run ticks until a STOP file appears").add_argument("folder")
    for name, (what, tool) in DIALOG.items():
        command = commands.add_parser(name, help=f"{what.lower()} is done over MCP, with {tool}: this records nothing")
        command.add_argument("folder")
        if name == "demote":
            command.add_argument("generation", nargs="?", help="not used")
        command.add_argument("--as", dest="who", default="", help="not used: a name comes only from the client's dialog")
    args = parser.parse_args(argv)
    if args.command == "run":
        return run(Path(args.folder).expanduser().resolve(), unsafe=_launch_risk())
    return _dialog_only(args.command)


__all__ = ["FIRST_REST", "LONGEST_REST", "NAP", "Runner", "act", "act_main", "demote", "enrol", "held", "main", "open_loop",
           "person_terms", "resume", "run"]
