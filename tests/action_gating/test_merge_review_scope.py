# SPDX-License-Identifier: Apache-2.0
"""The archive's ``domain_certification`` merged into ``g6.action_gating``: a review scope (allowed task patterns,
forbidden actions, a risk ceiling, an expiry) is declared context for ``evaluate_action`` and can only tighten its
decision; the archive's certificate becomes ``review_record``, a ``g6.evidence`` reading of observed receipts plus a
person's decision recorded on the control plane. Nothing in either grants autonomy. Caller files, reviewer strings,
``certified`` and ``allow_autonomous`` count for nothing."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from g6.action_gating import evaluate_action
from g6.action_gating.audit import ActionGateAuditLogger
from g6.action_gating.decision_engine import _DECISION_RANK
from g6.action_gating.scope import (
    PERSON_GATE,
    REVIEW_PREDICATES,
    ReviewScope,
    approval_scope,
    review_record,
    scope_digest,
)
from g6.core.approval_authority import ISSUER_POLICY_ORACLE, ApprovalAuthority
from g6.core.failure_modes import FailureModeRegistry
from g6.core.registry import get_registry
from g6.core.ledger import ChainLedger
from g6.evidence import Artifact, Check, Control, Receipt, record
from g6.invariants import check_envelope
from g6.mcp import invoke_reply

LATER = (datetime.now(UTC) + timedelta(days=30)).isoformat()
EARLIER = (datetime.now(UTC) - timedelta(days=1)).isoformat()
READ = {"action_type": "read_file", "target_resource": "notes.txt"}
WRITE = {"action_type": "write_file", "target_resource": "notes.txt"}
SHA = "c" * 64


def scope(**overrides) -> ReviewScope:
    fields = {"scope_id": "notes-assistant", "allowed_task_patterns": ["read_file"], "forbidden_actions": [],
              "risk_ceiling": "low", "expires_at": LATER} | overrides
    return ReviewScope.model_validate(fields)


def gate(action: dict, review: ReviewScope | dict | None):
    return evaluate_action(action, audit_logger=ActionGateAuditLogger(), scope=review)


# --- D1: a workflow used beyond what its review covers --------------------------------------------------------------

@pytest.mark.parametrize(("action", "review", "decision", "code"), [
    (WRITE, scope(), "require_human_review", "G6_E_SCOPE_OUTSIDE"),
    ({**READ, "description": "read_file notes, then tidy them"}, scope(allowed_task_patterns=["write_*"]),
     "require_human_review", "G6_E_SCOPE_OUTSIDE"),
    (READ, scope(allowed_task_patterns=["*"], forbidden_actions=["read_*"]), "block", "G6_E_SCOPE_FORBIDDEN_ACTION"),
    (READ, scope(expires_at=EARLIER), "require_human_review", "G6_E_SCOPE_EXPIRED"),
    ({**READ, "blast_radius": "organization"}, scope(), "require_human_review", "G6_E_SCOPE_RISK_CEILING"),
], ids=["outside", "description-ignored", "forbidden", "expired", "risk-ceiling"])
def test_d1_an_action_beyond_its_review_scope_is_held(action, review, decision, code) -> None:
    # Removing evaluate_action's call to scope.check_scope makes this fail (the gate's own allow stands).
    result = gate(action, review)
    assert result.gate_decision.decision == decision and code in result.gate_decision.reason_codes
    assert result.completion_state == "blocked-escalated" and check_envelope(result.to_response_dict()) == []
    assert any(item.get("kind") == "review_scope" and item.get("code") == code for item in result.evidence)


def test_d1_inside_the_scope_the_gate_s_own_decision_stands() -> None:
    # Removing check_scope's "allow" for a match makes this fail (an in-scope read would be held).
    assert gate(READ, scope()).gate_decision.decision == "allow"
    within = [i for i in gate(READ, scope()).evidence if i.get("kind") == "review_scope"]
    assert within and within[0]["within"] is True and within[0]["grants"] == "nothing"


# --- DOMAIN_CERTIFICATION-02: no string match grants autonomy -------------------------------------------------------

@pytest.mark.parametrize("action", [
    {"action_type": "send_email", "target_resource": "all-customers@example.invalid"},
    {"action_type": "delete_file", "target_backend": "local", "target_resource": "notes.txt"},
], ids=["send_email", "delete_file"])
def test_p1_02_a_matching_scope_never_loosens_a_hold(action) -> None:
    # Removing tighten_decision's monotone rank (taking the scope's "allow" as the decision) makes this fail.
    base = gate(action, None).gate_decision
    scoped = gate(action, scope(allowed_task_patterns=["*"], risk_ceiling="high")).gate_decision
    assert base.decision != "allow" and _DECISION_RANK[scoped.decision] >= _DECISION_RANK[base.decision]
    assert scoped.completion_state != "verified" and "allow_autonomous" not in json.dumps(scoped.model_dump())


def test_p1_02_patterns_match_the_whole_action_type_never_a_word_inside_it() -> None:
    # Replacing check_scope's whole-string fnmatchcase with a substring test makes this fail.
    for action_type in ("read_file_and_delete", "unread_file", "read_files"):
        held = gate({"action_type": action_type, "target_resource": "notes.txt"}, scope()).gate_decision
        assert "G6_E_SCOPE_OUTSIDE" in held.reason_codes, action_type


def test_m6_action_types_and_patterns_match_without_regard_to_case() -> None:
    # Removing check_scope's lower() on either side makes this fail.
    upper = gate({"action_type": "READ_FILE", "target_resource": "notes.txt"}, scope()).gate_decision
    assert "G6_E_SCOPE_OUTSIDE" not in upper.reason_codes   # unprofiled, so its unknown risk still meets the ceiling
    mixed = gate(READ, scope(allowed_task_patterns=["Read_File"])).gate_decision
    assert mixed.decision == "allow"
    forbidden = gate(WRITE, scope(allowed_task_patterns=["*"], forbidden_actions=["WRITE_*"])).gate_decision
    assert forbidden.decision == "block" and "G6_E_SCOPE_FORBIDDEN_ACTION" in forbidden.reason_codes


# --- DOMAIN_CERTIFICATION-01 and -03: caller files, approvals and standards count for nothing ------------------------

@pytest.mark.parametrize("field", [{"certified": True}, {"allow_autonomous": True}, {"autonomous_allowed": True},
                                   {"reviewer_id": "rev-1"}, {"review_log_path": "review.log"},
                                   {"standards": ["ISO 9001"]}, {"gold_case_results_path": "gold.json"},
                                   {"metric_thresholds": {"overall": 0.1}}])
def test_p0_01_and_p1_03_a_scope_takes_no_claim_of_review_or_standards(field) -> None:
    # Removing ReviewScope's extra="forbid" makes this fail (the claim would be dropped silently, or kept).
    with pytest.raises(ValidationError):
        scope(**field)
    result = gate(READ, {**scope().model_dump(mode="json"), **field})
    assert result.gate_decision.decision == "block" and "G6_E_SCOPE_INVALID" in result.gate_decision.reason_codes


def test_g4_a_scope_that_cannot_be_read_blocks() -> None:
    # Removing check_scope's validation refusal makes this fail (an unreadable scope would be ignored).
    for broken in ({}, {"allowed_task_patterns": []}, {**scope().model_dump(mode="json"), "expires_at": "soon"},
                   {**scope().model_dump(mode="json"), "expires_at": "2030-01-01T00:00:00"}, "read_file"):
        assert "G6_E_SCOPE_INVALID" in gate(READ, broken).gate_decision.reason_codes, broken


def _grounded(review: ReviewScope, predicate: str, *, result: str = "pass", author: str = "harness-team",
              kind: str = "independent_executable", artifact_sha: str | None = None) -> tuple[Check, Receipt]:
    check = Check(f"c-{predicate}", (predicate,), author, kind, SHA, 1,
                  controls=(Control(f"k-{predicate}", SHA, predicate, "g6", 2, True),), positive_seq=2)
    return check, Receipt(f"r-{predicate}", check.check_id, SHA, artifact_sha or scope_digest(review), result, 5)


def _record(review: ReviewScope, *, authority: object | None = None, approval_id: str | None = None,
            observed: tuple[tuple[Check, Receipt], ...] | None = None, producer: str = "workflow-author",
            recorded: bool = True) -> dict:
    pairs = observed if observed is not None else tuple(_grounded(review, p) for p in REVIEW_PREDICATES)
    # EVIDENCE-01: the runner that observed the controls and receipts wrote them here. Removing review_record's
    # `ledger=ledger` makes the observed-and-approved tests fail (nothing would be observed).
    ledger = ChainLedger()
    if recorded:
        record(ledger, *(k for c, _ in pairs for k in c.controls), *(r for _, r in pairs))
    return review_record(review, artifact=Artifact(scope_digest(review), producer, 3),
                         checks=[c for c, _ in pairs], receipts=[r for _, r in pairs],
                         approval_id=approval_id, authority=authority or ApprovalAuthority(), ledger=ledger)


def test_p0_01_a_reviewer_name_or_a_made_up_approval_is_no_person_s_decision() -> None:
    # Removing review_record's ApprovalAuthority.check makes this fail (a fabricated id would count).
    review = scope()
    for approval_id in (None, "approved by rev-1", "rev-1"):
        record = _record(review, approval_id=approval_id)
        assert record["completion_state"] == "blocked-escalated" and record["person_decision"] is None
        assert record["warning_card"]["code"] == "G6_E_SCOPE_DECISION_MISSING"


def test_p0_01_an_approval_minted_by_a_policy_is_not_a_person_s() -> None:
    # Removing require_human=True from review_record's check makes this fail.
    review, authority = scope(), ApprovalAuthority()
    minted = authority.issue(reviewer_id="policy", scope=approval_scope(review), issuer_kind=ISSUER_POLICY_ORACLE,
                             policy_version="v1")
    assert _record(review, authority=authority, approval_id=minted.approval_id)["person_decision"] is None


def test_p0_01_receipts_that_are_not_grounded_are_not_observations() -> None:
    # Removing review_record's "nothing observed" refusal makes this fail (the archive passed artifact_sha "pending").
    review, authority = scope(), ApprovalAuthority()
    approval = authority.issue(reviewer_id="dr-reviewer", scope=approval_scope(review)).approval_id
    for observed in ((_grounded(review, "harness_runs", artifact_sha="0" * 64),),
                     (_grounded(review, "harness_runs", author="workflow-author"),),
                     (_grounded(review, "harness_runs", kind="process"),), ()):
        record = _record(review, authority=authority, approval_id=approval, observed=observed)
        assert record["completion_state"] == "blocked-escalated", observed
        assert record["warning_card"]["code"] == "G6_E_SCOPE_NOTHING_OBSERVED"


def test_p0_01_a_fully_observed_and_approved_scope_is_a_qualified_draft_that_grants_nothing() -> None:
    # Removing review_record's cap at qualified-draft makes this fail (g6.evidence's own state here is verified).
    review, authority = scope(), ApprovalAuthority()
    approval = authority.issue(reviewer_id="dr-reviewer", scope=approval_scope(review)).approval_id
    record = _record(review, authority=authority, approval_id=approval)
    assert record["completion_state"] == "qualified-draft" and check_envelope(record) == []
    assert record["evidence"][0]["evidence_state"] == "verified" and record["gaps"] == []
    assert record["person_decision"]["reviewer_id"] == "dr-reviewer" and record["grants"].startswith("nothing")
    text = json.dumps(record).lower()
    assert "certif" not in text and "allow_autonomous" not in text and approval not in json.dumps(record)


def test_evidence_01_receipts_typed_by_hand_are_not_observations() -> None:
    # Removing g6.evidence's `elif missing := _unresolved(c, p, r, resolves):` branch makes this fail (verified).
    review, authority = scope(), ApprovalAuthority()
    approval = authority.issue(reviewer_id="dr-reviewer", scope=approval_scope(review)).approval_id
    record_ = _record(review, authority=authority, approval_id=approval, recorded=False)
    assert record_["evidence"][0]["evidence_state"] == "qualified-draft"
    assert record_["completion_state"] == "blocked-escalated"
    assert record_["warning_card"]["code"] == "G6_E_SCOPE_NOTHING_OBSERVED"
    assert all("not_in_ledger" in why for _, why in record_["gaps"]) and len(record_["gaps"]) == 2
    # a receipt the runner recorded as failing, handed in as passing, is not the record it wrote
    pairs = tuple(_grounded(review, p) for p in REVIEW_PREDICATES)
    ledger = ChainLedger()
    record(ledger, *(k for c, _ in pairs for k in c.controls), *(replace(r, result="fail") for _, r in pairs))
    edited = review_record(review, artifact=Artifact(scope_digest(review), "workflow-author", 3),
                           checks=[c for c, _ in pairs], receipts=[r for _, r in pairs], approval_id=approval,
                           authority=authority, ledger=ledger)
    assert edited["evidence"][0]["evidence_state"] == "qualified-draft"
    assert edited["warning_card"]["code"] == "G6_E_SCOPE_NOTHING_OBSERVED"


def test_review3_m2_a_formal_check_without_a_digest_never_puts_certificate_in_the_record() -> None:
    # Removing review_record's renaming of g6.evidence's formal_without_certificate reason makes this fail.
    review, authority = scope(), ApprovalAuthority()
    approval = authority.issue(reviewer_id="dr-reviewer", scope=approval_scope(review)).approval_id
    formal = Check("c-formal", ("harness_runs",), "harness-team", "formal_checked", SHA, 1,
                   controls=(Control("k-formal", SHA, "harness_runs", "g6", 2, True),), positive_seq=2)
    observed = ((formal, Receipt("r-formal", "c-formal", SHA, scope_digest(review), "pass", 5)),
                _grounded(review, "held_out_results"))
    record = _record(review, authority=authority, approval_id=approval, observed=observed)
    assert any("formal_without_proof_digest" in why for _, why in record["gaps"])
    assert "certif" not in json.dumps(record).lower()


def test_a_refuted_predicate_or_an_expired_scope_blocks_the_record() -> None:
    # Removing review_record's expiry check makes the second assertion fail.
    review, authority = scope(), ApprovalAuthority()
    approval = authority.issue(reviewer_id="dr-reviewer", scope=approval_scope(review)).approval_id
    refuted = (_grounded(review, "harness_runs", result="fail"), _grounded(review, "held_out_results"))
    assert _record(review, authority=authority, approval_id=approval, observed=refuted)["warning_card"]["code"] == \
        "G6_E_SCOPE_EVIDENCE_REFUTED"
    old = scope(expires_at=EARLIER)
    stale = authority.issue(reviewer_id="dr-reviewer", scope=approval_scope(old)).approval_id
    assert _record(old, authority=authority, approval_id=stale)["warning_card"]["code"] == "G6_E_SCOPE_EXPIRED"


def test_g4_an_approval_store_that_fails_blocks_the_record() -> None:
    # Removing review_record's guard around the authority call makes this fail (the error escaped).
    class Broken:
        def check(self, *args, **kwargs):
            raise OSError("the approval database is unreadable")

    record = _record(scope(), authority=Broken(), approval_id="anything")
    assert record["completion_state"] == "blocked-escalated" and record["person_decision"] is None


def test_a_record_for_another_scope_s_bytes_is_refused() -> None:
    # Removing review_record's artifact digest comparison makes this fail.
    review = scope()
    record = review_record(review, artifact=Artifact("d" * 64, "workflow-author", 3), checks=[], receipts=[])
    assert record["warning_card"]["code"] == "G6_E_SCOPE_RECORD_REFUSED"


# --- over the block, and the declarations ----------------------------------------------------------------------------

def test_d6_the_block_takes_a_typed_scope_and_refuses_claims_in_it() -> None:
    reply = invoke_reply("action_gating", "evaluate", json.dumps({"action": WRITE,
                                                                  "scope": scope().model_dump(mode="json")}))
    assert not reply["ok"] and "G6_E_SCOPE_OUTSIDE" in reply["result"]["gate_decision"]["reason_codes"]
    claimed = {**scope().model_dump(mode="json"), "allow_autonomous": True}
    refused = get_registry().invoke("action_gating", "evaluate", {"action": READ, "scope": claimed})
    assert str(refused.failure()).startswith("[G6_E_INPUT_VALIDATION]")


def test_d2_the_failure_mode_is_declared_and_no_longer_planned() -> None:
    import g6.action_gating

    assert "beyond_review_scope" in g6.action_gating.__g6_mitigates__
    assert "g6.action_gating" not in FailureModeRegistry.planned() and PERSON_GATE == "person_review"
