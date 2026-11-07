# SPDX-License-Identifier: Apache-2.0
"""Conservative, reproducible *candidate* assurance dossiers for G6 cognitive work.

This is NOT a trust root: it cannot give G6's `verified` completion label.
A real trusted evaluator must own the executable checkers, reference artifacts,
negative controls and domain standard profiles, outside the candidate agent's
writable scope. An unkeyed digest detects edits, not malicious reissuance.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import field, fields
from typing import Any, Mapping, Protocol

from .codes import DOSSIER_BLOCKED, MADE_FIELDS, STATES, affirmed, only_fields, refusal, settled, unclaimed
from .snapshot import publish, canonical_json, fresh, issue, plain, record, returned, snapshot, text

HEX64 = re.compile(r"[0-9a-f]{64}\Z")
FORMAT = "g6.cognitive_assurance.dossier/1"
DOCUMENT_FIELDS = (
    "objective_and_scope", "methodology", "standards_and_sources", "data_provenance",
    "assumptions", "uncertainty_and_limitations", "counterexamples_and_refutations",
    "reproduction_instructions", "exceptions_and_unresolved_items",
)
#: A dossier's own fields (its payload, beside the state, code, reason and card ``codes.settled`` writes): what
#: ``InProcessAssuranceEvaluator.evaluate`` writes, and all a dossier may carry. ``subject_sha256`` names the evidence
#: it assessed (:func:`evidence_sha256`).
PAYLOAD_FIELDS = (
    "schema", "kind", "scope_decision", "evaluator_id", "candidate_actor", "standard", "standard_digest",
    "subject_sha256", "checks", "documentary_sections", "missing_documentation", "professional_review", "blockers",
    "metrics", "promotion_authority", "limitations",
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_risk(value: Any) -> bool:
    """A risk is the number of one of G6's tiers, ``R0`` to ``R3``: an ``int`` (never a ``bool``) from 0 to 3. The
    standard and the problem context both take it by this one rule."""
    return type(value) is int and 0 <= value <= 3


@record
class Criterion:
    id: str
    description: str
    verifier_id: str
    normative_ref: str
    severity: str = "critical"        # critical | advisory
    failure_modes: tuple[str, ...] = ()
    expected_artifact_kind: str = "report"
    required_methods: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not all((self.id, self.description, self.verifier_id, self.normative_ref)):
            raise ValueError("criterion needs ID, description, verifier and normative reference")
        if self.severity not in ("critical", "advisory"):
            raise ValueError("criterion severity must be critical or advisory")
        if not self.expected_artifact_kind:
            raise ValueError("expected artifact kind missing")
        known_methods = {"graph", "causal", "formal", "predictive", "optimisation"}
        if set(self.required_methods) - known_methods:
            raise ValueError("criterion contains an unsupported mandatory reasoning method")


@record
class DomainStandard:
    id: str
    edition: str
    jurisdiction: str
    authority: str
    source_sha256: str
    criteria: tuple[Criterion, ...]
    risk: int = 1   # the risk tier's number, 0..3 for R0..R3. R2/R3 need authenticated professional review.

    def __post_init__(self) -> None:
        if not all((self.id, self.edition, self.jurisdiction, self.authority)):
            raise ValueError("standard requires identity, edition, jurisdiction and authority")
        if not HEX64.fullmatch(self.source_sha256):
            raise ValueError("standard source must have a 64-digit SHA-256 pin")
        if not self.criteria or not is_risk(self.risk):
            raise ValueError("standard requires criteria and a risk tier: risk 0 to 3 for R0 to R3")
        if len({x.id for x in self.criteria}) != len(self.criteria):
            raise ValueError("duplicate standard criterion IDs")

    @property
    def risk_tier(self) -> str:
        """G6's ``risk_tier`` for this standard: ``R0`` to ``R3``."""
        return publish(f"R{self.risk}", where="DomainStandard.risk_tier")

    def manifest(self) -> dict[str, Any]:
        """A new copy each call, never a view of what the standard holds (each criterion's fields, not its own
        ``__dict__``)."""
        return publish(unclaimed({"id": self.id, "edition": self.edition, "jurisdiction": self.jurisdiction,
                          "authority": self.authority, "source_sha256": self.source_sha256,
                          "criteria": [{f.name: getattr(c, f.name) for f in fields(c)} for c in self.criteria],
                          "risk": self.risk}, where="a standard's manifest"), where="DomainStandard.manifest")

    @property
    def digest(self) -> str:
        return publish(sha256(canonical_json(self.manifest())), where="DomainStandard.digest")


@record
class EvidenceArtifact:
    id: str
    content: bytes
    kind: str
    claimed_by: str
    source_ref: str
    claim_id: str

    def __post_init__(self) -> None:
        # The content is immutable bytes from the moment the artifact is made (the record's snapshot copied a bytes-like
        # buffer), so a later change to the caller's buffer cannot change what is checked after its manifest.
        if not isinstance(self.content, bytes):
            raise TypeError("evidence content must be bytes")
        if not all((self.id, self.content, self.kind, self.claimed_by, self.source_ref, self.claim_id)):
            raise ValueError("evidence needs bytes, claim, origin, kind and source reference")
        if len(self.content) > 2_000_000:
            raise ValueError("artifact exceeds 2MB conservative prototype cap")

    @property
    def digest(self) -> str:
        return publish(sha256(self.content), where="EvidenceArtifact.digest")

    def manifest(self) -> dict[str, Any]:
        return publish(unclaimed({"id": self.id, "kind": self.kind, "claimed_by": self.claimed_by,
                          "source_ref": self.source_ref, "claim_id": self.claim_id,
                          "sha256": self.digest, "length": len(self.content)}, where="an artifact's manifest"), where="EvidenceArtifact.manifest")


class Check(Protocol):
    def __call__(self, content: bytes) -> bool: ...


@record
class FrozenVerifier:
    """Evaluator-owned checker plus frozen controls, passed as trusted Python objects.

    A name or digest alone is never evidence of independence. Real G6 deployment
    must run this in an evaluator-owned process, not inside candidate execution.
    """
    id: str
    owner: str
    version_sha256: str
    check: Check = field(repr=False, compare=False)
    positive_controls: tuple[bytes, ...] = field(repr=False, compare=False)
    negative_controls: tuple[bytes, ...] = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.id or not self.owner or not HEX64.fullmatch(self.version_sha256):
            raise ValueError("checker needs owner, ID and pinned revision")
        if not self.positive_controls or not self.negative_controls:
            raise ValueError("independent checks need both positive and negative controls")
        if not callable(self.check):
            raise ValueError("checker must be executable")
        if any(not isinstance(x, bytes) or not x for x in (*self.positive_controls, *self.negative_controls)):
            raise ValueError("controls must be non-empty bytes")

    def manifest(self) -> dict[str, Any]:
        return publish(unclaimed({"id": self.id, "owner": self.owner, "version_sha256": self.version_sha256,
                          "positive_control_hashes": [sha256(x) for x in self.positive_controls],
                          "negative_control_hashes": [sha256(x) for x in self.negative_controls]},
                         where="a checker's manifest"), where="FrozenVerifier.manifest")


@record
class ProfessionalAttestation:
    reviewer_id: str
    role: str
    scope_sha256: str
    detached_signature: str
    # An attached string is never sufficient; the owner's trusted verifier must
    # cryptographically check this against their own reviewer key/identity registry.


class ReviewAuthenticator(Protocol):
    def __call__(self, attestation: ProfessionalAttestation) -> bool: ...


@record
class CandidateDossier:
    """An evaluator's dossier: only its format's fields (:data:`PAYLOAD_FIELDS`), no claim of a state anywhere in it
    (``codes.only_fields``), itself a result in the makers' format (``codes.result_faults``: blocked with a registered
    code and that code's card), and the state its blockers give, checked when it is made, whoever makes it."""
    payload: dict[str, Any]
    content_sha256: str

    def __post_init__(self) -> None:
        payload = only_fields(self.payload, allowed=PAYLOAD_FIELDS + MADE_FIELDS,
                              required=PAYLOAD_FIELDS + ("completion_state",), where="a dossier")
        blockers = payload["blockers"]
        # STATES is (qualified-draft, blocked-escalated): the second when there is any blocker
        if not isinstance(blockers, tuple) or payload["completion_state"] != STATES[bool(blockers)]:
            raise ValueError("a dossier's state is the one its blockers give: blocked when it has any")

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "CandidateDossier":
        payload = plain(payload, where="a dossier's payload")
        return publish(cls(payload, sha256(canonical_json(payload))), where="CandidateDossier.from_payload")

    def intact(self) -> bool:
        """Integrity only; digest can be recomputed by an attacker. Not authenticity."""
        return publish(sha256(canonical_json(self.payload)) == self.content_sha256, where="CandidateDossier.intact")

    def to_dict(self) -> dict[str, Any]:
        return publish(unclaimed({**self.payload, "content_sha256": self.content_sha256}, where="a dossier"), where="CandidateDossier.to_dict")


def _evidence(*, standard: DomainStandard, artifacts: Mapping[str, EvidenceArtifact],
              verifiers: Mapping[str, FrozenVerifier], documentation: Mapping[str, str],
              candidate_actor: str) -> dict[str, Any]:
    """What a dossier assesses: the standard, each artifact's full manifest (its id, kind, producer, source reference,
    claim id, digest and length), the checkers, the documentation and the producer."""
    return {"standard": standard.digest,
            "artifacts": {k: x.manifest() for k, x in sorted(artifacts.items())},
            "verifiers": {k: v.manifest() for k, v in sorted(verifiers.items())},
            "documentation": {k: documentation.get(k, "") for k in DOCUMENT_FIELDS},
            "candidate_actor": candidate_actor}


def evidence_sha256(**evidence: Any) -> str:
    """The digest of the evidence a dossier assesses (:func:`_evidence`'s fields, by keyword). A dossier names it as its
    ``subject_sha256``, and an assessment credits a dossier only for the evidence submitted to that assessment."""
    return sha256(canonical_json(_evidence(**evidence)))


def _verdict(result: Any) -> bool | None:
    """A checker's answer as the evaluator uses it (a ``snapshot.returned`` check): True or False, else None."""
    return result if type(result) is bool else None


def _test(checker: FrozenVerifier, content: bytes) -> tuple[bool, str]:
    try:
        result = returned(checker.check(fresh(content)), _verdict)
    except Exception as exc:
        return False, f"verifier exception {type(exc).__name__}"
    if result is None:
        return False, "verifier did not return a boolean"
    return result, "boolean result"


class InProcessAssuranceEvaluator:
    """Replays the evaluator's checks on actual bytes and frozen controls, in this Python process; emits bounded claims.

    It does not isolate the checks it runs and does not attest that they were isolated: the checkers, controls and
    admission hooks are Python objects the caller hands it, run with the caller's own rights. Custody and separation
    need an evaluator outside this process, which this class does not provide.
    """
    def __init__(self, *, evaluator_id: str, reviewer_authenticator: ReviewAuthenticator | None = None):
        self.evaluator_id = text(evaluator_id, where="evaluator_id, the evaluator's identity")
        if not self.evaluator_id:
            raise ValueError("named evaluator required")
        self.reviewer_authenticator = reviewer_authenticator

    def review_scope(self, *, standard: DomainStandard, artifacts: Mapping[str, EvidenceArtifact],
                     verifiers: Mapping[str, FrozenVerifier], documentation: Mapping[str, str],
                     candidate_actor: str) -> str:
        """What an authenticated review attests: the evidence a dossier assesses (:func:`_evidence`: the standard, each
        artifact's full manifest, the checkers, the documentation and the producer) and the evaluator. A change to any
        of them is a different scope, so an attestation of one never covers another."""
        standard = snapshot(standard, where="standard")
        artifacts = snapshot(artifacts, where="artifacts")
        verifiers = snapshot(verifiers, where="verifiers")
        documentation = snapshot(documentation, where="documentation")
        candidate_actor = text(candidate_actor, where="candidate_actor, the producer")
        return publish(sha256(canonical_json({
            **_evidence(standard=standard, artifacts=artifacts, verifiers=verifiers, documentation=documentation,
                        candidate_actor=candidate_actor),
            "evaluator_id": self.evaluator_id,
        })), where="InProcessAssuranceEvaluator.review_scope")

    def evaluate(self, *, standard: DomainStandard,
                 artifacts: Mapping[str, EvidenceArtifact],
                 verifiers: Mapping[str, FrozenVerifier],
                 documentation: Mapping[str, str],
                 candidate_actor: str,
                 attestation: ProfessionalAttestation | None = None) -> CandidateDossier:
        # Everything is taken where it enters, before anything is checked: plain data, records made again, identities
        # as text. The checks, the dossier and the review scope all read what was taken, never the caller's values; the
        # standard's digest is read again after use (a change would block).
        standard = snapshot(standard, where="standard")
        artifacts = snapshot(artifacts, where="artifacts")
        verifiers = snapshot(verifiers, where="verifiers")
        documentation = snapshot(documentation, where="documentation")
        candidate_actor = text(candidate_actor, where="candidate_actor, the producer")
        attestation = snapshot(attestation, where="attestation")
        if not candidate_actor or candidate_actor == self.evaluator_id:
            raise ValueError("producer and evaluator must have distinct identities")
        pinned = standard.digest
        # What this dossier is about, named by digest when the snapshot is taken (its subject_sha256).
        subject = evidence_sha256(standard=standard, artifacts=artifacts, verifiers=verifiers,
                                  documentation=documentation, candidate_actor=candidate_actor)
        if set(artifacts) - {c.id for c in standard.criteria}:
            raise ValueError("artifact supplied for undeclared criterion")
        if set(verifiers) - {c.verifier_id for c in standard.criteria}:
            raise ValueError("verifier for undeclared criterion")
        if set(documentation) - set(DOCUMENT_FIELDS):
            raise ValueError("unknown documentary field")
        documentary_gaps = [f for f in DOCUMENT_FIELDS
                            if not isinstance(documentation.get(f), str) or not documentation[f].strip()]
        checks: list[dict[str, Any]] = []
        blockers: list[str] = []
        for criterion in standard.criteria:
            artifact = artifacts.get(criterion.id)
            verifier = verifiers.get(criterion.verifier_id)
            row = {"criterion_id": criterion.id, "severity": criterion.severity,
                   "normative_ref": criterion.normative_ref, "failure_modes": list(criterion.failure_modes),
                   "verifier_id": criterion.verifier_id,
                   "artifact": artifact.manifest() if artifact else None,
                   "verifier_manifest": verifier.manifest() if verifier else None,
                   "result": "not_evaluated", "controls": "not_evaluated"}
            reasons: list[str] = []
            if artifact is None:
                reasons.append("missing evidence artifact")
            elif artifact.kind != criterion.expected_artifact_kind:
                reasons.append("wrong artifact kind")
            elif artifact.claimed_by != candidate_actor:
                reasons.append("artifact origin differs from named producer")
            if verifier is None:
                reasons.append("missing independently supplied checker")
            elif verifier.owner == candidate_actor:
                reasons.append("verifier owner same as producer")
            elif verifier.id != criterion.verifier_id:  # the checker's own identity, never the caller's mapping key
                reasons.append(f"the supplied checker is {verifier.id!r}, not the {criterion.verifier_id!r} the "
                               "standard pins")
            else:
                positives = [_test(verifier, a) for a in verifier.positive_controls]
                negatives = [_test(verifier, a) for a in verifier.negative_controls]
                # A planted incorrect artefact must be DETECTED, not merely
                # cause an exception or an execution refusal.
                controls_ok = (bool(positives) and bool(negatives)  # an empty replay detects nothing
                               and all(x == (True, "boolean result") for x in positives)
                               and all(x == (False, "boolean result") for x in negatives))
                row["controls"] = "detected" if controls_ok else "failed_or_inconclusive"
                row["control_tests"] = {"positive_count": len(positives),
                                         "negative_count": len(negatives),
                                         "positive_passed": sum(x == (True, "boolean result") for x in positives),
                                         "negative_detected": sum(x == (False, "boolean result") for x in negatives)}
                if not controls_ok:
                    reasons.append("positive/negative controls not reproducibly detected")
                if artifact is not None and not reasons:
                    ok, why = _test(verifier, artifact.content)
                    row["result"] = "passed" if ok and why == "boolean result" else "failed"
                    if not ok or why != "boolean result":
                        reasons.append("artifact failed verifier or verifier did not execute")
            if reasons:
                row["result"] = "failed" if row["result"] == "not_evaluated" else row["result"]
                row["reason"] = "; ".join(reasons)
                if criterion.severity == "critical":
                    blockers.append(f"{criterion.id}: {row['reason']}")
            checks.append(row)
        if documentary_gaps:
            blockers.append("required documentary sections absent: " + ", ".join(documentary_gaps))
        reviewed = False
        review_reason = "not requested at this risk level"
        # A string saying 'reviewed' is not an attestation. An authentication
        # function must be supplied by the trusted evaluator, not the producer.
        if standard.risk >= 2:
            review_reason = "authenticated professional review missing"
            if attestation is not None and self.reviewer_authenticator is not None:
                evidence_scope = self.review_scope(standard=standard, artifacts=artifacts, verifiers=verifiers,
                                                   documentation=documentation, candidate_actor=candidate_actor)
                if (attestation.scope_sha256 == evidence_scope
                        and attestation.reviewer_id not in (candidate_actor, self.evaluator_id)):
                    try:
                        authentic = affirmed(self.reviewer_authenticator(fresh(attestation)))
                    except Exception:
                        authentic = False
                    if authentic:
                        reviewed, review_reason = True, "authenticated separate professional reviewer attested this scope"
            if not reviewed:
                blockers.append(review_reason)
        critical = [x for x in checks if x["severity"] == "critical"]
        advisory_failed = [x["criterion_id"] for x in checks
                           if x["severity"] == "advisory" and x["result"] != "passed"]
        # A suite of only advisory rules cannot establish a critical objective.
        if not critical:
            blockers.append("no critical acceptance obligation exists")
        if advisory_failed:
            blockers.append("advisory exceptions require explicit professional disposition: " + ", ".join(advisory_failed))
        if standard.digest != pinned:  # read again after use
            blockers.append("the standard changed while it was evaluated")
        payload = settled(
            refusal(DOSSIER_BLOCKED, "; ".join(blockers)) if blockers else None,
            schema=FORMAT,
            kind="candidate_verification_dossier_not_G6_promotion_certificate",
            scope_decision="incomplete_or_failed" if blockers else "all_declared_checks_passed_in_this_evaluator",
            evaluator_id=self.evaluator_id,
            candidate_actor=candidate_actor,
            standard=standard.manifest(), standard_digest=standard.digest,
            subject_sha256=subject,
            checks=checks,
            documentary_sections={k: documentation.get(k, "") for k in DOCUMENT_FIELDS},
            missing_documentation=documentary_gaps,
            professional_review={"required": standard.risk >= 2, "risk_tier": standard.risk_tier,
                                 "authenticated": reviewed, "reason": review_reason},
            blockers=blockers,
            metrics={"declared_critical": len(critical),
                     "critical_passed": sum(x["result"] == "passed" for x in critical),
                     "declared_advisory": len(checks)-len(critical),
                     "advisory_exceptions": len(advisory_failed),
                     "negative_controls_detected": sum(
                         x.get("control_tests", {}).get("negative_detected", 0) for x in checks),
                     "negative_controls_attempted": sum(
                         x.get("control_tests", {}).get("negative_count", 0) for x in checks)},
            promotion_authority="none: only a G6 door that re-runs a check can promote a result",
            limitations=[
                "The standard and its faithful translation are trusted evaluator inputs, not independently confirmed here.",
                "A model being true in its formalisation does not imply it describes reality.",
                "The SHA-256 content digest is unkeyed: it detects edits, not malicious rewrite and reissuance.",
                "Executor/custody isolation is not established by this in-process reference implementation.",
                "An assurance dossier is not professional accreditation, an audit opinion, or a result G6 promoted.",
            ],
        )
        return publish(issue(self, CandidateDossier.from_payload(payload)), where="InProcessAssuranceEvaluator.evaluate")


__all__ = ["Criterion", "DomainStandard", "EvidenceArtifact", "FrozenVerifier",
           "ProfessionalAttestation", "CandidateDossier", "InProcessAssuranceEvaluator",
           "DOCUMENT_FIELDS", "FORMAT", "PAYLOAD_FIELDS", "canonical_json", "evidence_sha256", "sha256"]
