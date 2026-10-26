# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Suggested mitigation, rollback, and compensation steps.

Templates (audit 002, AG-011): generic controls chosen from the action's declared flags. None is
executed or checked here, so they do not lower the assessed risk; the archive cut a high or
unknown risk to a "medium" residual on the strength of this text alone.
"""
from __future__ import annotations

from g6.action_gating.schema import ActionCapsule, ActionScenarioModel, ImpactAssessment, MitigationPlan


def mitigation_required(capsule: ActionCapsule, risk: ImpactAssessment) -> bool:
    return capsule.irreversible_or_costly_to_reverse or risk.aggregate_risk in {"high", "critical", "unknown"}


def build_mitigation_plan(
    capsule: ActionCapsule,
    risk: ImpactAssessment,
    scenario_model: ActionScenarioModel | None = None,
) -> MitigationPlan:
    checks = ["Verify target resource from source of truth.", "Verify caller authority for this action."]
    controls = ["Perform dry run or preview before execution.", "Narrow scope to the named target resource."]
    confirmations = []
    rollback = []
    compensation = []
    if capsule.external_side_effect or capsule.irreversible_or_costly_to_reverse:
        confirmations.append(f"Confirm consequence for {capsule.action_type} on {capsule.target_resource}.")
    if capsule.reversibility == "reversible":
        rollback.append("Restore the prior state from the captured pre-action value.")
    else:
        compensation.append("Escalate to operator for manual compensation if rollback is unavailable.")
    residual = risk.aggregate_risk  # suggested controls are not applied controls
    if scenario_model is None and mitigation_required(capsule, risk):
        checks.append("Complete scenario modelling before execution.")
    return MitigationPlan(
        preventive_controls=controls,
        confirmation_requirements=confirmations,
        pre_execution_checks=checks,
        rollback_steps=rollback,
        compensation_steps=compensation,
        monitoring_steps=["Monitor for duplicated execution and downstream automation."],
        stop_conditions=["Stop if target, authority, or backend state cannot be verified."],
        escalation_paths=["Escalate to human review for high or critical residual risk."],
        residual_risk=residual,
        residual_risk_rationale=[
            "Suggested controls only: none has been executed or checked, so residual risk equals the assessed risk."
        ],
    )
