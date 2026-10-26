# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""PA-15: a canonical evidence-provenance vocabulary so the inspectable audit trail
DISTINGUISHES the four kinds of recorded entry — a MODEL assertion, a TOOL result, a
FORMAL proof, or a HUMAN approval — per ISO/IEC 42001 Clause 9.2 (an attributable,
inspectable trail). The durable action-gating trail tags each record with its provenance
class (human-approval / tool / model) inline; the CSF safety-decision trail supplies the
FORMAL class from its existing Z3/Lean verification fields. One vocabulary spans both, so
a reviewer can always tell WHAT backed any entry.

Minimal + additive (founder-approved, 2026-06-25): a new ``provenance_class`` field on the
durable record (default classified at write time) + deterministic classifiers. No schema
migration — the field round-trips inside the existing ``record_json`` blob, so the existing
query API (``get_by_audit_id`` / ``get_by_action_id`` / ``list_recent``) exposes it.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

# A human approval is the most authoritative attribution; then a formal proof; then a
# tool result; otherwise the entry rests on a model assertion. The precedence makes the
# classification deterministic when more than one signal is present.
_PRECEDENCE = ("human_approval", "formal", "tool", "model")



class EvidenceProvenance(str, Enum):
    """The four distinguishable provenance classes of an audit-trail entry."""

    MODEL = "model"  # an LLM / model assertion
    TOOL = "tool"  # a tool / component / backend call result
    FORMAL = "formal"  # a formal proof / verification (Z3 / Lean)
    HUMAN_APPROVAL = "human_approval"  # a human sign-off / HITL confirmation


def classify_provenance(
    *,
    requires_human_approval: bool = False,
    has_formal_verification: bool = False,
    is_tool_invocation: bool = False,
) -> EvidenceProvenance:
    """Classify an entry from explicit provenance signals (precedence: human > formal > tool > model)."""
    if requires_human_approval:
        return EvidenceProvenance.HUMAN_APPROVAL
    if has_formal_verification:
        return EvidenceProvenance.FORMAL
    if is_tool_invocation:
        return EvidenceProvenance.TOOL
    return EvidenceProvenance.MODEL


def _human_approval_signal(*, approval_receipt: Any) -> bool:
    """Human approval is evidenced only by a recorded approval receipt (audit 002, AG-006).

    The archive also counted a *required* confirmation, an issued token, a review-required
    decision and a user-authored action as approval; none of those shows that a person
    approved this action, so they no longer produce ``human_approval`` provenance.
    """
    return bool(approval_receipt)


def _tool_signal(*, actor_type: str, target_backend: str) -> bool:
    # A gated agent / service / system action against a concrete backend is a tool call.
    backend = (target_backend or "").strip().lower()
    return actor_type in {"agent", "service", "system"} and backend not in {"", "unknown"}


def classify_audit_record(record: Any) -> EvidenceProvenance:
    """Derive the provenance class of a (persisted) ``ActionGateAuditRecord`` from its signals.

    The action-gating trail produces human-approval / tool / model entries. (Formal-proof
    provenance lives on the CSF safety-decision trail — see ``classify_csf_provenance``.)
    """
    capsule = getattr(record, "action_capsule", None)
    actor = str(getattr(capsule, "actor_type", "") or "") if capsule is not None else ""
    backend = str(getattr(capsule, "target_backend", "") or "") if capsule is not None else ""
    return classify_provenance(
        requires_human_approval=_human_approval_signal(
            approval_receipt=getattr(record, "approval_receipt", None),
        ),
        has_formal_verification=False,
        is_tool_invocation=_tool_signal(actor_type=actor, target_backend=backend),
    )


def classify_csf_provenance(*, z3_verified: bool = False, lean_verified: bool = False) -> EvidenceProvenance:
    """The CSF safety-decision trail's class: a Z3/Lean-backed verdict is FORMAL provenance;
    otherwise the verdict rests on a model / policy assertion (MODEL)."""
    if z3_verified or lean_verified:
        return EvidenceProvenance.FORMAL
    return EvidenceProvenance.MODEL


def provenance_for_decision(capsule: Any, decision: Any) -> EvidenceProvenance:
    """Write-site classifier: derive provenance from the ``ActionCapsule`` + ``GateDecision``
    being recorded, so the durable record is tagged as it is created."""
    actor = str(getattr(capsule, "actor_type", "") or "")
    backend = str(getattr(capsule, "target_backend", "") or "")
    return classify_provenance(
        requires_human_approval=_human_approval_signal(
            approval_receipt=getattr(decision, "approval_receipt", None),
        ),
        has_formal_verification=False,
        is_tool_invocation=_tool_signal(actor_type=actor, target_backend=backend),
    )
