# SPDX-License-Identifier: Apache-2.0
"""The runner: a loop that runs on its own, on an injected clock and sleep, so a week passes in a test. FakeLLM only."""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import time as clock
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import g6
from g6.metaharness.loop import LoopSettings, ShadowLoop
from g6.metaharness.method import Method
from g6.metaharness.runner import FIRST_REST, LONGEST_REST, NAP, Runner, main, open_loop, run
from g6.self_maintenance.digest import digest_path, latest
from g6.self_maintenance.envelope import CYCLE_LOG, CycleLog, heartbeat, runner_alive, runner_lock
from g6.self_maintenance.pacing import AccountReading, ProviderWindow

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


def test_a_digest_that_cannot_be_written_does_not_end_the_run(tmp_path, monkeypatch):
    runner, time, _ = _runner(tmp_path)
    monkeypatch.setattr("g6.metaharness.runner.digest.write", lambda *a: (_ for _ in ()).throw(OSError("full")))
    _stopped_after(time, timedelta(seconds=1))
    assert runner.run() == 0 and runner.loop.log.cycles()


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
    assert run(root) == 0 and heartbeat(root) is None


def test_a_second_runner_on_a_folder_does_nothing(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("g6.workspace._coordination.MUTEX_WAIT", 0.05)
    root = tmp_path / "loop"
    root.mkdir()
    with runner_lock(root):
        assert run(root) == 1
    assert "already" in capsys.readouterr().err


def _enrolled_folder(tmp_path: Path, *, started: bool = True) -> tuple[Path, ShadowLoop]:
    root = _folder(tmp_path)
    loop, _ = open_loop(root, now=lambda: START)
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    if started:
        loop.log.start("Ada", at=START.isoformat())
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
    CycleLog(root / CYCLE_LOG).start("Ada", at=START.isoformat())
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
    loop.log.start("Ada", at=START.isoformat())       # a run needs a start that stands
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


def test_the_module_runs_as_a_command_with_python_minus_p():
    env = {**os.environ, "PYTHONPATH": str(Path(g6.__file__).resolve().parents[1])}
    done = subprocess.run([sys.executable, "-P", "-W", "error::RuntimeWarning", "-m", "g6.metaharness.loop", "--help"],
                          capture_output=True, text=True, env=env, timeout=120)
    assert done.returncode == 0, done.stderr
    assert "run" in done.stdout and "enrol" in done.stdout and "resume" in done.stdout


def test_a_method_a_domain_file_sets_is_what_the_tick_uses(tmp_path):
    assert open_loop(_folder(tmp_path), now=lambda: START)[0].settings.method.cycles == 1
