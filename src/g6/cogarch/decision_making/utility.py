# SPDX-License-Identifier: Apache-2.0
"""Utility models and expected utility over a validated outcome distribution.

A ``UtilityModel`` protocol, a linear one, and ``expected_utility``. Probabilities are validated, never normalised: a
malformed distribution is the caller's to correct, not something to paper over.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

_PROB_TOLERANCE = 1e-9


@runtime_checkable
class UtilityModel(Protocol):
    """Anything that scores an outcome with a real-valued utility."""

    def utility(self, outcome: Any) -> float:
        ...


@dataclass(frozen=True)
class LinearUtility:
    """``u(x) = scale * x + offset`` over numeric outcomes."""

    scale: float = 1.0
    offset: float = 0.0

    def utility(self, outcome: Any) -> float:
        return self.scale * float(outcome) + self.offset


def validate_distribution(probabilities: Iterable[float]) -> list[float]:
    """A finite probability distribution, as a list. Raises ``ValueError`` when it is empty, an entry is not finite
    or outside [0, 1], or the entries do not sum to 1 within 1e-9. Never normalises."""
    probs = [float(p) for p in probabilities]
    if not probs:
        raise ValueError("empty probability distribution")
    for p in probs:
        if not math.isfinite(p) or p < 0.0 or p > 1.0:
            raise ValueError(f"probability {p!r} outside [0, 1]")
    total = sum(probs)
    if abs(total - 1.0) > _PROB_TOLERANCE:
        raise ValueError(f"probabilities sum to {total!r}, not 1")
    return probs


def expected_utility(outcomes: Mapping[Any, float] | Iterable[tuple[Any, float]], model: UtilityModel) -> float:
    """``E[u]`` over an explicit ``outcome -> probability`` distribution."""
    pairs = list(outcomes.items()) if isinstance(outcomes, Mapping) else list(outcomes)
    validate_distribution(p for _, p in pairs)
    return sum(p * model.utility(outcome) for outcome, p in pairs)


__all__ = ["LinearUtility", "UtilityModel", "expected_utility", "validate_distribution"]
