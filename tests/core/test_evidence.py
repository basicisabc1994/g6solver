# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from g6.core.evidence import Assumption, Claim, EvidenceBundle, SourceRef, TestResult


def test_evidence_bundle_serialises_portable_fields() -> None:
    bundle = EvidenceBundle(
        claims=(Claim(text="claim", checked=True),),
        tests_run=(TestResult(name="unit", passed=True, command="pytest"),),
        sources=(SourceRef(uri="file://example"),),
        assumptions=(Assumption(text="assumption"),),
        confidence=0.75,
        verifier_id="test",
    )

    data = bundle.to_dict()

    assert data["schema_version"] == "1.0"
    assert data["claims"][0]["text"] == "claim"
    assert data["tests_run"][0]["passed"] is True
    assert data["confidence"] == 0.75


def test_evidence_bundle_rejects_invalid_confidence() -> None:
    try:
        EvidenceBundle(confidence=1.5)
    except ValueError as exc:
        assert "confidence" in str(exc)
    else:
        raise AssertionError("invalid confidence should fail")
