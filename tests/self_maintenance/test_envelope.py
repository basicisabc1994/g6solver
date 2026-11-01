# SPDX-License-Identifier: Apache-2.0
"""The loop's records and what halts it (design 6.10): none of it knows what the work is."""
from __future__ import annotations

import builtins
import hashlib
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
    TICK_BUDGET_SPENT,
    CycleLog,
    ask_to_stop,
    beat,
    heartbeat,
    note_refusal,
    record_stop,
    refused_requests,
    runner_alive,
    runner_lock,
    stop_reason,
    stop_standing,
    stops_asked,
)
from g6.workspace._coordination import Coordination
from g6.workspace._envelope import WorkspaceRefused

LONG_AGO = 1_000_000_000_000_000_000                                     # in nanoseconds, in 2001, whole on any file system


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


def test_a_tick_cut_off_at_its_own_reservation_is_no_fault(tmp_path):
    """It stopped where the loop told it to. Nothing broke, and three of them must not stop a week."""
    log = _log(tmp_path)
    for _ in range(4):
        _cycle(log, "blocked", code=TICK_BUDGET_SPENT)
    assert log.failed_streak() == 0


def test_a_cut_off_tick_neither_counts_in_a_streak_nor_ends_one(tmp_path):
    """It is no result either: it compared nothing, so the faults on each side of it are still in a row."""
    log = _log(tmp_path)
    _cycle(log, "error")
    _cycle(log, "blocked", code=TICK_BUDGET_SPENT)
    _cycle(log, "blocked", code="G6_E_LOOP_FROZEN_CHANGED")
    _cycle(log, "blocked", code=TICK_BUDGET_SPENT)
    _cycle(log, "error")
    assert log.failed_streak() == 3 and [c["outcome"] for c in log.failed_run()] == ["error", "blocked", "error"]


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
    with pytest.raises(LedgerError, match="a person"):
        log.demote(name, undoes=1)
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


def test_a_reader_holding_the_heartbeat_for_a_moment_costs_no_beat(tmp_path, monkeypatch):
    """A start reads it ten times a second, and a beat lost there is a whole nap in which the runner reads as still starting."""
    beat(tmp_path, tick=1)
    real, refusals = os.replace, [PermissionError("a reader holds the file")] * 2

    def held_for_a_moment(*args):
        if refusals:
            raise refusals.pop()
        real(*args)

    monkeypatch.setattr("g6.self_maintenance.envelope.os.replace", held_for_a_moment)
    beat(tmp_path, tick=2)
    assert heartbeat(tmp_path) == {"tick": 2}


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


# A start lifts only what a person agreed to, and moves nothing ------------------------------------------------------------

def _stop_files(folder) -> list[str]:
    return [found.name for found in folder.iterdir() if found.name.startswith("STOP")]


def _started_lifting(folder, **fields) -> None:
    """A person's start on the ``STOP`` file as it stands, as ``g6_improve_start`` records it."""
    _log(folder).start("Ada", **envelope.stop_to_lift(folder), **fields)


def _lifted_long_ago(folder, text: str = "stop\n") -> None:
    """A start on a file with an old time, so a real write after it lands on another time however coarse the clock is."""
    (folder / "STOP").write_text(text, encoding="utf-8")
    os.utime(folder / "STOP", ns=(LONG_AGO, LONG_AGO))
    _started_lifting(folder)


def test_a_stop_a_start_lifted_no_longer_stops_and_stays_where_it_is(tmp_path):
    ask_to_stop(tmp_path, "agreed")
    before = (tmp_path / "STOP").read_bytes()
    _started_lifting(tmp_path)
    assert stop_reason(tmp_path) == "" and _stop_files(tmp_path) == ["STOP"]
    assert (tmp_path / "STOP").read_bytes() == before


def test_a_stop_after_a_lifted_one_stands_and_names_itself(tmp_path):
    ask_to_stop(tmp_path, "agreed")
    _started_lifting(tmp_path)
    assert "a newer stop" in ask_to_stop(tmp_path, "a newer stop") and "agreed" not in stop_reason(tmp_path)


def test_with_nothing_lifted_a_stop_stands(tmp_path):
    ask_to_stop(tmp_path, "new")
    _log(tmp_path).start("Ada", lifted="", lifted_bytes=0)
    assert "new" in stop_reason(tmp_path)


def test_a_start_that_recorded_no_lifting_lifts_nothing(tmp_path):
    ask_to_stop(tmp_path, "new")
    _log(tmp_path).start("Ada", stops=1)
    assert "new" in stop_reason(tmp_path)


def test_a_stop_with_no_records_stands_and_reading_it_writes_nothing(tmp_path):
    ask_to_stop(tmp_path, "new")
    names = sorted(found.name for found in tmp_path.iterdir())
    assert "new" in stop_reason(tmp_path) and sorted(found.name for found in tmp_path.iterdir()) == names


@pytest.mark.parametrize("written", [lambda held: held + b"\n", lambda held: held[:-1], lambda held: b"",
                                     lambda held: b"X" + held[1:]])
def test_any_change_to_a_lifted_stop_makes_it_stand(tmp_path, written):
    ask_to_stop(tmp_path, "agreed")
    _started_lifting(tmp_path)
    (tmp_path / "STOP").write_bytes(written((tmp_path / "STOP").read_bytes()))
    assert stop_reason(tmp_path)


def test_a_person_clears_a_stale_stop_by_deleting_it_and_the_next_stop_stands_whatever_it_says(tmp_path):
    ask_to_stop(tmp_path, "the same reason")
    _started_lifting(tmp_path)
    (tmp_path / "STOP").unlink()
    assert stop_reason(tmp_path) == "" and "the same reason" in ask_to_stop(tmp_path, "the same reason")


def test_a_stop_stands_when_the_records_that_could_lift_it_do_not_check_out(tmp_path):
    ask_to_stop(tmp_path, "agreed")
    _started_lifting(tmp_path)
    path = tmp_path / "records" / "constraint_log.jsonl"
    path.write_text(path.read_text(encoding="utf-8").replace("Ada", "Bo"), encoding="utf-8")
    assert stop_reason(tmp_path)


def test_a_stop_stands_when_the_records_that_could_lift_it_cannot_be_opened(tmp_path, monkeypatch):
    def held(path):
        raise PermissionError("another process holds this file")

    ask_to_stop(tmp_path, "agreed")
    _started_lifting(tmp_path)
    monkeypatch.setattr(envelope, "CycleLog", held)
    assert stop_reason(tmp_path)


def test_a_stop_during_a_start_outlives_a_crash_with_no_count_and_no_note(tmp_path, monkeypatch):
    """Nothing moved, so nothing can be lost: what the stop wrote is what a restart reads."""
    (tmp_path / "STOP").write_text("an old stop\n", encoding="utf-8")
    _started_lifting(tmp_path, stops=0)
    real = builtins.open

    def no_count(path, mode="r", *args, **kwargs):
        if Path(path).name == STOPS_FILE:
            raise PermissionError("the count cannot be added to")
        return real(path, mode, *args, **kwargs)

    def no_note(*args, **kwargs):
        raise PermissionError("the log cannot be written")

    with monkeypatch.context() as broken:
        broken.setattr(builtins, "open", no_count)
        broken.setattr(CycleLog, "stop", no_note)
        record_stop(tmp_path, ask_to_stop(tmp_path, "a new stop"), at="noon")
    assert stops_asked(tmp_path) == 0 and "a new stop" in stop_reason(tmp_path) and _stop_files(tmp_path) == ["STOP"]


def test_the_same_bytes_written_over_a_lifted_stop_by_hand_stand(tmp_path):
    """``echo stop > STOP``. This file once pinned the opposite as a limit: bytes alone cannot say a person wrote again."""
    _lifted_long_ago(tmp_path)
    assert stop_reason(tmp_path) == ""
    (tmp_path / "STOP").write_text("stop\n", encoding="utf-8")
    assert stop_reason(tmp_path) == "a STOP file is present: stop"


def test_a_touch_on_a_lifted_stop_stands(tmp_path):
    _lifted_long_ago(tmp_path)
    (tmp_path / "STOP").touch()
    assert stop_reason(tmp_path) == "a STOP file is present: stop"


def test_a_lifted_stop_deleted_and_made_again_the_same_stands(tmp_path):
    _lifted_long_ago(tmp_path)
    (tmp_path / "STOP").unlink()
    (tmp_path / "STOP").write_text("stop\n", encoding="utf-8")
    assert stop_reason(tmp_path) == "a STOP file is present: stop"


def test_a_lifted_file_nobody_changed_still_does_not_stop_across_a_reopen(tmp_path):
    (tmp_path / "STOP").write_text("stop\n", encoding="utf-8")
    _started_lifting(tmp_path)
    (tmp_path / "STOP").read_bytes()                                     # reading it is no write
    assert _log(tmp_path).started()["lifted"] and stop_reason(tmp_path) == ""


def test_a_stop_arriving_between_the_time_and_the_bytes_stands(tmp_path, monkeypatch):
    """The time is taken first, so a write between the two leaves an old time beside new bytes."""
    (tmp_path / "STOP").write_text("agreed\n", encoding="utf-8")
    os.utime(tmp_path / "STOP", ns=(LONG_AGO, LONG_AGO))
    real, fired = Path.read_bytes, []

    def a_stop_just_before_the_read(path):
        if path.name == "STOP" and not fired:
            fired.append(path)
            ask_to_stop(tmp_path, "a late stop")
        return real(path)

    with monkeypatch.context() as hooked:
        hooked.setattr(Path, "read_bytes", a_stop_just_before_the_read)
        agreed = envelope.stop_to_lift(tmp_path)
    _log(tmp_path).start("Ada", **agreed)
    assert fired and stop_reason(tmp_path)


def test_a_start_row_with_no_time_on_it_stands_rather_than_lifts(tmp_path):
    (tmp_path / "STOP").write_text("stop\n", encoding="utf-8")
    held = (tmp_path / "STOP").read_bytes()
    _log(tmp_path).start("Ada", lifted=hashlib.sha256(held).hexdigest(), lifted_bytes=len(held))
    assert stop_reason(tmp_path) == "a STOP file is present: stop"


def test_a_stop_whose_time_cannot_be_read_stands_and_gives_a_start_nothing_to_lift(tmp_path, monkeypatch):
    ask_to_stop(tmp_path, "agreed")
    _started_lifting(tmp_path)
    real = Path.stat

    def refused(path, **kwargs):
        if path.name == "STOP":
            raise PermissionError("the file cannot be looked at")
        return real(path, **kwargs)

    monkeypatch.setattr(Path, "stat", refused)
    assert "could not be read" in stop_reason(tmp_path) and envelope.stop_to_lift(tmp_path) == {}


def test_the_limit_of_this_a_writer_that_puts_the_old_time_back_is_not_seen(tmp_path):
    """What is left of the limit, and a file system whose time is coarse enough for a rewrite to land on the same tick is
    the same case. It takes the very bytes and the very time, so only that or a deliberate hand gets here."""
    _lifted_long_ago(tmp_path)
    (tmp_path / "STOP").write_text("stop\n", encoding="utf-8")
    os.utime(tmp_path / "STOP", ns=(LONG_AGO, LONG_AGO))
    assert stop_reason(tmp_path) == ""


def test_what_a_start_lifts_is_the_file_as_it_stands(tmp_path):
    assert envelope.stop_to_lift(tmp_path) == {}
    (tmp_path / "STOP").write_bytes(b"why\n")
    os.utime(tmp_path / "STOP", ns=(LONG_AGO, LONG_AGO))
    assert envelope.stop_to_lift(tmp_path) == {"lifted": hashlib.sha256(b"why\n").hexdigest(), "lifted_bytes": 4,
                                               "lifted_mtime_ns": LONG_AGO}


def test_a_stop_that_cannot_be_read_gives_a_start_nothing_to_lift(tmp_path):
    (tmp_path / "STOP").mkdir()
    assert envelope.stop_to_lift(tmp_path) == {}


def test_a_count_that_cannot_be_read_is_no_count_a_start_recorded(tmp_path):
    (tmp_path / STOPS_FILE).mkdir()
    assert stops_asked(tmp_path) == -1


def test_a_stop_that_cannot_be_counted_still_stops(tmp_path):
    (tmp_path / STOPS_FILE).mkdir()
    assert "why" in ask_to_stop(tmp_path, "why") and (tmp_path / "STOP").is_file()


# A stop that died between its two writes -----------------------------------------------------------------------------------

def _counted_only(folder) -> None:
    """What ``ask_to_stop`` leaves if it dies between its two writes."""
    with open(folder / STOPS_FILE, "a", encoding="utf-8") as stops:
        stops.write("the owner said no\n")


def test_a_stop_that_died_before_its_file_stands_by_its_count(tmp_path):
    _counted_only(tmp_path)
    assert STOPS_FILE in stop_standing(tmp_path, {"stops": 0}) and stop_reason(tmp_path) == ""


def test_no_stop_since_the_start_is_no_stop(tmp_path):
    assert stop_standing(tmp_path, {"stops": 0}) == ""
    _counted_only(tmp_path)
    assert stop_standing(tmp_path, {"stops": 1}) == ""


@pytest.mark.parametrize("start", [None, {}, {"by": "Ada"}])
def test_with_no_count_on_a_start_there_is_nothing_to_compare(tmp_path, start):
    _counted_only(tmp_path)
    assert stop_standing(tmp_path, start) == ""


def test_a_stop_file_speaks_for_itself_ahead_of_the_count(tmp_path):
    ask_to_stop(tmp_path, "the owner's review")
    assert "the owner's review" in stop_standing(tmp_path, {"stops": 0})


def test_a_count_that_cannot_be_read_stands_as_a_stop(tmp_path):
    (tmp_path / STOPS_FILE).mkdir()
    assert stop_standing(tmp_path, {"stops": 0})


# What was refused --------------------------------------------------------------------------------------------------------

DIGEST = "a" * 64


def test_a_refusal_is_kept_with_no_records_needed_and_reading_it_changes_nothing(tmp_path):
    assert refused_requests(tmp_path) == set() and list(tmp_path.iterdir()) == []
    note_refusal(tmp_path, DIGEST, "G6_E_MODEL_REFUSED: cyber\nand a second line")
    assert refused_requests(tmp_path) == {DIGEST} and len((tmp_path / "refused.log").read_text(encoding="utf-8").splitlines()) == 1


def test_a_refusal_that_cannot_be_kept_raises_nothing(tmp_path):
    (tmp_path / "refused.log").mkdir()
    note_refusal(tmp_path, DIGEST, "said")


def test_a_line_a_crash_cut_short_is_no_digest(tmp_path):
    note_refusal(tmp_path, DIGEST, "said")
    with open(tmp_path / "refused.log", "a", encoding="utf-8") as cut:
        cut.write("abc")
    assert refused_requests(tmp_path) == {DIGEST}


def test_refusals_that_cannot_be_read_are_refused_as_records_are(tmp_path):
    (tmp_path / "refused.log").mkdir()
    with pytest.raises(LedgerError, match="refused.log"):
        refused_requests(tmp_path)


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
