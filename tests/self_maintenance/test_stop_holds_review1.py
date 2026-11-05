# SPDX-License-Identifier: Apache-2.0
"""Review 1 of claude/stop-holds: its eight findings as the reviewer reproduced them, with the reviewer's fixtures (the
review's own temporary folder aside: pytest's serves here). FakeLLM is the only model anywhere here, and nothing starts a
process."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core.ledger import LedgerError, digest
from g6.mcp import improve
from g6.metaharness.loop import ShadowLoop
from g6.metaharness.runner import Runner, _gate
from g6.self_maintenance import envelope
from g6.self_maintenance.envelope import (
    CYCLE_LOG,
    STOP_UNREADABLE,
    STOPS_FILE,
    CycleLog,
    Stop,
    ask_to_stop,
    file_digest,
    stop_in_force,
    stop_seen,
)

START = datetime(2026, 10, 26, 8, tzinfo=UTC)
NOW = START + timedelta(days=2)


@pytest.fixture(autouse=True)
def review_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("G6_HOME", str(home))


@pytest.fixture
def rig(tmp_path, monkeypatch):
    root = tmp_path / "loop"
    root.mkdir()
    domain = tmp_path / "domain.py"
    domain.write_text("# Review: never executed.\n", encoding="utf-8")
    settings = {"domain_file": str(domain), "read_account": False, "windows": [
        {"provider": "claude", "allowance": 1_000_000, "week_start": START.isoformat()}]}
    (root / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    log = CycleLog(root / CYCLE_LOG)
    log.enrol("Ada", {"domain_file": file_digest(domain)}, domain="review")
    spawned = []
    monkeypatch.setattr(improve, "_spawn", lambda *args: spawned.append(args) or object())
    monkeypatch.setattr(improve, "_watch", lambda *args: {"completion_state": "qualified-draft"})
    return SimpleNamespace(root=root, domain=domain, settings=settings, log=log, spawned=spawned)


@pytest.fixture
def active(rig):
    rig.log.start("Ada", settings=digest(rig.settings), **stop_seen(rig.root).binding())
    return rig


@pytest.fixture
def live_loop(tmp_path, monkeypatch):
    from g6.metaharness.loop import LoopSettings
    from g6.metaharness.method import Method
    from g6.self_maintenance.pacing import ProviderWindow

    path = Path(__file__).parents[1] / "metaharness" / "_loop_domains.py"
    spec = importlib.util.spec_from_file_location("review_loop_domains", path)
    domains = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = domains
    spec.loader.exec_module(domains)
    domain = domains.WordProblems(tmp_path)
    settings = LoopSettings(windows=(ProviderWindow("claude", 1_000_000, START),),
                            method=Method(cycles=1, propose_budget=120, select_budget=400,
                                          race_block=12, reflect_calls=1), audit_per_tick=60)
    loop = ShadowLoop(domain, tmp_path / "loop", settings,
                      proposer=domains.proposes({"prompt": domains.WORKING}),
                      now=lambda: NOW, clock=domains.Clock())
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    loop.log.start("Ada", **stop_seen(loop.root).binding())
    return SimpleNamespace(loop=loop, domain=domain, now=NOW)


# Finding 1 --------------------------------------------------------------------------------------------------------------

def test_records_replaced_by_a_directory_are_unreadable_not_absent(active):
    records = active.root / CYCLE_LOG
    records.unlink()
    records.mkdir()
    observed = {"decision": stop_in_force(active.root).code,
                "status": improve.status(str(active.root))["stop_code"]}
    assert observed == {"decision": STOP_UNREADABLE, "status": STOP_UNREADABLE}, observed


# Finding 2 --------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("damage", ["truncated", "permission error"])
def test_corrupt_lift_records_get_the_shared_unreadable_code(active, monkeypatch, damage):
    records = active.root / CYCLE_LOG
    if damage == "truncated":
        records.write_bytes(b'{"truncated":')
    else:
        read = Path.read_bytes

        def denied(path):
            if path == records:
                raise PermissionError("review: records cannot be read")
            return read(path)

        monkeypatch.setattr(Path, "read_bytes", denied)

    def code(call):
        try:
            return call()
        except Exception as exc:
            return type(exc).__name__

    handle = SimpleNamespace(root=active.root, log=active.log, mh=None, _beat=lambda *a, **k: None)
    observed = {"decision": stop_in_force(active.root).code,
                "gate": code(lambda: _gate(active.root, active.settings)[0]),
                "dialog": code(lambda: improve.asked(str(active.root))["warning_card"]["code"]),
                "start": code(lambda: improve.start(active.root, "Ada")["warning_card"]["code"]),
                "tick": code(lambda: ShadowLoop.tick(handle)["warning_card"]["code"]),
                "status": code(lambda: improve.status(str(active.root))["stop_code"])}
    assert observed == dict.fromkeys(observed, STOP_UNREADABLE), observed


# Finding 3 --------------------------------------------------------------------------------------------------------------

def test_first_start_must_show_a_truncated_journal_stop(rig):
    (rig.root / STOPS_FILE).write_bytes(b"operator requested stop")
    offered = improve.asked(str(rig.root))
    if isinstance(offered, dict):
        assert offered["completion_state"] == "blocked-escalated"
        return
    _, question, shown = offered
    improve.start(rig.root, "Ada", shown)
    assert not rig.spawned or "Starting goes past it" in question or "Starting lifts the stop" in question, (
        "start lifted a nonempty, truncated stops.log and spawned without showing any stop in the dialog")


# Finding 4 --------------------------------------------------------------------------------------------------------------

def test_start_records_nothing_if_journal_breaks_while_waiting_for_tick_lock(rig, monkeypatch):
    _, _, shown = improve.asked(str(rig.root))
    real = improve.Coordination

    def changed_before_lock(path):
        # The start has read its snapshot but has not acquired .tick or written a row.
        (rig.root / STOPS_FILE).mkdir()
        return real(path)

    monkeypatch.setattr(improve, "Coordination", changed_before_lock)
    reply = improve.start(rig.root, "Ada", shown)
    assert not rig.spawned
    assert reply["completion_state"] == "blocked-escalated"
    assert CycleLog(rig.root / CYCLE_LOG).started() is None, (
        "start wrote a started row after stops.log became unreadable, then detected it only in its post-check")


# Finding 5 --------------------------------------------------------------------------------------------------------------

def test_tick_stops_at_next_request_when_lift_records_become_unreadable(live_loop):
    loop, domain = live_loop.loop, live_loop.domain
    sent = []

    def corrupted_after_first(system, user):
        sent.append(user)
        if len(sent) == 1:
            (loop.root / CYCLE_LOG).write_bytes(b'{"truncated":')
            assert stop_in_force(loop.root).code == STOP_UNREADABLE
        return domain.tutor(system, user)

    domain.llm._responder = corrupted_after_first
    try:
        loop.tick()
    except Exception:
        # A later attempt to append a cycle may detect corruption; that is too late.
        pass
    assert len(sent) == 1, f"tick sent {len(sent)} model requests despite unreadable lift records after request one"


# Finding 6 --------------------------------------------------------------------------------------------------------------

def test_runner_does_not_resume_a_revoked_start_when_stop_files_become_readable(live_loop, monkeypatch):
    loop, domain = live_loop.loop, live_loop.domain
    sent = []

    def unreadable_after_first(system, user):
        sent.append(user)
        if len(sent) == 1:
            (loop.root / STOPS_FILE).mkdir()
        return domain.tutor(system, user)

    domain.llm._responder = unreadable_after_first
    runner = Runner(loop, now=lambda: live_loop.now)
    runner._tick(live_loop.now)
    reply = runner.last
    assert reply["warning_card"]["code"] == STOP_UNREADABLE
    assert len(sent) == 1 and not loop.log.start_in_force()
    (loop.root / STOPS_FILE).rmdir()  # Readability restored; no person starts it again.
    attempted = []
    monkeypatch.setattr(runner, "_tick", lambda now: attempted.append(now))
    runner.step()
    assert not attempted, "runner attempted another tick after the prior tick durably revoked its start"


# Finding 7 --------------------------------------------------------------------------------------------------------------

def test_malformed_lift_fields_are_an_unreadable_stop_not_an_exception(rig):
    ask_to_stop(rig.root, "owner stop")
    fields = stop_seen(rig.root).binding()
    fields["lifted_bytes"] = "not a byte count"
    try:
        rig.log.start("Ada", **fields)  # Exercise the actual producer and validator.
    except LedgerError:
        # Rejecting malformed lift records at the producer is also a valid fix.
        return
    try:
        observed = stop_in_force(rig.root)
    except Exception as exc:
        observed = type(exc).__name__
    assert isinstance(observed, Stop) and observed.code == STOP_UNREADABLE, (
        f"malformed lifted_bytes produced {observed!r} instead of {STOP_UNREADABLE}")


# Finding 8 --------------------------------------------------------------------------------------------------------------

def test_start_cannot_lift_a_journal_write_before_its_fsync(rig, monkeypatch):
    real_fsync = os.fsync
    observed = []

    class InterruptedStop(BaseException):
        pass

    def before_sync(handle):
        # The journal has been flushed to the OS, but not yet made durable.
        offered = improve.asked(str(rig.root))
        if not isinstance(offered, dict):
            _, _, shown = offered
            observed.append(improve.start(rig.root, "Ada", shown))
        raise InterruptedStop()

    # Only interrupt the journal's fsync. Starts have their own ledger fsync.
    journal_sync = True

    def interrupt_journal(handle):
        nonlocal journal_sync
        if journal_sync:
            journal_sync = False
            return before_sync(handle)
        return real_fsync(handle)

    monkeypatch.setattr(os, "fsync", interrupt_journal)
    try:
        envelope.ask_to_stop(rig.root, "stop not durable yet")
    except InterruptedStop:
        pass
    assert not rig.spawned, "start bound and lifted stops.log and spawned before the stop writer's fsync"
