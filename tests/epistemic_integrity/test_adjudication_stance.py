# SPDX-License-Identifier: Apache-2.0
"""Stance: evidence that only opposes a claim never reads as support (spec section 10.9, EI-090).

The grade counts supporting (``supports`` or ``mixed``) non-model lineages only. Credible opposition with no support
makes the claim ``REFUTED``, ``BLOCK`` at every tier; support and opposition together stay ``CONTESTED``.
"""
from __future__ import annotations

import pytest

from g6.core.evidence import EvidenceRecord
from g6.epistemic_integrity import adjudicator
from g6.epistemic_integrity.adjudicator import adjudicate, collapse_independence, grade_evidence


def _rec(**kw) -> EvidenceRecord:
    kw.setdefault("source_uri_or_artifact_id", "src")
    return EvidenceRecord(**kw)


def _grade(records) -> str:
    return grade_evidence(records, collapse_independence(records))


_OPPOSING = (
    _rec(independence_group="a", source_type="primary", supports_or_opposes="opposes", authority="high"),
    _rec(independence_group="b", source_type="primary", supports_or_opposes="opposes", authority="high"),
)


def test_opposing_evidence_alone_grades_e0() -> None:
    assert _grade(_OPPOSING) == "E0"
    assert _grade(_OPPOSING[:1]) == "E0"


def test_two_opposing_primary_sources_with_approval_and_rollback_refute_a_health_claim() -> None:
    res = adjudicate("The drug cures the infection in adults.", action_criticality="health",
                     evidence_records=_OPPOSING, has_human_approval=True, has_rollback=True)
    claim = res.claims[0].claim
    assert (claim.evidence_grade, claim.epistemic_status, claim.disposition) == ("E0", "REFUTED", "BLOCK")
    assert claim.confidence == 0.2
    assert (res.overall_status, res.overall_disposition) == ("REFUTED", "BLOCK")
    assert any("refuted" in t for t in res.claims[0].triggered)


@pytest.mark.parametrize("criticality", ["creative", "info", "operational", "irreversible"])
def test_a_refuted_claim_is_blocked_at_every_tier(criticality: str) -> None:
    res = adjudicate("The office opens at nine.", action_criticality=criticality, evidence_records=_OPPOSING[:1])
    assert (res.overall_status, res.overall_disposition) == ("REFUTED", "BLOCK")


def test_opposition_never_raises_the_grade_of_supported_evidence() -> None:
    support = (_rec(independence_group="a", source_type="secondary"),)
    both = support + (_rec(independence_group="b", source_type="primary", supports_or_opposes="opposes",
                           authority="high"),)
    assert _grade(support) == _grade(both) == "E1"


def test_support_and_opposition_together_stay_contested() -> None:
    records = (_rec(independence_group="a", source_type="primary"),
               _rec(independence_group="b", source_type="primary", supports_or_opposes="opposes"))
    res = adjudicate("The drug reduces mortality.", action_criticality="info", evidence_records=records)
    assert res.overall_status == "CONTESTED" and not adjudicator.detect_refuted(records)
    assert res.claims[0].claim.evidence_grade == "E2"  # the one supporting primary lineage; opposition adds nothing


def test_context_only_and_model_opposition_neither_support_nor_refute() -> None:
    context = (_rec(independence_group="a", source_type="primary", supports_or_opposes="context_only"),)
    model = (_rec(independence_group="m", source_type="model_assertion", supports_or_opposes="opposes"),)
    for records in (context, model):
        assert _grade(records) == "E0" and not adjudicator.detect_refuted(records)
        res = adjudicate("The widget is quick", action_criticality="info", evidence_records=records)
        assert res.overall_status == "UNVERIFIABLE" and res.overall_disposition != "ALLOW"
