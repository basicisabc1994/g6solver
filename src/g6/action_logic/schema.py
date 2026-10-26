# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Frozen input/output types for the Dörner action-logic controls.

These operationalise ``G6_Action_Logic.md`` (Dörner, *The Logic of Failure*). They are
deliberately plain frozen dataclasses (matching ``mvp.core.objective_contract`` /
``mvp.core.decision_record`` / ``mvp.epistemic_integrity.adjudicator``) so the control
floor in ``controls.py`` stays a pure, deterministic, LLM-free function set.

Severity is expressed in the SAME vocabulary the real action gate uses
(``mvp.action_gating.decision_engine._SEVERITY_RANK``: ``notice < review < block``) so
the controls compose monotonically into ``decide_gate`` without a translation layer.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

# Severity ladder, shared with action_gating.decision_engine. ``None`` = no control fired.
SEVERITY_RANK: dict[str | None, int] = {None: -1, "notice": 0, "review": 1, "block": 2}


def raise_severity(current: str | None, candidate: str | None) -> str | None:
    """Monotonic combine: never lower an already-raised severity (Dörner controls only tighten)."""
    if SEVERITY_RANK[candidate] > SEVERITY_RANK[current]:
        return candidate
    return current


# --------------------------------------------------------------------------- #
# G6-INV-05 — closed-loop action / ballistic-action prevention (AT-06)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class VerificationContract:
    """The closed-loop verification plan every *material* action MUST carry (G6-INV-05).

    Dörner's "ballistic action" is fire-and-forget: an operator commits a side-effecting
    action and never checks whether it worked. The spec is unambiguous — "An action
    without a verification plan MUST be blocked." A contract is *complete* only when every
    element the invariant lists is present — including both leading AND lagging indicators,
    each with at least one non-blank entry (audit 003, AL-011; the archive accepted either
    kind, or a blank string); an incomplete contract is still ballistic.

    Complete is syntactic: these are declared strings. Nothing checks that the monitor,
    stop threshold or rollback exist or work, so a complete contract is not an operationally
    verified one.
    """

    expected_result: str = ""
    observation_window: str = ""
    leading_indicators: tuple[str, ...] = ()
    lagging_indicators: tuple[str, ...] = ()
    monitor: str = ""
    verification_time: str = ""
    stop_threshold: str = ""
    rollback_procedure: str = ""
    model_update_rule: str = ""

    def missing_elements(self) -> tuple[str, ...]:
        missing: list[str] = []
        if not self.expected_result.strip():
            missing.append("expected_result")
        if not self.observation_window.strip():
            missing.append("observation_window")
        if not any(i.strip() for i in self.leading_indicators):
            missing.append("leading_indicators")
        if not any(i.strip() for i in self.lagging_indicators):
            missing.append("lagging_indicators")
        if not self.monitor.strip():
            missing.append("monitor")
        if not self.verification_time.strip():
            missing.append("verification_time")
        if not self.stop_threshold.strip():
            missing.append("stop_threshold")
        if not self.rollback_procedure.strip():
            missing.append("rollback_procedure")
        if not self.model_update_rule.strip():
            missing.append("model_update_rule")
        return tuple(missing)

    def is_complete(self) -> bool:
        return not self.missing_elements()


# --------------------------------------------------------------------------- #
# G6-INV-08 — coordinated interventions (AT-05)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ActionIntent:
    """A proposed or active intervention, for collision detection (G6-INV-08).

    Fields are *declared* facts about an intervention (mirroring the EI floor, which
    enforces declared evidence). ``window`` is an ISO-8601 interval ``start/end`` or a bucket
    token (``w1``): intervals overlap when they intersect, equal tokens overlap; anything
    that cannot be proven separate (empty, another time format) is treated as overlapping
    (fail-safe). ``action_id`` must be unique: a different action reusing an id is an
    identity collision.
    """

    action_id: str
    target_resource: str = ""
    direction: str = ""  # "increase" | "decrease" | "set" | "" — objective direction on target
    shared_resources: tuple[str, ...] = ()
    costs: tuple[tuple[str, float], ...] = ()  # per-shared-resource draw, for combined-threshold
    effect_signature: str = ""
    window: str = ""
    relies_on_assumptions: tuple[str, ...] = ()
    invalidates_assumptions: tuple[str, ...] = ()
    # Declared idempotency: two actions with the same effect AND the same non-empty key are
    # safe to run in sequence (the second is a no-op). Without it a duplicate effect is a
    # real duplicate (a second charge, a second email) and cannot be serialised away.
    idempotency_key: str = ""


@dataclass(frozen=True)
class ActionConflict:
    """A detected collision between a proposed action and an active/scheduled one."""

    kind: str  # resource_contention | contradictory_objectives | duplicate_effects |
    #            combined_threshold_breach | invalidated_assumption
    with_action: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "with_action": self.with_action, "detail": self.detail}


# --------------------------------------------------------------------------- #
# §9 — weak-signal / future-risk review (AT-01) + G6-INV-03 temporal state
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ControlVariable:
    """A monitored system variable as a (value, threshold, trend) triple (G6-INV-03)."""

    name: str
    value: float | None = None
    threshold: float | None = None
    trend: str = "unknown"  # rising | falling | flat | unknown
    is_resource: bool = False  # a finite supporting resource that depletes downward
    acknowledged: bool = False  # the emerging risk has been represented / explicitly accepted


@dataclass(frozen=True)
class WorldState:
    """A snapshot of monitored variables plus which future horizons were evaluated (§9)."""

    variables: tuple[ControlVariable, ...] = ()
    horizons_evaluated: tuple[str, ...] = ()  # horizons beyond the immediate objective


# --------------------------------------------------------------------------- #
# G6-INV-06 — monitoring survives crises (AT-07)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class MonitoringCoverage:
    """Monitoring coverage, for the crisis-monitoring invariant (G6-INV-06).

    A shorter review interval (higher ``verification_per_hour``) is allowed in a crisis;
    a *drop* in any dimension is a critical control failure.
    """

    telemetry_signals: int = 0
    verification_per_hour: float = 0.0
    audit_logging: bool = False
    anomaly_detection: bool = False
    escalation_ready: bool = False


# --------------------------------------------------------------------------- #
# G6-INV-04 / G6-INV-09 / §11 — hypotheses are not facts (AT-08, AT-12)
# --------------------------------------------------------------------------- #

class HypothesisStatus(str, Enum):
    """Lifecycle of an operational hypothesis. There is no terminal "proven": §11 forbids a
    successful outcome from auto-validating the causal model, so confirmation lands in
    SUPPORTED (corroborated, still falsifiable), never a validated absorbing state."""

    ACTIVE = "active"  # in use, not yet tested against an outcome
    SUPPORTED = "supported"  # corroborated by an outcome — NOT proven
    WEAKENED = "weakened"  # disconfirming evidence present; confidence reduced
    FALSIFIED = "falsified"  # disconfirmed by an observed outcome


@dataclass(frozen=True)
class Hypothesis:
    """An operational hypothesis (G6-INV-04). MUST carry confidence, supporting evidence,
    possible falsifiers, and a falsification/review condition to be well-formed."""

    hypothesis_id: str
    statement: str
    confidence: float = 0.5
    supporting_evidence: tuple[str, ...] = ()
    disconfirming_evidence: tuple[str, ...] = ()
    falsification_condition: str = ""
    status: HypothesisStatus = HypothesisStatus.ACTIVE

    def is_well_formed(self) -> bool:
        return bool(self.statement.strip() and self.falsification_condition.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "statement": self.statement,
            "confidence": self.confidence,
            "supporting_evidence": list(self.supporting_evidence),
            "disconfirming_evidence": list(self.disconfirming_evidence),
            "falsification_condition": self.falsification_condition,
            "status": self.status.value,
        }


class Outcome(str, Enum):
    """Reflection-engine outcome classes (§11)."""

    CONFIRMED = "confirmed"
    PARTIALLY_CONFIRMED = "partially_confirmed"
    DISCONFIRMED = "disconfirmed"
    INCONCLUSIVE = "inconclusive"
    HARMFUL = "harmful"
    CONFOUNDED = "confounded"


# --------------------------------------------------------------------------- #
# Combined assessment result
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ActionLogicAssessment:
    """The combined verdict of the per-action Dörner controls (verification + coordination +
    weak-signal). Crisis monitoring is a system-level invariant assessed separately."""

    severity: str | None = None
    reason_codes: tuple[str, ...] = ()
    items: tuple[dict[str, Any], ...] = ()

    def blocks(self) -> bool:
        return self.severity == "block"

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "reason_codes": list(self.reason_codes),
            "items": [dict(i) for i in self.items],
        }


__all__ = [
    "SEVERITY_RANK",
    "raise_severity",
    "VerificationContract",
    "ActionIntent",
    "ActionConflict",
    "ControlVariable",
    "WorldState",
    "MonitoringCoverage",
    "HypothesisStatus",
    "Hypothesis",
    "Outcome",
    "ActionLogicAssessment",
]
