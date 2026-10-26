# SPDX-License-Identifier: Apache-2.0
"""g6.evidence — the certificate issuer: what a mission's evidence supports, and nothing more.

Capability label: ``implemented``.

``issue(facts, artifact, checks, receipts)`` gives every predicate a Belnap value from receipts
taken on the exact artifact bytes, applies the A4 grounding rule (independent or formal check,
author is not the producer, frozen before the artifact, a G6-generated negative control killed),
and returns ``verified`` / ``qualified-draft`` / ``blocked-escalated`` with every gap named.
``verify`` decides a certificate again offline, from the checks, receipts, gates and obligations
it records, and names every field that does not follow. A support rests only on controls and
receipts G6 wrote to its ledger (``record`` into a ``g6.core.ledger.ChainLedger`` passed as
``ledger``), resolved by id and hash; hand-written records stay unresolved and the state stays
at most ``qualified-draft``. The issuer trusts the evidence *kinds* it is told; callers must not
label author-controlled tests as independent (see docs/transition/DECISIONS.md #22).
"""
from g6.evidence.certificate import (
    EVIDENCE_KINDS,
    RESULTS,
    STATES,
    Artifact,
    Certificate,
    CertificateRefused,
    Check,
    Control,
    MissionFacts,
    PredicateStatement,
    Receipt,
    catalogue_sha,
    digest_of,
    grounding,
    issue,
    record,
    verify,
)

__g6_component__ = True
__g6_maturity__ = "beta"  # no block: Python API only
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("overstated_verification",)
__g6_introduces__: tuple[str, ...] = ()

__all__ = [
    "EVIDENCE_KINDS", "RESULTS", "STATES", "Artifact", "Certificate", "CertificateRefused", "Check", "Control",
    "MissionFacts", "PredicateStatement", "Receipt", "catalogue_sha", "digest_of", "grounding", "issue", "record",
    "verify",
]
