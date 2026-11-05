# SPDX-License-Identifier: Apache-2.0
"""One compiled formal claim with its standard, artifact, checker and documentation, for the bridge's review tests; the
same with a registry that answers; and one guard change with the held-out pairs and controls that pass the gate."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def offline_door(monkeypatch):
    """G6's door asks for the model the person configured; these tests run no model, so it is none."""
    from g6.core import door

    monkeypatch.setattr(door, "person_model", lambda: None)


@pytest.fixture
def executed_case():
    """Review 2's case: a compiled formal claim whose registry answers every call qualified-draft."""
    from returns.result import Success

    from g6.cognitive_bridge import (
        ClaimIR, CognitiveAssuranceOrchestrator, Criterion, DomainStandard, EvidenceArtifact, FrozenVerifier,
        InProcessAssuranceEvaluator, MetacognitiveMethodSelector, MethodVariant, ProblemSignature,
    )
    from g6.cognitive_bridge.assurance import DOCUMENT_FIELDS

    class Registry:
        def __init__(self):
            self.calls = []

        def invoke(self, name, op, params=None, **kwargs):
            self.calls.append((name, op, params))
            return Success({"completion_state": "qualified-draft", "answer": {"valid": True}})

    standard = DomainStandard("review", "1", "AU", "rubric", "a" * 64,
                              (Criterion("c", "formal obligation", "checker", "1:1", required_methods=("formal",)),))
    registry = Registry()
    orchestrator = CognitiveAssuranceOrchestrator(MetacognitiveMethodSelector(), registry=registry)
    compiled = orchestrator.compile(
        claim=ClaimIR("claim", "A entails A", ("formal",), {}),
        context=ProblemSignature("review", "logic", "validity", 1, 2), standard=standard,
        variants=[MethodVariant("v", "formal", "SMT", "formal", "check_validity", 1)],
        variant_models={"v": {"formula": "A", "premises": ["A"]}})
    return dict(
        standard=standard, orchestrator=orchestrator, compiled=compiled, registry=registry,
        evaluator=InProcessAssuranceEvaluator(evaluator_id="outside"),
        artifacts={"c": EvidenceArtifact("e", b"GOOD", "report", "candidate", "store:original", "claim")},
        verifiers={"checker": FrozenVerifier("checker", "evaluator", "b" * 64, lambda b: b == b"GOOD", (b"GOOD",),
                                             (b"BAD",))},
        documentation={name: "Explicit limits for " + name for name in DOCUMENT_FIELDS},
        candidate_actor="candidate",
    )


@pytest.fixture
def case3():
    """Review 3's case: as review 2's, with the checker owned by the evaluator ``outside``."""
    from returns.result import Success

    from g6.cognitive_bridge import (
        DOCUMENT_FIELDS, ClaimIR, CognitiveAssuranceOrchestrator, Criterion, DomainStandard, EvidenceArtifact,
        FrozenVerifier, InProcessAssuranceEvaluator, MetacognitiveMethodSelector, MethodVariant, ProblemSignature,
    )

    class Registry:
        def invoke(self, name, op, params=None, **kwargs):
            return Success({"completion_state": "qualified-draft", "answer": {"valid": True}})

    criterion = Criterion("c", "formal obligation", "checker", "1:1", required_methods=("formal",))
    standard = DomainStandard("review", "1", "AU", "rubric", "a" * 64, (criterion,))
    orch = CognitiveAssuranceOrchestrator(MetacognitiveMethodSelector(), registry=Registry())
    compiled = orch.compile(
        claim=ClaimIR("claim", "A entails A", ("formal",), {}),
        context=ProblemSignature("review", "logic", "validity", 1, 2), standard=standard,
        variants=[MethodVariant("v", "formal", "SMT", "formal", "check_validity", 1)],
        variant_models={"v": {"formula": "A", "premises": ["A"]}})
    return dict(standard=standard, orchestrator=orch, compiled=compiled,
                evaluator=InProcessAssuranceEvaluator(evaluator_id="outside"),
                artifacts={"c": EvidenceArtifact("e", b"GOOD", "report", "candidate", "store:e", "claim")},
                verifiers={"checker": FrozenVerifier("checker", "outside", "b" * 64,
                                                     lambda b: b == b"GOOD", (b"GOOD",), (b"BAD",))},
                documentation={name: "Explicit limits for " + name for name in DOCUMENT_FIELDS},
                candidate_actor="candidate")


@pytest.fixture
def passing_adaptation():
    """Review 2's guard change, with six held-out pairs measured on it (its candidate and baseline digests, where a
    pair can carry them) and both controls, all authenticated by the evaluator's own admission."""
    from g6.cognitive_bridge import AdaptationPromotionGate, ControlTrial, GuardChange, PairedTrial

    change = GuardChange("old", "a" * 64, "b" * 64, "c" * 64, ("src/g6/cognitive_bridge/method_adapter.py",),
                         "cognitive_bridge", "candidate", "outside", ("false_confidence",))
    bound = ({"candidate_digest": change.code_digest, "baseline_digest": change.baseline_digest}
             if "candidate_digest" in PairedTrial.__dataclass_fields__ else {})
    pairs = tuple(PairedTrial(str(i), change.suite_digest, False, True, True, "holdout", f"receipt:{i}", **bound)
                  for i in range(6))
    suite = {"suite_digest": change.suite_digest} if "suite_digest" in ControlTrial.__dataclass_fields__ else {}
    controls = (ControlTrial("positive", True, "positive", "receipt:p", **suite),
                ControlTrial("negative", True, "negative", "receipt:n", **suite))
    gate = AdaptationPromotionGate(admit_pair=lambda x: x in pairs, admit_control=lambda x: x in controls,
                                   min_cases=6, min_discordant=6, max_evaluations=1)
    return gate, change, pairs, controls


@pytest.fixture
def assurance_case():
    from g6.cognitive_bridge import (
        ClaimIR, CognitiveAssuranceOrchestrator, Criterion, DomainStandard, EvidenceArtifact, FrozenVerifier,
        InProcessAssuranceEvaluator, MetacognitiveMethodSelector, MethodVariant, ProblemSignature,
    )
    from g6.cognitive_bridge.assurance import DOCUMENT_FIELDS

    standard = DomainStandard('review', '1', 'AU', 'review rubric', 'a' * 64,
                              (Criterion('c', 'formal obligation', 'checker', '1:1', required_methods=('formal',)),))
    source = {'formula': 'B', 'premises': ['A', '(=> A B)'], 'sorts': {'A': 'Bool', 'B': 'Bool'}}
    orchestrator = CognitiveAssuranceOrchestrator(MetacognitiveMethodSelector())
    compiled = orchestrator.compile(
        claim=ClaimIR('claim', 'A and A=>B entail B', ('formal',), {}),
        context=ProblemSignature('review', 'logic', 'validity', 1, 2), standard=standard,
        variants=[MethodVariant('v', 'formal', 'SMT', 'formal', 'check_validity', 1)], variant_models={'v': source})
    return {
        'standard': standard, 'source': source, 'orchestrator': orchestrator, 'compiled': compiled,
        'artifacts': {'c': EvidenceArtifact('e', b'GOOD', 'report', 'candidate', 'store:original', 'claim')},
        'verifiers': {'checker': FrozenVerifier('checker', 'evaluator', 'b' * 64, lambda b: b == b'GOOD', (b'GOOD',),
                                                (b'BAD',))},
        'documentation': {name: 'Explicit limits for ' + name for name in DOCUMENT_FIELDS},
        'candidate_actor': 'candidate', 'evaluator': InProcessAssuranceEvaluator(evaluator_id='outside'),
    }
