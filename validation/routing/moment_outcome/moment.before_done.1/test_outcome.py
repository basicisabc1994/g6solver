# SPDX-License-Identifier: Apache-2.0
"""Held-out checks of moment.before_done.1: total raises ValueError('negative quantity') for an order line with a
negative quantity, wherever that line is, and adds up every other order as before."""
from fixture_paths import attribute

import pytest


def test_the_lines_still_add_up():
    total = attribute('shop_m', 'source', 'total')
    assert total([(2, 3), (1, 4)]) == 10
    assert total([]) == 0


def test_a_negative_quantity_raises():
    total = attribute('shop_m', 'source', 'total')
    with pytest.raises(ValueError, match="negative quantity"):
        total([(-1, 3)])


def test_a_negative_quantity_after_other_lines_raises_though_the_sum_stays_positive():
    total = attribute('shop_m', 'source', 'total')
    with pytest.raises(ValueError, match="negative quantity"):
        total([(5, 10), (-1, 3)])


def test_a_zero_quantity_is_not_negative():
    total = attribute('shop_m', 'source', 'total')
    assert total([(0, 5), (2, 1)]) == 2


def test_lines_given_one_at_a_time_still_add_up():
    total = attribute('shop_m', 'source', 'total')
    assert total(iter([(2, 3), (1, 4)])) == 10
