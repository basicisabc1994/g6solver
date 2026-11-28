# SPDX-License-Identifier: Apache-2.0
from datetime import date, datetime

from ledger.dates import add_months


def test_within_a_year():
    assert add_months(date(2026, 3, 15), 2) == date(2026, 5, 15)


def test_into_the_next_year():
    assert add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)


def test_onto_a_shorter_month():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)


def test_backwards():
    assert add_months(date(2026, 1, 31), -2) == date(2025, 11, 30)
    assert add_months(date(2026, 3, 31), -13) == date(2025, 2, 28)


def test_a_whole_year():
    assert add_months(date(2026, 12, 31), 12) == date(2027, 12, 31)


def test_a_date_and_time_keeps_its_time():
    assert add_months(datetime(2026, 1, 31, 9, 30), 1) == datetime(2026, 2, 28, 9, 30)
