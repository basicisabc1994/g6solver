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
