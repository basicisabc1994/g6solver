# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Evidence bundle types used by harness verification."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, get_args

ClaimType = Literal[
    "SEMANTIC",
    "LOGICAL",
    "MATHEMATICAL",
    "EMPIRICAL",
    "CAUSAL",
    "PREDICTIVE",
    "RECOMMENDATION",
    "NORMATIVE_OR_OPINION",
    "HYPOTHESIS",
]
RiskLevel = Literal["R0", "R1", "R2", "R3"]
EvidenceGrade = Literal["E0", "E1", "E2", "E3"]
EpistemicStatus = Literal[
    "ASSERTED",
    "CLARIFICATION_REQUIRED",
    "SUPPORTED",
    "CONTESTED",
    "REFUTED",
    "UNVERIFIABLE",
    "OPINION",
    "EXPIRED",
]
Disposition = Literal["ALLOW", "ALLOW_WITH_LABEL", "ASK", "ESCALATE", "BLOCK"]
EvidenceStance = Literal["supports", "opposes", "mixed", "context_only"]
SourceType = Literal[
    "primary",
    "canonical",
    "secondary",
    "dataset",
    "experiment",
    "direct_observation",
    "expert_opinion",
    "anecdote",
    "model_assertion",
]


def _datetime_or_none(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


@dataclass(frozen=True)
class Claim:
    text: str
    claim_type: str = "inference"
    evidence_refs: tuple[str, ...] = ()
    checked: bool = False
    risk_level: str = "R0"
    evidence_grade: str = "E0"
    epistemic_status: str = "ASSERTED"
    scope: dict = field(default_factory=dict)
    counterevidence_refs: tuple[str, ...] = ()
    falsification_conditions: tuple[str, ...] = ()
    independence_group: str = ""
    confidence: float | None = None
    confidence_ceiling_applied: bool = False
    disposition: str | None = None
    as_of: datetime | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "claim_type": self.claim_type,
            "evidence_refs": list(self.evidence_refs),
            "checked": self.checked,
            "risk_level": self.risk_level,
            "evidence_grade": self.evidence_grade,
            "epistemic_status": self.epistemic_status,
            "scope": self.scope,
            "counterevidence_refs": list(self.counterevidence_refs),
            "falsification_conditions": list(self.falsification_conditions),
            "independence_group": self.independence_group,
            "confidence": self.confidence,
            "confidence_ceiling_applied": self.confidence_ceiling_applied,
            "disposition": self.disposition,
            "as_of": _datetime_or_none(self.as_of),
            "expires_at": _datetime_or_none(self.expires_at),
        }


@dataclass(frozen=True)
class TestResult:
    name: str
    passed: bool
    command: str = ""
    output_ref: str = ""


@dataclass(frozen=True)
class SourceRef:
    uri: str
    title: str = ""
    source_type: str = "file"
    excerpt: str = ""


@dataclass(frozen=True)
class Assumption:
    text: str
    risk: str = "medium"
    validation: str = ""


@dataclass(frozen=True)
class EvidenceRecord:
    source_uri_or_artifact_id: str
    source_type: str = "secondary"
    supports_or_opposes: str = "supports"
    independence_group: str = ""
    domain_relevance: str = "medium"
    authority: str = "unknown"
    methodology_quality: str = "unknown"
    recency_status: str = "unknown"
    title: str = ""
    author_or_owner: str = ""
    publication_or_effective_date: datetime | None = None
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    quoted_or_mapped_span: str = ""
    conflicts_of_interest: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    raw_data_available: bool | None = None
    reproducible: bool | None = None
    integrity_hash: str = ""
    claim_id: str = ""
    evidence_id: str = ""
    verification_method: str = ""  # W4: declared method → certificate grade; "" = axis not asserted

    def __post_init__(self) -> None:
        # Review B1-1: a stance outside the vocabulary ("refutes", "Opposes") counted as neither support nor
        # opposition, so a refutation was silently missed. No stance at all is the default, "supports".
        if self.supports_or_opposes not in get_args(EvidenceStance):
            raise ValueError(f"supports_or_opposes must be one of {get_args(EvidenceStance)}, "
                             f"not {self.supports_or_opposes!r}")

    def to_dict(self) -> dict[str, Any]:
        publication_date = _datetime_or_none(self.publication_or_effective_date)
        return {
            "source_uri_or_artifact_id": self.source_uri_or_artifact_id,
            "source_type": self.source_type,
            "supports_or_opposes": self.supports_or_opposes,
            "independence_group": self.independence_group,
            "domain_relevance": self.domain_relevance,
            "authority": self.authority,
            "methodology_quality": self.methodology_quality,
            "recency_status": self.recency_status,
            "title": self.title,
            "author_or_owner": self.author_or_owner,
            "publication_or_effective_date": publication_date,
            "retrieved_at": self.retrieved_at.isoformat(),
            "quoted_or_mapped_span": self.quoted_or_mapped_span,
            "conflicts_of_interest": list(self.conflicts_of_interest),
            "limitations": list(self.limitations),
            "raw_data_available": self.raw_data_available,
            "reproducible": self.reproducible,
            "integrity_hash": self.integrity_hash,
            "claim_id": self.claim_id,
            "evidence_id": self.evidence_id,
            "verification_method": self.verification_method,
        }


@dataclass(frozen=True)
class EvidenceBundle:
    """Portable evidence returned by per-harness verification."""

    claims: tuple[Claim, ...] = ()
    tests_run: tuple[TestResult, ...] = ()
    sources: tuple[SourceRef, ...] = ()
    assumptions: tuple[Assumption, ...] = ()
    unresolved_questions: tuple[str, ...] = ()
    confidence: float | None = None
    verifier_id: str = "unknown"
    verified_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    schema_version: str = "1.0"
    evidence_records: tuple[EvidenceRecord, ...] = ()

    def __post_init__(self) -> None:
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "claims": [claim.to_dict() for claim in self.claims],
            "tests_run": [test.__dict__ for test in self.tests_run],
            "sources": [source.__dict__ for source in self.sources],
            "assumptions": [assumption.__dict__ for assumption in self.assumptions],
            "unresolved_questions": list(self.unresolved_questions),
            "confidence": self.confidence,
            "verifier_id": self.verifier_id,
            "verified_at": self.verified_at.isoformat(),
            "evidence_records": [record.to_dict() for record in self.evidence_records],
        }


TestResult.__test__ = False


__all__ = [
    "Assumption",
    "Claim",
    "ClaimType",
    "Disposition",
    "EpistemicStatus",
    "EvidenceBundle",
    "EvidenceGrade",
    "EvidenceRecord",
    "EvidenceStance",
    "RiskLevel",
    "SourceRef",
    "SourceType",
    "TestResult",
]