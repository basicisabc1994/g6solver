# SPDX-License-Identifier: Apache-2.0

"""Benign safety QA checks for gate decisions."""
from __future__ import annotations

from g6.action_gating.schema import ActionCapsule, DegradationNotice, MitigationPlan, SafetyQAAssurance


def run_safety_qa(
    capsule: ActionCapsule,
    mitigation_plan: MitigationPlan | None,
    degradation: list[DegradationNotice],
    required_confirmation_phrase: str | None,
) -> SafetyQAAssurance:
    issues: list[str] = []
    fixes: list[str] = []
    high = capsule.external_side_effect or capsule.irreversible_or_costly_to_reverse or capsule.real_world_effect
    if high and mitigation_plan is None:
        issues.append("High-risk action has no mitigation plan.")
        fixes.append("Generate mitigation, rollback, or compensation plan before execution.")
    if high and not required_confirmation_phrase:
        issues.append("High-risk action lacks consequence-specific confirmation language.")
        fixes.append("Provide a confirmation phrase naming the action and target.")
    if any(d.blocks_execution for d in degradation):
        issues.append("Blocking degradation is present.")
        fixes.append("Restore missing dependency or block execution.")
    return SafetyQAAssurance(
        policy_check_passed=True,
        authority_check_passed=not (capsule.required_authority and not capsule.observed_authority and high),
        consent_check_passed=not high or bool(required_confirmation_phrase),
        reversibility_check_passed=not (capsule.irreversible_or_costly_to_reverse and capsule.reversibility == "reversible"),
        mitigation_check_passed=not (high and mitigation_plan is None),
        degradation_check_passed=not any(d.blocks_execution for d in degradation),
        explanation_check_passed=not high or bool(required_confirmation_phrase),
        detected_issues=issues,
        required_fixes_before_execution=fixes,
        confidence=None,  # rule checks provide no confidence estimate
    )
