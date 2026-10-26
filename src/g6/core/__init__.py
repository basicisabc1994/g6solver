# SPDX-License-Identifier: Apache-2.0
"""g6.core — the small shared kernel every component builds on.

Importing this package has no side effects: no network, no file writes, no import hooks.
Names are resolved lazily so `import g6.core` stays cheap.
"""
from __future__ import annotations

import importlib
from typing import Any

# Reliability labels (canonical vocabulary; never extend without a migration).
VERIFIED = "verified"
QUALIFIED_DRAFT = "qualified-draft"
BLOCKED_ESCALATED = "blocked-escalated"
COMPLETION_STATES = (VERIFIED, QUALIFIED_DRAFT, BLOCKED_ESCALATED)

_LAZY = {
    "Result": "g6.core.result",
    "Status": "g6.core.result",
    "AIBlock": "g6.core.ai_block",
    "BlockContract": "g6.core.block_contract",
    "FailureMode": "g6.core.block_contract",
    "block_contract": "g6.core.contract_decorator",
    "component_maturity": "g6.core.maturity",
    "get_maturity": "g6.core.maturity",
    "EvidenceBundle": "g6.core.evidence",
    "Claim": "g6.core.evidence",
    "ReliabilityEnvelope": "g6.core.reliability_envelope",
    "WarningCard": "g6.core.reliability_envelope",
    "EvidenceEntry": "g6.core.reliability_envelope",
    "get_registry": "g6.core.registry",
    "ModelProvider": "g6.core.executor_contracts",
    "CodingAgent": "g6.core.executor_contracts",
}

__all__ = ["VERIFIED", "QUALIFIED_DRAFT", "BLOCKED_ESCALATED", "COMPLETION_STATES", *_LAZY]


def __getattr__(name: str) -> Any:
    mod = _LAZY.get(name)
    if mod is None:
        raise AttributeError(f"module 'g6.core' has no attribute {name!r}")
    value = getattr(importlib.import_module(mod), name)
    globals()[name] = value
    return value
