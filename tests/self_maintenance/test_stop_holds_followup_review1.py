# SPDX-License-Identifier: Apache-2.0
"""Review 1 of claude/stop-holds-followup: its six findings as the reviewer reproduced them, with the reviewer's fixture
(its own temporary folder aside: pytest's serves here). Nothing starts a process or a model."""
from __future__ import annotations

import json
import os
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core.ledger import digest
from g6.mcp import improve
from g6.metaharness import runner
from g6.self_maintenance import digest as daily
from g6.self_maintenance import envelope
from g6.self_maintenance.envelope import (
    CYCLE_LOG,
    STOP_FILE,
    STOP_UNREADABLE,
    STOPPED,
    CycleLog,
    file_digest,
    heartbeat,
    runner_alive,
    stop_in_force,
    stop_seen,
)
from g6.workspace._coordination import Coordination


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))

    def forbidden(*args, **kwargs):
        raise AssertionError("review tests must not start processes")

    monkeypatch.setattr(subprocess, "Popen", forbidden)


@pytest.fixture
def active(tmp_path, monkeypatch):
    root = tmp_path / "loop"
    root.mkdir()
    domain = tmp_path / "domain.py"
    domain.write_text("# Inert review fixture, never executed.\n", encoding="utf-8")
    settings = {"domain_file": str(domain), "read_account": False, "windows": [
        {"provider": "claude", "allowance": 1000000,
         "week_start": "2026-10-26T08:00:00+00:00"}]}
    (root / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    log = CycleLog(root / CYCLE_LOG)
    log.enrol("Ada", {"domain_file": file_digest(domain)}, domain="review")
    log.start("Ada", settings=digest(settings), **stop_seen(root).binding())
    spawned = []
    monkeypatch.setattr(improve, "_spawn", lambda *a: spawned.append(a) or object())
    monkeypatch.setattr(improve, "_watch", lambda *a: {"completion_state": "qualified-draft"})
    return SimpleNamespace(root=root, log=log, settings=settings, spawned=spawned)


@pytest.mark.parametrize("refusal", ["changed-terms", "writer-changed", "writer-error", "dialog-keeps-moving"])
def test_refusals_carry_the_stop_they_did_not_lift(active, monkeypatch, refusal):
    """Finding 1."""
    shown = improve.asked(str(active.root))[2]
    active.log.stop("cut off while the dialog was open")
    before = (active.root / CYCLE_LOG).read_bytes()
    assert stop_in_force(active.root).code == STOPPED
    if refusal != "changed-terms":
        _, _, shown = improve.asked(str(active.root))
    if refusal == "writer-changed":
        def changed_writer(path):
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            path.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
            return CycleLog(path)

        monkeypatch.setattr(improve, "CycleLog", changed_writer)
    elif refusal == "writer-error":
        def failed_writer(path):
            raise OSError("review: writer open denied")

        monkeypatch.setattr(improve, "CycleLog", failed_writer)
    elif refusal == "dialog-keeps-moving":
        real_dialog = improve._dialog

        def moving_dialog(root, reading):
            result = real_dialog(root, reading)
            active.log.pause("changed during dialog construction")
            return result

        monkeypatch.setattr(improve, "_dialog", moving_dialog)
    reply = (improve.asked(str(active.root)) if refusal == "dialog-keeps-moving" else
             improve.start(active.root, "Ada", shown))
    assert not active.spawned
    assert reply["warning_card"]["code"] == (improve.START_FAILED if refusal == "writer-error" else improve.TERMS_CHANGED)
    if refusal in ("changed-terms", "writer-error"):
        assert (active.root / CYCLE_LOG).read_bytes() == before
    assert not CycleLog(active.root / CYCLE_LOG).start_in_force()
    assert reply.get("stop_code") == STOPPED, f"{refusal} refusal hid the liftable stop"


def test_stop_unreadable_after_append_keeps_primary_unreadable_code(active, monkeypatch):
    """Finding 2."""
    _, _, shown = improve.asked(str(active.root))
    real_start = CycleLog.start

    def append_then_stop_path_becomes_unreadable(self, *args, **kwargs):
        result = real_start(self, *args, **kwargs)
        (active.root / STOP_FILE).mkdir()
        return result

    monkeypatch.setattr(CycleLog, "start", append_then_stop_path_becomes_unreadable)
    reply = improve.start(active.root, "Ada", shown)
    assert not active.spawned
    assert stop_in_force(active.root).code == STOP_UNREADABLE
    assert reply["warning_card"]["code"] == STOP_UNREADABLE, reply


def test_workspace_refusal_after_runner_lock_still_writes_terminal_page(active, monkeypatch):
    """Finding 3."""
    now = datetime(2026, 11, 6, tzinfo=UTC)
    # A real workspace bookkeeping refusal, independent of the runner's own lock.
    workspace = active.root / "workspace" / ".operation"
    workspace.mkdir(parents=True)
    spare = active.root / "spare-lock"
    spare.write_bytes(b"lock")
    (workspace / "mutex").hardlink_to(spare)
    opened = []

    def cannot_open_loop(*args, **kwargs):
        opened.append(True)
        with Coordination(workspace).mutex(wait=0):
            raise AssertionError("a hard-linked mutex must be refused")

    monkeypatch.setattr(runner, "open_loop", cannot_open_loop)
    assert runner.run(active.root, now=lambda: now) == 1
    assert opened and not runner_alive(active.root)
    terminal = heartbeat(active.root)
    observed = {"state": terminal.get("state"), "code": terminal.get("code"),
                "page": daily.digest_path(active.root, now.date()).is_file()}
    assert observed == {"state": "not-running", "code": runner.CANNOT_OPEN, "page": True}, observed


@pytest.mark.parametrize("consumer", ["dialog", "start"])
@pytest.mark.parametrize("failure_point", ["stat", "second-read"])
def test_settings_failure_returns_refusal_with_stop(active, monkeypatch, consumer, failure_point):
    """Finding 4."""
    (active.root / STOP_FILE).write_bytes(b"owner stopped the loop\n")
    assert stop_in_force(active.root).code == STOPPED
    real_stat = Path.stat

    def denied(path, *args, **kwargs):
        if path == active.root / "settings.json":
            raise PermissionError("review: settings stat denied")
        return real_stat(path, *args, **kwargs)

    if failure_point == "stat":
        monkeypatch.setattr(Path, "stat", denied)
    else:
        real_read = improve.read_settings
        reads = []

        def break_after_validation(root):
            result = real_read(root)
            reads.append(True)
            if len(reads) == 1:
                (root / "settings.json").write_bytes(b'{"truncated":')
            return result

        monkeypatch.setattr(improve, "read_settings", break_after_validation)
    try:
        reply = (improve.asked(str(active.root)) if consumer == "dialog" else
                 improve.start(active.root, "Ada"))
        observed = (reply["warning_card"]["code"], reply.get("stop_code"))
    except Exception as exc:  # noqa: BLE001 - what escapes is what this looks for
        observed = type(exc).__name__
    assert not active.spawned
    assert observed == (improve.BAD_SETTINGS, STOPPED), f"{consumer} escaped: {observed!r}"


def test_terminal_page_survives_a_ledger_accepted_bad_cycle_time(active, monkeypatch):
    """Finding 5."""
    now = datetime(2026, 11, 6, tzinfo=UTC)
    # Produced by the real ledger, with a valid chain and an accepted cycle.
    active.log.cycle({"outcome": "idle", "at": "noon"})
    page = daily.write(active.root, now.date(), "old page: loop is waiting\n")
    (active.root / STOP_FILE).write_bytes(b"owner stopped this run\n")

    def forbidden(*args, **kwargs):
        raise AssertionError("stopped preflight must not open a loop")

    monkeypatch.setattr(runner, "open_loop", forbidden)
    assert runner.run(active.root, now=lambda: now) == 0
    assert heartbeat(active.root)["code"] == STOPPED
    assert "owner stopped this run" in page.read_text(encoding="utf-8"), "terminal heartbeat preceded a stale page"


def test_start_does_not_commit_a_lift_before_journal_fsync(active, monkeypatch):
    """Finding 6."""
    journal = active.root / envelope.STOPS_FILE
    at_sync, release_sync, synced = threading.Event(), threading.Event(), threading.Event()
    start_done = threading.Event()
    commits, errors = [], []
    real_sync, real_start = os.fsync, envelope.CycleLog.start

    def delayed_journal_sync(fd):
        if journal.exists() and os.fstat(fd).st_ino == journal.stat().st_ino:
            at_sync.set()  # The real writer flushed the bytes, but has not fsynced them.
            if not release_sync.wait(15):
                raise TimeoutError("review did not release journal fsync")
            real_sync(fd)
            synced.set()
            return
        return real_sync(fd)

    def note_start_commit(self, *args, **kwargs):
        result = real_start(self, *args, **kwargs)
        commits.append(synced.is_set())
        return result

    def stop_writer():
        try:
            envelope.ask_to_stop(active.root, "owner's new stop")
        except BaseException as exc:  # noqa: BLE001 - whatever escapes the thread is what this looks for
            errors.append(exc)

    def starter(shown):
        try:
            improve.start(active.root, "Ada", shown)
        except BaseException as exc:  # noqa: BLE001 - as above
            errors.append(exc)
        finally:
            start_done.set()

    monkeypatch.setattr(os, "fsync", delayed_journal_sync)
    monkeypatch.setattr(envelope.CycleLog, "start", note_start_commit)
    stopping = threading.Thread(target=stop_writer)
    starting = None
    try:
        with envelope.stop_lock(active.root):
            # Make the real stop take its explicitly allowed lock-timeout path.
            stopping.start()
            assert at_sync.wait(5), "stop did not reach its journal fsync"
            _, _, shown = improve.asked(str(active.root))
        starting = threading.Thread(target=starter, args=(shown,))
        starting.start()
        # A correct implementation may wait for the in-flight fsync or refuse.
        start_done.wait(2)
    finally:
        release_sync.set()
        stopping.join(10)
        if starting is not None:
            starting.join(10)
    assert not stopping.is_alive() and (starting is None or not starting.is_alive())
    assert not errors, errors
    assert all(commits), f"start rows committed before journal durability: {commits}"
