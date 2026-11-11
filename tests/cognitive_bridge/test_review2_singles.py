# SPDX-License-Identifier: Apache-2.0
"""Review 2, findings 8 and 12. The conflict gate reads ``adjudicate_claim``'s whole result: a BLOCK or ASK
disposition, or a failure it triggered, refuses whatever its status says (failure_read_as_success). A risk is an exact
tier, an ``int`` that is not a ``bool`` from 0 to 3, wherever a standard or a context is made (boundary_case)."""
from __future__ import annotations

from dataclasses import replace

import pytest

from g6.cognitive_bridge import CognitiveBridgeBlock, CrossMethodConflictGate, ProblemSignature, ScopedAssertion
from g6.core.evidence import EvidenceRecord
from g6.epistemic_integrity import adjudicator


def _reconcile(proposition: str, source: str = "measurement:on"):
    assertion = ScopedAssertion("c", proposition, "a" * 64, "supports", "formal", source, "receipt")
    return CrossMethodConflictGate(admit_assertion=lambda row: row == assertion).reconcile([assertion])


def test_finding_8_adjudicator_failed_check_is_not_successful_reconciliation(monkeypatch):
    def broken_markers(text):
        raise RuntimeError("lexical check unavailable")

    monkeypatch.setattr(adjudicator, "markers", broken_markers)
    source = "measurement:on"
    canonical = adjudicator.adjudicate_claim("switch-on", evidence_records=(
        EvidenceRecord(source, supports_or_opposes="supports", independence_group=source, claim_id="c"),))
    assert canonical.claim.disposition == "BLOCK"
    assert "G6_E_MARKER_CHECK_FAILED" in canonical.triggered
    result = _reconcile("switch-on", source)
    assert result["completion_state"] == "blocked-escalated", (
        f"adjudicator BLOCK was read as {result['scoped_findings'][0]['status']}")


def test_an_ask_disposition_with_a_triggered_failure_refuses_an_asserted_status():
    canonical = adjudicator.adjudicate_claim("p is certainly true", evidence_records=(
        EvidenceRecord("e1", supports_or_opposes="supports", independence_group="e1", claim_id="c"),))
    assert canonical.claim.epistemic_status == "ASSERTED" and canonical.claim.disposition == "ASK"
    assert "G6_E_CERTAINTY_WEAK_EVIDENCE" in canonical.triggered
    finding = _reconcile("p is certainly true", "e1")["scoped_findings"][0]
    assert finding["completion_state"] == "blocked-escalated" and finding["status"] == "not_reconciled"
    assert finding["disposition"] == "ASK" and "G6_E_CERTAINTY_WEAK_EVIDENCE" in finding["triggered"]


@pytest.mark.parametrize("disposition", ["BLOCK", "ASK", "NEVER_SEEN"])
def test_any_disposition_but_allow_refuses_a_passing_status(monkeypatch, disposition):
    real = adjudicator.adjudicate_claim

    def held(*args, **kwargs):
        result = real(*args, **kwargs)
        return replace(result, claim=replace(result.claim, disposition=disposition))

    monkeypatch.setattr(adjudicator, "adjudicate_claim", held)
    out = _reconcile("switch-on")
    assert out["completion_state"] == "blocked-escalated"
    assert out["scoped_findings"][0]["epistemic_status"] == "ASSERTED"


def test_a_passing_result_with_a_mapped_status_still_counts():
    out = _reconcile("switch-on")
    assert out["completion_state"] == "qualified-draft"
    assert out["scoped_findings"][0]["status"] == "consistent_with_admitted_evidence"
    assert out["scoped_findings"][0]["disposition"] == "ALLOW"


@pytest.mark.parametrize("kind,risk", [("standard", 1.5), ("standard", True),
                                       ("context", 1.0), ("context", True)])
def test_finding_12_risk_must_be_an_integer_canonical_tier(executed_case, kind, risk):
    try:
        value = (replace(executed_case["standard"], risk=risk) if kind == "standard"
                 else ProblemSignature("review", "logic", "validity", risk, 2))
    except (TypeError, ValueError):
        return
    assert value.risk_tier in {"R0", "R1", "R2", "R3"}, (
        f"{kind} accepted risk={risk!r} and emitted {value.risk_tier}")


@pytest.mark.parametrize("risk", [-1, 4, 2.0, False, "2", None])
def test_every_value_but_an_exact_tier_is_refused_where_a_standard_or_context_is_made(executed_case, risk):
    with pytest.raises(ValueError):
        replace(executed_case["standard"], risk=risk)
    with pytest.raises(ValueError):
        ProblemSignature("review", "logic", "validity", risk, 2)


@pytest.mark.parametrize("risk", [0, 1, 2, 3])
def test_each_exact_tier_is_taken(executed_case, risk):
    assert replace(executed_case["standard"], risk=risk).risk_tier == f"R{risk}"
    assert ProblemSignature("review", "logic", "validity", risk, 2).risk_tier == f"R{risk}"


@pytest.mark.parametrize("risk,state", [(True, "blocked-escalated"), (1.0, "blocked-escalated"),
                                        ("1", "blocked-escalated"), (1, "qualified-draft")])
def test_the_blocks_input_takes_only_an_integer_risk(risk, state):
    out = CognitiveBridgeBlock().infer({
        "op": "rank_methods",
        "context": {"domain": "d", "uncertainty": "u", "claim_kind": "k", "risk": risk, "budget": 1},
        "variants": [{"id": "v", "method": "formal", "representation": "SMT", "engine": "formal",
                      "op": "check_validity", "estimated_cost": 1}],
        "required_methods": ["formal"]}).value
    assert out["completion_state"] == state, out
