# SPDX-License-Identifier: Apache-2.0
"""Held-out checks of moment.after_refusal.1: total_with_tax(lines, rate) is the order total plus tax at rate, with
rate a fraction (0.2 for 20%), rounded to two decimals; total is unchanged."""
from fixture_paths import attribute

import pytest


def test_tax_is_added_at_the_rate():
    total_with_tax = attribute('shop_m', 'source', 'total_with_tax')
    assert float(total_with_tax([(2, 3), (1, 4)], 0.2)) == pytest.approx(12.0, abs=1e-9)


def test_the_result_is_rounded_to_two_decimals():
    total_with_tax = attribute('shop_m', 'source', 'total_with_tax')
    assert float(total_with_tax([(1, 9.99)], 0.2)) == pytest.approx(11.99, abs=1e-9)
    assert float(total_with_tax([(1, 19.99)], 0.07)) == pytest.approx(21.39, abs=1e-9)


def test_no_tax_and_no_lines():
    total_with_tax = attribute('shop_m', 'source', 'total_with_tax')
    assert float(total_with_tax([(2, 3), (1, 4)], 0)) == pytest.approx(10, abs=1e-9)
    assert float(total_with_tax([], 0.2)) == pytest.approx(0, abs=1e-9)


def test_total_is_unchanged():
    total = attribute('shop_m', 'source', 'total')
    assert total([(2, 3), (1, 4)]) == 10
