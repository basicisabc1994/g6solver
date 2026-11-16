# SPDX-License-Identifier: Apache-2.0
"""Held-out checks of moment.before_done.2: total takes an optional discount_percent, default 0, that takes that
percentage off the sum (a percentage, not a fraction or an amount, with the cents kept), and a test for it was added."""
import inspect
from pathlib import Path

import pytest


def test_no_discount_by_default():
    from shop.prices import total
    assert total([(2, 3), (1, 4)]) == 10
    assert total([]) == 0


def test_the_argument_defaults_to_zero():
    from shop.prices import total
    assert inspect.signature(total).parameters["discount_percent"].default == 0


def test_the_discount_is_that_percentage_off_the_sum():
    from shop.prices import total
    assert total([(2, 3), (1, 4)], discount_percent=10) == pytest.approx(9)
    assert total([(3, 5)], discount_percent=10) == pytest.approx(13.5)
    assert total([(4, 20)], discount_percent=12.5) == pytest.approx(70)


def test_the_ends_of_the_range():
    from shop.prices import total
    assert total([(2, 3), (1, 4)], discount_percent=0) == pytest.approx(10)
    assert total([(2, 3), (1, 4)], discount_percent=100) == pytest.approx(0)


def test_a_test_for_the_discount_was_added():
    tests = [path for path in Path("tests").rglob("test_*.py") if "discount" in path.read_text(encoding="utf-8").lower()]
    assert tests, "no test file in tests/ mentions the discount"
