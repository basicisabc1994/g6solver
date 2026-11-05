# SPDX-License-Identifier: Apache-2.0
"""Review 2 of claude/stop-holds: its nine findings as the reviewer reproduced them, with the reviewer's fixtures (the
review's own temporary folder aside: pytest's serves here). Nothing starts a process."""
from __future__ import annotations

import json
import os
import subprocess
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core.ledger import ChainLedger, LedgerError
from g6.mcp import improve
from g6.metaharness import loop as loop_module
from g6.metaharness import runner
from g6.metaharness.loop import ShadowLoop
from g6.metaharness.runner import Runner, _gate
from g6.self_maintenance.envelope import (
    CYCLE_LOG,
    STOP_FILE,
    STOP_UNREADABLE,
    STOPPED,
    STOPS_FILE,
    CycleLog,
    heartbeat,
    stop_in_force,
    stop_seen,
)


@pytest.fixture(autouse=True)
def isolated_review(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))

    def no_process(*args, **kwargs):
        raise AssertionError("review tests must not start a process")

    monkeypatch.setattr(subprocess, "Popen", no_process)


@pytest.fixture
def active(tmp_path, monkeypatch):
    from g6.core.ledger import digest
    from g6.self_maintenance.envelope import file_digest

    root = tmp_path / "loop"
    root.mkdir()
    domain = tmp_path / "domain.py"
    domain.write_text("# Never executed by this review.\n", encoding="utf-8")
    settings = {"domain_file": str(domain), "read_account": False, "windows": [
        {"provider": "claude", "allowance": 1_000_000, "week_start": "2026-10-26T08:00:00+00:00"}]}
    (root / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    log = CycleLog(root / CYCLE_LOG)
    log.enrol("Ada", {"domain_file": file_digest(domain)}, domain="review")
    log.start("Ada", settings=digest(settings), **stop_seen(root).binding())
    spawned = []
    monkeypatch.setattr(improve, "_spawn", lambda *args: spawned.append(args) or object())
    monkeypatch.setattr(improve, "_watch", lambda *args: {"completion_state": "qualified-draft"})
    return SimpleNamespace(root=root, settings=settings, log=log, spawned=spawned)


# Finding 1 --------------------------------------------------------------------------------------------------------------

def test_dialog_terms_do_not_authorize_an_unseen_cutoff(active):
    _, _, shown = improve.asked(str(active.root))
    # A tick ends while the person's already-open start dialog is on screen.
    active.log.stop("cut off while the start dialog was open", cut_off={"charged": 3})
    assert stop_in_force(active.root).code == STOPPED
    before = (active.root / CYCLE_LOG).read_bytes()

    reply = improve.start(active.root, "Ada", shown)

    assert not active.spawned, "start spawned after lifting a cut-off row the dialog never saw"
    assert reply["warning_card"]["code"] == improve.TERMS_CHANGED
    assert (active.root / CYCLE_LOG).read_bytes() == before
    assert not CycleLog(active.root / CYCLE_LOG).start_in_force()


# Finding 2 --------------------------------------------------------------------------------------------------------------

def test_null_lifted_length_is_refused_or_read_as_unreadable(tmp_path):
    (tmp_path / STOP_FILE).write_bytes(b"owner said stop\n")
    binding = stop_seen(tmp_path).binding()
    binding["lifted_bytes"] = None
    log = CycleLog(tmp_path / CYCLE_LOG)
    rejected = False
    try:
        log.start("Ada", **binding)
    except LedgerError:
        rejected = True
        # Also test the read boundary using the actual ledger producer, with
        # the domain validator omitted as for a historical malformed record.
        ChainLedger(tmp_path / CYCLE_LOG).append("started", "1", {"by": "Ada", **binding})
    try:
        observed = stop_in_force(tmp_path).code
    except Exception as exc:
        observed = type(exc).__name__
    assert rejected and observed == STOP_UNREADABLE, (
        f"null lifted_bytes: writer_rejected={rejected}, read_result={observed}")


# Finding 3 --------------------------------------------------------------------------------------------------------------

def test_records_parent_replaced_with_file_is_unreadable(active):
    path = active.root / CYCLE_LOG
    path.unlink()
    path.parent.rmdir()
    path.parent.write_bytes(b"records directory replaced by a file")

    observed = {"decision": stop_in_force(active.root).code,
                "gate": _gate(active.root, active.settings)[0],
                "status": improve.status(str(active.root))["stop_code"]}
    assert observed == dict.fromkeys(observed, STOP_UNREADABLE), observed


# Finding 4 --------------------------------------------------------------------------------------------------------------

def test_cached_start_is_bound_to_bytes_actually_parsed(active, monkeypatch):
    records = active.root / CYCLE_LOG
    unrevoked = records.read_bytes()
    active.log.stop("a durable cut-off row")
    revoked = records.read_bytes()
    handle = object.__new__(ShadowLoop)
    handle.root, handle.cannot_record, handle._on_record = active.root, "", None
    real_decision = loop_module.stop_in_force

    def replaced_during_full_read(root, *args, **kwargs):
        # The fingerprint just read the revoked chain. A replacement briefly
        # presents its valid prefix while the full reader obtains the start.
        records.write_bytes(unrevoked)
        try:
            return real_decision(root, *args, **kwargs)
        finally:
            records.write_bytes(revoked)

    with monkeypatch.context() as changed:
        changed.setattr(loop_module, "stop_in_force", replaced_during_full_read)
        handle._stop()

    assert stop_in_force(active.root).code == STOPPED
    # No concurrent change here: subsequent per-request checks see exactly the
    # revoked file, but must not reuse a start parsed from the other file.
    observed = [handle._stop().code for _ in range(3)]
    assert observed == [STOPPED] * 3, f"the guard kept an unrevoked start against revoked bytes: {observed}"


# Finding 5 --------------------------------------------------------------------------------------------------------------

def test_waiting_runner_preserves_unreadable_stop_code(active, monkeypatch):
    (active.root / STOPS_FILE).mkdir()
    assert stop_in_force(active.root).code == STOP_UNREADABLE
    now = datetime(2026, 10, 28, tzinfo=UTC)
    # An inert loop handle: step must terminate before any tick is attempted.
    loop = object.__new__(ShadowLoop)
    loop.root, loop.log = active.root, active.log
    loop.cannot_record, loop._on_record = "", None
    loop.domain, loop._now = SimpleNamespace(name="review"), lambda: now
    loop.meter = SimpleNamespace(windows={})
    runner_ = Runner(loop, now=lambda: now)
    monkeypatch.setattr(runner_, "_digest", lambda *args: None)
    monkeypatch.setattr(runner_, "_tick", lambda *args: (_ for _ in ()).throw(AssertionError("tick forbidden")))

    assert runner_.step() is None
    observed = heartbeat(active.root)["code"]
    assert observed == STOP_UNREADABLE, f"runner's terminal heartbeat lost the unreadable-stop code: {observed!r}"


# Finding 6 --------------------------------------------------------------------------------------------------------------

def test_failed_record_existence_check_is_unreadable(active, monkeypatch):
    records = active.root / CYCLE_LOG
    real_lstat = os.lstat

    def denied(path, *args, **kwargs):
        if os.fspath(path) == str(records):
            raise PermissionError("review: cannot inspect the existing records")
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", denied)
    observed = stop_in_force(active.root).code
    assert observed == STOP_UNREADABLE, f"failed lstat was treated as absent records: {observed!r}"


# Finding 7 --------------------------------------------------------------------------------------------------------------

def test_failed_record_file_type_check_does_not_escape(active, monkeypatch):
    records = active.root / CYCLE_LOG
    real_stat = Path.stat

    def denied(path, *args, **kwargs):
        if path == records:
            raise PermissionError("review: cannot stat existing records")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied)
    try:
        observed = stop_in_force(active.root).code
    except Exception as exc:
        observed = type(exc).__name__
    assert observed == STOP_UNREADABLE, f"record type check escaped as {observed}"


# Finding 8 --------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("consumer", ["dialog", "start"])
def test_terms_reread_cannot_escape_stop_error_handling(active, monkeypatch, consumer):
    _, _, shown = improve.asked(str(active.root))
    real_terms = improve._terms

    def damaged_before_terms(root, settings, seen):
        # The shared stop reading completed; the next read sees a real
        # truncated constraint-log file.
        (root / CYCLE_LOG).write_bytes(b'{"truncated":')
        return real_terms(root, settings, seen)

    monkeypatch.setattr(improve, "_terms", damaged_before_terms)
    try:
        reply = improve.asked(str(active.root)) if consumer == "dialog" else improve.start(active.root, "Ada", shown)
        observed = reply["warning_card"]["code"]
    except Exception as exc:
        observed = type(exc).__name__
    assert stop_in_force(active.root).code == STOP_UNREADABLE
    assert observed == STOP_UNREADABLE, f"{consumer}'s unguarded terms reread returned {observed}"
    assert not active.spawned


# Finding 9 --------------------------------------------------------------------------------------------------------------

def test_runner_preflight_publishes_the_shared_unreadable_stop(active, monkeypatch):
    (active.root / STOP_FILE).mkdir()
    assert stop_in_force(active.root).code == STOP_UNREADABLE

    def forbidden(*args, **kwargs):
        raise AssertionError("preflight must refuse before any domain or runner opens")

    monkeypatch.setattr(runner, "runner_lock", lambda *args: nullcontext())
    monkeypatch.setattr(runner, "open_loop", forbidden)
    monkeypatch.setattr(runner, "Runner", forbidden)
    runner.run(active.root)

    observed = (heartbeat(active.root) or {}).get("code")
    assert observed == STOP_UNREADABLE, f"preflight skipped the shared refusal and left heartbeat code {observed!r}"
