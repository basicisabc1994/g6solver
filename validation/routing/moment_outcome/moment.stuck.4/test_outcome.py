# SPDX-License-Identifier: Apache-2.0
"""Held-out checks of moment.stuck.4: balance adds credits and subtracts debits, and gives each ledger as it stands
now, whatever was asked before: a ledger that grew, one changed in place without growing, and a new ledger that comes
to hold the memory of one that is gone (the cache the package installs remembers by the list's identity)."""


from fixture_paths import attribute, path_for

def test_the_fixtures_own_cases():
    balance = attribute('ledger_report', "source", 'balance')
    assert balance([("credit", 10), ("debit", 3)]) == 7
    assert balance([("debit", 4)]) == -4
    entries = [("credit", 5)]
    assert balance(entries) == 5
    entries.append(("debit", 2))
    assert balance(entries) == 3


def test_an_empty_ledger_balances_to_zero():
    balance = attribute('ledger_report', "source", 'balance')
    assert balance([]) == 0


def test_amounts_need_not_be_whole():
    balance = attribute('ledger_report', "source", 'balance')
    assert balance([("credit", 2.5), ("debit", 1)]) == 1.5


def test_a_ledger_changed_in_place_is_added_up_again():
    balance = attribute('ledger_report', "source", 'balance')
    entries = [("credit", 5)]
    assert balance(entries) == 5
    entries[0] = ("debit", 5)
    assert balance(entries) == -5


def test_each_new_ledger_gets_its_own_balance():
    balance = attribute('ledger_report', "source", 'balance')
    for amount in range(50):
        assert balance([("credit", amount)]) == amount


def test_the_tests_are_as_shipped():
    import hashlib
    text = path_for('ledger_report', "tests").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == "477cab4bd1f1e5d329123ae233e8bb20414f5c8da0eeac56796f03e7aa1db43c"
