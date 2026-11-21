# SPDX-License-Identifier: Apache-2.0
from decimal import Decimal

from ledger.amounts import parse_amount


def test_a_comma_for_thousands():
    assert parse_amount("1,234.50") == Decimal("1234.50")


def test_a_dot_for_thousands():
    assert parse_amount("1.234,50") == Decimal("1234.50")
    assert parse_amount("1.234") == Decimal("1234")


def test_a_space_for_thousands_and_a_sign():
    assert parse_amount("1 234,50 €") == Decimal("1234.50")
    assert parse_amount("EUR 7") == Decimal("7")


def test_brackets_and_a_minus_are_negative():
    assert parse_amount("(12.00)") == Decimal("-12.00")
    assert parse_amount("-3.10") == Decimal("-3.10")
