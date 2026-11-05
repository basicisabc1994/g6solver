# SPDX-License-Identifier: Apache-2.0
"""The bridge keeps G6's own rules: every name it uses is bound, its composed results go through G6's checked door,
each fact is decided by the rule G6 already has, and its words say no more than the code does."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from returns.result import Success

from g6.cognitive_bridge import (
    ClaimIR, CognitiveAssuranceOrchestrator, Criterion, DomainStandard, FrozenVerifier, MetacognitiveMethodSelector,
    MethodVariant, ProblemSignature, ScopedAssertion,
)
from g6.cognitive_bridge.assurance import DOCUMENT_FIELDS, canonical_json

PIN, PIN2 = "a" * 64, "b" * 64
ROOT = Path(__file__).resolve().parents[2]


class FormalRegistry:
    """The registry's invoke contract (``returns.result.Success``), for one formal op."""

    def invoke(self, name, op, params=None, *, block_sensitive=False, llm=None):
        return Success({"component": name, "op": op, "completion_state": "qualified-draft",
                        "answer": {"valid": True}, "premises": params["premises"]})


def _standard() -> DomainStandard:
    return DomainStandard(id="software_engineering", edition="local-review-v1", jurisdiction="AU",
                          authority="operator-approved rubric (illustrative)", source_sha256=PIN, risk=1,
                          criteria=(Criterion("c1", "Proposition has a checked derivation", "checker1",
                                              "local-review-v1:clause-1", "critical", ("false_confidence",),
                                              "report", ("formal",)),))


def _compiled():
    orchestrator = CognitiveAssuranceOrchestrator(MetacognitiveMethodSelector(), registry=FormalRegistry())
    compiled = orchestrator.compile(
        claim=ClaimIR("claim-1", "Premises entail the conclusion", ("formal",), models={}),
        context=ProblemSignature("software_engineering", "formal_specification", "invariant", 1, 5),
        standard=_standard(), variants=[MethodVariant("smt", "formal", "SMT", "formal", "check_validity", 2)],
        variant_models={"smt": {"premises": ["(=> A B)", "A"], "formula": "B", "sorts": {"A": "Bool", "B": "Bool"}}})
    return orchestrator, compiled


def _assess(orchestrator, compiled, **kwargs):
    from g6.cognitive_bridge import IndependentAssuranceEvaluator as Evaluator

    run = orchestrator.execute(compiled, approved=True)
    evidence = orchestrator.execution_artifact(run, actor="candidate", claim_id="claim-1")
    check = FrozenVerifier("checker1", "outside", PIN2, lambda content: content == evidence.content,
                           (evidence.content,), (b"WRONG",))
    docs = {name: f"Description of {name} with its limits" for name in DOCUMENT_FIELDS}
    return orchestrator.assess(compiled, standard=_standard(), run_result=run, evaluator=Evaluator(evaluator_id="ev"),
                               artifacts={"c1": evidence}, verifiers={"checker1": check}, documentation=docs,
                               candidate_actor="candidate", **kwargs)


def test_assertions_without_a_gate_are_judged_not_a_name_error():
    # ruff F821 on 9ebfbbe: orchestration.py used CrossMethodConflictGate and ScopedAssertion without importing them,
    # so an assessment given assertions and no gate raised NameError. With no admission hook, no assertion is
    # admitted, so the assessment blocks.
    orchestrator, compiled = _compiled()
    assertion = ScopedAssertion("claim-1", "truth", PIN, "supports", "formal", "source1", "receipt1")
    report = _assess(orchestrator, compiled, scoped_assertions=[assertion])
    assert report["completion_state"] == "blocked-escalated"
    assert report["epistemic_reconciliation"]["unadmitted_evidence_refs"] == ["source1"]
    json.dumps(report)  # the report is plain data
    assert canonical_json(report["dossier"])
    assert report["warning_card"]["code"] == "G6_E_COGNITIVE_ASSURANCE_BLOCKED"


def test_risk_is_the_number_of_g6s_risk_tier():
    # 9ebfbbe's risk was a bare 0..3 scale; G6's canonical noun is risk_tier, R0 to R3. The number stays the field
    # (it orders tiers), and every surface names the tier.
    assert ProblemSignature("d", "u", "k", 2, 1).risk_tier == "R2"
    assert _standard().risk_tier == "R1"
    orchestrator, compiled = _compiled()
    assert compiled.to_dict()["risk_tier"] == "R1"
    assert _assess(orchestrator, compiled)["dossier"]["professional_review"]["risk_tier"] == "R1"
    with pytest.raises(ValueError, match="risk tier"):
        ProblemSignature("d", "u", "k", 4, 1)
    with pytest.raises(ValueError, match="risk tier"):
        replace(_standard(), risk=4)


def test_the_bridges_own_nouns_are_defined_beside_g6s():
    spec = (ROOT / "docs" / "cognitive_bridge" / "TECHNICAL_SPEC.md").read_text(encoding="utf-8")
    vocabulary = spec.split("## Vocabulary", 1)[1].split("\n## ", 1)[0]
    for noun in ("problem signature", "method variant", "trial", "cohort", "assurance dossier", "risk_tier"):
        assert f"`{noun}`" in vocabulary, noun


def _keys(value) -> set[str]:
    """Every key anywhere in a nested result, except inside G6's own warning cards (whose shape, INV-ENV-2, carries
    its own flag)."""
    if isinstance(value, dict):
        return set(value) | {k for key, item in value.items() if key != "warning_card" for k in _keys(item)}
    if isinstance(value, list):
        return {k for item in value for k in _keys(item)}
    return set()


def test_the_assessment_carries_the_envelope_fields_and_no_bare_verified_field():
    # 9ebfbbe's assess() returned "verified": False beside completion_state: a second state field, and no
    # warning_card, evidence, request_id or run_id. The state lives in completion_state only.
    orchestrator, compiled = _compiled()
    report = _assess(orchestrator, compiled)
    assert report["completion_state"] == "qualified-draft"
    assert "verified" not in _keys(report)
    assert report["warning_card"]["code"] == "G6_E_COGNITIVE_NOT_PROMOTED"
    assert report["request_id"] and report["run_id"] == report["method_results"]["run_id"]
    assert report["evidence"][0]["source"] == "dossier:" + report["dossier"]["content_sha256"]
