# SPDX-License-Identifier: Apache-2.0
"""V0.2 proof obligations, refusal tests, and representative integration simulation.

No test here claims that a separate process's keys, execution boundary or G6
promotion law were verified; those must be tested in the full repository.
"""
from __future__ import annotations

import json
from dataclasses import replace

import pytest
from returns.result import Success as FakeSuccess

from g6.cognitive_bridge import (
    AdaptationPromotionGate, CandidateDossier, ClaimIR, CognitiveAssuranceOrchestrator,
    CognitiveBridgeBlock, CompositionalFailureModel, ControlTrial, Criterion,
    DomainStandard, EvaluatedTrial, EvidenceArtifact,
    FrozenVerifier, GuardChange, InProcessAssuranceEvaluator,
    LLMMetacognitiveCritic, MetacognitiveMethodSelector, MethodVariant,
    MitigationHypothesis, PairedTrial, ProblemSignature, ProfessionalAttestation,
    detect_failure_modes,
)
from g6.cognitive_bridge.assurance import DOCUMENT_FIELDS, sha256, canonical_json

PIN = "a" * 64
PIN2 = "b" * 64
PIN3 = "c" * 64


def docs():
    return {name: f"Independent description for {name} with stated limitations" for name in DOCUMENT_FIELDS}


def standard(risk=1):
    return DomainStandard(id="software_engineering", edition="local-review-v1", jurisdiction="AU",
                          authority="operator-approved software verification rubric (illustrative, NOT a professional standard)",
                          source_sha256=PIN, risk=risk,
                          criteria=(Criterion("c1", "Proposition has a checked derivation",
                                              "checker1", "local-review-v1:clause-1", "critical",
                                              ("false_confidence",), "report", ("formal",)),))


def artifact(content=b"GOOD", actor="candidate"):
    return EvidenceArtifact("a1", content, "report", actor, "store://eval/a1", "claim-1")


def verifier(check=None, *, owner="outside"):
    return FrozenVerifier("checker1", owner, PIN2,
                          check=check or (lambda content: content == b"GOOD"),
                          positive_controls=(b"GOOD",),
                          negative_controls=(b"BAD",))


def audit(*, risk=1, content=b"GOOD", check=None, attestation=None, auth=None,
          documentation=None, who="candidate", verifier_owner="outside"):
    return InProcessAssuranceEvaluator(evaluator_id="evaluator", reviewer_authenticator=auth).evaluate(
        standard=standard(risk), artifacts={"c1": artifact(content, who)},
        verifiers={"checker1": verifier(check, owner=verifier_owner)},
        documentation=docs() if documentation is None else documentation,
        candidate_actor="candidate", attestation=attestation)


def test_replayed_check_and_negative_control_do_not_mint_verified():
    dossier = audit()
    assert dossier.payload["scope_decision"] == "all_declared_checks_passed_in_this_evaluator"
    assert dossier.payload["completion_state"] == "qualified-draft"
    assert dossier.payload["metrics"]["negative_controls_detected"] == 1
    assert dossier.intact()
    assert "verified" not in {dossier.payload["completion_state"]}


def test_failing_artifact_reports_failure_codes_and_trace():
    dossier = audit(content=b"BAD")
    assert dossier.payload["completion_state"] == "blocked-escalated"
    event = detect_failure_modes(dossier)[0]
    assert event.code == "false_confidence"
    assert event.artifact_digest == sha256(b"BAD")
    assert event.severity == "critical"


def test_bad_controls_cannot_self_certify_even_when_artifact_passes():
    dossier = audit(check=lambda x: True)
    assert dossier.payload["completion_state"] == "blocked-escalated"
    assert dossier.payload["checks"][0]["controls"] == "failed_or_inconclusive"


def test_checker_throwing_on_negative_control_does_not_count_as_detection():
    def check(x):
        if x == b"BAD":
            raise RuntimeError("refused to test the negative example")
        return x == b"GOOD"
    dossier = audit(check=check)
    assert dossier.payload["completion_state"] == "blocked-escalated"


def test_missing_documentation_blocks_all_passing_checks():
    d = docs()
    d.pop("exceptions_and_unresolved_items")
    dossier = audit(documentation=d)
    assert "exceptions_and_unresolved_items" in dossier.payload["missing_documentation"]
    assert dossier.payload["completion_state"] == "blocked-escalated"


def test_same_producer_and_verifier_blocks():
    assert audit(verifier_owner="candidate").payload["completion_state"] == "blocked-escalated"
    with pytest.raises(ValueError, match="distinct identities"):
        InProcessAssuranceEvaluator(evaluator_id="candidate").evaluate(
            standard=standard(), artifacts={"c1": artifact()}, verifiers={"checker1": verifier()},
            documentation=docs(), candidate_actor="candidate")


def test_dossier_digest_is_integrity_check_not_authenticity():
    original = audit()
    original.payload["checks"][0]["result"] = "failed"
    assert not original.intact()
    with pytest.raises(ValueError, match="digest mismatch"):
        detect_failure_modes(original)
    # Self-reissuing a changed unkeyed digest is possible. Not a digital signature.
    assert CandidateDossier.from_payload(original.payload).intact()


def test_high_risk_refuses_unverified_reviewer_name():
    assert audit(risk=3).payload["completion_state"] == "blocked-escalated"
    forged = ProfessionalAttestation("reviewer", "professional", PIN3, "claimed-sig")
    assert audit(risk=3, attestation=forged).payload["completion_state"] == "blocked-escalated"
    assert audit(risk=3, attestation=forged, auth=lambda a: True).payload["completion_state"] == "blocked-escalated"


def test_authenticated_independent_review_passes_local_gate_but_never_issues_verified():
    st = standard(3)
    ev = artifact()
    scope = sha256(canonical_json({
        "standard": st.digest,
        "artifacts": {"c1": ev.digest},
        "verifiers": {"checker1": verifier().manifest()},
        "documentation": docs(),
        "candidate_actor": "candidate",
        "evaluator_id": "evaluator",
    }))
    att = ProfessionalAttestation("separate_professional", "domain_expert", scope, "sig-external")
    d = audit(risk=3, attestation=att, auth=lambda a: a.detached_signature == "sig-external")
    assert d.payload["professional_review"]["authenticated"] is True
    assert d.payload["completion_state"] == "qualified-draft"


def test_wrong_edition_or_unpinned_standard_refused():
    with pytest.raises(ValueError, match="SHA-256"):
        replace(standard(), source_sha256="not a hash")
    with pytest.raises(ValueError, match="unsupported mandatory"):
        Criterion("x", "reason", "oracle", "ref", required_methods=("made_up_solver",))


def test_compositional_failure_distinguishes_observed_from_proposed():
    observed = detect_failure_modes(audit(content=b"BAD"))
    guessed = MitigationHypothesis("g1", "false_confidence", "unearned trust in result",
                                   "require seeded wrong-answer controls", "remove test to see if bug escapes")
    graph = CompositionalFailureModel().map(observed, [guessed])
    assert graph["edges"][0]["edge"] == "proposed_mitigation_not_established"
    assert graph["observed"][0]["status"] == "observed_failure"


def context(domain="software_engineering", risk=1, budget=8):
    return ProblemSignature(domain, "formal_specification", "invariant", risk, budget)


def variants():
    return [MethodVariant("smt", "formal", "SMT", "formal", "check_validity", 2),
            MethodVariant("bool", "formal", "propositional", "formal", "check_validity", 2),
            MethodVariant("graph-json", "graph", "annotated_json", "graph_reasoning", "explain", 3)]


def trial(i, variant, *, resolved, domain="software_engineering", split="heldout"):
    return EvaluatedTrial(f"trial-{variant}-{i}", f"case-{variant}-{i}", "evaluator-frozen-suite-1",
                          domain, "formal_specification", "invariant", variant, resolved,
                          2, "trusted://evaluator/receipt", PIN, split)


def test_selector_cold_start_and_no_implicit_trust():
    candidate_trials = [trial(i, "smt", resolved=True) for i in range(12)]
    selector = MetacognitiveMethodSelector(candidate_trials)
    decision = selector.select(context(), variants(), required_methods=("formal",))
    assert decision.admitted_trials == 0
    assert decision.rejected_trials == 12
    assert decision.completion_state == "qualified-draft"
    assert decision.ordered_variants[0]["training_cases"] == 0


def test_selector_learns_context_specific_reliable_representation():
    history = [trial(i, "smt", resolved=True) for i in range(16)] + [trial(i, "bool", resolved=False) for i in range(16)]
    learned = MetacognitiveMethodSelector(history, admit_trial=lambda t: t.evaluator_ref.startswith("trusted://"))
    selection = learned.select(context(), variants(), required_methods=("formal", "graph"))
    assert selection.completion_state == "qualified-draft"
    assert [x["variant_id"] for x in selection.ordered_variants] == ["smt", "graph-json"]
    assert selection.ordered_variants[0]["training_cases"] == 16
    assert selection.ordered_variants[0]["historical_resolutions"] == 16
    assert all(x["obligation"] == "mandatory" for x in selection.ordered_variants)


def test_selector_does_not_learn_from_other_domains_or_training_split():
    traces = [trial(i, "smt", resolved=True, domain="unrelated") for i in range(20)]
    traces += [trial(100+i, "smt", resolved=True, split="training") for i in range(20)]
    selector = MetacognitiveMethodSelector(traces, admit_trial=lambda t: True)
    decision = selector.select(context(), variants(), required_methods=("formal",))
    assert decision.ordered_variants[0]["training_cases"] == 0
    assert selector.rejected_trials == 20


def test_selector_never_waives_mandatory_method_even_if_budget_low():
    dec = MetacognitiveMethodSelector().select(context(budget=2), variants(), required_methods=("formal", "graph"))
    assert dec.completion_state == "blocked-escalated"
    assert "graph" in dec.missing
    assert any(x["obligation"] == "mandatory" for x in dec.ordered_variants)


def test_high_risk_disables_exploration_bonus():
    dec = MetacognitiveMethodSelector(exploration_weight=1.0).select(context(risk=3), variants(),
                                                                      required_methods=("formal",))
    assert dec.ordered_variants[0]["exploration_bonus"] == 0


def test_tool_mismatch_refused_for_meta_variants():
    with pytest.raises(ValueError, match="arbitrarily substitute"):
        MethodVariant("escape", "formal", "SMT", "graph_reasoning", "explain", 0)


# FakeSuccess is the registry's own answer type (imported above): G6's door reads an answer by that class, so a
# stand-in class of another name would read as a failure there.


class FakeRegistry:
    def invoke(self, name, op, params=None, *, block_sensitive=False):
        assert block_sensitive
        assert name == "formal" and op == "check_validity"
        return FakeSuccess({"component": name, "op": op, "completion_state": "qualified-draft",
                            "answer": {"valid": True}, "premises": params["premises"]})


def compile_one(standard_override=None, budget=5):
    std = standard_override or standard()
    meta = MetacognitiveMethodSelector()
    obj = CognitiveAssuranceOrchestrator(meta, registry=FakeRegistry())
    claim = ClaimIR("claim-1", "Premises entail the conclusion", ("formal",), models={})
    plan = obj.compile(
        claim=claim, context=context(risk=std.risk, budget=budget), standard=std,
        variants=variants()[:2],
        variant_models={"smt": {"premises": ["(=> A B)", "A"], "formula": "B",
                                "sorts": {"A": "Bool", "B": "Bool"}},
                        "bool": {"premises": ["(=> A B)", "A"], "formula": "B",
                                 "sorts": {"A": "Bool", "B": "Bool"}}})
    return obj, plan


def test_compile_uses_selected_explicit_representation_and_canonical_g6_router():
    obj, compiled = compile_one()
    assert compiled.completion_state == "qualified-draft"
    assert compiled.executable_plan.steps[0].component == "formal"
    assert compiled.executable_plan.steps[0].op == "check_validity"
    assert compiled.selected_model_hashes
    assert obj.execute(compiled)["completion_state"] == "blocked-escalated"
    result = obj.execute(compiled, approved=True)
    assert result["completion_state"] == "qualified-draft"
    assert result["claim_id"] == "claim-1"


def test_freeze_standard_and_mandatory_obligations():
    std = standard()
    obj = CognitiveAssuranceOrchestrator(MetacognitiveMethodSelector())
    with pytest.raises(ValueError, match="omits"):
        obj.compile(claim=ClaimIR("claim-1", "wrong kind", ("graph",), models={}),
                    context=context(), standard=std, variants=variants(), variant_models={})
    with pytest.raises(ValueError, match="cannot weaken"):
        obj.compile(claim=ClaimIR("claim-1", "check", ("formal",), models={}),
                    context=context(risk=0), standard=standard(3), variants=variants(), variant_models={})


def test_orchestration_dossier_remains_scoped_and_blocked_if_method_refused():
    obj, compiled = compile_one()
    run = obj.execute(compiled, approved=True)
    evidence = obj.execution_artifact(run, actor="candidate", claim_id="claim-1")
    # The acceptance test checks that the executed method is a G6 formal op,
    # not a self-asserted 'verified' field. This is a WORKED EXAMPLE ONLY.
    def independent_report_checker(content):
        try:
            row = json.loads(content)
        except ValueError:
            return False
        return row.get("steps", [{}])[0].get("op") == "check_validity" and row.get("claim_id") == "claim-1"
    positive = canonical_json(run)
    check = FrozenVerifier("checker1", "outside", PIN2, independent_report_checker,
                           (positive,), (b'{}',))
    report = obj.assess(compiled, standard=standard(), run_result=run,
                        evaluator=InProcessAssuranceEvaluator(evaluator_id="evaluator"),
                        artifacts={"c1": evidence}, verifiers={"checker1": check},
                        documentation=docs(), candidate_actor="candidate")
    assert report["completion_state"] == "qualified-draft"
    # the state is completion_state alone: no second state field, and never verified
    assert "verified" not in report and report["completion_state"] != "verified"
    assert report["dossier"]["content_sha256"]
    broken = {**run, "completion_state": "blocked-escalated"}
    denied = obj.assess(compiled, standard=standard(), run_result=broken,
                        evaluator=InProcessAssuranceEvaluator(evaluator_id="evaluator"),
                        artifacts={"c1": evidence}, verifiers={"checker1": check},
                        documentation=docs(), candidate_actor="candidate")
    assert denied["completion_state"] == "blocked-escalated"


def change(paths=("src/g6/cognitive_bridge/method_adapter.py",)):
    return GuardChange("guard123", PIN, PIN2, PIN3, paths, "cognitive_bridge", "agent", "outside",
                       ("false_confidence",))


def pairs(n=26, wins=24, losses=0):
    result = []
    for i in range(n):
        was, now = ((False, True) if i < wins else
                    ((True, False) if i < wins + losses else (True, True)))
        result.append(PairedTrial(f"heldout-{i}", PIN3, was, now, True, "holdout",
                                  f"outside://case/{i}"))
    return result


def controls():
    return [ControlTrial("neg-1", True, "negative", "outside://control/neg"),
            ControlTrial("pos-1", True, "positive", "outside://control/pos")]


def gate(**kwargs):
    return AdaptationPromotionGate(admit_pair=lambda p: p.evaluator_receipt.startswith("outside://"),
                                   admit_control=lambda c: c.evaluator_receipt.startswith("outside://"), **kwargs)


def test_adaptation_gate_evaluates_frozen_pairs_and_never_activates():
    report = gate().assess(change(), pairs(), controls(), detected_failures=("false_confidence",))
    assert report["recommendation"] == "recommend_isolated_shadow_only"
    assert report["completion_state"] == "qualified-draft"
    assert report["requires_owner_authorisation"]
    assert report["p_one_sided"] < report["multiplicity_corrected_threshold"]


def test_adaptation_gate_blocks_relabelled_or_contaminated_holdout():
    ch = change()
    ch = replace(ch, previously_used_case_ids=("heldout-4",))
    report = gate().assess(ch, pairs(), controls(), detected_failures=("false_confidence",))
    assert report["completion_state"] == "blocked-escalated"
    assert any("overlaps" in msg for msg in report["blocked_reasons"])


def test_adaptation_refuses_weakened_evaluators_and_unanchored_failure():
    report = gate().assess(change(("src/g6/core/validation_gates.py",)), pairs(), controls(),
                           detected_failures=("false_confidence",))
    assert report["completion_state"] == "blocked-escalated"
    report2 = gate().assess(change(), pairs(), controls(), detected_failures=("unknown",))
    assert any("not bound" in x for x in report2["blocked_reasons"])


def test_adaptation_blocks_critical_regression_even_if_other_cases_improve():
    sample = pairs(n=30, wins=26, losses=1)
    result = gate().assess(change(), sample, controls(), detected_failures=("false_confidence",))
    assert result["completion_state"] == "blocked-escalated"
    assert result["critical_regression_cases"]


def test_adaptation_blocks_false_negative_control_and_no_outside_oracle():
    broken = [replace(controls()[0], correct_detection=False), controls()[1]]
    result = gate().assess(change(), pairs(), broken, detected_failures=("false_confidence",))
    assert result["completion_state"] == "blocked-escalated"
    result = AdaptationPromotionGate().assess(change(), pairs(), controls(), detected_failures=("false_confidence",))
    assert result["completion_state"] == "blocked-escalated"


def test_metacognitive_critic_is_not_a_verifier_or_executor():
    class Model:
        def complete(self, system, user, *, max_tokens):
            assert "NOT a verifier" in system
            return json.dumps({"candidates": [{
                "failure_mode": "unearned_certainty", "assumption_at_risk": "no confounders",
                "counterexample_or_refutation": "inject confounder and re-estimate",
                "suggested_method": "causal", "evidence_required": "evaluator-owned data",
                "potential_domain_standard_gap": "external calibration",
            }]})
    report = LLMMetacognitiveCritic(Model()).propose("Assess causal claim", "T -> Y", [])
    assert report.completion_state == "qualified-draft"
    assert report.candidates[0].suggested_method == "causal"
    assert "candidate" in report.warning.lower()


def test_stateless_g6_door_meta_rank_does_not_accept_training_data():
    block = CognitiveBridgeBlock()
    out = block.infer({"op": "rank_methods", "context": {
        "domain": "software_engineering", "uncertainty": "formal_specification",
        "claim_kind": "invariant", "risk": 1, "budget": 4},
        "required_methods": ["formal"],
        "variants": [vars(variants()[0])]}).value
    assert out["completion_state"] == "qualified-draft"
    assert out["admitted_trials"] == 0
    assert "cold-start" in out["evidence_note"]
    assert "assurance_schema" in block.infer({"op": "ops"}).value["ops"]


def test_router_allows_real_g6_alternative_operations_not_arbitrary_ops():
    from g6.cognitive_bridge import MethodRouter
    claim = ClaimIR("c2", "Proposition P is satisfiable", ("formal",), models={
        "formal": {"variant_op": "check_propositional", "formula": "P", "assertions": []}})
    plan = MethodRouter().plan([claim])
    assert plan.steps[0].op == "check_propositional"
    assert "variant_op" not in plan.steps[0].params
    with pytest.raises(ValueError, match="not allowlisted"):
        MethodRouter().plan([replace(claim, models={
            "formal": {"variant_op": "run_python", "formula": "P"}})])


def test_meta_selects_true_g6_operation_variant_and_executor_preserves_it():
    choices = [MethodVariant("z3", "formal", "SMT", "formal", "check_validity", 2),
               MethodVariant("dpll", "formal", "propositional", "formal", "check_propositional", 1)]
    class Registry:
        def invoke(self, name, op, params=None, *, block_sensitive=False):
            assert name == "formal" and op == "check_propositional" and block_sensitive is True
            assert params["formula"] == "P"
            return FakeSuccess({"completion_state": "qualified-draft", "result": {"answer": True}})
    std = standard()
    obj = CognitiveAssuranceOrchestrator(MetacognitiveMethodSelector(), registry=Registry())
    claim = ClaimIR("claim-1", "Classical proposition P", ("formal",), {})
    compiled = obj.compile(claim=claim, standard=std, context=context(), variants=choices,
                           variant_models={"z3": {"formula": "P", "sorts": {"P": "Bool"}},
                                           "dpll": {"formula": "P", "assertions": []}})
    assert compiled.executable_plan.steps[0].op == "check_propositional"
    assert compiled.executable_plan.claims[0].models["formal"]["variant_op"] == "check_propositional"
    result = obj.execute(compiled, approved=True)
    assert result["completion_state"] == "qualified-draft"


def test_portfolio_optimizer_picks_cheaper_representation_to_cover_all_obligations():
    methods = [MethodVariant("formal-rich", "formal", "full_SMT", "formal", "check_validity", 2.0),
               MethodVariant("formal-cheap", "formal", "compact_SMT", "formal", "check_validity", 1.0),
               MethodVariant("graph", "graph", "annotated", "graph_reasoning", "explain", 2.0)]
    decision = MetacognitiveMethodSelector().select(context(budget=3.0), methods,
                                                     required_methods=("formal", "graph"))
    assert decision.completion_state == "qualified-draft"
    assert {x["variant_id"] for x in decision.ordered_variants} == {"formal-cheap", "graph"}


def test_duplicate_case_variant_does_not_double_count_training_evidence():
    t = trial(1, "smt", resolved=True)
    repeated = replace(t, trial_id="a-second-name-for-the-same-case")
    selector = MetacognitiveMethodSelector([t, repeated], admit_trial=lambda a: True)
    # both trials are kept (grouped by case, not dropped), and the case still counts once
    assert len(selector.trials) == 2 and selector.rejected_trials == 0
    row = selector.select(context(), variants()[:1], required_methods=("formal",)).ordered_variants[0]
    assert row["training_cases"] == 1 and row["historical_resolutions"] == 1


def test_epistemic_gate_detects_cross_method_conflict_and_blocks():
    from g6.cognitive_bridge import ScopedAssertion, CrossMethodConflictGate
    records = [
        ScopedAssertion("c", "p", PIN, "supports", "formal", "ev://formal", "proof1"),
        ScopedAssertion("c", "p", PIN, "refutes", "causal", "ev://causal", "proof2"),
    ]
    judged = CrossMethodConflictGate(admit_assertion=lambda a: a.evaluator_receipt in ("proof1", "proof2"))
    result = judged.reconcile(records)
    assert result["completion_state"] == "blocked-escalated"
    assert result["conflicts"] == 1
    assert result["scoped_findings"][0]["status"] == "contested"


def test_epistemic_gate_rejects_llm_only_or_unadmitted_support():
    from g6.cognitive_bridge import ScopedAssertion, CrossMethodConflictGate
    a = ScopedAssertion("c", "p", PIN, "supports", "llm", "generated://text", "pretend")
    result = CrossMethodConflictGate().reconcile([a])
    assert result["completion_state"] == "blocked-escalated"
    assert result["unadmitted_evidence_refs"] == ["generated://text"]


def test_epistemic_gate_does_not_conflate_incomparable_scopes():
    from g6.cognitive_bridge import ScopedAssertion, CrossMethodConflictGate
    rows = [ScopedAssertion("c", "p", PIN, "supports", "formal", "e1", "one"),
            ScopedAssertion("c", "p", PIN2, "refutes", "causal", "e2", "two")]
    output = CrossMethodConflictGate(admit_assertion=lambda x: True).reconcile(rows)
    assert output["conflicts"] == 0
    assert len(output["scoped_findings"]) == 2
    assert "incomparable" in output["limitations"][0]


def test_orchestration_never_averages_conflicting_independent_assertions():
    from g6.cognitive_bridge import ScopedAssertion, CrossMethodConflictGate
    obj, compiled = compile_one()
    run = obj.execute(compiled, approved=True)
    evidence = obj.execution_artifact(run, actor="candidate", claim_id="claim-1")
    verifier_spec = FrozenVerifier("checker1", "outside", PIN2,
                                   lambda b: b == evidence.content,
                                   (evidence.content,), (b"WRONG",))
    assertions = [ScopedAssertion("claim-1", "truth", PIN, "supports", "formal", "source1", "sig1"),
                  ScopedAssertion("claim-1", "truth", PIN, "refutes", "causal", "source2", "sig2")]
    result = obj.assess(compiled, standard=standard(), run_result=run,
                        evaluator=InProcessAssuranceEvaluator(evaluator_id="evaluator"),
                        artifacts={"c1": evidence}, verifiers={"checker1": verifier_spec},
                        documentation=docs(), candidate_actor="candidate",
                        scoped_assertions=assertions,
                        epistemic_gate=CrossMethodConflictGate(admit_assertion=lambda a: True))
    assert result["dossier"]["completion_state"] == "qualified-draft"
    assert result["epistemic_reconciliation"]["conflicts"] == 1
    assert result["completion_state"] == "blocked-escalated"


def test_repeated_evidence_source_not_counted_as_independent_corroboration():
    from g6.cognitive_bridge import ScopedAssertion, CrossMethodConflictGate
    first = ScopedAssertion("c", "p", PIN, "supports", "formal", "same-source", "p1")
    second = replace(first, evaluator_receipt="p2", method="graph")
    rec = CrossMethodConflictGate(admit_assertion=lambda a: True).reconcile([first, second])
    assert rec["scoped_findings"][0]["source_count"] == 1
    assert rec["repeated_source_refs"] == ["same-source"]
