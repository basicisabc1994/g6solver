# SPDX-License-Identifier: Apache-2.0
"""Coordinate declared team capabilities and claims; operator ranks never authorise real-world actions."""
from g6.integrations.hat_orchestrator.block import SENSITIVE_OPS, HATOrchestratorBlock, HATOrchestratorInput

__g6_component__ = True
__g6_block__ = "HATOrchestratorBlock"
__g6_requires__ = ("action_gating",)
__g6_mitigates__ = ("unclear_authority",)
__g6_introduces__ = ("authority_conflict", "prompt_injection")
__g6_sensitive_ops__ = SENSITIVE_OPS
__g6_outside__ = ()
__g6_extras__ = {}

__all__ = ["HATOrchestratorBlock", "HATOrchestratorInput"]

#: How each refusal this component writes is repaired (``g6.core.refusals``): code -> who fixes it, and
#: G6's words where they are not the class's own. A census test holds every code it writes to one entry.
__g6_refusals__ = {
    "G6_E_GATE_ERROR": "person",
    "G6_E_HAT_AGENT_MISSING": "none",
    "G6_E_HAT_AUTHORITY_DECLARED": "person",
    "G6_E_HAT_AUTHORITY_ESCALATED": ("person", "wait"),
    "G6_E_HAT_DRY_RUN": "none",
    "G6_E_HAT_DUPLICATE": "none",
    "G6_E_HAT_GATE_HELD": "person",
    "G6_E_HAT_INFO": "none",
    "G6_E_HAT_NO_MATCH": "none",
    "G6_E_HAT_PARTICIPANT_REFUSED": "none",
    "G6_E_HAT_RECORDED": "none",
    "G6_E_HAT_REPORT": "none",
    "G6_E_HAT_REPORT_REFUSED": "none",
    "G6_E_HAT_ROSTER_MISSING": "none",
    "G6_E_HAT_STATE_BUSY": "retry",
    "G6_E_HAT_STATE_FAILED": "none",
    "G6_E_HAT_SYNC_MISSING": "none",
    "G6_E_HAT_TASK_MISSING": "none",
}
