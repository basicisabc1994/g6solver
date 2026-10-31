# SPDX-License-Identifier: Apache-2.0
"""The loop's budget (design 6.6): weekly provider windows, paced, on the operator's one ledger."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from g6.operator.store import OperatorStore
from g6.self_maintenance.pacing import AccountReading, BudgetMeter, ProviderWindow, ceiling, target

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
    meter.reserve("claude", _day(3), 300)
    assert meter.reading("claude", _day(3)).used == 300


def test_at_a_reported_limit_the_loop_waits_for_the_reset():
    reset = _day(2) + timedelta(hours=4)
    meter = _meter(read_account=lambda provider, start: AccountReading(limited_until=reset))
    refused = meter.reserve("claude", _day(2), 1)
    assert not refused.admitted and refused.wait_until == reset
    assert meter.reserve("claude", reset, 1).admitted


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
