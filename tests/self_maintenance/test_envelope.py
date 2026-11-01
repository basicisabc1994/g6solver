# SPDX-License-Identifier: Apache-2.0
"""The loop's records and what halts it (design 6.10): none of it knows what the work is."""
from __future__ import annotations

import json
import time

import pytest

from g6.core.ledger import LedgerCorrupt, LedgerError
from g6.self_maintenance.envelope import (
    FINDINGS,
    CycleLog,
    ask_to_stop,
    beat,
    heartbeat,
    runner_alive,
    runner_lock,
    stop_reason,
)
from g6.workspace._envelope import WorkspaceRefused


def _log(tmp_path) -> CycleLog:
    return CycleLog(tmp_path / "records" / "constraint_log.jsonl")


def _cycle(log: CycleLog, outcome: str, **fields) -> None:
    log.cycle({"outcome": outcome, **fields})


def test_the_log_is_a_local_file_that_survives_a_new_process(tmp_path):
    log = _log(tmp_path)
    _cycle(log, "adopted", constraint="wrong_answer")
    again = _log(tmp_path)
    assert [c["outcome"] for c in again.cycles()] == ["adopted"]
    assert again.cycles()[0]["tick"] == 1 and again.cycles()[0]["constraint"] == "wrong_answer"


def test_a_changed_line_does_not_open(tmp_path):
    log = _log(tmp_path)
    _cycle(log, "adopted", constraint="wrong_answer")
    text = log.path.read_text(encoding="utf-8").replace("wrong_answer", "right_answer")
    log.path.write_text(text, encoding="utf-8")
    with pytest.raises(LedgerCorrupt):
        _log(tmp_path)


def test_ticks_are_numbered_in_order(tmp_path):
    log = _log(tmp_path)
    for outcome in ("idle", "refused", "adopted"):
        _cycle(log, outcome)
    assert [c["tick"] for c in log.cycles()] == [1, 2, 3]


def test_a_cycle_needs_a_known_outcome(tmp_path):
    with pytest.raises(LedgerError):
        _cycle(_log(tmp_path), "finished")


# The streak ---------------------------------------------------------------------------------------------------------

def test_failed_cycles_in_a_row_are_counted(tmp_path):
    log = _log(tmp_path)
    for outcome in ("error", "blocked", "sealed_breach"):
        _cycle(log, outcome)
    assert log.failed_streak() == 3


@pytest.mark.parametrize("ending", ["adopted", "idle", "no_winner", "no_constraint", "refused", "rolled_back"])
def test_any_result_ends_the_streak_since_a_candidate_that_lost_is_a_result_not_a_fault(tmp_path, ending):
    log = _log(tmp_path)
    for outcome in ("error", ending, "blocked"):
        _cycle(log, outcome)
    assert log.failed_streak() == 1


def test_results_alone_are_never_a_streak(tmp_path):
    log = _log(tmp_path)
    for outcome in ("no_winner", "no_constraint", "refused", "rolled_back") * 3:
        _cycle(log, outcome)
    assert log.failed_streak() == 0


def test_a_rollback_is_a_result_even_of_an_adoption_it_undid(tmp_path):
    log = _log(tmp_path)
    _cycle(log, "error")
    _cycle(log, "adopted", head_version=1)
    _cycle(log, "rolled_back", undoes=1)
    assert log.failed_streak() == 0


# Nothing to try ------------------------------------------------------------------------------------------------------

def test_cycles_that_found_nothing_to_try_are_counted_in_a_row(tmp_path):
    log = _log(tmp_path)
    for outcome in ("adopted", "idle", "no_winner", "no_constraint"):
        _cycle(log, outcome)
    assert log.quiet_streak() == 3


@pytest.mark.parametrize("ending", ["adopted", "refused", "rolled_back", "error"])
def test_a_cycle_that_tried_something_or_broke_is_not_quiet(tmp_path, ending):
    log = _log(tmp_path)
    for outcome in ("idle", ending):
        _cycle(log, outcome)
    assert log.quiet_streak() == 0


def test_a_person_enrolling_or_resuming_starts_the_quiet_count_again(tmp_path):
    log = _log(tmp_path)
    _cycle(log, "idle")
    log.enrol("Ada", {"evaluator": "a"})
    assert log.quiet_streak() == 0
    _cycle(log, "idle")
    log.resume("Ada")
    assert log.quiet_streak() == 0


def test_a_person_resuming_clears_the_streak_and_the_pause(tmp_path):
    log = _log(tmp_path)
    _cycle(log, "error")
    log.pause("three failed cycles")
    assert log.paused() == "three failed cycles"
    log.resume("Ada")
    assert log.paused() == "" and log.failed_streak() == 0


@pytest.mark.parametrize("name", ["", "   ", "auto:loop"])
def test_only_a_person_resumes_it_or_enrols_a_domain(tmp_path, name):
    log = _log(tmp_path)
    log.pause("why")
    with pytest.raises(LedgerError, match="a person"):
        log.resume(name)
    with pytest.raises(LedgerError, match="a person"):
        log.enrol(name, {"evaluator": "a"})
    assert log.paused() == "why"


def test_the_last_enrolment_is_what_counts(tmp_path):
    log = _log(tmp_path)
    assert log.enrolled() is None
    log.enrol("Ada", {"evaluator": "a"})
    log.enrol("Bo", {"evaluator": "b"})
    assert log.enrolled()["frozen"] == {"evaluator": "b"} and log.enrolled()["by"] == "Bo"


# STOP and the heartbeat ----------------------------------------------------------------------------------------------

def test_a_stop_file_in_the_loops_folder_says_why(tmp_path):
    assert stop_reason(tmp_path) == ""
    (tmp_path / "STOP").write_text("gone fishing\n", encoding="utf-8")
    assert "gone fishing" in stop_reason(tmp_path)
    (tmp_path / "STOP").write_text("", encoding="utf-8")
    assert stop_reason(tmp_path)
    (tmp_path / "STOP").unlink()
    assert stop_reason(tmp_path) == ""


def test_a_stop_that_cannot_be_read_still_stops(tmp_path):
    (tmp_path / "STOP").mkdir()
    assert stop_reason(tmp_path)


def test_a_heartbeat_is_whole_or_absent(tmp_path):
    assert heartbeat(tmp_path) is None
    beat(tmp_path, tick=4, state="waiting")
    assert heartbeat(tmp_path) == {"tick": 4, "state": "waiting"}
    assert json.loads((tmp_path / "heartbeat.json").read_text(encoding="utf-8"))["tick"] == 4
    assert not list(tmp_path.glob("*.tmp"))


def test_a_heartbeat_that_cannot_be_written_is_only_a_missed_beat(tmp_path, monkeypatch):
    def refused(*args):
        raise PermissionError("a reader holds the file")

    beat(tmp_path, tick=1)
    monkeypatch.setattr("g6.self_maintenance.envelope.os.replace", refused)
    beat(tmp_path, tick=2)
    assert heartbeat(tmp_path) == {"tick": 1}


def test_asking_to_stop_writes_the_file_and_repeating_keeps_the_first_reason(tmp_path):
    assert "owner's review" in ask_to_stop(tmp_path, "owner's review\nsecond line")
    assert "owner's review" in ask_to_stop(tmp_path, "something later")
    assert (tmp_path / "STOP").read_text(encoding="utf-8") == "owner's review second line\n"


def test_asking_to_stop_with_no_reason_still_says_where_it_came_from(tmp_path):
    assert "g6_improve_stop" in ask_to_stop(tmp_path)


# Is a runner alive? ----------------------------------------------------------------------------------------------------

def test_a_folder_no_runner_ever_held_is_not_alive_and_looking_writes_nothing(tmp_path):
    assert not runner_alive(tmp_path) and list(tmp_path.iterdir()) == []


def test_a_runner_is_alive_while_it_holds_the_lock_and_not_after(tmp_path):
    with runner_lock(tmp_path):
        assert runner_alive(tmp_path)
    assert not runner_alive(tmp_path)


def test_a_second_runner_is_refused_the_lock(tmp_path, monkeypatch):
    monkeypatch.setattr("g6.workspace._coordination.MUTEX_WAIT", 0.05)
    with runner_lock(tmp_path), pytest.raises(WorkspaceRefused):
        with runner_lock(tmp_path):
            pass


def test_looking_is_immediate_while_a_runner_lives(tmp_path):
    with runner_lock(tmp_path):
        started = time.monotonic()
        assert runner_alive(tmp_path)
        assert time.monotonic() - started < 2


# Who started it --------------------------------------------------------------------------------------------------------

def test_a_person_starts_the_runner_and_the_log_keeps_who(tmp_path):
    log = _log(tmp_path)
    assert log.started() is None
    log.start("Ada", at="noon")
    assert log.started() == {"by": "Ada", "at": "noon"}


@pytest.mark.parametrize("name", ["", "   ", "auto:loop"])
def test_no_agent_name_can_start_it(tmp_path, name):
    with pytest.raises(LedgerError, match="a person"):
        _log(tmp_path).start(name)


# What a proposer found -------------------------------------------------------------------------------------------------

def test_a_cycle_has_the_four_fields_and_they_stay_empty_with_none_given(tmp_path):
    log = _log(tmp_path)
    _cycle(log, "idle")
    row = log.cycles()[0]
    assert {name: row[name] for name in FINDINGS} == {"structure": "", "resource": "", "obstruction": "", "composed_from": []}
    assert log.findings() == []


def test_the_log_gives_a_next_tick_the_latest_findings_and_nothing_else(tmp_path):
    log = _log(tmp_path)
    _cycle(log, "idle")
    _cycle(log, "refused", structure="a regularity", constraint="wrong_answer")
    assert log.findings() == [{"tick": 2, "structure": "a regularity", "resource": "", "obstruction": "", "composed_from": []}]
    for _ in range(12):
        _cycle(log, "refused", resource="r")
    assert [f["tick"] for f in log.findings(last=3)] == [12, 13, 14]
