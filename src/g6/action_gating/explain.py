# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""User-facing action risk explanations."""
from __future__ import annotations

from g6.action_gating.schema import ActionCapsule, GateDecision


def render_user_risk_message(capsule: ActionCapsule, decision: GateDecision) -> str:
    matrix = decision.rumsfeld_matrix
    mitigation = decision.mitigation_plan
    worst = ""
    if decision.scenario_model is not None:
        worst = decision.scenario_model.incorrect_assumption_case.summary
    known = "; ".join(matrix.known_knowns[:3]) if matrix else "Action facts are limited."
    unknowns = "; ".join(matrix.known_unknowns[:3]) if matrix else "Unresolved risk remains."
    hidden = "; ".join(matrix.unknown_knowns[:2]) if matrix else "Prior constraints may be missing."
    hard = "; ".join(matrix.unknown_unknowns[:2]) if matrix else "Downstream effects may be unpredictable."
    controls = "; ".join(mitigation.preventive_controls[:2]) if mitigation else "No mitigation plan is available."
    residual = mitigation.residual_risk if mitigation else decision.risk.aggregate_risk
    confirmation = decision.required_confirmation_phrase or "No confirmation required."
    return (
        "Before I do this, here is the risk assessment:\n\n"
        f"**Action:** {capsule.user_visible_summary}\n\n"
        f"**Why this matters:** {decision.required_user_message}\n\n"
        f"**Known facts:** {known}\n\n"
        f"**Unresolved uncertainties:** {unknowns}\n\n"
        f"**Possible hidden assumptions:** {hidden}\n\n"
        f"**Hard-to-predict risks:** {hard}\n\n"
        f"**Worst plausible outcome:** {worst or 'The action affects the wrong target or cannot be recovered cleanly.'}\n\n"
        f"**Mitigation plan:** {controls}\n\n"
        f"**Residual risk:** {residual}\n\n"
        f"**Confirmation required:** {confirmation}"
    )
