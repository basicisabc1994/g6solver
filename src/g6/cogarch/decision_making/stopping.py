# SPDX-License-Identifier: Apache-2.0
"""A sequential value-of-information stopping rule: keep going while the best next step is worth its cost; stop when
it is not. Deterministic and fail closed (non-finite thresholds refused).
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class VoIThresholdStop:
    """Stop when the best candidate's expected gain, less its cost times ``cost_weight``, falls below
    ``min_net_gain``. ``min_net_gain = 0`` with ``cost_weight = 0`` never stops while any positive gain exists."""

    min_net_gain: float = 0.0
    cost_weight: float = 0.0

    def __post_init__(self) -> None:
        for name, value in (("min_net_gain", self.min_net_gain), ("cost_weight", self.cost_weight)):
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite, got {value!r}")
        if self.cost_weight < 0.0:
            raise ValueError(f"cost_weight must be >= 0, got {self.cost_weight!r}")

    def net_gain(self, expected_gain: float, cost: float) -> float:
        return expected_gain - self.cost_weight * float(cost)

    def should_stop(self, *, best_expected_gain: float, best_cost: float = 0.0) -> bool:
        return self.net_gain(best_expected_gain, best_cost) < self.min_net_gain


__all__ = ["VoIThresholdStop"]
