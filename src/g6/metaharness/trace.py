# SPDX-License-Identifier: Apache-2.0
"""One measured trace for the meta-harness proposers; observations never certify a workflow."""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from heapq import heappop, heappush
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MeasuredStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=128)
    model: str = ""
    depends_on: tuple[str, ...] = ()
    latency_s: float = Field(ge=0, allow_inf_nan=False)
    input_tokens: int | None = Field(None, ge=0, strict=True)
    output_tokens: int | None = Field(None, ge=0, strict=True)
    cost_usd: float | None = Field(None, ge=0, allow_inf_nan=False)
    checked_by: tuple[str, ...] = ()
    passed: bool | None = Field(None, strict=True)
    error: str = ""

    @model_validator(mode="after")
    def checked(self) -> MeasuredStep:
        if self.passed is not None and not self.checked_by:
            raise ValueError("G6_E_META_TRACE: a check outcome needs checked_by")
        if any(not n.strip() for n in (self.name, *self.checked_by, *self.depends_on)):
            raise ValueError("G6_E_META_TRACE: names must not be blank")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("G6_E_META_TRACE: repeated dependency")
        return self


def validate_dag(steps: Sequence[MeasuredStep]) -> tuple[str, ...]:
    counts = Counter(s.name for s in steps)
    problems = [f"duplicate step {n}" for n, count in counts.items() if count > 1]
    for step in steps:
        problems += [f"{step.name}: missing dependency {n}" for n in step.depends_on if n not in counts]
        if step.name in step.depends_on:
            problems.append(f"{step.name}: self dependency")
    if not problems and len(_ordered(steps)) != len(steps):
        problems.append("cycle in step dependencies")
    return tuple(problems)


def _ordered(steps: Sequence[MeasuredStep]) -> list[MeasuredStep]:
    by_name = {s.name: s for s in steps}
    children: dict[str, list[str]] = {n: [] for n in by_name}
    degree = {s.name: len(s.depends_on) for s in steps}
    for step in steps:
        for parent in step.depends_on:
            children[parent].append(step.name)
    ready: list[str] = []
    for name, n in degree.items():
        if not n:
            heappush(ready, name)
    ordered = []
    while ready:
        name = heappop(ready)
        ordered.append(by_name[name])
        for child in children[name]:
            degree[child] -= 1
            if not degree[child]:
                heappush(ready, child)
    return ordered


class MeasuredTrace(BaseModel):
    """Dev observations only. External traces are claims; only evaluator runs can open the meta-gate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1, max_length=256)
    profile_digest: str = ""
    steps: tuple[MeasuredStep, ...] = Field(default=(), max_length=10_000)
    completion_state: Literal["qualified-draft"] = "qualified-draft"

    @model_validator(mode="after")
    def dag(self) -> MeasuredTrace:
        problems = validate_dag(self.steps)
        if problems:
            raise ValueError("G6_E_META_TRACE: " + "; ".join(problems))
        return self

    def ordered(self) -> tuple[MeasuredStep, ...]:
        return tuple(_ordered(self.steps))

    def critical_path(self) -> tuple[str, ...]:
        distances: dict[str, float] = {}
        predecessors: dict[str, str | None] = {}
        for step in self.ordered():
            parent = max(step.depends_on, key=lambda n: (distances[n], n), default=None)
            distances[step.name] = step.latency_s + (distances[parent] if parent else 0.0)
            predecessors[step.name] = parent
        end = max(distances, key=lambda n: (distances[n], n), default=None)
        path = []
        while end is not None:
            path.append(end)
            end = predecessors[end]
        return tuple(reversed(path))
