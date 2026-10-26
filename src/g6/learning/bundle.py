# SPDX-License-Identifier: Apache-2.0
"""Portable harness bundles: export chosen theories and artifacts with a review; import them back as candidates.

Hardened from the archive's ``harness_bundle.py`` and the import op of ``learning_block.py`` (audit 006):

- import never yields an active theory: each comes back a ``candidate`` with zero evidence and the default prior,
  whatever the bundle says about its status or strength, and adding it to a store refuses an existing id (LL-007);
- theory text (instruction, rationale, skill) is scanned along with the artifacts, and the artifacts are returned,
  not counted and dropped (LL-007). The scan is a few regular expressions: a tripwire, not a prompt-injection defence;
- a bundle without a manifest is refused, and the leakage check is recomputed on import against the caller's own
  forbidden terms rather than trusting the bundle's claim (LL-011). With no terms it is ``not_applicable``, never
  ``passed``; short answers such as ``"42"`` are checked, as whole words;
- export writes the theories asked for and never a retired one, not the whole store (LL-015);
- the manifest's digest seals the theory and artifact files and the review it carries, and import checks it, so a
  review edited, added or removed after export is refused. A review is a record: nothing authenticates its author.
  The digest is unkeyed, so it catches an accidental or partial edit, not a rewrite that also recomputes it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from g6.learning.schema import HarnessArtifact, PromptAddendum, ReviewRecord, SkillDocument, Theory
from g6.learning.theory_store import TheoryStore

#: ``/2``: the digest seals the review too. A ``/1`` bundle (digest over the files only) is refused as made by an
#: earlier version of G6, not as tampered with (review B1-3).
FORMAT = "g6.learning.bundle/2"
OLD_FORMATS = ("g6.learning.bundle/1",)
CANDIDATES_ONLY = "G6_E_LEARNING_CANDIDATES_ONLY"
OLD_FORMAT = "G6_E_LEARNING_BUNDLE_OLD_FORMAT"

_RISKY = tuple((re.compile(pattern, re.IGNORECASE), code) for pattern, code in (
    (r"\bsubprocess\b|\bos\.system\b|\beval\(|\bexec\(", "dangerous_code_execution"),
    (r"\bopen\(.+['\"]w|Path\(.+\)\.write_", "filesystem_write"),
    (r"\brequests\.|urllib\.|httpx\.", "network_access"),
    (r"ignore (all )?(previous|prior) instructions|system prompt|developer message", "prompt_injection"),
    (r"\bapi[_-]?key\b|\bsecret\b|\bpassword\b|\btoken\b", "possible_secret_reference"),
))


def scan_text(text: str) -> list[str]:
    """The risk codes whose pattern appears in ``text`` (empty when none does)."""
    return [code for pattern, code in _RISKY if pattern.search(text or "")]


def leakage_attestation(theories: Sequence[Theory], artifacts: Sequence[HarnessArtifact],
                        forbidden_terms: Iterable[str]) -> dict[str, Any]:
    """Whether any forbidden term (a gold answer, say) appears as a whole word in the theories or artifacts."""
    terms = sorted({str(t).strip() for t in forbidden_terms if str(t).strip()})
    if not terms:
        return {"status": "not_applicable", "terms_checked": 0, "violations": []}  # audit 006, LL-011
    violations = []
    for location, text in _texts(theories, artifacts):
        for term in terms:
            if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.IGNORECASE):
                violations.append({"location": location, "term_sha256": hashlib.sha256(term.encode()).hexdigest()[:16]})
    return {"status": "failed" if violations else "passed", "terms_checked": len(terms), "violations": violations}


def export_bundle(path: str | os.PathLike[str], store: TheoryStore, artifacts: Sequence[HarnessArtifact] = (),
                  review: ReviewRecord | None = None, *, theory_ids: Iterable[str] | None = None,
                  forbidden_terms: Iterable[str] = ()) -> dict[str, Any]:
    """Write a bundle directory at ``path`` and return its manifest. ``theory_ids`` picks the theories (default: every
    non-retired theory of the store's scope). Refuses retired or unknown theories, risky text and leaked terms."""
    if theory_ids is None:
        theories = [t for t in store.theories() if t.status != "retired"]
    else:
        theories = []
        for tid in theory_ids:
            theory = store.get(tid)
            if theory is None or theory.status == "retired":
                raise ValueError(f"G6_E_LEARNING_UNKNOWN_THEORY: {tid!r} is not a live theory in scope {store.scope!r}")
            theories.append(theory)
    unsafe = _violations(theories, artifacts)
    if unsafe:
        raise ValueError(f"G6_E_LEARNING_BUNDLE_UNSAFE: {unsafe}")
    attestation = leakage_attestation(theories, artifacts, forbidden_terms)
    if attestation["status"] == "failed":
        raise ValueError(f"G6_E_LEARNING_BUNDLE_LEAKAGE: {attestation['violations']}")
    theories_doc = [_theory_doc(t) for t in theories]
    artifacts_doc = [_artifact_doc(a) for a in artifacts]
    review_doc = review.to_dict() if review else None
    manifest = {"format": FORMAT, "scope": store.scope, "created_at": datetime.now(UTC).isoformat(),
                "n_theories": len(theories_doc), "n_artifacts": len(artifacts_doc),
                "content_digest": _content_digest(theories_doc, artifacts_doc, review_doc),
                "anti_leakage_attestation": attestation, "review": review_doc}
    out = Path(path)
    out.mkdir(parents=True, exist_ok=True)
    for name, doc in (("theories.json", theories_doc), ("artifacts.json", artifacts_doc), ("manifest.json", manifest)):
        (out / name).write_text(json.dumps(doc, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


@dataclass(frozen=True)
class CandidateBundle:
    """An imported bundle: candidate theories (never active), its artifacts and review, or why it was refused."""

    theories: tuple[Theory, ...] = ()
    artifacts: tuple[HarnessArtifact, ...] = ()
    review: ReviewRecord | None = None
    attestation: dict[str, Any] = field(default_factory=dict)
    code: str = CANDIDATES_ONLY
    reason: str = ""
    path: str = ""

    @property
    def completion_state(self) -> str:
        return "qualified-draft" if self.code == CANDIDATES_ONLY else "blocked-escalated"

    def to_envelope(self, *, request_id: str = "", run_id: str = "") -> dict[str, Any]:
        from g6.core.reliability_envelope import envelope_fields, warning_card

        message = self.reason or ("Imported as candidates with no evidence; none is used outside training until "
                                  "promote() recommends it and a person accepts it.")
        return {"path": self.path, "theory_ids": [t.theory_id for t in self.theories],
                "n_artifacts": len(self.artifacts), "attestation": dict(self.attestation),
                **envelope_fields(reliability_label=self.completion_state,  # type: ignore[arg-type]
                                  warning_card=warning_card(self.code, message), request_id=request_id, run_id=run_id)}


def import_bundle(path: str | os.PathLike[str], *, forbidden_terms: Iterable[str] = ()) -> CandidateBundle:
    """Read a bundle as candidates. Refused (``blocked-escalated``, nothing returned) when the manifest is missing or
    malformed, the bundle was made by an earlier version of G6 (``OLD_FORMAT``: export it again), the files or the
    review do not match its digest, the review is incomplete, text is risky, or a forbidden term appears. Add the
    result to a store with ``store.add_candidates(bundle.theories)``."""
    bp = Path(path)

    def refused(code: str, reason: str) -> CandidateBundle:
        return CandidateBundle(code=code, reason=reason, path=str(bp))

    if not bp.is_dir():
        return refused("G6_E_LEARNING_BUNDLE_NOT_FOUND", f"no bundle directory at {bp}")
    if not (bp / "manifest.json").is_file():  # audit 006, LL-011: the archive imported a bundle with no manifest
        return refused("G6_E_LEARNING_BUNDLE_NO_MANIFEST", "the bundle has no manifest.json")
    try:
        manifest = _load(bp / "manifest.json", dict)
        theories_doc = _load(bp / "theories.json", list)
        artifacts_doc = _load(bp / "artifacts.json", list)
        if manifest.get("format") in OLD_FORMATS:
            return refused(OLD_FORMAT, f"this bundle ({manifest.get('format')}) was made by an earlier version of G6, "
                           f"whose digest does not seal its review; export it again with this version ({FORMAT})")
        if manifest.get("format") != FORMAT:
            return refused("G6_E_LEARNING_BUNDLE_INVALID", f"unknown bundle format {manifest.get('format')!r}")
        raw_review = manifest.get("review")
        review = None if raw_review is None else ReviewRecord(**{k: str(raw_review.get(k, "")) for k in (
            "reviewer_id", "reviewed_at", "scope_summary", "residual_risk_notes")})
        if manifest.get("content_digest") != _content_digest(theories_doc, artifacts_doc,
                                                             review.to_dict() if review else None):
            return refused("G6_E_LEARNING_BUNDLE_DIGEST_MISMATCH",
                           "the files or the review do not match the manifest's digest")
        theories = tuple(_candidate(item) for item in theories_doc)
        artifacts = tuple(_artifact(item) for item in artifacts_doc)
    except ValueError as exc:
        code = "G6_E_LEARNING_REVIEW_INCOMPLETE" if str(exc).startswith("G6_E_LEARNING_REVIEW") else \
            "G6_E_LEARNING_BUNDLE_INVALID"
        return refused(code, str(exc))
    except (OSError, KeyError, TypeError, AttributeError) as exc:
        return refused("G6_E_LEARNING_BUNDLE_INVALID", f"{type(exc).__name__}: {exc}")
    unsafe = _violations(theories, artifacts)
    if unsafe:  # audit 006, LL-007: theory text is what enters prompts, so it is scanned too
        return refused("G6_E_LEARNING_BUNDLE_UNSAFE", f"risky text: {unsafe}")
    attestation = leakage_attestation(theories, artifacts, forbidden_terms)
    if attestation["status"] == "failed":
        return refused("G6_E_LEARNING_BUNDLE_LEAKAGE", f"forbidden terms appear: {attestation['violations']}")
    reason = "" if attestation["status"] == "passed" else (
        "Imported as candidates with no evidence. Leakage was not checked: no forbidden terms were given.")
    return CandidateBundle(theories, artifacts, review, attestation, reason=reason, path=str(bp))


def _texts(theories: Sequence[Theory], artifacts: Sequence[HarnessArtifact]) -> list[tuple[str, str]]:
    found = []
    for t in theories:
        found += [(f"theory:{t.theory_id}:instruction", t.instruction), (f"theory:{t.theory_id}:rationale", t.rationale),
                  (f"theory:{t.theory_id}:skill", t.skill_content or "")]
    for idx, a in enumerate(artifacts):
        found.append((f"artifact:{idx}:{type(a).__name__}", a.text if isinstance(a, PromptAddendum) else a.content))
    return found


def _violations(theories: Sequence[Theory], artifacts: Sequence[HarnessArtifact]) -> list[dict[str, str]]:
    return [{"location": loc, "code": code} for loc, text in _texts(theories, artifacts) for code in scan_text(text)]


def _theory_doc(t: Theory) -> dict[str, Any]:
    return {"theory_id": t.theory_id, "failure_code": t.failure_code, "trigger": t.trigger,
            "instruction": t.instruction, "rationale": t.rationale, "source_instances": list(t.source_instances),
            "skill_content": t.skill_content,
            # informational only: import resets status and evidence (LL-007)
            "exported_state": {"status": t.status, "strength": t.strength, "confirmations": t.confirmations,
                               "disconfirmations": t.disconfirmations}}


def _candidate(item: dict[str, Any]) -> Theory:
    # audit 006, LL-007: status, strength and counters from the bundle are ignored; the theory starts as a candidate.
    return Theory(theory_id=str(item["theory_id"]), failure_code=str(item["failure_code"]),
                  instruction=str(item["instruction"]), trigger=str(item.get("trigger") or "always"),
                  rationale=str(item.get("rationale") or ""),
                  source_instances=[str(s) for s in item.get("source_instances") or []],
                  skill_content=None if item.get("skill_content") is None else str(item["skill_content"]))


def _artifact_doc(a: HarnessArtifact) -> dict[str, Any]:
    if isinstance(a, PromptAddendum):
        return {"type": "PromptAddendum", "text": a.text, "theory_ids": list(a.theory_ids)}
    if isinstance(a, SkillDocument):
        return {"type": "SkillDocument", "content": a.content, "theory_id": a.theory_id}
    raise TypeError(f"G6_E_LEARNING_BAD_ARTIFACT: {type(a).__name__} is not a bundle artifact")


def _artifact(item: dict[str, Any]) -> HarnessArtifact:
    if item.get("type") == "PromptAddendum":
        return PromptAddendum(str(item["text"]), tuple(str(t) for t in item.get("theory_ids") or ()))
    if item.get("type") == "SkillDocument":
        return SkillDocument(str(item["content"]), str(item.get("theory_id") or ""))
    raise ValueError(f"G6_E_LEARNING_BUNDLE_INVALID: unknown artifact type {item.get('type')!r}")


def _content_digest(theories_doc: list[Any], artifacts_doc: list[Any], review_doc: dict[str, str] | None) -> str:
    canonical = json.dumps({"theories": theories_doc, "artifacts": artifacts_doc, "review": review_doc}, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _load(path: Path, kind: type) -> Any:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, kind):
        raise ValueError(f"G6_E_LEARNING_BUNDLE_INVALID: {path.name} is not a JSON {kind.__name__}")
    return value


__all__ = ["CANDIDATES_ONLY", "FORMAT", "OLD_FORMAT", "CandidateBundle", "export_bundle", "import_bundle",
           "leakage_attestation", "scan_text"]
