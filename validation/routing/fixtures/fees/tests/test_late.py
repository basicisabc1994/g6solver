# SPDX-License-Identifier: Apache-2.0
import pytest

from fees.late import late_fee


def test_within_the_grace_period():
    assert late_fee(0, 100) == 0
    assert late_fee(3, 100) == 0


def test_a_started_week_counts_whole():
    assert late_fee(4, 100) == pytest.approx(2)
    assert late_fee(8, 100) == pytest.approx(4)


def test_the_cap():
    assert late_fee(40, 100) == pytest.approx(10)


def test_a_long_delay():
    assert late_fee(70, 200) == pytest.approx(24)
