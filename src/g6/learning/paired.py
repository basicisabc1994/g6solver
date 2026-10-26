# SPDX-License-Identifier: Apache-2.0
"""Paired outcomes: the same instance run with and without a theory (or harness), and what the pairs show.

A pair is informative only when its two runs disagree. ``credit`` names what one pair says; ``ArmComparison`` adds
pairs up on one set of instances, with an exact one-sided sign test (the archive bucketed a McNemar chi-squared into
five values and paired a one-instance baseline with a full final arm, audit 006 LL-004).
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from g6.learning.schema import TaskResult

CONFIRMED = "confirmed"
DISCONFIRMED = "disconfirmed"
NO_DISCRIMINATING_EVIDENCE = "no_discriminating_evidence"


@dataclass(frozen=True)
class PairedOutcome:
    """Whether one instance passed with the theory and without it (same instance, same cycle)."""

    instance_id: str
    with_theory: bool
    without_theory: bool


def credit(with_theory: bool, without_theory: bool) -> str:
    """What one pair says about the theory: it helped, it hurt, or the pair cannot tell (both runs agree)."""
    if with_theory and not without_theory:
        return CONFIRMED
    if without_theory and not with_theory:
        return DISCONFIRMED
    return NO_DISCRIMINATING_EVIDENCE


def sign_test(wins: int, losses: int) -> float:
    """One-sided exact sign test: P(at least ``wins`` of ``wins + losses`` fair coin flips). 1.0 with no flips."""
    n = wins + losses
    return 1.0 if n == 0 else sum(math.comb(n, k) for k in range(wins, n + 1)) / 2 ** n


@dataclass(frozen=True)
class ArmComparison:
    """A baseline arm and an adapted arm on exactly the same instances."""

    pairs: tuple[PairedOutcome, ...]
    theory_ids: tuple[str, ...] = ()

    @classmethod
    def from_results(cls, baseline: Sequence[TaskResult], adapted: Sequence[TaskResult],
                     theory_ids: Sequence[str] = ()) -> ArmComparison:
        base = {r.instance_id: r.passed for r in baseline}
        adapt = {r.instance_id: r.passed for r in adapted}
        if base.keys() != adapt.keys() or len(base) != len(baseline) or len(adapt) != len(adapted):
            raise ValueError("G6_E_LEARNING_UNPAIRED: the two arms did not run the same instances")
        return cls(tuple(PairedOutcome(r.instance_id, adapt[r.instance_id], base[r.instance_id]) for r in baseline),
                   tuple(theory_ids))

    @property
    def n(self) -> int:
        return len(self.pairs)

    @property
    def baseline_passed(self) -> int:
        return sum(p.without_theory for p in self.pairs)

    @property
    def adapted_passed(self) -> int:
        return sum(p.with_theory for p in self.pairs)

    @property
    def wins(self) -> int:
        return sum(credit(p.with_theory, p.without_theory) == CONFIRMED for p in self.pairs)

    @property
    def losses(self) -> int:
        return sum(credit(p.with_theory, p.without_theory) == DISCONFIRMED for p in self.pairs)

    @property
    def ties(self) -> int:
        return self.n - self.wins - self.losses

    @property
    def baseline_accuracy(self) -> float:
        return self.baseline_passed / self.n if self.n else 0.0

    @property
    def adapted_accuracy(self) -> float:
        return self.adapted_passed / self.n if self.n else 0.0

    @property
    def lift(self) -> float:
        """Adapted minus baseline accuracy, on the same instances."""
        return self.adapted_accuracy - self.baseline_accuracy

    @property
    def p_one_sided(self) -> float:
        return sign_test(self.wins, self.losses)

    def to_dict(self) -> dict[str, Any]:
        return {"n": self.n, "baseline_passed": self.baseline_passed, "adapted_passed": self.adapted_passed,
                "baseline_accuracy": self.baseline_accuracy, "adapted_accuracy": self.adapted_accuracy,
                "lift": self.lift, "wins": self.wins, "losses": self.losses, "ties": self.ties,
                "p_one_sided": round(self.p_one_sided, 6), "theory_ids": list(self.theory_ids)}


__all__ = ["CONFIRMED", "DISCONFIRMED", "NO_DISCRIMINATING_EVIDENCE", "ArmComparison", "PairedOutcome", "credit",
           "sign_test"]
