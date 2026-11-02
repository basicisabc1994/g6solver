# SPDX-License-Identifier: Apache-2.0
"""The loop reading its own records: what limits progress, the work or the loop itself. Recorded rows and fake domains only."""
from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import g6
from g6.integrity import build_manifest, manifest_sha256
from g6.metaharness import loop as loop_module
from g6.metaharness import schema
from g6.metaharness.loop import ENVELOPE_PATTERNS, LoopSettings, ShadowLoop
from g6.metaharness.method import Method
from g6.metaharness.reflex import STAGES, funnel, refusals, stages
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
                                  "UNMETERED_CALLER", "STOPPED", "PAUSED", "CLASSIFIER_STOP", "CYCLE_FAILED", "CYCLE"])
def test_the_loops_codes_written_out_here_are_the_loops_own(name):
    from g6.metaharness import reflex
    assert getattr(reflex, name) == getattr(loop_module, name)
