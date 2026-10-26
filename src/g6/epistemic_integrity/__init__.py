# SPDX-License-Identifier: Apache-2.0

"""Epistemic integrity: deterministic claim adjudication and evidence gating.

Operationalises G6_Epistemic_Integrity_Spec.md (G6-EI-001) — atomic claim typing,
evidence grading (E0-E3) with independence collapse, confidence ceiling, CONTESTED
on credible conflict, and the risk x evidence (R0-R3 x E0-E3) fail-closed gate.

Built on the shared types in g6.core.evidence and g6.core.risk_policy.
"""
from g6.epistemic_integrity.markers import markers
from g6.epistemic_integrity.adjudicator import (
    AdjudicatedClaim,
    AdjudicationResult,
    adjudicate,
    adjudicate_claim,
)
from g6.epistemic_integrity.epistemic_integrity_block import (
    EpistemicIntegrityBlock,
    EpistemicIntegrityInput,
)

__g6_component__ = True
__g6_block__ = "EpistemicIntegrityBlock"
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("epistemic_indifference", "repetition_as_corroboration", "false_confidence")
__g6_introduces__: tuple[str, ...] = ()

__all__ = [
    "markers",
    "AdjudicatedClaim",
    "AdjudicationResult",
    "EpistemicIntegrityBlock",
    "EpistemicIntegrityInput",
    "adjudicate",
    "adjudicate_claim",
]
