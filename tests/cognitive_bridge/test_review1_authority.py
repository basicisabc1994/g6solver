# SPDX-License-Identifier: Apache-2.0
"""Review 1, class 2 (findings 4, 5, 7, 8 and 20): what a caller writes never decides an outcome. Execution is read from
the executor's own per-step record, a criterion is credited only by the checker its standard pins, a trial credits only
the variant definition it names, a review binds the evidence it reviewed, and an assessment echoes no caller result."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest
from returns.result import Success

from g6.cognitive_bridge import (
    EvaluatedTrial, InProcessAssuranceEvaluator, MetacognitiveMethodSelector, MethodVariant, ProblemSignature,
    ProfessionalAttestation, assurance,
)


def _trial(n: int, variant: MethodVariant, **kwargs) -> EvaluatedTrial:
    """A trial bound to the variant definition it evaluated, where the trial can carry that binding."""
    if "variant_digest" in EvaluatedTrial.__dataclass_fields__:
        kwargs["variant_digest"] = variant.definition_digest
    return EvaluatedTrial(f"t{n}", f"case{n}", "formal-suite", "d", "u", "k", variant.id, True, 1, "evaluator:receipt",
                          "a" * 64, **kwargs)


@pytest.mark.parametrize("run", [
    {"completion_state": "qualified-draft"},
    {"completion_state": "qualified-draft", "claim_id": "another-claim", "standard_digest": "0" * 64, "steps": [],
     "method_execution_coverage": 0, "run_id": "forged"},
])
def test_finding_4_assess_cannot_credit_a_caller_label_in_place_of_the_compiled_execution(assurance_case, run):
    c = assurance_case
    out = c["orchestrator"].assess(
        c["compiled"], standard=c["standard"], run_result=run, evaluator=c["evaluator"], verifiers=c["verifiers"],
        artifacts=c["artifacts"], documentation=c["documentation"], candidate_actor=c["candidate_actor"])
    assert out["completion_state"] == "blocked-escalated", (
        f'unexecuted mandatory formal step accepted: {out["completion_state"]}')


def test_finding_4_an_executed_run_with_a_step_the_caller_edited_is_not_credited(assurance_case):
    c = assurance_case

    class Registry:
        def invoke(self, name, op, params=None, **kwargs):
            return Success({"completion_state": "qualified-draft", "answer": {"valid": True}})
    c["orchestrator"].registry = Registry()
    run = c["orchestrator"].execute(c["compiled"], approved=True)
    assert run["completion_state"] == "qualified-draft"
    common = dict(standard=c["standard"], evaluator=c["evaluator"], verifiers=c["verifiers"],
                  artifacts=c["artifacts"], documentation=c["documentation"], candidate_actor=c["candidate_actor"])
    forged = json.loads(json.dumps(run))
    forged["steps"][0]["step_digest"] = "0" * 64
    assert c["orchestrator"].assess(c["compiled"], run_result=forged, **common)["completion_state"] == \
        "blocked-escalated"


def test_class_walk_a_hand_built_plan_without_the_standards_mandatory_method_is_not_credited(assurance_case):
    # the same class (caller input given authority), found walking it: a CompiledResearch is the caller's object, so
    # whether it covers the standard's mandatory methods is read again at assessment, not taken from compile()
    from dataclasses import fields

    from g6.cognitive_bridge import ClaimIR, MethodRouter, orchestration

    c = assurance_case

    class Registry:
        def invoke(self, name, op, params=None, **kwargs):
            return Success({"completion_state": "qualified-draft", "answer": {"derived": True}})
    graph_only = MethodRouter().plan([ClaimIR("claim", "p holds", ("graph",), {"graph": {"program": "p(a).",
                                                                                          "atom": "p(a)"}})])
    pins = {"plan_digest": orchestration._plan_digest(graph_only)} if any(
        f.name == "plan_digest" for f in fields(c["compiled"])) else {}
    swapped = replace(c["compiled"], executable_plan=graph_only, selected_model_hashes={}, **pins)
    c["orchestrator"].registry = Registry()
    run = c["orchestrator"].execute(swapped, approved=True)
    assert run["completion_state"] == "qualified-draft"
    out = c["orchestrator"].assess(
        swapped, standard=c["standard"], run_result=run, evaluator=c["evaluator"], verifiers=c["verifiers"],
        artifacts=c["artifacts"], documentation=c["documentation"], candidate_actor=c["candidate_actor"])
    assert out["completion_state"] == "blocked-escalated", "a plan without the mandatory formal step was credited"


def test_finding_5_a_mapping_key_cannot_substitute_a_different_checker_for_the_pinned_id(assurance_case):
    c = assurance_case
    different_checker = replace(c["verifiers"]["checker"], id="unrelated-checker")
    try:
        dossier = c["evaluator"].evaluate(
            standard=c["standard"], artifacts=c["artifacts"], verifiers={"checker": different_checker},
            documentation=c["documentation"], candidate_actor=c["candidate_actor"])
    except ValueError:
        return
    row = dossier.payload["checks"][0]
    assert dossier.payload["completion_state"] == "blocked-escalated", (
        f'{row["verifier_id"]} credited using manifest id={row["verifier_manifest"]["id"]}')


def test_finding_7_a_new_method_cannot_inherit_an_admitted_variants_history_by_reusing_its_name():
    old_variant = MethodVariant("stable", "formal", "SMT", "formal", "check_validity", 1)
    for bound in (False, True):  # the review's trials name no definition; bound ones name the formal one
        admitted = [_trial(i, old_variant) if bound else
                    EvaluatedTrial(f"t{i}", f"case{i}", "formal-suite", "d", "u", "k", old_variant.id, True, 1,
                                   "evaluator:receipt", "a" * 64) for i in range(20)]
        receipts = {t.trial_id: (t, old_variant) for t in admitted}
        accepted = []

        def admit(t):
            record, evaluated_variant = receipts[t.trial_id]
            accepted.append(evaluated_variant.method)
            return t == record and evaluated_variant == old_variant
        selector = MetacognitiveMethodSelector(admitted, admit_trial=admit)
        replacement = MethodVariant("stable", "graph", "rules", "graph_reasoning", "explain", 1)
        row = selector.select(ProblemSignature("d", "u", "k", 1, 2), [replacement],
                              required_methods=["graph"]).ordered_variants[0]
        assert accepted == ["formal"] * 20
        assert row["training_cases"] == 0, f'new graph method inherited {row["training_cases"]} formal cases'


def test_finding_7_the_definition_the_trials_name_is_still_credited():
    variant = MethodVariant("stable", "formal", "SMT", "formal", "check_validity", 1)
    selector = MetacognitiveMethodSelector([_trial(i, variant) for i in range(5)], admit_trial=lambda t: True)
    row = selector.select(ProblemSignature("d", "u", "k", 1, 2), [variant], required_methods=["formal"])
    assert row.ordered_variants[0]["training_cases"] == 5
    same_id_other_representation = replace(variant, representation="propositional")
    row = selector.select(ProblemSignature("d", "u", "k", 1, 2), [same_id_other_representation],
                          required_methods=["formal"])
    assert row.ordered_variants[0]["training_cases"] == 0


@pytest.mark.parametrize("change", ["all", "source_ref", "claim_id", "id"])
def test_finding_8_authenticated_review_cannot_be_reused_after_evidence_provenance_changes(
        assurance_case, monkeypatch, change):
    c = assurance_case
    standard = replace(c["standard"], risk=3)
    scopes = []  # observe the evaluator's own scope digest, so the test does not copy its serialisation
    real_sha256 = assurance.sha256

    def observe_digest(data):
        digest = real_sha256(data)
        try:
            value = json.loads(data)
        except ValueError:
            return digest
        if isinstance(value, dict) and "artifacts" in value and "candidate_actor" in value:
            scopes.append(digest)
        return digest
    monkeypatch.setattr(assurance, "sha256", observe_digest)
    attestation = ProfessionalAttestation("professional", "domain reviewer", "0" * 64, "authenticated-receipt")
    evaluator = InProcessAssuranceEvaluator(evaluator_id="outside", reviewer_authenticator=lambda a: a == attestation)
    common = dict(standard=standard, verifiers=c["verifiers"], documentation=c["documentation"],
                  candidate_actor="candidate")
    evaluator.evaluate(artifacts=c["artifacts"], attestation=attestation, **common)
    assert scopes
    attestation = replace(attestation, scope_sha256=scopes[-1])
    common["attestation"] = attestation
    assert evaluator.evaluate(artifacts=c["artifacts"], **common).payload["professional_review"]["authenticated"]
    edits = {"source_ref": "store:unreviewed-source", "claim_id": "unreviewed-claim", "id": "unreviewed-artifact"}
    edits = edits if change == "all" else {change: edits[change]}
    result = evaluator.evaluate(artifacts={"c": replace(c["artifacts"]["c"], **edits)}, **common)
    assert result.payload["professional_review"]["authenticated"] is False, (
        f"review remained authenticated after {change} changed")


def test_finding_20_assess_does_not_echo_a_callers_unchecked_verified_state(assurance_case):
    c = assurance_case
    out = c["orchestrator"].assess(
        c["compiled"], standard=c["standard"],
        run_result={"completion_state": "verified", "verified": True, "run_id": "caller-invented"},
        evaluator=c["evaluator"], verifiers=c["verifiers"], artifacts=c["artifacts"],
        documentation=c["documentation"], candidate_actor=c["candidate_actor"])
    assert out["completion_state"] == "blocked-escalated"
    carried = out["method_results"]
    assert carried.get("completion_state") != "verified" and "verified" not in carried, (
        f"assess echoed unchecked reliability claims: {carried}")
    assert out["run_id"] != "caller-invented"
