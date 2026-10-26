# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Boundary hardening for the deterministic epistemic-integrity floor (K-P0-4 document STC).

The floor already has gate-grade coverage (test_adjudication.py + test_adjudication_
gate_grade.py, ~38 tests) — the "1 test" premise in older planning docs is stale. This
file closes the remaining BOUNDARY gaps that gate-grade review flagged and that the
document-STC oracle now leans on (it composes ``collapse_independence`` /
``detect_contested`` / the grade table): atomise edge inputs, the ``Claim`` confidence
bound, the numeric-context and causal-without-E2 boundaries, the abstention R-tier
boundary, model-assertion exclusion from independence, ``detect_contested`` mixed
stance, ``most_restrictive`` fail-safe on an unknown disposition, the ``risk_policy``
fail-closed contract, and to_dict serialization stability.

Additive test coverage only — it raises confidence, it does NOT change the component's
maturity gate (tier1_review_pending is a founder decision, unaffected here).
"""
from __future__ import annotations

import pytest
from g6.core.evidence import Claim, EvidenceRecord
from g6.core.risk_policy import gate_decision, grade_meets, minimum_grade_for
from g6.epistemic_integrity.adjudicator import (
    AdjudicationResult,
    adjudicate,
    adjudicate_claim,
    atomise,
    collapse_independence,
    detect_contested,
    grade_evidence,
    most_restrictive,
)


def _rec(**kw) -> EvidenceRecord:
    kw.setdefault("source_uri_or_artifact_id", "src")
    return EvidenceRecord(**kw)


def _grade(records):
    return grade_evidence(records, collapse_independence(records))


# ── atomise boundaries ───────────────────────────────────────────────────────


def test_atomise_empty_and_whitespace_are_no_claims():
    assert atomise("") == []
    assert atomise("   \t\n ") == []


def test_atomise_single_fragment_no_delimiter():
    assert atomise("just one clause") == ["just one clause"]


def test_atomise_strips_trailing_period():
    assert atomise("A single sentence.") == ["A single sentence"]


def test_atomise_splits_on_semicolon_and_but():
    assert atomise("A holds; B holds") == ["A holds", "B holds"]
    assert atomise("A holds but C fails") == ["A holds", "C fails"]


# ── Claim confidence bound (evidence.py __post_init__) ───────────────────────


def test_claim_confidence_out_of_range_raises():
    with pytest.raises(ValueError):
        Claim(text="x", confidence=1.5)
    with pytest.raises(ValueError):
        Claim(text="x", confidence=-0.1)


def test_claim_confidence_none_and_bounds_ok():
    assert Claim(text="x", confidence=None).confidence is None
    assert Claim(text="x", confidence=0.0).confidence == 0.0
    assert Claim(text="x", confidence=1.0).confidence == 1.0


# ── numeric-context boundary (EI-030 / AT-07) ────────────────────────────────


def test_numeric_without_denominator_triggers_ask():
    ac = adjudicate_claim(
        "Sales grew 40%", action_criticality="info",
        evidence_records=(_rec(independence_group="a", source_type="secondary"),),
    )
    assert any("AT-07" in t for t in ac.triggered)


def test_numeric_with_denominator_cue_does_not_trigger():
    ac = adjudicate_claim(
        "Sales grew 40% of the prior baseline", action_criticality="info",
        evidence_records=(_rec(independence_group="a", source_type="secondary"),),
    )
    assert not any("AT-07" in t for t in ac.triggered)


# ── causal-without-E2 boundary (EI-035 / AT-04) ──────────────────────────────


def test_causal_below_e2_is_labeled():
    # CAUSAL claim with only E1 evidence → correlation must not read as cause.
    ac = adjudicate_claim(
        "Revenue rose because we shipped the logo", action_criticality="info",
        evidence_records=(_rec(independence_group="a", source_type="secondary"),),
    )
    assert any("AT-04" in t for t in ac.triggered)
    assert ac.claim.disposition == "ALLOW_WITH_LABEL"


def test_causal_at_e2_is_not_labeled():
    # Same causal claim WITH E2 evidence (two independent lineages) → no AT-04.
    ac = adjudicate_claim(
        "Revenue rose because we shipped the logo", action_criticality="info",
        evidence_records=(
            _rec(independence_group="a", source_type="secondary"),
            _rec(independence_group="b", source_type="secondary"),
        ),
    )
    assert not any("AT-04" in t for t in ac.triggered)


# ── abstention R-tier boundary (EI-005 / AT-13) ──────────────────────────────


def test_abstention_for_e0_low_risk():
    # Insufficient evidence at low risk is a valid abstention (UNVERIFIABLE), never a
    # forced answer and never a BLOCK. (R1+E0 gates to ASK — the base gate, not the
    # abstention line, since abstention only upgrades a plain ALLOW.)
    ac = adjudicate_claim("The widget is quick", action_criticality="info")
    assert ac.claim.epistemic_status == "UNVERIFIABLE"
    assert ac.claim.disposition != "BLOCK"
    assert any("AT-13" in t for t in ac.triggered)


def test_no_abstention_at_r2_even_with_e0():
    # The abstention path is R0/R1 only; an R2 E0 claim does NOT become UNVERIFIABLE.
    ac = adjudicate_claim("The widget is quick", action_criticality="operational")
    assert ac.claim.risk_level == "R2"
    assert ac.claim.epistemic_status != "UNVERIFIABLE"


# ── grade_evidence: model assertions never add a lineage ─────────────────────


def test_model_assertion_excluded_from_independence():
    # A real lineage plus a separate all-model group grades E1, not E2 — model
    # assertions cannot manufacture a second independent lineage (INV-03).
    records = (
        _rec(independence_group="a", source_type="secondary"),
        _rec(independence_group="b", source_type="model_assertion"),
    )
    assert _grade(records) == "E1"


# ── detect_contested: mixed stance + model-opposition ignored ────────────────


def test_detect_contested_single_mixed_record():
    records = (_rec(independence_group="a", supports_or_opposes="mixed", source_type="secondary"),)
    assert detect_contested(records, collapse_independence(records)) is True


def test_detect_contested_ignores_model_opposition():
    records = (
        _rec(independence_group="a", supports_or_opposes="supports", source_type="secondary"),
        _rec(independence_group="b", supports_or_opposes="opposes", source_type="model_assertion"),
    )
    assert detect_contested(records, collapse_independence(records)) is False


# ── most_restrictive fail-safe on an unknown disposition ──────────────────────


def test_most_restrictive_unknown_ranks_as_allow():
    # An unrecognised disposition ranks 0 (ALLOW), so a real BLOCK still dominates —
    # an unknown token can never mask a restrictive one.
    assert most_restrictive(["WEIRD_VALUE", "BLOCK"]) == "BLOCK"
    assert most_restrictive(["WEIRD_VALUE"]) == "WEIRD_VALUE"


# ── risk_policy fail-closed contract ─────────────────────────────────────────


def test_gate_decision_unknown_inputs_block():
    assert gate_decision("R9", "E0") == "BLOCK"
    assert gate_decision("R0", "E9") == "BLOCK"


def test_grade_meets_and_minimum_grade_fail_closed():
    assert grade_meets("E9", "E1") is False
    assert minimum_grade_for("R9") == "E3"


# ── to_dict serialization stability ──────────────────────────────────────────


def test_adjudicated_claim_to_dict_shape():
    d = adjudicate_claim("The build passed", action_criticality="info").to_dict()
    assert d["text"] == "The build passed"
    assert isinstance(d["triggered"], list)
    assert "disposition" in d and "epistemic_status" in d


def test_adjudication_result_to_dict_shape():
    res = adjudicate("The sky is blue. Water is wet.")
    assert isinstance(res, AdjudicationResult)
    d = res.to_dict()
    assert set(d) == {"overall_disposition", "overall_status", "claims"}
    assert isinstance(d["claims"], list) and len(d["claims"]) == 2


# ── the gate matrix is code: no document and no caller can change it ─────────


def test_a_policy_document_does_not_change_the_gate(tmp_path) -> None:
    from g6.core import risk_policy

    loosened = tmp_path / "g6_epistemic_policy.yaml"
    loosened.write_text("g6_epistemic_integrity:\n  risk_policy:\n    R2: {minimum_evidence_grade: E0}\n",
                        encoding="utf-8")
    had_path = hasattr(risk_policy, "_POLICY_PATH")
    saved = getattr(risk_policy, "_POLICY_PATH", None)
    clear = getattr(risk_policy.load_epistemic_policy, "cache_clear", lambda: None)
    try:
        risk_policy._POLICY_PATH = loosened  # where the archive read its matrix from
        clear()
        assert minimum_grade_for("R2") == "E2"
        assert gate_decision("R2", "E0") == "ESCALATE"
    finally:
        if had_path:
            risk_policy._POLICY_PATH = saved
        else:
            del risk_policy._POLICY_PATH
        clear()


def test_editing_the_loaded_policy_does_not_change_the_gate() -> None:
    from g6.core.risk_policy import load_epistemic_policy

    loaded = load_epistemic_policy()
    r2 = loaded["g6_epistemic_integrity"]["risk_policy"]["R2"]
    saved = r2["minimum_evidence_grade"]
    try:
        r2["minimum_evidence_grade"] = "E0"
        assert minimum_grade_for("R2") == "E2" and gate_decision("R2", "E0") == "ESCALATE"
    finally:
        r2["minimum_evidence_grade"] = saved
