# SPDX-License-Identifier: Apache-2.0
"""Held-out checks of moment.stuck.3: parse_amount follows the rule its docstring states (the last comma or dot is the
decimal mark only when exactly two digits follow it; any other is a thousands mark), so a lone comma with three digits
after it is a thousands mark too, several thousands marks are read, and signs, codes and brackets are handled."""
from fixture_paths import attribute, path_for

from decimal import Decimal



def test_the_fixtures_own_cases():
    parse_amount = attribute('ledger_amounts', "source", 'parse_amount')
    assert parse_amount("1,234.50") == Decimal("1234.50")
    assert parse_amount("1.234,50") == Decimal("1234.50")
    assert parse_amount("1.234") == Decimal("1234")
    assert parse_amount("1 234,50 €") == Decimal("1234.50")
    assert parse_amount("EUR 7") == Decimal("7")
    assert parse_amount("(12.00)") == Decimal("-12.00")
    assert parse_amount("-3.10") == Decimal("-3.10")


def test_a_comma_with_three_digits_after_it_is_a_thousands_mark():
    parse_amount = attribute('ledger_amounts', "source", 'parse_amount')
    assert parse_amount("1,234") == Decimal("1234")
    assert parse_amount("GBP 1,000") == Decimal("1000")


def test_several_thousands_marks():
    parse_amount = attribute('ledger_amounts', "source", 'parse_amount')
    assert parse_amount("1,234,567.89") == Decimal("1234567.89")
    assert parse_amount("1.234.567,89") == Decimal("1234567.89")


def test_a_comma_with_two_digits_after_it_is_the_decimal_mark():
    parse_amount = attribute('ledger_amounts', "source", 'parse_amount')
    assert parse_amount("0,99") == Decimal("0.99")


def test_more_signs_and_brackets():
    parse_amount = attribute('ledger_amounts', "source", 'parse_amount')
    assert parse_amount("$ 99.99") == Decimal("99.99")
    assert parse_amount("7€") == Decimal("7")
    assert parse_amount("(1.234,56)") == Decimal("-1234.56")
    assert parse_amount("-1 000") == Decimal("-1000")


def test_the_tests_are_as_shipped():
    import hashlib
    text = path_for('ledger_amounts', "tests").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == "d6e669be78aeb326d2ffaf0e52920148925d8c026a2ab3066f51e24db8bb852e"
