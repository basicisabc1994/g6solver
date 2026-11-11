# SPDX-License-Identifier: Apache-2.0
"""Follow-up review 4's four findings and repair 3's four open items, as the bridge's own tests (each reproduced on
7b5bc1a). Their classes, ``untrusted_input_given_authority`` and ``guard_covers_less_than_it_guards`` above all, came
back in every review: the bridge took arbitrary Python objects and caller-built records, and each repair taught a check
one more shape. They are removed at the root: every value that enters is converted once into plain JSON data
(``snapshot.plain``), and execution and assessment honour only the records this instance issued
(``snapshot.issued``). ``test_boundary_inventory.py`` lists every place from the code."""
from __future__ import annotations

from collections import deque
from dataclasses import replace

import pytest
from returns.result import Success

from g6.cognitive_bridge import (
    DOCUMENT_FIELDS, ClaimIR, CognitiveAssuranceOrchestrator, Criterion, DomainStandard, EvidenceArtifact,
    FrozenVerifier, InProcessAssuranceEvaluator, MetacognitiveMethodSelector, MethodRouter, MethodVariant,
    MixedMethodExecutor, ProblemSignature, SelectionDecision, codes,
)
from g6.cognitive_bridge.snapshot import record


class _Registry:
    def __init__(self, answer=None):
        self.calls, self.answer = [], answer

    def invoke(self, name, op, params=None, **kwargs):
        self.calls.append((name, op, params))
        answer = {"valid": True} if self.answer is None else self.answer
        return Success({"completion_state": "qualified-draft", "answer": answer})


@pytest.fixture
def research():
    """Review 4's case: a compiled formal claim, a registry that answers, and an assessment's evidence."""
    standard = DomainStandard("review", "1", "AU", "rubric", "a" * 64,
                              (Criterion("c", "formal obligation", "checker", "1:1", required_methods=("formal",)),))
    context = ProblemSignature("review", "logic", "validity", 1, 2)
    variant = MethodVariant("v", "formal", "SMT", "formal", "check_validity", 1)
    registry, selector = _Registry(), MetacognitiveMethodSelector()
    orch = CognitiveAssuranceOrchestrator(selector, registry=registry)
    claim = ClaimIR("claim", "A entails A", ("formal",), {})
    compiled = orch.compile(claim=claim, context=context, standard=standard, variants=[variant],
                            variant_models={"v": {"formula": "A", "premises": ["A"]}})
    evaluation = dict(
        standard=standard, evaluator=InProcessAssuranceEvaluator(evaluator_id="outside"),
        artifacts={"c": EvidenceArtifact("e", b"GOOD", "report", "candidate", "store:e", "claim")},
        verifiers={"checker": FrozenVerifier("checker", "outside", "b" * 64, lambda b: b == b"GOOD", (b"GOOD",),
                                             (b"BAD",))},
        documentation={name: "Explicit scope and limitations" for name in DOCUMENT_FIELDS}, candidate_actor="candidate")
    return dict(orch=orch, registry=registry, selector=selector, standard=standard, context=context, variant=variant,
                claim=claim, compiled=compiled, evaluation=evaluation)


def _refused_as_input(excinfo, where: str) -> None:
    """A refusal at the boundary: a registered code, and the path of what was refused."""
    assert getattr(excinfo.value, "code", None) == codes.INPUT, excinfo.value
    assert where in str(excinfo.value), excinfo.value


# --- Finding 1 (untrusted_input_given_authority) --------------------------------------------------------------------

@pytest.mark.parametrize("selection", ["reviewer-only", "empty"])
def test_finding_1_a_compilation_edited_to_another_selection_neither_runs_nor_is_credited(research, selection):
    c = research
    reviewer = MethodVariant("reviewer", "professional_review", "human", "reviewer", "review", 1, kind="reviewer")
    decision = c["selector"].select(c["context"], [reviewer], required_methods=["professional_review"])
    if selection == "empty":
        decision = replace(c["compiled"].decision, ordered_variants=())
    forged = replace(c["compiled"], decision=decision)
    out = c["orch"].execute(forged, approved=True)
    assert c["registry"].calls == [], f"a {selection} selection executed formal.check_validity"
    assert out["completion_state"] == "blocked-escalated" and out["code"] == codes.EXECUTION_NOT_APPROVED
    assessment = c["orch"].assess(forged, run_result=out, **c["evaluation"])
    assert assessment["completion_state"] == "blocked-escalated" and assessment["code"] == codes.ASSURANCE_BLOCKED


# --- Finding 2 (identity_from_rendering) -----------------------------------------------------------------------------

@pytest.mark.parametrize("field", ["artifacts", "verifiers"])
def test_finding_2_a_mapping_whose_keys_are_not_text_is_refused_where_it_enters(research, field):
    c = research
    criterion = replace(c["standard"].criteria[0], id="1", verifier_id="1")
    standard = replace(c["standard"], criteria=(criterion,))
    inputs = {k: v for k, v in c["evaluation"].items() if k != "evaluator"}
    inputs.update(standard=standard, artifacts={"1": inputs["artifacts"]["c"]},
                  verifiers={"1": replace(inputs["verifiers"]["checker"], id="1")})
    compiled = c["orch"].compile(claim=c["claim"], context=c["context"], standard=standard, variants=[c["variant"]],
                                 variant_models={"v": {"formula": "A", "premises": ["A"]}})
    evaluator = c["evaluation"]["evaluator"]
    genuine = evaluator.evaluate(**inputs)
    assert genuine.payload["completion_state"] == "qualified-draft"
    changed = {**inputs, field: {1: inputs[field]["1"]}}
    with pytest.raises(ValueError) as excinfo:
        evaluator.evaluate(**changed)
    _refused_as_input(excinfo, field)

    class Replay:
        def evaluate(self, **kwargs):
            return genuine

    run = c["orch"].execute(compiled, approved=True)
    with pytest.raises(ValueError) as excinfo:
        c["orch"].assess(compiled, run_result=run, evaluator=Replay(), **changed)
    _refused_as_input(excinfo, field)
    # the same dossier, replayed for the very evidence it was made for, is still not one this evaluator issued
    out = c["orch"].assess(compiled, run_result=run, evaluator=Replay(), **inputs)
    assert out["completion_state"] == "blocked-escalated" and "not one this evaluator" in out["reason"]


# --- Finding 3 (guard_covers_less_than_it_guards) --------------------------------------------------------------------

class _LabelledSet(set):
    def __init__(self):
        super().__init__([1])
        self.verified, self.reliability_label = True, "verified"


class _LabelledDeque(deque):
    def __init__(self):
        super().__init__([1])
        self.verified, self.reliability_label = True, "verified"


class _LabelledList(list):
    def __init__(self):
        super().__init__([1])
        self.verified, self.reliability_label = True, "verified"


class _LabelledDict(dict):
    def __init__(self):
        super().__init__(k=1)
        self.verified, self.reliability_label = True, "verified"


def _plan():
    return MethodRouter().plan([ClaimIR("c", "check A", ("formal",), {"formal": {"formula": "A"}})])


@pytest.mark.parametrize("container", [_LabelledSet, _LabelledDeque])
def test_finding_3_a_container_subclass_in_a_tools_answer_fails_its_step(container):
    out = MixedMethodExecutor(_Registry(answer=container())).execute(_plan(), approved=True)
    step = out["steps"][0]
    assert out["completion_state"] == "blocked-escalated" and step["code"] == codes.STEP_FAILED, step
    assert "result" not in step and "answer" in step["reason"], step["reason"]


@pytest.mark.parametrize("container", [_LabelledList, _LabelledDict])
def test_finding_3_a_credited_answer_is_plain_data_with_nothing_unread(container):
    # G6's door hands a list or dict subclass on as its own plain copy; what the bridge credits is plain JSON data
    out = MixedMethodExecutor(_Registry(answer=container())).execute(_plan(), approved=True)
    if out["completion_state"] == "qualified-draft":
        carried = out["steps"][0]["result"]["answer"]
        assert type(carried) in (list, dict) and not hasattr(carried, "verified"), type(carried)
    else:
        assert out["steps"][0]["code"] == codes.STEP_FAILED


# --- Finding 4 (check_then_act_race: an identity that is not text) ---------------------------------------------------

def test_finding_4_an_identity_that_is_not_text_is_refused_where_it_enters(research):
    c = research
    producer = {"id": "candidate"}

    def check(content):
        producer["id"] = "outside"
        return content == b"GOOD"

    with pytest.raises(ValueError) as excinfo:
        InProcessAssuranceEvaluator(evaluator_id={"id": "outside"})
    _refused_as_input(excinfo, "evaluator")
    with pytest.raises(ValueError) as excinfo:
        replace(c["evaluation"]["artifacts"]["c"], claimed_by=producer)
    _refused_as_input(excinfo, "EvidenceArtifact.claimed_by")
    verifier = FrozenVerifier("checker", "outside", "b" * 64, check, (b"GOOD",), (b"BAD",))
    with pytest.raises(ValueError) as excinfo:
        c["evaluation"]["evaluator"].evaluate(standard=c["standard"], artifacts=c["evaluation"]["artifacts"],
                                              verifiers={"checker": verifier},
                                              documentation=c["evaluation"]["documentation"], candidate_actor=producer)
    _refused_as_input(excinfo, "producer")
    assert producer == {"id": "candidate"}, "the checker ran before the identity was refused"


# --- Repair 3's open items --------------------------------------------------------------------------------------------

def _decision(research, **changes):
    return replace(research["compiled"].decision, **changes)


def test_open_1_a_decisions_refusal_and_missing_methods_cannot_disagree(research):
    blocked = MetacognitiveMethodSelector().select(ProblemSignature("review", "logic", "validity", 1, 0),
                                                   [research["variant"]], required_methods=["formal"])
    assert blocked.missing and blocked.refused is not None
    for changes in ({"missing": ("formal",)}, {"refused": blocked.refused}):  # on a qualified decision
        with pytest.raises(ValueError, match="refused exactly"):
            _decision(research, **changes)
    for changes in ({"missing": ()}, {"refused": None}):  # on a blocked one
        with pytest.raises(ValueError, match="refused exactly"):
            replace(blocked, **changes)
    with pytest.raises(ValueError, match="refused exactly"):  # made by hand, not through replace
        SelectionDecision((), ("formal",), ("formal",), 0, 0, "x", None)


def test_open_2_a_compilations_context_is_bound_to_it(research):
    c = research
    other = replace(c["context"], risk=0, claim_kind="something else")
    forged = replace(c["compiled"], context=other)
    out = c["orch"].execute(forged, approved=True)
    assert c["registry"].calls == [] and out["code"] == codes.EXECUTION_NOT_APPROVED
    run = c["orch"].execute(c["compiled"], approved=True)
    assessment = c["orch"].assess(forged, run_result=run, **c["evaluation"])
    assert assessment["completion_state"] == "blocked-escalated" and assessment["code"] == codes.ASSURANCE_BLOCKED
    assert c["orch"].assess(c["compiled"], run_result=run, **c["evaluation"])["completion_state"] == "qualified-draft"


@pytest.mark.parametrize("kind", ["deque", "set", "frozenset"])
def test_open_3_a_deque_or_set_in_a_tools_answer_is_refused_not_shared(kind):
    answer = {"deque": deque([1]), "set": {1}, "frozenset": frozenset({1})}[kind]
    out = MixedMethodExecutor(_Registry(answer={"items": answer})).execute(_plan(), approved=True)
    step = out["steps"][0]
    assert step["code"] == codes.STEP_FAILED and "result" not in step, step


def test_open_3_a_tools_plain_answer_is_a_copy_the_registry_does_not_hold():
    answer = {"items": [1, {"k": [2]}]}
    out = MixedMethodExecutor(_Registry(answer=answer)).execute(_plan(), approved=True)
    assert out["completion_state"] == "qualified-draft"
    answer["items"][1]["k"].append("changed by the registry after the step")
    assert out["steps"][0]["result"]["answer"] == {"items": [1, {"k": [2]}]}


@record
class _Holder:
    note: str = "clean"


@pytest.mark.parametrize("kind", ["record", "set", "deque"])
def test_open_4_a_record_set_or_deque_in_a_tools_answer_is_a_refused_step_not_an_exception(research, kind):
    c = research
    c["registry"].answer = {"held": {"record": _Holder(), "set": {1}, "deque": deque([1])}[kind]}
    out = c["orch"].execute(c["compiled"], approved=True)  # canonical_json of the run raised TypeError on 7b5bc1a
    assert out["completion_state"] == "blocked-escalated" and out["steps"][0]["code"] == codes.STEP_FAILED
