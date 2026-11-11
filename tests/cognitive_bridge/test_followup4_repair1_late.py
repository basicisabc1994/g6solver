"""Local caps must admit input before conversion, copying, or model calls."""
import json
import sys
from contextlib import contextmanager

import pytest

from g6.cognitive_bridge import snapshot, critique, engine, mixed_methods, metacognition
from g6.cognitive_bridge.codes import NotPlainData


@contextmanager
def calls_to(function, predicate=lambda frame: True):
    calls = []
    previous = sys.getprofile()

    def profile(frame, event, result):
        if event == 'call' and frame.f_code is function.__code__ and predicate(frame):
            calls.append(frame.f_code.co_name)

    try:
        sys.setprofile(profile)
        yield calls
    finally:
        sys.setprofile(previous)


def test_candidate_cap_precedes_detachment():
    selector = metacognition.MetacognitiveMethodSelector()
    variants = [metacognition.MethodVariant(str(i), 'formal', 'SMT', 'formal', 'check_validity', 1)
                for i in range(101)]
    context = metacognition.ProblemSignature('d', 'u', 'k', 1, 10)
    with calls_to(snapshot._walk, lambda f: f.f_locals.get('value') is variants) as calls:
        with pytest.raises(ValueError, match='at most 100'):
            selector.select(context, variants, required_methods=['formal'])
    assert calls == []


@pytest.mark.parametrize('wrapped', [False, True])
def test_json_reply_cap_precedes_text_conversion(wrapped):
    reply = ' ' * 13
    with calls_to(snapshot.text) as calls:
        with pytest.raises(ValueError, match='12-character cap'):
            if wrapped:
                snapshot.returned(reply, snapshot.json_reply, 12)
            else:
                snapshot.json_reply(reply, 12)
    assert calls == []


class Model:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def complete(self, *args, **kwargs):
        self.calls.append(args)
        return self.reply


def observe_json(monkeypatch, target):
    dumps = snapshot.json.dumps
    outputs = []

    def observed(value, *args, **kwargs):
        if target(value):
            outputs.append(value)
        return dumps(value, *args, **kwargs)

    monkeypatch.setattr(snapshot.json, 'dumps', observed)
    return outputs


def contains(value, target):
    if type(value) is dict:
        return value == target or any(contains(item, target) for item in value.values())
    if type(value) in (tuple, list):
        return any(contains(item, target) for item in value)
    return value == target if type(value) is type(target) else False


def test_tool_argument_one_over_precedes_encoding(monkeypatch):
    arguments = {'x': 'a' * (2001 - len('{"x":""}'))}
    assert len(json.dumps(arguments, separators=(',', ':'))) == 2001
    model = Model(json.dumps({'probes': [{'id': 'p', 'question': 'q', 'tool': 't',
        'arguments': arguments, 'positive_if': {'h': True}, 'novelty': 0.1}]}))
    proposer = engine.LLMProposer(model, {'t': engine.ToolSpec('t', lambda _: True, 1, .9)})
    outputs = observe_json(monkeypatch, lambda v: type(v) is dict and v == arguments)
    with pytest.raises(ValueError, match='tool arguments'):
        proposer.propose('q', [engine.Hypothesis('h', 'h', 1.0)], [])
    assert outputs == []


def test_declarative_model_one_over_precedes_encoding(monkeypatch):
    given = {'premises': ['a' * (250001 - len('{"premises":[""]}'))]}
    assert len(json.dumps(given, separators=(',', ':'))) == 250001
    claim = mixed_methods.ClaimIR('c', 's', ('formal',), {'formal': given})
    outputs = observe_json(monkeypatch, lambda v: contains(v, given))
    with pytest.raises(ValueError, match='250 kB'):
        mixed_methods.MethodRouter().plan([claim])
    assert outputs == []


@pytest.mark.parametrize('kind,cap,error', [('critique', 25000, 'critique prompt'),
                                          ('claims', 15000, 'prompt cap')])
def test_prompt_one_over_precedes_encoding(monkeypatch, kind, cap, error):
    # Capture the exact fixed frame once using a valid request, then plant one
    # byte beyond this bound. ASCII output has identical byte/character length.
    model = Model('{"candidates":[]}' if kind == 'critique' else '{"claims":[]}')
    caller = critique.LLMMetacognitiveCritic(model) if kind == 'critique' else mixed_methods.LLMClaimCompiler(model)
    captured = []
    dumps = snapshot.json.dumps

    def capture(value, *args, **kwargs):
        if type(value) is dict and ('allowed_methods' in value or 'supported_methods' in value):
            captured.append(dumps(value, *args, **kwargs))
        return dumps(value, *args, **kwargs)

    monkeypatch.setattr(snapshot.json, 'dumps', capture)
    try:
        if kind == 'critique':
            caller.propose('q', 's', [{'x': ''}])
        else:
            caller.draft('q', [{'x': ''}])
    except ValueError:
        pass  # an empty claims reply is intentionally invalid
    overhead = len(captured[0])
    payload = [{'x': 'a' * (cap + 1 - overhead)}]
    outputs = observe_json(monkeypatch, lambda v: contains(v, payload[0]['x']))
    model.calls.clear()
    with pytest.raises(ValueError, match=error):
        if kind == 'critique':
            caller.propose('q', 's', payload)
        else:
            caller.draft('q', payload)
    assert outputs == []
    assert model.calls == []


def test_encoded_one_over_counts_shared_containers_before_encoding(monkeypatch):
    value = {'a': ['\\', '😀'], 'b': [None, True, 1.25, -3]}
    expected = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()
    monkeypatch.setattr(snapshot, 'MAX_SIZE', len(expected) - 1)
    outputs = observe_json(monkeypatch, lambda v: type(v) is dict and v == value)
    with pytest.raises(NotPlainData, match='size'):
        snapshot.canonical_json(value)
    assert outputs == []
