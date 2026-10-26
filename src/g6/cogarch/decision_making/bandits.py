# SPDX-License-Identifier: Apache-2.0
"""Seeded multi-armed bandit selectors.

- ``ThompsonBernoulli``: Thompson sampling over Beta posteriors, the uniform prior ``Beta(1, 1)`` or a structural
  prior ``(1 + k s, 1 + k (1 - s))`` from a score ``s`` per arm, and optional epsilon exploration (off by default).
- ``UCB1``: every arm once in declared order, then ``argmax mean + sqrt(exploration ln t / n)``; ties break by
  declared order.
- ``BudgetedBandit``: a bandit behind a hard budget: arms whose cost exceeds what remains are masked, and spending
  goes through a :class:`~g6.cogarch.decision_making.budget.BoundedAllocator`.

Every random draw comes from ``random.Random(seed)``: the same seed and history give the same choice.
"""
from __future__ import annotations

import math
import random
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from g6.cogarch.decision_making.budget import BoundedAllocator


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _arms(arm_ids: Iterable[str]) -> tuple[str, ...]:
    arms = tuple(arm_ids)
    if not arms:
        raise ValueError("at least one arm required")
    if len(set(arms)) != len(arms):
        raise ValueError("duplicate arm ids")
    return arms


@dataclass
class ThompsonBernoulli:
    """Seeded Thompson sampling over Bernoulli arms with Beta posteriors."""

    arm_ids: tuple[str, ...]
    seed: int = 0
    structural_scores: Mapping[str, float] | None = None
    prior_strength: float = 0.0  # k; 0 gives the uniform Beta(1, 1) prior
    eps_floor: float = 0.0
    eps_initial: float = 0.0
    eps_decay: float = 1.0
    _posterior: dict[str, tuple[float, float]] = field(default_factory=dict, init=False)
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.arm_ids = _arms(self.arm_ids)
        k = float(self.prior_strength)
        if not math.isfinite(k) or k < 0.0:
            raise ValueError(f"prior_strength must be a finite number >= 0, got {self.prior_strength!r}")
        scores = self.structural_scores or {}
        unknown = sorted(set(scores) - set(self.arm_ids))
        if unknown:
            raise ValueError(f"a structural score names no arm: {unknown[0]!r}")
        if any(not math.isfinite(float(v)) for v in scores.values()):
            raise ValueError("structural scores must be finite")
        self._rng = random.Random(self.seed)
        for arm in self.arm_ids:
            s = _clamp01(scores.get(arm, 0.5))
            self._posterior[arm] = (1.0 + k * s, 1.0 + k * (1.0 - s))

    def posterior(self, arm_id: str) -> tuple[float, float]:
        return self._posterior[arm_id]

    def epsilon(self, iteration: int) -> float:
        return max(self.eps_floor, self.eps_initial * (self.eps_decay ** iteration))

    def select(self, mask: Iterable[str] = (), iteration: int = 0) -> str | None:
        """An arm; ``None`` when every arm is masked (exhaustion said, never a made-up choice)."""
        masked = set(mask)
        available = [a for a in self.arm_ids if a not in masked]
        if not available:
            return None
        if len(available) == 1:
            return available[0]
        if self._rng.random() < self.epsilon(iteration):
            return self._rng.choice(available)
        best_arm, best_theta = available[0], -1.0
        for arm in available:
            alpha, beta = self._posterior[arm]
            theta = self._rng.betavariate(alpha, beta)
            if theta > best_theta:
                best_theta, best_arm = theta, arm
        return best_arm

    def update(self, arm_id: str, success: bool) -> None:
        if arm_id not in self._posterior:
            raise ValueError(f"unknown arm {arm_id!r}")
        alpha, beta = self._posterior[arm_id]
        self._posterior[arm_id] = (alpha + 1.0, beta) if success else (alpha, beta + 1.0)


@dataclass
class UCB1:
    """Deterministic UCB1; ties break by declared arm order, with no hidden randomness."""

    arm_ids: tuple[str, ...]
    exploration: float = 2.0
    _counts: dict[str, int] = field(default_factory=dict, init=False)
    _sums: dict[str, float] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.arm_ids = _arms(self.arm_ids)
        if not math.isfinite(self.exploration) or self.exploration < 0:
            raise ValueError(f"exploration must be a finite number >= 0, got {self.exploration!r}")
        for arm in self.arm_ids:
            self._counts[arm] = 0
            self._sums[arm] = 0.0

    def select(self, mask: Iterable[str] = ()) -> str | None:
        masked = set(mask)
        available = [a for a in self.arm_ids if a not in masked]
        if not available:
            return None
        for arm in available:  # unexplored arms first, in declared order
            if self._counts[arm] == 0:
                return arm
        total = sum(self._counts[a] for a in available)
        best_arm, best_score = available[0], -math.inf
        for arm in available:
            n = self._counts[arm]
            score = self._sums[arm] / n + math.sqrt(self.exploration * math.log(total) / n)
            if score > best_score:  # strict: the earlier arm wins a tie
                best_score, best_arm = score, arm
        return best_arm

    def update(self, arm_id: str, reward: float) -> None:
        if arm_id not in self._counts:
            raise ValueError(f"unknown arm {arm_id!r}")
        if not math.isfinite(float(reward)):
            raise ValueError(f"reward must be finite, got {reward!r}")
        self._counts[arm_id] += 1
        self._sums[arm_id] += float(reward)


@dataclass
class BudgetedBandit:
    """A bandit behind a hard budget: unaffordable arms are masked and every spend goes through the allocator, so
    the cap holds whatever the selection policy wants."""

    base: ThompsonBernoulli | UCB1
    costs: Mapping[str, float]
    allocator: BoundedAllocator

    def __post_init__(self) -> None:
        for arm in self.base.arm_ids:
            if arm not in self.costs:
                raise ValueError(f"no cost declared for arm {arm!r}")
            cost = float(self.costs[arm])
            if cost != cost or cost < 0.0:
                raise ValueError(f"cost for arm {arm!r} must be >= 0, got {cost!r}")

    def unaffordable(self) -> set[str]:
        remaining = self.allocator.remaining()
        return {a for a in self.base.arm_ids if float(self.costs[a]) > remaining}

    def select(self, mask: Iterable[str] = (), **kwargs) -> str | None:
        """An affordable arm; ``None`` when none is."""
        return self.base.select(mask=set(mask) | self.unaffordable(), **kwargs)

    def commit(self, arm_id: str) -> bool:
        """Spend the arm's cost; ``False`` (nothing spent) if it no longer fits."""
        if arm_id not in self.costs:
            raise ValueError(f"unknown arm {arm_id!r}")
        return self.allocator.allocate(float(self.costs[arm_id]))

    def update(self, arm_id: str, outcome) -> None:  # noqa: ANN001 - a bool for Thompson, a reward for UCB1
        self.base.update(arm_id, outcome)


__all__ = ["UCB1", "BudgetedBandit", "ThompsonBernoulli"]
