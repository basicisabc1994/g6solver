# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""W4 adjudication certificate axis — dark-landed, gate flag default-OFF.

Revert-fail tests for ``mvp.epistemic_integrity.adjudicator.certificate_grade``
and its inert wiring into ``adjudicate_claim``. The certificate axis is derived
from each supporting, non-model record's declared ``verification_method`` and is
``None`` whenever no record declares one — keeping adjudication byte-identical to
baseline today. When a certificate IS present it can only ever floor the
disposition DOWN (never lift it), gated behind ``G6_RISK_POLICY_GRADE_FLOOR``.

Method-name choices (verified against ``mvp.core.evidence_grade._METHOD_CEILING``):

* ``formal_methods_verification`` — ceiling ``model`` → SUB-KERNEL (grades to
  ``model`` regardless of artifact).
* ``executed_test`` — ceiling ``kernel`` → grades ``kernel`` with a resolvable
  artifact (non-empty ``integrity_hash``).
"""
from __future__ import annotations

from g6.core.evidence import EvidenceRecord
from g6.epistemic_integrity.adjudicator import adjudicate_claim, certificate_grade

_SUB_KERNEL = "formal_methods_verification"
_KERNEL = "executed_test"

# Most-restrictive ordering, mirrors adjudicator._DISPOSITION_RANK.
_RANK = {"ALLOW": 0, "ALLOW_WITH_LABEL": 1, "ASK": 2, "ESCALATE": 3, "BLOCK": 4}

# A plain empirical claim: no vague evaluative, no percentage, no causal marker,
# so its disposition is driven purely by the risk x (effective) evidence gate.
_CLAIM = "The database contains user profiles"


# ─────────────────────────────────────────────────────────────────────────────
# certificate_grade aggregation
# ─────────────────────────────────────────────────────────────────────────────
def test_certificate_grade_none_when_empty_or_no_method():
    assert certificate_grade(()) is None
    recs = (
        EvidenceRecord(source_uri_or_artifact_id="a", source_type="primary", supports_or_opposes="supports"),
        EvidenceRecord(source_uri_or_artifact_id="b", source_type="secondary", supports_or_opposes="supports"),
    )
    assert certificate_grade(recs) is None


def test_certificate_grade_subkernel_empty_hash_is_model():
    rec = EvidenceRecord(
        source_uri_or_artifact_id="a",
        source_type="primary",
        supports_or_opposes="supports",
        verification_method=_SUB_KERNEL,
        integrity_hash="",
    )
    assert certificate_grade((rec,)) == "model"


def test_certificate_grade_kernel_with_artifact_hash():
    rec = EvidenceRecord(
        source_uri_or_artifact_id="a",
        source_type="primary",
        supports_or_opposes="supports",
        verification_method=_KERNEL,
        integrity_hash="deadbeef",
    )
    assert certificate_grade((rec,)) == "kernel"


def test_certificate_grade_excludes_model_assertion_and_opposes():
    model_rec = EvidenceRecord(
        source_uri_or_artifact_id="m",
        source_type="model_assertion",
        supports_or_opposes="supports",
        verification_method=_KERNEL,
        integrity_hash="h",
    )
    opp_rec = EvidenceRecord(
        source_uri_or_artifact_id="o",
        source_type="primary",
        supports_or_opposes="opposes",
        verification_method=_KERNEL,
        integrity_hash="h",
    )
    # Both records carry a kernel method+artifact, yet both are excluded → None.
    assert certificate_grade((model_rec, opp_rec)) is None
    # Non-vacuity: the same method on a qualifying record DOES grade kernel, so
    # the None above is the exclusion filters, not an inability to grade.
    good = EvidenceRecord(
        source_uri_or_artifact_id="g",
        source_type="primary",
        supports_or_opposes="supports",
        verification_method=_KERNEL,
        integrity_hash="h",
    )
    assert certificate_grade((good,)) == "kernel"


# ─────────────────────────────────────────────────────────────────────────────
# End-to-end through adjudicate_claim
# ─────────────────────────────────────────────────────────────────────────────
def test_adjudicate_inert_without_verification_method(monkeypatch):
    """Records with no verification_method → certificate_grade None → the gate is
    byte-identical whether the flag is OFF or ON (the landmine guard)."""
    recs = (
        EvidenceRecord(source_uri_or_artifact_id="s", source_type="primary", supports_or_opposes="supports"),
    )
    monkeypatch.delenv("G6_RISK_POLICY_GRADE_FLOOR", raising=False)
    off = adjudicate_claim(_CLAIM, evidence_records=recs).claim.disposition
    monkeypatch.setenv("G6_RISK_POLICY_GRADE_FLOOR", "1")
    on = adjudicate_claim(_CLAIM, evidence_records=recs).claim.disposition
    assert off == on
    assert off == "ALLOW"


def test_adjudicate_subkernel_certificate_is_down_only(monkeypatch):
    """A sub-kernel certificate can only tighten the disposition, never loosen it.

    Same primary supporting record (independence grade E2 → ALLOW at R1), but now
    it declares a sub-kernel method. Flag ON floors the effective grade to E0,
    tightening ALLOW→ASK; it never becomes LESS restrictive.
    """
    recs = (
        EvidenceRecord(
            source_uri_or_artifact_id="s",
            source_type="primary",
            supports_or_opposes="supports",
            verification_method=_SUB_KERNEL,
        ),
    )
    monkeypatch.delenv("G6_RISK_POLICY_GRADE_FLOOR", raising=False)
    off = adjudicate_claim(_CLAIM, evidence_records=recs).claim.disposition
    monkeypatch.setenv("G6_RISK_POLICY_GRADE_FLOOR", "1")
    on = adjudicate_claim(_CLAIM, evidence_records=recs).claim.disposition
    assert _RANK[on] >= _RANK[off]  # down-only: never looser
    assert off == "ALLOW"
    assert on == "ASK"
