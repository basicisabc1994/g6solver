# SPDX-License-Identifier: Apache-2.0
"""Follow-up review 1's five findings, brought in as the bridge's own tests. Findings 1, 2, 3 and 5 are
untrusted_input_given_authority (an answer credited for a subject it is not bound to, or republished with fields nothing
checked); finding 4 is failure_read_as_success (a collaborator's blocking ``reliability_label`` read as a pass)."""
from __future__ import annotations

from dataclasses import replace

import pytest

from g6.cognitive_bridge import (
    CandidateDossier, CognitiveAssuranceOrchestrator, CrossMethodConflictGate, InProcessAssuranceEvaluator,
    MetacognitiveMethodSelector, MetacognitivePlanner, MethodVariant, ProblemSignature, ScopedAssertion,
)
from g6.core.door import said_state


@pytest.fixture
def case(case3):
    variant = MethodVariant("v", "formal", "SMT", "formal", "check_validity", 1)
    run = case3["orchestrator"].execute(case3["compiled"], approved=True)
    assert run["completion_state"] == "qualified-draft"
    return {**case3, "run_result": run, "variant": variant, "claim": case3["compiled"].original_claim,
            "context": ProblemSignature("review", "logic", "validity", 1, 2)}


@pytest.fixture
def assessment(case):
    def assess(**changes):
        kwargs = {k: case[k] for k in ("standard", "run_result", "evaluator", "verifiers",
                                      "artifacts", "documentation", "candidate_actor")}
        kwargs.update(changes)
        return case["orchestrator"].assess(case["compiled"], **kwargs)
    return assess


def _assertion():
    return ScopedAssertion("claim", "switch-on", "a" * 64, "supports", "formal", "source:1", "receipt:1")


# --- Finding 1: a dossier is credited only for the evidence it assessed ---------------------------------------------

def test_finding_1_assess_binds_the_dossier_to_the_artifacts_submitted_now(case, assessment):
    keys = ("standard", "artifacts", "verifiers", "documentation", "candidate_actor")
    cached = case["evaluator"].evaluate(**{k: case[k] for k in keys})
    assert cached.payload["completion_state"] == "qualified-draft"
    bad_artifacts = {"c": replace(case["artifacts"]["c"], content=b"BAD")}
    assert assessment(artifacts=bad_artifacts)["completion_state"] == "blocked-escalated"

    class CachedEvaluator(InProcessAssuranceEvaluator):
        def evaluate(self, **kwargs):
            return cached

    assert cached.payload["checks"][0]["artifact"]["sha256"] != bad_artifacts["c"].digest
    try:
        out = assessment(artifacts=bad_artifacts, evaluator=CachedEvaluator(evaluator_id="outside"))
    except (TypeError, ValueError):
        return  # A mismatched evaluator response may be refused before assessment.
    assert out["completion_state"] == "blocked-escalated", "dossier for GOOD was credited for BAD"


# --- Finding 2: a reconciliation is credited only for the assertions it reconciled ----------------------------------

def test_finding_2_assess_binds_reconciliation_to_all_submitted_assertions(case, assessment):
    supports = _assertion()
    refutes = replace(supports, stance="refutes", method="causal", evidence_ref="source:2", evaluator_receipt="receipt:2")
    real = CrossMethodConflictGate(admit_assertion=lambda a: a in (supports, refutes))
    cached = real.reconcile([supports])
    assert cached["completion_state"] == "qualified-draft"
    assert assessment(scoped_assertions=[supports, refutes], epistemic_gate=real)["completion_state"] == \
        "blocked-escalated"

    class CachedGate(CrossMethodConflictGate):
        def reconcile(self, assertions):
            return cached

    assert cached["scoped_findings"][0]["source_count"] == 1
    try:
        out = assessment(scoped_assertions=[supports, refutes], epistemic_gate=CachedGate())
    except (TypeError, ValueError):
        return  # A response covering a different assertion set may be refused.
    assert out["completion_state"] == "blocked-escalated", "one-sided report was credited for contested assertions"


# --- Finding 3: a selection runs only when it is eligible under the context it is compiled for -----------------------

@pytest.mark.parametrize("constraint", ["budget", "available_evidence"])
def test_finding_3_compiler_does_not_credit_an_ineligible_cached_selection(case, constraint):
    variant = case["variant"]
    eligible = case["context"]
    if constraint == "budget":
        context = replace(eligible, budget=0)
    else:
        variant = replace(variant, expects=("dataset",))
        context = eligible
        eligible = replace(eligible, available_evidence=("dataset",))
    selector = MetacognitiveMethodSelector()
    decision = selector.select(eligible, [variant], required_methods=["formal"])
    assert selector.select(context, [variant], required_methods=["formal"]).missing == ("formal",)

    class CachedSelector:
        def select(self, *args, **kwargs):
            return decision

    orchestrator = CognitiveAssuranceOrchestrator(CachedSelector(), registry=case["orchestrator"].registry)
    try:
        compiled = orchestrator.compile(claim=case["claim"], context=context, standard=case["standard"],
                                        variants=[variant], variant_models={"v": {"formula": "A", "premises": ["A"]}})
    except (TypeError, ValueError):
        return
    run = orchestrator.execute(compiled, approved=True)
    assert run["completion_state"] == "blocked-escalated", f"cached selection bypassed {constraint}"


# --- Finding 4: a collaborator's answer that G6 reads as blocked never passes ---------------------------------------

@pytest.mark.parametrize("collaborator", ["evaluator", "conflict_gate"])
def test_finding_4_assess_respects_a_collaborators_blocking_reliability_label(case, assessment, collaborator):
    assertion = _assertion()

    class EnvelopeGate(CrossMethodConflictGate):
        def reconcile(self, assertions):
            out = super().reconcile(assertions)
            return {**out, "reliability_label": "blocked-escalated"}

    class EnvelopeEvaluator(InProcessAssuranceEvaluator):
        def evaluate(self, **kwargs):
            dossier = super().evaluate(**kwargs)
            payload = {**dossier.payload, "reliability_label": "blocked-escalated"}
            assert said_state(payload) == "blocked-escalated"
            return CandidateDossier.from_payload(payload)

    gate = EnvelopeGate(admit_assertion=lambda a: a == assertion)
    assert said_state(gate.reconcile([assertion])) == "blocked-escalated"
    changes = ({"evaluator": EnvelopeEvaluator(evaluator_id="outside")} if collaborator == "evaluator" else
               {"scoped_assertions": [assertion], "epistemic_gate": gate})
    try:
        out = assessment(**changes)
    except (TypeError, ValueError):
        return
    assert out["completion_state"] == "blocked-escalated", "assessment ignored the collaborator's blocking reliability_label"


# --- Finding 5: an output carries no reliability field a collaborator supplied --------------------------------------

@pytest.mark.parametrize("collaborator", ["evaluator", "conflict_gate", "selector_compile", "selector_plan"])
def test_finding_5_assess_does_not_republish_collaborator_supplied_reliability_claims(case, assessment, collaborator):
    assertion = _assertion()

    class ClaimingEvaluator(InProcessAssuranceEvaluator):
        def evaluate(self, **kwargs):
            dossier = super().evaluate(**kwargs)
            return CandidateDossier.from_payload({**dossier.payload, "verified": True, "reliability_label": "verified"})

    class ClaimingGate(CrossMethodConflictGate):
        def reconcile(self, assertions):
            return {**super().reconcile(assertions), "verified": True, "reliability_label": "verified"}

    class ClaimingSelector(MetacognitiveMethodSelector):
        def select(self, *args, **kwargs):
            decision = super().select(*args, **kwargs)
            return replace(decision, ordered_variants=tuple(
                {**row, "verified": True, "reliability_label": "verified"} for row in decision.ordered_variants))

    if collaborator.startswith("selector_"):
        try:
            if collaborator == "selector_compile":
                compiled = CognitiveAssuranceOrchestrator(ClaimingSelector()).compile(
                    claim=case["claim"], context=case["context"], standard=case["standard"], variants=[case["variant"]],
                    variant_models={"v": {"formula": "A", "premises": ["A"]}})
                selection = compiled.to_dict().get("metacognitive_selection", {})
            else:
                claim = replace(case["claim"], models={"formal": {"formula": "A", "premises": ["A"]}})
                selection = MetacognitivePlanner(ClaimingSelector()).plan(
                    [claim], case["context"], [case["variant"]], required_methods=["formal"]).get("selection", {})
        except (TypeError, ValueError):
            return
        rows = selection.get("ordered_variants", ())
        carried = rows[0] if rows else {}
        assert "verified" not in carried and carried.get("reliability_label") != "verified", \
            "selection republished unchecked verified fields"
        return

    changes = ({"evaluator": ClaimingEvaluator(evaluator_id="outside")} if collaborator == "evaluator" else
               {"scoped_assertions": [assertion],
                "epistemic_gate": ClaimingGate(admit_assertion=lambda a: a == assertion)})
    try:
        out = assessment(**changes)
    except (TypeError, ValueError):
        return
    carried = out.get("dossier", {}) if collaborator == "evaluator" else out.get("epistemic_reconciliation", {})
    assert "verified" not in carried and carried.get("reliability_label") != "verified", \
        "assess republished unchecked verified fields"
