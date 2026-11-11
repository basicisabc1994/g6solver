"""Remaining fixed widths, lower bounds, and opaque generation admission."""
from array import array
from dataclasses import replace
import sys

import pytest

from g6.cognitive_bridge import snapshot, xai, EvidenceArtifact
from g6.cognitive_bridge.codes import NotPlainData
from tests.cognitive_bridge.test_followup4_repair1_late import calls_to
from tests.cognitive_bridge.test_boundary_inventory import MADE


@pytest.mark.parametrize('field', ['oracle_digest', 'variant_digest'])
def test_digest_width_one_over_precedes_conversion(field):
    value = 'a' * 65
    trial = MADE['EvaluatedTrial']()
    with calls_to(snapshot._walk, lambda f: f.f_locals.get('value') is value) as copied:
        with pytest.raises(ValueError, match='digest'):
            replace(trial, **{field: value})
    assert copied == []


@pytest.mark.parametrize('train,holdout', [(9, 5), (10, 4)])
def test_distillation_row_minimum_precedes_numeric_conversion(train, holdout):
    with calls_to(xai._float32) as converted:
        with pytest.raises(ValueError, match='at least 10 training'):
            xai.distill_blackbox(None, [[float(i)] for i in range(train)],
                                [[float(i + 20)] for i in range(holdout)], ['x'])
    assert converted == []


@pytest.mark.parametrize('path', ['construct', 'replace', 'fresh'])
@pytest.mark.parametrize('view', [False, True])
def test_buffer_one_over_precedes_copy_on_each_record_path(path, view):
    value = memoryview(array('I', [0]) * 500001) if view else bytearray(2000001)
    original = EvidenceArtifact('e', b'x', 'r', 'c', 's', 'c')
    with calls_to(snapshot._bytes) as copied:
        with pytest.raises(ValueError, match='artifact exceeds 2MB'):
            if path == 'construct':
                EvidenceArtifact('e', value, 'r', 'c', 's', 'c')
            elif path == 'replace':
                replace(original, content=value)
            else:
                object.__setattr__(original, 'content', value)
                snapshot.fresh(original)
    assert copied == []


@pytest.mark.parametrize('which', ['visits', 'bytes', 'depth'])
def test_native_array_each_budget_before_tolist(which, monkeypatch):
    if which == 'visits':
        monkeypatch.setattr(snapshot, 'MAX_ITEMS', 4)
        value = array('b', [0]) * 4  # root + 4 leaves = 5 visits
    elif which == 'bytes':
        monkeypatch.setattr(snapshot, 'MAX_SIZE', 3)
        value = array('I', [0])  # native 4-byte element
    else:
        monkeypatch.setattr(snapshot, 'DEPTH', 1)
        value = memoryview(bytearray(1)).cast('B', (1, 1))
    calls = []
    previous = sys.getprofile()
    def observe(frame, event, call):
        if event == 'c_call' and getattr(call, '__name__', '') == 'tolist' and getattr(call, '__self__', None) is value:
            calls.append('tolist')
    try:
        sys.setprofile(observe)
        with pytest.raises(NotPlainData):
            xai._native(value, where='native array')
    finally:
        sys.setprofile(previous)
    assert calls == []


@pytest.mark.parametrize('kind,field,cap', [('probes', 'id', 100), ('probes', 'question', 1000),
    *[('ideas', field, 1000) for field in ('id', 'statement', 'cross_domain_analogy', 'falsifying_question')],
    *[('critique', field, 1200) for field in ('failure_mode', 'assumption_at_risk', 'counterexample_or_refutation',
                                           'suggested_method', 'evidence_required', 'potential_domain_standard_gap')]])
def test_model_field_one_over_precedes_parsed_field_copy(kind, field, cap):
    import json
    from g6.cognitive_bridge import engine, critique
    from tests.cognitive_bridge.test_followup4_repair1_late import Model
    value = 'x' * (cap + 1)
    if kind == 'probes':
        item = dict(id='p', question='q', tool='t', arguments={}, positive_if={'h': True}, novelty=.1)
        item[field] = value
        model = Model(json.dumps({'probes': [item]}))
        def call():
            return engine.LLMProposer(model, {'t': engine.ToolSpec('t', lambda *_: None)}).propose(
                'q', [engine.Hypothesis('h', 'h', 1.0)], [])
    elif kind == 'ideas':
        item = dict(id='i', statement='s', cross_domain_analogy='a', falsifying_question='q')
        item[field] = value
        def call():
            return engine.LLMIdeaGenerator(Model(json.dumps({'ideas': [item]}))).generate('q', [])
    else:
        item = dict(failure_mode='f', assumption_at_risk='a', counterexample_or_refutation='r',
                    suggested_method='formal', evidence_required='e', potential_domain_standard_gap='g')
        item[field] = value
        def call():
            return critique.LLMMetacognitiveCritic(Model(json.dumps({'candidates': [item]}))).propose('q', 's')
    with calls_to(snapshot._walk, lambda f: type(f.f_locals.get('value')) is str and f.f_locals['value'] == value) as copied:
        with pytest.raises(ValueError):
            call()
    assert copied == []


def test_encoder_all_bmp_escape_widths_before_output(monkeypatch):
    import json
    value = ''.join(chr(point) for point in range(65536)) + '😀'
    expected = json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode()
    monkeypatch.setattr(snapshot, 'MAX_SIZE', len(expected))
    assert snapshot.canonical_json(value) == expected
    monkeypatch.setattr(snapshot, 'MAX_SIZE', len(expected) - 1)
    from tests.cognitive_bridge.test_followup4_repair1_late import observe_json
    outputs = observe_json(monkeypatch, lambda item: item is value)
    with pytest.raises(NotPlainData, match='size'):
        snapshot.canonical_json(value)
    assert outputs == []


@pytest.mark.parametrize('hook', ['tolist', 'attribute', 'buffer'])
def test_python_array_hooks_cannot_bypass_native_size_admission(hook):
    calls = []
    if hook == 'tolist':
        class Hooked(array):
            def tolist(self):
                calls.append('tolist')
                return [0] * (snapshot.MAX_ITEMS + 1)
    elif hook == 'attribute':
        class Hooked(array):
            def __getattribute__(self, name):
                if name == 'tolist':
                    calls.append('attribute')
                    return lambda: [0] * (snapshot.MAX_ITEMS + 1)
                return super().__getattribute__(name)
    else:
        class Hooked(array):
            def __buffer__(self, flags):
                calls.append('buffer')
                return memoryview(bytearray(snapshot.MAX_SIZE + 1))
    value = Hooked('b', [0])  # measured storage is small, hooks' outputs are not
    with pytest.raises(NotPlainData, match='size'):
        xai._native(value, where='native hooks')
    assert calls == [], 'Python protocol ran before its output size could be measured'


def model_list_caps():
    import typing
    from tests.cognitive_bridge.test_followup4_repair1_limits import LIMITS
    return [(cls, name, cap) for cls, name, annotation, bound, cap in LIMITS
            if bound == 'max_length' and typing.get_origin(annotation) is list]


def test_unavailable_native_buffer_is_a_named_refusal(monkeypatch):
    def unavailable(value):
        raise BufferError('native buffer unavailable')
    monkeypatch.setattr(snapshot, 'memoryview', unavailable, raising=False)
    with pytest.raises(NotPlainData, match='size'):
        xai._native(array('b', [0]), where='native array')


@pytest.mark.parametrize('outer', ['dict-subclass', 'protocol', 'frozen'])
def test_outer_mapping_field_cap_precedes_detachment(outer):
    from g6.cognitive_bridge import block
    from tests.cognitive_bridge.test_conversion_inventory import REQUESTS
    value = [REQUESTS['update']['probes'][0]] * 101
    raw = {'op': 'help', 'probes': value}
    if outer == 'dict-subclass':
        class Outer(dict):
            pass
        given = Outer(raw)
    elif outer == 'protocol':
        class Outer:
            def keys(self):
                return raw.keys()
            def __getitem__(self, key):
                return raw[key]
        given = Outer()
    else:
        given = snapshot.FrozenMap(raw)
    with calls_to(snapshot._walk, lambda f: f.f_locals.get('value') is value) as copied:
        with pytest.raises(NotPlainData):
            block._arguments(given)
    assert copied == []


def test_model_input_does_not_compare_foreign_keys():
    from g6.cognitive_bridge import block
    calls = []
    class Key:
        def __hash__(self):
            return hash('budget')
        def __eq__(self, other):
            calls.append(other)
            return False
    raw = {Key(): 0}
    with pytest.raises(block.ValidationError):
        block.CognitiveInput.model_validate(raw)
    assert calls == []


def test_model_input_cannot_extract_unmeasured_attributes():
    from g6.cognitive_bridge import block
    from tests.cognitive_bridge.test_conversion_inventory import REQUESTS
    calls = []
    class Attributes:
        @property
        def probes(self):
            calls.append('extracted')
            return [REQUESTS['update']['probes'][0]] * 101
    with pytest.raises(block.ValidationError):
        block.CognitiveInput.model_validate(Attributes(), from_attributes=True)
    assert calls == []


def test_model_global_visit_budget_precedes_core_dictionary_copy(monkeypatch):
    from g6.cognitive_bridge import block
    from tests.cognitive_bridge.test_conversion_inventory import REQUESTS
    from copy import deepcopy
    raw = deepcopy(REQUESTS['update']['probes'][0])
    raw['arguments'] = {str(i): i for i in range(33)}
    def visits(value):
        if type(value) is dict:
            return 1 + sum(1 + visits(item) for item in value.values())
        if type(value) is list:
            return 1 + sum(visits(item) for item in value)
        return 1
    monkeypatch.setattr(snapshot, 'MAX_ITEMS', visits(raw) - 1)
    with pytest.raises(block.ValidationError, match='size'):
        block.ProbeInput.model_validate(raw)


@pytest.mark.parametrize('cls,name,cap', model_list_caps())
@pytest.mark.parametrize('api', ['model_validate', 'constructor'])
def test_direct_model_list_cap_precedes_item_validation(cls, name, cap, api, monkeypatch):
    """Observe the real list item validator, rather than just block detachment."""
    from copy import deepcopy
    from pydantic_core import SchemaValidator, core_schema
    from g6.cognitive_bridge import block
    from tests.cognitive_bridge.test_conversion_inventory import REQUESTS
    valid = {block.CognitiveInput: {'op': 'help'},
             block.HypothesisInput: REQUESTS['update']['hypotheses'][0],
             block.ProbeInput: REQUESTS['update']['probes'][0],
             block.ClaimInput: REQUESTS['plan_methods']['claims'][0],
             block.ProblemSignatureInput: REQUESTS['rank_methods']['context'],
             block.MethodVariantInput: REQUESTS['rank_methods']['variants'][0]}
    entries = {'claims': REQUESTS['plan_methods']['claims'][0],
               'hypotheses': REQUESTS['update']['hypotheses'][0],
               'probes': REQUESTS['update']['probes'][0],
               'variants': REQUESTS['rank_methods']['variants'][0],
               'required_methods': 'formal', 'evidence_needs': 'formal',
               'source_refs': 's', 'expects': 'e', 'available_evidence': 'e'}
    raw = deepcopy(valid[cls])
    raw[name] = [entries[name]] * (cap + 1)
    schema = deepcopy(cls.__pydantic_core_schema__)
    node = schema
    while node['type'] != 'model-fields':
        node = node['schema']
    field = node['fields'][name]['schema']
    while field['type'] != 'list':
        field = field['schema']
    visits = []
    def observed(item):
        visits.append(1)
        return item
    field['items_schema'] = core_schema.no_info_before_validator_function(observed, field['items_schema'])
    # Prevent the compiler from reusing the class's existing prebuilt validator
    # instead of the instrumented schema. The admitted control below checks it.
    monkeypatch.setattr(cls, '__pydantic_validator__', None)
    monkeypatch.setattr(cls, '__pydantic_validator__', SchemaValidator(schema))
    with pytest.raises(block.ValidationError):
        cls.model_validate(raw) if api == 'model_validate' else cls(**raw)
    assert visits == [], f'{cls.__name__}.{name}: validated {len(visits)} items before native length refusal'
    raw[name] = [entries[name]]
    cls.model_validate(raw) if api == 'model_validate' else cls(**raw)
    assert visits == [1], 'the item-validator instrument must fire for an admitted control'
