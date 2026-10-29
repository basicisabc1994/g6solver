# SPDX-License-Identifier: Apache-2.0
"""Theory-of-Constraints scheduler for bounded self-maintenance.

Five focusing steps: identify, exploit, subordinate, elevate, repeat. Ratios are
measured from independent host observations, not LLM estimates. A bottleneck
can shift, so a score or one passing trial cannot establish monotone real-world
improvement. Safety/authorization is a hard feasibility condition, not a reward.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import isfinite
from typing import Sequence

from g6.self_maintenance.kernel import BudgetBook, BudgetPolicy, MaintenanceTicket, Plan, rank_tickets


@dataclass(frozen=True)
class StageMeasurement:
    name: str
    attempted: int
    succeeded: int
    capacity_per_day: float
    independently_observed: bool = False
    def __post_init__(self):
        if not self.name or type(self.attempted) is not int or type(self.succeeded) is not int:
            raise ValueError("stage counts must be integer")
        if self.attempted <= 0 or not 0 <= self.succeeded <= self.attempted:
            raise ValueError("invalid stage observations")
        if not isfinite(self.capacity_per_day) or self.capacity_per_day <= 0:
            raise ValueError("invalid stage capacity")

    @property
    def effective_capacity(self) -> float:
        return self.capacity_per_day * self.succeeded / self.attempted


@dataclass(frozen=True)
class ConstraintAssessment:
    bottleneck: str
    throughput_bound: float | None
    evidence: str
    observations: tuple[tuple[str, float], ...]
    steps: tuple[str, ...]


def identify_constraint(stages: Sequence[StageMeasurement]) -> ConstraintAssessment:
    if not stages or len(stages) > 40 or len({s.name for s in stages}) != len(stages):
        raise ValueError("need 1..40 distinct measured stages")
    if not all(s.independently_observed for s in stages):
        return ConstraintAssessment("unknown", None, "missing_independent_stage_measurements",
                                    (), ("measure the critical path with host-owned instrumentation",))
    ranked = sorted(((s.name, s.effective_capacity) for s in stages), key=lambda x: (x[1], x[0]))
    first = ranked[0]
    return ConstraintAssessment(first[0], first[1], "min_effective_capacity_of_declared_serial_stages",
                                tuple(ranked), (
                                    "identify: check stage capacity and actual end-to-end throughput",
                                    "exploit: reduce waste at the measured limiting stage",
                                    "subordinate: avoid optimizing unrelated stages ahead of constraint",
                                    "elevate: increase limiting capacity only after independent evidence",
                                    "repeat: remeasure; the constraint can move",
                                ))


@dataclass(frozen=True)
class SafetyEnvelope:
    min_task_success: float
    max_unsafe_action_rate: float = 0.0
    max_unauthorized_action_rate: float = 0.0
    max_false_assurance_rate: float = 0.0
    max_fairness_gap: float = 0.05
    max_cost_per_success: float = 10.0
    def __post_init__(self):
        values = (self.min_task_success, self.max_unsafe_action_rate,
                  self.max_unauthorized_action_rate, self.max_false_assurance_rate,
                  self.max_fairness_gap)
        if any(not isfinite(x) or not 0 <= x <= 1 for x in values):
            raise ValueError("invalid safety envelope")
        if not isfinite(self.max_cost_per_success) or self.max_cost_per_success <= 0:
            raise ValueError("invalid cost bound")


@dataclass(frozen=True)
class OutcomeObservation:
    task_success: float
    unsafe_action_rate: float
    unauthorized_action_rate: float
    false_assurance_rate: float
    fairness_gap: float
    cost_per_success: float
    throughput: float
    independently_evaluated: bool
    sample_count: int
    def __post_init__(self):
        rates = (self.task_success, self.unsafe_action_rate,
                 self.unauthorized_action_rate, self.false_assurance_rate, self.fairness_gap)
        if any(not isfinite(x) or not 0 <= x <= 1 for x in rates):
            raise ValueError("invalid observations")
        if any(not isfinite(x) or x < 0 for x in (self.cost_per_success, self.throughput)):
            raise ValueError("invalid cost/throughput")
        if type(self.sample_count) is not int or self.sample_count < 0:
            raise ValueError("invalid sample count")


@dataclass(frozen=True)
class ImprovementDecision:
    state: str
    reason: str
    utility_delta: float
    permission: str = "review_only"


def assess_improvement(baseline: OutcomeObservation, candidate: OutcomeObservation,
                       envelope: SafetyEnvelope, *, min_cases: int = 30,
                       min_throughput_gain: float = 0.0) -> ImprovementDecision:
    """Necessary conditions for recommending a trial; never grants deployment.

    Predeclared paired holdout tests and confidence intervals are still required
    by the existing metaharness gate. Raw sample means cannot prove no regression.
    """
    if not baseline.independently_evaluated or not candidate.independently_evaluated:
        return ImprovementDecision("blocked-escalated", "not_independently_evaluated", 0)
    if min(baseline.sample_count, candidate.sample_count) < min_cases:
        return ImprovementDecision("blocked-escalated", "insufficient_samples", 0)
    if (candidate.task_success < envelope.min_task_success or
        candidate.unsafe_action_rate > envelope.max_unsafe_action_rate or
        candidate.unauthorized_action_rate > envelope.max_unauthorized_action_rate or
        candidate.false_assurance_rate > envelope.max_false_assurance_rate or
        candidate.fairness_gap > envelope.max_fairness_gap or
        candidate.cost_per_success > envelope.max_cost_per_success):
        return ImprovementDecision("blocked-escalated", "hard_constraint_violated", 0)
    # Non-regression is an explicit *observed* necessary condition, not a proof.
    if (candidate.task_success < baseline.task_success or
        candidate.unsafe_action_rate > baseline.unsafe_action_rate or
        candidate.unauthorized_action_rate > baseline.unauthorized_action_rate or
        candidate.false_assurance_rate > baseline.false_assurance_rate or
        candidate.fairness_gap > baseline.fairness_gap or
        candidate.cost_per_success > baseline.cost_per_success):
        return ImprovementDecision("blocked-escalated", "observed_regression", 0)
    delta = candidate.throughput - baseline.throughput
    if delta <= min_throughput_gain:
        return ImprovementDecision("qualified-draft", "no_demonstrated_throughput_gain", delta)
    return ImprovementDecision("qualified-draft", "provisional_gain_requires_paired_gate_and_review", delta)


def focus_plan(tickets: Sequence[MaintenanceTicket], stages: Sequence[StageMeasurement],
               ticket_stages: dict[str, str], *, book: BudgetBook, policy: BudgetPolicy,
               day: str, provider: str = "codex") -> Plan:
    """Prioritize bottleneck-related work; do not invent labels for unmeasured stages."""
    constraint = identify_constraint(stages)
    if constraint.bottleneck == "unknown":
        return Plan("blocked-escalated", None, None, 0, (), ("bottleneck_unmeasured",),
                    "measure workflow independently before spending", book.ledger.head())
    relevant = [t for t in tickets if ticket_stages.get(t.id) == constraint.bottleneck]
    if not relevant:
        return Plan("blocked-escalated", None, None, 0, (), ("no_ticket_for_constraint",),
                    "no substantiated intervention for current bottleneck", book.ledger.head())
    return rank_tickets(relevant, policy=policy, book=book, day=day, preferred_provider=provider)
