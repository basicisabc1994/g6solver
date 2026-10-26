# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Honesty detector for the epistemic_integrity component.

``adjudication_at_tests`` (FailureMode ``unsupported_claim_allowed``,
declared on ``EpistemicIntegrityBlock``) — a genuine self-consistency check
over the Output's own ``claims[]`` list (a plain dict payload from
``AdjudicationResult.to_dict()``, ``adjudicator.py:87-92``): each entry
carries ``evidence_grade`` and ``disposition`` (``mvp.core.evidence.Claim``,
``evidence.py:56-93``). Under the risk policy in ``core/risk_policy.py`` (code
only: no document is read) "ALLOW" is never returned for
``evidence_grade == "E0"`` at any risk tier, so this guards against a code
change that breaks it. A claim carrying both ``evidence_grade == "E0"`` (no
traceable evidence / model-assertion only) and ``disposition == "ALLOW"``
(the un-labelled, full-confidence disposition — NOT the honest
"ALLOW_WITH_LABEL") is exactly the floor's own invariant being violated,
whatever caused it.
"""
from __future__ import annotations

from typing import Any

from g6.core.block_contract import register_detector

_UNSUPPORTED_GRADE = "E0"
_UNLABELLED_ALLOW = "ALLOW"


def _field(output: Any, key: str, default: Any = None) -> Any:
    if isinstance(output, dict):
        return output.get(key, default)
    return getattr(output, key, default)


@register_detector(name="adjudication_at_tests")
def detect_unsupported_claim_allowed(output: Any) -> bool:
    claims = _field(output, "claims")
    if not isinstance(claims, list):
        return False
    for entry in claims:
        grade = _field(entry, "evidence_grade")
        disposition = _field(entry, "disposition")
        if grade == _UNSUPPORTED_GRADE and disposition == _UNLABELLED_ALLOW:
            return True
    return False


__all__ = ["detect_unsupported_claim_allowed"]
