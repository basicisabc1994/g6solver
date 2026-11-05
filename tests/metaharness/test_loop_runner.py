# SPDX-License-Identifier: Apache-2.0
"""The runner: a loop that runs on its own, on an injected clock and sleep, so a week passes in a test. FakeLLM only."""
from __future__ import annotations

import ctypes
import dataclasses
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import time as clock
from ctypes import wintypes
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import g6
from g6.core.ledger import LedgerError, digest
from g6.core.llm import LLMError
from g6.metaharness import Model
from g6.metaharness.loop import LoopSettings, ShadowLoop
from g6.metaharness import runner as runner_module
from g6.metaharness.method import Method
from g6.metaharness.runner import FIRST_REST, LONGEST_REST, NAP, Runner, _in_job, main, open_loop, run
from g6.metaharness.shadow import lineage
from g6.self_maintenance.digest import digest_path, latest
from g6.self_maintenance.envelope import (
    CYCLE_LOG,
    CycleLog,
    ask_to_stop,
    beat,
    heartbeat,
    runner_alive,
    runner_lock,
    stop_reason,
    stop_to_lift,
    stops_asked,
)
from g6.self_maintenance.pacing import AccountReading, ProviderWindow, read_settings
from g6.self_maintenance.usage import REFRESH, AccountReader

_spec = importlib.util.spec_from_file_location("loop_domains", Path(__file__).with_name("_loop_domains.py"))
domains = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(domains, "WordProblems"):
    _spec.loader.exec_module(domains)

START = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)
FAST = Method(cycles=1, propose_budget=120, select_budget=400, race_block=12, reflect_calls=1)
SETTINGS = LoopSettings(windows=(ProviderWindow("claude", 1_000_000, START),), method=FAST, audit_per_tick=60)


class Time:
    """The clock and the sleep of a run. ``stop_at`` writes the STOP file when the clock passes it."""

    def __init__(self, root: Path, at: datetime = START + timedelta(days=2)) -> None:
        self.root, self.at, self.naps, self.stop_at = root, at, [], None

    def now(self) -> datetime:
        return self.at

    def sleep(self, seconds: float) -> None:
        self.naps.append(seconds)
        self.at += timedelta(seconds=seconds)
        if self.stop_at is not None and self.at >= self.stop_at:
            (self.root / "STOP").write_text("the test is over\n", encoding="utf-8")


def _runner(tmp_path, *, domain=None, proposer=None, settings=SETTINGS, read_account=None, at=None, nap=NAP, reader=None):
    domain = domain if domain is not None else domains.WordProblems(tmp_path)
    proposer = proposer if proposer is not None else domains.proposes({"prompt": domains.WORKING})
    time = Time(tmp_path / "loop", **({"at": at} if at else {}))
    loop = ShadowLoop(domain, tmp_path / "loop", settings, proposer=proposer, now=time.now, clock=domains.Clock(),
                      read_account=read_account)
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    return Runner(loop, now=time.now, sleep=time.sleep, nap=nap, reader=reader), time, domain


def _windows(*allowances):
    return LoopSettings(windows=tuple(ProviderWindow(p, a, START) for p, a in allowances), method=FAST, audit_per_tick=60)


def _stopped_after(time: Time, delta: timedelta) -> None:
    time.stop_at = time.at + delta


# The pass -----------------------------------------------------------------------------------------------------------

def test_it_ticks_until_a_stop_file_and_then_ends_cleanly(tmp_path):
    runner, time, _ = _runner(tmp_path)
    (runner.loop.root / "STOP").write_text("not yet\n", encoding="utf-8")
    assert runner.run() == 0 and runner.loop.log.cycles() == []         # the stop is checked before the first tick
    (runner.loop.root / "STOP").unlink()
    _stopped_after(time, timedelta(seconds=1))
    assert runner.run() == 0
    assert [c["outcome"] for c in runner.loop.log.cycles()][0] == "adopted"
    beat = heartbeat(runner.loop.root)
    assert beat["state"] == "stopped" and "the test is over" in beat["reason"]


def test_a_stop_during_a_tick_ends_the_run_at_once_and_the_days_page_says_what_it_cut_off(tmp_path):
    runner, time, domain = _runner(tmp_path)
    tutor, seen = domain.tutor, []

    def stopped_at_the_third(system: str, user: str) -> str:
        seen.append(user)
        if len(seen) == 3:
            ask_to_stop(runner.loop.root, "the owner wants the subscription back")
        return tutor(system, user)

    domain.llm._responder = stopped_at_the_third
    assert runner.run() == 0 and len(seen) == 3 and time.naps == [1.0]
    assert heartbeat(runner.loop.root)["state"] == "stopped"
    page = latest(runner.loop.root).read_text(encoding="utf-8")
    assert "The runner ended" in page and "at a model call after 3 requests" in page


def _a_stop_dies_before_its_file(root: Path) -> None:
    with open(root / "stops.log", "a", encoding="utf-8") as stops:
        stops.write("the owner said no\n")


def test_a_stop_that_died_before_its_file_ends_a_tick_in_flight_and_then_the_run(tmp_path):
    runner, time, domain = _runner(tmp_path)
    runner.loop.log.start("Ada", stops=0)
    tutor, seen = domain.tutor, []

    def stopped_at_the_third(system: str, user: str) -> str:
        seen.append(user)
        if len(seen) == 3:
            _a_stop_dies_before_its_file(runner.loop.root)
        return tutor(system, user)

    domain.llm._responder = stopped_at_the_third
    _stopped_after(time, timedelta(minutes=30))                          # so a run that misses it ends, and this fails
    assert runner.run() == 0 and len(seen) == 3 and not (runner.loop.root / "STOP").exists()
    assert heartbeat(runner.loop.root)["state"] == "stopped" and time.naps == [1.0]
    assert not runner.loop.log.start_in_force()


def test_a_waiting_runner_ends_at_a_stop_that_died_before_its_file_and_does_not_spin(tmp_path):
    runner, time, _ = _runner(tmp_path, settings=_windows(("claude", 100)))
    runner.loop.log.start("Ada", stops=0)
    runner.step()
    _a_stop_dies_before_its_file(runner.loop.root)
    assert runner.step() is None and heartbeat(runner.loop.root)["state"] == "stopped"


def test_a_tick_refused_for_a_stop_does_not_leave_the_runner_ticking_once_a_second(tmp_path):
    """The tick and the runner must read the same stop, or the runner goes on, and asks again a second later, for ever."""
    runner, time, domain = _runner(tmp_path)
    runner.loop.log.start("Ada", stops=0)
    _a_stop_dies_before_its_file(runner.loop.root)
    _stopped_after(time, timedelta(minutes=5))
    assert runner.run() == 0 and domain.llm.calls == [] and len(time.naps) <= 1


def test_the_days_page_says_a_classifier_stopped_a_request_and_that_it_will_not_go_again(tmp_path):
    runner, _, domain = _runner(tmp_path)

    def refuses(system: str, user: str) -> str:
        raise LLMError("G6_E_MODEL_REFUSED: cyber")

    domain.llm._responder = refuses
    runner.step()
    said = latest(runner.loop.root).read_text(encoding="utf-8").split("## Stops and pauses")[1]
    assert "A classifier or a permission stopped a request at tick 1" in said and "not sent again" in said


def test_no_sleep_is_longer_than_a_minute_so_a_stop_is_seen_within_one(tmp_path):
    runner, time, _ = _runner(tmp_path, settings=_windows(("claude", 100)))     # never room: it only waits
    _stopped_after(time, timedelta(minutes=10))
    assert runner.run() == 0
    assert max(time.naps) <= 60 and time.at - time.stop_at < timedelta(minutes=1)
    assert NAP == 60


def test_a_refused_tick_spends_nothing_and_the_next_comes_when_a_ceiling_can_rise(tmp_path):
    runner, time, domain = _runner(tmp_path, settings=_windows(("claude", 100)), nap=3600)
    calls = len(domain.llm.calls)
    runner.step()
    assert runner.not_before == START + timedelta(days=3)               # time is two days in: day three ends here
    assert len(domain.llm.calls) == calls and runner.last["warning_card"]["code"] == "G6_E_LOOP_BUDGET_WAIT"


def test_it_uses_each_day_up_to_the_ceiling_and_goes_on_the_next_day(tmp_path):
    worst = _runner(tmp_path / "probe")[0].loop.worst_case()
    day_one = START + timedelta(hours=1)
    runner, time, _ = _runner(tmp_path, settings=_windows(("claude", worst * 3)), at=day_one, nap=3600)
    _stopped_after(time, timedelta(days=1, hours=2))
    assert runner.run() == 0
    assert runner.loop.log.cycles()                                      # day one's ceiling is too little; day two's is not
    assert all(datetime.fromisoformat(c["at"]) >= START + timedelta(days=1) for c in runner.loop.log.cycles())


def test_at_a_reported_limit_it_waits_for_the_reset_or_the_next_rise_of_a_ceiling(tmp_path):
    reset = START + timedelta(days=2, hours=3)
    runner, _, _ = _runner(tmp_path, read_account=lambda provider, start: AccountReading(limited_until=reset))
    runner.step()
    assert runner.not_before == reset


def test_a_limit_met_during_a_tick_is_waited_out_and_the_days_page_says_so(tmp_path):
    runner, time, domain = _runner(tmp_path, proposer=domains.proposes({"prompt": domains.WORKING}))
    answers = domain.llm._responder

    def refuses(system: str, user: str) -> str:
        raise LLMError("G6_E_LLM_RATE_LIMITED: HTTP 429 from u: You've hit your usage limit")

    domain.llm._responder = refuses
    first = time.at
    runner.step()
    assert runner.not_before == first + REFRESH and runner.last["warning_card"]["code"] == "G6_E_LOOP_BUDGET_WAIT"
    page = digest_path(runner.loop.root, first.date()).read_text(encoding="utf-8")
    assert "Waiting for the budget" in page and "hit your usage limit" in page
    assert heartbeat(runner.loop.root)["pause"] is None and runner.loop.log.cycles() == []
    domain.llm._responder = answers
    _stopped_after(time, REFRESH + timedelta(minutes=2))
    assert runner.run() == 0
    assert runner.loop.log.cycles()[0]["outcome"] == "adopted"
    assert datetime.fromisoformat(runner.loop.log.cycles()[0]["at"]) >= first + REFRESH


def test_a_full_week_is_waited_out_to_its_reset_and_no_later(tmp_path):
    """The old week's figure must not count against the new week's first ceiling: that slipped the next try a day."""
    reset = START + timedelta(days=7)
    window = ProviderWindow("claude", 5000, START)
    domain = domains.WordProblems(tmp_path, pool=86)
    run = domain.execution()
    domain.execution = lambda: dataclasses.replace(run, models={"local": Model(domain.llm, max_calls_per_run=1)})
    full = {"rateLimits": {"primary": {"usedPercent": 100, "windowDurationMins": 10080, "resetsAt": reset.timestamp()}}}
    empty = {"rateLimits": {"primary": {"usedPercent": 0, "windowDurationMins": 10080,
                                        "resetsAt": (reset + timedelta(days=7)).timestamp()}}}
    answers = iter((full, empty))
    reader = AccountReader((window,), ask={"claude": lambda: next(answers)})
    runner, time, _ = _runner(tmp_path, domain=domain, settings=dataclasses.replace(SETTINGS, windows=(window,)),
                              read_account=reader, reader=reader, at=reset - timedelta(seconds=1))
    runner.step()
    assert runner.last["warning_card"]["code"] == "G6_E_LOOP_BUDGET_WAIT" and runner.not_before == reset
    calls = len(domain.llm.calls)
    time.at = reset + timedelta(seconds=1)
    runner.step()
    assert runner.not_before <= time.at + REFRESH and len(domain.llm.calls) > calls


def test_a_tick_that_raises_does_not_end_the_run_and_counts_as_a_failed_cycle(tmp_path, monkeypatch):
    runner, time, _ = _runner(tmp_path)
    real, raised = runner.loop.tick, []

    def once(*args):
        if not raised:
            raised.append(1)
            raise OSError("the disk blinked")
        return real()

    monkeypatch.setattr(runner.loop, "tick", once)
    _stopped_after(time, timedelta(minutes=30))
    assert runner.run() == 0
    outcomes = [c["outcome"] for c in runner.loop.log.cycles()]
    assert outcomes[0] == "error" and "adopted" in outcomes
    assert "the disk blinked" in runner.loop.log.cycles()[0]["reason"]


def test_bookkeeping_that_breaks_around_a_tick_does_not_end_the_run(tmp_path, monkeypatch):
    runner, time, _ = _runner(tmp_path)
    real, raised = runner._use, []

    def once(now):
        if not raised:
            raised.append(1)
            raise sqlite3.OperationalError("database is locked")
        return real(now)

    monkeypatch.setattr(runner, "_use", once)
    _stopped_after(time, timedelta(minutes=5))
    assert runner.run() == 0 and raised
    assert heartbeat(runner.loop.root)["state"] == "stopped"


def test_a_stop_ends_the_run_even_when_nothing_can_be_written_about_it(tmp_path, monkeypatch):
    runner, _, _ = _runner(tmp_path)

    def always(provider, now):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(runner.loop.meter, "reading", always)
    (runner.loop.root / "STOP").write_text("now\n", encoding="utf-8")
    assert runner.run() == 0
    assert heartbeat(runner.loop.root)["state"] == "stopped"


def test_a_heartbeat_during_a_tick_keeps_the_last_use_so_status_is_not_blank(tmp_path):
    runner, _, domain = _runner(tmp_path)
    runner.step()
    seen = []
    real = domain.failures
    domain.failures = lambda view: seen.append(heartbeat(runner.loop.root)) or real(view)
    runner.not_before = None
    runner.loop.tick()
    assert seen and seen[0]["state"] == "running" and seen[0]["use"][0]["provider"] == "claude"


def test_three_ticks_that_raise_pause_the_loop_and_the_runner_lives_on(tmp_path, monkeypatch):
    runner, time, domain = _runner(tmp_path)

    def always(*args):
        raise RuntimeError("no")

    monkeypatch.setattr(runner.loop, "tick", always)
    _stopped_after(time, timedelta(hours=1))
    assert runner.run() == 0
    assert "three failed cycles" in runner.loop.log.paused() and len(runner.loop.log.cycles()) == 3


def test_a_paused_loop_spends_nothing_and_the_runner_waits_for_a_person(tmp_path):
    runner, time, domain = _runner(tmp_path)

    def broken(view):
        raise RuntimeError("the domain fell over")

    domain.failures = broken
    _stopped_after(time, timedelta(hours=3))
    assert runner.run() == 0
    assert runner.loop.log.paused() and len(runner.loop.log.cycles()) == 3
    assert "paused" in heartbeat(runner.loop.root)["pause"]["reason"]
    text = latest(runner.loop.root).read_text(encoding="utf-8")
    assert "The loop is paused" in text and "resume" in text


def test_a_person_lifting_the_pause_elsewhere_sets_the_same_runner_going(tmp_path):
    runner, time, domain = _runner(tmp_path)

    def broken(view):
        raise RuntimeError("the domain fell over")

    real, domain.failures = domain.failures, broken
    for _ in range(40):
        time.sleep(runner.step())
    assert runner.loop.log.paused()
    domain.failures = real
    person = ShadowLoop(domain, runner.loop.root, SETTINGS, proposer=domains.proposes({"prompt": domains.WORKING}),
                        now=time.now, clock=domains.Clock())
    assert person.resume("Ada")["completion_state"] == "qualified-draft"
    _stopped_after(time, timedelta(hours=1))
    runner.run()
    assert "adopted" in [c["outcome"] for c in runner.loop.log.cycles()]


def test_a_changed_frozen_item_stops_every_tick_and_goes_to_the_owner(tmp_path):
    runner, time, domain = _runner(tmp_path)
    domain.pool.append(domains.SuiteCase("late", "What is the answer?", "1"))
    calls = len(domain.llm.calls)
    _stopped_after(time, timedelta(hours=1))
    assert runner.run() == 0
    assert len(domain.llm.calls) == calls and runner.loop.log.cycles() == []
    assert heartbeat(runner.loop.root)["pause"]["code"] == "G6_E_LOOP_FROZEN_CHANGED"
    assert "G6_E_LOOP_FROZEN_CHANGED" not in latest(runner.loop.root).read_text(encoding="utf-8")   # plain words
    assert "changed since a person enrolled" in latest(runner.loop.root).read_text(encoding="utf-8")


# Nothing to try -----------------------------------------------------------------------------------------------------

def test_a_loop_with_nothing_to_try_backs_off_and_does_not_burn_the_week(tmp_path):
    domain = domains.WordProblems(tmp_path, weak=1.1)                    # nothing fails: every tick is idle
    runner, time, _ = _runner(tmp_path, domain=domain, nap=3600)
    _stopped_after(time, timedelta(days=4))
    assert runner.run() == 0
    cycles = runner.loop.log.cycles()
    assert {c["outcome"] for c in cycles} == {"idle"}
    gaps = [datetime.fromisoformat(b["at"]) - datetime.fromisoformat(a["at"]) for a, b in zip(cycles, cycles[1:])]
    assert gaps[:3] == [FIRST_REST, FIRST_REST * 2, FIRST_REST * 4]
    assert max(gaps) == LONGEST_REST and len(cycles) < 14                # at most a tick a day once it has settled
    assert "Backing off" in latest(runner.loop.root).read_text(encoding="utf-8")


def test_a_rest_survives_a_restart(tmp_path):
    domain = domains.WordProblems(tmp_path, weak=1.1)
    runner, time, _ = _runner(tmp_path, domain=domain, nap=3600)
    runner.step()
    assert len(runner.loop.log.cycles()) == 1
    again = Runner(runner.loop, now=time.now, sleep=time.sleep, nap=3600)
    time.sleep(60)
    again.step()
    assert len(runner.loop.log.cycles()) == 1                            # the count lives in the records, not the runner


def test_a_person_enrolling_again_ends_the_rest(tmp_path):
    domain = domains.WordProblems(tmp_path, weak=1.1)
    runner, time, _ = _runner(tmp_path, domain=domain, nap=3600)
    runner.step()
    assert runner.loop.log.quiet_streak() == 1
    assert runner.loop.enrol("Ada")["completion_state"] == "qualified-draft"
    time.sleep(60)
    runner.step()
    assert len(runner.loop.log.cycles()) == 2


# The heartbeat and the digest ---------------------------------------------------------------------------------------

def test_every_pass_beats_with_its_use_and_when_the_next_tick_is_due(tmp_path):
    runner, time, _ = _runner(tmp_path, settings=_windows(("claude", 100)))
    runner.step()
    beat = heartbeat(runner.loop.root)
    assert beat["state"] == "waiting" and beat["pid"] == os.getpid() and beat["code"] == "G6_E_LOOP_BUDGET_WAIT"
    assert beat["next_tick"] == (START + timedelta(days=3)).isoformat()
    [use] = beat["use"]
    assert (use["provider"], use["day"], use["estimated"]) == ("claude", 3, False) and use["ceiling"] == pytest.approx(60)


def test_a_digest_is_written_after_a_tick_and_one_a_day(tmp_path):
    runner, time, _ = _runner(tmp_path, nap=3600)
    start = time.at
    _stopped_after(time, timedelta(days=2))
    assert runner.run() == 0
    days = {p.name for p in (runner.loop.root / "digests").iterdir()}
    assert days == {f"{(start + timedelta(days=d)).date()}.md" for d in range(3)}
    first = digest_path(runner.loop.root, start.date()).read_text(encoding="utf-8")
    assert first.count("\n") < 60 and "1 adopted" in first and "Now at generation 1." in first
    assert "wrong_answer" in first and "held-out cases" in first


def test_each_page_holds_only_its_own_days_cycles(tmp_path):
    runner, time, _ = _runner(tmp_path, nap=3600)
    first = time.at.date()
    runner.step()
    time.sleep(24 * 3600)
    runner.step()
    assert "1 tick on" in digest_path(runner.loop.root, first).read_text(encoding="utf-8")
    second = digest_path(runner.loop.root, time.at.date()).read_text(encoding="utf-8")
    assert "1 tick on" in second and "adopted" not in second.split("## What ran")[1].split("##")[0]


def _a_day_with_no_tick_then_a_tick_past_midnight(tmp_path):
    midnight = datetime(2026, 10, 26, 0, 0, tzinfo=UTC)
    settings = LoopSettings(windows=(ProviderWindow("claude", 5000, midnight),), method=FAST)
    runner, time, _ = _runner(tmp_path, settings=settings, at=midnight + timedelta(hours=23, minutes=59))
    runner.step()
    assert not runner.loop.log.cycles()                                  # day one's ceiling cannot fund one tick
    yesterday = digest_path(runner.loop.root, midnight.date())
    assert "Now at generation 0." in yesterday.read_text(encoding="utf-8")
    time.at += timedelta(minutes=2)
    runner.step()
    assert runner.loop.log.cycles()[0]["outcome"] == "adopted"
    return yesterday.read_text(encoding="utf-8"), digest_path(runner.loop.root, time.at.date()).read_text(encoding="utf-8")


def test_a_day_that_has_ended_is_not_told_of_the_next_days_generation(tmp_path):
    yesterday, today = _a_day_with_no_tick_then_a_tick_past_midnight(tmp_path)
    assert "No tick ran today" in yesterday
    assert "Now at generation 0." in yesterday and "Generation 1" not in yesterday
    assert "Now at generation 1." in today


def test_a_day_that_has_ended_is_not_told_of_the_next_days_constraint(tmp_path):
    yesterday, today = _a_day_with_no_tick_then_a_tick_past_midnight(tmp_path)
    assert "None named yet." in yesterday and "wrong_answer" not in yesterday
    assert "wrong_answer" in today


def test_a_tick_that_began_before_midnight_stays_on_the_day_it_began(tmp_path):
    runner, time, _ = _runner(tmp_path, nap=3600, at=datetime(2026, 10, 28, 23, 59, tzinfo=UTC))
    real = runner.loop.tick

    def across_midnight():
        got = real()
        time.at += timedelta(minutes=2)
        return got

    runner.loop.tick = across_midnight
    runner.step()
    assert "1 tick on" in digest_path(runner.loop.root, datetime(2026, 10, 28).date()).read_text(encoding="utf-8")
    assert "No tick ran today" in digest_path(runner.loop.root, time.at.date()).read_text(encoding="utf-8")


def test_a_digest_that_cannot_be_written_does_not_end_the_run(tmp_path, monkeypatch):
    runner, time, _ = _runner(tmp_path)
    monkeypatch.setattr("g6.metaharness.runner.digest.write", lambda *a: (_ for _ in ()).throw(OSError("full")))
    _stopped_after(time, timedelta(seconds=1))
    assert runner.run() == 0 and runner.loop.log.cycles()


def test_the_owner_told_to_change_an_estimate_is_told_what_changing_the_file_costs(tmp_path):
    """Whole, past the digest's cut of a line. A runner alive keeps what it read, so nothing halts now: the next restart does."""
    estimated = LoopSettings(windows=(ProviderWindow("claude", 1_000_000, START, estimated=True),), method=FAST, audit_per_tick=60)
    runner, _, _ = _runner(tmp_path, settings=estimated)
    runner.step()
    owed = latest(runner.loop.root).read_text(encoding="utf-8").split("## Decisions for the owner")[1].split("##")[0]
    assert "settings.json" in owed and "keeps what it read" in owed and "new g6_improve_start" in owed


def test_the_account_is_read_between_ticks_not_in_one(tmp_path):
    class Reader:
        said = {"claude": "reported by its own tool"}

        def __init__(self) -> None:
            self.refreshed: list = []

        def refresh(self, now) -> None:
            self.refreshed.append(heartbeat(runner.loop.root))

        def __call__(self, provider, start):
            return None

    reader = Reader()
    runner, _, _ = _runner(tmp_path, read_account=reader, reader=reader)
    runner.step()
    assert reader.refreshed == [None]                                    # before the tick's first beat
    assert heartbeat(runner.loop.root)["use"][0]["account"] == "reported by its own tool"


# The entry point ----------------------------------------------------------------------------------------------------

DOMAIN_FILE = """
import importlib.util
import sys
from pathlib import Path

from g6.metaharness import runner as runner_module
from g6.metaharness.method import Method

_spec = importlib.util.spec_from_file_location("loop_domains", {rig!r})
domains = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(domains, "WordProblems"):
    _spec.loader.exec_module(domains)

LOOP = {{"domain": domains.WordProblems(Path(__file__).parent), "proposer": domains.proposes({{"prompt": domains.WORKING}}),
        "method": Method(cycles=1, propose_budget=120, select_budget=400, race_block=12, reflect_calls=1),
        "audit_per_tick": 60, "person_free_adoption_outside_shadow": True}}
"""


def _folder(tmp_path: Path, **settings) -> Path:
    (tmp_path / "domain.py").write_text(DOMAIN_FILE.format(rig=str(Path(__file__).with_name("_loop_domains.py"))),
                                        encoding="utf-8")
    root = tmp_path / "loop"
    root.mkdir()
    (root / "settings.json").write_text(json.dumps({
        "domain_file": str(tmp_path / "domain.py"), "read_account": False,
        "windows": [{"provider": "claude", "allowance": 1_000_000, "week_start": START.isoformat()}], **settings}),
        encoding="utf-8")
    return root


def test_a_folder_declares_its_domain_and_windows_in_its_settings_file(tmp_path):
    loop, reader = open_loop(_folder(tmp_path), now=lambda: START)
    assert loop.domain.name == "word-problems" and reader is None
    assert loop.meter.windows["claude"].estimated is True and loop.settings.audit_per_tick == 60


def test_the_domain_file_cannot_turn_the_owners_ruling_on(tmp_path):
    loop, _ = open_loop(_folder(tmp_path), now=lambda: START)
    assert loop.settings.person_free_adoption_outside_shadow is False


def test_the_settings_file_cannot_turn_the_owners_ruling_on(tmp_path):
    loop, _ = open_loop(_folder(tmp_path, person_free_adoption_outside_shadow=True), now=lambda: START)
    assert loop.settings.person_free_adoption_outside_shadow is False


TWO_WINDOWS = [{"provider": name, "allowance": 1_000_000, "week_start": START.isoformat()} for name in ("claude", "codex")]


def test_the_domain_file_says_which_provider_its_calls_go_to(tmp_path):
    root = _folder(tmp_path, windows=TWO_WINDOWS)
    file = tmp_path / "domain.py"
    file.write_text(file.read_text(encoding="utf-8").replace('"audit_per_tick": 60,', '"audit_per_tick": 60, "provider": "codex",'),
                    encoding="utf-8")
    loop, _ = open_loop(root, now=lambda: START)
    assert loop.provider == "codex" and loop.problems == []


def test_two_windows_and_no_word_on_which_one_the_calls_go_to_runs_nothing(tmp_path):
    loop, _ = open_loop(_folder(tmp_path, windows=TWO_WINDOWS), now=lambda: START)
    assert [code for code, _ in loop.problems] == ["G6_E_LOOP_NO_BUDGET"]


def test_an_edit_to_the_domain_file_a_person_enrolled_stops_the_loop(tmp_path):
    root = _folder(tmp_path)
    loop, _ = open_loop(root, now=lambda: START + timedelta(days=2))
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    path = tmp_path / "domain.py"
    path.write_text(path.read_text(encoding="utf-8") + "\n# one line more\n", encoding="utf-8")
    got = loop.tick()
    assert got["warning_card"]["code"] == "G6_E_LOOP_FROZEN_CHANGED" and "domain_file" in got["message"]


def test_a_settings_file_with_no_domain_says_so(tmp_path):
    root = _folder(tmp_path)
    settings = json.loads((root / "settings.json").read_text(encoding="utf-8"))
    del settings["domain_file"]
    (root / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    with pytest.raises(ValueError, match="domain_file"):
        open_loop(root, now=lambda: START)


def test_the_account_reader_is_on_unless_the_settings_turn_it_off(tmp_path):
    root = _folder(tmp_path, read_account=True)
    assert open_loop(root, now=lambda: START)[1] is not None


def test_a_folder_that_reads_its_account_reads_it_before_an_evaluation_too(tmp_path):
    loop, reader = open_loop(_folder(tmp_path, read_account=True), now=lambda: START)
    assert loop._read_now == reader.read_now


def test_a_folder_that_turned_the_read_off_has_none_before_an_evaluation_either(tmp_path):
    loop, reader = open_loop(_folder(tmp_path), now=lambda: START)
    assert reader is None and loop._read_now is None


def test_run_says_why_when_a_folder_cannot_be_opened_and_leaves_a_heartbeat(tmp_path, capsys):
    root = tmp_path / "loop"
    root.mkdir()
    assert run(root) == 1
    assert "settings.json" in capsys.readouterr().err
    assert heartbeat(root)["state"] == "not-running" and not runner_alive(root)


def test_run_on_a_folder_that_is_not_there_writes_nothing(tmp_path, capsys):
    assert run(tmp_path / "nowhere") == 1 and not (tmp_path / "nowhere").exists()


def test_run_with_a_stop_file_ends_before_it_opens_anything(tmp_path):
    root = tmp_path / "loop"
    root.mkdir()
    (root / "STOP").write_text("stay down\n", encoding="utf-8")
    assert run(root) == 0 and heartbeat(root)["code"] == "G6_E_LOOP_STOPPED"     # the stop that ended it (review 3, 3)


def test_a_starting_heartbeat_is_on_record_before_the_lock_is_taken(tmp_path, monkeypatch):
    root, _ = _enrolled_folder(tmp_path)
    seen, real = [], runner_lock

    def noted(folder):
        seen.append(heartbeat(root))
        return real(folder)

    monkeypatch.setattr("g6.metaharness.runner.runner_lock", noted)
    assert run(root, now=lambda: START + timedelta(days=2), sleep=_then_stop(root)) == 0
    assert seen[0]["state"] == "starting" and seen[0]["pid"] == os.getpid() and seen[0]["at"] and "in_job" in seen[0]


def test_a_refused_runner_leaves_the_heartbeat_of_the_one_alive_as_it_was(tmp_path, monkeypatch):
    monkeypatch.setattr("g6.workspace._coordination.MUTEX_WAIT", 0.05)
    root = tmp_path / "loop"
    root.mkdir()
    with runner_lock(root):
        beat(root, state="waiting", pid=7)
        assert run(root) == 1
        assert heartbeat(root) == {"state": "waiting", "pid": 7}


def test_every_heartbeat_of_a_run_says_whether_it_is_in_a_job(tmp_path):
    runner, _, _ = _runner(tmp_path)
    runner.step()
    assert heartbeat(runner.loop.root)["in_job"] == _in_job()
    (runner.loop.root / "STOP").write_text("done\n", encoding="utf-8")
    assert runner.step() is None and heartbeat(runner.loop.root)["in_job"] == _in_job()


@pytest.mark.skipif(os.name != "nt", reason="a Windows job")
def test_the_system_is_asked_whether_this_process_is_in_a_job():
    assert _in_job() in (True, False)


@pytest.mark.skipif(os.name == "nt", reason="elsewhere there is no such thing to ask")
def test_where_a_job_means_nothing_it_is_not_said():
    assert _in_job() is None


def test_a_second_runner_on_a_folder_does_nothing(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("g6.workspace._coordination.MUTEX_WAIT", 0.05)
    root = tmp_path / "loop"
    root.mkdir()
    with runner_lock(root):
        assert run(root) == 1
    assert "already" in capsys.readouterr().err


def _agreed(root: Path, log: CycleLog) -> None:
    """A person's start as ``g6_improve_start`` records it."""
    log.start("Ada", at=START.isoformat(), settings=digest(read_settings(root)), stops=stops_asked(root))


def _grow_allowance(root: Path) -> None:
    path = root / "settings.json"
    settings = json.loads(path.read_text(encoding="utf-8"))
    settings["windows"][0]["allowance"] = 1_000_000_000
    path.write_text(json.dumps(settings), encoding="utf-8")


def _enrolled_folder(tmp_path: Path, *, started: bool = True) -> tuple[Path, ShadowLoop]:
    root = _folder(tmp_path)
    loop, _ = open_loop(root, now=lambda: START)
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    if started:
        _agreed(root, loop.log)
    return root, loop


def _then_stop(root: Path):
    def sleep(seconds: float) -> None:
        (root / "STOP").write_text("done\n", encoding="utf-8")

    return sleep


def test_the_command_line_runs_no_tick_on_an_enrolled_folder_no_person_started(tmp_path):
    root, loop = _enrolled_folder(tmp_path, started=False)
    env = {**os.environ, "PYTHONPATH": str(Path(g6.__file__).resolve().parents[1])}   # one G6_HOME: it holds the enrolment
    child = subprocess.Popen([sys.executable, "-P", "-m", "g6.metaharness.loop", "run", str(root)], cwd=root, env=env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = clock.monotonic() + 25
        while child.poll() is None and clock.monotonic() < deadline:
            if (heartbeat(root) or {}).get("tick", 0) >= 1:
                break
            clock.sleep(0.05)
    finally:
        (root / "STOP").write_text("the test is over\n", encoding="utf-8")
        try:
            _, err = child.communicate(timeout=65)
        except subprocess.TimeoutExpired:
            child.kill()
            _, err = child.communicate(timeout=10)
    log = CycleLog(root / CYCLE_LOG)
    assert log.started() is None and log.cycles() == [], f"ran {len(log.cycles())} ticks with no start: exit {child.returncode}"
    assert child.returncode == 1 and "g6_improve_start" in err
    assert heartbeat(root)["code"] == "G6_E_LOOP_NOT_STARTED" and heartbeat(root)["state"] == "not-running"


def test_nothing_of_the_folders_runs_before_the_start_is_checked(tmp_path):
    root, _ = _enrolled_folder(tmp_path, started=False)
    marker = tmp_path / "ran"
    (tmp_path / "domain.py").write_text(f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")
    assert run(root) == 1 and not marker.exists()


def _plants_a_marker(tmp_path: Path) -> tuple[str, Path]:
    marker = tmp_path / "changed-domain-ran"
    return f"open({str(marker)!r}, 'w').close()\nraise RuntimeError('the changed file ran')\n", marker


def test_a_restart_does_not_run_a_domain_file_changed_since_it_was_enrolled(tmp_path, capsys):
    root, _ = _enrolled_folder(tmp_path)
    changed, marker = _plants_a_marker(tmp_path)
    (tmp_path / "domain.py").write_text(changed, encoding="utf-8")
    assert run(root) == 1 and not marker.exists()
    assert heartbeat(root)["code"] == "G6_E_LOOP_FROZEN_CHANGED" and "enrolled" in capsys.readouterr().err
    assert CycleLog(root / CYCLE_LOG).cycles() == []


def test_the_bytes_that_were_checked_are_the_bytes_that_run(tmp_path, monkeypatch):
    root, _ = _enrolled_folder(tmp_path)
    changed, marker = _plants_a_marker(tmp_path)
    rig = Path(__file__).with_name("_loop_domains.py")
    before = rig.read_bytes()
    real = importlib.util.spec_from_file_location

    def swapped_after_the_check(name, path, *args, **kwargs):
        if name == "g6_loop_domain":                                     # the folder's own file, and nothing it loads
            Path(path).write_text(changed, encoding="utf-8")
        return real(name, path, *args, **kwargs)

    monkeypatch.setattr("g6.metaharness.runner.importlib.util.spec_from_file_location", swapped_after_the_check)
    run(root, now=lambda: START + timedelta(days=2), sleep=_then_stop(root))
    assert not marker.exists() and rig.read_bytes() == before


def test_lifting_a_pause_does_not_run_a_domain_file_changed_since_it_was_enrolled(tmp_path, monkeypatch, capsys):
    root, loop = _enrolled_folder(tmp_path)
    loop.log.pause("three failed cycles in a row")
    changed, marker = _plants_a_marker(tmp_path)
    (tmp_path / "domain.py").write_text(changed, encoding="utf-8")
    monkeypatch.setattr("g6.metaharness.runner._at_a_terminal", lambda: True)
    assert main(["resume", str(root), "--as", "Ada"]) == 1 and not marker.exists()
    assert CycleLog(root / CYCLE_LOG).paused() and "enrolled" in capsys.readouterr().err


def test_a_person_enrolling_again_accepts_a_changed_domain_file(tmp_path, monkeypatch):
    root, _ = _enrolled_folder(tmp_path)
    path = tmp_path / "domain.py"
    path.write_text(path.read_text(encoding="utf-8") + "\n# one line more\n", encoding="utf-8")
    monkeypatch.setattr("g6.metaharness.runner._at_a_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "word-problems")
    assert main(["enrol", str(root), "--as", "Ada"]) == 0


def test_a_start_the_person_agreed_to_lets_a_restart_run(tmp_path):
    root, _ = _enrolled_folder(tmp_path)
    assert run(root, now=lambda: START + timedelta(days=2), sleep=_then_stop(root)) == 0
    assert CycleLog(root / CYCLE_LOG).cycles()


def test_a_stop_since_the_start_refuses_a_restart_even_with_its_file_gone(tmp_path, capsys):
    root, _ = _enrolled_folder(tmp_path)
    assert run(root, now=lambda: START + timedelta(days=2), sleep=_then_stop(root)) == 0
    ran = len(CycleLog(root / CYCLE_LOG).cycles())
    (root / "STOP").unlink()
    assert run(root, now=lambda: START + timedelta(days=2), sleep=_then_stop(root)) == 1
    assert len(CycleLog(root / CYCLE_LOG).cycles()) == ran and "g6_improve_start" in capsys.readouterr().err


def test_a_stop_counted_since_the_start_closes_the_gate_with_its_file_gone(tmp_path, capsys):
    root, _ = _enrolled_folder(tmp_path)
    ask_to_stop(root, "asked while a tick held the records")
    (root / "STOP").unlink()                                             # by hand, with no note of the stop written
    assert CycleLog(root / CYCLE_LOG).start_in_force()                   # nothing in the records says it stopped
    assert run(root, now=lambda: START + timedelta(days=2), sleep=_then_stop(root)) == 1
    assert CycleLog(root / CYCLE_LOG).cycles() == []
    assert "a stop" in capsys.readouterr().err and heartbeat(root)["code"] == "G6_E_LOOP_STOPPED"   # its own code
    assert not CycleLog(root / CYCLE_LOG).start_in_force()               # and the run's end noted it in the records


def test_a_start_with_no_stop_count_stands_on_nothing(tmp_path, capsys):
    root, loop = _enrolled_folder(tmp_path, started=False)
    loop.log.start("Ada", at=START.isoformat())
    assert run(root, now=lambda: START + timedelta(days=2), sleep=_then_stop(root)) == 1
    assert CycleLog(root / CYCLE_LOG).cycles() == []


def test_settings_changed_since_the_person_agreed_refuse_a_restart(tmp_path, capsys):
    root, _ = _enrolled_folder(tmp_path)
    _grow_allowance(root)
    assert run(root, now=lambda: START + timedelta(days=2), sleep=_then_stop(root)) == 1
    assert CycleLog(root / CYCLE_LOG).cycles() == []
    assert heartbeat(root)["code"] == "G6_E_LOOP_SETTINGS_CHANGED" and "g6_improve_start" in capsys.readouterr().err


def test_a_start_with_no_settings_on_record_stands_on_nothing(tmp_path):
    root, loop = _enrolled_folder(tmp_path, started=False)
    loop.log.start("Ada", at=START.isoformat(), stops=stops_asked(root))
    assert run(root, now=lambda: START + timedelta(days=2), sleep=_then_stop(root)) == 1
    assert CycleLog(root / CYCLE_LOG).cycles() == []


def test_a_run_uses_the_settings_it_checked_not_a_later_write(tmp_path, monkeypatch):
    root, _ = _enrolled_folder(tmp_path)
    real, seen = runner_module._gated, []

    def written_after_the_check(folder, settings):                       # the gate, which checks the settings it was given
        found = real(folder, settings)
        _grow_allowance(root)
        return found

    monkeypatch.setattr("g6.metaharness.runner._gated", written_after_the_check)
    assert run(root, now=lambda: START + timedelta(days=2), sleep=lambda seconds: (seen.append(heartbeat(root)),
                                                                                   _then_stop(root)(seconds))) == 0
    assert seen[0]["use"][0]["ceiling"] < 1_000_000


def test_a_lifted_stop_left_on_disk_lets_a_restart_run_and_any_stop_after_it_ends_the_run(tmp_path):
    root, loop = _enrolled_folder(tmp_path, started=False)
    (root / "STOP").write_text("an old stop\n", encoding="utf-8")
    loop.log.start("Ada", at=START.isoformat(), settings=digest(read_settings(root)), stops=stops_asked(root),
                   **stop_to_lift(root))

    def a_stop_after_a_tick(seconds: float) -> None:
        ask_to_stop(root, "a newer stop")

    assert run(root, now=lambda: START + timedelta(days=2), sleep=a_stop_after_a_tick) == 0
    assert len(CycleLog(root / CYCLE_LOG).cycles()) == 1 and "a newer stop" in stop_reason(root)


def test_a_stop_file_a_restart_finds_ends_the_start_too(tmp_path):
    root, _ = _enrolled_folder(tmp_path)
    (root / "STOP").write_text("set by hand\n", encoding="utf-8")
    assert run(root) == 0
    (root / "STOP").unlink()
    assert run(root) == 1 and CycleLog(root / CYCLE_LOG).cycles() == []


def test_a_new_start_after_a_stop_lets_it_run_again(tmp_path):
    root, _ = _enrolled_folder(tmp_path)
    assert run(root, now=lambda: START + timedelta(days=2), sleep=_then_stop(root)) == 0
    (root / "STOP").unlink()
    _agreed(root, CycleLog(root / CYCLE_LOG))
    assert run(root, now=lambda: START + timedelta(days=2), sleep=_then_stop(root)) == 0


def test_records_that_do_not_check_out_start_nothing(tmp_path, capsys):
    root, _ = _enrolled_folder(tmp_path)
    path = root / CYCLE_LOG
    path.write_text(path.read_text(encoding="utf-8").replace("Ada", "Bo"), encoding="utf-8")
    assert run(root) == 1 and "cannot be trusted" in capsys.readouterr().err


def test_a_run_holds_the_lock_while_it_lives(tmp_path):
    root = _folder(tmp_path)
    loop, _ = open_loop(root, now=lambda: START)
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    _agreed(root, loop.log)                           # a run needs a start that stands
    seen = []

    def sleep(seconds: float) -> None:
        seen.append(runner_alive(root))
        (root / "STOP").write_text("done\n", encoding="utf-8")

    assert run(root, now=lambda: START + timedelta(days=2), sleep=sleep) == 0
    assert seen == [True] and not runner_alive(root)


def test_only_a_person_at_a_terminal_enrols_a_domain(tmp_path, capsys):
    root = _folder(tmp_path)
    assert main(["enrol", str(root), "--as", "Ada"]) == 1              # the test's input is no terminal
    assert "terminal" in capsys.readouterr().err
    loop, _ = open_loop(root, now=lambda: START)
    assert loop.log.enrolled() is None


def test_a_person_enrols_by_typing_the_domains_name_back(tmp_path, monkeypatch, capsys):
    root = _folder(tmp_path)
    monkeypatch.setattr("g6.metaharness.runner._at_a_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "word-problems")
    assert main(["enrol", str(root), "--as", "Ada"]) == 0
    assert open_loop(root, now=lambda: START)[0].log.enrolled()["by"] == "Ada"
    assert "word-problems" in capsys.readouterr().out


def test_the_wrong_name_typed_back_enrols_nothing(tmp_path, monkeypatch):
    root = _folder(tmp_path)
    monkeypatch.setattr("g6.metaharness.runner._at_a_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    assert main(["enrol", str(root), "--as", "Ada"]) == 1
    assert open_loop(root, now=lambda: START)[0].log.enrolled() is None


def test_an_agents_name_enrols_nothing_even_at_a_terminal(tmp_path, monkeypatch):
    root = _folder(tmp_path)
    monkeypatch.setattr("g6.metaharness.runner._at_a_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "word-problems")
    assert main(["enrol", str(root), "--as", "auto:loop"]) == 1
    assert open_loop(root, now=lambda: START)[0].log.enrolled() is None


def test_only_a_person_at_a_terminal_lifts_a_pause(tmp_path, monkeypatch, capsys):
    root = _folder(tmp_path)
    loop, _ = open_loop(root, now=lambda: START)
    loop.enrol("Ada")
    loop.log.pause("three failed cycles in a row")
    assert main(["resume", str(root), "--as", "Ada"]) == 1
    assert loop.log.paused()
    monkeypatch.setattr("g6.metaharness.runner._at_a_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "word-problems")
    assert main(["resume", str(root), "--as", "Ada"]) == 0
    loop.log.reload()
    assert loop.log.paused() == ""


def _adopted_folder(tmp_path: Path) -> Path:
    root, _ = _enrolled_folder(tmp_path)
    assert open_loop(root, now=lambda: START + timedelta(days=2))[0].tick()["outcome"] == "adopted"
    return root


def lineage_of(root: Path) -> dict:
    return lineage(root / "workspace")


def _chain_length(root: Path) -> int:
    return len(lineage_of(root)["recent"])


def test_a_person_takes_a_generation_back_by_typing_the_domains_name(tmp_path, monkeypatch, capsys):
    root = _adopted_folder(tmp_path)
    monkeypatch.setattr("g6.metaharness.runner._at_a_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "word-problems")
    assert main(["demote", str(root), "1", "--as", "Ada"]) == 0
    assert lineage_of(root)["recent"][-1]["asked_by"] == "Ada" and "generation 1" in capsys.readouterr().out


def test_only_a_person_at_a_terminal_takes_a_generation_back(tmp_path, capsys):
    root = _adopted_folder(tmp_path)
    assert main(["demote", str(root), "1", "--as", "Ada"]) == 1
    assert "terminal" in capsys.readouterr().err and _chain_length(root) == 1


def test_the_wrong_name_typed_back_takes_nothing_back(tmp_path, monkeypatch):
    root = _adopted_folder(tmp_path)
    monkeypatch.setattr("g6.metaharness.runner._at_a_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    assert main(["demote", str(root), "1", "--as", "Ada"]) == 1 and _chain_length(root) == 1


def test_taking_a_generation_back_does_not_run_a_domain_file_changed_since_it_was_enrolled(tmp_path, monkeypatch, capsys):
    root = _adopted_folder(tmp_path)
    changed, marker = _plants_a_marker(tmp_path)
    (tmp_path / "domain.py").write_text(changed, encoding="utf-8")
    monkeypatch.setattr("g6.metaharness.runner._at_a_terminal", lambda: True)
    assert main(["demote", str(root), "1", "--as", "Ada"]) == 1 and not marker.exists()
    assert "enrolled" in capsys.readouterr().err and _chain_length(root) == 1

def test_the_module_runs_as_a_command_with_python_minus_p():
    env = {**os.environ, "PYTHONPATH": str(Path(g6.__file__).resolve().parents[1])}
    done = subprocess.run([sys.executable, "-P", "-W", "error::RuntimeWarning", "-m", "g6.metaharness.loop", "--help"],
                          capture_output=True, text=True, env=env, timeout=120)
    assert done.returncode == 0, done.stderr
    assert "run" in done.stdout and "enrol" in done.stdout and "resume" in done.stdout


def test_a_method_a_domain_file_sets_is_what_the_tick_uses(tmp_path):
    assert open_loop(_folder(tmp_path), now=lambda: START)[0].settings.method.cycles == 1


# A log that will not take a cycle ---------------------------------------------------------------------------------------

HELD_OPEN = PermissionError("held open by something else")


def _log_refuses(monkeypatch, loop, error: Exception = HELD_OPEN) -> None:
    def refused(*args, **kwargs):
        raise error

    monkeypatch.setattr(loop.log, "cycle", refused)
    monkeypatch.setattr(loop.log, "pause", refused)


def _three_passes(runner, time) -> None:
    for _ in range(3):
        runner.step()
        time.at += timedelta(hours=1)


@pytest.mark.skipif(os.name != "nt", reason="a real Windows sharing restriction")
def test_a_log_that_reads_but_will_not_write_stops_the_spending(tmp_path):
    runner, time, domain = _runner(tmp_path, domain=domains.WordProblems(tmp_path, weak=0.6))
    assert runner.loop.tick()["outcome"] == "adopted"
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                   wintypes.DWORD, wintypes.HANDLE)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.CreateFileW(str(runner.loop.log.path), 0x80000000, 1, None, 3, 0x80, None)    # readers yes, writers no
    assert handle != wintypes.HANDLE(-1).value
    try:
        _three_passes(runner, time)
        calls = len(domain.llm.calls)
        assert runner.step() is None and len(domain.llm.calls) == calls
    finally:
        assert kernel.CloseHandle(handle)


@pytest.mark.parametrize("error", [PermissionError("held open"), LedgerError("the ledger file changed")])
def test_three_cycles_the_log_would_not_take_end_the_run_and_say_why(tmp_path, monkeypatch, error):
    runner, time, domain = _runner(tmp_path)
    _log_refuses(monkeypatch, runner.loop, error)
    _three_passes(runner, time)
    calls, root = len(domain.llm.calls), runner.loop.root
    assert calls > 0 and runner.step() is None and len(domain.llm.calls) == calls
    assert "could not be written" in stop_reason(root) and stops_asked(root) == 1      # a restart stays closed
    beat = heartbeat(root)
    assert beat["state"] == "stopped" and beat["code"] == "G6_E_LOOP_CANNOT_RECORD"
    assert "could not be written" in beat["reason"]
    page = latest(root).read_text(encoding="utf-8")
    assert "The runner ended" in page and "could not be written" in page and "g6_improve_start" in page


def test_status_shows_cycles_being_lost_and_then_why_the_run_ended(tmp_path, monkeypatch):
    from g6.mcp import improve

    runner, time, _ = _runner(tmp_path)
    _log_refuses(monkeypatch, runner.loop)
    runner.step()
    time.at += timedelta(hours=1)
    seen = improve.status(str(runner.loop.root))
    assert seen["pause"]["code"] == "G6_E_LOOP_CANNOT_RECORD" and "could not be written" in seen["pause"]["reason"]
    assert seen["stop"] == ""
    _three_passes(runner, time)
    assert runner.step() is None
    seen = improve.status(str(runner.loop.root))
    assert "could not be written" in seen["stop"] and seen["last_heartbeat"]["state"] == "stopped"


def test_a_cycle_the_log_takes_starts_the_count_again(tmp_path, monkeypatch):
    runner, time, _ = _runner(tmp_path)
    taken, refusals = runner.loop.log.cycle, iter((True, True, False, True, True))

    def now_and_then(row):
        if next(refusals):
            raise PermissionError("held open for a moment")
        return taken(row)

    monkeypatch.setattr(runner.loop.log, "cycle", now_and_then)
    for _ in range(5):
        assert runner.step() is not None
        time.at += timedelta(hours=1)
    assert next(refusals, None) is None and not (runner.loop.root / "STOP").exists()


def test_a_runner_that_can_write_nothing_at_all_stops_spending_all_the_same(tmp_path, monkeypatch):
    def no_file(*args):
        raise PermissionError("nothing in this folder can be written")

    runner, time, domain = _runner(tmp_path)
    _log_refuses(monkeypatch, runner.loop)
    monkeypatch.setattr(sys.modules[ShadowLoop.__module__], "ask_to_stop", no_file)
    _three_passes(runner, time)
    calls = len(domain.llm.calls)
    assert not (runner.loop.root / "STOP").exists()
    assert "could not be written" in runner.loop.tick()["message"] and len(domain.llm.calls) == calls
    assert runner.step() is None and len(domain.llm.calls) == calls


def test_a_tick_that_broke_and_could_not_be_written_counts_once(tmp_path, monkeypatch):
    def broken():
        raise RuntimeError("the tick broke")

    runner, time, _ = _runner(tmp_path)
    _log_refuses(monkeypatch, runner.loop)
    monkeypatch.setattr(runner.loop, "_run", broken)
    runner.step()
    assert runner.loop.lost_cycles == 1
    time.at += timedelta(hours=1)
    _three_passes(runner, time)
    assert runner.step() is None and "could not be written" in stop_reason(runner.loop.root)


def test_a_fault_that_could_not_even_reach_the_log_counts_too(tmp_path, monkeypatch):
    def broken(*args):
        raise OSError("the log cannot be opened")

    runner, time, _ = _runner(tmp_path)
    monkeypatch.setattr(runner.loop, "tick", broken)
    monkeypatch.setattr(runner.loop, "fault", broken)
    _three_passes(runner, time)
    assert runner.step() is None and "could not be written" in stop_reason(runner.loop.root)
