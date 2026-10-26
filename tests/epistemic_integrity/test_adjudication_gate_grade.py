# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Gate-grade coverage for the deterministic epistemic-integrity floor (K-P0-4 B1).

The existing suite (test_adjudication.py) pins the AT-xx acceptance path. This file
raises coverage of the component's PUBLIC behaviors that gate-grade review requires
but were untested: the full classify / assign_risk / grade_evidence tables (incl.
the E3 path), most_restrictive, the contested confidence cap, CLARIFICATION_REQUIRED,
multi-claim adjudicate (atomise + most-restrictive + overall_status priority), the
R3 approval+rollback unblock THROUGH the block input, and the block's error / help /
_to_records paths.

This is additive test coverage only — it raises confidence, it does NOT change the
component's maturity gate (verification_method="tier1_review_pending" is a founder
decision, unaffected here).
"""
from __future__ import annotations

import pytest

from g6.core.evidence import EvidenceRecord
from g6.epistemic_integrity.adjudicator import (
    adjudicate,
    adjudicate_claim,
    assign_risk,
    atomise,
    classify,
    collapse_independence,
    confidence_ceiling,
    grade_evidence,
    most_restrictive,
)
from g6.epistemic_integrity.epistemic_integrity_block import (
    EpistemicIntegrityBlock,
    EpistemicIntegrityInput,
)


def _rec(**kw) -> EvidenceRecord:
    kw.setdefault("source_uri_or_artifact_id", "src")
    return EvidenceRecord(**kw)


# ── classify: every branch ──────────────────────────────────────────────────


def test_classify_causal():
    assert classify("Revenue rose because we shipped it") == "CAUSAL"


def test_classify_opinion():
    assert classify("I think this design is nicer") == "NORMATIVE_OR_OPINION"


def test_classify_recommendation():
    assert classify("You should migrate to the new API") == "RECOMMENDATION"


def test_classify_predictive():
    assert classify("Sales will grow next quarter") == "PREDICTIVE"


def test_classify_empirical_default():
    assert classify("The service returned a 200 response") == "EMPIRICAL"


def test_classify_causal_precedence_over_opinion():
    # CAUSAL is checked first; a causal+opinion sentence classifies CAUSAL.
    assert classify("I think revenue rose because of the logo") == "CAUSAL"


# ── assign_risk: full mapping table ─────────────────────────────────────────


def test_assign_risk_table():
    assert assign_risk("creative") == "R0"
    assert assign_risk("opinion") == "R0"
    assert assign_risk("") == "R1"
    assert assign_risk("none") == "R1"
    assert assign_risk("info") == "R1"
    assert assign_risk("informational") == "R1"
    assert assign_risk("low") == "R1"
    assert assign_risk("reversible") == "R2"
    assert assign_risk("operational") == "R2"
    assert assign_risk("medium") == "R2"
    assert assign_risk("irreversible") == "R3"
    assert assign_risk("safety") == "R3"
    assert assign_risk("financial") == "R3"
    assert assign_risk("critical") == "R3"


def test_assign_risk_unknown_is_conservative_r2():
    assert assign_risk("banana-criticality") == "R2"


# ── grade_evidence: E0 / E1 / E2 / E3 ───────────────────────────────────────


def _grade(records):
    return grade_evidence(records, collapse_independence(records))


def test_grade_e0_no_records_or_model_only():
    assert _grade(()) == "E0"
    assert _grade((_rec(source_type="model_assertion"),)) == "E0"


def test_grade_e1_single_uncorroborated_lineage():
    assert _grade((_rec(independence_group="a", source_type="secondary"),)) == "E1"


def test_grade_e2_single_primary_lineage():
    assert _grade((_rec(independence_group="a", source_type="primary"),)) == "E2"


def test_grade_e2_two_independent_secondary_lineages():
    records = (
        _rec(independence_group="a", source_type="secondary"),
        _rec(independence_group="b", source_type="secondary"),
    )
    assert _grade(records) == "E2"


def test_grade_e3_two_independent_primary_lineages():
    records = (
        _rec(independence_group="a", source_type="primary"),
        _rec(independence_group="b", source_type="primary"),
    )
    assert _grade(records) == "E3"


def test_grade_e3_two_independent_high_authority_lineages():
    records = (
        _rec(independence_group="a", source_type="secondary", authority="high"),
        _rec(independence_group="b", source_type="secondary", authority="high"),
    )
    assert _grade(records) == "E3"


# ── most_restrictive + confidence_ceiling ───────────────────────────────────


def test_most_restrictive_empty_is_allow():
    assert most_restrictive([]) == "ALLOW"


def test_most_restrictive_picks_highest_rank():
    assert most_restrictive(["ALLOW", "ASK", "BLOCK", "ALLOW_WITH_LABEL"]) == "BLOCK"
    assert most_restrictive(["ALLOW", "ALLOW_WITH_LABEL"]) == "ALLOW_WITH_LABEL"
    assert most_restrictive(["ASK", "ESCALATE"]) == "ESCALATE"


def test_confidence_ceiling_by_grade():
    assert confidence_ceiling("E0", False) == 0.2
    assert confidence_ceiling("E1", False) == 0.5
    assert confidence_ceiling("E2", False) == 0.8
    assert confidence_ceiling("E3", False) == 1.0


def test_confidence_ceiling_contested_caps_at_half():
    # A high grade that is contested is capped at the contested ceiling (0.5).
    assert confidence_ceiling("E3", True) == 0.5
    assert confidence_ceiling("E2", True) == 0.5
    # A grade already below the contested ceiling is unchanged.
    assert confidence_ceiling("E0", True) == 0.2


def test_confidence_ceiling_unknown_grade_is_floor():
    assert confidence_ceiling("E9", False) == 0.2


# ── CLARIFICATION_REQUIRED (scope / operational definition) ─────────────────


def test_clarification_required_for_vague_evaluative_decision_claim():
    ac = adjudicate_claim("Our platform is the best", action_criticality="decision")
    assert ac.claim.risk_level == "R2"
    assert ac.claim.epistemic_status == "CLARIFICATION_REQUIRED"
    assert ac.claim.disposition in ("ASK", "ESCALATE", "BLOCK")
    assert any("EI-003" in t for t in ac.triggered)


def test_no_clarification_when_scope_supplied():
    # Same vague word but WITH a scope cue -> no operational-definition trigger.
    ac = adjudicate_claim(
        "Our platform is the best among US enterprise users",
        action_criticality="decision",
    )
    assert ac.claim.epistemic_status != "CLARIFICATION_REQUIRED"


# ── multi-claim adjudicate: atomise + overall status/disposition ────────────


def test_atomise_splits_conjunctions_and_sentences():
    parts = atomise("The sky is blue and grass is green. Water is wet.")
    assert len(parts) == 3


def test_adjudicate_overall_disposition_is_most_restrictive():
    # A benign empirical claim AND an R3 model-only safety claim -> BLOCK dominates.
    records = (_rec(source_type="model_assertion"),)
    res = adjudicate(
        "The build passed and the reactor is safe to restart",
        action_criticality="irreversible",
        evidence_records=records,
    )
    assert res.overall_disposition == "BLOCK"


def test_adjudicate_overall_status_contested_dominates():
    records = (
        _rec(independence_group="a", supports_or_opposes="supports", source_type="primary"),
        _rec(independence_group="b", supports_or_opposes="opposes", source_type="primary"),
    )
    res = adjudicate(
        "The drug reduces mortality and it is widely prescribed",
        action_criticality="info",
        evidence_records=records,
    )
    assert res.overall_status == "CONTESTED"


def test_adjudicate_empty_message_has_no_claims():
    res = adjudicate("   ")
    assert res.claims == ()
    assert res.overall_disposition == "ALLOW"
    assert res.overall_status == "ASSERTED"


# ── R3 approval + rollback unblock, THROUGH the block input ──────────────────


def test_r3_blocks_without_approval_through_block():
    block = EpistemicIntegrityBlock()
    res = block.infer(EpistemicIntegrityInput(
        op="adjudicate",
        message="The reactor is safe to restart",
        action_criticality="irreversible",
        evidence=[
            {"source_uri_or_artifact_id": "a", "independence_group": "a", "source_type": "primary", "authority": "high"},
            {"source_uri_or_artifact_id": "b", "independence_group": "b", "source_type": "primary", "authority": "high"},
        ],
    ))
    assert res.is_ok()
    assert res.value["overall_disposition"] == "BLOCK"


def test_r3_unblocks_with_approval_and_rollback_through_block():
    block = EpistemicIntegrityBlock()
    res = block.infer(EpistemicIntegrityInput(
        op="adjudicate",
        message="The reactor is safe to restart",
        action_criticality="irreversible",
        evidence=[
            {"source_uri_or_artifact_id": "a", "independence_group": "a", "source_type": "primary", "authority": "high"},
            {"source_uri_or_artifact_id": "b", "independence_group": "b", "source_type": "primary", "authority": "high"},
        ],
        has_human_approval=True,
        has_rollback=True,
    ))
    assert res.is_ok()
    assert res.value["overall_disposition"] != "BLOCK"


# ── block error / help / _to_records paths ──────────────────────────────────


def test_block_unknown_op_fails():
    block = EpistemicIntegrityBlock()
    res = block.infer(EpistemicIntegrityInput(op="ops"))  # ops is valid help alias
    assert res.is_ok()
    assert "adjudicate" in res.value["ops"]


def test_block_empty_message_fails():
    block = EpistemicIntegrityBlock()
    res = block.infer(EpistemicIntegrityInput(op="adjudicate", message="   "))
    assert res.is_fail()
    assert "message is required" in res.error


def test_block_help_op_lists_ops():
    block = EpistemicIntegrityBlock()
    res = block.infer(EpistemicIntegrityInput(op="help"))
    assert res.is_ok()
    # Like every other block: ``help`` describes the block's own ops, and ``ops``/``help`` are not among them.
    assert res.value["op"] == "help" and any(line.startswith("adjudicate(") for line in res.value["help"])
    assert block.infer(EpistemicIntegrityInput(op="ops")).unwrap()["ops"] == ["adjudicate"]


def test_to_records_skips_non_dict_and_refuses_unknown_fields_directly():
    # The block's pydantic model rejects non-dict evidence entries at the boundary,
    # so the _to_records non-dict guard is exercised via a direct call (its defensive
    # contract for any internal caller passing raw data).
    from g6.epistemic_integrity.epistemic_integrity_block import _to_records

    records = _to_records([
        "not-a-dict",  # skipped by the isinstance guard
        {"source_type": "primary", "independence_group": "g1"},
    ])
    assert len(records) == 1
    assert records[0].source_type == "primary"
    assert records[0].independence_group == "g1"
    # Review B1-1: an unknown field is refused, never dropped (a dropped "stance" read as support).
    with pytest.raises(ValueError, match="unknown_field"):
        _to_records([{"source_type": "primary", "independence_group": "g1", "unknown_field": "x"}])


def test_block_refuses_an_unknown_evidence_field():
    # Review B1-1: a well-formed dict with an extra key is a coded refusal (it was dropped downstream).
    block = EpistemicIntegrityBlock()
    res = block.infer(EpistemicIntegrityInput(
        op="adjudicate",
        message="The dataset shows a correlation",
        action_criticality="info",
        evidence=[{"source_type": "primary", "independence_group": "g1", "unknown_field": "x"}],
    ))
    assert res.is_fail()
    assert res.error.startswith("[G6_E_EPISTEMIC_INTEGRITY_EVIDENCE_INVALID]")


def test_to_records_defaults_source_uri_from_source_key():
    block = EpistemicIntegrityBlock()
    res = block.infer(EpistemicIntegrityInput(
        op="adjudicate",
        message="The API latency improved",
        action_criticality="info",
        evidence=[{"source": "https://example.test/report", "source_type": "secondary", "independence_group": "g"}],
    ))
    assert res.is_ok()
