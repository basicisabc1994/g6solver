# SPDX-License-Identifier: Apache-2.0
"""Review 4 of claude/stop-holds: its nine findings as the reviewer reproduced them, with the reviewer's fixture (its own
temporary folder aside: pytest's serves here). Nothing starts a process or a model."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from g6.core.ledger import digest
from g6.mcp import improve
from g6.metaharness import runner
from g6.metaharness.loop import ShadowLoop
from g6.metaharness.runner import _gate
from g6.self_maintenance import digest as daily
from g6.self_maintenance.envelope import (
    CYCLE_LOG,
    RUNNER_LOCK,
    STOP_FILE,
    STOP_UNREADABLE,
    STOPPED,
    STOPS_FILE,
    CycleLog,
    file_digest,
    heartbeat,
    stop_in_force,
    stop_seen,
)

NOW = datetime(2026, 11, 6, tzinfo=UTC)


@pytest.fixture
def active(tmp_path, monkeypatch):
    root = tmp_path / "loop"
    root.mkdir()
    domain = tmp_path / "domain.py"
    domain.write_text("# Inert review fixture; never executed.\n", encoding="utf-8")
    settings = {"domain_file": str(domain), "read_account": False, "windows": [
        {"provider": "claude", "allowance": 1000000,
         "week_start": "2026-10-26T08:00:00+00:00"}]}
    (root / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    log = CycleLog(root / CYCLE_LOG)
    log.enrol("Ada", {"domain_file": file_digest(domain)}, domain="review")
    log.start("Ada", settings=digest(settings), **stop_seen(root).binding())
    assert not stop_in_force(root), repr(stop_in_force(root))
    spawned = []
    monkeypatch.setattr(improve, "_spawn", lambda *args: spawned.append(args) or object())
    monkeypatch.setattr(improve, "_watch", lambda *args: {"completion_state": "qualified-draft"})
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    return SimpleNamespace(root=root, log=log, settings=settings, spawned=spawned)


def _never_opened(monkeypatch):
    monkeypatch.setattr(runner, "open_loop", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("must not open a real loop")))


@pytest.mark.parametrize("consumer", ["runner", "stop-tool"])
def test_unreadable_deep_json_cannot_abort_termination(active, monkeypatch, consumer):
    """Finding 1."""
    records = active.root / CYCLE_LOG
    records.write_bytes(records.read_bytes() + b"[" * 10000 + b"0" + b"]" * 10000 + b"\n")
    assert stop_in_force(active.root).code == STOP_UNREADABLE
    _never_opened(monkeypatch)
    expected = 1 if consumer == "runner" else improve.STOP_ASKED
    try:
        observed = (runner.run(active.root, now=lambda: NOW) if consumer == "runner" else
                    improve.stop(str(active.root), "owner asks to stop")["warning_card"]["code"])
    except Exception as exc:  # noqa: BLE001 - what escapes is what this looks for
        observed = type(exc).__name__
    assert observed == expected, f"{consumer} escaped as {observed!r} while noting an unreadable stop"
    if consumer == "runner":
        assert heartbeat(active.root)["code"] == STOP_UNREADABLE


def test_preflight_stop_writes_terminal_digest(active, monkeypatch):
    """Finding 2."""
    page = daily.write(active.root, NOW.date(), "previous page: loop still waiting\n")
    (active.root / STOP_FILE).write_text("owner stopped this run\n", encoding="utf-8")
    _never_opened(monkeypatch)
    assert runner.run(active.root, now=lambda: NOW) == 0
    assert heartbeat(active.root)["code"] == STOPPED
    assert "owner stopped this run" in page.read_text(encoding="utf-8"), (
        "terminal heartbeat was written but the day's page still describes the running loop")


def _reformatting_writer(path):
    """Semantically identical chain, but different bytes from the approved reading."""
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    path.write_text("".join(json.dumps(e, separators=(",", ":")) + "\n" for e in entries), encoding="utf-8", newline="\n")
    return CycleLog(path)


def test_failed_start_fsync_leaves_no_start_in_force(active, monkeypatch):
    """Finding 3."""
    active.log.stop("previous authorization ended")
    _, _, shown = improve.asked(str(active.root))
    records = active.root / CYCLE_LOG
    approved = records.read_bytes()
    real_sync = os.fsync
    failed = False

    def fail_start_sync(handle):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("review: start row's fsync failed after flush")
        return real_sync(handle)

    monkeypatch.setattr(os, "fsync", fail_start_sync)
    monkeypatch.setattr(improve, "CycleLog", _reformatting_writer)
    reply = improve.start(active.root, "Ada", shown)
    assert not active.spawned
    assert reply["completion_state"] == "blocked-escalated"
    assert not records.read_bytes().startswith(approved)
    reading = stop_in_force(active.root)
    code, _ = _gate(active.root, active.settings)
    assert reading or code, "failed fsync bypassed revocation and left a restartable start on unapproved bytes"


def test_tick_does_not_raise_on_deeply_nested_records(active):
    """Finding 4."""
    records = active.root / CYCLE_LOG
    records.write_bytes(records.read_bytes() + b"[" * 10000 + b"0" + b"]" * 10000 + b"\n")
    assert stop_in_force(active.root).code == STOP_UNREADABLE
    handle = object.__new__(ShadowLoop)
    handle.root, handle.log, handle.mh = active.root, active.log, None
    handle.cannot_record, handle._records_known = "", None
    handle._beat = lambda *a, **k: None
    try:
        observed = ShadowLoop.tick(handle)["warning_card"]["code"]
    except Exception as exc:  # noqa: BLE001 - what escapes is what this looks for
        observed = type(exc).__name__
    assert observed == STOP_UNREADABLE, f"tick returned {observed!r} instead of the reading's stop code"


def test_ending_reports_that_a_directory_cannot_take_a_stop_row(active):
    """Finding 5."""
    records = active.root / CYCLE_LOG
    records.unlink()
    records.mkdir()
    assert runner.run(active.root, now=lambda: NOW) == 1
    terminal = heartbeat(active.root)
    assert terminal["code"] == STOP_UNREADABLE
    assert terminal.get("not_noted"), "records are a directory, but termination reports no failure to note the stop"


def test_crash_before_append_verification_cannot_leave_unapproved_start(active, monkeypatch):
    """Finding 6."""
    active.log.stop("previous start ended")
    _, _, shown = improve.asked(str(active.root))
    records = active.root / CYCLE_LOG
    approved = records.read_bytes()
    original_start = CycleLog.start

    class Crash(BaseException):
        pass

    def crash_after_durable_append(self, *args, **kwargs):
        original_start(self, *args, **kwargs)
        raise Crash("process died before _appended_onto")

    monkeypatch.setattr(improve, "CycleLog", _reformatting_writer)
    monkeypatch.setattr(CycleLog, "start", crash_after_durable_append)
    try:
        improve.start(active.root, "Ada", shown)
    except Crash:
        pass
    assert not active.spawned and not records.read_bytes().startswith(approved)
    reading = stop_in_force(active.root)
    code, _ = _gate(active.root, active.settings)
    assert reading or code, "restart admits a start on unapproved bytes after a crash before its verification"


@pytest.mark.parametrize("source", ["journal", "recorded-cutoff"])
def test_stop_seen_at_preflight_keeps_its_code_when_settings_are_broken(active, source):
    """Finding 7."""
    if source == "journal":
        (active.root / STOPS_FILE).write_bytes(b"stop writer died before STOP\n")
    else:
        active.log.stop("the previous tick ended this start")
    (active.root / "settings.json").write_bytes(b'{"truncated":')
    reading = stop_in_force(active.root)
    assert reading.code == STOPPED
    runner.run(active.root, now=lambda: NOW)
    observed = heartbeat(active.root)["code"]
    assert observed == STOPPED, f"preflight stop code was replaced with {observed!r}"


def test_lock_folder_replaced_by_file_does_not_escape_termination(active, monkeypatch):
    """Finding 8."""
    (active.root / RUNNER_LOCK).write_bytes(b"a file, not the runner's bookkeeping directory")
    _never_opened(monkeypatch)
    try:
        observed = runner.run(active.root, now=lambda: NOW)
    except Exception as exc:  # noqa: BLE001 - what escapes is what this looks for
        observed = type(exc).__name__
    assert observed == 1, f"runner lock failure escaped as {observed!r} instead of using _ending"
    assert heartbeat(active.root)["state"] != "starting"


@pytest.mark.parametrize("consumer", ["dialog", "start"])
def test_unreadable_stop_is_not_relabelled_as_bad_settings(active, consumer):
    """Finding 9."""
    (active.root / STOPS_FILE).mkdir()
    (active.root / "settings.json").write_bytes(b'{"truncated":')
    assert stop_in_force(active.root).code == STOP_UNREADABLE
    reply = (improve.asked(str(active.root)) if consumer == "dialog" else
             improve.start(active.root, "Ada"))
    observed = reply["warning_card"]["code"]
    assert observed == STOP_UNREADABLE, f"{consumer} replaced the unreadable-stop code with {observed!r}"
    assert not active.spawned
