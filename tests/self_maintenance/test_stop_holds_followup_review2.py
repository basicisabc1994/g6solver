# SPDX-License-Identifier: Apache-2.0
"""Review 2 of claude/stop-holds-followup: its five findings as the reviewer reproduced them, with the reviewer's fixture
(its own temporary folder aside: pytest's serves here), and the classes they belong to, each member and one the old pattern
missed. Nothing starts a process or a model."""
from __future__ import annotations

import ast
import contextlib
import errno
import inspect
import json
import os
import shutil
import stat
import subprocess
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core.ledger import digest
from g6.mcp import improve
from g6.metaharness import loop as loop_module
from g6.metaharness import runner
from g6.self_maintenance import digest as daily
from g6.self_maintenance import envelope
from g6.self_maintenance.envelope import (
    CYCLE_LOG,
    RUNNER_LOCK,
    STOP_FILE,
    STOP_UNREADABLE,
    STOPPED,
    STOPS_FILE,
    CycleLog,
    beat,
    file_digest,
    heartbeat,
    runner_alive,
    runner_lock,
    stop_in_force,
)
from g6.workspace import _coordination
from g6.workspace._envelope import BUSY, OP_FAILED, WorkspaceRefused

NOW = datetime(2026, 11, 6, tzinfo=UTC)
WINDOWS = os.name == "nt"
RECORDS_UNREADABLE = "G6_E_LOOP_RECORDS_UNREADABLE"
DOMAINS = Path(__file__).parents[1] / "metaharness" / "_loop_domains.py"
DOMAIN_FILE = """
import importlib.util
import sys
from pathlib import Path

from g6.metaharness.method import Method

_spec = importlib.util.spec_from_file_location("loop_domains", {rig!r})
domains = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(domains, "WordProblems"):
    _spec.loader.exec_module(domains)

LOOP = {{"domain": domains.WordProblems(Path(__file__).parent), "proposer": domains.proposes({{"prompt": domains.WORKING}}),
        "method": Method(cycles=1, propose_budget=120, select_budget=400, race_block=12, reflect_calls=1),
        "audit_per_tick": 60}}
"""


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))

    def forbidden(*args, **kwargs):
        raise AssertionError("review tests must not start processes")

    monkeypatch.setattr(subprocess, "Popen", forbidden)


@pytest.fixture
def active(tmp_path, monkeypatch):
    """The reviewer's fixture: an enrolled folder with a start in force and an inert domain file that never runs."""
    root = tmp_path / "loop"
    root.mkdir()
    domain = tmp_path / "domain.py"
    domain.write_text("# Inert fixture: never execute.\n", encoding="utf-8")
    settings = {"domain_file": str(domain), "read_account": False, "windows": [
        {"provider": "claude", "allowance": 1000000, "week_start": "2026-10-26T08:00:00+00:00"}]}
    (root / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    log = CycleLog(root / CYCLE_LOG)
    log.enrol("Ada", {"domain_file": file_digest(domain)}, domain="review")
    log.start("Ada", settings=digest(settings), **stop_in_force(root).seen.binding())
    spawned = []
    monkeypatch.setattr(improve, "_spawn", lambda *args: spawned.append(args) or object())
    monkeypatch.setattr(improve, "_watch", lambda *args: {"completion_state": "qualified-draft"})
    return SimpleNamespace(root=root, log=log, settings=settings, spawned=spawned)


@pytest.fixture
def looped(tmp_path):
    """A folder whose domain is a real one, enrolled by a person, for what only a loop that opens can show."""
    (tmp_path / "domain.py").write_text(DOMAIN_FILE.format(rig=str(DOMAINS)), encoding="utf-8")
    root = tmp_path / "loop"
    root.mkdir()
    (root / "settings.json").write_text(json.dumps({
        "domain_file": str(tmp_path / "domain.py"), "read_account": False,
        "windows": [{"provider": "claude", "allowance": 1_000_000, "week_start": "2026-10-26T08:00:00+00:00"}]}),
        encoding="utf-8")
    loop, _ = runner.open_loop(root, now=lambda: NOW)
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    return SimpleNamespace(root=root, loop=loop)


def _code(reply: dict) -> str:
    return reply["warning_card"]["code"]


def _no_loop(*args, **kwargs):
    raise AssertionError("no loop may open")


class SimulatedCrash(BaseException):
    """A process that dies where this is raised: nothing after it runs."""


class Unseen(Exception):
    """An error of a kind no code here names."""


def _lock_fails(monkeypatch, error: BaseException) -> list:
    """Every try for an operating-system lock in the workspace's coordination fails with ``error``, through whichever
    primitive the module takes it with (the C runtime's on the base, its own after), and each try is noted."""
    reached: list = []

    def failed(*args):
        reached.append(args)
        raise error

    if WINDOWS:
        import msvcrt

        real = msvcrt.locking

        def locking(fd, mode, size):
            if mode == msvcrt.LK_UNLCK:
                return real(fd, mode, size)
            return failed(fd)

        monkeypatch.setattr(msvcrt, "locking", locking)
    monkeypatch.setattr(_coordination, "_lock_once", failed, raising=False)
    return reached


def _unlock_fails(monkeypatch) -> list:
    """Every lock this process lets go of fails to be let go, through whichever primitive the module uses."""
    reached: list = []

    def failed(*args):
        reached.append(args)
        raise OSError(errno.EIO, "the lock could not be let go")

    if WINDOWS:
        import msvcrt

        real = msvcrt.locking

        def locking(fd, mode, size):
            if mode == msvcrt.LK_UNLCK:
                return failed(fd)
            return real(fd, mode, size)

        monkeypatch.setattr(msvcrt, "locking", locking)
    monkeypatch.setattr(_coordination, "_unlock", failed, raising=False)
    return reached


#: Errors a try for the lock can meet that are not another holder's lock: none is ever another runner.
LOCK_ERRORS = {
    "EIO": lambda: OSError(errno.EIO, "device I/O error while taking the lock"),
    "access denied, not a sharing violation": lambda: PermissionError(errno.EACCES, "access is denied", None, 5),
    "a Windows error never seen": lambda: OSError(errno.EINVAL, "an error never seen", None, 0x5EED),
    "an exception never seen": lambda: Unseen("an error of a kind never seen"),
}


# The reviewer's five findings, as reproduced ---------------------------------------------------------------------------

def test_stopped_preflight_does_not_overwrite_another_runners_state(active, monkeypatch):
    """Finding 1."""
    monkeypatch.setattr(runner, "open_loop", _no_loop)
    (active.root / STOP_FILE).write_bytes(b"owner asks the live runner to stop\n")
    page = daily.write(active.root, NOW.date(), "live runner's page\n")
    with runner_lock(active.root):
        beat(active.root, state="running", code="live runner", at=NOW.isoformat())
        before = heartbeat(active.root)
        assert runner_alive(active.root)
        runner.run(active.root, now=lambda: NOW)
        assert runner_alive(active.root)
        observed = (heartbeat(active.root) == before, page.read_text(encoding="utf-8") == "live runner's page\n")
        assert observed == (True, True), "second runner overwrote the live holder's heartbeat and page at preflight"


def test_start_does_not_bind_a_replacement_journal_that_was_never_synced(active, monkeypatch):
    """Finding 2."""
    journal = active.root / envelope.STOPS_FILE
    reason = "same stop reason"
    envelope._durably(journal, reason + "\n")
    shown = improve.asked(str(active.root))[2]
    original_inode = journal.stat().st_ino
    real_close, real_sync, real_start = os.close, os.fsync, envelope.CycleLog.start
    synced, committed, replaced = set(), [], []

    def sync(fd):
        inode = os.fstat(fd).st_ino
        if journal.exists() and inode == journal.stat().st_ino and inode != original_inode:
            raise SimulatedCrash()          # the fallback stop has flushed bytes but dies before its fsync or STOP write
        real_sync(fd)
        synced.add(inode)

    def close(fd):
        inode = os.fstat(fd).st_ino
        real_close(fd)
        if inode == original_inode and not replaced:
            replaced.append(True)           # replace the journal as soon as the start has let go of its own handle
            journal.replace(active.root / "old-journal")
            try:
                envelope.ask_to_stop(active.root, reason)
            except SimulatedCrash:
                pass

    def start(self, *args, **kwargs):
        result = real_start(self, *args, **kwargs)
        committed.append(journal.stat().st_ino in synced)
        return result

    monkeypatch.setattr(os, "fsync", sync)
    monkeypatch.setattr(os, "close", close)
    monkeypatch.setattr(envelope.CycleLog, "start", start)
    reply = improve.start(active.root, "Ada", shown)
    assert replaced and journal.stat().st_ino != original_inode
    assert all(committed), f"start bound replacement journal before its fsync: {committed}; reply={reply}"


def test_runner_lock_io_error_is_own_failure_not_another_runner(active, monkeypatch):
    """Finding 3, with the failure injected through whichever primitive takes the lock, and seen to be reached."""
    monkeypatch.setattr(_coordination, "MUTEX_WAIT", 0)
    reached = _lock_fails(monkeypatch, OSError(errno.EIO, "review: device I/O error while taking the lock"))
    monkeypatch.setattr(runner, "open_loop", _no_loop)
    assert runner.run(active.root, now=lambda: NOW) == 1
    terminal = heartbeat(active.root) or {}
    assert reached and terminal.get("code") == runner.CANNOT_OPEN, f"lock I/O failure was treated as another runner: {terminal}"
    assert daily.digest_path(active.root, NOW.date()).is_file()


@pytest.mark.skipif(not WINDOWS, reason="a junction is Windows' own")
def test_broken_records_junction_is_not_absence(active):
    """Finding 4."""
    import _winapi

    missing = active.root / "missing-directory"
    missing.mkdir()
    (active.root / CYCLE_LOG).unlink()
    linked = (active.root / CYCLE_LOG).parent
    linked.rmdir()
    _winapi.CreateJunction(str(missing), str(linked))
    missing.rmdir()
    assert linked.is_junction()
    reading = stop_in_force(active.root)
    assert reading.code == STOP_UNREADABLE, f"broken records junction was read as absent: {reading.code!r}"


def test_status_reports_stop_even_when_a_ledger_accepted_generation_cannot_be_used(active):
    """Finding 5."""
    active.log.cycle({"outcome": "adopted", "head_version": "not-a-generation"})
    (active.root / STOP_FILE).write_bytes(b"owner stopped the loop\n")
    assert stop_in_force(active.root).code == STOPPED
    try:
        observed = improve.status(str(active.root)).get("stop_code")
    except Exception as exc:  # noqa: BLE001 - what escapes is what this looks for
        observed = type(exc).__name__
    assert observed == STOPPED, f"status lost the stop behind an accepted cycle: {observed}"


# Finding 1's class: only the runner lock's holder writes the folder's page and heartbeat ---------------------------------

def _a_journal_stop(f) -> None:
    envelope._durably(f.root / STOPS_FILE, "a stop cut off before its STOP file\n")


#: What a run that another runner's lock refuses can find at its preflight.
PREFLIGHT = {
    "a STOP file": lambda f: (f.root / STOP_FILE).write_bytes(b"owner asks the live runner to stop\n"),
    "a journal stop": _a_journal_stop,
    "a stop on record": lambda f: f.log.stop("a tick cut it off"),
    "a STOP that cannot be read": lambda f: (f.root / STOP_FILE).mkdir(),
    "records that cannot be read": lambda f: (f.root / CYCLE_LOG).write_bytes(b'{"broken":'),
    "no stop": lambda f: None,
}


@pytest.mark.parametrize("state", list(PREFLIGHT))
def test_a_runner_another_holds_the_lock_from_writes_neither_page_nor_heartbeat(active, monkeypatch, state):
    monkeypatch.setattr(_coordination, "MUTEX_WAIT", 0.05)
    monkeypatch.setattr(runner, "open_loop", _no_loop)
    PREFLIGHT[state](active)
    page = daily.write(active.root, NOW.date(), "the live runner's page\n")
    with runner_lock(active.root):
        beat(active.root, state="running", code="the live runner's", at=NOW.isoformat())
        before = heartbeat(active.root)
        runner.run(active.root, now=lambda: NOW)
        observed = (heartbeat(active.root) == before, page.read_text(encoding="utf-8"))
        assert observed == (True, "the live runner's page\n"), observed
        assert runner_alive(active.root)
    runner.run(active.root, now=lambda: NOW)       # a restart once the live runner has let go holds it, and ends as its own
    assert heartbeat(active.root) != before and page.read_text(encoding="utf-8") != "the live runner's page\n"


def test_a_starting_beat_never_lands_on_a_live_runners_heartbeat(active, monkeypatch):
    """The old pattern's miss: a look that found no runner, and a runner that took the lock between the look and the beat."""
    monkeypatch.setattr(_coordination, "MUTEX_WAIT", 0.05)
    monkeypatch.setattr(runner, "open_loop", _no_loop)
    monkeypatch.setattr(runner, "runner_alive", lambda root: False, raising=False)
    with runner_lock(active.root):
        beat(active.root, state="running", code="the live runner's", at=NOW.isoformat())
        before = heartbeat(active.root)
        assert runner.run(active.root, now=lambda: NOW) == 1
        assert heartbeat(active.root) == before, heartbeat(active.root)


def test_an_ending_after_the_lock_is_let_go_writes_nothing_over_the_one_made_while_held(active, monkeypatch):
    """The other miss: what fails as the lock is let go is met once it no longer is."""
    settings = dict(active.settings, read_account=True)           # the gate refuses: these are not the agreed settings
    (active.root / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    monkeypatch.setattr(runner, "open_loop", _no_loop)
    reached = _unlock_fails(monkeypatch)
    runner.run(active.root, now=lambda: NOW)
    terminal = heartbeat(active.root) or {}
    assert reached and terminal.get("code") == runner.SETTINGS_CHANGED, terminal


def test_a_persons_command_writes_no_heartbeat_over_a_live_runners(looped, monkeypatch):
    real_open, opened = runner.open_loop, []

    def opening(*args, **kwargs):
        found = real_open(*args, **kwargs)
        opened.append(found[0])
        return found

    def typed(prompt=""):
        (looped.root / CYCLE_LOG).write_bytes(b'{"broken":')     # the records break once the person's loop is open
        return opened[0].domain.name

    monkeypatch.setattr(runner, "open_loop", opening)
    monkeypatch.setattr(runner, "_at_a_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", typed)
    with runner_lock(looped.root):
        beat(looped.root, state="waiting", code="the live runner's", at=NOW.isoformat())
        before = heartbeat(looped.root)
        runner.resume(looped.root, "Ada")
        assert opened and heartbeat(looped.root) == before, heartbeat(looped.root)


#: What writes the folder's page or its heartbeat, as calls in the code.
WRITERS = {"digest.write", "daily.write", "beat", "beat_holding", "self._beat", "self.loop._beat", "loop._beat"}


def _write_sites(module) -> dict[str, list[str]]:
    """Every call in ``module`` that writes the folder's page or heartbeat, by the function it is made in."""
    found: dict[str, list[str]] = {}

    def walk(node, where: str) -> None:
        for child in ast.iter_child_nodes(node):
            inside = where
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inside = f"{where}.{child.name}" if where else child.name
            if isinstance(child, ast.Call) and ast.unparse(child.func) in WRITERS:
                found.setdefault(where, []).append(ast.unparse(child.func))
            walk(child, inside)

    walk(ast.parse(inspect.getsource(module)), "")
    return found


def _function(module, name: str):
    return next(node for node in ast.walk(ast.parse(inspect.getsource(module)))
                if isinstance(node, ast.FunctionDef) and node.name == name)


def _under(root, target) -> list:
    """The nodes from ``root`` down to ``target``."""
    for child in ast.iter_child_nodes(root):
        if child is target:
            return [root, child]
        path = _under(child, target)
        if path:
            return [root, *path]
    return []


def test_every_page_and_heartbeat_write_is_the_runner_lock_holders():
    assert _write_sites(runner) == {
        "Runner._note": ["beat"], "Runner.step": ["self._beat"], "Runner._beat": ["self.loop._beat"],
        "Runner._pages": ["digest.write", "digest.write"],
        "_ending": ["digest.write", "beat"], "_folder_page": ["digest.write"], "run": ["beat_holding"]}
    # a tick's heartbeat is written in its locked body, ``_tick``, since the freeze follow-up split ``tick`` from it
    assert _write_sites(loop_module) == {
        "_reloaded": ["loop._beat"], "_exclusive.invoke": ["self._beat"], "ShadowLoop._tick": ["self._beat"],
        "ShadowLoop._beat": ["beat"], "ShadowLoop._run": ["self._beat"]}
    ending = _function(runner, "_ending")              # the one ending writes only as the holder, or with no holder shown
    writes = [node for node in ast.walk(ending) if isinstance(node, ast.Call) and ast.unparse(node.func) in WRITERS]
    assert writes and all(any(isinstance(up, ast.If) and ast.unparse(up.test) == "lock in WRITES"
                              for up in _under(ending, call)) for call in writes)
    assert runner.WRITES == (runner.HOLDS, runner.UNTAKEN)
    callers = {name for name in ("run", "_ending", "_folder_page", "_plain_page", "_gated", "enrol", "resume", "demote")
               if "_folder_page" in {ast.unparse(c.func) for c in ast.walk(_function(runner, name)) if isinstance(c, ast.Call)}}
    assert callers == {"_ending"}
    run = _function(runner, "run")
    lines = {ast.unparse(node.targets[0]): node.lineno for node in ast.walk(run) if isinstance(node, ast.Assign)}
    built = [node.lineno for node in ast.walk(run) if isinstance(node, ast.Call) and ast.unparse(node.func) == "Runner"]
    assert built and all(line > lines["taken"] for line in built)  # a Runner exists only once its lock is held
    taken = [node for node in ast.walk(run) if isinstance(node, ast.Assign) and ast.unparse(node) == "taken = True"]
    endings = [node for node in ast.walk(run) if isinstance(node, ast.Call) and ast.unparse(node.func) == "_ending"]
    for call in endings:
        [lock] = [ast.unparse(k.value) for k in call.keywords if k.arg == "lock"]
        if lock == "HOLDS":
            assert call.lineno > taken[0].lineno
        elif lock == "UNTAKEN":
            assert any(isinstance(up, ast.ExceptHandler) for up in _under(run, call)), ast.unparse(call)
        else:
            assert lock in ("NOT_HELD", "NOT_HELD if taken else UNTAKEN"), lock
    assert "lock=HOLDS" in inspect.getsource(runner.Runner._end)
    beat_source = textwrap.dedent(inspect.getsource(loop_module.ShadowLoop._beat))
    # The merge with the consent follow-up: a person's act runs in ``runner.act`` (the terminal path is gone).
    assert "if not self.beats:" in beat_source and "loop.beats = False" in inspect.getsource(runner.act)


# Finding 2's class: a start binds the journal file it flushed and holds ----------------------------------------------------

def _crash_at_a_new_journals_sync(monkeypatch, journal: Path, original: int) -> set:
    real_sync, synced = os.fsync, set()

    def sync(fd):
        inode = os.fstat(fd).st_ino
        if journal.exists() and inode == journal.stat().st_ino and inode != original:
            raise SimulatedCrash()
        real_sync(fd)
        synced.add(inode)

    monkeypatch.setattr(os, "fsync", sync)
    return synced


def test_a_journal_renamed_and_made_again_after_the_flush_is_never_bound(active, monkeypatch):
    """The review's interleaving, with the swap between the start's flush and the reading it checks."""
    journal, reason = active.root / STOPS_FILE, "same stop reason"
    envelope._durably(journal, reason + "\n")
    shown = improve.asked(str(active.root))[2]
    synced = _crash_at_a_new_journals_sync(monkeypatch, journal, journal.stat().st_ino)
    real_flush, real_reading, real_start = improve.flush_journal, improve.stop_in_force, CycleLog.start
    flushed, swapped, committed = [], [], []

    def flush(root, seen):
        flushed.append(True)
        return real_flush(root, seen)

    def reading(root, *args, **kwargs):
        if flushed and not swapped:
            try:
                journal.replace(active.root / "old-journal")
            except PermissionError:
                swapped.append(False)       # the start holds its journal open: Windows renames nothing it holds so
            else:
                swapped.append(True)
                try:
                    envelope.ask_to_stop(active.root, reason)    # the same bytes again, and a death before their fsync
                except SimulatedCrash:
                    pass
        return real_reading(root, *args, **kwargs)

    def start(self, *args, **kwargs):
        result = real_start(self, *args, **kwargs)
        committed.append(journal.stat().st_ino in synced)
        return result

    monkeypatch.setattr(improve, "flush_journal", flush)
    monkeypatch.setattr(improve, "stop_in_force", reading)
    monkeypatch.setattr(CycleLog, "start", start)
    reply = improve.start(active.root, "Ada", shown)
    assert flushed and swapped
    assert all(committed), f"the start bound a journal file it never flushed: {committed}; {reply}"
    if swapped[0]:
        assert _code(reply) == improve.TERMS_CHANGED and not committed and not active.spawned, reply


def test_a_start_whose_reading_is_not_of_the_journal_file_it_flushed_writes_no_row(active, monkeypatch):
    envelope._durably(active.root / STOPS_FILE, "a stop the start lifts\n")
    shown = improve.asked(str(active.root))[2]
    real = envelope.flush_journal

    @contextlib.contextmanager
    def another_file(root, seen):
        with real(root, seen) as flushed:
            yield (0, 1) if flushed is None else (flushed[0], flushed[1] + 1)

    monkeypatch.setattr(improve, "flush_journal", another_file)
    before = (active.root / CYCLE_LOG).read_bytes()
    reply = improve.start(active.root, "Ada", shown)
    assert _code(reply) == improve.TERMS_CHANGED and not active.spawned, reply
    assert (active.root / CYCLE_LOG).read_bytes() == before


def test_a_journal_that_appears_between_the_reading_and_the_flush_stands_and_starts_nothing(active, monkeypatch):
    """A reading that found no journal binds the empty one: a journal made after it is a stop that stands."""
    shown = improve.asked(str(active.root))[2]
    real_flush = improve.flush_journal

    def flush(root, seen):
        (active.root / STOPS_FILE).write_bytes(b"a stop that came after the reading\n")
        return real_flush(root, seen)

    monkeypatch.setattr(improve, "flush_journal", flush)
    reply = improve.start(active.root, "Ada", shown)
    assert reply["completion_state"] == "blocked-escalated" and not active.spawned, reply
    assert stop_in_force(active.root).code == STOPPED and runner._gate(active.root, active.settings)[0] == STOPPED


def test_a_journal_that_cannot_be_read_after_the_flush_writes_no_row(active, monkeypatch):
    envelope._durably(active.root / STOPS_FILE, "a stop the start lifts\n")
    shown = improve.asked(str(active.root))[2]
    real_flush, real_read = improve.flush_journal, envelope._read_file
    flushed = []

    def flush(root, seen):
        flushed.append(True)
        return real_flush(root, seen)

    def read(path, root, **kwargs):
        if flushed and path.name == STOPS_FILE:
            raise envelope.StopUnreadable("the journal cannot be read now")
        return real_read(path, root, **kwargs)

    monkeypatch.setattr(improve, "flush_journal", flush)
    monkeypatch.setattr(envelope, "_read_file", read)
    before = (active.root / CYCLE_LOG).read_bytes()
    reply = improve.start(active.root, "Ada", shown)
    assert _code(reply) == STOP_UNREADABLE and not active.spawned, reply
    assert (active.root / CYCLE_LOG).read_bytes() == before


def test_a_read_only_journal_is_held_to_read_and_still_refuses_no_start(active):
    journal = active.root / STOPS_FILE
    envelope._durably(journal, "an old stop the start lifts\n")
    shown = improve.asked(str(active.root))[2]
    os.chmod(journal, stat.S_IREAD)
    try:
        reply = improve.start(active.root, "Ada", shown)
    finally:
        os.chmod(journal, stat.S_IREAD | stat.S_IWRITE)
    assert reply["completion_state"] == "qualified-draft" and active.spawned, reply


def test_a_stop_between_the_flush_and_the_reading_stands_and_starts_nothing(active, monkeypatch):
    shown = improve.asked(str(active.root))[2]
    real_flush, flushed = improve.flush_journal, []

    def a_stop():
        envelope.ask_to_stop(active.root, "a stop between the flush and the reading")

    def flush(root, seen):
        flushed.append(True)
        found = real_flush(root, seen)
        if not hasattr(found, "__enter__"):          # a flush that is done once called: the stop comes after it
            a_stop()
            return found

        @contextlib.contextmanager
        def then_a_stop():                           # a flush held open: the stop comes once it is on disk and held
            with found as held:
                a_stop()
                yield held

        return then_a_stop()

    monkeypatch.setattr(improve, "flush_journal", flush)
    reply = improve.start(active.root, "Ada", shown)
    assert flushed and reply["completion_state"] == "blocked-escalated" and not active.spawned, reply
    assert stop_in_force(active.root).code == STOPPED


# Finding 3's class: only the lock's own contention is another holder --------------------------------------------------------

@pytest.mark.parametrize("windows, error, held", [
    (True, OSError(0, "ERROR_LOCK_VIOLATION", None, 33), True),
    (True, OSError(0, "ERROR_ACCESS_DENIED", None, 5), False),
    (True, OSError(0, "ERROR_SHARING_VIOLATION", None, 32), False),
    (True, OSError(0, "ERROR_IO_DEVICE", None, 1117), False),
    (True, OSError(0, "never seen", None, 0x5EED), False),
    (True, OSError(errno.EACCES, "the C runtime's EACCES, no Windows code"), False),
    (False, BlockingIOError(errno.EWOULDBLOCK, "EWOULDBLOCK"), True),
    (False, OSError(errno.EAGAIN, "EAGAIN"), True),
    (False, OSError(errno.EACCES, "EACCES from fcntl standing in for flock"), True),
    (False, OSError(errno.EIO, "EIO"), False),
    (False, OSError(errno.EPERM, "EPERM"), False),
    (False, OSError(errno.EBADF, "EBADF"), False),
    (False, OSError(4242, "never seen"), False),
    (True, Unseen("not an OSError"), False),
    (False, Unseen("not an OSError"), False),
])
def test_only_the_named_errors_of_a_held_lock_are_another_holder(windows, error, held):
    if windows and not WINDOWS and isinstance(error, OSError):
        pytest.skip("an OSError carries a Windows code only on Windows")
    assert _coordination.another_holds(error, windows=windows) is held


@pytest.mark.parametrize("error", list(LOCK_ERRORS))
def test_a_lock_that_cannot_be_taken_is_this_runners_own_ending_with_page_and_heartbeat(active, monkeypatch, error):
    monkeypatch.setattr(_coordination, "MUTEX_WAIT", 0)
    reached = _lock_fails(monkeypatch, LOCK_ERRORS[error]())
    monkeypatch.setattr(runner, "open_loop", _no_loop)
    assert runner.run(active.root, now=lambda: NOW) == 1
    terminal = heartbeat(active.root) or {}
    assert reached and (terminal.get("state"), terminal.get("code")) == ("not-running", runner.CANNOT_OPEN), terminal
    assert daily.digest_path(active.root, NOW.date()).is_file()


@pytest.mark.parametrize("error", list(LOCK_ERRORS))
def test_the_mutex_raises_what_kept_it_from_the_lock_and_never_says_busy(tmp_path, monkeypatch, error):
    monkeypatch.setattr(_coordination, "MUTEX_WAIT", 0)
    reached, entered, raised = _lock_fails(monkeypatch, LOCK_ERRORS[error]()), [], None
    try:
        with _coordination.Coordination(tmp_path / "book").mutex():
            entered.append(True)
    except BaseException as exc:  # noqa: BLE001 - what the mutex raises is what this looks at
        raised = exc
    assert reached and not entered and raised is not None
    assert not (isinstance(raised, WorkspaceRefused) and raised.code == BUSY), raised


def test_a_workspace_op_whose_lock_fails_is_the_machines_failure_not_busy(monkeypatch):
    from g6.workspace import _ops

    monkeypatch.setattr(_coordination, "MUTEX_WAIT", 0)
    reached = _lock_fails(monkeypatch, OSError(errno.EIO, "device I/O error"))
    result = _ops.run("write", "lock-fails", lambda ws: ws.write("a.txt", "text", dry_run=False), {})
    assert reached and result.code == OP_FAILED, result


@pytest.mark.parametrize("error", list(LOCK_ERRORS))
def test_whether_a_runner_is_alive_is_never_read_from_a_lock_that_cannot_be_tried(active, monkeypatch, error):
    with runner_lock(active.root):
        pass                                                     # the lock's folder and file, made
    reached = _lock_fails(monkeypatch, LOCK_ERRORS[error]())
    try:
        alive = runner_alive(active.root)
    except BaseException as exc:  # noqa: BLE001 - a lock that cannot be tried may raise; it may never say alive
        alive = exc
    assert reached and alive is not True and alive is not False, alive


def test_status_start_and_stop_say_a_runner_lock_cannot_be_tried_and_never_read_it_as_alive(active, monkeypatch):
    with runner_lock(active.root):
        pass
    (active.root / STOP_FILE).write_bytes(b"owner stopped the loop\n")
    _lock_fails(monkeypatch, OSError(errno.EIO, "device I/O error"))
    status = improve.status(str(active.root))
    assert status["stop_code"] == STOPPED and status["running"] is None, status
    started = improve.start(active.root, "Ada")
    assert _code(started) != improve.RUNNER_ALIVE and started["stop_code"] == STOPPED and not active.spawned, started
    stopped = improve.stop(str(active.root), "again")
    assert _code(stopped) == improve.STOP_ASKED and stopped["running"] is None and stopped["stopped"] is False, stopped
    assert "cannot be read" in stopped["message"]


def test_a_stop_that_cannot_take_the_records_lock_says_so_and_never_that_a_tick_holds_them(active, monkeypatch):
    monkeypatch.setattr(_coordination, "MUTEX_WAIT", 0)
    _lock_fails(monkeypatch, OSError(errno.EIO, "device I/O error"))
    said = envelope.record_stop(active.root, "why", at=NOW.isoformat())
    assert said and "held by a tick" not in said, said


def _a_linked_tick_folder(f, monkeypatch) -> None:
    if not WINDOWS:
        pytest.skip("a junction is Windows' own")
    import _winapi

    _clear(f.root / ".tick")
    elsewhere = f.root.parent / "elsewhere"
    elsewhere.mkdir()
    _winapi.CreateJunction(str(elsewhere), str(f.root / ".tick"))


TICK_LOCKS = {"EIO": lambda f, m: _lock_fails(m, OSError(errno.EIO, "device I/O error")),
              "a linked lock folder": _a_linked_tick_folder}


@pytest.mark.parametrize("failure", list(TICK_LOCKS))
def test_a_tick_lock_that_cannot_be_taken_is_never_another_operation(looped, monkeypatch, failure):
    monkeypatch.setattr(_coordination, "MUTEX_WAIT", 0)
    TICK_LOCKS[failure](looped, monkeypatch)
    try:
        said = _code(looped.loop.quiet())
    except Exception as exc:  # noqa: BLE001 - a lock that could not be taken may raise; it may never be another operation
        said = type(exc).__name__
    assert said != loop_module.BUSY, said


def test_a_tick_lock_another_holds_is_another_operation(looped, monkeypatch):
    monkeypatch.setattr(_coordination, "MUTEX_WAIT", 0)
    with _coordination.Coordination(looped.root / ".tick").mutex():
        assert _code(looped.loop.quiet()) == loop_module.BUSY


# Finding 4's class: an entry that is there is never absent --------------------------------------------------------------------

#: Each stop path, as a name under the loop's folder.
STOP_PATHS = {"STOP": Path(STOP_FILE), "the journal": Path(STOPS_FILE), "the records": CYCLE_LOG,
              "the records' folder": CYCLE_LOG.parent}


def _clear(path: Path) -> None:
    if path.is_dir() and not path.is_junction():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


@pytest.mark.skipif(not WINDOWS, reason="a junction is Windows' own")
@pytest.mark.parametrize("where", list(STOP_PATHS))
def test_a_broken_junction_at_any_stop_path_is_a_stop_in_force(active, where):
    import _winapi

    path = active.root / STOP_PATHS[where]
    _clear(path)
    missing = active.root / "missing-directory"
    missing.mkdir()
    _winapi.CreateJunction(str(missing), str(path))
    missing.rmdir()
    assert stop_in_force(active.root).code == STOP_UNREADABLE
    assert _code(improve.start(active.root, "Ada")) == STOP_UNREADABLE and not active.spawned


@pytest.mark.parametrize("where", list(STOP_PATHS))
def test_a_broken_symbolic_link_at_any_stop_path_is_a_stop_in_force(active, where):
    path = active.root / STOP_PATHS[where]
    _clear(path)
    try:
        os.symlink(active.root / "nowhere", path, target_is_directory=where == "the records' folder")
    except OSError as exc:
        pytest.skip(f"this machine makes no symbolic link ({exc})")
    assert stop_in_force(active.root).code == STOP_UNREADABLE
    assert _code(improve.start(active.root, "Ada")) == STOP_UNREADABLE and not active.spawned


def _an_entry_seen_as(monkeypatch, path: Path, mode: int | None, *, unlistable: bool = False) -> None:
    """``path`` cannot be opened (not found), and ``lstat`` finds an entry of ``mode`` there; ``None`` for an entry that
    cannot be looked at. With ``unlistable``, listing it fails as listing a broken link does."""
    real_open, real_lstat, real_scandir = Path.open, os.lstat, os.scandir

    def opened(self, *args, **kwargs):
        if self == path or path in self.parents:
            raise FileNotFoundError(2, "the system cannot find the file", str(self))
        return real_open(self, *args, **kwargs)

    def looked(name, *args, **kwargs):
        if Path(name) == path:
            if mode is None:
                raise PermissionError(13, "access is denied", str(name))
            return os.stat_result((mode, 0, 0, 1, 0, 0, 0, 0, 0, 0))
        if path in Path(name).parents:
            raise FileNotFoundError(2, "the system cannot find the path", str(name))
        return real_lstat(name, *args, **kwargs)

    def listed(name=".", *args, **kwargs):
        if unlistable and Path(name) == path:
            raise FileNotFoundError(2, "the system cannot find the path", str(name))
        return real_scandir(name, *args, **kwargs)

    monkeypatch.setattr(Path, "open", opened)
    monkeypatch.setattr(os, "lstat", looked)
    monkeypatch.setattr(os, "scandir", listed)


@pytest.mark.parametrize("where", list(STOP_PATHS))
@pytest.mark.parametrize("entry", ["a broken symbolic link", "an entry that cannot be looked at"])
def test_an_entry_lstat_finds_or_cannot_look_at_is_never_absence(active, monkeypatch, where, entry):
    mode = stat.S_IFLNK | 0o777 if entry == "a broken symbolic link" else None
    _an_entry_seen_as(monkeypatch, active.root / STOP_PATHS[where], mode, unlistable=True)
    assert stop_in_force(active.root).code == STOP_UNREADABLE


def test_a_records_folder_that_is_a_working_junction_to_an_empty_folder_is_absent_as_a_read_through_it_is(active):
    if not WINDOWS:
        pytest.skip("a junction is Windows' own")
    import _winapi

    _clear(active.root / CYCLE_LOG.parent)
    empty = active.root.parent / "empty"
    empty.mkdir()
    _winapi.CreateJunction(str(empty), str(active.root / CYCLE_LOG.parent))
    found = stop_in_force(active.root)
    assert found.code == "" and found.log is None and "records" not in found.unread


@pytest.mark.skipif(not WINDOWS, reason="a junction is Windows' own")
def test_a_stop_noted_on_a_broken_records_junction_says_it_could_not_be_noted(active):
    import _winapi

    _clear(active.root / CYCLE_LOG.parent)
    missing = active.root / "missing-directory"
    missing.mkdir()
    _winapi.CreateJunction(str(missing), str(active.root / CYCLE_LOG.parent))
    missing.rmdir()
    assert "cannot be read" in envelope.record_stop(active.root, "why", at=NOW.isoformat())


@pytest.mark.skipif(not WINDOWS, reason="a junction is Windows' own")
def test_a_broken_junction_at_the_runner_lock_is_never_no_runner(active):
    import _winapi

    missing = active.root / "missing-directory"
    missing.mkdir()
    _winapi.CreateJunction(str(missing), str(active.root / RUNNER_LOCK))
    missing.rmdir()
    try:
        alive = runner_alive(active.root)
    except Exception as exc:  # noqa: BLE001 - a lock that cannot be looked at may raise; it may never be no runner
        alive = exc
    assert alive is not False, alive


# Finding 5's class: status reports the stop whatever else it cannot read -----------------------------------------------------

def _raises(kind):
    def broken(*args, **kwargs):
        raise kind("status cannot read this")
    return broken


def _a_cycle_with_a_constraint_and_no_tick(f, m) -> None:
    f.log._append("cycle", {"outcome": "idle", "constraint": "a constraint with no tick"})


def _a_runner_lock_that_cannot_be_tried(f, m) -> None:
    with runner_lock(f.root):
        pass
    _lock_fails(m, OSError(errno.EIO, "device I/O error"))


def _a_page_that_cannot_be_read(f, m) -> None:
    daily.digest_path(f.root, NOW.date()).mkdir(parents=True)


#: Each other thing status reads, made unusable, with a stop in force.
STATUS_BREAKS = {
    "an adopted cycle's head is not a generation": lambda f, m: f.log.cycle({"outcome": "adopted",
                                                                             "head_version": "not-a-generation"}),
    "a cycle with a constraint and no tick": _a_cycle_with_a_constraint_and_no_tick,
    "the runner's lock cannot be tried": _a_runner_lock_that_cannot_be_tried,
    "the heartbeat cannot be read": lambda f, m: m.setattr(improve, "heartbeat", _raises(OSError)),
    "the chain of generations breaks": lambda f, m: m.setattr("g6.metaharness.shadow.lineage", _raises(RuntimeError)),
    "the latest page cannot be read": _a_page_that_cannot_be_read,
    "the folder cannot be looked at": lambda f, m: m.setattr(improve, "_is_a_loop", _raises(PermissionError)),
    "the folder cannot be found": lambda f, m: m.setattr(improve, "_root", _raises(RuntimeError)),
}


@pytest.mark.parametrize("broken", list(STATUS_BREAKS))
def test_status_reports_the_stop_and_says_what_it_cannot_read_never_raising(active, monkeypatch, broken):
    (active.root / STOP_FILE).write_bytes(b"owner stopped the loop\n")
    STATUS_BREAKS[broken](active, monkeypatch)
    try:
        reply = improve.status(str(active.root))
    except Exception as exc:  # noqa: BLE001 - what escapes is what this looks for
        pytest.fail(f"status raised {type(exc).__name__}: {exc}")
    assert reply["stop_code"] == STOPPED and reply["completion_state"] == "qualified-draft", reply
    assert (reply["pause"] or {}).get("code") == RECORDS_UNREADABLE, reply


def test_status_with_nothing_broken_still_reports_all_it_reads(active):
    (active.root / STOP_FILE).write_bytes(b"owner stopped the loop\n")
    reply = improve.status(str(active.root))
    assert reply["stop_code"] == STOPPED and reply["running"] is False and reply["pause"] is None, reply
    assert reply["started"] and reply["cycles"] == 0
