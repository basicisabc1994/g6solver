# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The research rig's view of the certificate issuer (RVC R1; core moved to the product in R6.1).

The issuer itself lives in ``g6.evidence.certificate`` (one source of truth for the rig and the product). This
module adapts the rig's types to it: it judges the ``MissionIR`` with the CSIR judgement (obligations A1-A6), binds it to the
``FrozenStandard``, and hands the issuer the resulting ``MissionFacts``. Everything the issuer decides, and why, is in the
product module's docstring.
"""
from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any

from g6.evidence.certificate import (  # noqa: F401 - re-exported for the rig and its tests
    A4_KINDS,
    EVIDENCE_KINDS,
    G6,
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
)
from g6.core.ledger import ChainLedger
from g6.evidence.certificate import issue as _issue
from g6.evidence.certificate import verify as _verify

from ..checks import OpFact
from ..flag import require_research
from ..ir import MissionIR
from .judgement import judge
from .solvability import Solvability
from .standard import FrozenStandard


def facts_of(ir: MissionIR, catalogue: Mapping[str, OpFact], standard: FrozenStandard) -> MissionFacts:
    """The mission as the issuer needs it: judged against ``catalogue`` and bound to ``standard``."""
    judgement = judge(ir, catalogue)
    return MissionFacts(
        mission_sha=ir.sha256, mission_standard_sha=ir.standard.sha256, standard_sha=standard.sha256,
        predicates=tuple(ir.standard.predicates), standard_predicates=tuple(standard.predicates),
        gates=tuple(n.id for n in ir.nodes if n.gate),
        violations=tuple((o, f.code, f.detail) for o, found in judgement.violations.items() for f in found),
        cost_cents=judgement.resources.cents, catalogue_sha=catalogue_sha(catalogue))


def issue(ir: MissionIR, catalogue: Mapping[str, OpFact], standard: FrozenStandard, artifact: Artifact,
          checks: Sequence[Check], receipts: Sequence[Receipt], *, decisions: Collection[str] = (),
          solvability: Solvability | None = None, ledger: ChainLedger | None = None) -> Certificate:
    """The certificate for ``artifact`` under ``ir`` and its frozen ``standard``, judged against ``catalogue``;
    ``ledger`` holds the controls and receipts G6 observed (the product issuer resolves support there)."""
    require_research()
    return _issue(facts_of(ir, catalogue, standard), artifact, checks, receipts, decisions=decisions,
                  solvability=solvability.as_dict() if solvability else None, ledger=ledger)


def verify(body: Mapping[str, Any], artifact_bytes: bytes, *, ir: MissionIR | None = None,
           standard: FrozenStandard | None = None, ledger: ChainLedger | None = None) -> list[str]:
    """Every inconsistency in ``body`` for ``artifact_bytes``; given ``ir`` and ``standard``, also against them."""
    return _verify(body, artifact_bytes, ledger=ledger,
                   mission_sha=ir.sha256 if ir is not None else None,
                   predicates=tuple(ir.standard.predicates) if ir is not None else None,
                   gates=tuple(n.id for n in ir.nodes if n.gate) if ir is not None else None,
                   standard_sha=standard.sha256 if standard is not None else None,
                   standard_predicates=tuple(standard.predicates) if standard is not None else None)
