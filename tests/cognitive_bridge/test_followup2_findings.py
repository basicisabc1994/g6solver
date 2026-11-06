# SPDX-License-Identifier: Apache-2.0
"""Follow-up review 2's eight findings, brought in as the bridge's own tests. Findings 1 to 5 are
untrusted_input_given_authority: a claim of a state below the top of an answer (a selection's limitations, a probe's
question or arguments, a tool's notes, an LLM's model) reached an output. Findings 6 and 7 are the same class through a
nested result outside the makers' format (a blocked dossier or reconciliation with an unregistered code and no card).
Finding 8 is failure_read_as_success: an approval read by truthiness."""
from __future__ import annotations

import json
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest
from returns.result import Success

from g6.cognitive_bridge import (
    DOCUMENT_FIELDS, CandidateDossier, ClaimIR, CognitiveAgent, CognitiveAssuranceOrchestrator, Criterion,
    CrossMethodConflictGate, DomainStandard, EvidenceArtifact, FrozenVerifier, Hypothesis, InProcessAssuranceEvaluator,
    LLMClaimCompiler, LLMProposer, MetacognitiveMethodSelector, MethodVariant, MixedMethodExecutor, Observation, Probe,
    ProblemSignature, ScopedAssertion, StaticProposer, ToolSpec,
)
from g6.cognitive_bridge import codes
from g6.cognitive_bridge.codes import CODES
from g6.core.reliability_envelope import warning_card


@pytest.fixture
def research_case():
    """Follow-up review 2's case, as its conftest made it."""
    class Registry:
        def invoke(self, name, op, params=None, **kwargs):
            return Success({"completion_state": "qualified-draft", "answer": {"valid": True}})

    standard = DomainStandard("review", "1", "AU", "rubric", "a" * 64,
                              (Criterion("c", "formal obligation", "checker", "1:1",
                                         required_methods=("formal",)),))
    variant = MethodVariant("v", "formal", "SMT", "formal", "check_validity", 1)
    context = ProblemSignature("review", "logic", "validity", 1, 2)
    claim = ClaimIR("claim", "A entails A", ("formal",), {})
    orch = CognitiveAssuranceOrchestrator(MetacognitiveMethodSelector(), registry=Registry())
    compiled = orch.compile(claim=claim, context=context, standard=standard, variants=[variant],
                            variant_models={"v": {"formula": "A", "premises": ["A"]}})
    return dict(
        standard=standard, variant=variant, context=context, claim=claim, compiled=compiled,
        orchestrator=orch, evaluator=InProcessAssuranceEvaluator(evaluator_id="outside"),
        artifacts={"c": EvidenceArtifact("e", b"GOOD", "report", "candidate", "store:e", "claim")},
        verifiers={"checker": FrozenVerifier("checker", "outside", "b" * 64,
                                             lambda b: b == b"GOOD", (b"GOOD",), (b"BAD",))},
        documentation={name: "Explicit scope for " + name for name in DOCUMENT_FIELDS},
        candidate_actor="candidate",
    )


# --- Findings 1 to 5: no claim of a state at any depth of an answer ---------------------------------------------------

def test_finding_1_selection_metadata_cannot_republish_verified_claims(research_case):
    c = research_case

    class ClaimingSelector(MetacognitiveMethodSelector):
        def select(self, *args, **kwargs):
            honest = super().select(*args, **kwargs)
            return replace(honest, limitations=({"verified": True, "reliability_label": "verified"},))

    try:
        compiled = CognitiveAssuranceOrchestrator(ClaimingSelector()).compile(
            claim=c["claim"], context=c["context"], standard=c["standard"], variants=[c["variant"]],
            variant_models={"v": {"formula": "A", "premises": ["A"]}})
        out = compiled.to_dict()
    except (TypeError, ValueError):
        return  # Refusing an answer outside its format is acceptable.
    assert out["completion_state"] == "qualified-draft"
    carried = out["metacognitive_selection"]["limitations"][0]
    assert "verified" not in carried and "reliability_label" not in carried, \
        "selector metadata republished verified=True and reliability_label=verified"


def test_finding_2_proposer_question_cannot_republish_verified_claims():
    try:
        probe = Probe("p", {"verified": True, "reliability_label": "verified"}, "read",
                      {"A": True, "B": False}, .8)
        out = CognitiveAgent(StaticProposer([probe]), {
            "read": ToolSpec("read", lambda q, args: Observation(True, "source:actual"))
        }).run("measure", [Hypothesis("A", "a", .5), Hypothesis("B", "b", .5)], budget=1, max_steps=1)
    except (TypeError, ValueError):
        return
    if not out["history"]:
        assert out["completion_state"] == "blocked-escalated"
        return
    carried = out["history"][0]["question"]
    assert "verified" not in carried and "reliability_label" not in carried, \
        "proposer question republished verified=True and reliability_label=verified"


def test_finding_3_tool_notes_cannot_carry_a_bare_verified_flag():
    probe = Probe("p", "measure", "read", {"A": True, "B": False}, .8)
    try:
        out = CognitiveAgent(StaticProposer([probe]), {
            "read": ToolSpec("read", lambda q, args: Observation(True, "source:actual", notes={"verified": True}))
        }).run("measure", [Hypothesis("A", "a", .5), Hypothesis("B", "b", .5)], budget=1, max_steps=1)
    except (TypeError, ValueError):
        return
    if not out["history"]:
        assert out["completion_state"] == "blocked-escalated"
        return
    assert "verified" not in out["history"][0]["notes"], "tool notes left verified=False outside a warning card"


def test_finding_4_claim_compiler_cannot_republish_model_state_fields():
    response = {"claims": [{"id": "c", "statement": "Check A", "evidence_needs": ["formal"],
                            "models": {"formal": {"formula": {"verified": True,
                                                              "reliability_label": "verified"}}}}]}
    model = SimpleNamespace(complete=lambda *args, **kwargs: json.dumps(response))
    try:
        out = LLMClaimCompiler(model).draft("Does A hold?").to_dict()
    except (TypeError, ValueError):
        return
    carried = out["steps"][0]["params"]["formula"]
    assert "verified" not in carried and "reliability_label" not in carried, \
        "LLM formula republished verified=True and reliability_label=verified"


def test_finding_5_llm_probe_arguments_cannot_carry_verified_claims():
    response = {"probes": [{"id": "p", "question": "Measure", "tool": "read",
                            "arguments": {"verified": True, "reliability_label": "verified"},
                            "positive_if": {"A": True, "B": False}, "novelty": 0}]}
    model = SimpleNamespace(complete=lambda *args, **kwargs: json.dumps(response))
    tools = {"read": ToolSpec("read", lambda q, a: Observation(True, "source:actual"))}
    try:
        proposals = LLMProposer(model, tools).propose(
            "measure", [Hypothesis("A", "a", .5), Hypothesis("B", "b", .5)], [])
    except (TypeError, ValueError):
        return
    carried = asdict(proposals[0])["arguments"]
    assert "verified" not in carried and "reliability_label" not in carried, \
        "LLM probe arguments republished verified=True and reliability_label=verified"


# --- Findings 6 and 7: a nested result has the makers' format ---------------------------------------------------------

def test_finding_6_blocked_dossier_cannot_publish_an_unregistered_cardless_refusal(research_case):
    c = research_case

    class CardlessEvaluator(InProcessAssuranceEvaluator):
        def evaluate(self, **kwargs):
            honest = super().evaluate(**kwargs)
            payload = dict(honest.payload)
            assert payload["completion_state"] == "blocked-escalated"
            payload["code"] = "G6_E_REVIEW_UNREGISTERED"
            del payload["warning_card"]
            return CandidateDossier.from_payload(payload)

    run = c["orchestrator"].execute(c["compiled"], approved=True)
    try:
        out = c["orchestrator"].assess(
            c["compiled"], standard=c["standard"], run_result=run,
            evaluator=CardlessEvaluator(evaluator_id="outside"), artifacts={},
            verifiers=c["verifiers"], documentation=c["documentation"], candidate_actor=c["candidate_actor"])
    except (TypeError, ValueError):
        return
    carried = out["dossier"]
    assert carried["code"] in CODES and carried.get("warning_card", {}).get("code") == carried["code"], \
        "blocked dossier republished an unregistered code with no warning card"


def test_finding_7_blocked_reconciliation_cannot_publish_an_unregistered_cardless_refusal(research_case):
    c = research_case

    class CardlessGate(CrossMethodConflictGate):
        def reconcile(self, assertions):
            out = super().reconcile(assertions)
            assert out["completion_state"] == "blocked-escalated"
            out["code"] = "G6_E_REVIEW_UNREGISTERED"
            del out["warning_card"]
            return out

    run = c["orchestrator"].execute(c["compiled"], approved=True)
    assertion = ScopedAssertion("claim", "p", "a" * 64, "supports", "formal", "source:1", "receipt:1")
    try:
        out = c["orchestrator"].assess(
            c["compiled"], standard=c["standard"], run_result=run,
            evaluator=c["evaluator"], artifacts=c["artifacts"], verifiers=c["verifiers"],
            documentation=c["documentation"], candidate_actor=c["candidate_actor"],
            scoped_assertions=[assertion], epistemic_gate=CardlessGate())
    except (TypeError, ValueError):
        return
    carried = out["epistemic_reconciliation"]
    assert carried["code"] in CODES and carried.get("warning_card", {}).get("code") == carried["code"], \
        "blocked reconciliation republished an unregistered code with no warning card"


#: A blocked result in each shape item 2 names: an unregistered code (with a card for it), no card, a card for another
#: code, and the format codes.refusal makes.
BLOCKED = {
    "unregistered": lambda made: {**made, "code": "G6_E_REVIEW_UNREGISTERED",
                                  "warning_card": warning_card("G6_E_REVIEW_UNREGISTERED", "unregistered")},
    "missing-card": lambda made: {k: v for k, v in made.items() if k != "warning_card"},
    "another-codes-card": lambda made: {**made, "warning_card": warning_card(codes.INPUT, "another code's card")},
    "well-formed": lambda made: dict(made),
}


@pytest.mark.parametrize("shape", sorted(BLOCKED))
def test_one_check_decides_the_format_for_the_makers_and_the_rule(shape):
    result = BLOCKED[shape](codes.refusal(codes.DOSSIER_BLOCKED, "a reason"))
    faults = codes.result_faults(result)
    assert (faults == []) is (shape == "well-formed"), faults
    # the makers take a refusal handed back only in that format, and the rule reads a nested one by the same check
    if faults:
        with pytest.raises(ValueError):
            codes.settled(result)
        with pytest.raises(ValueError):
            codes.refusal(result["code"], "a reason", card=result.get("warning_card", "no card"))
    else:
        assert codes.settled(result) == result
    assert bool(codes.claims({"nested": [result]})) is bool(faults)


@pytest.mark.parametrize("shape", sorted(BLOCKED))
def test_a_blocked_dossier_an_evaluator_returns_has_the_makers_format(research_case, shape):
    c = research_case

    class Evaluator(InProcessAssuranceEvaluator):
        def evaluate(self, **kwargs):
            honest = super().evaluate(**kwargs)
            assert honest.payload["completion_state"] == "blocked-escalated"
            return CandidateDossier.from_payload(BLOCKED[shape](honest.payload))

    run = c["orchestrator"].execute(c["compiled"], approved=True)

    def assess():
        return c["orchestrator"].assess(
            c["compiled"], standard=c["standard"], run_result=run, evaluator=Evaluator(evaluator_id="outside"),
            artifacts={}, verifiers=c["verifiers"], documentation=c["documentation"],
            candidate_actor=c["candidate_actor"])

    if shape != "well-formed":
        with pytest.raises(ValueError):
            assess()
        return
    carried = assess()["dossier"]
    assert carried["code"] in CODES and carried["warning_card"]["code"] == carried["code"]


@pytest.mark.parametrize("shape", sorted(BLOCKED))
def test_a_blocked_reconciliation_a_gate_returns_has_the_makers_format(research_case, shape):
    c = research_case

    class Gate(CrossMethodConflictGate):
        def reconcile(self, assertions):
            honest = super().reconcile(assertions)
            assert honest["completion_state"] == "blocked-escalated"
            return BLOCKED[shape](honest)

    run = c["orchestrator"].execute(c["compiled"], approved=True)
    assertion = ScopedAssertion("claim", "p", "a" * 64, "supports", "formal", "source:1", "receipt:1")

    def assess():
        return c["orchestrator"].assess(
            c["compiled"], standard=c["standard"], run_result=run, evaluator=c["evaluator"],
            artifacts=c["artifacts"], verifiers=c["verifiers"], documentation=c["documentation"],
            candidate_actor=c["candidate_actor"], scoped_assertions=[assertion], epistemic_gate=Gate())

    if shape != "well-formed":
        with pytest.raises(ValueError):
            assess()
        return
    carried = assess()["epistemic_reconciliation"]
    assert carried["code"] in CODES and carried["warning_card"]["code"] == carried["code"]


#: A qualified-draft result claims nothing more: its card, if it has one, is a G6 card that claims nothing.
DRAFTS = {
    "card-says-verified": {"completion_state": "qualified-draft",
                           "warning_card": {**warning_card(codes.NOT_PROMOTED, "m"), "verified": True}},
    "card-has-a-label": {"completion_state": "qualified-draft",
                         "warning_card": {**warning_card(codes.NOT_PROMOTED, "m"), "reliability_label": "verified"}},
    "card-without-a-code": {"completion_state": "qualified-draft", "warning_card": {"message": "no code"}},
    "not-a-state": {"completion_state": "verified"},
    "no-card": {"completion_state": "qualified-draft", "answer": 1},
    "a-card-that-claims-nothing": {"completion_state": "qualified-draft",
                                   "warning_card": warning_card("G6_E_QUALIFIED_DRAFT", "a draft")},
}


@pytest.mark.parametrize("shape", sorted(DRAFTS))
def test_a_qualified_draft_claims_nothing_more(shape):
    well_formed = shape in ("no-card", "a-card-that-claims-nothing")
    assert (codes.claims({"nested": DRAFTS[shape]}) == []) is well_formed


# --- Finding 8 and item 3: approval is True and nothing else -----------------------------------------------------------

@pytest.mark.parametrize("entry", ["executor", "orchestrator"])
def test_finding_8_false_text_is_not_execution_approval(research_case, entry):
    calls = []

    class Registry:
        def invoke(self, name, op, params=None, **kwargs):
            calls.append((name, op))
            return Success({"completion_state": "qualified-draft", "answer": {"valid": True}})

    c = research_case
    registry = Registry()
    try:
        if entry == "executor":
            out = MixedMethodExecutor(registry).execute(c["compiled"].executable_plan, approved="false")
        else:
            orch = CognitiveAssuranceOrchestrator(MetacognitiveMethodSelector(), registry=registry)
            out = orch.execute(c["compiled"], approved="false")
    except (TypeError, ValueError):
        assert calls == []
        return
    assert calls == [], 'approved="false" executed formal.check_validity'
    assert out["completion_state"] == "blocked-escalated"


class _Truthy:
    def __bool__(self):
        return True


@pytest.mark.parametrize("approval", ["false", 1, "yes", _Truthy(), True], ids=["false-text", "one", "yes", "truthy",
                                                                                 "True"])
@pytest.mark.parametrize("entry", ["executor", "orchestrator"])
def test_only_true_approves_execution(research_case, entry, approval):
    calls = []

    class Registry:
        def invoke(self, name, op, params=None, **kwargs):
            calls.append((name, op))
            return Success({"completion_state": "qualified-draft", "answer": {"valid": True}})

    c = research_case
    if entry == "executor":
        out = MixedMethodExecutor(Registry()).execute(c["compiled"].executable_plan, approved=approval)
        refused = codes.APPROVAL_REQUIRED
    else:
        orch = CognitiveAssuranceOrchestrator(MetacognitiveMethodSelector(), registry=Registry())
        out = orch.execute(c["compiled"], approved=approval)
        refused = codes.EXECUTION_NOT_APPROVED
    if approval is True:
        assert calls == [("formal", "check_validity")] and out["completion_state"] == "qualified-draft"
    else:
        assert calls == [], f"approved={approval!r} ran a step"
        assert out["completion_state"] == "blocked-escalated" and out["code"] == refused
