# SPDX-License-Identifier: Apache-2.0
"""Review 3, findings 1, 2 and 7 (check_then_act_race, met in all three reviews), removed at the root: every value the
bridge checks and then uses is a deep, immutable snapshot taken when it is accepted, nothing it returns is a live view
of what it uses, and a decision that rests on the standard's digest reads it again after use."""
from __future__ import annotations

import json
from dataclasses import replace

from g6.cognitive_bridge import (
    CognitiveAgent, CompositionalFailureModel, Criterion, Hypothesis, InProcessAssuranceEvaluator, Observation, Probe,
    StaticProposer, ToolSpec,
)
from g6.cognitive_bridge.adaptation import FailureModeFinding
from g6.cognitive_bridge.assurance import CandidateDossier


def test_finding_1_clearing_the_callers_controls_cannot_bypass_negative_controls(case3):
    negatives = [b"BAD"]
    try:
        verifier = replace(case3["verifiers"]["checker"], negative_controls=negatives, check=lambda content: True)
    except (TypeError, ValueError):
        return  # Refusing mutable controls at construction is also safe.
    negatives.clear()
    out = case3["evaluator"].evaluate(
        standard=case3["standard"], artifacts=case3["artifacts"], verifiers={"checker": verifier},
        documentation=case3["documentation"], candidate_actor=case3["candidate_actor"])
    assert out.payload["completion_state"] == "blocked-escalated", (
        "always-true checker credited after negative controls were cleared; "
        f"negative_controls_attempted={out.payload['metrics']['negative_controls_attempted']}")


def test_finding_2_assess_cannot_pass_when_a_mandatory_criterion_is_relabelled_during_evaluation(case3):
    from g6.cognitive_bridge import ClaimIR, MethodVariant, ProblemSignature

    first = case3["standard"].criteria[0]
    missing = Criterion("missing", "unmet obligation", "checker", "1:2", required_methods=("formal",))
    standard = replace(case3["standard"], criteria=(first, missing))
    public_manifest = standard.manifest()
    compiled = case3["orchestrator"].compile(
        claim=ClaimIR("claim", "A entails A", ("formal",), {}),
        context=ProblemSignature("review", "logic", "validity", 1, 2), standard=standard,
        variants=[MethodVariant("v", "formal", "SMT", "formal", "check_validity", 1)],
        variant_models={"v": {"formula": "A", "premises": ["A"]}})
    original_pin = standard.digest
    run = case3["orchestrator"].execute(compiled, approved=True)
    assert run["completion_state"] == "qualified-draft"

    def check(content):
        # Deterministic interleaving after assess checked standard.digest: the manifest handed out a criterion's
        # own fields, and renaming the missing obligation to c's id made it reuse c's evidence.
        public_manifest["criteria"][1]["id"] = "c"
        return content == b"GOOD"

    verifier = replace(case3["verifiers"]["checker"], check=check)
    result = case3["orchestrator"].assess(
        compiled, standard=standard, run_result=run, evaluator=case3["evaluator"],
        verifiers={"checker": verifier}, artifacts=case3["artifacts"],
        documentation=case3["documentation"], candidate_actor=case3["candidate_actor"])
    assert result["completion_state"] == "blocked-escalated", (
        "assessment credited a weakened standard: "
        f"compiled={original_pin}, evaluated={result['dossier']['standard_digest']}")
    assert standard.criteria[1].id == "missing" and standard.digest == original_pin


def test_finding_7_an_observation_updates_the_likelihood_model_that_was_selected():
    prediction = {"A": True, "B": False}
    probe = Probe("p", "measure", "read", prediction, .8)

    def measure(question, arguments):
        prediction.update(A=False, B=True)  # deterministic interleaving while the observed tool is in flight
        return Observation(True, "fake:measurement")

    result = CognitiveAgent(StaticProposer([probe]), {"read": ToolSpec("read", measure)}).run(
        "measure", [Hypothesis("A", "a", .5), Hypothesis("B", "b", .5)], budget=1, max_steps=1)
    if result["completion_state"] == "blocked-escalated":
        return
    assert result["posterior"]["A"] == .8, (
        f"positive observation credited the post-selection model: posterior={result['posterior']}")


def test_an_assessment_refuses_a_dossier_for_a_standard_other_than_the_one_compiled(case3):
    # The re-read, on its own: an evaluator whose dossier is for another standard (its digest) is not credited.
    c = case3
    other = replace(c["standard"], edition="2")

    class Elsewhere(InProcessAssuranceEvaluator):
        def evaluate(self, *, standard, **kwargs):
            return super().evaluate(standard=other, **kwargs)

    run = c["orchestrator"].execute(c["compiled"], approved=True)
    common = dict(standard=c["standard"], run_result=run, verifiers=c["verifiers"], artifacts=c["artifacts"],
                  documentation=c["documentation"], candidate_actor=c["candidate_actor"])
    assert c["orchestrator"].assess(c["compiled"], evaluator=c["evaluator"], **common)["completion_state"] == \
        "qualified-draft"
    out = c["orchestrator"].assess(c["compiled"], evaluator=Elsewhere(evaluator_id="outside"), **common)
    assert out["completion_state"] == "blocked-escalated"
    assert "standard evaluated is not the standard compiled" in out["reason"]


def test_inventory_finding_a_returned_failure_finding_is_not_the_finding_itself():
    # Found by the inventory (no vars() view): the failure model handed out each finding's own __dict__.
    finding = FailureModeFinding("false_confidence", "c", "a" * 64, "store:e", "failed", "critical")
    out = CompositionalFailureModel().map([finding], [])
    try:
        out["observed"][0]["code"] = "renamed"
    except TypeError:
        pass
    assert finding.code == "false_confidence", "a returned finding was a live view of the finding"


def test_inventory_finding_a_selection_row_handed_out_is_not_the_row_the_decision_keeps(case3):
    decision = case3["compiled"].decision
    rows = decision.to_dict()["ordered_variants"]
    try:
        rows[0]["variant_id"] = "renamed"
    except TypeError:
        pass
    assert decision.ordered_variants[0]["variant_id"] == "v", "the decision handed out its own row"


def test_a_dossier_payload_is_a_snapshot_and_its_digest_still_reads_the_same(case3):
    out = case3["evaluator"].evaluate(
        standard=case3["standard"], artifacts=case3["artifacts"], verifiers=case3["verifiers"],
        documentation=case3["documentation"], candidate_actor=case3["candidate_actor"])
    assert out.intact()
    try:
        out.payload["checks"][0]["result"] = "failed"
    except TypeError:
        pass
    assert out.payload["checks"][0]["result"] == "passed" and out.intact()
    copy = json.loads(json.dumps(out.to_dict()))  # the snapshot renders as the same JSON, so the same digest
    plain = CandidateDossier.from_payload({k: v for k, v in copy.items() if k != "content_sha256"})
    assert plain.content_sha256 == out.content_sha256 == copy["content_sha256"]
