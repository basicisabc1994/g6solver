# SPDX-License-Identifier: Apache-2.0
"""The fixed 15-step table. A step is a name and a number; what it does is the runner the caller supplies."""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass


@dataclass(frozen=True)
class StepMeta:
    """One step: ``name`` is the key a runner is registered under; ``fallback_categories`` name the
    ``CategoryRegistry`` groups tried, in order, when the step's own runner gives nothing usable."""

    number: int
    name: str
    title: str
    fallback_categories: tuple[str, ...] = ()
    max_attempts: int = 3


# The archive's per-step component lists, voting, strategy and distillation flags are not ported: steps call no
# archive component (audit 028 §11). The attempt counts are the archive's per-step retry defaults.
_STEPS = (
    StepMeta(1, "goal_setting", "Goal Setting", ("alignment",)),
    StepMeta(2, "data_gathering", "Data Gathering", ("context",)),
    StepMeta(3, "problem_formulation", "Problem Formulation", ("context", "standalone")),
    StepMeta(4, "success_criteria", "Success Criteria", ("alignment",)),
    StepMeta(5, "ai_design", "AI Design/Upskilling", ("utility", "standalone")),
    StepMeta(6, "algorithm_design", "Algorithm Design", ("standalone",)),
    StepMeta(7, "algorithm_refinement", "Algorithm Refinement", ("safety", "alignment")),
    StepMeta(8, "code_construction", "Code Construction", ("standalone", "agents"), max_attempts=5),
    StepMeta(9, "code_debugging", "Code Debugging", ("safety", "adaptation", "standalone"), max_attempts=5),
    StepMeta(10, "solution_execution", "Solution Execution", ("standalone",), max_attempts=1),
    StepMeta(11, "solution_evaluation", "Solution Evaluation", ("alignment", "safety")),
    StepMeta(12, "solution_review", "Solution Review", ("alignment", "adaptation")),
    StepMeta(13, "system_learnings", "AI System Learnings", ("adaptation",)),
    StepMeta(14, "project_review", "Project Success Review", ("alignment",)),
    StepMeta(15, "metalearning", "AI Metalearning", ("adaptation", "utility")),
)


class StepRegistry:
    """Look up the 15 steps by number or by runner name; iterates in step order."""

    def __init__(self) -> None:
        self._by_number = {m.number: m for m in _STEPS}
        self._by_name = {m.name: m for m in _STEPS}

    def __iter__(self) -> Iterator[StepMeta]:
        return iter(_STEPS)

    def __len__(self) -> int:
        return len(_STEPS)

    def by_number(self, number: int) -> StepMeta:
        return self._by_number[number]

    def by_name(self, name: str) -> StepMeta:
        return self._by_name[name]

    def names(self) -> tuple[str, ...]:
        return tuple(m.name for m in _STEPS)

    def numbers(self) -> frozenset[int]:
        return frozenset(self._by_number)


STEP_REGISTRY = StepRegistry()

__all__ = ["STEP_REGISTRY", "StepMeta", "StepRegistry"]
