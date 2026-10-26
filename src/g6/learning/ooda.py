# SPDX-License-Identifier: Apache-2.0
"""What a strategy sees each cycle: one arm's results on the whole training set.

Trimmed from the archive's ``learning_layer/ooda.py``. ``Orientation`` and ``TempoDecision`` are not kept: a strategy
here only proposes theories (orient and decide), and the runner owns acting, measuring and stopping, so a strategy
cannot skip the paired control or declare its own convergence (audit 006 LL-002, LL-005).
"""
from __future__ import annotations

from dataclasses import dataclass

from g6.learning.schema import TaskResult


@dataclass(frozen=True)
class ObservationResult:
    """Results of the current harness on every training instance (never a subsample, LL-004)."""

    results: tuple[TaskResult, ...] = ()
    cycle: int = 0

    @property
    def accuracy(self) -> float:
        return self.n_passed / len(self.results) if self.results else 0.0

    @property
    def n_passed(self) -> int:
        return sum(r.passed for r in self.results)

    @property
    def n_failed(self) -> int:
        return len(self.results) - self.n_passed


__all__ = ["ObservationResult"]
