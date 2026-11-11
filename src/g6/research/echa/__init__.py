# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""ECHA: an audit and analysis view over the issuer's records (G6 0.3 design, section 6). Not a second authority.

``g6.evidence.certificate`` alone decides a requirement's status; ``view.certificate_view`` shows each status as the
issuer gave it, with what each record is and where it is. ECHA's typed evidence, claim and episode records, its
append-only hash-chained ledger, its claim rule, its evaluator-only access boundary and its trace remain as research
analysis: the claim rule's result is never a requirement's status, and ECHA's own records are never a source of
evidence for the issuer (``view.source_record``). Its custody binding graduated into ``g6.evidence.mapping``; its
competence measure is retired for selection; its episodes wait for slice B. There is no flag: nothing in the product
imports ECHA, and ECHA imports from the product only the graduated ledger, vault and binding.
"""
from .access import AccessDenied, Vault, blinding_status, guarded_read
from .claims import evaluate_claim
from .episodes import Episode, EpisodeRecord
from .ledger import Ledger, LedgerCorrupt, LedgerError
from .records import ClaimRecord, EvidenceRecord, StandardRef, canonical, digest
from .trace import trace_claim
from .view import certificate_view

__all__ = [
    "AccessDenied",
    "ClaimRecord",
    "Episode",
    "EpisodeRecord",
    "EvidenceRecord",
    "Ledger",
    "LedgerCorrupt",
    "LedgerError",
    "StandardRef",
    "Vault",
    "blinding_status",
    "canonical",
    "certificate_view",
    "digest",
    "evaluate_claim",
    "guarded_read",
    "trace_claim",
]
