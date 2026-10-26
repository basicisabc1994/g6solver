# SPDX-License-Identifier: Apache-2.0
"""Exact Clopper-Pearson bounds: tabulated values, closed forms and exact coverage."""
import math

import pytest

from g6.core.binomial import lower_bound, upper_bound


def test_tabulated_two_sided_95_percent_intervals():
    # Removal: binomial.py `if at_most(middle) > alpha:` bisection leaves the bound at its starting bracket.
    assert abs(lower_bound(5, 10, 0.025) - 0.1871) < 1e-4 and abs(upper_bound(5, 10, 0.025) - 0.8129) < 1e-4
    assert abs(upper_bound(1, 10, 0.025) - 0.4450) < 1e-4
    assert abs(lower_bound(1, 10, 0.025) - (1 - 0.975 ** 0.1)) < 1e-12


def test_closed_forms_at_zero_and_all_successes():
    # Removal: binomial.py `if successes == 0:` closed form; the all-success bound must not be zero-width.
    assert abs(upper_bound(0, 30, 0.05) - (1 - 0.05 ** (1 / 30))) < 1e-12  # about 3/30, the rule of three
    assert upper_bound(10, 10, 0.025) == 1.0 and abs(lower_bound(10, 10, 0.025) - 0.025 ** 0.1) < 1e-12


@pytest.mark.parametrize("p", [0.01, 0.1, 0.27, 0.5, 0.73, 0.9, 0.99])
def test_exact_coverage_is_at_least_nominal(p):
    # Removal: binomial.py `return high` (the conservative end of the bracket) can drop coverage below 95%.
    n = 20
    covered = sum(math.comb(n, k) * p ** k * (1 - p) ** (n - k) for k in range(n + 1)
                  if lower_bound(k, n, 0.025) <= p <= upper_bound(k, n, 0.025))
    assert covered >= 0.95


@pytest.mark.parametrize("args", [(11, 10, 0.05), (-1, 10, 0.05), (1, 0, 0.05), (1, 10, 0), (1, 10, 1),
                                  (1, 10, True), (1.0, 10, 0.05)])
def test_invalid_counts_or_levels_are_refused(args):
    # Removal: binomial.py `_check(...)` computes a bound on impossible counts.
    with pytest.raises(ValueError):
        upper_bound(*args)
