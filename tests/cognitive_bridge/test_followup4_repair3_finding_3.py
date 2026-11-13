"""A record with a missing field escapes the copier as AttributeError, not the boundary's refusal.

The walker names a record's missing field itself (NotPlainData, "missing record field ..."). Repair 2 runs a declared
record type's own check (``_admit_claim``) before that reader, and the check reads the fields by ``getattr``: on
6e39ab0 each entry below refused as the boundary refuses, and the executor answered PLAN_TAMPERED; on this commit a raw
AttributeError leaves every one of them, the doors that otherwise answer with a refusal included.
"""
import pytest

from g6.cognitive_bridge import (
    ClaimIR, CognitiveAssuranceOrchestrator, MetacognitiveMethodSelector, MetacognitivePlanner, MethodPlan,
    MixedMethodExecutor, snapshot,
)
from tests.cognitive_bridge.followup4_repair3_support import compile_inputs


def _forged_claim():
    return object.__new__(ClaimIR)  # the exact record type, with no field set


def _forged_plan():
    plan = object.__new__(MethodPlan)
    for name, value in (('claims', (_forged_claim(),)), ('steps', ()), ('missing', ()),
                        ('translation_status', 'candidate_unreviewed')):
        object.__setattr__(plan, name, value)
    return plan


def _compile(claim):
    inputs = compile_inputs()
    inputs['claim'] = claim
    return CognitiveAssuranceOrchestrator(MetacognitiveMethodSelector()).compile(**inputs)


def _planned(claim):
    inputs = compile_inputs()
    return MetacognitivePlanner(MetacognitiveMethodSelector()).plan(
        [claim], inputs['context'], inputs['variants'], required_methods=['formal'])


@pytest.mark.parametrize('entry', [
    lambda claim: snapshot.snapshot(claim),
    lambda claim: snapshot.fresh([claim]),
    _compile,
    _planned,
], ids=['snapshot', 'fresh', 'compile', 'planner'])
def test_a_claim_with_a_missing_field_is_refused_as_the_boundary_refuses(entry):
    raised = None
    try:
        entry(_forged_claim())
    except Exception as exc:  # the kind of what is raised is what is under test
        raised = exc
    assert isinstance(raised, ValueError) and 'missing record field' in str(raised), (
        f'a record with a missing field left the copier as {raised!r}, not as the boundary\'s refusal')


def test_the_executor_answers_a_plan_with_a_fieldless_claim_as_tampered():
    raised = None
    answer = {}
    try:
        answer = MixedMethodExecutor(None).execute(_forged_plan(), approved=True)
    except Exception as exc:
        raised = exc
    assert raised is None, f'an approved execution raised {raised!r} instead of answering with a refusal'
    assert answer['code'] == 'G6_E_COGNITIVE_PLAN_TAMPERED'
    assert answer['completion_state'] == 'blocked-escalated'


@pytest.mark.parametrize('name', ['EvidenceArtifact', 'EvaluatedTrial'])
def test_a_record_with_field_caps_and_a_missing_field_is_refused_as_the_boundary_refuses(name):
    # the same class one repair earlier (6e39ab0 fails these two as well): the field-cap admission reads a capped
    # field by getattr before the walker's own reader of a missing field
    import g6.cognitive_bridge as bridge
    raised = None
    try:
        snapshot.snapshot(object.__new__(getattr(bridge, name)))
    except Exception as exc:
        raised = exc
    assert isinstance(raised, ValueError) and 'missing record field' in str(raised), (
        f'a record with a missing field left the copier as {raised!r}, not as the boundary\'s refusal')
