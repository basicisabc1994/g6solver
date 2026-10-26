# SPDX-License-Identifier: Apache-2.0
"""A bounded, one-way-tightening allocator.

A hard total that spending never exceeds, and a ``tighten`` that only ever lowers the bound. ``allocate`` refuses
honestly (``False``, nothing spent) when a cost does not fit.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BoundedAllocator:
    """A hard-capped spend tracker: every change keeps ``0 <= spent <= total``."""

    total: float
    spent: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self.total = float(self.total)
        if self.total < 0.0 or self.total != self.total or self.total == float("inf"):
            raise ValueError(f"total budget must be a finite number >= 0, got {self.total!r}")

    def remaining(self) -> float:
        return max(0.0, self.total - self.spent)

    def can_afford(self, cost: float) -> bool:
        c = float(cost)
        if c < 0.0 or c != c:
            raise ValueError(f"cost must be >= 0, got {cost!r}")
        return c <= self.remaining()

    def allocate(self, cost: float) -> bool:
        """Spend ``cost`` if it fits; refuse (``False``, nothing spent) if not. The clamp after adding keeps
        ``spent <= total`` exactly, where float rounding could push it one ulp over."""
        if not self.can_afford(cost):
            return False
        self.spent = min(self.spent + float(cost), self.total)
        return True

    def tighten(self, new_total: float) -> float:
        """Lower the cap (never raise it, and never below what is already spent); returns the effective total."""
        nt = float(new_total)
        if nt < 0.0 or nt != nt:
            raise ValueError(f"new_total must be >= 0, got {new_total!r}")
        self.total = min(self.total, max(nt, self.spent))
        return self.total

    def exhausted(self) -> bool:
        return self.remaining() <= 0.0


__all__ = ["BoundedAllocator"]
