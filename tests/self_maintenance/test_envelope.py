# SPDX-License-Identifier: Apache-2.0
"""The loop's records and what halts it (design 6.10): none of it knows what the work is."""
from __future__ import annotations

import builtins
import json
import os
import time
from pathlib import Path

import pytest

from g6.core.ledger import LedgerCorrupt, LedgerError
from g6.self_maintenance import envelope
from g6.self_maintenance.envelope import (
    FINDINGS,
    STOPS_FILE,
    CycleLog,
    ask_to_stop,
    beat,
    heartbeat,
    record_stop,
    runner_alive,
    runner_lock,
    stop_reason,
    stops_asked,
)
from g6.workspace._coordination import Coordination
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
    assert (tmp_path / "STOP").read_text(encoding="utf-8").splitlines()[0] == "owner's review second line"


def test_asking_to_stop_with_no_reason_still_says_where_it_came_from(tmp_path):
    assert "g6_improve_stop" in ask_to_stop(tmp_path)


def test_every_stop_asked_for_is_counted_even_with_one_already_there(tmp_path):
    assert stops_asked(tmp_path) == 0
    ask_to_stop(tmp_path, "first")
    ask_to_stop(tmp_path, "second")
    assert stops_asked(tmp_path) == 2 and (tmp_path / "STOP").read_text(encoding="utf-8").splitlines()[0] == "first"


def test_a_stop_is_counted_before_its_file_is_written(tmp_path, monkeypatch):
    seen = []
    real = builtins.open

    def noted(path, *args, **kwargs):
        if Path(path).name == "STOP":
            seen.append(stops_asked(tmp_path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", noted)
    ask_to_stop(tmp_path, "why")
    assert seen == [1]


def test_no_two_stops_leave_the_same_bytes_whatever_they_say(tmp_path):
    ask_to_stop(tmp_path, "the same reason")
    first = (tmp_path / "STOP").read_bytes()
    (tmp_path / "STOP").unlink()
    ask_to_stop(tmp_path, "the same reason")
    assert (tmp_path / "STOP").read_bytes() != first


def test_each_stop_changes_the_file_even_with_one_already_there(tmp_path):
    ask_to_stop(tmp_path, "first")
    before = envelope.file_digest(tmp_path / "STOP")
    ask_to_stop(tmp_path, "second")
    assert envelope.file_digest(tmp_path / "STOP") != before


def test_a_stop_whose_file_is_taken_as_it_writes_writes_it_again(tmp_path, monkeypatch):
    real, taken = builtins.open, []

    class Taken:
        """What a start moving the file aside does to a writer on a system that lets it."""

        def __init__(self, handle) -> None:
            self.handle = handle

        def __enter__(self):
            return self.handle

        def __exit__(self, *exc) -> None:
            self.handle.close()
            (tmp_path / "STOP").unlink()

    def once(path, *args, **kwargs):
        handle = real(path, *args, **kwargs)
        if Path(path).name == "STOP" and not taken:
            taken.append(path)
            return Taken(handle)
        return handle

    monkeypatch.setattr(builtins, "open", once)
    assert "why" in ask_to_stop(tmp_path, "why") and (tmp_path / "STOP").is_file()


# A start clears only what a person agreed to --------------------------------------------------------------------------

def _stop_files(folder) -> list[str]:
    return [found.name for found in folder.iterdir() if found.name.startswith("STOP")]


def test_a_start_clears_the_bytes_it_agreed_to(tmp_path):
    ask_to_stop(tmp_path, "agreed")
    assert envelope.clear_stop(tmp_path, envelope.file_digest(tmp_path / "STOP")) == ""
    assert _stop_files(tmp_path) == []


def test_a_stop_nobody_agreed_to_clear_stands(tmp_path):
    ask_to_stop(tmp_path, "first")
    agreed = envelope.file_digest(tmp_path / "STOP")
    ask_to_stop(tmp_path, "second")
    assert "first" in envelope.clear_stop(tmp_path, agreed) and (tmp_path / "STOP").is_file()


def test_with_nothing_agreed_a_stop_stands(tmp_path):
    ask_to_stop(tmp_path, "new")
    assert "new" in envelope.clear_stop(tmp_path, "") and (tmp_path / "STOP").is_file()


def test_with_nothing_there_nothing_stands(tmp_path):
    assert envelope.clear_stop(tmp_path, "") == "" and envelope.clear_stop(tmp_path, "gone") == ""


def test_a_stop_landing_between_the_check_and_the_move_is_put_back(tmp_path, monkeypatch):
    ask_to_stop(tmp_path, "agreed")
    agreed = envelope.file_digest(tmp_path / "STOP")
    real, reads = envelope.file_digest, []

    def a_stop_after_the_check(path):
        found = real(path)
        reads.append(path)
        if len(reads) == 1:
            ask_to_stop(tmp_path, "a late stop")
        return found

    monkeypatch.setattr(envelope, "file_digest", a_stop_after_the_check)
    assert "agreed" in envelope.clear_stop(tmp_path, agreed)
    assert "a late stop" in (tmp_path / "STOP").read_text(encoding="utf-8") and _stop_files(tmp_path) == ["STOP"]


def test_a_stop_landing_after_the_move_stands_in_a_file_of_its_own(tmp_path, monkeypatch):
    ask_to_stop(tmp_path, "agreed")
    agreed = envelope.file_digest(tmp_path / "STOP")
    real = os.replace

    def then_a_stop(source, target):
        real(source, target)
        if Path(target).name != "STOP":
            ask_to_stop(tmp_path, "a late stop")

    monkeypatch.setattr(envelope.os, "replace", then_a_stop)
    assert "a late stop" in envelope.clear_stop(tmp_path, agreed) and _stop_files(tmp_path) == ["STOP"]


def test_a_file_a_stop_holds_open_is_not_cleared_and_says_so(tmp_path, monkeypatch):
    ask_to_stop(tmp_path, "agreed")
    agreed = envelope.file_digest(tmp_path / "STOP")

    def held(source, target):
        raise PermissionError("another process holds this file")

    monkeypatch.setattr(envelope.os, "replace", held)
    with pytest.raises(OSError, match="holds this file"):
        envelope.clear_stop(tmp_path, agreed)
    assert (tmp_path / "STOP").is_file()


def test_a_count_that_cannot_be_read_is_no_count_a_start_recorded(tmp_path):
    (tmp_path / STOPS_FILE).mkdir()
    assert stops_asked(tmp_path) == -1


def test_a_stop_that_cannot_be_counted_still_stops(tmp_path):
    (tmp_path / STOPS_FILE).mkdir()
    assert "why" in ask_to_stop(tmp_path, "why") and (tmp_path / "STOP").is_file()


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


# The generation the log saw --------------------------------------------------------------------------------------------

def test_the_log_knows_the_latest_generation_a_cycle_made(tmp_path):
    log = _log(tmp_path)
    assert log.generation() == 0
    _cycle(log, "adopted", head_version=1)
    _cycle(log, "rolled_back", head_version=2, undoes=1)
    assert log.generation() == 2


def test_a_cycle_that_made_no_generation_says_nothing_of_the_chain(tmp_path):
    log = _log(tmp_path)
    _cycle(log, "refused", head_version=7)                       # the head it began on, which can be a person's version
    assert log.generation() == 0


# A start in force ------------------------------------------------------------------------------------------------------

def test_no_start_is_in_force_until_a_person_gives_one(tmp_path):
    log = _log(tmp_path)
    log.enrol("Ada", {"evaluator": "a"})
    assert not log.start_in_force()
    log.start("Ada", at="noon")
    assert log.start_in_force()


def test_a_stop_ends_a_start_and_only_a_new_start_brings_it_back(tmp_path):
    log = _log(tmp_path)
    log.start("Ada", at="noon")
    log.stop("the owner's review", at="one")
    assert not log.start_in_force() and log.started()["by"] == "Ada"
    log.start("Ada", at="two")
    assert log.start_in_force()


def test_a_stop_on_record_states_its_reason(tmp_path):
    with pytest.raises(LedgerError, match="states its reason"):
        _log(tmp_path).stop("")


def test_a_recorded_stop_goes_in_the_log_once(tmp_path):
    log = _log(tmp_path)
    log.start("Ada", at="noon")
    for _ in range(2):
        record_stop(tmp_path, "the owner's review", at="one")
    log.reload()
    assert not log.start_in_force() and sum(kind == "stopped" for kind, _ in log._rows()) == 1


def test_recording_a_stop_with_no_records_makes_nothing(tmp_path):
    record_stop(tmp_path, "why", at="one")
    assert list(tmp_path.iterdir()) == []


def test_records_that_do_not_read_never_get_in_a_stops_way(tmp_path):
    log = _log(tmp_path)
    log.start("Ada", at="noon")
    log.path.write_text(log.path.read_text(encoding="utf-8").replace("Ada", "Bo"), encoding="utf-8")
    record_stop(tmp_path, "why", at="one")


def test_a_tick_holding_the_records_leaves_the_stop_for_the_runner_to_note(tmp_path):
    log = _log(tmp_path)
    log.start("Ada", at="noon")
    with Coordination(tmp_path / ".tick").mutex():
        record_stop(tmp_path, "why", at="one")
    log.reload()
    assert log.start_in_force()


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
