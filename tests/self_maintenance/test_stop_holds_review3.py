# SPDX-License-Identifier: Apache-2.0
"""Review 3 of claude/stop-holds: its six findings as the reviewer reproduced them, with the reviewer's fixtures (its own
temporary folder aside: pytest's serves here). Finding 1 swaps the file once the bytes are read from the handle the reading
opened, where the reviewer's swapped it after ``Path.read_bytes``, which the reading no longer calls. No process starts."""
from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core.ledger import digest
from g6.mcp import improve
from g6.metaharness import runner
from g6.metaharness.loop import ShadowLoop
from g6.metaharness.runner import Runner
from g6.self_maintenance.envelope import (
    CYCLE_LOG,
    STOP_FILE,
    STOP_UNREADABLE,
    STOPPED,
    STOPS_FILE,
    CycleLog,
    beat,
    file_digest,
    heartbeat,
    stop_in_force,
    stop_seen,
    stop_standing,
)


@pytest.fixture(autouse=True)
def isolated_review(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))

    def forbidden(*args, **kwargs):
        raise AssertionError("review tests must not launch processes")

    monkeypatch.setattr(subprocess, "Popen", forbidden)


@pytest.fixture
def active(tmp_path, monkeypatch):
    root = tmp_path / "loop"
    root.mkdir()
    domain = tmp_path / "domain.py"
    domain.write_text("# Inert: never executed.\n", encoding="utf-8")
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


def test_replacement_inode_is_compared_with_the_file_actually_read(active, monkeypatch):
    """Finding 1."""
    records = active.root / CYCLE_LOG
    old = records.read_bytes()
    active.log.stop("durable cut-off in the replacement")
    revoked = records.read_bytes()
    # Equal sizes, but DIFFERENT inodes: this is not a same-inode rewrite.
    records.write_bytes(old + b" " * (len(revoked) - len(old)))
    replacement = active.root / "replacement.jsonl"
    replacement.write_bytes(revoked)
    assert records.stat().st_size == replacement.stat().st_size
    assert records.stat().st_ino != replacement.stat().st_ino
    original_open = Path.open
    replaced = False

    class SwapsAfterTheRead:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self.handle.__exit__(*exc)

        def fileno(self):
            return self.handle.fileno()

        def read(self, *args):
            nonlocal replaced
            data = self.handle.read(*args)
            if not replaced:
                replaced = True
                self.handle.close()            # Windows replaces no file held open
                os.replace(replacement, records)
            return data

    def opening(path, mode="r", *args, **kwargs):
        handle = original_open(path, mode, *args, **kwargs)
        return SwapsAfterTheRead(handle) if path == records and "r" in mode and not replaced else handle

    monkeypatch.setattr(Path, "open", opening)
    reading = stop_in_force(active.root)
    assert replaced and records.read_bytes() == revoked
    assert reading.code == STOPPED, (
        f"accepted bytes from a replaced inode and missed its cut-off: {reading.code!r}")


def test_compatibility_reader_honors_the_same_recorded_cutoff(active):
    """Finding 2."""
    active.log.stop("this start was durably revoked")
    reading = stop_in_force(active.root)
    assert reading.code == STOPPED
    observed = stop_standing(active.root)
    assert observed == reading.reason, (
        f"stop_standing reports {observed!r} for the same revoked start")


def test_preflight_stop_publishes_the_terminal_reading(active, monkeypatch):
    """Finding 3."""
    beat(active.root, state="waiting", code="old-tick-code")
    (active.root / STOP_FILE).write_bytes(b"owner asks to stop\n")
    assert stop_in_force(active.root).code == STOPPED

    def forbidden(*args, **kwargs):
        raise AssertionError("preflight must end without opening a loop or runner")

    monkeypatch.setattr(runner, "open_loop", forbidden)
    monkeypatch.setattr(runner, "Runner", forbidden)
    runner.run(active.root, now=lambda: datetime(2026, 11, 5, tzinfo=UTC))
    terminal = heartbeat(active.root)
    assert terminal["code"] == STOPPED, (
        f"preflight returned on STOP but left heartbeat {terminal!r}")


def test_start_does_not_implicitly_lift_records_replaced_after_its_reading(active, monkeypatch):
    """Finding 4."""
    records = active.root / CYCLE_LOG
    _, _, shown = improve.asked(str(active.root))
    before = records.read_bytes()
    # Prepare a valid durable cut-off chain. It replaces the log only after
    # start's fresh reading/terms check, before the writer reopens that log.
    active.log.stop("cut-off which the dialog never saw")
    cut_off = records.read_bytes()
    replacement = active.root / "replacement.jsonl"
    replacement.write_bytes(cut_off)
    records.write_bytes(before)
    changed = False

    def replace_before_writer_opens(path):
        nonlocal changed
        assert path == records
        os.replace(replacement, records)
        changed = True
        return CycleLog(path)

    monkeypatch.setattr(improve, "CycleLog", replace_before_writer_opens)
    reply = improve.start(active.root, "Ada", shown)
    assert changed
    assert not active.spawned, (
        "start spawned after its writer reopened and lifted an unseen cut-off chain")
    assert reply["completion_state"] == "blocked-escalated"
    assert records.read_bytes() == cut_off, "a refused start must append no start row"
    assert stop_in_force(active.root).revoked


def test_failed_record_stat_cannot_prevent_a_waiting_runner_from_ending(active, monkeypatch):
    """Finding 5."""
    records = active.root / CYCLE_LOG
    real_stat = Path.stat

    def denied(path, *args, **kwargs):
        if path == records:
            raise PermissionError("review: record stat denied")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied)
    assert stop_in_force(active.root).code == STOP_UNREADABLE
    now = datetime(2026, 11, 5, tzinfo=UTC)
    loop = object.__new__(ShadowLoop)
    loop.root, loop.log = active.root, active.log
    loop.cannot_record, loop._on_record = "", None
    loop.domain, loop._now = SimpleNamespace(name="review"), lambda: now
    loop.meter = SimpleNamespace(windows={})
    handle = Runner(loop, now=lambda: now)
    monkeypatch.setattr(handle, "_digest", lambda *a: None)
    try:
        observed = handle.step()
    except Exception as exc:
        observed = type(exc).__name__
    assert observed is None, f"stop termination escaped as {observed}; Runner.run would retry it"
    assert heartbeat(active.root)["code"] == STOP_UNREADABLE


@pytest.mark.parametrize("source", ["journal-only", "recorded-cutoff"])
def test_gate_exit_keeps_the_code_of_the_stop_that_ended_it(active, monkeypatch, source):
    """Finding 6."""
    if source == "journal-only":
        (active.root / STOPS_FILE).write_bytes(b"stop writer died before STOP\n")
    else:
        active.log.stop("a tick durably ended the last start")
    reading = stop_in_force(active.root)
    assert reading.code == STOPPED

    def forbidden(*args, **kwargs):
        raise AssertionError("gate must end without opening a loop or runner")

    monkeypatch.setattr(runner, "open_loop", forbidden)
    monkeypatch.setattr(runner, "Runner", forbidden)
    runner.run(active.root, now=lambda: datetime(2026, 11, 5, tzinfo=UTC))
    observed = heartbeat(active.root)["code"]
    assert observed == reading.code, (
        f"gate exit replaced the terminating stop's code with {observed}")
