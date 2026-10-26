# SPDX-License-Identifier: Apache-2.0
"""ECHA §6 Phase C (validate locally) and Phase E (gate). The best outcome is ``shadow_approved``.

The gate is product code now, ``g6.core.adaptation_gate`` (graduated, audit 037: automated reviewers refused, the
validation bound to the proposal's content, a wider protected list). This module adds the research flag.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from g6.core import adaptation_gate as _gate
from g6.core.adaptation_gate import PROTECTED_FRAGMENTS, VERDICTS, LocalValidation, PromotionDecision
from g6.research.ahi.contracts import AdaptationProposal, Control
from g6.research.echa.flag import require_research

__all__ = ["PROTECTED_FRAGMENTS", "VERDICTS", "LocalValidation", "PromotionDecision", "decide_promotion",
           "validate_locally"]


def validate_locally(proposal: AdaptationProposal, guard: Callable[[Any], bool],
                     controls: list[Control]) -> LocalValidation:
    """Run the candidate guard on every control (``g6.core.adaptation_gate.validate_locally``)."""
    require_research()
    return _gate.validate_locally(proposal, guard, controls)


def decide_promotion(proposal: AdaptationProposal, validation: LocalValidation | None, *, policy_version: str,
                     reviewer: str, owner_confirmed: bool, regression: str, budget: str,
                     security: str) -> PromotionDecision:
    """Phase E gate (``g6.core.adaptation_gate.decide_promotion``). Unknown blocks; a failure rejects; the most it
    grants is isolated shadow use."""
    require_research()
    return _gate.decide_promotion(proposal, validation, policy_version=policy_version, reviewer=reviewer,
                                  owner_confirmed=owner_confirmed, regression=regression, budget=budget,
                                  security=security)
