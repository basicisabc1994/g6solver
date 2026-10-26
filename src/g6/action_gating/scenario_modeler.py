# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Consequence scenarios for material actions.

Templates, not predictions (audit 002, AG-011): each case is a fixed narrative; severity follows
the assessed risk and recoverability the declared reversibility. No likelihood is estimated, so
every case says ``unknown`` (the archive marked them all ``possible``).
"""
from __future__ import annotations

from g6.action_gating.schema import ActionCapsule, ActionScenarioModel, ImpactAssessment, Scenario


def scenario_required(capsule: ActionCapsule, risk: ImpactAssessment) -> bool:
    return (
        capsule.irreversible_or_costly_to_reverse
        or capsule.external_side_effect
        or capsule.real_world_effect
        or risk.aggregate_risk in {"high", "critical", "unknown"}
    )


def build_action_scenarios(capsule: ActionCapsule, risk: ImpactAssessment | None = None) -> ActionScenarioModel:
    severity = "high" if risk and risk.aggregate_risk in {"high", "critical", "unknown"} else "medium"
    entity = capsule.target_resource
    base = {
        "affected_entities": capsule.affected_entities or ([entity] if entity != "unknown" else []),
        "severity": severity,
        "assumptions": ["The action target and authority are accurate."],
    }
    return ActionScenarioModel(
        best_case=Scenario(
            summary=f"{capsule.action_type} completes as intended.",
            likely_consequences=["Requested state change occurs with no follow-on issue."],
            likelihood="unknown",
            detectability="easy",
            recoverability="easy" if capsule.reversibility == "reversible" else "hard",
            **base,
        ),
        expected_case=Scenario(
            summary="The action succeeds and requires monitoring for downstream effects.",
            likely_consequences=["Caller must verify the target state after execution."],
            likelihood="unknown",
            detectability="moderate",
            recoverability="moderate",
            **base,
        ),
        failure_case=Scenario(
            summary="The action fails before completion.",
            likely_consequences=["No useful value is delivered; operator needs error review."],
            likelihood="unknown",
            detectability="easy",
            recoverability="moderate",
            **base,
        ),
        partial_failure_case=Scenario(
            summary="The action partially completes.",
            likely_consequences=["State may be inconsistent until reconciled."],
            likelihood="unknown",
            detectability="moderate",
            recoverability="hard",
            **base,
        ),
        incorrect_assumption_case=Scenario(
            summary="The action target or intent is wrong.",
            likely_consequences=["The wrong resource or external party may be affected."],
            likelihood="unknown",
            detectability="hard",
            recoverability="hard",
            **base,
        ),
        abuse_or_misuse_case=Scenario(
            summary="The action is duplicated, retried, or used outside intended scope.",
            likely_consequences=["Impact may exceed the user's intended blast radius."],
            likelihood="unknown",
            detectability="moderate",
            recoverability="hard",
            **base,
        ),
        rollback_case=Scenario(
            summary="Rollback or compensation is attempted.",
            likely_consequences=["Recovery may require backup, manual repair, or notification."],
            likelihood="unknown",
            detectability="moderate",
            recoverability="impossible" if capsule.reversibility == "irreversible" else "moderate",
            **base,
        ),
    )
