# SPDX-License-Identifier: Apache-2.0

"""Deterministic epistemic-integrity adjudication (G6-EI-001, spec section 12).

This is a DETERMINISTIC floor — no LLM. It cannot be talked out of a gate by tone,
fluency, confidence, or user pressure (the central epistemic failure mode: EI-008,
EI-019, EI-094). The conformant claim lifecycle is:

    atomise -> classify -> scope / operational-definition check -> assign risk ->
    collapse correlated evidence (independence) -> grade evidence (E0-E3) ->
    detect counterevidence / CONTESTED -> cap confidence by evidence ->
    numeric & causal integrity checks -> risk x evidence gate (R0-R3 x E0-E3)

It builds on the shared types in ``mvp.core.evidence`` (Claim, EvidenceRecord) and the
fail-closed gate in ``mvp.core.risk_policy``. An optional LLM refinement MAY be layered
on later; the floor stands alone and is fully tested.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from g6.epistemic_integrity.markers import markers
from g6.core import risk_policy
from g6.core.evidence import Claim, EvidenceRecord
from g6.core.evidence_grade import grade_from_verification_method  # W4

# Vague evaluatives that need an operational definition before a decision (EI-003).
_VAGUE_EVALUATIVES = (
    "better", "best", "safe", "effective", "significant", "leading", "large",
    "successful", "optimal", "superior", "huge", "massive", "world-class",
)
# Causal language markers (EI-035 / AT-04): correlation MUST NOT be rendered causal
# without causal-identification evidence.
_CAUSAL_MARKERS = (
    "because", "causes", "caused", "cause of", "leads to", "led to", "due to",
    "results in", "resulted in", "driven by", "makes ", "make it", "responsible for",
)
_RECOMMEND_MARKERS = ("should", "recommend", "ought to", "must use", "best to", "advise")
_OPINION_MARKERS = ("i think", "i believe", "in my opinion", "imo", "feels like", "i prefer")
_PREDICT_MARKERS = ("will ", "predict", "forecast", "expected to", "by 20", "going to")

_NUMBER_RE = re.compile(r"\d")
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?\s*%|\bpercent\b|\bpercentage\b)")
# Cues that a numeric claim carries its denominator / baseline / sample (EI-030).
_DENOMINATOR_CUES = (
    " of ", " per ", "out of", "n=", "n =", "sample", "baseline", "compared",
    "relative to", "versus", " vs ", "rate of", "/", "from ", " to ",
)
_SCOPE_CUES = (
    "in 20", "during", "among", "for ", " us ", " uk ", " eu ", "population",
    "patients", "users", "between 20", "as of", "since 20", "study of",
)

# Confidence ceiling per evidence grade (EI-019): confidence cannot exceed evidence.
_GRADE_CEILING = {"E0": 0.2, "E1": 0.5, "E2": 0.8, "E3": 1.0}
_CONTESTED_CEILING = 0.5

# Most-restrictive ordering for combining dispositions across atomic claims.
_DISPOSITION_RANK = {
    "ALLOW": 0, "ALLOW_WITH_LABEL": 1, "ASK": 2, "ESCALATE": 3, "BLOCK": 4,
}
_PRIMARY_SOURCE_TYPES = frozenset(
    {"primary", "canonical", "experiment", "direct_observation", "dataset"}
)
# Stances that support a claim (``mixed`` supports it in part). Only these count toward its grade.
_SUPPORTING_STANCES = frozenset({"supports", "mixed"})


def _credible_support(record: EvidenceRecord) -> bool:
    """A non-model record whose stance supports the claim."""
    return record.source_type != "model_assertion" and record.supports_or_opposes in _SUPPORTING_STANCES


@dataclass(frozen=True)
class AdjudicatedClaim:
    """A single atomic claim after adjudication."""

    claim: Claim
    triggered: tuple[str, ...] = ()  # AT-/EI- markers this claim exercised

    marker_spans: dict[str, list[dict]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = self.claim.to_dict()
        d["triggered"] = list(self.triggered)
        d["markers"] = self.marker_spans
        return d


@dataclass(frozen=True)
class AdjudicationResult:
    claims: tuple[AdjudicatedClaim, ...] = ()
    overall_disposition: str = "ALLOW"
    overall_status: str = "ASSERTED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_disposition": self.overall_disposition,
            "overall_status": self.overall_status,
            "claims": [c.to_dict() for c in self.claims],
        }


# --------------------------------------------------------------------------- #
# Stage functions (each deterministic + independently testable)
# --------------------------------------------------------------------------- #

def atomise(text: str) -> list[str]:
    """Split a message into atomic claims (EI-001). Sentence + conjunction split."""
    if not text or not text.strip():
        return []
    parts = re.split(r"(?<=[.!?])\s+|;\s*|\s+\band\b\s+|\s+\bbut\b\s+", text.strip())
    return [p.strip().rstrip(".") for p in parts if p.strip()]


def classify(text: str) -> str:
    """Heuristic claim-type classification (EI taxonomy, spec section 6)."""
    low = f" {text.lower()} "
    if any(m in low for m in _CAUSAL_MARKERS):
        return "CAUSAL"
    if any(m in low for m in _OPINION_MARKERS):
        return "NORMATIVE_OR_OPINION"
    if any(m in low for m in _RECOMMEND_MARKERS):
        return "RECOMMENDATION"
    if any(m in low for m in _PREDICT_MARKERS):
        return "PREDICTIVE"
    return "EMPIRICAL"


def assign_risk(action_criticality: str) -> str:
    """Map declared action criticality to R0-R3 (spec section 9.1).

    Risk = worst credible harm. Informational claims default low (R1); the caller
    (e.g. action_gating) supplies higher tiers when the claim is an action
    precondition. An unknown non-empty criticality is treated conservatively (R2).
    """
    key = (action_criticality or "").strip().lower()
    if key in ("", "none", "creative", "opinion"):
        return "R0" if key in ("creative", "opinion") else "R1"
    if key in ("info", "informational", "low"):
        return "R1"
    if key in ("reversible", "decision", "medium", "operational"):
        return "R2"
    if key in ("irreversible", "external", "high", "safety", "health", "legal",
               "security", "financial", "critical"):
        return "R3"
    return "R2"  # conservative default for an unrecognised criticality


def collapse_independence(records: tuple[EvidenceRecord, ...]) -> dict[str, list[EvidenceRecord]]:
    """Collapse correlated/derivative sources into one lineage (EI-013, AT-03, AT-10).

    Records sharing an ``independence_group`` are ONE evidential lineage. Records
    that declare NO group cannot be proven independent, so they conservatively
    collapse into a single ``_ungrouped`` lineage rather than counting as separate
    corroboration ("source count is not source independence").
    """
    groups: dict[str, list[EvidenceRecord]] = {}
    for r in records:
        key = r.independence_group.strip() or "_ungrouped"
        groups.setdefault(key, []).append(r)
    return groups


def grade_evidence(
    records: tuple[EvidenceRecord, ...],
    groups: dict[str, list[EvidenceRecord]],
) -> str:
    """Grade evidence E0-E3 from the deduplicated lineages that SUPPORT the claim (spec section 9.2).

    Only non-model records whose stance is ``supports`` or ``mixed`` count: evidence that
    opposes the claim, or is context only, never raises its grade (``detect_refuted``
    reads opposition with no support). E0 no supporting traceable evidence / only model
    assertion; E1 one uncorroborated lineage; E2 a primary/canonical lineage OR >=2
    independent lineages; E3 >=2 independent lineages that are primary or authoritative.
    """
    supporting = [r for r in records if _credible_support(r)]
    if not supporting:
        return "E0"
    indep_groups = {g for g, rs in groups.items() if any(_credible_support(r) for r in rs)}
    n_indep = len(indep_groups)
    has_primary = any(r.source_type in _PRIMARY_SOURCE_TYPES for r in supporting)
    authoritative_lineages = sum(
        1 for g in indep_groups if any(r.authority == "high" and _credible_support(r) for r in groups[g])
    )
    if n_indep >= 2 and (has_primary or authoritative_lineages >= 2):
        return "E3"
    if has_primary or n_indep >= 2:
        return "E2"
    return "E1"


def certificate_grade(records: tuple[EvidenceRecord, ...]) -> str | None:
    """Best declared certificate grade across supporting non-model records; None if none.

    W4 CERTIFICATE axis — orthogonal to the INDEPENDENCE E-level from ``grade_evidence``.
    Independence + a resolvable artifact are the ceiling inputs (the D1 content-cost
    rule); a record with no ``verification_method`` contributes nothing (the certificate
    axis is not asserted for it). Returns a canonical ``EvidenceGrade`` string
    (kernel/instrument/model) — ``risk_policy.gate_decision`` converts it to an E-level
    and floors down-only — or ``None`` when NO record declares a method (so the gate is
    byte-identical to baseline: the landmine guard against absent→E0 collapse).
    """
    graded = [
        grade_from_verification_method(
            r.verification_method, independent=True, has_resolvable_artifact=bool(r.integrity_hash)
        )
        for r in records
        if r.verification_method
        and r.source_type != "model_assertion"
        and r.supports_or_opposes in ("supports", "mixed")
    ]
    if not graded:
        return None
    # best (highest) certificate available — credit for the strongest declared check
    order = {"model": 0, "instrument": 1, "kernel": 2}
    return max(graded, key=lambda g: order[g])


def detect_contested(
    records: tuple[EvidenceRecord, ...],
    groups: dict[str, list[EvidenceRecord]],
) -> bool:
    """Credible independent support AND opposition -> CONTESTED (EI-018, AT-06)."""
    supports = {
        g for g, rs in groups.items()
        if any(r.supports_or_opposes in ("supports", "mixed") and r.source_type != "model_assertion" for r in rs)
    }
    opposes = {
        g for g, rs in groups.items()
        if any(r.supports_or_opposes in ("opposes", "mixed") and r.source_type != "model_assertion" for r in rs)
    }
    return bool(supports) and bool(opposes)


def detect_refuted(records: tuple[EvidenceRecord, ...]) -> bool:
    """Credible opposition and no credible support -> REFUTED (EI-090): opposition never reads as support."""
    opposed = any(r.source_type != "model_assertion" and r.supports_or_opposes == "opposes" for r in records)
    return opposed and not any(_credible_support(r) for r in records)


def confidence_ceiling(grade: str, contested: bool) -> float:
    """Cap confidence by evidence grade and unresolved conflict (EI-019, AT-12)."""
    ceiling = _GRADE_CEILING.get(grade, 0.2)
    if contested:
        ceiling = min(ceiling, _CONTESTED_CEILING)
    return ceiling


def _missing_numeric_context(text: str) -> bool:
    """A material numeric/percentage claim lacking denominator/baseline (EI-030, AT-07)."""
    low = text.lower()
    if not _PERCENT_RE.search(low):
        return False
    return not any(cue in low for cue in _DENOMINATOR_CUES)


def _missing_scope(text: str) -> bool:
    low = text.lower()
    return not any(cue in low for cue in _SCOPE_CUES)


def _has_vague_evaluative(text: str) -> bool:
    low = f" {text.lower()} "
    return any(f" {w} " in low for w in _VAGUE_EVALUATIVES)


def most_restrictive(dispositions: list[str]) -> str:
    if not dispositions:
        return "ALLOW"
    return max(dispositions, key=lambda d: _DISPOSITION_RANK.get(d, 0))


def adjudicate_claim(
    text: str,
    *,
    action_criticality: str = "",
    evidence_records: tuple[EvidenceRecord, ...] = (),
    has_human_approval: bool = False,
    has_rollback: bool = False,
) -> AdjudicatedClaim:
    """Adjudicate a SINGLE atomic claim deterministically (spec section 12)."""
    triggered: list[str] = []
    claim_type = classify(text)
    risk = assign_risk(action_criticality)

    groups = collapse_independence(evidence_records)
    if any(len(rs) > 1 for rs in groups.values()):
        triggered.append("EI-013/AT-03/AT-10:independence_collapsed")

    grade = grade_evidence(evidence_records, groups)
    if all(r.source_type == "model_assertion" for r in evidence_records) and evidence_records:
        triggered.append("INV-03:model_output_is_E0")
    contested = detect_contested(evidence_records, groups)
    refuted = detect_refuted(evidence_records)

    status = "ASSERTED"
    if contested:
        status = "CONTESTED"
        triggered.append("EI-018/AT-06:contested")

    ceiling = confidence_ceiling(grade, contested)

    # Risk x evidence gate (fail-closed; R3<E3 -> BLOCK; R3 needs approval+rollback).
    # W4: derive the certificate grade from the records' declared verification methods and
    # floor the disposition at min(independence, certificate), down-only. certificate_grade
    # is None until a producer sets EvidenceRecord.verification_method, so this is INERT
    # today (baseline adjudication byte-identical) and tightens only as real certificate
    # data arrives — gated behind G6_RISK_POLICY_GRADE_FLOOR inside gate_decision.
    _cert_grade = certificate_grade(evidence_records)
    disposition = risk_policy.gate_decision(
        risk, grade,
        unresolved_conflict=contested,
        has_human_approval=has_human_approval,
        has_rollback=has_rollback,
        certificate_grade=_cert_grade,
    )

    # Scope / operational-definition (EI-002/EI-003): a decision-grade factual claim
    # using a vague evaluative without a metric needs clarification.
    if risk in ("R2", "R3") and _has_vague_evaluative(text) and _missing_scope(text):
        status = "CLARIFICATION_REQUIRED"
        disposition = most_restrictive([disposition, "ASK"])
        triggered.append("EI-003:operational_definition_required")

    # Numeric integrity (EI-030 / AT-07).
    if _missing_numeric_context(text):
        disposition = most_restrictive([disposition, "ASK"])
        triggered.append("EI-030/AT-07:numeric_context_required")

    # Causal integrity (EI-035 / AT-04): a causal claim without causal-grade evidence
    # (E2+) cannot be rendered as established cause.
    if claim_type == "CAUSAL" and not risk_policy.grade_meets(grade, "E2"):
        if disposition == "ALLOW":
            disposition = "ALLOW_WITH_LABEL"
        triggered.append("EI-035/AT-04:causal_language_unsupported")

    # Insufficient evidence is a VALID abstention, never a forced answer (EI-005/AT-13).
    if grade == "E0" and not contested and not refuted and risk in ("R0", "R1"):
        status = "UNVERIFIABLE" if status == "ASSERTED" else status
        if disposition == "ALLOW":
            disposition = "ALLOW_WITH_LABEL"
        triggered.append("EI-005/AT-13:abstention_allowed")

    # Credible opposition with no support (EI-090): the only credible evidence says the claim
    # is false, so it is REFUTED and blocked at every tier, never stated or acted on as given.
    if refuted:
        status = "REFUTED"
        disposition = "BLOCK"
        triggered.append("EI-090:refuted_by_opposing_evidence")

    try:
        found_markers = markers(text)
        if grade in ("E0", "E1") and any(not hit["negated"] for hit in found_markers["confidence"]):
            triggered.append("G6_E_CERTAINTY_WEAK_EVIDENCE")
            disposition = most_restrictive([disposition, "ASK"])
    except Exception:
        found_markers = {}
        triggered.append("G6_E_MARKER_CHECK_FAILED")
        disposition = "BLOCK"

    claim = Claim(
        text=text,
        claim_type=claim_type,
        risk_level=risk,
        evidence_grade=grade,
        epistemic_status=status,
        confidence=ceiling,
        confidence_ceiling_applied=True,
        independence_group="|".join(sorted(g for g in groups if g != "_ungrouped")),
        disposition=disposition,
    )
    return AdjudicatedClaim(claim=claim, triggered=tuple(triggered), marker_spans=found_markers)


def adjudicate(
    message: str,
    *,
    action_criticality: str = "",
    evidence_records: tuple[EvidenceRecord, ...] = (),
    has_human_approval: bool = False,
    has_rollback: bool = False,
) -> AdjudicationResult:
    """Adjudicate a whole message: atomise then adjudicate each claim (section 12).

    The message-level evidence is bound to every atomic claim in this floor (a future
    LLM refinement may bind evidence per-claim). The overall disposition is the MOST
    RESTRICTIVE across claims (a single BLOCK blocks the message).
    """
    texts = atomise(message) or ([message.strip()] if message.strip() else [])
    adjudicated = tuple(
        adjudicate_claim(
            t,
            action_criticality=action_criticality,
            evidence_records=evidence_records,
            has_human_approval=has_human_approval,
            has_rollback=has_rollback,
        )
        for t in texts
    )
    overall_disp = most_restrictive([a.claim.disposition or "ALLOW" for a in adjudicated])
    # Overall status: CONTESTED dominates, else CLARIFICATION_REQUIRED, else the
    # first non-ASSERTED, else ASSERTED.
    statuses = [a.claim.epistemic_status for a in adjudicated]
    overall_status = "ASSERTED"
    for priority in ("CONTESTED", "CLARIFICATION_REQUIRED", "REFUTED", "UNVERIFIABLE"):
        if priority in statuses:
            overall_status = priority
            break
    return AdjudicationResult(
        claims=adjudicated,
        overall_disposition=overall_disp,
        overall_status=overall_status,
    )


__all__ = [
    "AdjudicatedClaim",
    "AdjudicationResult",
    "adjudicate",
    "adjudicate_claim",
    "assign_risk",
    "atomise",
    "classify",
    "collapse_independence",
    "confidence_ceiling",
    "detect_contested",
    "detect_refuted",
    "grade_evidence",
    "most_restrictive",
]
