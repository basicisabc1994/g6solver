# SPDX-License-Identifier: Apache-2.0
from g6.core.evidence import EvidenceRecord


def test_markers_return_offsets_and_negation_without_a_score():
    # Removing markers.py's preceding-negation flag makes this fail.
    from g6.epistemic_integrity import markers
    text = "Perhaps it works. It is not definitely reliable. Certainly worth checking."
    result = markers(text)
    assert result["uncertainty"][0]["text"] == "Perhaps"
    confidence = result["confidence"]
    assert [hit["negated"] for hit in confidence] == [True, False]
    assert all(text[h["start"]:h["end"]] == h["text"] for hits in result.values() for h in hits)
    assert "score" not in result


def test_certainty_on_weak_evidence_is_flagged_through_existing_block():
    # Removing adjudicator.py's certainty-with-weak-evidence rule makes this fail (ALIGN_VERBSAMP-01, D1).
    from g6.epistemic_integrity import EpistemicIntegrityBlock, EpistemicIntegrityInput
    result = EpistemicIntegrityBlock().infer(EpistemicIntegrityInput(
        message="The copper valve definitely reduces pressure.", action_criticality="info",
        evidence=[{"source": "note", "source_type": "secondary"}])).unwrap()
    assert "G6_E_CERTAINTY_WEAK_EVIDENCE" in result["claims"][0]["triggered"]
    assert result["overall_disposition"] == "ASK" and result["llm_used"] is False


def test_negated_certainty_does_not_trigger_and_no_model_is_needed():
    # Removing adjudicator.py's not-negated filter makes this fail (ALIGN_VERBSAMP-02).
    from g6.epistemic_integrity import adjudicate_claim
    result = adjudicate_claim("The copper valve is not definitely effective", action_criticality="info",
                               evidence_records=(EvidenceRecord("note"),))
    assert "G6_E_CERTAINTY_WEAK_EVIDENCE" not in result.triggered


def test_marker_dependency_failure_blocks(monkeypatch):
    # Removing adjudicator.py's marker-failure BLOCK branch makes this fail.
    import g6.epistemic_integrity.adjudicator as module
    monkeypatch.setattr(module, "markers", lambda *a: (_ for _ in ()).throw(RuntimeError()))
    result = module.adjudicate_claim("The valve works", action_criticality="creative")
    assert result.claim.disposition == "BLOCK"
    assert "G6_E_MARKER_CHECK_FAILED" in result.triggered
