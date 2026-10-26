# SPDX-License-Identifier: Apache-2.0
"""AHI — governed harness adaptation (ECHA spec v0.1 §4.4–4.6 and §6). Research code, default OFF.

The library's adaptation pillar without weight training: a real failure (``FailureCase``) motivates one
minimal change (``AdaptationProposal``); the change is checked locally against independently authored
negative and positive controls (``validate_locally``); and a ``decide_promotion`` gate admits it to
**shadow** use at most. Nothing here promotes to production, and any unknown required input blocks.
A ``Funnel`` records present → reachable → activated → effective, where *effective* needs a
counterfactual evidence reference, not an invocation log.

Set ``G6_ECHA_RESEARCH=1`` to use it.
"""
from .contracts import (
    PROPOSAL_STATUSES,
    ROOT_CAUSES,
    AdaptationProposal,
    Control,
    FailureCase,
    Funnel,
    LearnedComponent,
)
from .lifecycle import LocalValidation, PromotionDecision, decide_promotion, validate_locally

__all__ = [
    "PROPOSAL_STATUSES",
    "ROOT_CAUSES",
    "AdaptationProposal",
    "Control",
    "FailureCase",
    "Funnel",
    "LearnedComponent",
    "LocalValidation",
    "PromotionDecision",
    "decide_promotion",
    "validate_locally",
]
