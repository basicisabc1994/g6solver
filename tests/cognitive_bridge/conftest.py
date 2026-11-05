# SPDX-License-Identifier: Apache-2.0
"""One compiled formal claim with its standard, artifact, checker and documentation, for the bridge's review tests."""
from __future__ import annotations

import pytest


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
