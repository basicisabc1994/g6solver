# SPDX-License-Identifier: Apache-2.0
"""The loop's budget (design 6.6): weekly provider windows, paced, on the operator's one ledger."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta, timezone

import pytest

from g6.operator.store import OperatorStore
from g6.self_maintenance.pacing import (
    OWNER,
    SETTINGS_FILE,
    AccountReading,
    BudgetMeter,
    ProviderWindow,
    ceiling,
    read_settings,
    standing,
    target,
    windows_from,
)

START = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)


class _Clock:
    """A store's own clock, which the test opens the store with and moves: the store alone dates a settlement, by it.
    Until moved it reads the wall clock, as a store with no clock of its own does."""

    def __init__(self, now: datetime | None = None) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now or datetime.now(UTC)


def _settle_at(meter: BudgetMeter, admission, used: float, at: datetime) -> float:
    """Settle as the store does at ``at``: it is the store's clock that is moved, never a time handed to ``settle``."""
    clock = getattr(meter.scope.store.clock, "__wrapped__", None)          # the store reads it through utc_clock
    assert isinstance(clock, _Clock), "open the store with a _Clock to settle at a time of the test's"
    clock.now = at
    return meter.settle(admission, used)

def _day(day: int) -> datetime:
    return START + timedelta(days=day - 1, hours=1)


def _meter(allowance: float = 1000, *, read_account=None, reserve_share: float = 0.2) -> BudgetMeter:
    window = ProviderWindow("claude", allowance, START, reserve_share=reserve_share)
    return BudgetMeter((window,), store=OperatorStore(":memory:", clock=_Clock()), read_account=read_account)


@pytest.mark.parametrize("day,share", [(1, 0.2), (2, 0.4), (3, 0.6), (4, 0.8), (5, 0.8), (6, 0.8), (7, 1.0)])
def test_the_ceiling_paces_the_week_and_keeps_the_owners_reserve_until_the_last_day(day, share):
    assert ceiling(1000, day, 0.2) == pytest.approx(1000 * share)


def test_the_reserve_is_a_setting():
    assert ceiling(1000, 5, 0.0) == 1000 and ceiling(1000, 5, 0.5) == 500
    assert ceiling(1000, 7, 0.5) == 1000


def test_the_target_is_the_allowance_spread_over_seven_days():
    assert target(700, 3) == pytest.approx(300)


def test_the_day_of_the_week_rolls_over_with_the_provider_not_the_calendar():
    window = ProviderWindow("claude", 1000, START)
    assert window.place(START) == (START, 1)
    assert window.place(START + timedelta(days=6, hours=23)) == (START, 7)
    assert window.place(START + timedelta(days=7)) == (START + timedelta(days=7), 1)
    assert window.place(START + timedelta(days=15)) == (START + timedelta(days=14), 2)


def test_a_session_starts_only_if_its_worst_case_stays_under_the_ceiling():
    meter = _meter()
    assert meter.reserve("claude", _day(1), 200).admitted          # exactly the ceiling of day one
    refused = meter.reserve("claude", _day(1), 1)
    assert not refused.admitted and "ceiling" in refused.reason and refused.reservation == ""


def test_what_was_reserved_counts_until_it_is_settled_so_a_crashed_tick_is_charged_in_full():
    meter = _meter()
    assert meter.reserve("claude", _day(2), 300).admitted
    assert meter.reading("claude", _day(2)).used == 300             # no settle: a crash leaves it standing
    assert not meter.reserve("claude", _day(2), 101).admitted


def test_settling_charges_what_the_host_counted_up_or_down():
    meter = _meter()
    spend = meter.reserve("claude", _day(2), 300)
    assert meter.settle(spend, 40) == 40
    assert meter.reading("claude", _day(2)).used == 40
    over = meter.reserve("claude", _day(3), 100)
    assert meter.settle(over, 150) == 150
    assert meter.reading("claude", _day(3)).used == 190


def test_a_settled_tick_that_used_nothing_costs_nothing():
    meter = _meter()
    assert meter.settle(meter.reserve("claude", _day(1), 50), 0) == 0
    assert meter.reading("claude", _day(1)).used == 0


def test_a_reservation_settles_once():
    meter = _meter()
    spend = meter.reserve("claude", _day(1), 50)
    meter.settle(spend, 10)
    with pytest.raises(ValueError):
        meter.settle(spend, 10)


def test_the_whole_accounts_use_counts_and_the_loop_gives_way():
    reading = AccountReading(used=350, estimated=True)
    meter = _meter(read_account=lambda provider, start: reading)
    seen = meter.reading("claude", _day(2))
    assert seen.used == 350 and seen.estimated
    assert not meter.reserve("claude", _day(2), 100).admitted      # 350 + 100 is over day two's 400
    assert meter.reserve("claude", _day(2), 50).admitted


def test_the_ledger_is_never_read_below_what_the_loop_itself_committed():
    meter = _meter(read_account=lambda provider, start: AccountReading(used=10, read_at=_day(3) + timedelta(hours=1)))
    _settle_at(meter, meter.reserve("claude", _day(3), 300), 300, _day(3))
    assert meter.reading("claude", _day(3) + timedelta(hours=1)).used == 300


def test_a_reading_with_no_time_holds_none_of_the_charges_so_each_goes_on_top():
    """Review wks1, finding 1: it cannot say which charges it already holds, so it is read as holding none: high, never
    low."""
    meter = _meter(read_account=lambda provider, start: AccountReading(used=10))
    _settle_at(meter, meter.reserve("claude", _day(3), 300), 300, _day(3))
    assert meter.reading("claude", _day(3)).used == 310


def test_at_a_reported_limit_the_loop_waits_for_the_reset():
    reset = _day(2) + timedelta(hours=4)
    meter = _meter(read_account=lambda provider, start: AccountReading(limited_until=reset))
    refused = meter.reserve("claude", _day(2), 1)
    assert not refused.admitted and refused.wait_until == reset
    assert meter.reserve("claude", reset, 1).admitted


def test_a_limit_a_request_ran_into_stands_as_a_reported_one_does():
    reset = _day(2) + timedelta(hours=4)
    meter = _meter()
    meter.limit_reached("claude", reset)
    refused = meter.reserve("claude", _day(2), 1)
    assert not refused.admitted and refused.wait_until == reset and "reported limit" in refused.reason
    assert meter.reading("claude", _day(2)).wait_until == reset
    assert meter.reserve("claude", reset, 1).admitted


def test_the_later_of_a_reported_limit_and_one_met_stands():
    soon, late = _day(2) + timedelta(hours=1), _day(2) + timedelta(hours=4)
    meter = _meter(read_account=lambda provider, start: AccountReading(limited_until=late))
    meter.limit_reached("claude", soon)
    assert meter.reserve("claude", _day(2), 1).wait_until == late
    meter = _meter(read_account=lambda provider, start: AccountReading(limited_until=soon))
    meter.limit_reached("claude", late)
    assert meter.reserve("claude", _day(2), 1).wait_until == late


def test_a_limit_met_on_one_provider_holds_no_other():
    meter = BudgetMeter((ProviderWindow("claude", 1000, START), ProviderWindow("codex", 1000, START)),
                        store=OperatorStore(":memory:", clock=_Clock()))
    meter.limit_reached("claude", _day(5))
    assert meter.reserve("codex", _day(2), 1).admitted


def test_a_new_week_starts_empty():
    meter = _meter()
    spend = meter.reserve("claude", _day(5), 800)
    assert spend.admitted and not meter.reserve("claude", _day(5), 1).admitted
    _settle_at(meter, spend, 800, _day(5) + timedelta(hours=1))
    assert meter.reserve("claude", _day(8), 200).admitted


def test_a_reservation_still_open_counts_in_every_week_from_its_start_until_it_is_settled():
    """Review wks1, finding 6: a request it sent may still come back and be charged after its week ends."""
    meter = _meter()
    spend = meter.reserve("claude", _day(5), 800)
    assert meter.reading("claude", _day(8)).used == 800 and meter.reading("claude", _day(15)).used == 800
    _settle_at(meter, spend, 800, _day(8) + timedelta(hours=1))
    assert meter.reading("claude", _day(8)).used == 800 and meter.reading("claude", _day(15)).used == 0


def test_a_reading_gives_the_end_its_provider_reported_even_one_already_past():
    soon, gone = _day(2) + timedelta(hours=1), _day(2) - timedelta(hours=1)
    assert _meter().reading("claude", _day(2)).ends == START + timedelta(days=7)
    assert _meter(read_account=lambda provider, start: AccountReading(resets_at=soon)).reading("claude", _day(2)).ends == soon
    past = _meter(read_account=lambda provider, start: AccountReading(resets_at=gone))
    assert past.reading("claude", _day(2)).ends == gone
    refused = past.reserve("claude", _day(2), 1)          # nothing it said stands now: unread, until a read replaces it
    assert not refused.admitted and "not known" in refused.reason and refused.ends == gone
    fresh = _meter(read_account=lambda provider, start: AccountReading(resets_at=gone + timedelta(days=7)))
    assert fresh.reserve("claude", _day(2), 1).ends == gone + timedelta(days=7)   # a tick reserves in the week that came after


def test_each_provider_has_its_own_week():
    windows = (ProviderWindow("claude", 100, START), ProviderWindow("codex", 1000, START + timedelta(days=3)))
    meter = BudgetMeter(windows, store=OperatorStore(":memory:", clock=_Clock()))
    assert meter.reading("codex", _day(5)).day == 2
    assert meter.reserve("claude", _day(5), 80).admitted
    assert meter.reading("codex", _day(5)).used == 0


def test_a_provider_with_no_window_gets_no_budget():
    assert not _meter().reserve("codex", _day(1), 1).admitted


@pytest.mark.parametrize("bad", [
    {"allowance": 0}, {"allowance": float("inf")}, {"reserve_share": 1.0}, {"reserve_share": -0.1},
    {"week_start": datetime(2026, 10, 26)},
])
def test_a_window_that_makes_no_sense_is_refused(bad):
    fields = {"provider": "claude", "allowance": 100, "week_start": START} | bad
    with pytest.raises(ValueError):
        ProviderWindow(**fields)


# Finding 7: a pending reservation is not hidden by what the account has used ----------------------------------------------

def test_7_a_pending_reservation_adds_to_what_the_account_reports():
    meter = _meter(read_account=lambda provider, start: AccountReading(used=350))
    meter.reserve("claude", _day(3), 200)                      # nothing of it can be in the account's figure yet
    assert meter.reading("claude", _day(3)).used == 550
    assert not meter.reserve("claude", _day(3), 51).admitted   # 550 + 51 is over day three's 600
    assert meter.reserve("claude", _day(3), 50).admitted


def test_7_a_charge_can_already_be_in_the_accounts_figure_so_it_does_not_add():
    meter = _meter(read_account=lambda provider, start: AccountReading(used=350, read_at=_day(3) + timedelta(hours=1)))
    _settle_at(meter, meter.reserve("claude", _day(3), 200), 100, _day(3))   # settled before it was read: the 350 may include it
    assert meter.reading("claude", _day(3) + timedelta(hours=1)).used == 350


def test_7_with_no_account_reading_the_reservation_still_counts_once():
    meter = _meter()
    meter.settle(meter.reserve("claude", _day(3), 100), 40)
    meter.reserve("claude", _day(3), 200)
    assert meter.reading("claude", _day(3)).used == 240


def test_7_outstanding_is_reserved_less_released_and_a_settled_week_has_none():
    scope = OperatorStore(":memory:", clock=_Clock()).for_owner("o")
    venture = scope.create_venture("v", caps={"model": 100, "cash": 0})["venture_id"]
    open_one = scope.reserve(venture, month="m", account="model", amount=30)
    assert scope.outstanding(venture, "m") == {"model": 30.0}
    scope.settle(venture, open_one, metered=5)
    assert scope.outstanding(venture, "m") == {"model": 0.0}
    assert scope.committed(venture, "m") == {"model": 5.0}


def test_7_the_estimate_flag_follows_whichever_figure_was_used():
    meter = _meter(read_account=lambda provider, start: AccountReading(used=350, estimated=True))
    meter.reserve("claude", _day(3), 200)
    assert meter.reading("claude", _day(3)).estimated
    quiet = _meter(read_account=lambda provider, start: AccountReading(used=10, estimated=True,
                                                                       read_at=_day(3) + timedelta(hours=1)))
    _settle_at(quiet, quiet.reserve("claude", _day(3), 100), 100, _day(3))
    assert not quiet.reading("claude", _day(3) + timedelta(hours=1)).estimated

# Finding 8: the ceiling is checked in the transaction that reserves --------------------------------------------------------

def _shared_meters(path, count: int, allowance: float = 1000, read_account=None) -> list[BudgetMeter]:
    return [BudgetMeter((ProviderWindow("claude", allowance, START),), store=OperatorStore(path, clock=_Clock()), read_account=read_account)
            for _ in range(count)]


def test_8_two_admissions_that_each_read_room_cannot_both_pass_the_ceiling(tmp_path):
    """A rival that tries to reserve once this one has read its room cannot get in before it writes: the room is read in
    the transaction that writes, so the rival waits for it, reads the room again in its own, and finds none."""
    path = tmp_path / "operator.db"
    late: list = []
    rivals: list[threading.Thread] = []

    meter, rival = _shared_meters(path, 2)
    read = meter._look

    def another_tick_tries_in_between(provider, now):
        seen = read(provider, now)
        if not rivals:
            rivals.append(threading.Thread(target=lambda: late.append(rival.reserve("claude", _day(2), 300))))
            rivals[0].start()
            rivals[0].join(0.5)
            assert rivals[0].is_alive() and not late         # held at the lock, after this one read its room
        return seen

    meter._look = another_tick_tries_in_between
    got = meter.reserve("claude", _day(2), 300)                # 300 + 300 is over day two's 400
    rivals[0].join(10)
    assert got.admitted and not late[0].admitted and "ceiling" in late[0].reason
    assert meter.reading("claude", _day(2)).used == 300


def test_8_concurrent_admissions_stay_under_the_ceiling(tmp_path):
    path, workers = tmp_path / "operator.db", 8
    meters = _shared_meters(path, workers)
    start = threading.Barrier(workers)
    admitted: list[bool] = []

    def tick(meter: BudgetMeter) -> None:
        start.wait()
        admitted.append(meter.reserve("claude", _day(2), 150).admitted)

    threads = [threading.Thread(target=tick, args=(m,)) for m in meters]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(admitted) == 2                                  # 2 x 150 fits day two's 400; a third never does
    assert meters[0].reading("claude", _day(2)).used == 300


def test_8_the_check_sees_the_account_as_read_and_the_ledger_as_it_stands(tmp_path):
    reading = AccountReading(used=250)
    meter, rival = _shared_meters(tmp_path / "operator.db", 2, read_account=lambda provider, start: reading)
    assert rival.reserve("claude", _day(2), 100).admitted      # 250 + 100 fits 400
    assert not meter.reserve("claude", _day(2), 100).admitted  # 250 + 100 pending + 100 does not


def test_8_a_refused_admission_writes_nothing(tmp_path):
    meter = _shared_meters(tmp_path / "operator.db", 1)[0]
    assert not meter.reserve("claude", _day(1), 201).admitted
    assert meter.reading("claude", _day(1)).used == 0


# A reading has a date: what was charged after it cannot be in its figure ---------------------------------------------------

def _dated(used: float, hour: int) -> AccountReading:
    return AccountReading(used=used, read_at=START + timedelta(hours=hour))


def test_a_charge_after_a_dated_reading_goes_on_top_of_it():
    reading = _dated(350, 1)
    meter = _meter(read_account=lambda provider, start: reading)
    assert meter.reading("claude", _day(3)).used == 350
    meter.settle(meter.reserve("claude", _day(3), 200), 100)
    assert meter.reading("claude", _day(3)).used == 450


def test_a_charge_before_a_dated_reading_may_be_in_it_so_it_does_not_add():
    readings = [None]
    meter = _meter(read_account=lambda provider, start: readings[0])
    _settle_at(meter, meter.reserve("claude", _day(3), 200), 100, _day(3))
    readings[0] = AccountReading(used=350, read_at=_day(3) + timedelta(hours=1))
    assert meter.reading("claude", _day(3) + timedelta(hours=1)).used == 350


def test_a_fresh_reading_takes_over_from_what_was_added_to_the_last():
    readings = [AccountReading(used=350, read_at=_day(3) - timedelta(hours=1))]
    meter = _meter(read_account=lambda provider, start: readings[0])
    meter.reading("claude", _day(3))
    _settle_at(meter, meter.reserve("claude", _day(3), 100), 100, _day(3))
    assert meter.reading("claude", _day(3)).used == 450
    readings[0] = AccountReading(used=470, read_at=_day(3) + timedelta(hours=1))
    assert meter.reading("claude", _day(3) + timedelta(hours=1)).used == 470


def test_a_charge_settled_after_a_reading_goes_on_top_whenever_a_meter_first_sees_it(tmp_path):
    """Review-fx4's finding 3: the baseline was what had been charged when a reading first reached a meter, so a charge
    settled on another connection after the reading was taken, and before any meter looked, was hidden in it."""
    reading, path = AccountReading(used=500, read_at=_day(3)), tmp_path / "operator.db"
    first_store, second_store = OperatorStore(path, clock=_Clock()), OperatorStore(path, clock=_Clock())
    try:
        first = BudgetMeter((ProviderWindow("claude", 1000, START),), store=first_store)
        spend = first.reserve("claude", _day(3) - timedelta(minutes=5), 200)
        _settle_at(first, spend, 200, _day(3) + timedelta(minutes=1))
        second = BudgetMeter((ProviderWindow("claude", 1000, START),), store=second_store, read_account=lambda *_: reading)
        later = _day(3) + timedelta(minutes=2)
        assert second.reading("claude", later).used == 700 and not second.reserve("claude", later, 1).admitted
    finally:
        first_store.close()
        second_store.close()


def test_another_connections_charge_after_a_snapshot_is_not_absorbed_into_its_baseline(tmp_path):
    """Review-fx4's finding 3 as it was found, on the real clock, its charge settled with no time of its own."""
    now = datetime.now(UTC)
    window = ProviderWindow("codex", 1000, now - timedelta(days=3))
    path = tmp_path / "operator.db"
    first_store, second_store = OperatorStore(path, clock=_Clock()), OperatorStore(path, clock=_Clock())
    try:
        first = BudgetMeter((window,), store=first_store)
        pending = first.reserve("codex", now, 200)
        assert pending.admitted
        snapshot = AccountReading(used=500, read_at=datetime.now(UTC), estimated=False)
        time.sleep(1.05)
        first.settle(pending, 200)
        second = BudgetMeter((window,), store=second_store, read_account=lambda *_: snapshot)
        seen = second.reading("codex", datetime.now(UTC))
        admitted = second.reserve("codex", datetime.now(UTC), 200)
        assert (seen.used, admitted.admitted) == (700, False), "a post-reading charge was hidden by the first baseline"
    finally:
        first_store.close()
        second_store.close()


def test_a_charge_settled_when_a_reading_was_taken_may_be_in_it():
    reading = AccountReading(used=300, read_at=_day(3))
    meter = _meter(read_account=lambda *_: reading)
    _settle_at(meter, meter.reserve("claude", _day(3) - timedelta(minutes=5), 200), 200, _day(3))
    assert meter.reading("claude", _day(3)).used == 300


def test_a_charge_with_no_time_goes_on_top_of_every_reading():
    reading = AccountReading(used=300, read_at=_day(3) + timedelta(days=1))
    meter = _meter(read_account=lambda *_: reading)
    meter.settle(meter.reserve("claude", _day(3), 200), 200)
    assert meter.reading("claude", _day(4)).used == 500


def test_the_check_inside_the_transaction_counts_a_charge_since_the_reading():
    reading = _dated(350, 1)
    meter, rival = _shared_meters_with(reading)
    assert meter.reading("claude", _day(3)).used == 350
    rival.settle(rival.reserve("claude", _day(3), 100), 100)         # another handle, on the same ledger
    assert meter.reading("claude", _day(3)).used == 450
    assert not meter.reserve("claude", _day(3), 151).admitted        # 450 + 151 is over day three's 600
    assert meter.reserve("claude", _day(3), 150).admitted


def _shared_meters_with(reading: AccountReading) -> list[BudgetMeter]:
    store = OperatorStore(":memory:", clock=_Clock())
    return [BudgetMeter((ProviderWindow("claude", 1000, START),), store=store, read_account=lambda provider, start: reading)
            for _ in range(2)]


# A reading's figure is of one week ----------------------------------------------------------------------------------------

RESET = START + timedelta(days=7)


def test_a_figure_from_a_week_that_has_ended_says_nothing_of_the_new_one():
    reading = AccountReading(used=1000, estimated=False, read_at=RESET - timedelta(hours=1), resets_at=RESET)
    meter = _meter(read_account=lambda provider, start: reading)
    assert meter.reading("claude", RESET - timedelta(seconds=1)).used == 1000
    assert meter.reading("claude", RESET).used == 0
    assert not meter.reserve("claude", RESET, 200).admitted                # not known until it is read again
    reading = AccountReading(used=0, estimated=False, read_at=RESET, resets_at=RESET + timedelta(days=7))
    assert meter.reserve("claude", RESET, 200).admitted


def test_a_limit_still_ahead_stands_when_the_figure_beside_it_has_ended():
    until = RESET + timedelta(hours=2)
    reading = AccountReading(used=1000, estimated=False, limited_until=until, read_at=RESET - timedelta(hours=1), resets_at=RESET)
    meter = _meter(read_account=lambda provider, start: reading)
    seen = meter.reserve("claude", RESET + timedelta(hours=1), 1)
    assert not seen.admitted and seen.wait_until == until and seen.used == 0


def test_a_figure_with_no_reset_on_it_stands_as_it_did():
    reading = AccountReading(used=500)
    meter = _meter(read_account=lambda provider, start: reading)
    assert meter.reading("claude", RESET).used == 500


# The provider's own week ---------------------------------------------------------------------------------------------------

OFF_THE_GRID = START + timedelta(days=3, hours=5)           # a reset the typed week_start does not lead to


def test_a_window_moves_to_a_reset_it_was_not_on_and_stays_on_one_it_was():
    window = ProviderWindow("claude", 1000, START)
    assert window.aligned(OFF_THE_GRID).place(OFF_THE_GRID) == (OFF_THE_GRID, 1)
    assert window.aligned(START + timedelta(days=14)) is window  # the same moment in another zone must not name a new week


def test_a_reset_the_provider_reports_sets_the_day_of_the_week():
    reading = AccountReading(used=100, estimated=False, read_at=START, resets_at=OFF_THE_GRID)
    meter = _meter(read_account=lambda provider, start: reading)
    seen = meter.reading("claude", OFF_THE_GRID - timedelta(hours=1))
    assert seen.day == 7 and seen.ceiling == 1000                    # by the settings alone it would be day 4, at 800


def test_a_reservation_ends_with_the_providers_week_and_not_the_settings(tmp_path):
    reading = AccountReading(used=100, estimated=False, read_at=START, resets_at=OFF_THE_GRID)
    meter = _meter(read_account=lambda provider, start: reading)
    assert meter.reserve("claude", OFF_THE_GRID - timedelta(hours=1), 1).ends == OFF_THE_GRID


def test_the_week_stays_where_the_provider_put_it_after_the_reading_goes():
    readings = [AccountReading(used=100, estimated=False, read_at=START, resets_at=OFF_THE_GRID)]
    meter = _meter(read_account=lambda provider, start: readings[0])
    meter.reading("claude", START + timedelta(days=1))
    readings[0] = None
    assert meter.reading("claude", OFF_THE_GRID + timedelta(hours=1)).day == 1


def test_a_reading_with_no_reset_on_it_moves_nothing():
    meter = _meter(read_account=lambda provider, start: AccountReading(used=100, read_at=START))
    assert meter.reading("claude", _day(4)).day == 4


def test_the_room_a_tick_waits_for_follows_the_providers_days():
    reading = AccountReading(used=0, estimated=False, read_at=START, resets_at=OFF_THE_GRID)
    meter = _meter(read_account=lambda provider, start: reading)
    meter.reading("claude", START + timedelta(hours=1))
    assert meter.next_room(START + timedelta(hours=1)) == OFF_THE_GRID - timedelta(days=3)


# The open week keeps its book when the provider's own week is learned ------------------------------------------------------

def _learns_the_week(reset=OFF_THE_GRID):
    """A meter on its typed week, and what hands it a reading part way through: ``readings[0]`` is what the tool has said."""
    readings: list[AccountReading | None] = [None]
    return _meter(read_account=lambda provider, start: readings[0]), readings


def _reading(used: float, at: datetime, reset: datetime = OFF_THE_GRID) -> AccountReading:
    return AccountReading(used=used, estimated=False, read_at=at, resets_at=reset)


def test_a_tick_that_learns_the_providers_week_keeps_its_whole_charge_against_the_ceiling():
    meter, readings = _learns_the_week()
    now = _day(3)
    tick = meter.reserve("claude", now, 100)
    readings[0] = _reading(20, now + timedelta(seconds=1))
    assert meter.reading("claude", now + timedelta(seconds=1)).used >= 20
    assert meter.settle(tick, 50) == 50
    later = now + timedelta(seconds=2)
    seen = meter.reading("claude", later)
    assert seen.used >= 50
    assert not meter.reserve("claude", later, seen.ceiling - 40).admitted  # room for 20 used and this, and none for 50


def test_what_was_charged_before_the_week_moved_still_counts():
    meter, readings = _learns_the_week()
    meter.settle(meter.reserve("claude", _day(3), 150), 150)
    readings[0] = _reading(0, _day(3))
    assert meter.reading("claude", _day(3) + timedelta(seconds=1)).used == 150


def test_a_reservation_still_open_when_the_week_moves_stays_on_the_ceiling():
    meter, readings = _learns_the_week()
    meter.reserve("claude", _day(3), 150)
    readings[0] = _reading(0, _day(3))
    assert meter.reading("claude", _day(3) + timedelta(seconds=1)).used == 150


def test_a_second_move_of_the_same_week_keeps_the_same_book():
    meter, readings = _learns_the_week()
    meter.settle(meter.reserve("claude", _day(3), 150), 150)
    readings[0] = _reading(0, _day(3))
    meter.reading("claude", _day(3))
    readings[0] = _reading(0, _day(3), reset=OFF_THE_GRID + timedelta(hours=2))
    assert meter.reading("claude", _day(3) + timedelta(seconds=1)).used == 150


def test_the_week_after_a_move_opens_a_book_of_its_own():
    meter, readings = _learns_the_week()
    _settle_at(meter, meter.reserve("claude", _day(3), 150), 150, _day(3))
    readings[0] = _reading(0, _day(3))
    meter.reading("claude", _day(3))
    readings[0] = None
    assert meter.reading("claude", OFF_THE_GRID + timedelta(hours=1)).used == 0


def test_a_reset_already_gone_opens_the_new_week_and_carries_nothing_over():
    meter, readings = _learns_the_week()
    _settle_at(meter, meter.reserve("claude", _day(4), 150), 150, _day(4))
    readings[0] = _reading(900, OFF_THE_GRID - timedelta(hours=1))
    assert meter.reading("claude", OFF_THE_GRID + timedelta(minutes=1)).used == 0


def test_a_tick_still_open_when_its_week_moves_earlier_counts_in_both_weeks_it_may_have_sent_in():
    """Its span runs to the end of the week it reserved in, past the provider's new reset: what it may send after that
    reset is in the new week, so it counts there too until it is settled."""
    meter, readings = _learns_the_week()
    meter.reserve("claude", _day(3), 150)
    readings[0] = _reading(0, _day(3))
    meter.reading("claude", _day(3))
    readings[0] = None
    assert meter.reading("claude", OFF_THE_GRID + timedelta(hours=1)).used == 150


def test_a_charge_settled_without_a_time_counts_in_every_week_its_reservation_could_reach():
    meter, readings = _learns_the_week()
    meter.settle(meter.reserve("claude", _day(3), 150), 150)
    readings[0] = _reading(0, _day(3))
    meter.reading("claude", _day(3))
    readings[0] = None
    assert meter.reading("claude", OFF_THE_GRID + timedelta(hours=1)).used == 150


# When room comes, and the settings file ----------------------------------------------------------------------------------

def test_the_next_day_of_a_window_is_when_its_ceiling_can_rise():
    window = ProviderWindow("claude", 1000, START)
    assert window.next_day(START) == START + timedelta(days=1)
    assert window.next_day(_day(7)) == START + timedelta(days=7)      # the last day ends the week


def test_room_comes_with_the_soonest_rise_of_any_ceiling():
    windows = (ProviderWindow("claude", 100, START), ProviderWindow("codex", 100, START + timedelta(hours=5)))
    meter = BudgetMeter(windows, store=OperatorStore(":memory:", clock=_Clock()))
    assert meter.next_room(START + timedelta(hours=6)) == START + timedelta(days=1)
    assert meter.next_room(START + timedelta(hours=25)) == START + timedelta(hours=29)


def _settings(tmp_path, **window) -> dict:
    fields = {"provider": "claude", "allowance": 5000, "week_start": START.isoformat()} | window
    (tmp_path / SETTINGS_FILE).write_text(json.dumps({"windows": [fields]}), encoding="utf-8")
    return read_settings(tmp_path)


def test_window_settings_come_from_the_file_in_the_loops_folder(tmp_path):
    [window] = windows_from(_settings(tmp_path, reserve_share=0.3))
    assert (window.provider, window.allowance, window.week_start, window.reserve_share) == ("claude", 5000, START, 0.3)


def test_an_allowance_is_an_estimate_until_the_file_says_it_is_a_real_figure(tmp_path):
    assert windows_from(_settings(tmp_path))[0].estimated is True
    assert windows_from(_settings(tmp_path, estimated=False))[0].estimated is False


def test_the_owners_reserve_stays_the_default_when_the_file_is_silent(tmp_path):
    assert windows_from(_settings(tmp_path))[0].reserve_share == 0.2


@pytest.mark.parametrize("bad", [
    {"allowance": 0}, {"allowance": "plenty"}, {"week_start": "soon"}, {"week_start": "2026-10-26T08:00:00"},
    {"reserve_share": 1.0}, {"provider": ""},
])
def test_a_settings_file_that_makes_no_sense_is_refused(tmp_path, bad):
    with pytest.raises(ValueError):
        windows_from(_settings(tmp_path, **bad))


@pytest.mark.parametrize("text", ["not json", "[]", '{"windows": 3}', '{"windows": [3]}', '{"windows": []}'])
def test_a_settings_file_that_cannot_be_read_says_so(tmp_path, text):
    (tmp_path / SETTINGS_FILE).write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        windows_from(read_settings(tmp_path))


def test_a_missing_settings_file_says_so(tmp_path):
    with pytest.raises(ValueError, match=SETTINGS_FILE):
        read_settings(tmp_path)


# Each reservation carries its own span, and no book moves between weeks -------------------------------------------------

MONDAY = datetime(2026, 10, 26, tzinfo=UTC)
BRISBANE = timezone(timedelta(hours=10))


def _codex(store, start=MONDAY, resets_at=None) -> BudgetMeter:
    read = (lambda *_: AccountReading(resets_at=resets_at)) if resets_at is not None else None
    return BudgetMeter((ProviderWindow("codex", 1000, start),), store=store, read_account=read)


def test_a_week_that_goes_monday_to_tuesday_and_back_keeps_its_reservation_through_every_move(tmp_path):
    path, now = tmp_path / "operator.db", MONDAY + timedelta(days=4, hours=1)
    store = OperatorStore(path, clock=_Clock())
    try:
        assert _codex(store).reserve("codex", MONDAY + timedelta(days=2), 350).admitted
    finally:
        store.close()
    seen = []
    for days in (8, 7, 8, 7, 9):                    # a restart at each move
        store = OperatorStore(path, clock=_Clock())
        try:
            meter = _codex(store, resets_at=MONDAY + timedelta(days=days))
            seen.append((meter.reading("codex", now).used, meter.reserve("codex", now, 500).admitted))
        finally:
            store.close()
    assert seen == [(350, False)] * 5


def test_a_reservation_is_filed_under_the_week_it_was_made_in_for_good(tmp_path):
    store = OperatorStore(tmp_path / "operator.db", clock=_Clock())
    try:
        made = _codex(store).reserve("codex", MONDAY + timedelta(days=2), 350)
        for days in (8, 7, 9):
            _codex(store, resets_at=MONDAY + timedelta(days=days)).reading("codex", MONDAY + timedelta(days=2))
        rows = {tuple(row) for row in store.conn.execute("SELECT venture_id, month FROM ledger WHERE ref = ?",
                                                         (made.reservation,))}
        assert rows == {(made.venture, MONDAY.isoformat())}
        tables = {row[0] for row in store.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert not tables & {"week_books", "book_holders", "reading_baselines"}
    finally:
        store.close()


def test_reservations_on_two_grids_each_count_only_in_the_weeks_their_spans_overlap(tmp_path):
    store = OperatorStore(tmp_path / "operator.db", clock=_Clock())
    try:
        monday = _codex(store)
        done = monday.reserve("codex", MONDAY + timedelta(hours=2), 100)
        _settle_at(monday, done, 100, MONDAY + timedelta(hours=3))            # over before any Tuesday week began
        still_open = monday.reserve("codex", MONDAY + timedelta(days=2), 50)        # open: it counts wherever it may send
        tuesday = _codex(store, resets_at=MONDAY + timedelta(days=8))
        now = MONDAY + timedelta(days=3, hours=1)
        assert tuesday.reading("codex", now).used == 50
        later = tuesday.reserve("codex", now, 350)
        assert later.admitted and tuesday.reading("codex", now).used == 400
        assert tuesday.reading("codex", MONDAY + timedelta(days=8, hours=1)).used == 400     # both still open
        _settle_at(tuesday, still_open, 50, MONDAY + timedelta(days=2, hours=1))
        _settle_at(tuesday, later, 350, now + timedelta(hours=1))
        assert tuesday.reading("codex", now).used == 400
        assert tuesday.reading("codex", MONDAY + timedelta(days=8, hours=1)).used == 0    # nothing counts twice
    finally:
        store.close()


def test_a_settlement_on_another_connection_during_a_reading_cannot_set_its_baseline(tmp_path, monkeypatch):
    """What review-fu3's finding 2 held: a settlement between two of a reading's reads made up its baseline. A reading is one
    read now, and a charge is in a reading's figure by its own time alone."""
    now, path = MONDAY + timedelta(days=3), tmp_path / "op.db"
    first, second = OperatorStore(path, clock=_Clock()), OperatorStore(path, clock=_Clock())
    try:
        worker = _codex(second)
        pending = worker.reserve("codex", now, 200)
        account = AccountReading(used=500, read_at=now, estimated=False)
        reader = BudgetMeter((ProviderWindow("codex", 1000, MONDAY),), store=first, read_account=lambda *_: account)
        grid = reader.scope.kept_grid                       # the first of a reading's reads

        def settled_in_between(provider):
            _settle_at(worker, pending, 0, now + timedelta(seconds=1))
            return grid(provider)

        monkeypatch.setattr(reader.scope, "kept_grid", settled_in_between)
        reader.reading("codex", now)
        monkeypatch.setattr(reader.scope, "kept_grid", grid)
        spend = worker.reserve("codex", now + timedelta(seconds=2), 200)
        _settle_at(worker, spend, 200, now + timedelta(seconds=3))
        restarted = BudgetMeter((ProviderWindow("codex", 1000, MONDAY),), store=second, read_account=lambda *_: account)
        later = now + timedelta(seconds=4)
        assert restarted.reading("codex", later).used == 700 and not restarted.reserve("codex", later, 300).admitted
    finally:
        first.close()
        second.close()


def test_a_reading_dated_in_any_zone_is_compared_as_an_instant():
    readings = [None]
    meter = _meter(read_account=lambda *_: readings[0])
    _settle_at(meter, meter.reserve("claude", _day(3) - timedelta(minutes=5), 200), 200, _day(3))
    readings[0] = AccountReading(used=300, read_at=(_day(3) - timedelta(microseconds=1)).astimezone(BRISBANE))
    assert meter.reading("claude", _day(3)).used == 500
    readings[0] = AccountReading(used=300, read_at=_day(3).astimezone(BRISBANE))
    assert meter.reading("claude", _day(3)).used == 300


def test_an_expired_figure_is_left_out_and_its_reset_and_limit_stand():
    reset, until = _day(3), _day(3) + timedelta(hours=2)
    reading = AccountReading(used=900, estimated=False, read_at=_day(2), limited_until=until, resets_at=reset)
    assert standing(reading, reset - timedelta(microseconds=1)) is reading
    gone = standing(reading, reset)
    assert (gone.used, gone.read_at, gone.resets_at, gone.limited_until) == (0, None, reset, until)
    assert standing(None, reset) is None


# A folder kept before reservations carried spans --------------------------------------------------------------------------

_OLD_TABLES = """
CREATE TABLE IF NOT EXISTS week_books (owner TEXT NOT NULL, provider TEXT NOT NULL, week TEXT NOT NULL, book TEXT NOT NULL,
    PRIMARY KEY (owner, provider, week));
CREATE TABLE IF NOT EXISTS book_holders (owner TEXT NOT NULL, provider TEXT NOT NULL, book TEXT NOT NULL, week TEXT NOT NULL,
    PRIMARY KEY (owner, provider, book));
"""


def _old_folder(path, books, *, provider="codex", moves=(), holders=(), grid=None) -> list[tuple[str, str]]:
    """A folder as the book code before spans left it: each ``(book, amount)`` a reservation under a venture named for its
    week as it was written, with no span; ``moves`` the ``(week, book)`` rows that gave a later week a book, ``holders`` the
    ``(book, week)`` rows of where a book went last, and ``grid`` a moment the provider's weeks end."""
    store = OperatorStore(path, clock=_Clock())
    try:
        store.conn.executescript(_OLD_TABLES)
        scope = store.for_owner(OWNER)
        made = []
        for book, amount in books:
            venture = scope.venture_named(f"{provider}@{book}", caps={"model": 1000, "cash": 0})
            made.append((venture, scope.reserve(venture, month=book, account="model", amount=amount)))
        with store.conn:
            store.conn.executemany("INSERT INTO week_books VALUES (?, ?, ?, ?)", [(OWNER, provider, *row) for row in moves])
            store.conn.executemany("INSERT INTO book_holders VALUES (?, ?, ?, ?)", [(OWNER, provider, *row) for row in holders])
            if grid is not None:
                store.conn.execute("INSERT OR REPLACE INTO week_grids (owner, provider, reset) VALUES (?, ?, ?)",
                                   (OWNER, provider, grid))
        return made
    finally:
        store.close()


def test_an_old_week_that_went_monday_to_tuesday_and_back_before_holders_keeps_its_reservation(tmp_path):
    """Review-fx4's finding 2: with no holder row, the old code's fallback read the last move, Tuesday, while the grid had
    gone back to Monday, so the next move lost the book."""
    start = datetime(2026, 10, 26, tzinfo=BRISBANE)
    moved, now, path = start + timedelta(days=1), (start + timedelta(days=4, hours=1)).astimezone(UTC), tmp_path / "op.db"
    _old_folder(path, [(start.isoformat(), 350)], moves=[(moved.isoformat(), start.isoformat())],
                grid=(start + timedelta(days=7)).isoformat())
    store = OperatorStore(path, clock=_Clock())
    try:
        meter = BudgetMeter((ProviderWindow("codex", 1000, start),), store=store,
                            read_account=lambda *_: AccountReading(resets_at=start + timedelta(days=9)))
        assert (meter.reading("codex", now).used, meter.reserve("codex", now, 300).admitted) == (350, False)
    finally:
        store.close()


def test_an_old_book_counts_in_every_week_that_held_it_and_in_none_after(tmp_path):
    start = datetime(2026, 10, 26, tzinfo=BRISBANE)
    moved, path = start + timedelta(days=1), tmp_path / "op.db"
    [(venture, reservation)] = _old_folder(path, [(start.isoformat(), 350)], holders=[(start.isoformat(), moved.isoformat())])
    store = OperatorStore(path, clock=_Clock())
    try:
        _codex(store, start=start).reading("codex", start + timedelta(days=2))     # read into its span
        store.clock.__wrapped__.now = moved + timedelta(days=7) - timedelta(hours=1)   # the store's own clock dates it
        store.for_owner(OWNER).settle(venture, reservation, metered=350)
        for days, used in ((9, 350), (14, 350), (15, 0)):     # clear from the end of the last week that held it
            meter = _codex(store, start=start, resets_at=start + timedelta(days=days))
            assert meter.reading("codex", start + timedelta(days=days) - timedelta(hours=1)).used == used
    finally:
        store.close()


def test_an_old_charge_is_dated_a_second_after_the_ledger_wrote_it(tmp_path):
    now = datetime.now(UTC)
    window, path = ProviderWindow("codex", 1000, now - timedelta(days=3)), tmp_path / "op.db"
    [(venture, reservation)] = _old_folder(path, [(window.place(now)[0].isoformat(), 200)])
    store = OperatorStore(path, clock=_Clock())
    try:
        store.for_owner(OWNER).settle(venture, reservation, metered=200)     # as the old meter settled: with no time
    finally:
        store.close()
    store = OperatorStore(path, clock=_Clock())
    try:
        later = now + timedelta(seconds=30)
        after = BudgetMeter((window,), store=store, read_account=lambda *_: AccountReading(used=500, read_at=later))
        assert after.reading("codex", later).used == 500
        before = BudgetMeter((window,), store=store,
                             read_account=lambda *_: AccountReading(used=500, read_at=now - timedelta(minutes=1)))
        assert before.reading("codex", later).used == 700
    finally:
        store.close()


def test_old_rows_are_read_into_spans_once(tmp_path):
    path = tmp_path / "op.db"
    _old_folder(path, [(MONDAY.isoformat(), 350)])
    for _ in range(2):
        store = OperatorStore(path, clock=_Clock())
        try:
            assert _codex(store).reading("codex", MONDAY + timedelta(days=2)).used == 350
            assert store.conn.execute("SELECT COUNT(*) FROM week_spans").fetchone()[0] == 1
        finally:
            store.close()


@pytest.mark.parametrize("old", [
    {"books": [("soon", 350)]},
    {"books": [("2026-10-26T00:00:00", 350)]},                                   # no zone: another machine reads it otherwise
    {"books": [(MONDAY.isoformat(), 350)], "moves": [("garbled", MONDAY.isoformat())]},
    {"books": [(MONDAY.isoformat(), 350)], "holders": [(MONDAY.isoformat(), "2026-10-27")]},
])
def test_an_old_row_that_cannot_be_read_is_a_refusal_and_never_a_fresh_book(tmp_path, old):
    path = tmp_path / "op.db"
    _old_folder(path, **old)
    _old_folder(path, [(MONDAY.isoformat(), 100)], provider="claude")
    store = OperatorStore(path, clock=_Clock())
    try:
        windows = (ProviderWindow("codex", 1000, MONDAY), ProviderWindow("claude", 1000, MONDAY))
        meter, now = BudgetMeter(windows, store=store), MONDAY + timedelta(days=2)
        refused = meter.reserve("codex", now, 1)
        assert not refused.admitted and "cannot all be read" in refused.reason
        assert meter.reading("codex", now).reason == refused.reason
        assert meter.reading("claude", now).used == 100 and meter.reserve("claude", now, 1).admitted
        assert BudgetMeter(windows, store=store).reserve("codex", now, 1).reason == refused.reason     # and after a restart
    finally:
        store.close()


def test_a_grid_that_cannot_be_read_is_a_refusal(tmp_path):
    store = OperatorStore(tmp_path / "op.db", clock=_Clock())
    try:
        with store.conn:
            store.conn.execute("INSERT INTO week_grids (owner, provider, reset) VALUES (?, 'codex', 'garbled')", (OWNER,))
        refused = _codex(store).reserve("codex", MONDAY + timedelta(days=2), 1)
        assert not refused.admitted and "cannot be read" in refused.reason
    finally:
        store.close()


def test_a_span_that_is_not_one_is_refused_and_a_kept_span_never_changes(tmp_path):
    store = OperatorStore(tmp_path / "op.db", clock=_Clock())
    try:
        meter = _codex(store)
        _settle_at(meter, meter.reserve("codex", MONDAY + timedelta(days=2), 350), 200, MONDAY + timedelta(days=2, minutes=1))
        for bad in (("a", OWNER, "codex", 0, "soon", 10), ("b", OWNER, "codex", 0, 10, 10), ("c", OWNER, "codex", 0, 1.5, 10)):
            with pytest.raises(sqlite3.IntegrityError):
                store.conn.execute("INSERT INTO week_spans VALUES (?, ?, ?, ?, ?, ?)", bad)
        for sql in ("UPDATE week_spans SET end_us = start_us + 1", "DELETE FROM week_spans", "DELETE FROM week_settled",
                    "INSERT OR REPLACE INTO week_spans SELECT ref, owner, provider, week_us, start_us, start_us + 1 FROM week_spans"):
            with pytest.raises(sqlite3.IntegrityError):
                store.conn.execute(sql)
        store.conn.rollback()
        assert _codex(store).reading("codex", MONDAY + timedelta(days=2)).used == 200
    finally:
        store.close()


# A stop between any two writes ---------------------------------------------------------------------------------------------

_DIES = '''
import os, sys
from datetime import UTC, datetime, timedelta
from g6.operator.store import OperatorStore
from g6.self_maintenance.pacing import OWNER, AccountReading, BudgetMeter, ProviderWindow

class Dies:
    """The store's connection, until the statement that holds ``text`` for the ``at``-th time: the process ends there."""
    def __init__(self, conn, text, at=1):
        self.conn, self.text, self.left = conn, text, at
    def __getattr__(self, name):
        return getattr(self.conn, name)
    def __enter__(self):
        return self.conn.__enter__()
    def __exit__(self, *exc):
        return self.conn.__exit__(*exc)
    def execute(self, sql, *args):
        if self.text in sql:
            self.left -= 1
            if not self.left:
                os._exit(73)
        return self.conn.execute(sql, *args)

MONDAY = datetime(2026, 10, 26, tzinfo=UTC)
NOW = MONDAY + timedelta(days=2)
store, step = OperatorStore(sys.argv[1]), sys.argv[2]
if step == "adopting":
    scope = store.for_owner(OWNER)
    scope.conn = Dies(scope.conn, "INSERT INTO week_spans", at=2)
    scope.adopt_books(("codex",))
read = (lambda *_: AccountReading(resets_at=MONDAY + timedelta(days=8))) if step == "grid kept" else None
meter = BudgetMeter((ProviderWindow("codex", 1000, MONDAY),), store=store, read_account=read)
if step == "reserving":
    meter.scope.conn = Dies(meter.scope.conn, "INSERT INTO week_spans")
    meter.reserve("codex", NOW, 350)
elif step == "settling":
    spend = meter.reserve("codex", NOW, 350)
    meter.scope.conn = Dies(meter.scope.conn, "INSERT INTO week_settled")
    meter.settle(spend, 10)
elif step == "grid kept":
    meter.scope.conn = Dies(meter.scope.conn, "FROM week_spans")
    meter.reading("codex", NOW)
'''


@pytest.mark.parametrize("step, used", [("reserving", 0), ("settling", 350), ("grid kept", 350), ("adopting", 450)])
def test_a_stop_between_two_writes_leaves_every_reservation_counted_once(tmp_path, step, used):
    path = tmp_path / "op.db"
    if step == "grid kept":
        store = OperatorStore(path, clock=_Clock())
        try:
            assert _codex(store).reserve("codex", MONDAY + timedelta(days=2), 350).admitted
        finally:
            store.close()
    if step == "adopting":
        _old_folder(path, [(MONDAY.isoformat(), 350), (MONDAY.isoformat(), 100)])
    ended = subprocess.run([sys.executable, "-c", _DIES, str(path), step], cwd=tmp_path, capture_output=True, text=True,
                           timeout=60, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert ended.returncode == 73, ended.stderr
    store = OperatorStore(path, clock=_Clock())
    try:
        if step == "adopting":
            assert store.conn.execute("SELECT COUNT(*) FROM week_spans").fetchone()[0] == 0     # as the old code left them
        meter = _codex(store)
        assert meter.reading("codex", MONDAY + timedelta(days=2)).used == used
        assert store.conn.execute("SELECT COUNT(*) FROM ledger WHERE kind = 'release'").fetchone()[0] == 0
    finally:
        store.close()

# Review wks1: the six findings of the first review of the week spans -------------------------------------------------------

def test_untimed_charge_goes_on_top_of_an_undated_reading():
    """Finding 1: a reading with no time cannot say which charges it already holds, so every one goes on top of it."""
    start = datetime(2026, 10, 26, tzinfo=UTC)
    now = start + timedelta(days=6, hours=1)
    reading = [None]
    store = OperatorStore(":memory:", clock=_Clock())
    try:
        meter = BudgetMeter((ProviderWindow("codex", 1000, start),), store=store, read_account=lambda *_: reading[0])
        spend = meter.reserve("codex", now, 300)
        assert spend.admitted
        meter.settle(spend, 300)
        reading[0] = AccountReading(used=700, resets_at=start + timedelta(days=7))
        admitted = meter.reserve("codex", now, 200)
        assert not admitted.admitted, "700 reported + 300 untimed + 200 newly admitted exceeds 1000"
    finally:
        store.close()


def test_grid_change_between_look_and_reserve_cannot_admit_on_obsolete_week(tmp_path, monkeypatch):
    """Finding 2: the week was chosen before the transaction that reserved, so a grid kept by another connection in
    between was never read again."""
    monday = datetime(2026, 10, 26, tzinfo=UTC)
    now = monday + timedelta(days=6, hours=1)
    path = tmp_path / "operator.db"
    first, second = OperatorStore(path, clock=_Clock()), OperatorStore(path, clock=_Clock())
    try:
        window = ProviderWindow("codex", 1000, monday)
        authoritative = BudgetMeter((window,), store=second)
        earlier = monday + timedelta(days=2)
        spend = authoritative.reserve("codex", earlier, 500)
        assert spend.admitted
        _settle_at(authoritative, spend, 500, earlier + timedelta(minutes=1))
        authoritative.scope.keep_grid("codex", monday + timedelta(days=10))      # Thursday weeks leave Wednesday's out
        stale = BudgetMeter((window,), store=first)
        assert stale.reading("codex", now).used == 0
        authoritative._read_account = lambda *_: AccountReading(resets_at=monday + timedelta(days=7))
        reserve_span = stale.scope.reserve_span

        def changed_before_transaction(*args, **kwargs):
            assert authoritative.reading("codex", now).used == 500             # Monday weeks again, from another connection
            return reserve_span(*args, **kwargs)

        monkeypatch.setattr(stale.scope, "reserve_span", changed_before_transaction)
        admitted = stale.reserve("codex", now, 700)
        assert not admitted.admitted, "grid changed before BEGIN IMMEDIATE: 500 + 700 admitted against a 1000 week"
    finally:
        first.close()
        second.close()


def test_reading_does_not_expire_before_reset_during_repeated_hour():
    """Finding 3: two times in one zone compare by their wall clocks, which repeat in the hour a clock goes back."""
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("America/New_York")
    now = datetime(2026, 11, 1, 1, 45, tzinfo=zone, fold=0)          # 45 minutes before the second 01:30
    reset = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1)
    assert now.astimezone(UTC) < reset.astimezone(UTC)
    reading = AccountReading(used=950, estimated=False, resets_at=reset)
    assert standing(reading, now.astimezone(UTC)).used == 950
    assert standing(reading, now).used == 950, "same instant in local time discards 950 usage before the reset"


def test_settlement_before_reservation_cannot_put_later_spend_in_earlier_reading():
    """Finding 5: a settle time before the reservation began put its charge inside a reading taken before it."""
    start = datetime(2026, 10, 26, tzinfo=UTC)
    read_at = start + timedelta(days=6)
    opened = read_at + timedelta(hours=1)
    store = OperatorStore(":memory:", clock=_Clock())
    try:
        meter = BudgetMeter((ProviderWindow("codex", 1000, start),), store=store,
                            read_account=lambda *_: AccountReading(used=500, read_at=read_at))
        spend = meter.reserve("codex", opened, 300)
        assert spend.admitted
        with pytest.raises(ValueError):
            meter.settle(spend, 300, read_at - timedelta(hours=1))
        admitted = meter.reserve("codex", opened + timedelta(minutes=1), 300)
        assert not admitted.admitted, "charge begun after the reading was absorbed into it via an impossible settle time"
    finally:
        store.close()


def test_request_finishing_after_reset_is_counted_in_the_week_it_overlaps():
    """Finding 6: a settled span was cut at the end of the week it was made in, so a request in flight across the reset
    was charged to no week after it."""
    start = datetime(2026, 10, 26, tzinfo=UTC)
    reset = start + timedelta(days=7)
    store = OperatorStore(":memory:", clock=_Clock())
    try:
        meter = BudgetMeter((ProviderWindow("codex", 1000, start),), store=store)
        spend = meter.reserve("codex", reset - timedelta(minutes=1), 1000)
        assert spend.admitted
        _settle_at(meter, spend, 1000, reset + timedelta(minutes=1))
        admitted = meter.reserve("codex", reset + timedelta(minutes=2), 200)
        assert not admitted.admitted, "settled span crosses reset but its 1000 charge disappears from the new week"
    finally:
        store.close()

def test_a_reservation_a_dead_tick_left_open_is_settled_in_full_and_then_stops_counting():
    """Open, it would count in every week to come (finding 6's ruling), so the next tick settles it, in full, on the
    store's clock: charged its worst case once, in the weeks it ran through."""
    clock = [_day(5)]
    store = OperatorStore(":memory:", clock=lambda: clock[0])
    meter = BudgetMeter((ProviderWindow("claude", 1000, START),), store=store)
    assert meter.reserve("claude", _day(5), 300).admitted          # its tick died before it settled
    assert meter.reading("claude", _day(15)).used == 300
    clock[0] = _day(6)
    assert meter.settle_open("claude") == 300 and meter.settle_open("claude") == 0
    assert meter.reading("claude", _day(6)).used == 300 and meter.reading("claude", _day(8)).used == 0


def test_the_store_dates_a_settlement_by_its_own_clock_and_never_before_the_reservation_began():
    """Review wks1, finding 5: the time a settlement is dated by decides where its charge counts, so it is the store's."""
    clock = [_day(3)]
    store = OperatorStore(":memory:", clock=lambda: clock[0])
    meter = BudgetMeter((ProviderWindow("claude", 1000, START),), store=store)
    early = meter.reserve("claude", _day(3), 100)
    clock[0] = _day(2)                                              # a clock that went back
    meter.settle(early, 100)
    held = store.for_owner("g6-loop").week_held("claude", "model", START, START + timedelta(days=7))
    assert [h.settled for h in held] == [_day(3)]                  # held to the reservation's start

# Review wks2: the six findings of the second review of the week spans -----------------------------------------------------

def test_caller_cannot_backdate_charge_into_readings_baseline():
    """Finding 1: a settle time a caller gave decided where the charge counted; now only the store's clock dates it."""
    start = datetime(2026, 10, 26, tzinfo=UTC)
    clock = [start + timedelta(days=6, hours=1)]
    reading = [None]
    store = OperatorStore(":memory:", clock=lambda: clock[0])
    try:
        meter = BudgetMeter((ProviderWindow("codex", 1000, start),), store=store, read_account=lambda *_: reading[0])
        spend = meter.reserve("codex", clock[0], 300)
        assert spend.admitted
        alleged_end = clock[0] + timedelta(minutes=1)
        clock[0] += timedelta(minutes=2)
        reading[0] = AccountReading(used=600, estimated=False, read_at=clock[0], resets_at=start + timedelta(days=7))
        clock[0] += timedelta(minutes=1)
        with pytest.raises(ValueError, match="store's own clock"):
            meter.settle(spend, 300, alleged_end)
        meter.settle(spend, 300)                                     # dated now, after the reading
        got = meter.reserve("codex", clock[0], 200)
        assert not got.admitted, f"600 read + 300 settled after read + 200 exceeds 1000; meter used {got.used}"
    finally:
        store.close()


@pytest.mark.parametrize("shared", [True, False])
def test_concurrent_admissions_on_one_store_or_one_file_do_not_share_a_transaction(tmp_path, shared):
    """Finding 2: the transaction's depth belonged to the store, so a second thread on it joined the first's."""
    start = datetime(2026, 10, 26, tzinfo=UTC)
    now = start + timedelta(days=1, hours=1)
    stores = [OperatorStore(tmp_path / "operator.db", clock=lambda: now)]
    stores.append(stores[0] if shared else OperatorStore(tmp_path / "operator.db", clock=lambda: now))
    read, resume = threading.Event(), threading.Event()
    outcomes, errors, workers = [], [], []
    try:
        window = ProviderWindow("codex", 1000, start)
        first, second = BudgetMeter((window,), store=stores[0]), BudgetMeter((window,), store=stores[1])
        look = first._look

        def paused_look(*args):
            result = look(*args)
            read.set()
            assert resume.wait(5)
            return result

        first._look = paused_look

        def reserve(meter):
            try:
                outcomes.append(meter.reserve("codex", now, 300))
            except BaseException as exc:
                errors.append(exc)

        workers.append(threading.Thread(target=reserve, args=(first,)))
        workers[0].start()
        assert read.wait(5)
        workers.append(threading.Thread(target=reserve, args=(second,)))
        workers[1].start()
        workers[1].join(0.3)
        assert workers[1].is_alive()                                 # the second waits for the first to commit
    finally:
        resume.set()
        for worker in workers:
            worker.join(10)
        for store in {id(s): s for s in stores}.values():
            store.close()
    assert not errors and all(not w.is_alive() for w in workers), errors
    assert sorted(o.admitted for o in outcomes) == [False, True]
    assert sum(o.amount for o in outcomes if o.admitted) == 300


def test_existing_meter_counts_legacy_reservation_written_after_it_opened(tmp_path):
    """Finding 6: the operator's own reserve still writes a reservation with no span, and adoption ran only when a meter
    opened; now admission reads every such row into its span inside its own transaction."""
    start = datetime(2026, 10, 26, tzinfo=UTC)
    now = start + timedelta(days=2, hours=1)
    path = tmp_path / "operator.db"
    store, other = OperatorStore(path, clock=lambda: now), OperatorStore(path, clock=lambda: now)
    try:
        meter = BudgetMeter((ProviderWindow("codex", 1000, start),), store=store)
        assert meter.reading("codex", now).used == 0
        scope = other.for_owner(OWNER)
        venture = scope.venture_named(f"codex@{start.isoformat()}", caps={"model": 1000, "cash": 0})
        scope.reserve(venture, month=start.isoformat(), account="model", amount=350)
        got = meter.reserve("codex", now, 350)
        assert not got.admitted and got.used == 350, f"350 outstanding + 350 new exceeds 600, but used {got.used}"
    finally:
        store.close()
        other.close()

# Review wks3: the four findings of the third review of the week spans ------------------------------------------------------

def test_another_thread_cannot_commit_an_admissions_transaction(tmp_path, monkeypatch):
    """Finding 1: the operator's ``record`` committed the shared connection outside the store's lock, and so committed
    another thread's admission half way, before its check was done."""
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as Waited

    start = datetime(2026, 11, 1, tzinfo=UTC)
    now = start + timedelta(days=6, hours=1)
    path = tmp_path / "operator.db"
    first, second = OperatorStore(path, clock=lambda: now), OperatorStore(path, clock=lambda: now)
    paused, resume = threading.Event(), threading.Event()
    try:
        window = ProviderWindow("codex", 100, start, reserve_share=0)
        a, b = BudgetMeter((window,), store=first), BudgetMeter((window,), store=second)
        unrelated = first.for_owner("another-owner")
        venture = unrelated.create_venture("independent", caps={"model": 100, "cash": 0})["venture_id"]
        append = a.scope._append_ledger

        def pause_before_write(*args):
            if args[3] == "reserve":
                paused.set()
                assert resume.wait(4)
            return append(*args)

        monkeypatch.setattr(a.scope, "_append_ledger", pause_before_write)
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(a.reserve, "codex", now, 70)
            assert paused.wait(4)
            note = pool.submit(unrelated.record, venture, "note")
            with pytest.raises(Waited):
                note.result(timeout=1)                               # it waits for the admission's transaction
            resume.set()
            admitted = pending.result(timeout=4)
            note.result(timeout=4)
        competing = b.reserve("codex", now, 70)
        assert admitted.admitted and not competing.admitted, "both 70s were admitted under a 100 ceiling"
    finally:
        resume.set()
        first.close()
        second.close()


@pytest.mark.parametrize("amount", ["unreadable", b"\x00", float("inf")])
def test_unreadable_old_amount_refuses_admission_after_restart(tmp_path, amount):
    """Finding 3: an old reservation's amount was never read as one; a text the schema let in summed as zero."""
    start = datetime(2026, 11, 1, tzinfo=UTC)
    now = start + timedelta(days=6)
    path = tmp_path / "operator.db"
    store = OperatorStore(path, clock=lambda: now)
    try:
        venture = store.for_owner(OWNER).create_venture(f"codex@{start.isoformat()}", caps={"model": 100, "cash": 0})
        with store.conn:
            store.conn.execute("INSERT INTO ledger (venture_id, month, account, kind, amount, ref, at)"
                               " VALUES (?, ?, 'model', 'reserve', ?, 'old-reservation', ?)",
                               (venture["venture_id"], start.isoformat(), amount, now.isoformat()))
    finally:
        store.close()
    store = OperatorStore(path, clock=lambda: now)
    try:
        meter = BudgetMeter((ProviderWindow("codex", 100, start, reserve_share=0),), store=store)
        admission = meter.reserve("codex", now, 100)
        assert not admission.admitted and "cannot all be read" in admission.reason, admission.reason
    finally:
        store.close()


@pytest.mark.parametrize("amount", ["unreadable", b"\x00"])
def test_an_unreadable_amount_on_a_kept_span_refuses_and_never_reads_as_zero(tmp_path, amount):
    start = datetime(2026, 11, 1, tzinfo=UTC)
    now = start + timedelta(days=6)
    store = OperatorStore(tmp_path / "operator.db", clock=lambda: now)
    try:
        meter = BudgetMeter((ProviderWindow("codex", 100, start, reserve_share=0),), store=store)
        spend = meter.reserve("codex", now, 10)
        with store.conn:                                             # a row written by hand, as the ledger cannot change
            store.conn.execute("INSERT INTO ledger (venture_id, month, account, kind, amount, ref, at)"
                               " VALUES (?, ?, 'model', 'charge', ?, ?, ?)",
                               (spend.venture, start.isoformat(), amount, spend.reservation, now.isoformat()))
        refused = meter.reserve("codex", now, 1)
        assert not refused.admitted and "cannot be read" in refused.reason, refused.reason
    finally:
        store.close()


def test_older_cached_reading_cannot_undo_newer_provider_grid(tmp_path):
    """Finding 4: a reading put its reset in the store whatever its age, so an older one undid a newer grid."""
    from g6.self_maintenance.usage import AccountReader

    start = datetime(2026, 10, 25, tzinfo=UTC)
    clock = [start + timedelta(days=6)]
    path = tmp_path / "operator.db"
    first, second = OperatorStore(path, clock=lambda: clock[0]), OperatorStore(path, clock=lambda: clock[0])
    try:
        window = ProviderWindow("codex", 100, start, reserve_share=0)
        initial = BudgetMeter((window,), store=first)
        previous = initial.reserve("codex", clock[0], 90)
        assert previous.admitted
        clock[0] += timedelta(seconds=1)
        initial.settle(previous, 90)

        def reply(percent, reset):
            return {"rateLimits": {"primary": {"usedPercent": percent, "windowDurationMins": 10080,
                                                "resetsAt": reset.timestamp()}}}

        clock[0] = start + timedelta(days=12)
        old_reset = start + timedelta(days=14)
        old_reader = AccountReader((window,), ask={"codex": lambda: reply(0, old_reset)})
        old_reader.refresh(clock[0])
        stale = BudgetMeter((window,), store=first, read_account=old_reader)
        assert stale.reading("codex", clock[0]).used == 0
        clock[0] += timedelta(minutes=1)
        new_reset = old_reset - timedelta(days=1)
        new_reader = AccountReader((window,), ask={"codex": lambda: reply(90, new_reset)})
        new_reader.refresh(clock[0])
        authoritative = BudgetMeter((window,), store=second, read_account=new_reader)
        assert authoritative.reading("codex", clock[0]).used == 90
        assert authoritative.scope.week_grid("codex") == new_reset
        clock[0] += timedelta(minutes=1)
        admission = stale.reserve("codex", clock[0], 60)
        assert not admission.admitted, "older cached reading undid the newer grid and admitted 90 + 60 against 100"
        assert stale.scope.week_grid("codex") == new_reset                  # and left the newer grid as it was
    finally:
        first.close()
        second.close()


def test_a_tick_reservation_whose_amount_cannot_be_read_is_not_recovered_and_refuses(tmp_path):
    """Recovery reads each amount it settles as one: one that does not read leaves every open reservation as it was, and
    admission refuses on the provider; it is never settled as zero, nor does recovery end the tick."""
    store = OperatorStore(tmp_path / "operator.db", clock=_Clock())
    try:
        meter, now = _codex(store), MONDAY + timedelta(days=2)
        spend = meter.reserve("codex", now, 10)
        span = store.conn.execute("SELECT week_us, start_us, end_us FROM week_spans WHERE ref=?", (spend.reservation,)).fetchone()
        month = store.conn.execute("SELECT month FROM ledger WHERE ref=?", (spend.reservation,)).fetchone()["month"]
        with store.conn:                                             # a row written by hand, as the ledger cannot change
            store.conn.execute("INSERT INTO ledger (venture_id, month, account, kind, amount, ref, at)"
                               " VALUES (?, ?, 'model', 'reserve', 'unreadable', 'by-hand', ?)",
                               (spend.venture, month, now.isoformat()))
            store.conn.execute("INSERT INTO week_spans VALUES ('by-hand', ?, 'codex', ?, ?, ?)", (OWNER, *span))
            store.conn.execute("INSERT INTO week_ticks VALUES ('by-hand', 'a tick that is gone')")
        assert meter.recover("codex") == 0.0
        assert store.conn.execute("SELECT COUNT(*) FROM ledger WHERE kind='release'").fetchone()[0] == 0
        refused = meter.reserve("codex", now, 1)
        assert not refused.admitted and "cannot be read" in refused.reason, refused.reason
    finally:
        store.close()


def test_a_grid_row_that_cannot_be_read_refuses_once_and_a_reading_replaces_it(tmp_path):
    store = OperatorStore(tmp_path / "op.db", clock=_Clock())
    try:
        with store.conn:
            store.conn.execute("INSERT INTO week_grids (owner, provider, reset) VALUES (?, 'codex', 'garbled')", (OWNER,))
        now, reset = MONDAY + timedelta(days=2), MONDAY + timedelta(days=7)
        meter = BudgetMeter((ProviderWindow("codex", 1000, MONDAY),), store=store,
                            read_account=lambda *_: AccountReading(used=0, read_at=now, resets_at=reset))
        refused = meter.reserve("codex", now, 1)
        assert not refused.admitted and "cannot be read" in refused.reason, refused.reason
        assert store.for_owner(OWNER).kept_grid("codex") == (reset, now)
        assert meter.reserve("codex", now, 1).admitted
    finally:
        store.close()
