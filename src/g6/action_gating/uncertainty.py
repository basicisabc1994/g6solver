# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Rumsfeld uncertainty prompts for action gating.

Templates (audit 002, AG-011): ``known_unknowns`` (and the assumptions to validate) are derived
from the capsule's missing facts; every other list is a fixed prompt, not a finding about this
action.
"""
from __future__ import annotations

from g6.action_gating.schema import ActionCapsule, ImpactAssessment, RumsfeldMatrix


def rumsfeld_required(capsule: ActionCapsule, risk: ImpactAssessment) -> bool:
    return risk.aggregate_risk in {"medium", "high", "critical", "unknown"} or capsule.reversibility == "unknown"


def build_rumsfeld_matrix(capsule: ActionCapsule, risk: ImpactAssessment) -> RumsfeldMatrix:
    known_unknowns = []
    if capsule.target_backend == "unknown":
        known_unknowns.append("Target backend is not known.")
    if capsule.target_resource == "unknown":
        known_unknowns.append("Target resource is not known.")
    if capsule.reversibility == "unknown":
        known_unknowns.append("Reversibility of this action is unknown.")
    if not capsule.observed_authority and capsule.required_authority:
        known_unknowns.append("Caller authority has not been verified.")
    if risk.aggregate_risk == "unknown":
        known_unknowns.append("Aggregate risk cannot be reduced to low with current facts.")
    return RumsfeldMatrix(
        known_knowns=[
            f"Requested action type: {capsule.action_type}.",
            f"Target backend: {capsule.target_backend}.",
            f"Target resource: {capsule.target_resource}.",
        ],
        known_unknowns=known_unknowns or ["No material known unknowns identified."],
        unknown_knowns=[
            "The user or organization may have policy, history, or constraints not present in this request."
        ],
        unknown_unknowns=[
            "Backend race conditions, third-party side effects, or downstream automation may appear only after execution."
        ],
        assumptions_to_validate=known_unknowns or ["Validate backend state immediately before execution."],
        hidden_knowledge_prompts=[
            "Check prior audit logs and user-provided constraints for this target."
        ],
        monitoring_tripwires=[
            "Stop and escalate on duplicate execution, unexpected recipient, or changed backend state."
        ],
        contingency_plans=["Use rollback or compensation path; otherwise escalate to human review."],
    )
