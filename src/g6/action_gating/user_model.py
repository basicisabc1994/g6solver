# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""User mental-model notes for risk communication.

Templates, not estimates (audit 002, AG-011): the goal, assumptions and needs are fixed text
chosen from the action's declared flags. Nothing about the user is measured, so no confidence
is reported; the archive emitted fixed 0.55 / 0.65 values as if they were measured.
"""
from __future__ import annotations

from g6.action_gating.schema import ActionCapsule, UserMentalModelEstimate


def estimate_user_mental_model(capsule: ActionCapsule) -> UserMentalModelEstimate:
    high = capsule.external_side_effect or capsule.irreversible_or_costly_to_reverse or capsule.real_world_effect
    missing = []
    if high:
        missing.append("The user has not explicitly acknowledged external or irreversible consequences.")
    if capsule.target_resource == "unknown":
        missing.append("The user has not pinned the exact target resource.")
    return UserMentalModelEstimate(
        likely_user_goal=f"Complete {capsule.action_type} successfully.",
        likely_user_assumptions=[
            "The action will affect only the named target.",
            "The backend state still matches the user's intent.",
        ],
        possible_user_misunderstandings=[
            "The action may be harder to undo than expected.",
            "External systems or people may be affected after execution.",
        ] if high else [],
        missing_user_acknowledgements=missing,
        user_capability_signals=["No protected traits or identity attributes inferred."],
        user_capability_uncertainties=["The user's authority and risk understanding are not proven by the request."],
        communication_needs=["Use consequence-specific language.", "Separate known facts from unresolved uncertainty."],
        consent_quality="insufficient" if high else "not_required",
        caveats=[
            "Template text chosen from the action's declared flags; nothing about the user was measured.",
            "It must not bypass policy, audit, authority, or safety checks.",
        ],
    )
