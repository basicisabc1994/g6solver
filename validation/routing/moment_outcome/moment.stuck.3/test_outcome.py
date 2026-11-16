# SPDX-License-Identifier: Apache-2.0
"""Held-out checks of moment.stuck.3: parse_amount follows the rule its docstring states (the last comma or dot is the
decimal mark only when exactly two digits follow it; any other is a thousands mark), so a lone comma with three digits
after it is a thousands mark too, several thousands marks are read, and signs, codes and brackets are handled."""
from decimal import Decimal

from ledger.amounts import parse_amount


def test_the_fixtures_own_cases():
    assert parse_amount("1,234.50") == Decimal("1234.50")
    assert parse_amount("1.234,50") == Decimal("1234.50")
    assert parse_amount("1.234") == Decimal("1234")
    assert parse_amount("1 234,50 €") == Decimal("1234.50")
    assert parse_amount("EUR 7") == Decimal("7")
    assert parse_amount("(12.00)") == Decimal("-12.00")
    assert parse_amount("-3.10") == Decimal("-3.10")


def test_a_comma_with_three_digits_after_it_is_a_thousands_mark():
    assert parse_amount("1,234") == Decimal("1234")
    assert parse_amount("GBP 1,000") == Decimal("1000")


def test_several_thousands_marks():
    assert parse_amount("1,234,567.89") == Decimal("1234567.89")
    assert parse_amount("1.234.567,89") == Decimal("1234567.89")


def test_a_comma_with_two_digits_after_it_is_the_decimal_mark():
    assert parse_amount("0,99") == Decimal("0.99")


def test_more_signs_and_brackets():
    assert parse_amount("$ 99.99") == Decimal("99.99")
    assert parse_amount("7€") == Decimal("7")
    assert parse_amount("(1.234,56)") == Decimal("-1234.56")
    assert parse_amount("-1 000") == Decimal("-1000")
