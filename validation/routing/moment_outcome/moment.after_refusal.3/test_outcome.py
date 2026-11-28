# SPDX-License-Identifier: Apache-2.0
"""Held-out checks of moment.after_refusal.3: total raises ValueError('negative price') for a line whose unit price is
negative, wherever that line is and whatever its quantity, not for a zero price, adds up every other order as before,
and a test for it was added."""

from fixture_paths import attribute, path_for

import pytest


def test_a_negative_price_raises():
    total = attribute('shop_m', 'source', 'total')
    with pytest.raises(ValueError, match="negative price"):
        total([(1, -3)])


def test_a_negative_price_after_other_lines_raises():
    total = attribute('shop_m', 'source', 'total')
    with pytest.raises(ValueError, match="negative price"):
        total([(2, 3), (1, -1)])


def test_a_negative_price_raises_whatever_the_quantity():
    total = attribute('shop_m', 'source', 'total')
    with pytest.raises(ValueError):
        total([(-1, -3)])


def test_a_zero_price_is_not_negative():
    total = attribute('shop_m', 'source', 'total')
    assert total([(1, 0), (2, 3)]) == 6


def test_the_lines_still_add_up():
    total = attribute('shop_m', 'source', 'total')
    assert total([(2, 3), (1, 4)]) == 10
    assert total([]) == 0
    assert total(iter([(2, 3), (1, 4)])) == 10


def test_a_test_for_it_was_added():
    tests = [path for path in path_for('shop_m', "tests").parent.rglob("test_*.py") if "negative price" in path.read_text(encoding="utf-8")]
    assert tests, "no test file in tests/ names the negative price"
