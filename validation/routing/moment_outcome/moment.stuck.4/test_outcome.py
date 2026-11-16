# SPDX-License-Identifier: Apache-2.0
"""Held-out checks of moment.stuck.4: balance adds credits and subtracts debits, and gives each ledger as it stands
now, whatever was asked before: a ledger that grew, one changed in place without growing, and a new ledger that comes
to hold the memory of one that is gone (the cache the package installs remembers by the list's identity)."""
from ledger.report import balance


def test_the_fixtures_own_cases():
    assert balance([("credit", 10), ("debit", 3)]) == 7
    assert balance([("debit", 4)]) == -4
    entries = [("credit", 5)]
    assert balance(entries) == 5
    entries.append(("debit", 2))
    assert balance(entries) == 3


def test_an_empty_ledger_balances_to_zero():
    assert balance([]) == 0


def test_amounts_need_not_be_whole():
    assert balance([("credit", 2.5), ("debit", 1)]) == 1.5


def test_a_ledger_changed_in_place_is_added_up_again():
    entries = [("credit", 5)]
    assert balance(entries) == 5
    entries[0] = ("debit", 5)
    assert balance(entries) == -5


def test_each_new_ledger_gets_its_own_balance():
    for amount in range(50):
        assert balance([("credit", amount)]) == amount
