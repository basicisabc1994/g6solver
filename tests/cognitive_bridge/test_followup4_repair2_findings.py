# SPDX-License-Identifier: Apache-2.0
"""Review 2 of PR #8's fourth follow-up: its eight findings, brought in with the reviewer's assertions unchanged.

Each bound is read by the copier itself (``snapshot._walk``, ``snapshot.plain``, the encoder), so no public entry can
detach, round-trip or encode a value before its bound refuses it; the converter that is measured is the one that runs;
and a raw mapping's keys pass the one text-key reader before anything looks one up.
"""
import json
import sys
from array import array
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from g6.cognitive_bridge import (
    ClaimIR, CognitiveAgent, CognitiveAssuranceOrchestrator, Criterion, DomainStandard, Hypothesis, LLMClaimCompiler,
    MetacognitiveMethodSelector, MetacognitivePlanner, MethodPlan, MethodVariant, MixedMethodExecutor,
    ProblemSignature, StaticProposer, snapshot, xai,
)
from g6.cognitive_bridge.block import CognitiveBridgeBlock
from g6.cognitive_bridge.codes import INPUT, NotPlainData


@contextmanager
def observe_walk(predicate):
    """Record completed detachments, so admission inside the walker can refuse first."""
    calls = []
    previous = sys.getprofile()

    def profile(frame, event, answer):
        if event == 'return' and answer is not None and frame.f_code is snapshot._walk.__code__:
            if predicate(frame.f_locals['value']):
                calls.append('walk')

    try:
        sys.setprofile(profile)
        yield calls
    finally:
        sys.setprofile(previous)


def compile_inputs():
    return dict(
        claim=ClaimIR('claim', 'A entails A', ('formal',), {}),
        context=ProblemSignature('review', 'logic', 'validity', 1, 2),
        standard=DomainStandard('review', '1', 'AU', 'rubric', 'a' * 64,
            (Criterion('c', 'formal obligation', 'checker', '1:1', required_methods=('formal',)),)),
        variants=[MethodVariant('v', 'formal', 'SMT', 'formal', 'check_validity', 1)],
        variant_models={'v': {'formula': 'A', 'premises': ['A']}},
    )


def contains_text(value, text):
    if type(value) is str:
        return value == text
    if type(value) is dict:
        return any(contains_text(v, text) for v in value.values())
    if type(value) is list:
        return any(contains_text(v, text) for v in value)
    return False


@contextmanager
def encodings_of(monkeypatch, text):
    """Each ``json.dumps`` of a value that holds ``text``."""
    dumps = snapshot.json.dumps
    encoded = []

    def observed(value, *args, **kwargs):
        if contains_text(value, text):
            encoded.append('encoded')
        return dumps(value, *args, **kwargs)

    monkeypatch.setattr(snapshot.json, 'dumps', observed)
    yield encoded


def test_finding_1_instance_shadow_cannot_expand_before_native_array_admission():
    calls = []

    class ArrayWithState(array):
        pass

    names = ArrayWithState('b', [0])

    def expand():
        calls.append('expanded')
        return ['x'] * (snapshot.MAX_ITEMS + 1)

    # The inherited native method is a non-data descriptor: normal lookup
    # invokes this instance attribute instead, despite _admit_array's MRO check.
    names.tolist = expand
    estimator = SimpleNamespace(tree_=None, n_features_in_=1, feature_names_in_=names)
    with pytest.raises(NotPlainData):
        xai.extract_tree(estimator)
    assert calls == [], 'unmeasured instance converter expanded before refusal'


def test_finding_2_infer_refuses_foreign_key_without_invoking_its_equality():
    comparisons = []

    class Key:
        def __hash__(self):
            return hash('probes')

        def __eq__(self, other):
            comparisons.append(other)
            return False

    raw = {Key(): None}
    answer = CognitiveBridgeBlock().infer(raw).unwrap()
    assert answer['code'] == INPUT
    assert comparisons == [], 'schema preflight compared a key before the boundary read it'


@pytest.mark.parametrize('field,value', [
    ('id', 'i' * 101),
    ('statement', 's' * 3001),
    ('evidence_needs', ['formal'] * 6),
])
def test_finding_3_claim_reply_field_cap_precedes_parsed_field_copy(field, value):
    item = dict(id='c', statement='s', evidence_needs=['formal'], models={'formal': {'formula': 'A'}})
    item[field] = value

    class Model:
        def complete(self, *args, **kwargs):
            return json.dumps({'claims': [item]})

    with observe_walk(lambda raw: type(raw) is type(value) and raw == value) as copied:
        with pytest.raises(ValueError, match='length|evidence needs'):
            LLMClaimCompiler(Model()).draft('q', [])
    assert copied == [], 'over-limit model claim field copied before its local cap'


@pytest.mark.parametrize('entry', ['compile', 'planner', 'executor'])
def test_finding_4_model_cap_precedes_any_model_encoding(monkeypatch, entry):
    inputs = compile_inputs()
    formula = 'A' * 250_001
    inputs['variant_models'] = {'v': {'formula': formula}}
    claim = ClaimIR('claim', 's', ('formal',), {'formal': {'formula': formula}})
    plan = MethodPlan((claim,), (), ())
    with encodings_of(monkeypatch, formula) as encoded:
        if entry == 'executor':
            result = MixedMethodExecutor(None).execute(plan, approved=True)
            assert result['code'] == 'G6_E_COGNITIVE_PLAN_TAMPERED'
            assert '250 kB' in result['reason']
        else:
            with pytest.raises(ValueError, match='250 kB'):
                if entry == 'compile':
                    CognitiveAssuranceOrchestrator(MetacognitiveMethodSelector()).compile(**inputs)
                else:
                    MetacognitivePlanner(MetacognitiveMethodSelector()).plan(
                        [claim], inputs['context'], inputs['variants'], required_methods=['formal'])
    assert encoded == [], 'oversized declarative model encoded before the router cap'


@pytest.mark.parametrize('entry', ['orchestrator', 'planner'])
def test_finding_5_candidate_cap_precedes_detachment_in_selector_callers(entry):
    inputs = compile_inputs()
    variants = [MethodVariant(f'v{i}', 'formal', 'SMT', 'formal', 'check_validity', 1) for i in range(101)]
    inputs['variants'] = variants
    selector = MetacognitiveMethodSelector()
    with observe_walk(lambda raw: raw is variants) as copied:
        with pytest.raises(ValueError, match='at most 100'):
            if entry == 'orchestrator':
                CognitiveAssuranceOrchestrator(selector).compile(**inputs)
            else:
                MetacognitivePlanner(selector).plan([inputs['claim']], inputs['context'], variants,
                                                    required_methods=['formal'])
    assert copied == [], '101 candidates detached before their count was admitted'


def test_finding_5_obligation_cap_precedes_detachment_in_planner():
    inputs = compile_inputs()
    required = ['formal'] * 6
    with observe_walk(lambda raw: raw is required) as copied:
        with pytest.raises(ValueError, match='at most 5 obligations'):
            MetacognitivePlanner(MetacognitiveMethodSelector()).plan(
                [inputs['claim']], inputs['context'], inputs['variants'], required_methods=required)
    assert copied == [], 'six explicit obligations detached before their count was admitted'


def test_finding_6_execution_artifact_size_precedes_json_buffer_construction(monkeypatch):
    data = 'a' * 2_000_001
    with encodings_of(monkeypatch, data) as encoded:
        with pytest.raises(ValueError, match='artifact exceeds 2MB'):
            CognitiveAssuranceOrchestrator.execution_artifact({'data': data}, actor='producer', claim_id='c')
    assert encoded == [], 'over-limit artifact JSON built before its 2MB bound'


@pytest.mark.parametrize('entry', ['agent', 'static-proposer'])
def test_finding_7_hypothesis_count_precedes_copy_in_belief_rule_callers(entry):
    hypotheses = [Hypothesis(str(i), 's', 1 / 33) for i in range(33)]
    proposer = StaticProposer([])
    agent = CognitiveAgent(proposer, {})
    with observe_walk(lambda raw: raw is hypotheses) as copied:
        with pytest.raises(ValueError, match='32 competing hypotheses'):
            if entry == 'agent':
                agent.run('q', hypotheses, budget=0)
            else:
                proposer.propose('q', hypotheses, [])
    assert copied == [], '33 hypotheses copied before the 32-hypothesis cap'


@pytest.mark.parametrize('entry', ['executor', 'planner'])
def test_finding_8_claim_count_precedes_copy_in_router_callers(entry):
    claims = tuple(ClaimIR(str(i), 's', ('formal',), {'formal': {'formula': 'A'}}) for i in range(21))
    plan = MethodPlan(claims, (), ())
    inputs = compile_inputs()
    with observe_walk(lambda raw: raw is claims or raw is plan.claims) as copied:
        if entry == 'executor':
            result = MixedMethodExecutor(None).execute(plan, approved=True)
            assert result['code'] == 'G6_E_COGNITIVE_PLAN_TAMPERED'
            assert '20 typed claims' in result['reason']
        else:
            with pytest.raises(ValueError, match='20 typed claims'):
                MetacognitivePlanner(MetacognitiveMethodSelector()).plan(
                    claims, inputs['context'], inputs['variants'], required_methods=['formal'])
    assert copied == [], '21 claims copied before the 20-claim cap'
