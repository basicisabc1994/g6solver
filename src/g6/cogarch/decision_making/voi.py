# SPDX-License-Identifier: Apache-2.0
"""Value-of-information quantities: three standard, deterministic, pure functions.

- ``expected_value_of_perfect_information`` (EVPI): what knowing the true state before acting is worth;
  non-negative by Jensen's inequality.
- ``expected_information_gain``: the mutual information between a binary test and a binary hypothesis, the expected
  entropy reduction. It values an experiment by how much it can change the belief: a test that can only confirm what
  is already believed scores about 0.
- ``expected_improvement``: the Gaussian expected improvement of probing a candidate whose payoff is uncertain.

Every probability is validated, fail closed, never normalised.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from g6.cogarch.decision_making.utility import validate_distribution

_EPS = 1e-12


def _check_unit(name: str, value: float) -> float:
    v = float(value)
    if not 0.0 <= v <= 1.0:          # NaN fails this too
        raise ValueError(f"{name} must be in [0, 1], got {value!r}")
    return v


def expected_value_of_perfect_information(prior: Mapping[Any, float],
                                          utilities: Mapping[Any, Mapping[Any, float]]) -> float:
    """``EVPI = E_s[max_a u(a, s)] - max_a E_s[u(a, s)]``.

    ``prior`` maps state -> probability; ``utilities`` maps action -> {state -> utility} and must cover every prior
    state for every action (a missing utility raises, never assumed). Clamped at 0 only against round-off."""
    if not utilities:
        raise ValueError("no actions supplied")
    states = list(prior)
    validate_distribution(prior[s] for s in states)
    for action, per_state in utilities.items():
        for s in states:
            if s not in per_state:
                raise ValueError(f"action {action!r} has no utility for state {s!r}")
            if not math.isfinite(float(per_state[s])):
                raise ValueError(f"action {action!r}: the utility for state {s!r} is not finite")
    best_eu_without = max(sum(prior[s] * utilities[a][s] for s in states) for a in utilities)
    eu_with_perfect_info = sum(prior[s] * max(utilities[a][s] for a in utilities) for s in states)
    return max(0.0, eu_with_perfect_info - best_eu_without)


def _binary_entropy(p: float) -> float:
    if p <= 0.0 or p >= 1.0:
        return 0.0
    return -(p * math.log2(p) + (1.0 - p) * math.log2(1.0 - p))


def expected_information_gain(prior: float, true_positive_rate: float, false_positive_rate: float) -> float:
    """Mutual information (bits) between a binary test and a binary hypothesis: ``H(prior) - E[H(posterior)]``.

    ``prior`` = P(hypothesis true); ``true_positive_rate`` = P(positive | true); ``false_positive_rate`` =
    P(positive | false). A test whose outcome distribution is the same under both hypotheses (tpr == fpr: it can
    refute nothing) scores exactly 0."""
    p = _check_unit("prior", prior)
    tpr = _check_unit("true_positive_rate", true_positive_rate)
    fpr = _check_unit("false_positive_rate", false_positive_rate)
    p_pos = p * tpr + (1.0 - p) * fpr
    p_neg = 1.0 - p_pos
    posterior_pos = (p * tpr / p_pos) if p_pos > _EPS else p
    posterior_neg = (p * (1.0 - tpr) / p_neg) if p_neg > _EPS else p
    expected_posterior_entropy = 0.0
    if p_pos > _EPS:
        expected_posterior_entropy += p_pos * _binary_entropy(posterior_pos)
    if p_neg > _EPS:
        expected_posterior_entropy += p_neg * _binary_entropy(posterior_neg)
    return max(0.0, _binary_entropy(p) - expected_posterior_entropy)


def _norm_pdf(z: float) -> float:
    return math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)


def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def expected_improvement(mean: float, std: float, best: float, *, xi: float = 0.0) -> float:
    """Gaussian expected improvement of a candidate with payoff ``N(mean, std^2)`` over the incumbent ``best``
    (maximisation). ``std == 0`` gives ``max(0, mean - best - xi)``; a negative or non-finite input raises."""
    if not all(math.isfinite(v) for v in (mean, std, best, xi)):
        raise ValueError("mean, std, best and xi must be finite")
    if std < 0.0:
        raise ValueError(f"std must be >= 0, got {std!r}")
    delta = mean - best - xi
    if std == 0.0:
        return max(0.0, delta)
    z = delta / std
    return max(0.0, delta * _norm_cdf(z) + std * _norm_pdf(z))


__all__ = ["expected_improvement", "expected_information_gain", "expected_value_of_perfect_information"]
