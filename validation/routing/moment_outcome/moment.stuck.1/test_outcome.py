# SPDX-License-Identifier: Apache-2.0
"""Held-out checks of moment.stuck.1: round_money rounds to whole cents with a half cent away from zero, read from the
amount as written (2.675 is stored a little below it), so the fixture's own cases pass and so do others of the same
rule, and an amount just below half a cent still rounds down (no nudge by a small amount)."""
from ledger.money import round_money


def test_the_fixtures_own_cases():
    assert round_money(12.34) == 12.34
    assert round_money(0.124) == 0.12
    assert round_money(2.675) == 2.68
    assert round_money(1.005) == 1.01
    assert round_money(0.125) == 0.13
    assert round_money(-2.675) == -2.68


def test_more_half_cents_round_away_from_zero():
    assert round_money(1.115) == 1.12
    assert round_money(0.005) == 0.01
    assert round_money(-0.005) == -0.01
    assert round_money(-1.005) == -1.01
    assert round_money(-0.125) == -0.13


def test_just_below_half_a_cent_rounds_down():
    assert round_money(0.12499999999999) == 0.12
    assert round_money(-2.67499999999999) == -2.67


def test_whole_amounts_stay():
    assert round_money(10) == 10
    assert round_money(2.5) == 2.5
