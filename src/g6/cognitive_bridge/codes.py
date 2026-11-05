# SPDX-License-Identifier: Apache-2.0
"""The stable codes of g6.cognitive_bridge: each refusal and each card carries one, the same on every surface.

A refusal is ``blocked-escalated`` and says which of these it is, in ``code`` and in its ``warning_card``; the block's
refusals reach ``g6_invoke`` with their card, so the door shows the same code. ``tests/cognitive_bridge/
test_block_refusal_codes.py`` pins each one, and the skill names each one.
"""
from __future__ import annotations

from typing import Any

from g6.core.reliability_envelope import warning_card

#: The block refused its input: a field it does not take is ``G6_E_INPUT_VALIDATION`` at the door; this is the rest
#: (priors that do not sum to one, a probe that does not predict every hypothesis, a plan with no claim).
INPUT = "G6_E_COGNITIVE_BRIDGE_INPUT"
#: An executor asked to run a plan without the caller's approval: nothing ran.
APPROVAL_REQUIRED = "G6_E_COGNITIVE_APPROVAL_REQUIRED"
#: A plan's steps differ from the canonical routing of its claims: nothing ran.
PLAN_TAMPERED = "G6_E_COGNITIVE_PLAN_TAMPERED"
#: The orchestrator was asked to run a plan that is not approved, not fully specified, or has no registry: nothing ran.
EXECUTION_NOT_APPROVED = "G6_E_COGNITIVE_EXECUTION_NOT_APPROVED"
#: An assessment blocked: a method step, a critical check, a documentary section, a review or a conflict failed.
ASSURANCE_BLOCKED = "G6_E_COGNITIVE_ASSURANCE_BLOCKED"
#: An assessment passed the checks it ran in this process; no G6 door re-ran them and no outside custody held them.
NOT_PROMOTED = "G6_E_COGNITIVE_NOT_PROMOTED"

CODES: tuple[str, ...] = (INPUT, APPROVAL_REQUIRED, PLAN_TAMPERED, EXECUTION_NOT_APPROVED, ASSURANCE_BLOCKED,
                          NOT_PROMOTED)


def refusal(code: str, reason: str, **extra: Any) -> dict[str, Any]:
    """A ``blocked-escalated`` answer that says why, under ``code`` and in a warning card with the same code."""
    reason = reason[:400]
    return {"completion_state": "blocked-escalated", "code": code, "reason": reason,
            "warning_card": warning_card(code, reason), **extra}


__all__ = ["APPROVAL_REQUIRED", "ASSURANCE_BLOCKED", "CODES", "EXECUTION_NOT_APPROVED", "INPUT", "NOT_PROMOTED",
           "PLAN_TAMPERED", "refusal"]
