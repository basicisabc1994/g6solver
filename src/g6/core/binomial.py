# SPDX-License-Identifier: Apache-2.0
"""Exact (Clopper-Pearson) one-sided bounds on a binomial proportion, in the standard library.

``upper_bound(k, n, alpha)`` is the p at which seeing ``k`` or fewer successes in ``n`` trials has probability
``alpha``: whatever the true proportion is, the chance that it lies above the bound is at most ``alpha``.
``lower_bound`` mirrors it. A two-sided interval at confidence ``1 - alpha`` is the pair of bounds at ``alpha / 2``.
No normal approximation is used, so a small or all-success sample gets a wide bound, not a zero-width one.

The bound is found by bisection on the exact binomial tail to within 1e-12, and the bisection returns the end of its
final bracket on the conservative side; zero and ``n`` successes use their closed forms.
"""
from __future__ import annotations

import math

_TOLERANCE = 1e-12


def _check(successes: int, trials: int, alpha: float) -> None:
    if type(trials) is not int or type(successes) is not int or trials < 1 or not 0 <= successes <= trials:
        raise ValueError("successes and trials must be integers with 0 <= successes <= trials and trials >= 1")
    if isinstance(alpha, bool) or not isinstance(alpha, int | float) or not 0 < alpha < 1:
        raise ValueError("alpha must be a probability strictly between 0 and 1")


def upper_bound(successes: int, trials: int, alpha: float) -> float:
    """The exact one-sided upper bound at level ``alpha``: P(true proportion > bound) <= alpha."""
    _check(successes, trials, alpha)
    if successes == trials:
        return 1.0
    if successes == 0:
        return -math.expm1(math.log(alpha) / trials)  # (1 - p) ** n = alpha
    # P(X <= successes) for X ~ Binomial(trials, p), summed over the shorter tail.
    lower_tail = successes <= trials // 2
    base = math.lgamma(trials + 1)
    terms = [(i, base - math.lgamma(i + 1) - math.lgamma(trials - i + 1))
             for i in (range(successes + 1) if lower_tail else range(successes + 1, trials + 1))]

    def at_most(p: float) -> float:
        log_p, log_q = math.log(p), math.log1p(-p)
        tail = math.fsum(math.exp(c + i * log_p + (trials - i) * log_q) for i, c in terms)
        return tail if lower_tail else 1.0 - tail

    low, high = successes / trials, 1.0
    while high - low > _TOLERANCE:
        middle = (low + high) / 2
        if at_most(middle) > alpha:
            low = middle
        else:
            high = middle
    return high


def lower_bound(successes: int, trials: int, alpha: float) -> float:
    """The exact one-sided lower bound at level ``alpha``: P(true proportion < bound) <= alpha."""
    _check(successes, trials, alpha)
    return 1.0 - upper_bound(trials - successes, trials, alpha)


__all__ = ["lower_bound", "upper_bound"]
