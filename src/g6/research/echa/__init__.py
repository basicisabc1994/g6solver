# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The ECHA evidence kernel (Checkpoint 1; founder decision A1, 2026-10-09). Research code, default OFF.

Typed evidence, claim and episode records; an append-only hash-chained ledger; the claim rule that computes a claim's
result from its evidence; an evaluator-only access boundary; and the trace of a claim back to its standard and evidence.
It imports nothing from the product and nothing in the product imports it. Set ``G6_ECHA_RESEARCH=1`` to use it.
"""
from .access import AccessDenied, Vault, blinding_status, guarded_read
from .claims import evaluate_claim
from .episodes import Episode, EpisodeRecord
from .flag import FLAG, ResearchDisabled, require_research, research_enabled
from .ledger import Ledger, LedgerCorrupt, LedgerError
from .records import ClaimRecord, EvidenceRecord, StandardRef, canonical, digest
from .trace import trace_claim

__all__ = [
    "FLAG",
    "AccessDenied",
    "ClaimRecord",
    "Episode",
    "EpisodeRecord",
    "EvidenceRecord",
    "Ledger",
    "LedgerCorrupt",
    "LedgerError",
    "ResearchDisabled",
    "StandardRef",
    "Vault",
    "blinding_status",
    "canonical",
    "digest",
    "evaluate_claim",
    "guarded_read",
    "require_research",
    "research_enabled",
    "trace_claim",
]
