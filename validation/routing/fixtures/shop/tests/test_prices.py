# SPDX-License-Identifier: Apache-2.0
import pytest

from shop.prices import total


def test_total_adds_the_lines():
    assert total([(2, 3), (1, 4)]) == 10


def test_negative_raises():
    with pytest.raises(ValueError, match="negative quantity"):
        total([(-1, 3)])