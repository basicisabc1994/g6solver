"""Discover schema limits and challenge admission before the first child read."""
import inspect
from contextlib import contextmanager
import sys

import pytest

from g6.cognitive_bridge import block, snapshot, engine, mixed_methods, critique, xai
from g6.cognitive_bridge.codes import NotPlainData


@contextmanager
def reads_of(value):
    reads = []
    old = sys.getprofile()

    def profile(frame, event, result):
        if event == 'call' and frame.f_code is snapshot._walk.__code__ and frame.f_locals.get('value') is value:
            reads.append('walk')

    try:
        sys.setprofile(profile)
        yield reads
    finally:
        sys.setprofile(old)


def model_limits():
    for name, cls in inspect.getmembers(block, inspect.isclass):
        if cls.__module__ != block.__name__ or not issubclass(cls, block.BaseModel):
            continue
        for field_name, field in cls.model_fields.items():
            for metadata in field.metadata:
                for bound in ('max_length', 'min_length', 'le', 'lt', 'ge', 'gt'):
                    limit = getattr(metadata, bound, None)
                    if limit is not None:
                        yield cls, field_name, field.annotation, bound, limit


def planted(annotation, bound, limit):
    if bound.endswith('length'):
        count = limit + 1 if bound == 'max_length' else limit - 1
        return 'x' * count if annotation is str else [None] * count
    return limit + 1 if bound in ('le', 'lt') else limit - 1


LIMITS = list(model_limits())


@pytest.mark.parametrize('cls,name,annotation,bound,limit', LIMITS,
    ids=[f'{cls.__name__}.{name}.{bound}' for cls, name, _, bound, _ in LIMITS])
@pytest.mark.parametrize('typed', [False, True])
def test_every_model_bound_precedes_boundary_conversion(cls, name, annotation, bound, limit, typed):
    from copy import deepcopy
    from tests.cognitive_bridge.test_conversion_inventory import REQUESTS
    value = planted(annotation, bound, limit)
    valid = {block.CognitiveInput: {'op': 'help'},
             block.HypothesisInput: REQUESTS['update']['hypotheses'][0],
             block.ProbeInput: REQUESTS['update']['probes'][0],
             block.ClaimInput: REQUESTS['plan_methods']['claims'][0],
             block.ProblemSignatureInput: REQUESTS['rank_methods']['context'],
             block.MethodVariantInput: REQUESTS['rank_methods']['variants'][0]}
    raw = deepcopy(valid[cls])
    raw[name] = value
    # This is deliberately model_construct: raw, potentially invalid model
    # fields must be admitted before _walk, just like plain dictionary fields.
    given = cls.model_construct(**raw) if typed else raw
    if cls is not block.CognitiveInput:
        slots = {block.HypothesisInput: 'hypotheses', block.ProbeInput: 'probes',
                 block.ClaimInput: 'claims', block.MethodVariantInput: 'variants',
                 block.ProblemSignatureInput: 'context'}
        slot = slots[cls]
        given = {slot: given if slot == 'context' else [given]}
        if typed:
            given = block.CognitiveInput.model_construct(**given)
    with reads_of(value) as reads:
        with pytest.raises((NotPlainData, block.ValidationError, ValueError)):
            block._arguments(given)
    assert reads == [], f'{cls.__name__}.{name}.{bound}: converted before its schema bound'


@pytest.mark.parametrize('shape', ['list', 'mapping', 'text'])
def test_known_walk_budget_one_over_precedes_children(shape, monkeypatch):
    monkeypatch.setattr(snapshot, 'MAX_ITEMS', 8)
    monkeypatch.setattr(snapshot, 'MAX_SIZE', 16)
    marker = 'child'
    value = [marker] * 8 if shape == 'list' else {str(i): marker for i in range(4)} if shape == 'mapping' else 'x' * 17
    with reads_of(marker) as reads:
        with pytest.raises(NotPlainData, match='size'):
            snapshot.snapshot(value)
    assert reads == []


@pytest.mark.parametrize('name,value,error', [('claims', [None] * 21, '20'),
    ('hypotheses', [None] * 33, '32')])
def test_collection_bound_precedes_detachment(name, value, error):
    with reads_of(value) as reads:
        with pytest.raises(ValueError, match=error):
            if name == 'claims':
                mixed_methods.MethodRouter().plan(value)
            else:
                engine.check_beliefs(value)
    assert reads == []


@pytest.mark.parametrize('kind,field,cap', [('claim', 'id', 100), ('claim', 'statement', 3000),
    ('claim', 'evidence_needs', 5), ('critique', 'failure_mode', 1200)])
def test_record_local_length_before_conversion(kind, field, cap):
    value = ['formal'] * (cap + 1) if field == 'evidence_needs' else 'x' * (cap + 1)
    if kind == 'claim':
        claim = mixed_methods.ClaimIR('c', 's', ('formal',), {})
        object.__setattr__(claim, field, value)
        def call():
            return mixed_methods.MethodRouter().plan([claim])
    else:
        params = dict(failure_mode='f', assumption_at_risk='a', counterexample_or_refutation='r',
                      suggested_method='formal', evidence_required='e')
        params[field] = value
        def call():
            return critique.CritiqueCandidate(**params)
    with reads_of(value) as reads:
        with pytest.raises(ValueError):
            call()
    assert reads == []


@pytest.mark.parametrize('kind,field,cap', [('critic', 'objective', 3000), ('critic', 'model_summary', 12000),
                                         ('compiler', 'question', 3000), ('features', 'feature_names', 100)])
def test_function_length_before_conversion(kind, field, cap):
    value = ['x'] * (cap + 1) if kind == 'features' else 'x' * (cap + 1)
    class NoModel:
        def complete(self, *args, **kwargs):
            raise AssertionError('model called before admission')

    with reads_of(value) as reads:
        with pytest.raises(ValueError):
            if kind == 'critic':
                params = dict(objective='o', model_summary='s')
                params[field] = value
                critique.LLMMetacognitiveCritic(NoModel()).propose(**params)
            elif kind == 'compiler':
                mixed_methods.LLMClaimCompiler(NoModel()).draft(value)
            else:
                xai.distill_blackbox(NoModel(), [], [], value)
    assert reads == []


@pytest.mark.parametrize('kind,collection,error', [('probes', 'probes', 'too many probes'),
    ('ideas', 'ideas', 'idea schema or count'), ('claims', 'claims', 'claim count'),
    ('critique', 'candidates', 'too many critique')])
def test_model_count_one_over_before_parsed_collection_detachment(kind, collection, error):
    import json
    from tests.cognitive_bridge.test_followup4_repair1_late import calls_to, Model
    model = Model(json.dumps({collection: [{}, {}]}))
    calls = {
        'probes': lambda: engine.LLMProposer(model, {'t': engine.ToolSpec('t', lambda *_: None)},
            max_probes=1).propose('o', [engine.Hypothesis('a', 's', 1.0)], []),
        'ideas': lambda: engine.LLMIdeaGenerator(model, max_ideas=1).generate('o', []),
        'claims': lambda: mixed_methods.LLMClaimCompiler(model, max_claims=1).draft('q'),
        'critique': lambda: critique.LLMMetacognitiveCritic(model, max_items=1).propose('o', 's'),
    }
    def is_collection(frame):
        value = frame.f_locals.get('value')
        return type(value) is list and value == [{}, {}]
    with calls_to(snapshot._walk, is_collection) as detached:
        with pytest.raises(ValueError, match=error):
            calls[kind]()
    assert detached == []


def scalar_limits():
    from dataclasses import replace
    import math
    from g6.cognitive_bridge import adaptation, metacognition
    from tests.cognitive_bridge.test_boundary_inventory import MADE
    def probe(**changes):
        value = replace(MADE['Probe'](), positive_if={'a': True, 'b': False}, **changes)
        return engine._validate_probe(value, {'a': .5, 'b': .5})
    return {
        'adaptation.min_cases.upper': lambda: adaptation.AdaptationPromotionGate(min_cases=100001),
        'adaptation.min_cases.lower': lambda: adaptation.AdaptationPromotionGate(min_cases=0),
        'adaptation.min_discordant.upper': lambda: adaptation.AdaptationPromotionGate(min_cases=30, min_discordant=31),
        'adaptation.alpha.upper': lambda: adaptation.AdaptationPromotionGate(alpha=1.0),
        'adaptation.alpha.lower': lambda: adaptation.AdaptationPromotionGate(alpha=0.0),
        'adaptation.max_evaluations.upper': lambda: adaptation.AdaptationPromotionGate(max_evaluations=101),
        'adaptation.max_evaluations.lower': lambda: adaptation.AdaptationPromotionGate(max_evaluations=0),
        'critic.max_items.upper': lambda: critique.LLMMetacognitiveCritic(None, max_items=13),
        'critic.max_items.lower': lambda: critique.LLMMetacognitiveCritic(None, max_items=0),
        'proposer.max_probes.upper': lambda: engine.LLMProposer(None, {}, max_probes=13),
        'proposer.max_probes.lower': lambda: engine.LLMProposer(None, {}, max_probes=0),
        'ideas.max_ideas.upper': lambda: engine.LLMIdeaGenerator(None, max_ideas=11),
        'ideas.max_ideas.lower': lambda: engine.LLMIdeaGenerator(None, max_ideas=0),
        'compiler.max_claims.upper': lambda: mixed_methods.LLMClaimCompiler(None, max_claims=11),
        'compiler.max_claims.lower': lambda: mixed_methods.LLMClaimCompiler(None, max_claims=0),
        'information.novelty.upper': lambda: engine.InformationGainSelector(novelty_weight=2),
        'information.novelty.lower': lambda: engine.InformationGainSelector(novelty_weight=-1),
        'information.min_gain.lower': lambda: engine.InformationGainSelector(min_gain=-1),
        'selector.exploration.upper': lambda: metacognition.MetacognitiveMethodSelector(exploration_weight=2),
        'selector.exploration.lower': lambda: metacognition.MetacognitiveMethodSelector(exploration_weight=-1),
        'context.risk.upper': lambda: replace(MADE['ProblemSignature'](), risk=4),
        'context.risk.lower': lambda: replace(MADE['ProblemSignature'](), risk=-1),
        'context.budget.lower': lambda: replace(MADE['ProblemSignature'](), budget=-1),
        'variant.cost.lower': lambda: replace(MADE['MethodVariant'](), estimated_cost=-1),
        'trial.cost.lower': lambda: replace(MADE['EvaluatedTrial'](), actual_cost=-1),
        'belief.probability.upper': lambda: engine._beliefs({'a': 2, 'b': 0}),
        'belief.probability.lower': lambda: engine._beliefs({'a': -1, 'b': 2}),
        'probe.match.upper': lambda: probe(assumed_match_probability=2),
        'probe.match.lower': lambda: probe(assumed_match_probability=-.5),
        'probe.cost.lower': lambda: probe(cost=-1),
        'probe.novelty.upper': lambda: probe(novelty=2),
        'probe.novelty.lower': lambda: probe(novelty=-1),
        'selector.optional.upper': lambda: metacognition.MetacognitiveMethodSelector().select(
            MADE['ProblemSignature'](), [], required_methods=['formal'], max_optional=11),
        'selector.optional.lower': lambda: metacognition.MetacognitiveMethodSelector().select(
            MADE['ProblemSignature'](), [], required_methods=['formal'], max_optional=-1),
        'agent.max_steps.upper': lambda: engine.CognitiveAgent(engine.StaticProposer([]), {}).run('q', [], budget=1, max_steps=21),
        'agent.max_steps.lower': lambda: engine.CognitiveAgent(engine.StaticProposer([]), {}).run('q', [], budget=1, max_steps=0),
        'agent.budget.lower': lambda: engine.CognitiveAgent(engine.StaticProposer([]), {}).run('q', [], budget=-1),
        'surrogate.max_depth.upper': lambda: xai.distill_blackbox(None, [[i] for i in range(10)], [[i+20] for i in range(5)], ['x'], max_depth=13),
        'surrogate.max_depth.lower': lambda: xai.distill_blackbox(None, [[i] for i in range(10)], [[i+20] for i in range(5)], ['x'], max_depth=0),
        'surrogate.min_leaf.upper': lambda: xai.distill_blackbox(None, [[i] for i in range(10)], [[i+20] for i in range(5)], ['x'], min_samples_leaf=1001),
        'surrogate.min_leaf.lower': lambda: xai.distill_blackbox(None, [[i] for i in range(10)], [[i+20] for i in range(5)], ['x'], min_samples_leaf=0),
        'number.float.upper': lambda: snapshot.number(math.inf, where='numeric bound'),
    }


@pytest.mark.parametrize('name', list(scalar_limits()))
def test_every_scalar_budget_refuses_before_external_work(name, monkeypatch):
    # These inputs are native scalar leaves. Their size is constant at every
    # threshold; callbacks and models must never be reached for a bad budget.
    from tests.cognitive_bridge.test_followup4_repair1_late import calls_to
    with calls_to(engine.expected_information_gain) as calls:
        with pytest.raises(ValueError):
            scalar_limits()[name]()
    assert calls == []


@pytest.mark.parametrize('kind', ['information', 'ml', 'method'])
def test_unaffordable_candidate_never_scored(kind):
    from g6.cognitive_bridge import metacognition
    from tests.cognitive_bridge.test_followup4_repair1_late import calls_to
    probe = engine.Probe('p', 'q', 't', {'a': True, 'b': False}, .8, cost=2)
    if kind == 'method':
        selector = metacognition.MetacognitiveMethodSelector()
        variant = metacognition.MethodVariant('v', 'formal', 'SMT', 'formal', 'check_validity', 2)
        with calls_to(selector._score) as scored:
            answer = selector.select(metacognition.ProblemSignature('d', 'u', 'k', 1, 1),
                                     [variant], required_methods=['formal'])
        assert answer.missing == ('formal',)
    else:
        class NoPredictor:
            def predict(self, *_):
                raise AssertionError('predictor called for an unaffordable probe')
        selector = engine.InformationGainSelector() if kind == 'information' else engine.MLAcquisitionSelector(NoPredictor())
        with calls_to(engine.expected_information_gain) as scored:
            assert selector.rank({'a': .5, 'b': .5}, [probe], budget=1) == []
    assert scored == []


def test_portfolio_obligation_cap_precedes_scoring():
    from g6.cognitive_bridge import metacognition
    from tests.cognitive_bridge.test_followup4_repair1_late import calls_to
    selector = metacognition.MetacognitiveMethodSelector()
    variants = [metacognition.MethodVariant(str(i), str(i), 'r', 'e', 'o', 1, kind='reviewer') for i in range(6)]
    with calls_to(selector._score) as scored:
        with pytest.raises(ValueError, match='5'):
            selector.select(metacognition.ProblemSignature('d', 'u', 'k', 1, 10), variants,
                            required_methods=[str(i) for i in range(6)])
    assert scored == []
