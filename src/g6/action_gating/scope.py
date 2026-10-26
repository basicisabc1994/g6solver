# SPDX-License-Identifier: Apache-2.0
"""A review scope: the bounds a person reviewed a workflow for, as declared context that can only tighten the gate.

Merged from the archive's ``domain_certification`` (finding DOMAIN_CERTIFICATION-01 to -03). A ``ReviewScope`` names
the action types the review covers (``allowed_task_patterns``, shell-style wildcards matched against the whole action
type, ignoring case, never against its description), the ones it forbids, a risk ceiling on the gate's own risk reading, and an
expiry. ``evaluate_action(action, scope=...)`` runs :func:`check_scope` after its own checks, through
``tighten_decision``: an action outside the scope, above its ceiling, or after its expiry goes to a person; a forbidden
one, or a scope that cannot be read, is blocked; inside the scope the gate's own decision stands. A scope never grants
anything: no field of it can lower a hold, and it has no ``certified``, ``allow_autonomous``, reviewer, review log,
standards, gold-case or threshold field (the archive passed each of those on whatever the caller wrote).

:func:`review_record` is what the archive called a certificate: what was observed for a scope, read by ``g6.evidence``
(receipts of harness runs and held-out results, each counted only under its grounding rule), plus a person's decision
recorded on the control plane (``g6.core.approval_authority``, for :func:`approval_scope`). It is ``qualified-draft``
at best, and ``blocked-escalated`` when the scope has expired, nothing observed counts, a check refuted it or no
person's decision is recorded. It grants nothing either: each action is still decided by ``evaluate_action``.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from g6.core.ledger import ChainLedger
from g6.evidence import Artifact, CertificateRefused, Check, MissionFacts, Receipt, issue

#: What a review record reads receipts for, and the gate a person's decision fills.
REVIEW_PREDICATES = ("harness_runs", "held_out_results")
PERSON_GATE = "person_review"
#: The gate's risk readings in order; a scope's ceiling is one of the first three.
_RISK_ORDER = ("low", "medium", "high", "critical", "unknown")
_GRANTS = "nothing: each action is still decided by evaluate_action, and a person approves a held one"
#: One action-type pattern: shell-style wildcards (``*``, ``?``, ``[...]``) over the whole action type.
Pattern = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class ReviewScope(BaseModel):
    """The bounds a workflow was reviewed for. Frozen; an unknown field is refused, never dropped."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope_id: str = Field(min_length=1, max_length=100)
    allowed_task_patterns: list[Pattern] = Field(min_length=1, max_length=100)
    forbidden_actions: list[Pattern] = Field(default_factory=list, max_length=100)
    risk_ceiling: Literal["low", "medium", "high"]
    expires_at: AwareDatetime


def scope_digest(scope: ReviewScope) -> str:
    """The scope's SHA-256 over its canonical JSON: what a record and a person's approval are bound to."""
    body = json.dumps(scope.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def approval_scope(scope: ReviewScope) -> str:
    """The control-plane approval scope a person's decision on this review scope is issued for."""
    return f"action_gating.review_scope:{scope_digest(scope)}"


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def check_scope(action_type: str, aggregate_risk: str, scope: ReviewScope | dict | Any,
                *, now: datetime | None = None) -> list[tuple[str, str, dict[str, Any]]]:
    """Each ``(candidate decision, code, evidence item)`` the scope adds for one action; ``"allow"`` adds nothing."""
    try:
        bounds = scope if isinstance(scope, ReviewScope) else ReviewScope.model_validate(scope)
    except (ValidationError, ValueError, TypeError) as exc:
        return [("block", "G6_E_SCOPE_INVALID", {"kind": "review_scope", "code": "G6_E_SCOPE_INVALID",
                                                  "reason": str(exc).splitlines()[0][:300], "grants": "nothing"})]
    base = {"kind": "review_scope", "scope_id": bounds.scope_id, "scope_sha256": scope_digest(bounds),
            "action_type": action_type, "aggregate_risk": aggregate_risk, "grants": "nothing"}
    found, kind = [], action_type.lower()   # case never decides a match: both sides are lowered
    if any(fnmatchcase(kind, p.lower()) for p in bounds.forbidden_actions):
        found.append(("block", "G6_E_SCOPE_FORBIDDEN_ACTION"))
    if _now(now) >= bounds.expires_at:
        found.append(("require_human_review", "G6_E_SCOPE_EXPIRED"))
    if not any(fnmatchcase(kind, p.lower()) for p in bounds.allowed_task_patterns):
        found.append(("require_human_review", "G6_E_SCOPE_OUTSIDE"))
    ceiling = _RISK_ORDER.index(bounds.risk_ceiling)
    if aggregate_risk not in _RISK_ORDER or _RISK_ORDER.index(aggregate_risk) > ceiling:
        found.append(("require_human_review", "G6_E_SCOPE_RISK_CEILING"))
    if not found:
        return [("allow", "G6_E_SCOPE_WITHIN", {**base, "within": True})]
    return [(decision, code, {**base, "within": False, "code": code}) for decision, code in found]


def _person(authority: Any, approval_id: str | None, scope: ReviewScope) -> dict[str, Any] | None:
    """The person's recorded decision on this scope, or ``None``: an approval issued on the control plane by a person
    for :func:`approval_scope`, still live. A reviewer's name, a log or an id that resolves to nothing is no decision,
    and a store that fails counts as none."""
    if not isinstance(approval_id, str) or not approval_id:
        return None
    try:
        if not authority.check(approval_id, scope=approval_scope(scope), require_human=True):
            return None
        record = authority.resolve(approval_id)
        return {k: record.redacted()[k] for k in ("reviewer_id", "decision", "issuer_kind", "created_at")}
    except Exception:  # noqa: BLE001 - an approval store that fails is no decision (fail closed)
        return None


def review_record(scope: ReviewScope, *, artifact: Artifact, checks: Sequence[Check] = (),
                  receipts: Sequence[Receipt] = (), approval_id: str | None = None, authority: Any = None,
                  now: datetime | None = None, ledger: ChainLedger | None = None) -> dict[str, Any]:
    """What was observed for ``scope``: ``g6.evidence`` reads ``checks`` and ``receipts`` against ``artifact`` (whose
    digest must be the scope's) for :data:`REVIEW_PREDICATES`, and a person's decision fills :data:`PERSON_GATE`.
    ``ledger``: the ``g6.core.ledger.ChainLedger`` the runner that observed the controls and receipts wrote them to
    (``g6.evidence.record``); a record it does not hold counts as nothing observed. ``qualified-draft`` at best; it
    grants nothing."""
    if authority is None:
        from g6.core.approval_authority import get_approval_authority

        authority = get_approval_authority()
    digest, ids = scope_digest(scope), {"request_id": uuid.uuid4().hex, "run_id": uuid.uuid4().hex}
    person = _person(authority, approval_id, scope)
    head = {"kind": "review_scope_record", "scope_id": scope.scope_id, "scope_sha256": digest,
            "expires_at": scope.expires_at.isoformat(), "person_decision": person, "grants": _GRANTS}
    facts = MissionFacts(mission_sha=digest, mission_standard_sha=digest, standard_sha=digest,
                         predicates=REVIEW_PREDICATES, standard_predicates=REVIEW_PREDICATES, gates=(PERSON_GATE,))
    try:
        if artifact.sha256 != digest:
            raise CertificateRefused(["the artifact is not this scope: its digest differs"])
        read = issue(facts, artifact, checks, receipts, decisions=(PERSON_GATE,) if person else (), ledger=ledger)
    except CertificateRefused as refused:
        return _finish(head | {"predicates": [], "gaps": [], "receipts": []}, "G6_E_SCOPE_RECORD_REFUSED",
                       "; ".join(refused.problems)[:500], "", ids)
    # g6.evidence names a formal check with no digest "formal_without_certificate"; this record keeps that word out.
    gaps = [[subject, why.replace("formal_without_certificate", "formal_without_proof_digest")]
            for subject, why in read.gaps]
    body = head | {"predicates": [s.as_dict() for s in read.predicates], "gaps": gaps,
                   "receipts": [{"receipt_id": r.receipt_id, "check_id": r.check_id, "result": r.result}
                                for r in read.receipts]}
    if _now(now) >= scope.expires_at:
        code, why = "G6_E_SCOPE_EXPIRED", "the scope expired, so its review no longer covers anything"
    elif any(s.refute for s in read.predicates):
        code, why = "G6_E_SCOPE_EVIDENCE_REFUTED", "a grounded check refuted the scope's record"
    elif not any(s.support for s in read.predicates):
        code, why = "G6_E_SCOPE_NOTHING_OBSERVED", "no receipt counts under g6.evidence's grounding rule"
    elif person is None:
        code, why = "G6_E_SCOPE_DECISION_MISSING", "no live approval by a person is recorded for this scope"
    else:
        code, why = "", ""
    return _finish(body, code, why, read.state, ids)


def _finish(body: dict[str, Any], code: str, why: str, evidence_state: str, ids: dict[str, str]) -> dict[str, Any]:
    state = "blocked-escalated" if code else "qualified-draft"
    card = {"code": code or "G6_E_SCOPE_RECORD_DRAFT", "severity": "blocked" if code else "amber",
            "message": why or "What was observed for this scope, with a person's recorded decision. It grants "
                              "nothing: each action is still decided by the gate.",
            "suggested_action": "Fill the gaps named, then record a person's decision for approval_scope(scope)."
            if code else "Keep the scope current; evaluate each action with evaluate_action(action, scope=scope)."}
    return body | {"completion_state": state, "warning_card": card, **ids,
                   "evidence": [{"kind": "review_scope_record", "evidence_state": evidence_state,
                                 "predicates": list(REVIEW_PREDICATES), "gate": PERSON_GATE, "code": card["code"],
                                 "capped_at": "qualified-draft"}]}


__all__ = ["PERSON_GATE", "REVIEW_PREDICATES", "ReviewScope", "approval_scope", "check_scope", "review_record",
           "scope_digest"]
