# SPDX-License-Identifier: Apache-2.0
"""The loop's budget (design 6.6): weekly provider windows, paced, on the operator's one ledger."""
from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta

import pytest

from g6.operator.store import OperatorStore
from g6.self_maintenance.pacing import (
    SETTINGS_FILE,
    AccountReading,
    BudgetMeter,
    ProviderWindow,
    ceiling,
    read_settings,
    target,
    windows_from,
)

START = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)


def _day(day: int) -> datetime:
    return START + timedelta(days=day - 1, hours=1)


def _meter(allowance: float = 1000, *, read_account=None, reserve_share: float = 0.2) -> BudgetMeter:
    window = ProviderWindow("claude", allowance, START, reserve_share=reserve_share)
    return BudgetMeter((window,), store=OperatorStore(":memory:"), read_account=read_account)


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
    meter = _meter(read_account=lambda provider, start: AccountReading(used=10))
    meter.settle(meter.reserve("claude", _day(3), 300), 300)
    assert meter.reading("claude", _day(3)).used == 300


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
                        store=OperatorStore(":memory:"))
    meter.limit_reached("claude", _day(5))
    assert meter.reserve("codex", _day(2), 1).admitted


def test_a_new_week_starts_empty():
    meter = _meter()
    assert meter.reserve("claude", _day(5), 800).admitted
    assert not meter.reserve("claude", _day(5), 1).admitted
    assert meter.reserve("claude", _day(8), 200).admitted


def test_each_provider_has_its_own_week():
    windows = (ProviderWindow("claude", 100, START), ProviderWindow("codex", 1000, START + timedelta(days=3)))
    meter = BudgetMeter(windows, store=OperatorStore(":memory:"))
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
    meter = _meter(read_account=lambda provider, start: AccountReading(used=350))
    meter.settle(meter.reserve("claude", _day(3), 200), 100)   # settled: the account's 350 may include it
    assert meter.reading("claude", _day(3)).used == 350


def test_7_with_no_account_reading_the_reservation_still_counts_once():
    meter = _meter()
    meter.settle(meter.reserve("claude", _day(3), 100), 40)
    meter.reserve("claude", _day(3), 200)
    assert meter.reading("claude", _day(3)).used == 240


def test_7_outstanding_is_reserved_less_released_and_a_settled_week_has_none():
    scope = OperatorStore(":memory:").for_owner("o")
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
    quiet = _meter(read_account=lambda provider, start: AccountReading(used=10, estimated=True))
    quiet.settle(quiet.reserve("claude", _day(3), 100), 100)
    assert not quiet.reading("claude", _day(3)).estimated

# Finding 8: the ceiling is checked in the transaction that reserves --------------------------------------------------------

def _shared_meters(path, count: int, allowance: float = 1000, read_account=None) -> list[BudgetMeter]:
    return [BudgetMeter((ProviderWindow("claude", allowance, START),), store=OperatorStore(path), read_account=read_account)
            for _ in range(count)]


def test_8_two_admissions_that_each_read_room_cannot_both_pass_the_ceiling(tmp_path):
    path = tmp_path / "operator.db"
    late: list = []

    meter, rival = _shared_meters(path, 2)
    read = meter._look

    def another_tick_gets_in_first(provider, now):
        seen = read(provider, now)
        if not late:
            late.append(rival.reserve("claude", _day(2), 300))  # after this one has read its room, before it writes
        return seen

    meter._look = another_tick_gets_in_first
    got = meter.reserve("claude", _day(2), 300)                # 300 + 300 is over day two's 400
    assert late[0].admitted and not got.admitted and "ceiling" in got.reason
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
    meter.settle(meter.reserve("claude", _day(3), 200), 100)
    readings[0] = _dated(350, 1)
    assert meter.reading("claude", _day(3)).used == 350


def test_a_fresh_reading_takes_over_from_what_was_added_to_the_last():
    readings = [_dated(350, 1)]
    meter = _meter(read_account=lambda provider, start: readings[0])
    meter.reading("claude", _day(3))
    meter.settle(meter.reserve("claude", _day(3), 100), 100)
    assert meter.reading("claude", _day(3)).used == 450
    readings[0] = _dated(470, 2)
    assert meter.reading("claude", _day(3)).used == 470


def test_the_check_inside_the_transaction_counts_a_charge_since_the_reading():
    reading = _dated(350, 1)
    meter, rival = _shared_meters_with(reading)
    assert meter.reading("claude", _day(3)).used == 350
    rival.settle(rival.reserve("claude", _day(3), 100), 100)         # another handle, on the same ledger
    assert meter.reading("claude", _day(3)).used == 450
    assert not meter.reserve("claude", _day(3), 151).admitted        # 450 + 151 is over day three's 600
    assert meter.reserve("claude", _day(3), 150).admitted


def _shared_meters_with(reading: AccountReading) -> list[BudgetMeter]:
    store = OperatorStore(":memory:")
    return [BudgetMeter((ProviderWindow("claude", 1000, START),), store=store, read_account=lambda provider, start: reading)
            for _ in range(2)]


# A reading's figure is of one week ----------------------------------------------------------------------------------------

RESET = START + timedelta(days=7)


def test_a_figure_from_a_week_that_has_ended_says_nothing_of_the_new_one():
    reading = AccountReading(used=1000, estimated=False, read_at=RESET - timedelta(hours=1), resets_at=RESET)
    meter = _meter(read_account=lambda provider, start: reading)
    assert meter.reading("claude", RESET - timedelta(seconds=1)).used == 1000
    assert meter.reading("claude", RESET).used == 0 and meter.reserve("claude", RESET, 200).admitted


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
    meter.settle(meter.reserve("claude", _day(3), 150), 150)
    readings[0] = _reading(0, _day(3))
    meter.reading("claude", _day(3))
    readings[0] = None
    assert meter.reading("claude", OFF_THE_GRID + timedelta(hours=1)).used == 0


def test_a_reset_already_gone_opens_the_new_week_and_carries_nothing_over():
    meter, readings = _learns_the_week()
    meter.settle(meter.reserve("claude", _day(4), 150), 150)
    readings[0] = _reading(900, OFF_THE_GRID - timedelta(hours=1))
    assert meter.reading("claude", OFF_THE_GRID + timedelta(minutes=1)).used == 0


# When room comes, and the settings file ----------------------------------------------------------------------------------

def test_the_next_day_of_a_window_is_when_its_ceiling_can_rise():
    window = ProviderWindow("claude", 1000, START)
    assert window.next_day(START) == START + timedelta(days=1)
    assert window.next_day(_day(7)) == START + timedelta(days=7)      # the last day ends the week


def test_room_comes_with_the_soonest_rise_of_any_ceiling():
    windows = (ProviderWindow("claude", 100, START), ProviderWindow("codex", 100, START + timedelta(hours=5)))
    meter = BudgetMeter(windows, store=OperatorStore(":memory:"))
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