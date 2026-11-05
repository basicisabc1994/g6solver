# SPDX-License-Identifier: Apache-2.0
"""The bridge keeps G6's own rules: every name it uses is bound, its composed results go through G6's checked door,
each fact is decided by the rule G6 already has, and its words say no more than the code does."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from returns.result import Failure, Success

from g6.cognitive_bridge import (
    ClaimIR, CognitiveAssuranceOrchestrator, Criterion, DomainStandard, FrozenVerifier, MetacognitiveMethodSelector,
    MethodRouter, MethodVariant, MixedMethodExecutor, ProblemSignature, ScopedAssertion,
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
    from g6.cognitive_bridge import InProcessAssuranceEvaluator as Evaluator

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


class _ClaimingRegistry:
    """The registry's invoke contract for a component that calls its own answer verified; ``sensitive`` ops are
    refused on an untrusted surface, as the real registry refuses a component's ``__g6_sensitive_ops__``."""

    def __init__(self, sensitive: frozenset[tuple[str, str]] = frozenset()) -> None:
        self.sensitive, self.calls = sensitive, []

    def invoke(self, name, op, params=None, *, block_sensitive=False, llm=None):
        if block_sensitive and (name, op) in self.sensitive:
            return Failure(f"[G6_E_SENSITIVE_COMPONENT_DENIED] {name}.{op} is not available on this surface")
        self.calls.append((name, op))
        return Success({"completion_state": "verified", "answer": {"valid": True}})


def _formal_plan():
    return MethodRouter().plan([ClaimIR("q", "Check it", ("formal",), {"formal": {"formula": "True"}})])


def test_a_components_own_verified_is_held_by_g6s_door():
    # 9ebfbbe's executor called registry.invoke directly: the step's result kept the component's own "verified" and
    # no door card said it was held. Through G6's checked door the copy says qualified-draft and the card says why.
    step = MixedMethodExecutor(_ClaimingRegistry()).execute(_formal_plan(), approved=True)["steps"][0]
    assert step["completion_state"] == "qualified-draft"
    assert step["result"]["completion_state"] == "qualified-draft"
    assert step["result"]["claimed_completion_state"] == "verified"
    assert step["warning_card"]["code"] == "G6_E_PROMOTION_LAW"


def test_a_sensitive_op_runs_only_with_the_persons_allow(monkeypatch):
    # The door's rule: refused unless the person allowed it, in their own code or in G6_MCP_SENSITIVE. 9ebfbbe always
    # refused a sensitive op, whatever the person allowed, and took no allow at all.
    monkeypatch.delenv("G6_MCP_SENSITIVE", raising=False)
    registry = _ClaimingRegistry(sensitive=frozenset({("formal", "check_validity")}))
    refused = MixedMethodExecutor(registry).execute(_formal_plan(), approved=True)["steps"][0]
    assert refused["completion_state"] == "blocked-escalated" and registry.calls == []
    assert refused["code"] == refused["warning_card"]["code"] == "G6_E_COGNITIVE_STEP_FAILED"
    assert refused["door"]["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED"  # the door's own card, whole
    assert "allow=('formal.check_validity',)" in refused["reason"]
    allowed = MixedMethodExecutor(registry, allow=("formal.check_validity",)).execute(_formal_plan(), approved=True)
    assert allowed["steps"][0]["completion_state"] == "qualified-draft"
    monkeypatch.setenv("G6_MCP_SENSITIVE", "formal.check_validity")
    by_setting = MixedMethodExecutor(registry).execute(_formal_plan(), approved=True)
    assert by_setting["steps"][0]["completion_state"] == "qualified-draft"
    assert registry.calls == [("formal", "check_validity")] * 2


def test_the_adaptation_gate_decides_by_the_meta_harness_gates_own_statistic():
    # 9ebfbbe's adaptation.py computed its own sign test beside the one the meta-harness gate decides by: one fact,
    # two rules. The PR's interface (exact_sign_p, the gate's arguments and extra conditions) stays.
    import importlib

    from g6.cognitive_bridge import AdaptationPromotionGate, ControlTrial, GuardChange, PairedTrial, adaptation
    from g6.metaharness.schema import EvidencePolicy
    from g6.reasoning import sign_test_p

    evaluate = importlib.import_module("g6.metaharness.evaluate")  # the package's evaluate() shadows the module
    assert adaptation.exact_sign_p is sign_test_p is evaluate.sign_test_p
    change = GuardChange("g", PIN, PIN2, "c" * 64, ("src/g6/cognitive_bridge/guard.py",), "cognitive_bridge", "agent",
                         "outside", ("false_confidence",))
    pairs = [PairedTrial(f"h{i}", "c" * 64, i >= 22, True, False, "holdout", f"outside://{i}") for i in range(26)]
    controls = [ControlTrial("n", True, "negative", "outside://n"), ControlTrial("p", True, "positive", "outside://p")]
    report = AdaptationPromotionGate(admit_pair=lambda p: True, admit_control=lambda c: True).assess(
        change, pairs, controls, detected_failures=("false_confidence",))
    assert report["p_one_sided"] == sign_test_p(report["wins"], report["losses"])
    assert report["multiplicity_corrected_threshold"] == EvidencePolicy(alpha=0.05, max_evaluations=5).threshold


def test_the_conflict_gate_reads_stance_and_lineage_by_epistemic_integritys_rule(monkeypatch):
    # 9ebfbbe's epistemic.py decided "contested" and "one source repeated" with rules of its own beside
    # g6.epistemic_integrity's. The gate now hands each admitted assertion to those rules as an evidence record.
    from g6.cognitive_bridge import CrossMethodConflictGate
    from g6.epistemic_integrity import adjudicator

    rows = [ScopedAssertion("c", "p", PIN, "supports", "formal", "e1", "r1"),
            ScopedAssertion("c", "p", PIN, "refutes", "causal", "e2", "r2")]
    gate = CrossMethodConflictGate(admit_assertion=lambda a: True)
    assert gate.reconcile(rows)["conflicts"] == 1
    seen = []
    real = adjudicator.collapse_independence
    monkeypatch.setattr(adjudicator, "collapse_independence", lambda records: seen.append(records) or real(records))
    monkeypatch.setattr(adjudicator, "detect_contested", lambda records, groups: False)
    assert gate.reconcile(rows)["conflicts"] == 0  # the contested decision is epistemic_integrity's
    assert [r.source_uri_or_artifact_id for r in seen[0]] == ["e1", "e2"]
    assert [r.supports_or_opposes for r in seen[0]] == ["supports", "opposes"]


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
