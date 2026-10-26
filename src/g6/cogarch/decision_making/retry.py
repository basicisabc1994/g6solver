# SPDX-License-Identifier: Apache-2.0
"""How many retries to allow each task: a bounded budget shared out by the expected value of one more attempt.

Each task declares what a success is worth (``value``), what an attempt costs (``cost``, in the same units), its
attempts so far (``successes``, ``failures``) and a Beta prior (``prior_alpha``, ``prior_beta``; ``Beta(1, 1)`` by
default). A retry is only made after a failure, so the ``r``-th retry's chance of success is the Beta-Bernoulli
predictive after that many more failures::

    p_r = (prior_alpha + successes) / (prior_alpha + prior_beta + successes + failures + r - 1)

and its net value is ``p_r * value - cost``. :class:`RetryBudgetPolicy` grants retries one at a time to the task whose
next retry is worth most (ties by declared order), paying each from a :class:`~g6.cogarch.decision_making.budget.
BoundedAllocator`, until the best next retry's net value is below ``min_net_gain``
(:class:`~g6.cogarch.decision_making.stopping.VoIThresholdStop`), nothing more is affordable, or every task has
``max_retries``. Deterministic; every grant is returned with its working (DECISION_MAKING-01).
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from g6.cogarch.decision_making.budget import BoundedAllocator
from g6.cogarch.decision_making.stopping import VoIThresholdStop

MAX_RETRIES = 100


@dataclass(frozen=True)
class RetryTask:
    """A task that may be retried: what success is worth, what an attempt costs, and its record so far."""

    task_id: str
    value: float
    cost: float
    successes: int = 0
    failures: int = 0
    prior_alpha: float = 1.0
    prior_beta: float = 1.0

    def __post_init__(self) -> None:
        if not self.task_id:
            raise ValueError("task_id must be non-empty")
        for name in ("value", "cost", "prior_alpha", "prior_beta"):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"{self.task_id}: {name} must be finite")
        if self.cost < 0 or self.value < 0:
            raise ValueError(f"{self.task_id}: value and cost must be >= 0")
        if self.prior_alpha <= 0 or self.prior_beta <= 0:
            raise ValueError(f"{self.task_id}: prior_alpha and prior_beta must be > 0")
        if self.successes < 0 or self.failures < 0:
            raise ValueError(f"{self.task_id}: successes and failures must be >= 0")

    def success_probability(self, retry: int) -> float:
        """The chance that the ``retry``-th retry (1, 2, ...) succeeds, every attempt before it having failed."""
        alpha = self.prior_alpha + self.successes
        return alpha / (alpha + self.prior_beta + self.failures + retry - 1)


@dataclass
class RetryBudgetPolicy:
    """Grant retries across tasks by the net value of each one's next retry, within the budget."""

    allocator: BoundedAllocator
    stopping: VoIThresholdStop
    max_retries: int = 10

    def __post_init__(self) -> None:
        if not 0 <= self.max_retries <= MAX_RETRIES:
            raise ValueError(f"max_retries must be 0 to MAX_RETRIES {MAX_RETRIES}, got {self.max_retries!r}")

    def decide(self, tasks: Sequence[RetryTask]) -> dict[str, Any]:
        if not tasks:
            raise ValueError("no tasks supplied")
        ids = [t.task_id for t in tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate task ids")
        granted = {t.task_id: 0 for t in tasks}
        grants: list[dict[str, Any]] = []
        stop_reason = "max_retries_for_every_task"
        while True:
            open_tasks = [t for t in tasks if granted[t.task_id] < self.max_retries]
            if not open_tasks:
                break
            rows = []
            for task in open_tasks:
                retry = granted[task.task_id] + 1
                p = task.success_probability(retry)
                rows.append({"task_id": task.task_id, "retry": retry, "success_probability": p,
                             "expected_value": p * task.value, "cost": task.cost,
                             "net_value": self.stopping.net_gain(p * task.value, task.cost),
                             "affordable": self.allocator.can_afford(task.cost)})
            affordable = [r for r in rows if r["affordable"]]
            if not affordable:
                stop_reason = "budget_exhausted_for_every_task"
                break
            best = max(affordable, key=lambda r: r["net_value"])  # max keeps the first of equals: declared order
            if self.stopping.should_stop(best_expected_gain=best["expected_value"], best_cost=best["cost"]):
                stop_reason = "best_next_retry_below_min_net_gain"
                break
            if not self.allocator.allocate(best["cost"]):
                stop_reason = "allocation_refused"
                break
            granted[best["task_id"]] += 1
            grants.append({**best, "budget_remaining": self.allocator.remaining()})
        return {"retries": granted, "grants": grants, "stop_reason": stop_reason,
                "budget_spent": self.allocator.spent, "budget_remaining": self.allocator.remaining(),
                "rule": {"min_net_gain": self.stopping.min_net_gain, "cost_weight": self.stopping.cost_weight,
                         "max_retries": self.max_retries}}


__all__ = ["MAX_RETRIES", "RetryBudgetPolicy", "RetryTask"]
