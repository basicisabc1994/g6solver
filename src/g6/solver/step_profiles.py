# SPDX-License-Identifier: Apache-2.0
"""Which steps run at each complexity level; everything else is skipped."""
from __future__ import annotations

from dataclasses import dataclass

from g6.solver.complexity_classifier import TaskComplexity

ALL_STEPS = frozenset(range(1, 16))

_ACTIVE = {
    TaskComplexity.SIMPLE: frozenset({1, 8, 10, 11}),         # goal, answer, execute, evaluate
    TaskComplexity.MODERATE: frozenset({1, 8, 10, 11, 12}),   # ... plus review
    TaskComplexity.COMPLEX: ALL_STEPS,
}


@dataclass(frozen=True)
class ExecutionProfile:
    complexity: TaskComplexity
    active_steps: frozenset[int]

    @property
    def skipped_steps(self) -> frozenset[int]:
        return ALL_STEPS - self.active_steps


def profile_for(complexity: TaskComplexity | str) -> ExecutionProfile:
    """The deterministic step profile for a complexity level."""
    level = TaskComplexity(complexity)
    return ExecutionProfile(level, _ACTIVE[level])


__all__ = ["ALL_STEPS", "ExecutionProfile", "profile_for"]
