# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from datetime import datetime, timezone

from g6.core.evidence import Claim, EvidenceBundle, EvidenceRecord


def test_claim_and_bundle_old_keyword_construction_still_work() -> None:
    claim = Claim(
        text="old shape",
        claim_type="inference",
        evidence_refs=("src-1",),
        checked=True,
    )
    bundle = EvidenceBundle(claims=(claim,), verifier_id="unit")

    assert claim.text == "old shape"
    assert claim.claim_type == "inference"
    assert claim.evidence_refs == ("src-1",)
    assert claim.checked is True
    assert bundle.claims == (claim,)


def test_claim_new_fields_default_to_conservative_epistemic_posture() -> None:
    claim = Claim(text="new defaults")

    assert claim.risk_level == "R0"
    assert claim.evidence_grade == "E0"
    assert claim.epistemic_status == "ASSERTED"
    assert claim.scope == {}
    assert claim.counterevidence_refs == ()
    assert claim.confidence is None


def test_evidence_bundle_to_dict_is_json_serializable_with_new_records() -> None:
    as_of = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    retrieved_at = datetime(2026, 1, 3, 3, 4, tzinfo=timezone.utc)
    claim = Claim(
        text="claim",
        evidence_refs=("evidence-1",),
        risk_level="R2",
        evidence_grade="E2",
        scope={"jurisdiction": "AU"},
        as_of=as_of,
    )
    record = EvidenceRecord(
        source_uri_or_artifact_id="file://artifact",
        source_type="primary",
        supports_or_opposes="supports",
        retrieved_at=retrieved_at,
        conflicts_of_interest=("none declared",),
        limitations=("limited sample",),
        claim_id="claim-1",
        evidence_id="evidence-1",
    )
    bundle = EvidenceBundle(claims=(claim,), evidence_records=(record,))

    data = bundle.to_dict()

    assert data["claims"][0]["as_of"] == as_of.isoformat()
    assert data["claims"][0]["evidence_refs"] == ["evidence-1"]
    assert data["claims"][0]["scope"] == {"jurisdiction": "AU"}
    assert data["evidence_records"][0]["retrieved_at"] == retrieved_at.isoformat()
    assert data["evidence_records"][0]["conflicts_of_interest"] == ["none declared"]
    json.dumps(data)


def test_evidence_record_to_dict_round_trips_json_safe_fields() -> None:
    published = datetime(2026, 2, 1, tzinfo=timezone.utc)
    record = EvidenceRecord(
        source_uri_or_artifact_id="https://example.test/source",
        publication_or_effective_date=published,
        limitations=("scope",),
        raw_data_available=True,
        reproducible=False,
    )

    data = record.to_dict()

    assert data["source_uri_or_artifact_id"] == "https://example.test/source"
    assert data["publication_or_effective_date"] == published.isoformat()
    assert data["limitations"] == ["scope"]
    assert data["raw_data_available"] is True
    assert data["reproducible"] is False
    json.dumps(data)


def test_claim_rejects_invalid_confidence() -> None:
    try:
        Claim(text="bad confidence", confidence=1.01)
    except ValueError as exc:
        assert "confidence" in str(exc)
    else:
        raise AssertionError("invalid confidence should fail")

def test_claim_field_order_appends_new_contract_fields() -> None:
    from dataclasses import fields

    names = [field.name for field in fields(Claim)]

    assert names[:4] == ["text", "claim_type", "evidence_refs", "checked"]
    assert names[4:] == [
        "risk_level",
        "evidence_grade",
        "epistemic_status",
        "scope",
        "counterevidence_refs",
        "falsification_conditions",
        "independence_group",
        "confidence",
        "confidence_ceiling_applied",
        "disposition",
        "as_of",
        "expires_at",
    ]


def test_claim_to_dict_serializes_all_new_contract_fields() -> None:
    as_of = datetime(2026, 3, 1, tzinfo=timezone.utc)
    expires_at = datetime(2026, 4, 1, tzinfo=timezone.utc)
    claim = Claim(
        text="contract claim",
        risk_level="R3",
        evidence_grade="E3",
        epistemic_status="SUPPORTED",
        scope={"system": "core"},
        counterevidence_refs=("counter-1",),
        falsification_conditions=("fails replication",),
        independence_group="group-a",
        confidence=0.5,
        confidence_ceiling_applied=True,
        disposition="ALLOW",
        as_of=as_of,
        expires_at=expires_at,
    )

    data = claim.to_dict()

    assert data["risk_level"] == "R3"
    assert data["evidence_grade"] == "E3"
    assert data["epistemic_status"] == "SUPPORTED"
    assert data["scope"] == {"system": "core"}
    assert data["counterevidence_refs"] == ["counter-1"]
    assert data["falsification_conditions"] == ["fails replication"]
    assert data["independence_group"] == "group-a"
    assert data["confidence"] == 0.5
    assert data["confidence_ceiling_applied"] is True
    assert data["disposition"] == "ALLOW"
    assert data["as_of"] == as_of.isoformat()
    assert data["expires_at"] == expires_at.isoformat()
    json.dumps(data)