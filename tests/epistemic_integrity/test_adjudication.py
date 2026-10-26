# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Acceptance + property tests for the deterministic epistemic-integrity floor.

Each AT-xx maps to G6_Epistemic_Integrity_Spec.md section 14. These are the EMPIRICAL
verification leg for WS-A.1: the enforcement is real iff these pass.
"""
from __future__ import annotations

from g6.core import risk_policy
from g6.core.evidence import EvidenceRecord
from g6.epistemic_integrity.adjudicator import (
    _GRADE_CEILING,
    adjudicate,
    adjudicate_claim,
    grade_evidence,
    collapse_independence,
)


def _rec(**kw) -> EvidenceRecord:
    kw.setdefault("source_uri_or_artifact_id", "src")
    return EvidenceRecord(**kw)


# --------------------------------------------------------------------------- #
# Acceptance tests (spec section 14)
# --------------------------------------------------------------------------- #

def test_at01_unsupported_assertion_is_E0_and_not_allowed() -> None:
    res = adjudicate("Aspartame is definitely safe.", action_criticality="info")
    claim = res.claims[0].claim
    assert claim.evidence_grade == "E0"
    assert res.overall_disposition != "ALLOW"


def test_at03_copied_sources_collapse_to_one_lineage() -> None:
    records = tuple(_rec(independence_group="press_release_1", source_type="secondary") for _ in range(5))
    groups = collapse_independence(records)
    assert len([g for g in groups if g != "_ungrouped"]) == 1
    # five copies of one lineage do NOT reach multi-source corroboration (E2)
    assert grade_evidence(records, groups) == "E1"


def test_at04_correlation_as_cause_is_flagged() -> None:
    res = adjudicate("Revenue rose because we changed the logo.", action_criticality="info")
    claim = res.claims[0].claim
    assert claim.claim_type == "CAUSAL"
    assert any("AT-04" in t for t in res.claims[0].triggered)
    assert res.overall_disposition != "ALLOW"


def test_at05_r3_claim_by_model_only_is_blocked() -> None:
    records = (_rec(source_type="model_assertion"),)
    res = adjudicate("The reactor is safe to restart.", action_criticality="irreversible", evidence_records=records)
    assert res.overall_disposition == "BLOCK"


def test_at06_support_and_opposition_is_contested() -> None:
    records = (
        _rec(independence_group="a", supports_or_opposes="supports", source_type="primary"),
        _rec(independence_group="b", supports_or_opposes="opposes", source_type="primary"),
    )
    res = adjudicate("The drug reduces mortality.", action_criticality="info", evidence_records=records)
    assert res.overall_status == "CONTESTED"


def test_at07_percentage_without_denominator_asks() -> None:
    res = adjudicate("Mortality dropped 50%.", action_criticality="info")
    assert res.overall_disposition == "ASK" or any("AT-07" in t for c in res.claims for t in c.triggered)


def test_at10_same_model_repeated_is_not_independent() -> None:
    records = tuple(_rec(independence_group="modelX", source_type="model_assertion") for _ in range(10))
    groups = collapse_independence(records)
    # ten model queries from one lineage are E0 (model output is never corroboration)
    assert grade_evidence(records, groups) == "E0"


def test_at13_insufficient_evidence_is_valid_abstention() -> None:
    res = adjudicate("Widget X outsells Widget Y.", action_criticality="info")
    assert res.overall_status == "UNVERIFIABLE"
    assert res.overall_disposition != "BLOCK"  # abstention is allowed, not a hard block


# --------------------------------------------------------------------------- #
# Property tests (formal-leg seed: invariants over inputs)
# --------------------------------------------------------------------------- #

def test_property_confidence_never_exceeds_grade_ceiling() -> None:
    configs = [
        (),
        (_rec(source_type="model_assertion"),),
        (_rec(independence_group="a", source_type="secondary"),),
        (_rec(independence_group="a", source_type="primary"),),
        (_rec(independence_group="a", source_type="primary", authority="high"),
         _rec(independence_group="b", source_type="primary", authority="high")),
        (_rec(independence_group="a", supports_or_opposes="supports", source_type="primary"),
         _rec(independence_group="b", supports_or_opposes="opposes", source_type="primary")),
    ]
    for records in configs:
        ac = adjudicate_claim("Some factual claim about the world.", evidence_records=records)
        claim = ac.claim
        assert claim.confidence is not None
        assert claim.confidence <= _GRADE_CEILING[claim.evidence_grade] + 1e-9
        assert claim.confidence_ceiling_applied is True


def test_property_r3_below_e3_always_blocks() -> None:
    for grade in ("E0", "E1", "E2"):
        # no human approval / rollback -> R3 must fail closed
        assert risk_policy.gate_decision("R3", grade) == "BLOCK"
    # even at E3, R3 requires human approval AND rollback
    assert risk_policy.gate_decision("R3", "E3") == "BLOCK"
    assert risk_policy.gate_decision("R3", "E3", has_human_approval=True, has_rollback=True) != "BLOCK"


def test_block_adjudicate_op() -> None:
    from g6.epistemic_integrity.epistemic_integrity_block import EpistemicIntegrityBlock

    block = EpistemicIntegrityBlock()
    res = block.infer(_mk_input())
    assert res.is_ok()
    payload = res.value
    assert "overall_disposition" in payload
    assert payload["llm_used"] is False


def _mk_input():
    from g6.epistemic_integrity.epistemic_integrity_block import EpistemicIntegrityInput

    return EpistemicIntegrityInput(message="The sky is green.", action_criticality="info")


def test_block_lists_its_own_ops_and_help_like_every_other_block() -> None:
    from g6.epistemic_integrity.epistemic_integrity_block import EpistemicIntegrityBlock, EpistemicIntegrityInput

    block = EpistemicIntegrityBlock()
    ops = block.infer(EpistemicIntegrityInput(op="ops")).unwrap()
    helped = block.infer(EpistemicIntegrityInput(op="help")).unwrap()
    assert ops == {"component": "epistemic_integrity", "op": "ops", "ops": ["adjudicate"]}
    assert helped["op"] == "help" and [line.split("(")[0] for line in helped["help"]] == ["adjudicate"]
