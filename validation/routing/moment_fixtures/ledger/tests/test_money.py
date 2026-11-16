# SPDX-License-Identifier: Apache-2.0
from ledger.money import round_money


def test_whole_cents_stay():
    assert round_money(12.34) == 12.34


def test_below_half_a_cent_rounds_down():
    assert round_money(0.124) == 0.12


def test_half_a_cent_rounds_away_from_zero():
    assert round_money(2.675) == 2.68
    assert round_money(1.005) == 1.01
    assert round_money(0.125) == 0.13


def test_a_negative_half_cent_rounds_away_from_zero():
    assert round_money(-2.675) == -2.68
