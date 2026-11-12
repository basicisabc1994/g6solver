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
from g6.core.ledger import LedgerError
from g6.core.llm import FakeLLM, LLMError
from g6.core.vault import AccessDenied
from g6.harness import Check
from g6.integrity import build_manifest
from g6.metaharness import Model, Profile, SuiteCase
from g6.metaharness import monitor as drift
from g6.metaharness.domain import Judge
from g6.metaharness.loop import ENVELOPE_PATTERNS, LoopSettings, ShadowLoop, _Counted, _Requests
from g6.metaharness.method import Method
from g6.metaharness.propose import DevBudget
from g6.metaharness.select import race, race_calls
from g6.self_maintenance.envelope import heartbeat
from g6.self_maintenance.pacing import AccountReading, ProviderWindow
from g6.self_maintenance.usage import REFRESH
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


def test_a_tick_is_billed_to_the_provider_its_calls_go_to_whatever_the_first_window_holds(tmp_path):
    settings = dataclasses.replace(_windows(("claude", 100), ("codex", 1_000_000)), provider="codex")
    loop, _ = _enrolled(tmp_path, settings=settings)
    got = loop.tick()
    assert got["outcome"] == "adopted" and got["budget"]["provider"] == "codex"


def _codex_is_full(provider: str, week_start: datetime) -> AccountReading | None:
    return AccountReading(used=1_000_000, limited_until=START + timedelta(days=7)) if provider == "codex" else None


def _routed_to_codex(tmp_path):
    domain = domains.WordProblems(tmp_path)
    run = domain.execution()
    domain.execution = lambda: dataclasses.replace(run, models={"codex": Model(domain.llm)},
                                                   initial={"routing": {"answer": "codex"}, "prompt": domains.TERSE})
    return domain


def test_another_providers_allowance_cannot_pay_for_calls_when_nothing_says_where_they_go(tmp_path):
    domain = _routed_to_codex(tmp_path)
    alone, _ = _enrolled(tmp_path, domain=domain, settings=_windows(("codex", 1_000_000)), read_account=_codex_is_full)
    assert _code(alone.tick()) == "G6_E_LOOP_BUDGET_WAIT" and domain.llm.calls == []
    both, _ = _loop(tmp_path, domain=domain, settings=_windows(("codex", 1_000_000), ("claude", 1_000_000)),
                    read_account=_codex_is_full)
    got = both.tick()
    assert _code(got) == "G6_E_LOOP_NO_BUDGET" and "codex, claude" in got["message"] and domain.llm.calls == []


def test_a_stated_provider_at_its_limit_waits_however_much_another_window_holds(tmp_path):
    domain = _routed_to_codex(tmp_path)
    settings = dataclasses.replace(_windows(("claude", 1_000_000), ("codex", 1_000_000)), provider="codex")
    loop, _ = _enrolled(tmp_path, domain=domain, settings=settings, read_account=_codex_is_full)
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_BUDGET_WAIT" and got["wait_until"] == (START + timedelta(days=7)).isoformat()
    assert domain.llm.calls == []


def test_a_provider_with_no_window_runs_nothing(tmp_path):
    settings = dataclasses.replace(_windows(("claude", 1_000_000)), provider="codex")
    loop, domain = _loop(tmp_path, settings=settings)
    assert _code(loop.enrol("Ada")) == "G6_E_LOOP_NO_BUDGET"
    assert _code(loop.tick()) == "G6_E_LOOP_NO_BUDGET" and domain.llm.calls == []


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


def _breaks(domain) -> None:
    def broken(view):
        raise RuntimeError("the domain fell over")

    domain.failures = broken


def test_three_failed_cycles_in_a_row_pause_the_loop_with_a_stated_reason(tmp_path):
    loop, domain = _enrolled(tmp_path)
    _breaks(domain)
    ends = [loop.tick()["outcome"] for _ in range(3)]
    why = loop.log.paused()
    assert "three failed cycles" in why and all(end in why for end in ends)
    calls = len(domain.llm.calls)
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_PAUSED" and why in got["message"] and len(domain.llm.calls) == calls
    assert len(loop.log.cycles()) == 3 and heartbeat(loop.root)["code"] == "G6_E_LOOP_PAUSED"


def test_two_failed_cycles_do_not_pause_it(tmp_path):
    loop, domain = _enrolled(tmp_path)
    _breaks(domain)
    for _ in range(2):
        loop.tick()
    assert loop.log.paused() == ""


def test_a_pause_outlives_the_process(tmp_path):
    loop, domain = _enrolled(tmp_path)
    _breaks(domain)
    for _ in range(3):
        loop.tick()
    again, _ = _loop(tmp_path, domain=domain)
    assert _code(again.tick()) == "G6_E_LOOP_PAUSED"


def test_only_a_person_lifts_the_pause_and_then_it_runs(tmp_path):
    loop, domain = _enrolled(tmp_path)
    _breaks(domain)
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


def test_a_candidate_that_lost_is_a_result_so_the_loop_never_pauses_on_it(tmp_path):
    loop, _ = _enrolled(tmp_path, proposer=domains.proposes(NOTHING_HELPS))
    ends = [loop.tick() for _ in range(4)]
    assert {got["outcome"] for got in ends} <= {"no_winner", "refused"}
    assert all(got["completion_state"] == "qualified-draft" for got in ends)
    assert loop.log.paused() == "" and loop.log.failed_streak() == 0


def test_no_constraint_is_a_result_so_the_loop_never_pauses_on_it(tmp_path):
    domain = domains.WordProblems(tmp_path)
    real = domain.failures
    domain.failures = lambda view: tuple(dataclasses.replace(r, changeable=False) for r in real(view))
    loop, _ = _enrolled(tmp_path, domain=domain)
    assert [loop.tick()["outcome"] for _ in range(4)] == ["no_constraint"] * 4
    assert loop.log.paused() == "" and loop.log.quiet_streak() == 4


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
    assert got["outcome"] == "rolled_back" and got["undoes"] == 1 and got["completion_state"] == "qualified-draft"
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


def test_adopting_and_rolling_back_in_turn_is_bounded_by_the_held_out_split_and_never_pauses(tmp_path):
    domain = domains.WordProblems(tmp_path, weak=0.6)
    tries = iter(range(99))

    def always_something_new(system: str, user: str) -> str:
        return json.dumps({"prompt": f"{domains.WORKING} Variant {next(tries)}."})

    loop, _ = _enrolled(tmp_path, domain=domain, proposer=FakeLLM(always_something_new))
    for _ in range(loop.workflow.policy.max_evaluations - 1):
        domain.tutor.collapsed = False
        assert loop.tick()["outcome"] == "adopted"
        domain.tutor.collapsed = True
        assert loop.tick()["outcome"] == "rolled_back"
    assert loop.log.paused() == "" and loop.log.failed_streak() == 0
    domain.tutor.collapsed = False
    assert loop.tick()["outcome"] == "adopted"                     # the fifth and last use of this split
    assert _code(loop.tick()) == "G6_E_LOOP_HELD_OUT_SPENT"        # each try used a held-out evaluation: a person's turn


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


def _with_answer(domain, case_id: str, answer: str) -> None:
    domain.pool[:] = [dataclasses.replace(c, expected=answer) if c.case_id == case_id else c for c in domain.pool]


def test_enrolling_again_on_an_open_handle_shows_proposers_the_cases_it_froze(tmp_path):
    loop, domain = _enrolled(tmp_path)
    assert loop.mh.measure(budget=120)["view"].dev
    changed = loop.cut.tune[0].case_id
    _with_answer(domain, changed, "999999")
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    assert loop._changed() == []
    shown = loop.mh.measure(budget=120)["view"].dev
    assert next(case["expected"] for case in shown if case["case_id"] == changed) == "999999"


def test_enrolling_again_on_an_open_handle_runs_the_split_it_recorded(tmp_path):
    loop, domain = _enrolled(tmp_path)
    assert loop.tick()["outcome"] == "adopted"
    spent = _in_held_out(loop)
    domain.pool[:] = [c for c in domain.pool if c.case_id not in spent] + _fresh_cases(5000, 240)
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    row, fresh = loop.log.enrolled(), _loop(tmp_path, domain=domain)[0]
    assert row["held_out_digest"] == loop.workflow.suite.digest("held_out") == fresh.workflow.suite.digest("held_out")
    assert row["held_out"] == sorted(_in_held_out(fresh)) and not spent & _in_held_out(loop)
    assert loop.mh.held_out_left() == loop.workflow.policy.max_evaluations
    assert _code(loop.tick()) != "G6_E_LOOP_FROZEN_CHANGED"


def test_a_spent_split_is_seen_on_an_open_handle_as_on_a_fresh_one(tmp_path):
    loop, domain = _enrolled(tmp_path)
    assert loop.tick()["outcome"] == "adopted"
    domain.pool.extend(_fresh_cases(5000, 40))
    got = loop.enrol("Ada")
    assert _code(got) == "G6_E_LOOP_SPLIT_REUSED" and _code(loop.tick()) == "G6_E_LOOP_FROZEN_CHANGED"


def test_cases_that_no_longer_fill_the_splits_refuse_enrolling_again_and_write_nothing(tmp_path):
    loop, domain = _enrolled(tmp_path)
    rows = len(loop.log.enrolments())
    domain.pool[:] = domain.pool[:10]
    assert _code(loop.enrol("Ada")) == "G6_E_LOOP_POOL_TOO_SMALL" and len(loop.log.enrolments()) == rows
    assert _code(loop.tick()) == "G6_E_LOOP_POOL_TOO_SMALL"


def test_enrolling_again_keeps_what_the_handle_counts_with(tmp_path):
    loop, domain = _enrolled(tmp_path)
    Limited(domain, lambda problem: True)
    loop.enrol("Ada")
    assert _code(loop.tick()) == "G6_E_LOOP_BUDGET_WAIT"             # it went through this handle's count of requests


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

# An unchanged head is not measured again ------------------------------------------------------------------------------

def test_an_unchanged_head_costs_nothing_to_measure_tick_after_tick(tmp_path):
    loop, domain = _enrolled(tmp_path, domain=domains.WordProblems(tmp_path, weak=1.0))
    assert loop.tick()["outcome"] == "idle"
    calls = len(domain.llm.calls)
    assert calls == len(loop.cut.tune)
    got = loop.tick()
    assert got["outcome"] == "idle" and len(domain.llm.calls) == calls and got["budget"]["charged"] == 0


def test_a_cycles_proposers_are_shown_the_view_already_measured(tmp_path):
    method = Method(cycles=2, propose_budget=120, select_budget=400, race_block=12, reflect_calls=1, use_recovery=False,
                    use_reflection=False)
    loop, domain = _enrolled(tmp_path, settings=dataclasses.replace(SETTINGS, method=method))
    loop.tick()
    tuning = {c.input for c in loop.cut.tune}
    assert sum(user.rsplit("Problem: ", 1)[-1].strip() in tuning for _, user in domain.llm.calls) == len(tuning)


def test_a_new_head_is_measured_once(tmp_path):
    loop, domain = _enrolled(tmp_path)
    assert loop.tick()["outcome"] == "adopted"
    calls = len(domain.llm.calls)
    view = loop.mh.measure(budget=120)["view"]
    assert len(domain.llm.calls) == calls + len(loop.cut.tune) and domains.WORKING in view.base["prompt"]
    assert loop.mh.measure(budget=120)["view"] is view and len(domain.llm.calls) == calls + len(loop.cut.tune)


def test_the_same_head_on_other_cases_is_measured_anew(tmp_path):
    loop, domain = _enrolled(tmp_path)
    loop.mh.measure(budget=120)
    calls, fewer = len(domain.llm.calls), loop.cut.tune[1:]
    loop.mh.workflow = dataclasses.replace(loop.mh.workflow, suite=dataclasses.replace(loop.mh.workflow.suite, dev=fewer))
    assert len(loop.mh._view(DevBudget(120)).dev) == len(fewer) and len(domain.llm.calls) == calls + len(fewer)


def test_a_view_short_of_its_budget_is_kept_for_that_budget_only(tmp_path):
    loop, _ = _enrolled(tmp_path)
    short = loop.mh.measure(budget=5)["view"]
    assert len(short.ran) == 5 and loop.mh.measure(budget=5)["view"] is short
    assert len(loop.mh.measure(budget=120)["view"].ran) == len(loop.cut.tune)


def test_a_run_the_model_failed_is_not_kept_as_what_the_head_does(tmp_path):
    domain = domains.WordProblems(tmp_path, weak=1.0)
    tutor, down = domain.tutor, [True]

    def until_it_is_back(system: str, user: str) -> str:
        if down[0]:
            raise LLMError("G6_E_LLM_TIMEOUT: no answer in time")
        return tutor(system, user)

    domain.llm._responder = until_it_is_back
    loop, _ = _enrolled(tmp_path, domain=domain)
    assert loop.mh.measure(budget=120)["view"].failures
    down[0] = False
    assert loop.mh.measure(budget=120)["view"].failures == ()


def test_a_failed_view_is_read_by_the_cycles_proposers_and_not_run_again_for_each(tmp_path):
    domain = domains.WordProblems(tmp_path)

    def down(system: str, user: str) -> str:
        raise LLMError("G6_E_LLM_TIMEOUT: no answer in time")

    domain.llm._responder = down
    loop, _ = _enrolled(tmp_path, domain=domain, settings=dataclasses.replace(SETTINGS, audit_per_tick=0))
    assert loop.mh.measure(budget=120)["view"].failures
    calls = len(domain.llm.calls)
    assert loop.mh._view(DevBudget(120)).failures and len(domain.llm.calls) == calls


def test_a_view_cut_off_at_what_a_tick_reserved_is_not_kept(tmp_path, monkeypatch):
    loop, _ = _enrolled(tmp_path, domain=domains.WordProblems(tmp_path, weak=1.0))
    with monkeypatch.context() as cut_short:
        cut_short.setattr(loop, "worst_case", lambda: 5)
        assert _code(loop.tick()) == "G6_E_LOOP_TICK_BUDGET_SPENT"
    assert loop.tick()["outcome"] == "idle"

# What a tick reserves, at the design's example allowance ---------------------------------------------------------------
EXAMPLE = LoopSettings(windows=(ProviderWindow("claude", 5000, START),))        # 20% kept back, and Method's defaults
DAY_ONE, DAY_TWO, DAY_THREE = START + timedelta(hours=1), START + timedelta(days=1, hours=1), START + timedelta(days=2, hours=1)


def _one_call(tmp_path, *, pool=86):
    """A domain whose every run is one model call, which is all it declares."""
    domain = domains.WordProblems(tmp_path, pool=pool)
    run = domain.execution()
    domain.execution = lambda: dataclasses.replace(run, models={"local": Model(domain.llm, max_calls_per_run=1)})
    return domain


def test_a_one_call_domain_on_a_small_pool_is_admitted_on_day_one_at_the_example_allowance(tmp_path):
    loop, _ = _enrolled(tmp_path, domain=_one_call(tmp_path), settings=EXAMPLE, now=lambda: DAY_ONE)
    assert loop.tick()["outcome"] == "adopted"


def test_a_one_call_domain_on_a_larger_pool_is_admitted_on_day_three_at_the_example_allowance(tmp_path):
    """A race of ties runs every candidate on every case, so a tick's worst case here is over day two's ceiling of 2000."""
    days = [DAY_ONE]
    loop, _ = _enrolled(tmp_path, domain=_one_call(tmp_path, pool=240), settings=EXAMPLE, now=lambda: days[0])
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_BUDGET_WAIT" and "day 1's ceiling of 1000" in got["message"]
    days[0] = DAY_THREE
    assert loop.tick()["outcome"] == "adopted"


def test_the_one_call_executor_is_not_held_to_the_cap_a_model_declares_by_default(tmp_path):
    """``Model`` caps a run at 8 calls, and a run of G6's own executor is one call and a retry of an empty answer."""
    domain = domains.WordProblems(tmp_path, pool=86)
    loop, _ = _enrolled(tmp_path, domain=domain, settings=EXAMPLE, now=lambda: DAY_TWO)
    assert domain.execution().models["local"].max_calls_per_run == 8 and loop._calls_per_run() == 2
    assert loop.tick()["outcome"] == "adopted"


def test_a_real_week_at_the_example_allowance_runs_tick_after_tick_and_stays_under_the_ceiling(tmp_path):
    days = [START]
    domain = _one_call(tmp_path)
    proposer = FakeLLM(lambda system, user: json.dumps({"prompt": f"{domains.WORKING} Try {len(proposer.calls)}."}))
    loop, _ = _enrolled(tmp_path, domain=domain, settings=EXAMPLE, proposer=proposer, now=lambda: days[0])
    for hour in range(0, 7 * 24, 6):
        days[0] = START + timedelta(hours=hour, minutes=1)
        assert _code(loop.tick()) != "G6_E_LOOP_TICK_BUDGET_SPENT"
        seen = loop.meter.reading("claude", days[0])
        assert seen.used <= seen.ceiling
    assert seen.used >= 3000 and len(loop.log.cycles()) >= 7


def test_the_evaluation_is_sized_by_the_cases_it_has_not_by_what_it_may_call(tmp_path):
    def worst(folder, **changes) -> int:
        loop, _ = _loop(tmp_path / folder, domain=_one_call(tmp_path / folder), settings=dataclasses.replace(EXAMPLE, **changes))
        return loop.worst_case()

    assert worst("a", eval_calls=2000) == worst("b", eval_calls=9000)    # 30 cases cannot call 2000 times, whatever is allowed
    assert worst("c", eval_calls=20) < worst("a", eval_calls=2000)       # a smaller limit still lowers it


def _every_call(ctx):
    """A domain's own executor that spends all its run may: what a one-call rule cannot see."""

    def execute(contract):
        caller = ctx.caller("answer")
        prompt = ctx.profile.prompt or ""
        return [caller.complete(prompt, contract.task) for _ in range(caller.budget.max_model_calls)][-1]

    return execute


def test_a_larger_pool_reserves_more_and_so_does_a_factory_of_the_domains_own(tmp_path):
    def worst(folder, domain) -> int:
        return _loop(tmp_path / folder, domain=domain, settings=EXAMPLE)[0].worst_case()

    small = worst("a", domains.WordProblems(tmp_path / "a", pool=86))
    assert small < worst("b", domains.WordProblems(tmp_path / "b", pool=240))
    own = domains.WordProblems(tmp_path / "c", pool=86)
    run = own.execution()
    own.execution = lambda: dataclasses.replace(run, factory=_every_call)
    assert small < worst("c", own)


@pytest.mark.parametrize(("per_run", "pool", "allowance"), [(1, 86, 5000), (3, 86, 50_000), (8, 120, 200_000)])
def test_no_tick_spends_past_what_it_reserved_when_all_it_may_do_is_done(tmp_path, per_run, pool, allowance):
    domain = domains.WordProblems(tmp_path, pool=pool)
    run = domain.execution()
    domain.execution = lambda: dataclasses.replace(run, factory=_every_call, models={
        "local": Model(domain.llm, max_calls_per_run=per_run)})
    proposer = FakeLLM(lambda system, user: json.dumps({"prompt": f"{domains.WORKING} Try {len(proposer.calls)}."}))
    settings = LoopSettings(windows=(ProviderWindow("claude", allowance, START),))
    loop, _ = _enrolled(tmp_path, domain=domain, settings=settings, proposer=proposer, now=lambda: START + timedelta(days=6, hours=1))
    for _ in range(4):
        worst = loop.worst_case()
        got = loop.tick()
        assert _code(got) != "G6_E_LOOP_TICK_BUDGET_SPENT" and got["budget"]["charged"] <= worst, got["message"]


def test_a_caller_that_retries_past_the_estimate_still_stops_at_what_was_reserved(tmp_path):
    domain = _one_call(tmp_path)
    class RetriesBlanks(FakeLLM):
        def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
            for _ in range(4):
                text = super().complete(system, user, max_tokens=max_tokens)
                if text.strip():
                    return text
            raise LLMError("empty completion on 4 attempts")

    backend = RetriesBlanks(default="")
    run = domain.execution()
    domain.execution = lambda: dataclasses.replace(run, models={"local": Model(backend, max_calls_per_run=1)})
    proposer = FakeLLM(lambda system, user: json.dumps({"prompt": f"{domains.WORKING} Try {len(proposer.calls)}."}))
    loop, _ = _enrolled(tmp_path, domain=domain, settings=EXAMPLE, proposer=proposer, now=lambda: DAY_TWO)
    worst = loop.worst_case()
    got = loop.tick()
    assert got["budget"]["charged"] <= worst and len(backend.calls) + len(proposer.calls) <= worst


def test_a_race_never_takes_more_runs_than_its_bound(tmp_path):
    loop, domain = _enrolled(tmp_path, domain=_one_call(tmp_path), settings=EXAMPLE)
    base = loop.mh._head()[1]
    candidates = [base.model_copy(update={"prompt": f"{domains.WORKING} {i}"}) for i in range(7)]
    ran = race(loop.workflow, loop.mh.factory, base, candidates, block=10, keep=0.5, budget=10 ** 6, clock=domains.Clock())
    assert ran.calls_used <= race_calls(len(loop.cut.selection), len(candidates))

# A provider's limit is a wait --------------------------------------------------------------------------------------------
AT_A_LIMIT = ("G6_E_LLM_HOST_FAILED: claude exited 1 and gave an error result: "
              '{"api_error_status":429,"result":"You\'ve hit your usage limit"}')
TWO_OFFERS = ({"prompt": domains.WORKING}, {"prompt": f"{domains.WORKING} Show each step."})


class Limited:
    """The domain's model, which answers until ``reached(problem)`` and refuses at its limit from then until ``lifted``."""

    def __init__(self, domain, reached) -> None:
        self.tutor, self.reached, self.refused, self.lifted = domain.tutor, reached, 0, False
        domain.llm._responder = self

    def __call__(self, system: str, user: str) -> str:
        if not self.lifted and self.reached(user.rsplit("Problem: ", 1)[-1].strip()):
            self.refused += 1
            raise LLMError(AT_A_LIMIT)
        return self.tutor(system, user)


def _nothing_counted(loop) -> None:
    assert loop.log.cycles() == [] and loop.log.failed_streak() == 0 and loop.log.paused() == ""


def test_a_limit_ends_the_tick_at_once_as_a_budget_wait_and_scores_nothing(tmp_path):
    loop, domain = _enrolled(tmp_path)
    limited, head = Limited(domain, lambda problem: True), loop.mh._head()
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_BUDGET_WAIT" and got["completion_state"] == "blocked-escalated"
    assert got["wait_until"] == (NOW + REFRESH).isoformat() and "hit your usage limit" in got["message"]
    assert limited.refused == 1 and loop.mh._head() == head and "outcome" not in got
    _nothing_counted(loop)


def _reopened(tmp_path, domain, now=lambda: NOW):
    return ShadowLoop(domain, tmp_path / "loop", SETTINGS, proposer=domains.proposes({"prompt": domains.WORKING}), now=now,
                      clock=domains.Clock())


def test_a_restart_before_a_limit_ends_sends_nothing_until_it_does(tmp_path):
    loop, domain = _enrolled(tmp_path)
    limited = Limited(domain, lambda problem: True)
    until = loop.tick()["wait_until"]
    sent, refused = len(domain.llm.calls), limited.refused
    again = _reopened(tmp_path, domain)
    got = again.tick()
    assert _code(got) == "G6_E_LOOP_BUDGET_WAIT" and got["wait_until"] == until
    assert len(domain.llm.calls) == sent and limited.refused == refused
    _nothing_counted(again)


def test_a_restart_after_a_limit_ended_runs(tmp_path):
    loop, domain = _enrolled(tmp_path)
    limited = Limited(domain, lambda problem: True)
    until = datetime.fromisoformat(loop.tick()["wait_until"])
    limited.lifted = True
    got = _reopened(tmp_path, domain, now=lambda: until).tick()
    assert _code(got) != "G6_E_LOOP_BUDGET_WAIT" and len(loop.log.cycles()) == 0 and got["outcome"]


def test_the_latest_limit_of_a_provider_is_the_one_a_restart_holds(tmp_path):
    loop, domain = _enrolled(tmp_path)
    loop.log.limit("claude", (NOW + timedelta(hours=5)).isoformat())
    loop.log.limit("claude", (NOW + timedelta(hours=2)).isoformat())
    assert _reopened(tmp_path, domain).tick()["wait_until"] == (NOW + timedelta(hours=2)).isoformat()


def test_a_limit_the_records_would_not_take_is_still_waited_out_by_this_process(tmp_path, monkeypatch):
    loop, domain = _enrolled(tmp_path)
    Limited(domain, lambda problem: True)

    def refuses(*args):
        raise PermissionError("held open")

    monkeypatch.setattr(loop.log, "limit", refuses)
    until = loop.tick()["wait_until"]
    sent = len(domain.llm.calls)
    assert loop.tick()["wait_until"] == until and len(domain.llm.calls) == sent


@pytest.mark.parametrize("fields", [{}, {"provider": "claude"}, {"provider": "claude", "until": "soon"},
                                    {"provider": "claude", "until": "2026-10-28T08:00:00"}, {"until": NOW.isoformat()}])
def test_a_limit_row_with_no_provider_or_no_time_on_a_clock_is_refused(tmp_path, fields):
    loop, _ = _enrolled(tmp_path)
    with pytest.raises(LedgerError, match="limited"):
        loop.log._append("limited", fields)


def test_what_a_tick_sent_before_a_limit_is_charged_and_the_rest_of_its_reservation_is_given_back(tmp_path):
    loop, domain = _enrolled(tmp_path)
    late = {c.input for c in loop.cut.tune[10:]}
    Limited(domain, late.__contains__)
    calls = len(domain.llm.calls)
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_BUDGET_WAIT" and got["charged"] == len(domain.llm.calls) - calls
    assert loop.meter.reading("claude", NOW).used == got["charged"] < loop.worst_case()


def test_a_limit_during_the_evaluation_spends_none_of_the_held_out_split(tmp_path):
    loop, domain = _enrolled(tmp_path, proposer=domains.proposes(*TWO_OFFERS))
    held, left = {c.input for c in loop.cut.held_out[5:]}, loop.mh.held_out_left()
    limited = Limited(domain, held.__contains__)
    head = loop.mh._head()
    assert _code(loop.tick()) == "G6_E_LOOP_BUDGET_WAIT" and limited.refused == 1
    assert loop.mh.held_out_left() == left and loop.mh._head() == head
    assert not any(loop.mh.ledger.rows(kind) for kind in ("evaluation", "report", "decision", "tuning_run"))
    _nothing_counted(loop)


def test_the_next_tick_waits_for_the_reset_spending_nothing_and_then_runs(tmp_path):
    days = [NOW]
    loop, domain = _enrolled(tmp_path, now=lambda: days[0], proposer=domains.proposes(*TWO_OFFERS))
    limited = Limited(domain, lambda problem: True)
    reset = datetime.fromisoformat(loop.tick()["wait_until"])
    calls = len(domain.llm.calls)
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_BUDGET_WAIT" and got["wait_until"] == reset.isoformat() and len(domain.llm.calls) == calls
    days[0], limited.lifted = reset, True
    assert loop.tick()["outcome"] == "adopted"


def test_limits_in_a_row_never_pause_the_loop(tmp_path):
    days = [NOW]
    loop, domain = _enrolled(tmp_path, now=lambda: days[0])
    Limited(domain, lambda problem: True)
    for _ in range(5):
        got = loop.tick()
        assert _code(got) == "G6_E_LOOP_BUDGET_WAIT"
        days[0] = datetime.fromisoformat(got["wait_until"])
    _nothing_counted(loop)


def test_a_limit_a_proposer_meets_ends_the_tick_as_well(tmp_path):
    def refuses(system: str, user: str) -> str:
        raise LLMError(AT_A_LIMIT)

    loop, domain = _enrolled(tmp_path, proposer=FakeLLM(refuses))
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_BUDGET_WAIT" and got["wait_until"] == (NOW + REFRESH).isoformat()
    _nothing_counted(loop)


def test_a_wait_a_server_asked_for_is_that_long(tmp_path):
    loop, domain = _enrolled(tmp_path)

    def refuses(system: str, user: str) -> str:
        raise LLMError("G6_E_LLM_RATE_LIMITED: HTTP 429 from https://model asks for a wait of 120 s; with 0 s waited already")

    domain.llm._responder = refuses
    assert loop.tick()["wait_until"] == (NOW + timedelta(seconds=120)).isoformat()


def test_a_failure_that_is_no_limit_is_still_a_failed_case_and_the_tick_runs_on(tmp_path):
    loop, domain = _enrolled(tmp_path)

    def down(system: str, user: str) -> str:
        raise LLMError("G6_E_LLM_TIMEOUT: no answer in time")

    domain.llm._responder = down
    got = loop.tick()
    assert _code(got) != "G6_E_LOOP_BUDGET_WAIT" and len(domain.llm.calls) > 1 and len(loop.log.cycles()) == 1


def test_a_server_error_that_names_a_limit_in_its_body_is_a_failed_case_not_a_wait(tmp_path):
    loop, domain = _enrolled(tmp_path)

    def broken(system: str, user: str) -> str:
        raise LLMError("HTTP 500 from https://model: ImportError: cannot import name 'RateLimitError' from 'openai'")

    domain.llm._responder = broken
    assert _code(loop.tick()) != "G6_E_LOOP_BUDGET_WAIT" and len(loop.log.cycles()) == 1


CODEX_AT_A_LIMIT = "G6_E_LLM_HOST_FAILED: codex exited 1: ERROR: You've hit your usage limit."


def test_the_limit_of_this_a_limit_codex_only_printed_is_a_cycle_with_no_winner_and_pauses_nothing(tmp_path):
    """Nothing Codex prints as text is read, so its account reading is what holds the next tick. Until then this is the record.
    The Codex the loop sends through prints its events as JSON, and a limit there is a wait (``test_provider_stops.py``)."""
    loop, domain = _enrolled(tmp_path)

    def at_a_limit(system: str, user: str) -> str:
        raise LLMError(CODEX_AT_A_LIMIT)

    domain.llm._responder = at_a_limit
    assert loop.tick()["outcome"] == "no_winner" and loop.log.failed_streak() == 0 and loop.log.paused() == ""


def test_the_limit_of_this_a_limit_codex_only_printed_during_an_evaluation_spends_one_of_the_five(tmp_path):
    """What not reading its words costs: a refusal that says nothing true of the candidate, on a spent evaluation. A limit
    that reads as one spends none (``test_a_limit_during_the_evaluation_spends_none_of_the_held_out_split``)."""
    loop, domain = _enrolled(tmp_path, proposer=domains.proposes(*TWO_OFFERS))
    held, left, tutor = {c.input for c in loop.cut.held_out[5:]}, loop.mh.held_out_left(), domain.tutor

    def at_a_limit_on_the_held_out_cases(system: str, user: str) -> str:
        if user.rsplit("Problem: ", 1)[-1].strip() in held:
            raise LLMError(CODEX_AT_A_LIMIT)
        return tutor(system, user)

    domain.llm._responder = at_a_limit_on_the_held_out_cases
    assert loop.tick()["outcome"] == "refused" and loop.mh.held_out_left() == left - 1


def test_a_model_writing_of_a_limit_in_a_lost_reply_is_a_failed_case_not_a_wait(tmp_path):
    loop, domain = _enrolled(tmp_path)

    def lost(system: str, user: str) -> str:
        raise LLMError("G6_E_LLM_HOST_FAILED: codex exited 0 but wrote no last message: assistant: You've hit your usage limit")

    domain.llm._responder = lost
    assert _code(loop.tick()) != "G6_E_LOOP_BUDGET_WAIT" and len(loop.log.cycles()) == 1


def test_a_sealed_breach_is_recorded_even_when_a_limit_ended_the_tick(tmp_path):
    loop, domain = _enrolled(tmp_path)
    real, sealed = domain.failures, domain.sealed_file

    def looks_at_the_sealed_split(view):
        try:
            sealed.read_text(encoding="utf-8")
        except AccessDenied:
            pass
        return real(view)

    def refuses(system: str, user: str) -> str:
        raise LLMError(AT_A_LIMIT)

    domain.failures = looks_at_the_sealed_split
    loop._proposer.inner = FakeLLM(refuses)
    got = loop.tick()
    assert got["outcome"] == "sealed_breach" and _code(got) == "G6_E_LOOP_SEALED_BREACH"


def _cut_off_at_the_evaluation(tmp_path, days, proposer):
    """A loop whose first tick met a limit in the held-out evaluation, with its race won. Lifted, it is past the reset."""
    loop, domain = _enrolled(tmp_path, proposer=proposer, now=lambda: days[0])
    held = {c.input for c in loop.cut.held_out[5:]}
    limited = Limited(domain, held.__contains__)
    days[0] = datetime.fromisoformat(loop.tick()["wait_until"])
    limited.lifted = True
    return loop, domain


def test_the_candidate_a_limit_cut_off_is_finished_after_the_reset_and_adopted(tmp_path):
    days = [NOW]
    loop, _ = _cut_off_at_the_evaluation(tmp_path, days, domains.proposes(*TWO_OFFERS))
    won = next(row["winner"] for row in loop.mh.ledger.rows("selection"))
    got = loop.tick()
    assert got["outcome"] == "adopted" and got["champion"] == won
    assert loop.mh.ledger.rows("evaluation") and len(loop.mh.ledger.rows("tuning_run")) == 1


def test_a_restart_during_the_wait_loses_no_candidate(tmp_path):
    days = [NOW]
    proposer = domains.proposes(*TWO_OFFERS)
    first, domain = _cut_off_at_the_evaluation(tmp_path, days, proposer)
    again = ShadowLoop(domain, tmp_path / "loop", SETTINGS, proposer=proposer, now=lambda: days[0], clock=domains.Clock())
    assert again.tick()["outcome"] == "adopted"


def test_only_what_is_unfinished_comes_back(tmp_path):
    """The race's loser has its result, and a tick that finished leaves nothing behind."""
    days = [NOW]
    loop, _ = _cut_off_at_the_evaluation(tmp_path, days, domains.proposes(*TWO_OFFERS))
    won = next(row["winner"] for row in loop.mh.ledger.rows("selection"))
    assert [p.candidate_digest for p in loop.mh.unfinished()] == [won]
    assert loop.tick()["outcome"] == "adopted" and loop.mh.unfinished() == []


def test_candidates_a_limit_stopped_before_their_race_race_after_the_reset(tmp_path):
    days = [NOW]
    loop, domain = _enrolled(tmp_path, now=lambda: days[0], proposer=domains.proposes(*TWO_OFFERS))
    selection = {c.input for c in loop.cut.selection}
    limited = Limited(domain, selection.__contains__)
    days[0] = datetime.fromisoformat(loop.tick()["wait_until"])
    limited.lifted = True
    assert len(loop.mh.unfinished()) == 2 and loop.mh.ledger.rows("selection") == []
    assert loop.tick()["outcome"] == "adopted"


def test_what_came_back_from_a_limit_is_in_the_reservation(tmp_path):
    days = [NOW]
    uncapped = dataclasses.replace(SETTINGS, method=FAST.model_copy(update={"select_budget": 10 ** 6}))     # the cap would hide an entrant
    fresh, _ = _loop(tmp_path / "fresh", domain=domains.WordProblems(tmp_path / "fresh"), settings=uncapped)
    loop, domain = _enrolled(tmp_path, now=lambda: days[0], proposer=domains.proposes(*TWO_OFFERS), settings=uncapped)
    selection = {c.input for c in loop.cut.selection}
    Limited(domain, selection.__contains__)
    loop.tick()
    assert loop.worst_case() > fresh.worst_case()


def test_candidates_made_on_another_head_do_not_come_back(tmp_path):
    days = [NOW]
    loop, domain = _cut_off_at_the_evaluation(tmp_path, days, domains.proposes(*TWO_OFFERS))
    assert loop.mh.unfinished()
    other = loop.mh._head()[1].model_copy(update={"prompt": "another head"})
    loop.mh._head = lambda: (0, other)
    assert loop.mh.unfinished() == []


def test_outside_a_tick_a_limit_is_the_error_it_always_was():
    def refuses(system: str, user: str) -> str:
        raise LLMError(AT_A_LIMIT)

    with pytest.raises(LLMError, match="hit your usage limit"):
        _Counted(FakeLLM(refuses), _Requests()).complete("system", "user")

# The envelope is frozen too ------------------------------------------------------------------------------------------

def test_a_change_to_the_loops_own_envelope_code_stops_the_loop(tmp_path, monkeypatch):
    loop, _ = _enrolled(tmp_path)
    monkeypatch.setattr("g6.metaharness.loop.envelope_sha256", lambda: "changed")
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_FROZEN_CHANGED" and "envelope" in got["message"]


def test_the_envelope_digest_covers_each_file_of_the_loop():
    found = build_manifest(Path(g6.__file__).resolve().parent, patterns=ENVELOPE_PATTERNS)
    assert {"metaharness/domain.py", "metaharness/loop.py", "metaharness/shadow.py", "self_maintenance/envelope.py",
            "self_maintenance/pacing.py"} <= set(found)