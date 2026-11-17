"""Transport refusals and authority lifetime without filesystem-dependent fixtures."""
from contextvars import copy_context
from types import SimpleNamespace

import pytest

from g6.core import cloud_consent as cloud, consent_callers, host_models, llm


@pytest.mark.parametrize('provider', ('ollama', 'openai', 'anthropic', 'claude_code', 'codex', 'sdk'))
@pytest.mark.parametrize('switch', ('none', 'file_kill', 'env_kill'))
@pytest.mark.parametrize('images', (False, True))
def test_every_transport_refuses_after_settings_change(monkeypatch, provider, switch, images):
    filed, sent = {}, []
    monkeypatch.delenv('G6_DISABLE_LLM', raising=False)
    monkeypatch.setattr(llm.key_file, 'read', lambda: dict(filed))
    monkeypatch.setattr(llm, '_post_json', lambda *a: sent.append(a) or {
        'message': {'content': 'answer'}, 'choices': [{'message': {'content': 'answer'}}]})
    monkeypatch.setattr(host_models, '_run', lambda *a: sent.append(a))
    if '__new__' in vars(host_models._HostCaller):
        monkeypatch.setattr(host_models._HostCaller, '__new__',
                            staticmethod(lambda kind, *a, **k: object.__new__(kind)))
    class Client(consent_callers.SDKClient):
        base_url = 'http://127.0.0.1:9'

    client = Client()
    client.messages = SimpleNamespace(create=lambda **kw: sent.append(kw) or SimpleNamespace(
        content=[SimpleNamespace(type='text', text='answer')], stop_reason='end_turn'))
    callers = {
        'ollama': lambda: llm.OllamaCaller(),
        'openai': lambda: llm.OpenAICompatibleCaller('synthetic', base_url='https://unused.invalid', api_key=None),
        'anthropic': lambda: llm.AnthropicCaller(client=client, fallbacks=False),
        'claude_code': lambda: host_models.ClaudeCodeCaller(),
        'codex': lambda: host_models.CodexCaller(),
        'sdk': lambda: None,
    }
    caller = callers[provider]()
    if switch == 'env_kill':
        monkeypatch.setenv('G6_DISABLE_LLM', '1')
    else:
        filed['G6_LLM_PROVIDER' if switch == 'none' else 'G6_DISABLE_LLM'] = 'none' if switch == 'none' else '1'
    code = 'G6_E_NO_MODEL' if switch == 'none' else 'G6_E_LLM_DISABLED'
    with pytest.raises(llm.LLMError, match=code):
        if provider == 'sdk':
            with consent_callers.sdk_send(client, {'model': 'synthetic'}):
                sent.append('sdk')
        elif images and provider in ('ollama', 'openai', 'anthropic'):
            caller.complete_with_images('system', 'user', [llm.Image(b'\x89PNG\r\n\x1a\nsynthetic', 'image/png')])
        else:
            caller.complete('system', 'user')
    assert sent == []


@pytest.mark.parametrize('nested', (False, True))
def test_capture_expiry_is_shared_with_unowned_contexts(nested):
    ref = cloud.Source.text('g6_operational', 'synthetic', 'synthetic')
    with cloud.collect_sources() as request:
        request.client = ('openai:codex', object())
        if nested:
            with cloud.collect_sources():
                copied = copy_context()
        else:
            copied = copy_context()
        assert cloud._basis({}, 'openai:codex', ref, (), True) == 'calling_client'
    assert copied.run(cloud.calling_capture) is None
    assert copied.run(cloud._basis, {}, 'openai:codex', ref, (), True) == ''
    grant = {(ref.source_id, 'openai:codex'): {'kind': 'grant'}}
    assert copied.run(cloud._basis, grant, 'openai:codex', ref, (), True) == 'grant'


def test_owned_job_snapshot_ends_when_its_job_ends():
    from threading import Event
    from g6.metaharness.jobs import Jobs

    release, jobs, copied = Event(), Jobs(), []
    try:
        with cloud.collect_sources() as request:
            request.client = ('openai:codex', object())

            def operation():
                assert release.wait(5)
                capture = cloud.calling_capture()
                assert capture is not None and capture is not request
                assert capture.client == request.client
                copied.append(copy_context())
                return cloud.bind_output({'owned': True}, ())

            pending = jobs.start(operation)
        release.set()
        jobs.close()
        assert jobs.status(pending['job_id'])['result'] == {'owned': True}
        assert copied[0].run(cloud.calling_capture) is None
    finally:
        release.set()
        jobs.close()


@pytest.mark.parametrize('switch', ('none', 'file_kill', 'env_kill'))
def test_counted_sdk_resource_rechecks_settings(monkeypatch, switch):
    filed, sent = {}, []
    monkeypatch.setattr(llm.key_file, 'read', lambda: dict(filed))
    monkeypatch.delenv('G6_DISABLE_LLM', raising=False)

    class Client(consent_callers.SDKClient):
        base_url = 'http://127.0.0.1:9'

    client = Client()
    with consent_callers._sdk_call(client, 'local:loopback'):
        if switch == 'env_kill':
            monkeypatch.setenv('G6_DISABLE_LLM', '1')
        else:
            filed['G6_LLM_PROVIDER' if switch == 'none' else 'G6_DISABLE_LLM'] = 'none' if switch == 'none' else '1'
        with pytest.raises(llm.LLMError, match='G6_E_NO_MODEL' if switch == 'none' else 'G6_E_LLM_DISABLED'):
            with consent_callers.sdk_send(client, {'model': 'synthetic'}):
                sent.append('synthetic')
    assert sent == []


def test_sdk_preparation_cannot_keep_an_earlier_settings_decision(monkeypatch):
    filed, sent = {}, []
    monkeypatch.setattr(llm.key_file, 'read', lambda: dict(filed))
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: sent.append(kw) or SimpleNamespace(
        content=[SimpleNamespace(type='text', text='answer')], stop_reason='end_turn')))
    caller = llm.AnthropicCaller(fallbacks=False)

    def prepare():
        filed['G6_LLM_PROVIDER'] = 'none'
        return client

    monkeypatch.setattr(caller, '_get_client', prepare)
    with pytest.raises(llm.LLMError, match='G6_E_NO_MODEL'):
        caller.complete('system', 'user')
    assert sent == []
