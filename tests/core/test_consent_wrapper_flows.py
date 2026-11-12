# SPDX-License-Identifier: Apache-2.0
"""Consent at the real backends of the metaharness's request wrapper fakes."""
import importlib.util
import sys
from pathlib import Path

import pytest

from g6.core import cloud_consent as cloud, consent_callers as callers, llm
from g6.metaharness.loop import _Counted, _Refused, _Requests


def review_fakes():
    name = 'consent_review_fakes'
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / 'metaharness/test_loop_review.py'
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


FLOWS = ('Delegates', 'Own', 'TriesAgain', 'Swallows', 'Declares', 'Rewrites', 'Varies', 'nested', 'blank-retry')


@pytest.mark.parametrize('kind', ['review-native', 'remote-ollama', 'remote-compatible',
                                 'late-ollama', 'late-compatible'])
def test_reviewer_retry_sender_without_a_grant_starts_no_http_request(tmp_path, monkeypatch, kind):
    from types import SimpleNamespace
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    sent = []
    monkeypatch.setattr(llm.urllib.request, 'build_opener', lambda *a: SimpleNamespace(
        open=lambda *a, **kw: sent.append(a)))
    if kind in ('remote-ollama', 'late-ollama'):
        endpoint = 'http://review.invalid' if kind == 'late-ollama' else 'http://unused.invalid'
        caller = llm.OllamaCaller(base_url=endpoint)
    else:
        endpoint = {'review-native': 'https://review.invalid', 'remote-compatible': 'https://unused.invalid/v1',
                    'late-compatible': 'http://unused.invalid'}[kind]
        caller = llm.OpenAICompatibleCaller('test', base_url=endpoint, api_key=None)
    requests = _Requests()
    requests.open(1)
    with pytest.raises(_Refused, match=cloud.REQUIRED):
        _Counted(caller, requests).complete('system', 'PLANTED_REVIEW_RETRY_INPUT')
    assert sent == []
    assert requests.refusal['code'] == cloud.REQUIRED


def test_opaque_reviewer_retry_sender_cannot_borrow_its_backends_grant(tmp_path, monkeypatch,
                                                                   accepted_cloud_dialog):
    from g6.core.model_calls import note_model_call
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    sent = []
    def send(handler, connection, request, **kwargs):
        sent.append(request)
        raise AssertionError('opaque caller reached the socket handoff')
    monkeypatch.setattr(llm.urllib.request.AbstractHTTPHandler, 'do_open', send)
    known = llm.OpenAICompatibleCaller('test', base_url='https://review.invalid', api_key=None)
    accepted_cloud_dialog('model_inputs', callers.destination(known).provider, text='synthetic retry inputs')

    class Foreign:
        def complete(self, system, user, *, max_tokens):
            note_model_call()
            return llm._post_json('https://review.invalid/completion', {}, {}, 1)['answer']

    requests = _Requests()
    requests.open(1)
    with cloud.collect_sources() as capture:
        with pytest.raises(_Refused, match=cloud.REQUIRED):
            _Counted(Foreign(), requests).complete('system', 'PLANTED_OPAQUE_REVIEW_RETRY')
    assert capture.refused_provider == 'cloud:unknown'
    assert sent == []


@pytest.mark.parametrize('flow', ['unmetered', 'self-rewrite', 'sdk-sender', 'prompt-only'])
def test_newly_rebuilt_flow_has_an_opaque_refusal_counterpart(tmp_path, monkeypatch, accepted_cloud_dialog, flow):
    from types import SimpleNamespace
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    sent = []
    fakes = review_fakes()
    if flow == 'unmetered':
        known = fakes.Retrying(lambda *a: sent.append(a) or 'answer')
    elif flow == 'self-rewrite':
        known = fakes.RewritesItself()
    elif flow == 'sdk-sender':
        class SDK(callers.SDKClient):
            base_url = 'https://api.anthropic.com'
            messages = SimpleNamespace(create=lambda **kw: sent.append(kw))
        known = llm.AnthropicCaller(client=SDK(), fallbacks=False)
    else:
        class PromptOnly(callers.CallerWrapper):
            def complete(self, system, user, **kw):
                sent.append(user)
                return 'answer'
        known = PromptOnly(llm.FakeLLM())
    accepted_cloud_dialog('fixture:rebuilt', 'anthropic:api', text='PLANTED_NEW_OPAQUE_FLOW')
    class Foreign:
        def complete(self, system, user, **kw):
            return known.complete(system, user, **kw)
    with pytest.raises(llm.LLMError, match=cloud.REQUIRED + '.*cloud:unknown'):
        callers.guard(Foreign()).complete('system', 'PLANTED_NEW_OPAQUE_FLOW')
    assert sent == []
    if flow == 'self-rewrite':
        assert known.inner.calls == []


def test_identified_sdk_captures_grant_and_revocation(tmp_path, monkeypatch, accepted_cloud_dialog):
    from types import SimpleNamespace
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    sent = []
    class SDK(callers.SDKClient):
        base_url = 'https://api.anthropic.com'
        messages = SimpleNamespace(create=lambda **kw: sent.append(kw) or
            SimpleNamespace(content=[], stop_reason='end_turn'))
    caller = callers.guard(llm.AnthropicCaller(client=SDK(), fallbacks=False))
    with pytest.raises(llm.LLMError, match=cloud.REQUIRED):
        caller.complete('system', 'PLANTED_SDK_SOURCE')
    assert sent == []
    accepted_cloud_dialog('fixture:sdk', 'anthropic:api', text='PLANTED_SDK_SOURCE')
    accepted_cloud_dialog('model_inputs', 'anthropic:api', text='PLANTED_SDK_SOURCE')
    caller.complete('system', 'PLANTED_SDK_SOURCE')
    assert 'PLANTED_SDK_SOURCE' in str(sent) and len(sent) == 1
    cloud.revoke('fixture:sdk', 'anthropic:api')
    with pytest.raises(llm.LLMError, match=cloud.REQUIRED):
        caller.complete('system', 'PLANTED_SDK_SOURCE')
    assert len(sent) == 1


def _recording_sdk(sent, callback=None):
    from types import SimpleNamespace
    class SDK(callers.SDKClient):
        base_url = 'https://api.anthropic.com'
        def __init__(self):
            def create(**kwargs):
                sent.append(kwargs)
                if callback:
                    callback()
                return SimpleNamespace(content=[], stop_reason='end_turn', usage=None, model='fixture-model')
            self.messages = SimpleNamespace(create=create)
    return SDK()


def test_raw_recording_sdk_resource_needs_a_grant(tmp_path, monkeypatch, accepted_cloud_dialog):
    from g6.harness.verification_proposer import RecordingClient
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    sent = []
    recorded = RecordingClient(_recording_sdk(sent))
    request = {'model': 'fixture-model', 'messages': [{'role': 'user', 'content': 'PLANTED_RAW_SDK'}]}
    with pytest.raises(llm.LLMError, match=cloud.REQUIRED):
        recorded.messages.create(**request)
    assert sent == []
    accepted_cloud_dialog('model_inputs', 'anthropic:api', text='PLANTED_RAW_SDK')
    recorded.messages.create(**request)
    assert 'PLANTED_RAW_SDK' in str(sent) and len(sent) == 1
    cloud.revoke('model_inputs', 'anthropic:api')
    with pytest.raises(llm.LLMError, match=cloud.REQUIRED):
        recorded.messages.create(**request)
    assert len(sent) == 1


def test_recorded_native_sdk_checks_once(tmp_path, monkeypatch, accepted_cloud_dialog):
    from g6.harness.verification_proposer import StudyCaller
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    accepted_cloud_dialog('model_inputs', 'anthropic:api', text='PLANTED_ONCE')
    sent = []
    caller = StudyCaller('fixture-model', client=_recording_sdk(sent))
    caller.complete('', 'PLANTED_ONCE')
    assert len(sent) == 1 and len(cloud.disclosures()) == 1


def test_copied_sdk_context_cannot_retain_admission(tmp_path, monkeypatch, accepted_cloud_dialog):
    import contextvars
    from g6.harness.verification_proposer import StudyCaller
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    accepted_cloud_dialog('model_inputs', 'anthropic:api', text='PLANTED_LATE_SDK')
    sent, contexts = [], []
    caller = StudyCaller('fixture-model', client=_recording_sdk(sent, lambda: contexts.append(contextvars.copy_context())))
    caller.complete('', 'PLANTED_LATE_SDK')
    cloud.revoke('model_inputs', 'anthropic:api')
    with pytest.raises(llm.LLMError, match=cloud.REQUIRED):
        contexts[0].run(caller.recording.messages.create, model='fixture-model',
                        messages=[{'role': 'user', 'content': 'PLANTED_LATE_SDK'}])
    assert len(sent) == 1


def wrapped(flow, backend):
    fakes = review_fakes()
    if flow == 'blank-retry':
        return fakes._retrying_blanks(lambda: backend)()
    return fakes.Delegates(fakes.Delegates(backend)) if flow == 'nested' else getattr(fakes, flow)(backend)


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    sent = []
    monkeypatch.setattr(llm, '_post_json', lambda url, body, *a: sent.append(body) or {
        'choices': [{'message': {'content': 'answer'}}]})
    return llm.OpenAICompatibleCaller('fake', base_url='https://api.openai.com/v1', api_key=None), sent


@pytest.mark.parametrize('flow', FLOWS)
def test_each_rebuilt_flow_without_a_grant_withholds_before_its_backend(flow, backend):
    raw, sent = backend
    wrapper = wrapped(flow, raw)
    assert callers.destination(wrapper).provider == 'openai:api'
    requests = _Requests()
    requests.open(10)
    with pytest.raises(_Refused, match=cloud.REQUIRED):
        _Counted(wrapper, requests).complete('system', 'PLANTED_UNGRANTED')
    assert requests.refusal['code'] == cloud.REQUIRED
    assert sent == [] and requests.sent == 0


@pytest.mark.parametrize('flow', FLOWS)
def test_each_rebuilt_flow_uses_the_real_dialog_grant(flow, backend, accepted_cloud_dialog):
    raw, sent = backend
    accepted_cloud_dialog('fixture:wrapper-flow', 'openai:api', text='synthetic original and rewritten requests')
    accepted_cloud_dialog('model_inputs', 'openai:api', text='synthetic original and rewritten requests')
    wrapper = wrapped(flow, raw)
    assert callers.guard(wrapper).complete('system', 'request') == 'answer'
    assert len(sent) == 1
    if flow == 'Rewrites':
        assert sent[0]['messages'][1]['content'] == 'fixed request'


@pytest.mark.parametrize('flow', FLOWS)
def test_foreign_opaque_version_of_each_flow_remains_unknown(flow, backend, accepted_cloud_dialog):
    raw, sent = backend
    accepted_cloud_dialog('fixture:wrapper-flow', 'openai:api', text='synthetic original and rewritten requests')

    class Foreign:
        def __init__(self, inner):
            self.inner = inner

        def complete(self, system, user, *, max_tokens=1024):
            return self.inner.complete(system, user, max_tokens=max_tokens)

    foreign = Foreign(wrapped(flow, raw))
    assert callers.destination(foreign).provider == 'cloud:unknown'
    with pytest.raises(llm.LLMError, match=cloud.REQUIRED + '.*cloud:unknown'):
        callers.guard(foreign).complete('system', 'PLANTED_OPAQUE')
    assert sent == []


def test_an_unknown_wrapper_does_not_gain_locality_by_copying_the_contracts_name():
    foreign = type('CallerWrapper', (), {'inner': llm.FakeLLM()})()
    assert callers.destination(foreign).provider == 'cloud:unknown'


@pytest.mark.parametrize('opaque', [False, True], ids=['no-grant', 'opaque-with-inner-grant'])
def test_followup_rate_limit_flow_withholds_before_its_transport(
        tmp_path, monkeypatch, accepted_cloud_dialog, opaque):
    import io
    import urllib.error

    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    sent = []

    def send(handler, connection, request, **kwargs):
        sent.append(request)
        raise urllib.error.HTTPError(request.full_url, 429, 'rate limited', {'Retry-After': '0'}, io.BytesIO(b'limit'))
    monkeypatch.setattr(llm.urllib.request.AbstractHTTPHandler, 'do_open', send)
    raw = llm.OpenAICompatibleCaller('fake', base_url='https://unused.invalid', api_key=None)
    if opaque:
        accepted_cloud_dialog('model_inputs', callers.destination(raw).provider, text='PLANTED_RATE_LIMIT')
        accepted_cloud_dialog('fixture:rate-limit', callers.destination(raw).provider, text='PLANTED_RATE_LIMIT')

        class Foreign:
            def __init__(self, inner):
                self.inner = inner

            def complete(self, system, user, *, max_tokens=1024):
                return self.inner.complete(system, user, max_tokens=max_tokens)

        raw = Foreign(raw)
        assert callers.destination(raw).provider == 'cloud:unknown'
    requests = _Requests()
    requests.open(10)
    with pytest.raises(_Refused, match=cloud.REQUIRED):
        _Counted(raw, requests).complete('system', 'PLANTED_RATE_LIMIT')
    assert requests.refusal['code'] == cloud.REQUIRED and requests.limited is None
    assert sent == [] and requests.sent == 0


def test_foreign_followup_context_wrapper_never_reaches_its_local_fake(tmp_path, monkeypatch):
    import threading

    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    path = Path(__file__).resolve().parents[1] / 'metaharness/test_loop_followup.py'
    spec = importlib.util.spec_from_file_location('consent_followup_fakes', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    requests, failures = _Requests(), []
    inner = module._late_refusal(requests, threading.Event(), failures)

    class Foreign:
        def __init__(self, caller):
            self.inner = caller

        def complete(self, system, user, *, max_tokens=1024):
            return self.inner.complete(system, user, max_tokens=max_tokens)

    opaque = Foreign(inner)
    assert callers.destination(opaque).provider == 'cloud:unknown'
    with pytest.raises(llm.LLMError, match=cloud.REQUIRED + '.*cloud:unknown'):
        callers.guard(opaque).complete('system', 'PLANTED_CONTEXT')
    assert inner.inner.calls == [] and inner.workers == [] and failures == []
