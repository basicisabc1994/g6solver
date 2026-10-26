# SPDX-License-Identifier: Apache-2.0
"""``compose_bound``: an upper bound on P(at least one of two parts fails), from an upper bound on each.

audit 012, CSF-010. The archive's "Theorem 1" gave P(both safe) >= (1 - e1)(1 - e2) - rho * min(e1, e2). With rho = 0
that is the value under independence, which overstates safety whenever the failures are disjoint or negatively
correlated (e1 = e2 = 0.1 with disjoint failures fails with probability 0.2, not 0.19); its rho was not a correlation
of the failure events (positive correlation makes the true value safer, so the sign was wrong); and an epsilon outside
[0, 1] was only noted, so e1 = -1 composed to 0 and passed.

Here the default is the Fréchet (Boole) bound, P(F1 or F2) <= e1 + e2, which holds whatever the dependence between
the failures. The independence value 1 - (1 - e1)(1 - e2) is used only when the caller asserts independence; nothing
here checks that assertion. Both epsilons must be in [0, 1]. The archive's interface and resource checks never
changed its verdict and are not ported.
"""
from __future__ import annotations

from g6.csf.schema import INPUT_INVALID, CSFInputError, check_epsilon


def compose_bound(eps1: float, eps2: float, *, assume_independent: bool = False) -> float:
    """An upper bound on the probability that either part fails, given P(part i fails) <= ``eps_i``.

    ``min(1, eps1 + eps2)`` by default, valid for any dependence; ``eps1 + eps2 - eps1 * eps2`` when
    ``assume_independent=True``, valid only if the two failures are independent. The composed safety lower bound is
    ``1 - compose_bound(...)``.
    """
    e1 = check_epsilon(eps1, "eps1")
    e2 = check_epsilon(eps2, "eps2")
    if not isinstance(assume_independent, bool):
        raise CSFInputError(INPUT_INVALID, "assume_independent must be True or False")
    if assume_independent:
        return e1 + e2 - e1 * e2
    return min(1.0, e1 + e2)


__all__ = ["compose_bound"]
