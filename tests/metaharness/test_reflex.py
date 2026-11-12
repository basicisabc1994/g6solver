# SPDX-License-Identifier: Apache-2.0
"""The loop reading its own records: what limits progress, the work or the loop itself. Recorded rows and fake domains only."""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import g6
from g6.core.llm import FakeLLM
from g6.integrity import build_manifest, manifest_sha256
from g6.metaharness import loop as loop_module
from g6.metaharness import schema
from g6.metaharness.coding_replay import CodingReplay
from g6.metaharness.domain import Domain, FailureRecord
from g6.metaharness.lessons import Lesson
from g6.metaharness.loop import ENVELOPE_PATTERNS, LoopSettings, ShadowLoop
from g6.metaharness.method import Method
from g6.metaharness.reflex import STAGES, diagnose, funnel, lessons_from, lessons_in, read, refusals, seen_in, stages
from g6.metaharness.runner import Runner
from g6.self_maintenance.constraints import identify_constraint
from g6.self_maintenance.envelope import TICK_BUDGET_SPENT
from g6.self_maintenance.pacing import ProviderWindow

_spec = importlib.util.spec_from_file_location("loop_domains", Path(__file__).with_name("_loop_domains.py"))
domains = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(domains, "WordProblems"):
    _spec.loader.exec_module(domains)

START = datetime(2026, 10, 26, tzinfo=UTC)
NOW = START + timedelta(days=2)
FAST = Method(cycles=1, propose_budget=120, select_budget=400, race_block=12, reflect_calls=1)
SETTINGS = LoopSettings(windows=(ProviderWindow("claude", 1_000_000, START),), method=FAST, audit_per_tick=60)
WALL_CLOCK = "wall_clock_s: the upper bound on the increase 1.4 exceeds 5% of the base (0.1)"
BUDGET = {"provider": "claude", "day": 3, "charged": 10, "used": 10, "target": 100, "ceiling": 200, "estimated": True, "waited": 0}
AUDITED = {"audited": 10, "pairs": 10, "mean_difference": 0.0, "threshold": 0.1}
#: What three ticks did on ``_loop``'s default inputs on the commit before this step existed: read from a run of that commit, not from this code.
BEFORE = {"outcomes": ["adopted", "no_winner", "no_winner"], "head": "d9fe63586c692aeef4e627f61837b79f036c38e57628f6bef0dddd065f2fdd32",
          "champion": "6e59ecedf79e17f01c07b084636dd5d3b563c1c4db1754591ce3832a11be573c"}


def _loop(tmp_path, domain=None, proposer=None, **kw):
    domain = domain if domain is not None else domains.WordProblems(tmp_path)
    proposer = proposer if proposer is not None else domains.proposes({"prompt": domains.WORKING})
    return ShadowLoop(domain, tmp_path / "loop", SETTINGS, proposer=proposer, now=lambda: NOW, clock=domains.Clock(), **kw), domain


def _tuned(offered=2, new=2, repeated=0, measured=2, kept=2, raced=3):
    return {"offered": offered, "new": new, "repeated": repeated, "measured": measured, "kept": kept, "raced": raced}


def _cycle(outcome, **fields):
    return {"at": "2026-11-01T00:00:00+00:00", "domain": "d", "head_version": 0, "code": "G6_E_LOOP_CYCLE", "reason": "", "outcome": outcome,
            "budget": BUDGET, **fields}


def _objective_refused():
    """A cycle as our real stall wrote it: the gate refused its champion on one objective, on a row older than this step."""
    decision = {"verdict": "refuse", "code": schema.OBJECTIVE_REGRESSED, "reasons": [WALL_CLOCK]}
    return _cycle("refused", code=schema.OBJECTIVE_REGRESSED, reason=WALL_CLOCK, champion="c" * 8, decision=decision,
                  confirmation={"n": 30}, audit=AUDITED, constraint="wrong_answer")


def _adopted():
    decision = {"verdict": "recommend", "code": schema.RECOMMENDED, "reasons": ["beat the base"]}
    return _cycle("adopted", champion="c" * 8, decision=decision, tuning=_tuned(), audit=AUDITED, constraint="wrong_answer")


def _no_winner(**tuned):
    return _cycle("no_winner", champion=None, tuning=_tuned(**tuned), audit=AUDITED, constraint="wrong_answer")


def _measured(rows) -> dict[str, tuple[int, int]]:
    return {s.name: (s.succeeded, s.attempted) for s in stages(rows)}


# Stages ----------------------------------------------------------------------------------------------------------------
def test_the_stages_are_the_loops_own_in_serial_order():
    assert STAGES == ("budget", "refused", "proposed", "distinct", "tuning", "selection", "gate", "monitor")


def test_a_row_of_every_kind_reads_as_stages_identify_constraint_takes():
    rows = [_adopted(), _no_winner(), _objective_refused()]
    found = identify_constraint(stages(rows))
    assert found.bottleneck in STAGES and found.evidence == "min_effective_capacity_of_declared_serial_stages"
    assert all(s.independently_observed for s in stages(rows))     # the tick's own records, and no model's estimate


def test_each_stage_counts_the_cycles_that_reached_it_and_the_cycles_that_passed():
    assert _measured([_adopted(), _adopted(), _no_winner()]) == {
        "budget": (3, 3), "refused": (3, 3), "proposed": (3, 3), "distinct": (3, 3), "tuning": (3, 3), "selection": (2, 3),
        "gate": (2, 2), "monitor": (3, 3)}


def test_a_stage_with_no_record_is_unknown_and_never_a_failure():
    row = {"tick": 1, "outcome": "no_winner", "code": "G6_E_LOOP_CYCLE", "reason": "no candidate beat the head"}
    assert _measured([row]) == {"refused": (1, 1)}                 # a row from an older log says only that nothing refused it


def test_an_older_row_gives_no_tuning_stage_and_its_champion_still_counts():
    assert _measured([_objective_refused()]) == {"budget": (1, 1), "refused": (1, 1), "selection": (1, 1), "gate": (0, 1), "monitor": (1, 1)}


def test_a_proposer_that_offers_nothing_is_read_apart_from_one_that_repeats_itself():
    silent = _no_winner(offered=0, new=0, measured=0, kept=0, raced=0)
    repeating = _no_winner(offered=3, new=0, measured=0, kept=0, raced=0)
    assert _measured([silent])["proposed"] == (0, 1) and "distinct" not in _measured([silent])
    assert _measured([repeating])["proposed"] == (1, 1) and _measured([repeating])["distinct"] == (0, 1)


def test_a_repeat_the_pool_caught_is_a_repeat():
    caught = _no_winner(offered=0, new=0, repeated=2, measured=0, kept=0, raced=0)
    assert _measured([caught])["distinct"] == (0, 1)


def test_candidates_that_lost_on_tuning_are_new_and_lost_there():
    lost = _no_winner(offered=0, new=0, measured=3, kept=0, raced=0)
    got = _measured([lost])
    assert got["distinct"] == (1, 1) and got["tuning"] == (0, 1) and "selection" not in got


def test_a_cycle_cut_off_at_what_it_reserved_fails_the_budget_stage_and_reached_no_other():
    cut = _cycle("blocked", code=TICK_BUDGET_SPENT, reason="this tick has sent 9 of the 9 it reserved")
    assert _measured([cut]) == {"budget": (0, 1)}


def test_a_budget_wait_leaves_no_row_so_the_next_cycle_counts_it():
    assert _measured([_cycle("idle", budget={**BUDGET, "waited": 4})])["budget"] == (1, 5)


def test_a_rollback_fails_the_monitor():
    assert _measured([_cycle("rolled_back", audit=AUDITED)])["monitor"] == (0, 1)


def test_stages_reads_the_recent_cycles_only():
    assert _measured([_objective_refused()] * 40)["gate"][1] <= 12


# Refusals by reason ------------------------------------------------------------------------------------------------------
def test_refusals_are_counted_by_reason_and_an_objective_shows():
    got = refusals([_objective_refused()] * 7)
    assert got == {f"{schema.OBJECTIVE_REGRESSED}:wall_clock_s": 7}


def test_a_blocked_cycle_counts_under_the_code_its_reason_carries():
    row = _cycle("blocked", reason="G6_E_META_NOT_CONFIRMED: no person confirmed this workflow")
    assert refusals([row]) == {"G6_E_META_NOT_CONFIRMED": 1}


def test_a_candidate_that_lost_is_no_refusal():
    assert refusals([_adopted(), _no_winner()]) == {}


# The diagnosis -------------------------------------------------------------------------------------------------------------
def test_retrodiction_seven_cycles_the_gate_refused_on_one_objective_name_the_gate_for_the_owner():
    found = diagnose([_objective_refused() for _ in range(7)], (), ())
    assert (found.level, found.constraint, found.stuck) == ("owner", "gate", True)
    assert found.evidence["refusals"] == {f"{schema.OBJECTIVE_REGRESSED}:wall_clock_s": 7}
    assert found.evidence["stages"]["gate"] == {"attempted": 7, "succeeded": 0} and found.evidence["adopted"] == 0
    assert found.change is not None


def test_a_proposer_that_repeats_itself_names_the_loop():
    found = diagnose([_no_winner(offered=3, new=0, measured=0, kept=0, raced=0) for _ in range(4)], (), ())
    assert (found.level, found.constraint, found.stuck) == ("loop", "distinct", True)
    assert found.evidence["stages"]["distinct"] == {"attempted": 4, "succeeded": 0}


def test_a_proposer_with_nothing_to_offer_names_the_loop_as_well():
    found = diagnose([_no_winner(offered=0, new=0, measured=0, kept=0, raced=0) for _ in range(3)], (), ())
    assert (found.level, found.constraint) == ("loop", "proposed")


@pytest.mark.parametrize("row", [
    {"outcome": "idle", "code": "G6_E_LOOP_IDLE", "dev_success": 1.0, "dev_cases": 6},
    {"outcome": "blocked", "code": "G6_E_LOOP_POOL_TOO_SMALL", "reason": "the held-out split has 12 cases and needs 30"},
    {"outcome": "refused", "code": schema.INSUFFICIENT_EVIDENCE, "champion": "c",
     "decision": {"verdict": "refuse", "code": schema.INSUFFICIENT_EVIDENCE, "reasons": ["12 held-out cases (30 needed)"]}},
], ids=["no-failure-left", "refused-for-size", "too-few-cases-for-the-gate"])
def test_a_starved_pool_is_named(row):
    found = diagnose([_cycle(**row) for _ in range(3)], (), ())
    assert (found.level, found.constraint) == ("loop", "pool")


@pytest.mark.parametrize(("code", "named"), [
    ("G6_E_LOOP_HELD_OUT_SPENT", "held_out"), ("G6_E_LOOP_SEALED_BREACH", "held_out"), ("G6_E_LOOP_FROZEN_CHANGED", "envelope"),
    ("G6_E_LOOP_UNMETERED_CALLER", "envelope"), ("G6_E_LOOP_STOPPED", "stop"), ("G6_E_LOOP_PAUSED", "stop"),
    ("G6_E_LOOP_CLASSIFIER_STOP", "stop"), ("G6_E_LOOP_HOST_UNKNOWN", "stop"), ("G6_E_LOOP_NO_CONSTRAINT", "person_only")])
def test_what_only_the_owner_can_change_is_named_at_the_owners_level(code, named):
    found = diagnose([_cycle("blocked", code=code, reason="held") for _ in range(2)], (), ())
    assert (found.level, found.constraint) == ("owner", named)


@pytest.mark.parametrize(("code", "named"), [(schema.HOLDOUT_REUSED, "held_out"), (schema.SUITE_CANNOT_DISCRIMINATE, "judge")])
def test_a_gate_refusal_that_points_past_the_gate_names_what_it_points_at(code, named):
    decision = {"verdict": "refuse", "code": code, "reasons": ["why"]}
    found = diagnose([_cycle("refused", code=code, champion="c", decision=decision) for _ in range(2)], (), ())
    assert (found.level, found.constraint) == ("owner", named)


def test_an_adoption_blocked_after_the_gate_passed_names_what_blocked_it_and_not_the_gate():
    passed = {"verdict": "recommend", "code": schema.RECOMMENDED, "reasons": ["beat the base"]}
    rows = [_cycle("blocked", code="G6_E_LOOP_FROZEN_CHANGED", reason="changed during this cycle: cases", champion="c", decision=passed)
            for _ in range(2)]
    found = diagnose(rows, (), ())
    assert (found.level, found.constraint) == ("owner", "envelope")
    assert found.evidence["refusals"] == {"G6_E_LOOP_FROZEN_CHANGED": 2} and found.evidence["stages"]["gate"]["succeeded"] == 2


def test_a_gate_finding_no_gain_is_the_work_and_never_a_rule_for_the_owner_to_lower():
    decision = {"verdict": "refuse", "code": schema.NO_GAIN, "reasons": ["wins 3, losses 4, p 0.6 (needs p <= 0.05)"]}
    found = diagnose([_cycle("refused", code=schema.NO_GAIN, champion="c", decision=decision) for _ in range(3)], (), ())
    assert (found.level, found.constraint, found.stuck) == ("work", "no_gain", True)


def test_a_rollback_that_keeps_coming_is_the_loops():
    found = diagnose([_cycle("rolled_back", audit=AUDITED) for _ in range(3)], (), ())
    assert (found.level, found.constraint) == ("loop", "monitor")


def test_a_cycle_that_crashed_is_the_loops():
    found = diagnose([_cycle("error", code="G6_E_LOOP_CYCLE_FAILED", reason="ValueError: x") for _ in range(2)], (), ())
    assert (found.level, found.constraint) == ("loop", "error")


def test_a_reason_nothing_here_knows_stays_the_loops_to_read():
    found = diagnose([_cycle("blocked", reason="G6_E_META_SOMETHING_NEW: why") for _ in range(2)], (), ())
    assert (found.level, found.constraint) == ("loop", "refused")


def test_three_cycles_of_one_constraint_with_no_adoption_are_stuck_and_two_are_not():
    two = diagnose([_objective_refused() for _ in range(2)], (), ())
    assert not two.stuck and two.change is None
    three = diagnose([_objective_refused() for _ in range(3)], (), ())
    assert three.stuck and three.change == "method"


def test_staying_stuck_moves_up_from_method_to_level_to_frame():
    seen = [diagnose([_objective_refused() for _ in range(n)], (), ()).change for n in (3, 5, 6, 8, 9, 12)]
    assert seen == ["method", "method", "level", "level", "frame", "frame"]


def test_an_adoption_ends_a_run_of_one_constraint():
    rows = [_objective_refused(), _objective_refused(), _adopted(), _objective_refused(), _objective_refused()]
    assert not diagnose(rows, (), ()).stuck


def test_three_cycles_stopped_at_different_stages_are_not_one_constraint():
    rows = [_objective_refused(), _no_winner(), _no_winner(offered=3, new=0, measured=0, kept=0, raced=0)]
    assert not diagnose(rows, (), ()).stuck


def test_the_stuck_constraint_is_the_present_one_whatever_stood_before_it():
    rows = [*[_objective_refused() for _ in range(5)], *[_no_winner(offered=3, new=0, measured=0, kept=0, raced=0) for _ in range(3)]]
    found = diagnose(rows, (), ())
    assert (found.level, found.constraint, found.stuck) == ("loop", "distinct", True)


def test_a_healthy_loop_is_not_stuck_and_names_no_constraint_in_the_loop():
    rows = [_adopted(), _no_winner(), _adopted(), _objective_refused(), _adopted(), _no_winner()]
    found = diagnose(rows, (), ())
    assert found.level == "work" and found.constraint == "wrong_answer" and not found.stuck and found.change is None
    assert found.evidence["adopted"] == 3


def test_a_loop_two_cycles_past_an_adoption_is_still_moving():
    found = diagnose([_adopted(), _objective_refused(), _objective_refused()], (), ())
    assert found.level == "work" and not found.stuck


def test_a_loop_three_cycles_past_an_adoption_is_read_again():
    found = diagnose([_adopted(), *[_objective_refused() for _ in range(3)]], (), ())
    assert (found.level, found.constraint, found.stuck) == ("owner", "gate", True)


def test_a_null_proposer_is_not_called_progress():
    found = diagnose([_no_winner(raced=2) for _ in range(5)], (), ())
    assert (found.level, found.constraint, found.stuck) == ("work", "selection", True)
    assert found.evidence["adopted"] == 0 and found.change is not None


def test_unknown_stages_are_reported_and_are_never_the_constraint():
    found = diagnose([_objective_refused() for _ in range(3)], (), ())
    assert set(found.evidence["unknown"]) == {"proposed", "distinct", "tuning"} and found.constraint == "gate"
    bare = {"tick": 1, "outcome": "no_winner", "code": "G6_E_LOOP_CYCLE", "reason": ""}
    empty = diagnose([bare for _ in range(5)], (), ())
    assert empty.constraint == "unknown" and empty.level == "work" and not empty.stuck
    assert set(empty.evidence["unknown"]) == set(STAGES) - {"refused"}


def test_an_empty_log_reads():
    found = diagnose([], (), ())
    assert (found.level, found.constraint, found.stuck, found.change, found.repeated) == ("work", "unknown", False, None, {})
    assert set(found.evidence["unknown"]) == set(STAGES)


def test_a_mistake_that_came_back_after_its_lesson_shows_in_repeated():
    lesson = Lesson("t1", 1, "verification_gap", ("a",), "h", ("x",), "c", "s")
    once = diagnose([_adopted()], (lesson,), [(2, "verification_gap", "a"), (2, "verification_gap", "b")])
    assert once.repeated == {"verification_gap": {"after_lesson": 2, "same_cases": 1, "generations": 1, "stuck": False}}
    twice = diagnose([_adopted()], (lesson,), [(2, "verification_gap", "a"), (3, "verification_gap", "b")])
    assert twice.repeated["verification_gap"]["stuck"] is True


def test_a_mistake_with_no_lesson_cannot_be_repeated():
    assert diagnose([_adopted()], (), [(2, "verification_gap", "a")]).repeated == {}


def test_a_diagnosis_is_frozen_and_a_row_can_hold_it():
    found = diagnose([_objective_refused() for _ in range(3)], (), ())
    with pytest.raises(dataclasses.FrozenInstanceError):
        found.level = "work"
    assert json.loads(json.dumps(dataclasses.asdict(found)))["constraint"] == "gate"


# What the tick records for the stages --------------------------------------------------------------------------------------
def test_funnel_adds_up_a_tuning_run_and_takes_what_a_strategy_keeps_a_running_count_of_from_its_last_cycle():
    def stats(proposed, offered, attempts=None, duplicates=None, measured=None):
        return {"proposed": proposed, "code": "c", "attempts": attempts, "duplicates": duplicates, "offered": offered, "measured": measured}

    cycles = [{"strategies": {"reflect": stats(1, 1, 3, 1, 2), "failure_trace": stats(1, 2)}, "raced": [{}, {}]},
              {"strategies": {"reflect": stats(0, 1, 6, 2, 4), "failure_trace": stats(0, 0)}, "raced": [{}]}]
    assert funnel(cycles) == {"offered": 4, "new": 2, "repeated": 2, "measured": 4, "kept": 2, "raced": 3}


def test_funnel_of_a_run_that_never_began_is_empty():
    assert funnel([]) == {}


def test_a_tick_records_what_tuning_did_and_how_the_budget_stood(tmp_path):
    loop, _ = _loop(tmp_path)
    loop.enrol("Ada")
    row = loop.tick()
    assert row["outcome"] == "adopted"
    assert row["tuning"]["new"] >= 1 and row["tuning"]["offered"] >= row["tuning"]["new"] and row["tuning"]["raced"] >= 1
    assert row["budget"]["waited"] == 0
    assert _measured(loop.log.cycles())["gate"] == (1, 1)


def test_a_repeating_proposer_shows_on_the_row(tmp_path):
    loop, _ = _loop(tmp_path, proposer=domains.proposes({"prompt": "Please be polite."}))
    loop.enrol("Ada")
    loop.tick()
    row = loop.tick()
    assert row["outcome"] == "no_winner" and row["tuning"]["offered"] >= 1 and row["tuning"]["new"] == 0


def test_a_budget_wait_is_written_on_the_next_cycle_and_then_cleared(tmp_path):
    loop, _ = _loop(tmp_path)
    loop.enrol("Ada")
    real = loop._reserve
    loop._reserve = lambda: (None, loop_module._blocked(loop_module.BUDGET_WAIT, "wait", ""))
    assert loop.tick()["warning_card"]["code"] == loop_module.BUDGET_WAIT
    assert loop.tick()["warning_card"]["code"] == loop_module.BUDGET_WAIT
    assert loop.log.cycles() == []
    loop._reserve = real
    assert loop.tick()["budget"]["waited"] == 2
    assert loop.tick()["budget"]["waited"] == 0


# The tick reads it and records it ------------------------------------------------------------------------------------------
class _Placed(domains.BugFixes):
    """A domain that can place a cause on the closed list, and one it cannot."""

    def root_cause(self, cause):
        return {"accepted_defect": "verification_gap", "refused_patch": "not on the list"}.get(cause)


def _kept(row):
    return {name: row.get(name) for name in ("outcome", "code", "head_version", "head_profile", "champion", "decision", "dev_success", "constraint")}


def test_a_tick_records_the_diagnosis_and_its_lessons_and_both_read_back_after_a_restart(tmp_path):
    domain = _Placed(tmp_path)
    loop, _ = _loop(tmp_path, domain=domain, proposer=domains.proposes({"prompt": domains.CODE_HINT}))
    loop.enrol("Ada")
    first = loop.tick()
    assert first["reflex"]["constraint"] == "unknown" and first["reflex"]["level"] == "work" and first["reflex"]["stuck"] is False
    [lesson] = first["lessons"]
    assert lesson["root_cause"] == "verification_gap" and lesson["generation"] == 0 and lesson["case_ids"] and lesson["check"]
    second = loop.tick()
    assert second["reflex"]["evidence"]["adopted"] == 1            # it read the first cycle, written before this one opened
    again, _ = _loop(tmp_path, domain=domain, proposer=domains.proposes({"prompt": domains.CODE_HINT}))
    rows = again.log.cycles()
    assert [row["reflex"]["constraint"] for row in rows] == [first["reflex"]["constraint"], second["reflex"]["constraint"]]
    assert rows[0]["lessons"] == json.loads(json.dumps(first["lessons"]))
    [kept] = lessons_in(rows[:1])
    assert kept.root_cause == "verification_gap" and seen_in((kept,))[0] == (0, "verification_gap", kept.case_ids[0])


def test_a_cause_with_no_place_on_the_list_and_a_domain_with_no_root_cause_leave_no_lesson(tmp_path):
    assert not hasattr(domains.WordProblems, "root_cause")
    loop, _ = _loop(tmp_path)
    loop.enrol("Ada")
    row = loop.tick()
    assert row["lessons"] == [] and "constraint" in row["reflex"]


def test_lessons_come_only_from_what_an_independent_witness_saw():
    records = (FailureRecord("a", "accepted_defect", True, "", True), FailureRecord("b", "accepted_defect", True, "", False))
    [lesson] = lessons_from(records, {"accepted_defect": "verification_gap"}.get, generation=2, tick=5, scope="d")
    assert lesson.case_ids == ("a",) and lesson.generation == 2 and lesson.lesson_id == "5:accepted_defect"
    assert lessons_from(records, None, generation=2, tick=5, scope="d") == ()


def test_coding_replay_places_its_two_ways_of_reading_wrongly(tmp_path):
    cases = tmp_path / "cases.jsonl"
    cases.write_text(json.dumps({"id": "p1", "bug": "b1", "input": "a patch", "label": "wrong"}) + "\n", encoding="utf-8")
    replay = CodingReplay(cases, None, FakeLLM(lambda system, user: "VERDICT: RIGHT"))
    assert replay.root_cause("accepted_defect") == "verification_gap" and replay.root_cause("false_alarm") == "bad_remediation"
    assert replay.root_cause("check_rejected") is None
    assert isinstance(replay, Domain)


def test_a_row_from_an_older_log_still_reads():
    rows = [{"tick": 1, "outcome": "refused", "code": schema.NO_GAIN, "reason": "wins 1", "constraint": "wrong_answer"}, _adopted()]
    assert read(rows)["constraint"] == "wrong_answer" and lessons_in(rows) == ()


def test_a_lesson_that_does_not_read_is_left_out():
    assert lessons_in([{"lessons": [{"root_cause": "not on the list"}]}]) == ()


def test_a_mistake_back_in_a_later_generation_reaches_a_rows_reflex():
    lesson = {"lesson_id": "1:x", "root_cause": "verification_gap", "hypothesis": "h", "alternatives": ["a"], "check": "c", "scope": "s"}
    rows = [{"outcome": "no_winner", "lessons": [{**lesson, "generation": 0, "case_ids": ["a"]}]},
            {"outcome": "no_winner", "lessons": [{**lesson, "generation": 1, "case_ids": ["a", "b"]}]}]
    assert read(rows)["repeated"] == {"verification_gap": {"after_lesson": 2, "same_cases": 1, "generations": 1, "stuck": False}}


def test_a_diagnosis_that_fails_is_recorded_and_costs_the_cycle_nothing(tmp_path, monkeypatch):
    def fails(*given, **named):
        raise RuntimeError("the records surprised it")

    monkeypatch.setattr("g6.metaharness.reflex.diagnose", fails)
    loop, _ = _loop(tmp_path)
    loop.enrol("Ada")
    row = loop.tick()
    assert row["outcome"] == "adopted" and row["reflex"] == {"unreadable": "RuntimeError: the records surprised it"}


def test_a_domain_whose_root_cause_fails_costs_the_cycle_nothing(tmp_path):
    class Faulty(domains.WordProblems):
        def root_cause(self, cause):
            raise RuntimeError("no idea")

    loop, _ = _loop(tmp_path, domain=Faulty(tmp_path))
    loop.enrol("Ada")
    row = loop.tick()
    assert row["outcome"] == "adopted" and row["lessons"] == []


def test_the_diagnosis_is_on_a_cycle_that_went_wrong_too(tmp_path, monkeypatch):
    loop, _ = _loop(tmp_path)
    loop.enrol("Ada")

    def fails(*given, **named):
        raise RuntimeError("the cycle broke")

    monkeypatch.setattr(loop, "_cycle", fails)
    row = loop.tick()
    assert row["outcome"] == "error" and "constraint" in row["reflex"]


def test_a_stall_on_a_fake_domain_is_read_from_its_own_rows_within_three_cycles_of_it_starting(tmp_path):
    loop, _ = _loop(tmp_path, proposer=domains.proposes({"prompt": "Please be polite."}))
    loop.enrol("Ada")
    rows = [loop.tick() for _ in range(5)]
    assert [row["outcome"] for row in rows] == ["no_winner"] * 5
    assert (rows[-1]["reflex"]["level"], rows[-1]["reflex"]["constraint"]) == ("loop", "distinct")
    assert rows[-1]["reflex"]["stuck"] and rows[-1]["reflex"]["change"] == "method" and not rows[0]["reflex"]["stuck"]


def test_no_proposer_is_shown_what_the_step_recorded(tmp_path):
    shown: list[str] = []
    answers = domains.proposes({"prompt": "Please be polite."})

    def reply(system, user):
        shown.append(user)
        return answers.complete(system, user)

    loop, _ = _loop(tmp_path, proposer=FakeLLM(reply))
    loop.enrol("Ada")
    loop.tick()
    loop.tick()
    assert shown and not any("reflex" in text or "lessons" in text for text in shown)


def test_a_tick_adopts_exactly_what_it_adopted_before_this_step(tmp_path):
    loop, _ = _loop(tmp_path)
    loop.enrol("Ada")
    rows = [loop.tick() for _ in range(3)]
    assert [row["outcome"] for row in rows] == BEFORE["outcomes"]
    assert rows[0]["champion"] == BEFORE["champion"] and rows[0]["head_version"] == 1
    assert [row["head_profile"] for row in rows[:2]] == [BEFORE["head"], BEFORE["champion"]]


def test_the_step_changes_nothing_a_cycle_does_when_it_is_taken_out(tmp_path, monkeypatch):
    def rows(folder, make):
        loop, _ = _loop(folder, domain=make(folder), proposer=domains.proposes({"prompt": domains.CODE_HINT}))
        loop.enrol("Ada")
        got = [_kept(loop.tick()) for _ in range(3)]
        return got, loop.meter.reading("claude", NOW).used

    with_it = rows(tmp_path / "a", _Placed)
    monkeypatch.setattr("g6.metaharness.reflex.read", lambda cycles: {})
    monkeypatch.setattr("g6.metaharness.reflex.lessons_from", lambda *given, **named: ())
    assert rows(tmp_path / "b", _Placed) == with_it


def test_the_digest_leads_with_what_the_rows_say_now_and_not_with_what_the_last_cycle_opened_on(tmp_path):
    loop, _ = _loop(tmp_path, proposer=domains.proposes({"prompt": "Please be polite."}))
    loop.enrol("Ada")
    for _ in range(4):
        loop.tick()
    last = loop.log.cycles()[-1]
    assert last["reflex"]["stuck"] is False                        # it opened on two cycles of the same
    page = Runner(loop, now=lambda: NOW, calendar=UTC)._page(NOW.date(), NOW, "")
    assert page.splitlines()[2].startswith("**What limits progress: distinct, at the loop level.** Stuck for 3 cycles")


def test_a_page_over_a_log_older_than_this_step_has_no_such_line(tmp_path):
    loop, _ = _loop(tmp_path)
    loop.enrol("Ada")
    assert "What limits progress" not in Runner(loop, now=lambda: NOW, calendar=UTC)._page(NOW.date(), NOW, "")


@pytest.mark.parametrize("name", ["metaharness/reflex.py", "metaharness/lessons.py"])
def test_the_step_cannot_change_under_an_enrolled_folder(tmp_path, name):
    source = Path(g6.__file__).resolve().parent
    assert name in build_manifest(source, patterns=ENVELOPE_PATTERNS)
    copy = tmp_path / "copy"
    for found in build_manifest(source, patterns=ENVELOPE_PATTERNS):
        (copy / found).parent.mkdir(parents=True, exist_ok=True)
        (copy / found).write_bytes((source / found).read_bytes())
    before = manifest_sha256(build_manifest(copy, patterns=ENVELOPE_PATTERNS))
    with (copy / name).open("a", encoding="utf-8") as changed:
        changed.write("\n# a change\n")
    assert manifest_sha256(build_manifest(copy, patterns=ENVELOPE_PATTERNS)) != before


@pytest.mark.parametrize("name", ["NO_CONSTRAINT", "IDLE", "POOL_TOO_SMALL", "HELD_OUT_SPENT", "SPLIT_REUSED", "SEALED_BREACH", "FROZEN_CHANGED",
                                  "UNMETERED_CALLER", "STOPPED", "PAUSED", "CLASSIFIER_STOP", "HOST_UNKNOWN", "CYCLE_FAILED",
                                  "CYCLE"])
def test_the_loops_codes_written_out_here_are_the_loops_own(name):
    from g6.metaharness import reflex
    assert getattr(reflex, name) == getattr(loop_module, name)
