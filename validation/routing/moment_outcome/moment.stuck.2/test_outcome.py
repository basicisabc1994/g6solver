# SPDX-License-Identifier: Apache-2.0
"""Held-out checks of moment.stuck.2: add_months moves across years both ways, lands on a shorter month's last day,
keeps a date a date, and keeps all of a date and time's time (seconds, microseconds and time zone too, not only the
hour and minute the fixture's own case shows)."""
from datetime import date, datetime, timezone

from ledger.dates import add_months


def test_the_fixtures_own_cases():
    assert add_months(date(2026, 3, 15), 2) == date(2026, 5, 15)
    assert add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)
    assert add_months(date(2026, 1, 31), -2) == date(2025, 11, 30)
    assert add_months(date(2026, 3, 31), -13) == date(2025, 2, 28)
    assert add_months(date(2026, 12, 31), 12) == date(2027, 12, 31)
    assert add_months(datetime(2026, 1, 31, 9, 30), 1) == datetime(2026, 2, 28, 9, 30)


def test_more_month_ends():
    assert add_months(date(2026, 5, 31), -1) == date(2026, 4, 30)
    assert add_months(date(2024, 2, 29), 12) == date(2025, 2, 28)
    assert add_months(date(2026, 3, 31), 23) == date(2028, 2, 29)
    assert add_months(date(2026, 1, 31), -25) == date(2023, 12, 31)


def test_no_months_is_the_same_day():
    assert add_months(date(2026, 1, 15), 0) == date(2026, 1, 15)


def test_a_date_stays_a_date():
    assert type(add_months(date(2026, 1, 31), 1)) is date


def test_a_date_and_time_keeps_all_of_its_time():
    moved = add_months(datetime(2026, 1, 31, 9, 30, 15, 250, tzinfo=timezone.utc), 1)
    assert moved == datetime(2026, 2, 28, 9, 30, 15, 250, tzinfo=timezone.utc)
    assert moved.tzinfo is timezone.utc
