# SPDX-License-Identifier: Apache-2.0
from ledger.report import balance


def test_credits_add_and_debits_subtract():
    assert balance([("credit", 10), ("debit", 3)]) == 7


def test_debits_alone_are_negative():
    assert balance([("debit", 4)]) == -4


def test_a_ledger_that_grew_is_added_up_again():
    entries = [("credit", 5)]
    assert balance(entries) == 5
    entries.append(("debit", 2))
    assert balance(entries) == 3
