# SPDX-License-Identifier: Apache-2.0
"""Stable ``G6_E_GOAL_*`` codes, and the warning card every goal_engine result carries.

The card is ``g6.core.reliability_envelope.WarningCard`` (the archive's own ``envelope.py`` is not ported, audit 013
§7). No result here is ``verified``: the two states are ``qualified-draft`` and ``blocked-escalated``.
"""
from __future__ import annotations

from typing import Any, Literal

from g6.core.reliability_envelope import WarningCard

#: The only states a goal_engine result can hold. ``verified`` is not a member (audit 013, GE-003).
CompletionState = Literal["qualified-draft", "blocked-escalated"]

# refusals and per-node rejections
INPUT_INVALID = "G6_E_GOAL_INPUT_INVALID"
GOAL_EMPTY = "G6_E_GOAL_EMPTY"
PREDICATE_UNKNOWN = "G6_E_GOAL_PREDICATE_UNKNOWN"
CONTROL_INVALID = "G6_E_GOAL_CONTROL_INVALID"
PREDICATE_REGISTRATION = "G6_E_GOAL_PREDICATE_REGISTRATION"
NODE_UNKNOWN = "G6_E_GOAL_NODE_UNKNOWN"
# build_plan_tree
PLAN_REJECTED = "G6_E_GOAL_PLAN_REJECTED"
PLAN_TRUNCATED = "G6_E_GOAL_PLAN_TRUNCATED"
NOT_DECOMPOSED = "G6_E_GOAL_NOT_DECOMPOSED"
PLAN_DECLARED = "G6_E_GOAL_PLAN_DECLARED"
# evaluate_controls
PREDICATE_ERROR = "G6_E_GOAL_PREDICATE_ERROR"
GUARDRAIL_FAILED = "G6_E_GOAL_GUARDRAIL_FAILED"
GUARDRAIL_UNKNOWN = "G6_E_GOAL_GUARDRAIL_UNKNOWN"
CHECKPOINT_FAILED = "G6_E_GOAL_CHECKPOINT_FAILED"
CHECKPOINT_UNKNOWN = "G6_E_GOAL_CHECKPOINT_UNKNOWN"
APPROVAL_REQUIRED = "G6_E_GOAL_APPROVAL_REQUIRED"
OBSERVATION_UNCHECKED = "G6_E_GOAL_OBSERVATION_UNCHECKED"


def card(state: CompletionState, code: str, message: str, action: str) -> dict[str, Any]:
    """A warning card for ``state``, with its code, the reason in words and what to do next."""
    built = WarningCard(code=code, message=message, suggested_action=action,
                        severity="blocked" if state == "blocked-escalated" else "amber").model_dump()
    built["completion_state"] = state
    return built


def evidence(code: str, message: str, source: str, **extra: Any) -> dict[str, Any]:
    """One evidence entry: a code, what it means here, and which function recorded it."""
    return {"code": code, "message": message, "source": source, **extra}
