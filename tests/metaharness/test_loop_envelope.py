# SPDX-License-Identifier: Apache-2.0
"""The envelope around a tick (design 6.6, 6.10), enforced in code. Each section's test was seen failing first."""
from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from g6.core import stop
from g6.metaharness.loop import LoopSettings, ShadowLoop
from g6.metaharness.method import Method
from g6.self_maintenance.envelope import heartbeat
from g6.self_maintenance.pacing import ProviderWindow

_spec = importlib.util.spec_from_file_location("loop_domains", Path(__file__).with_name("_loop_domains.py"))
domains = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(domains, "WordProblems"):
    _spec.loader.exec_module(domains)

START = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)
NOW = START + timedelta(days=2)
FAST = Method(cycles=1, propose_budget=120, select_budget=400, race_block=12, reflect_calls=1)
SETTINGS = LoopSettings(windows=(ProviderWindow("claude", 1_000_000, START),), method=FAST, audit_per_tick=60)


def _loop(tmp_path, domain=None, proposer=None, settings=SETTINGS, now=lambda: NOW, **kw):
    domain = domain if domain is not None else domains.WordProblems(tmp_path)
    proposer = proposer if proposer is not None else domains.proposes({"prompt": domains.WORKING})
    return ShadowLoop(domain, tmp_path / "loop", settings, proposer=proposer, now=now, clock=domains.Clock(), **kw), domain


def _enrolled(tmp_path, **kw):
    loop, domain = _loop(tmp_path, **kw)
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    return loop, domain


def _code(reply: dict) -> str:
    return reply["warning_card"]["code"]


# STOP -----------------------------------------------------------------------------------------------------------------

def test_a_stop_file_halts_the_next_tick_and_removing_it_resumes(tmp_path):
    loop, domain = _enrolled(tmp_path)
    (loop.root / "STOP").write_text("maintenance window\n", encoding="utf-8")
    calls = len(domain.llm.calls)
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_STOPPED" and "maintenance window" in got["message"]
    assert len(domain.llm.calls) == calls and loop.log.cycles() == []
    (loop.root / "STOP").unlink()
    assert loop.tick()["outcome"] == "adopted"


def test_g6s_own_stop_halts_it_too(tmp_path):
    loop, domain = _enrolled(tmp_path)
    calls = len(domain.llm.calls)
    stop.engage("test")
    try:
        got = loop.tick()
    finally:
        stop.release()
    assert _code(got) == stop.STOPPED and len(domain.llm.calls) == calls and loop.log.cycles() == []


# The heartbeat -------------------------------------------------------------------------------------------------------

def test_every_tick_leaves_a_heartbeat_even_a_refused_one(tmp_path):
    loop, _ = _loop(tmp_path)
    assert heartbeat(loop.root) is None
    loop.tick()                                                    # refused: not enrolled
    assert heartbeat(loop.root)["code"] == "G6_E_LOOP_NOT_ENROLLED"
    loop.enrol("Ada")
    (loop.root / "STOP").write_text("", encoding="utf-8")
    loop.tick()
    assert heartbeat(loop.root)["code"] == "G6_E_LOOP_STOPPED"
    (loop.root / "STOP").unlink()
    got = loop.tick()
    beat = heartbeat(loop.root)
    assert beat["tick"] == 1 and beat["state"] == got["completion_state"] and beat["at"] == NOW.isoformat()


def test_a_cycle_under_way_says_so_in_the_heartbeat(tmp_path):
    seen = []
    loop, domain = _enrolled(tmp_path)
    real = domain.failures
    domain.failures = lambda view: seen.append(heartbeat(loop.root)["state"]) or real(view)
    loop.tick()
    assert seen == ["running"]


# A cycle that breaks --------------------------------------------------------------------------------------------------

def test_a_cycle_that_breaks_is_recorded_and_the_loop_lives_to_its_next_tick(tmp_path):
    loop, domain = _enrolled(tmp_path)

    def broken(view):
        raise RuntimeError("the domain fell over")

    real, domain.failures = domain.failures, broken
    got = loop.tick()
    assert got["outcome"] == "error" and "the domain fell over" in got["message"]
    assert got["completion_state"] == "blocked-escalated" and loop.log.cycles()[-1]["outcome"] == "error"
    domain.failures = real
    assert loop.tick()["outcome"] == "adopted"


# The budget ----------------------------------------------------------------------------------------------------------

def _windows(*allowances):
    return LoopSettings(windows=tuple(ProviderWindow(p, a, START) for p, a in allowances), method=FAST, audit_per_tick=60)


def test_a_tick_starts_only_if_its_worst_case_stays_under_the_ceiling(tmp_path):
    loop, domain = _enrolled(tmp_path, settings=_windows(("claude", 100)))
    calls = len(domain.llm.calls)
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_BUDGET_WAIT" and "ceiling" in got["message"]
    assert len(domain.llm.calls) == calls


def test_a_wait_for_the_budget_is_not_a_failed_cycle(tmp_path):
    loop, _ = _enrolled(tmp_path, settings=_windows(("claude", 100)))
    for _ in range(4):
        loop.tick()
    assert loop.log.cycles() == [] and loop.log.failed_streak() == 0 and loop.log.paused() == ""


def test_it_runs_again_when_the_week_allows(tmp_path):
    days = [START]
    worst = _loop(tmp_path / "probe")[0].worst_case()
    settings = _windows(("claude", worst * 3))                     # day one's ceiling is a fifth of this: too little
    loop, _ = _enrolled(tmp_path, settings=settings, now=lambda: days[0])
    assert _code(loop.tick()) == "G6_E_LOOP_BUDGET_WAIT"
    days[0] = START + timedelta(days=1, hours=1)
    assert loop.tick()["outcome"] == "adopted"


def test_a_tick_is_charged_what_the_host_counted_and_never_more_than_its_stated_worst_case(tmp_path):
    proposer = domains.proposes({"prompt": domains.WORKING})
    loop, domain = _enrolled(tmp_path, proposer=proposer)
    loop.tick()
    counted = len(domain.llm.calls) + len(proposer.calls)
    used = loop.meter.reading("claude", NOW).used
    assert used == counted > 0 and counted <= loop.worst_case()


def test_the_whole_accounts_use_makes_the_loop_give_way(tmp_path):
    from g6.self_maintenance.pacing import AccountReading

    reading = AccountReading(used=399_000, estimated=True)         # day three's ceiling is 600,000
    loop, _ = _enrolled(tmp_path, read_account=lambda provider, start: AccountReading(used=599_999))
    assert _code(loop.tick()) == "G6_E_LOOP_BUDGET_WAIT"
    other, _ = _enrolled(tmp_path / "b", read_account=lambda provider, start: reading)
    assert other.tick()["outcome"] == "adopted"


def test_at_a_reported_limit_the_loop_waits_for_the_reset_and_says_when(tmp_path):
    from g6.self_maintenance.pacing import AccountReading

    reset = NOW + timedelta(hours=3)
    loop, _ = _enrolled(tmp_path, read_account=lambda provider, start: AccountReading(limited_until=reset))
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_BUDGET_WAIT" and got["wait_until"] == reset.isoformat()


def test_a_second_provider_takes_over_when_the_first_is_at_its_ceiling(tmp_path):
    loop, _ = _enrolled(tmp_path, settings=_windows(("claude", 100), ("codex", 1_000_000)))
    got = loop.tick()
    assert got["outcome"] == "adopted" and got["budget"]["provider"] == "codex"


def test_every_cycle_records_its_use_against_the_pacing_rule(tmp_path):
    loop, _ = _enrolled(tmp_path)
    loop.tick()
    budget = loop.log.cycles()[-1]["budget"]
    assert budget["provider"] == "claude" and budget["day"] == 3
    assert budget["target"] == pytest.approx(1_000_000 * 3 / 7) and budget["ceiling"] == pytest.approx(600_000)
    assert 0 < budget["charged"] == budget["used"] and budget["estimated"] is False


def test_a_cycle_that_breaks_is_charged_what_it_used(tmp_path):
    loop, domain = _enrolled(tmp_path)

    def broken(view):
        raise RuntimeError("no")

    domain.failures = broken
    assert loop.tick()["outcome"] == "error"
    assert 0 < loop.meter.reading("claude", NOW).used == len(domain.llm.calls)