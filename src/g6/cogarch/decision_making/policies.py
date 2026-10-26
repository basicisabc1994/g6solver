# SPDX-License-Identifier: Apache-2.0
"""The next-experiment policy: the affordable experiment with the most expected information gain, or stop.

``PlanNextActionPolicy`` scores each candidate by ``expected_information_gain`` (an experiment that cannot change the
belief scores 0), keeps to a hard budget (``BoundedAllocator``) and stops by a value-of-information rule
(``VoIThresholdStop``). The pick is by gain alone among the affordable candidates; cost enters only through the
budget and the stopping rule, so a much cheaper experiment with slightly less gain loses (DECISION_MAKING-02, stated
rather than changed). Every decision is returned with its full working: the inputs, each candidate's gain, the rule
that fired and the budget left. ``record_decision`` appends that record to a ledger the caller holds. Deterministic:
no randomness; ties break by declared order.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from g6.cogarch.decision_making.budget import BoundedAllocator
from g6.cogarch.decision_making.stopping import VoIThresholdStop
from g6.cogarch.decision_making.voi import expected_information_gain

ACTION_RUN_EXPERIMENT = "RUN_EXPERIMENT"
ACTION_STOP = "STOP"


@dataclass(frozen=True)
class ExperimentCandidate:
    """A proposable experiment: a binary test of a binary hypothesis."""

    candidate_id: str
    prior: float
    true_positive_rate: float
    false_positive_rate: float
    cost: float = 0.0

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate_id must be non-empty")
        if not (self.cost >= 0.0 and math.isfinite(self.cost)):  # refuses NaN and inf too
            raise ValueError(f"cost must be a finite number >= 0, got {self.cost!r}")

    def expected_gain(self) -> float:
        return expected_information_gain(self.prior, self.true_positive_rate, self.false_positive_rate)


@dataclass
class PlanNextActionPolicy:
    """The affordable candidate with the most expected information gain, or STOP when the rule says nothing left is
    worth running or nothing is affordable."""

    allocator: BoundedAllocator
    stopping: VoIThresholdStop

    def decide(self, candidates: Sequence[ExperimentCandidate]) -> dict:
        if not candidates:
            raise ValueError("no candidates supplied")
        ids = [c.candidate_id for c in candidates]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate candidate ids")
        remaining = self.allocator.remaining()
        scored = []
        for candidate in candidates:
            gain = candidate.expected_gain()
            scored.append({"candidate_id": candidate.candidate_id, "expected_gain_bits": gain,
                           "cost": candidate.cost, "net_gain": self.stopping.net_gain(gain, candidate.cost),
                           "affordable": candidate.cost <= remaining})
        best = None
        for row in (r for r in scored if r["affordable"]):  # declared order: the first wins a tie
            if best is None or row["expected_gain_bits"] > best["expected_gain_bits"]:
                best = row
        rationale: dict[str, Any] = {"policy": "voi_argmax", "candidates": scored, "budget_remaining": remaining,
                                     "stopping_rule": {"min_net_gain": self.stopping.min_net_gain,
                                                       "cost_weight": self.stopping.cost_weight}}
        if best is None:
            rationale["stop_reason"] = "budget_exhausted_for_all_candidates"
            return {"action": ACTION_STOP, "chosen": None, "rationale": rationale}
        if self.stopping.should_stop(best_expected_gain=best["expected_gain_bits"], best_cost=best["cost"]):
            rationale["stop_reason"] = "best_net_gain_below_threshold"
            return {"action": ACTION_STOP, "chosen": None, "rationale": rationale}
        if not self.allocator.allocate(best["cost"]):  # the allocator has the last word
            rationale["stop_reason"] = "allocation_refused"
            return {"action": ACTION_STOP, "chosen": None, "rationale": rationale}
        return {"action": ACTION_RUN_EXPERIMENT, "chosen": best["candidate_id"], "rationale": rationale}


class Ledger(Protocol):
    """Anything a decision record can be appended to."""

    def append(self, kind: str, **fields: Any) -> dict:
        ...


def record_decision(ledger: Ledger, decision: Mapping, *, recorded_at: str, actor_id: str,
                    actor_role: str = "planner") -> dict:
    """Append a decision to the caller's ledger as ``DECISION_RECORDED``; returns what the ledger returns. The
    actor fields are the caller's claim, kept as given."""
    return ledger.append("DECISION_RECORDED", recorded_at=recorded_at, actor_id=actor_id, actor_role=actor_role,
                         payload=dict(decision))


__all__ = ["ACTION_RUN_EXPERIMENT", "ACTION_STOP", "ExperimentCandidate", "Ledger", "PlanNextActionPolicy",
           "record_decision"]
