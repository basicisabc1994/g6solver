# SPDX-License-Identifier: Apache-2.0
"""The envelope around a tick (design 6.6, 6.10), enforced in code. Each section's test was seen failing first."""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import g6
from g6.core import stop
from g6.core.llm import FakeLLM
from g6.harness import Check
from g6.integrity import build_manifest
from g6.metaharness import Profile, SuiteCase
from g6.metaharness import monitor as drift
from g6.metaharness.domain import Judge
from g6.metaharness.loop import ENVELOPE_PATTERNS, LoopSettings, ShadowLoop
from g6.metaharness.method import Method
from g6.self_maintenance.envelope import heartbeat
from g6.self_maintenance.pacing import ProviderWindow
from g6.workspace._coordination import Coordination

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

# Three failed cycles -------------------------------------------------------------------------------------------------
NOTHING_HELPS = {"prompt": "Please be polite."}


def test_three_failed_cycles_in_a_row_pause_the_loop_with_a_stated_reason(tmp_path):
    loop, domain = _enrolled(tmp_path, proposer=domains.proposes(NOTHING_HELPS))
    ends = [loop.tick()["outcome"] for _ in range(3)]
    why = loop.log.paused()
    assert "three failed cycles" in why and all(end in why for end in ends)
    calls = len(domain.llm.calls)
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_PAUSED" and why in got["message"] and len(domain.llm.calls) == calls
    assert len(loop.log.cycles()) == 3 and heartbeat(loop.root)["code"] == "G6_E_LOOP_PAUSED"


def test_two_failed_cycles_do_not_pause_it(tmp_path):
    loop, _ = _enrolled(tmp_path, proposer=domains.proposes(NOTHING_HELPS))
    for _ in range(2):
        loop.tick()
    assert loop.log.paused() == ""


def test_a_pause_outlives_the_process(tmp_path):
    loop, domain = _enrolled(tmp_path, proposer=domains.proposes(NOTHING_HELPS))
    for _ in range(3):
        loop.tick()
    again, _ = _loop(tmp_path, domain=domain)
    assert _code(again.tick()) == "G6_E_LOOP_PAUSED"


def test_only_a_person_lifts_the_pause_and_then_it_runs(tmp_path):
    loop, domain = _enrolled(tmp_path, proposer=domains.proposes(NOTHING_HELPS))
    for _ in range(3):
        loop.tick()
    for name in ("", "auto:loop"):
        assert loop.resume(name)["completion_state"] == "blocked-escalated"
    assert _code(loop.tick()) == "G6_E_LOOP_PAUSED"
    assert loop.resume("Ada")["completion_state"] == "qualified-draft"
    assert loop.log.paused() == "" and loop.status()["failed_streak"] == 0
    assert _code(loop.tick()) != "G6_E_LOOP_PAUSED"


def test_a_cycle_that_breaks_counts_towards_the_pause(tmp_path):
    loop, domain = _enrolled(tmp_path)

    def broken(view):
        raise RuntimeError("no")

    domain.failures = broken
    for _ in range(3):
        loop.tick()
    assert "error" in loop.log.paused()


# The frozen digests --------------------------------------------------------------------------------------------------

def test_a_changed_frozen_case_stops_the_loop_before_anything_is_spent(tmp_path):
    loop, domain = _enrolled(tmp_path)
    domain.pool[0] = SuiteCase(domain.pool[0].case_id, domain.pool[0].input, "999")
    calls = len(domain.llm.calls)
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_FROZEN_CHANGED" and "cases" in got["message"]
    assert len(domain.llm.calls) == calls and loop.log.cycles() == []


def test_a_new_case_is_a_changed_pool(tmp_path):
    loop, domain = _enrolled(tmp_path)
    domain.pool.append(SuiteCase("late", "What is the answer?", "1"))
    assert _code(loop.tick()) == "G6_E_LOOP_FROZEN_CHANGED"


def _judge_in(folder: Path, name: str):
    path = folder / f"{name}.py"
    path.write_text("def exact(output, expected):\n    return output.strip() == f'Answer: {expected}'\n",
                    encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.exact, path


def test_an_edit_anywhere_in_the_judges_file_stops_the_loop(tmp_path):
    (tmp_path / "judges").mkdir()
    verdict, path = _judge_in(tmp_path / "judges", "judge_for_a_test")
    domain = domains.WordProblems(tmp_path)
    domain.judge = lambda: Judge(verdict, answers_by="independent")
    loop, _ = _enrolled(tmp_path, domain=domain)
    path.write_text(path.read_text(encoding="utf-8") + "\n# a helper the judge calls changed\n", encoding="utf-8")
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_FROZEN_CHANGED" and "evaluator" in got["message"]


def test_a_change_to_g6s_judging_code_stops_the_loop(tmp_path, monkeypatch):
    loop, _ = _enrolled(tmp_path)
    monkeypatch.setattr("g6.metaharness.loop.judging_sha256", lambda: "changed")
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_FROZEN_CHANGED" and "evaluator" in got["message"]


def test_a_changed_check_stops_the_loop(tmp_path):
    loop, domain = _enrolled(tmp_path)
    run = domain.execution()
    weaker = Check("anything goes", lambda out: True, check_id="anything", known_bad=(), known_good="x")
    domain.execution = lambda: dataclasses.replace(run, checks=(weaker,))
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_FROZEN_CHANGED" and "evaluator" in got["message"]


def test_a_changed_sealed_file_stops_the_loop(tmp_path):
    loop, domain = _enrolled(tmp_path)
    domain.sealed_file.write_text("a sealed case was edited", encoding="utf-8")
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_FROZEN_CHANGED" and "sealed" in got["message"]


def test_a_change_is_not_a_failed_cycle_and_a_person_enrolling_again_accepts_it(tmp_path):
    loop, domain = _enrolled(tmp_path)
    domain.pool.append(SuiteCase("late", "What is the answer?", "1"))
    for _ in range(4):
        loop.tick()
    assert loop.log.failed_streak() == 0 and loop.log.paused() == ""
    again, _ = _loop(tmp_path, domain=domain)
    assert _code(again.tick()) == "G6_E_LOOP_FROZEN_CHANGED"       # the new process is no way round it
    assert again.enrol("Ada")["completion_state"] == "qualified-draft"
    assert _code(again.tick()) != "G6_E_LOOP_FROZEN_CHANGED"


def test_the_digest_is_of_what_judges_never_of_the_profile(tmp_path):
    loop, _ = _enrolled(tmp_path)
    assert loop.tick()["outcome"] == "adopted"                     # the head moved
    assert _code(loop.tick()) != "G6_E_LOOP_FROZEN_CHANGED"        # and nothing frozen did


# The held-out split ---------------------------------------------------------------------------------------------------

def _spend_the_split(loop) -> None:
    split = loop.workflow.suite.digest("held_out")
    for i in range(loop.workflow.policy.max_evaluations):
        loop.mh.ledger.append("evaluation", f"spent-{i}", {"split_digest": split})


def test_a_spent_held_out_split_is_a_wait_for_a_person_and_costs_nothing(tmp_path):
    loop, domain = _enrolled(tmp_path)
    _spend_the_split(loop)
    calls = len(domain.llm.calls)
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_HELD_OUT_SPENT" and "fresh" in got["message"]
    assert len(domain.llm.calls) == calls and loop.log.cycles() == []


# What the loop can reach ---------------------------------------------------------------------------------------------

def test_the_loops_folder_may_not_hold_the_sealed_split(tmp_path):
    domain = domains.WordProblems(tmp_path)
    with pytest.raises(ValueError, match="G6_E_LOOP_EVALUATOR_IN_REACH"):
        ShadowLoop(domain, tmp_path, SETTINGS, proposer=domains.proposes({"prompt": "x"}))


def test_the_loops_folder_may_not_hold_the_judge(tmp_path):
    (tmp_path / "judge-here").mkdir()
    verdict, _ = _judge_in(tmp_path / "judge-here", "judge_two")
    domain = domains.WordProblems(tmp_path)
    domain.judge = lambda: Judge(verdict, answers_by="independent")
    with pytest.raises(ValueError, match="G6_E_LOOP_EVALUATOR_IN_REACH"):
        ShadowLoop(domain, tmp_path / "judge-here" / "loop", SETTINGS, proposer=domains.proposes({"prompt": "x"}))
    with pytest.raises(ValueError, match="G6_E_LOOP_EVALUATOR_IN_REACH"):
        ShadowLoop(domain, tmp_path / "judge-here", SETTINGS, proposer=domains.proposes({"prompt": "x"}))


def test_the_loops_folder_may_not_lie_in_g6s_own_source_and_nothing_is_created_there(tmp_path):
    root = Path(g6.__file__).parent / "loop-must-not-be-created-here"
    with pytest.raises(ValueError, match="G6_E_LOOP_EVALUATOR_IN_REACH"):
        ShadowLoop(domains.WordProblems(tmp_path), root, SETTINGS, proposer=domains.proposes({"prompt": "x"}))
    assert not root.exists()

# The sealed split inside a tick --------------------------------------------------------------------------------------

def _reaches_for_the_sealed_file(domain, attempts):
    def reply(system: str, user: str) -> str:
        try:
            attempts.append(domain.sealed_file.read_text(encoding="utf-8"))
        except PermissionError:
            attempts.append("refused")
        return json.dumps({"prompt": domains.WORKING})

    return FakeLLM(reply)


def test_a_proposer_that_reaches_for_the_sealed_split_gets_nothing_and_the_loop_pauses(tmp_path):
    domain, attempts = domains.WordProblems(tmp_path), []
    loop, _ = _enrolled(tmp_path, domain=domain, proposer=_reaches_for_the_sealed_file(domain, attempts))
    start = loop.mh._head()
    got = loop.tick()
    assert attempts and set(attempts) == {"refused"}
    assert got["outcome"] == "sealed_breach" and got["completion_state"] == "blocked-escalated"
    assert loop.mh._head() == start                                # it swallowed the refusal; the record did not
    assert "sealed" in loop.log.paused()
    assert _code(loop.tick()) == "G6_E_LOOP_PAUSED"


def test_the_sealed_split_is_readable_again_once_the_tick_is_over(tmp_path):
    loop, domain = _enrolled(tmp_path)
    loop.tick()
    assert domain.sealed_file.read_text(encoding="utf-8")


def test_no_proposer_is_ever_shown_a_sealed_held_out_or_monitor_case(tmp_path):
    proposer = domains.proposes({"prompt": domains.WORKING})
    loop, domain = _enrolled(tmp_path, proposer=proposer)
    loop.tick()
    shown = "\n".join(user for _, user in proposer.calls)
    assert shown
    for case in (*domain.sealed_cases, *loop.cut.held_out, *loop.cut.monitor, *loop.cut.selection):
        assert case.input not in shown


def test_the_task_model_never_runs_a_sealed_case(tmp_path):
    loop, domain = _enrolled(tmp_path)
    loop.tick()
    ran = "\n".join(user for _, user in domain.llm.calls)
    assert ran and not any(case.input in ran for case in domain.sealed_cases)


def test_no_sealed_case_is_in_anything_the_loop_holds(tmp_path):
    loop, domain = _enrolled(tmp_path)
    suite = loop.workflow.suite
    held = {c.case_id for split in (suite.dev, suite.selection, suite.held_out, loop.cut.monitor) for c in split}
    assert not held & {c.case_id for c in domain.sealed_cases}


# The monitor inside a tick -------------------------------------------------------------------------------------------

def test_an_adoption_that_got_worse_is_rolled_back_with_no_one_asked(tmp_path):
    domain = domains.WordProblems(tmp_path, weak=0.6)
    loop, _ = _enrolled(tmp_path, domain=domain)
    start = loop.mh._head()[1]
    assert loop.tick()["outcome"] == "adopted"
    left = loop.mh.held_out_left()
    domain.tutor.collapsed = True
    got = loop.tick()
    assert got["outcome"] == "rolled_back" and got["undoes"] == 1 and got["completion_state"] == "blocked-escalated"
    assert loop.mh._head()[1] == start and loop.mh.held_out_left() == left
    assert loop.log.cycles()[-1]["audit"]["pairs"] >= drift.MIN_PAIRS


def test_a_healthy_adoption_is_audited_and_kept(tmp_path):
    domain = domains.WordProblems(tmp_path, weak=0.6)
    loop, _ = _enrolled(tmp_path, domain=domain)
    loop.tick()
    head = loop.mh._head()
    got = loop.tick()
    assert got["outcome"] != "rolled_back" and loop.mh._head() == head
    assert loop.log.cycles()[-1]["audit"]["audited"] == len(loop.cut.monitor)


def test_adopting_and_rolling_back_in_turn_still_pauses(tmp_path):
    domain = domains.WordProblems(tmp_path, weak=0.6)
    tries = iter(range(99))

    def always_something_new(system: str, user: str) -> str:
        return json.dumps({"prompt": f"{domains.WORKING} Variant {next(tries)}."})

    loop, _ = _enrolled(tmp_path, domain=domain, proposer=FakeLLM(always_something_new))
    for _ in range(3):
        domain.tutor.collapsed = False
        assert loop.tick()["outcome"] == "adopted"
        domain.tutor.collapsed = True
        assert loop.tick()["outcome"] == "rolled_back"
    assert "three failed cycles" in loop.log.paused()


# The owner's ruling ---------------------------------------------------------------------------------------------------

def test_by_default_adoption_never_stands_in_for_a_person_outside_shadow(tmp_path):
    assert SETTINGS.person_free_adoption_outside_shadow is False
    loop, _ = _enrolled(tmp_path)
    loop.tick()
    got = loop.leave_shadow()
    assert got["completion_state"] == "blocked-escalated" and _code(got) == "G6_E_LOOP_PERSON_REQUIRED"
    assert domains.WORKING in got["candidate"]["prompt"]           # the profile, for a person to take to the gate
    assert [v["kind"] for v in loop.mh.store.versions()] == ["initial"]


def test_with_the_owners_setting_on_this_phase_still_moves_nothing(tmp_path):
    settings = dataclasses.replace(SETTINGS, person_free_adoption_outside_shadow=True)
    loop, _ = _enrolled(tmp_path, settings=settings)
    loop.tick()
    got = loop.leave_shadow()
    assert got["completion_state"] == "blocked-escalated" and _code(got) == "G6_E_LOOP_NO_PATH_OUT"
    assert [v["kind"] for v in loop.mh.store.versions()] == ["initial"]


def test_a_profile_cannot_turn_the_setting_on(tmp_path):
    assert "person_free_adoption_outside_shadow" not in Profile.model_fields


# Nothing outward -----------------------------------------------------------------------------------------------------

def test_a_tick_starts_no_process_and_opens_no_connection(tmp_path, monkeypatch):
    def refused(*args, **kwargs):
        raise AssertionError("a tick reached outside this process")

    monkeypatch.setattr(subprocess, "Popen", refused)
    monkeypatch.setattr(socket.socket, "connect", refused)
    loop, _ = _enrolled(tmp_path)
    assert loop.tick()["outcome"] == "adopted"


def test_everything_a_tick_writes_is_inside_the_loops_folder(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("x", encoding="utf-8")
    domain = domains.WordProblems(tmp_path)
    before = sorted(p.name for p in tmp_path.iterdir())
    sealed_bytes = domain.sealed_file.read_bytes()
    loop = ShadowLoop(domain, tmp_path / "loop", SETTINGS, proposer=domains.proposes({"prompt": domains.WORKING}),
                      now=lambda: NOW, clock=domains.Clock())
    loop.enrol("Ada")
    loop.tick()
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([*before, "loop"])
    assert domain.sealed_file.read_bytes() == sealed_bytes

# What a self-review of the envelope found ------------------------------------------------------------------------------

def test_an_audit_whose_pairs_all_tie_does_not_break_the_tick(tmp_path):
    domain = domains.WordProblems(tmp_path, weak=0.6)
    loop, _ = _enrolled(tmp_path, domain=domain)
    assert loop.tick()["outcome"] == "adopted"
    domain.tutor.weak = domain.tutor.strong                        # old and new now answer alike: no disagreement
    got = loop.tick()
    assert got["outcome"] != "error"
    assert loop.log.cycles()[-1]["audit"]["pairs"] == 0 and loop.log.cycles()[-1]["audit"]["threshold"] is None


def test_two_ticks_cannot_run_on_one_folder(tmp_path, monkeypatch):
    monkeypatch.setattr("g6.workspace._coordination.MUTEX_WAIT", 0.05)
    loop, domain = _enrolled(tmp_path)
    calls = len(domain.llm.calls)
    with Coordination(loop.root / ".tick").mutex():                # another process's tick
        got = loop.tick()
    assert _code(got) == "G6_E_LOOP_BUSY" and got["completion_state"] == "blocked-escalated"
    assert len(domain.llm.calls) == calls and heartbeat(loop.root) is None
    assert loop.tick()["outcome"] == "adopted"


def _in_held_out(loop) -> set[str]:
    return {c.case_id for c in loop.cut.held_out}


def _fresh_cases(start: int, count: int) -> list[SuiteCase]:
    return [SuiteCase(f"fresh-{i:04d}", *domains._case(domains.word_problem(i))) for i in range(start, start + count)]


def test_a_person_cannot_refresh_a_spent_split_by_adding_a_case_to_it(tmp_path):
    loop, domain = _enrolled(tmp_path)
    assert loop.tick()["outcome"] == "adopted"                     # one use of the split
    domain.pool.extend(_fresh_cases(5000, 40))                     # its digest moves; its cases are the same
    again, _ = _loop(tmp_path, domain=domain)
    got = again.enrol("Ada")
    assert _code(got) == "G6_E_LOOP_SPLIT_REUSED" and got["completion_state"] == "blocked-escalated"
    assert _code(again.tick()) == "G6_E_LOOP_FROZEN_CHANGED"


def test_a_truly_fresh_split_resets_the_count_and_the_head_carries_over(tmp_path):
    loop, domain = _enrolled(tmp_path)
    assert loop.tick()["outcome"] == "adopted"
    head, spent = loop.mh._head(), _in_held_out(loop)
    domain.pool[:] = [c for c in domain.pool if c.case_id not in spent] + _fresh_cases(5000, 240)
    again, _ = _loop(tmp_path, domain=domain)
    assert again.enrol("Ada")["completion_state"] == "qualified-draft"
    assert again.mh.held_out_left() == again.workflow.policy.max_evaluations
    assert again.mh._head() == head and not spent & _in_held_out(again)


def test_a_new_seed_cannot_move_cases_proposers_saw_into_the_held_out_split(tmp_path):
    loop, domain = _enrolled(tmp_path)
    moved = dataclasses.replace(SETTINGS, seed=SETTINGS.seed + 1)
    again, _ = _loop(tmp_path, domain=domain, settings=moved)
    assert _code(again.enrol("Ada")) == "G6_E_LOOP_SPLIT_REUSED"


def test_a_judge_taken_away_after_enrolment_stops_the_loop(tmp_path):
    loop, domain = _enrolled(tmp_path)
    domain._judged = False
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_FROZEN_CHANGED" and "evaluator" in got["message"]
    again, _ = _loop(tmp_path, domain=domain)
    assert _code(again.tick()) == "G6_E_LOOP_NO_INDEPENDENT_JUDGE"


def test_the_gate_runs_at_the_designs_numbers_and_no_setting_moves_them(tmp_path):
    loop, _ = _enrolled(tmp_path)
    policy = loop.workflow.policy
    assert (policy.alpha, policy.max_evaluations, policy.threshold) == (0.05, 5, 0.01)
    assert (policy.min_cases, policy.min_discordant) == (30, 5)
    assert not {"alpha", "max_evaluations", "min_cases", "min_discordant"} & set(LoopSettings.__dataclass_fields__)


def test_the_worst_case_counts_every_proposer_the_method_asks_for(tmp_path):
    more = Method(cycles=1, propose_budget=120, select_budget=400, race_block=12, reflect_calls=1, use_edits=True)
    loop, _ = _enrolled(tmp_path, settings=dataclasses.replace(SETTINGS, method=more))
    fewer, _ = _enrolled(tmp_path / "b", settings=SETTINGS)
    assert loop.worst_case() > fewer.worst_case()

# The envelope is frozen too ------------------------------------------------------------------------------------------

def test_a_change_to_the_loops_own_envelope_code_stops_the_loop(tmp_path, monkeypatch):
    loop, _ = _enrolled(tmp_path)
    monkeypatch.setattr("g6.metaharness.loop.envelope_sha256", lambda: "changed")
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_FROZEN_CHANGED" and "envelope" in got["message"]


def test_the_envelope_digest_covers_each_file_of_the_loop():
    found = build_manifest(Path(g6.__file__).resolve().parent, patterns=ENVELOPE_PATTERNS)
    assert len(found) == len(ENVELOPE_PATTERNS) == 5