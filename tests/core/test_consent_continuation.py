# SPDX-License-Identifier: Apache-2.0
"""Regression checks for section 16's continuation, with no live models."""
from types import SimpleNamespace
import asyncio
from contextlib import contextmanager

import pytest

from g6.core import consent_callers as callers, llm
from g6.reasoning.budget import MeteredCaller


def test_published_wrapper_contract_reads_real_inner_identity(monkeypatch):
    assert hasattr(callers, 'CallerWrapper'), 'no published source-aware caller wrapper contract'
    sent = []
    monkeypatch.setattr(llm, '_post_json', lambda url, body, *a: sent.append(body) or {
        'message': {'content': 'local'}})

    class Rewrites(callers.CallerWrapper):
        def complete(self, system, user, *, max_tokens=1024):
            return self.inner.complete('fixed system', 'fixed request', max_tokens=max_tokens)

    inner = llm.OllamaCaller(base_url='http://127.0.0.1:11434')
    wrapper = Rewrites(inner)
    assert wrapper._inner is inner
    assert callers.destination(wrapper).local
    assert callers.guard(wrapper).complete('', 'original request') == 'local'
    assert sent[0]['messages'][1]['content'] == 'fixed request'
    inner.base_url = 'https://api.openai.com/v1'
    assert not callers.destination(wrapper).local


def test_identifiable_rewriting_cloud_wrapper_needs_dialog_grant(
        tmp_path, monkeypatch, accepted_cloud_dialog):
    assert hasattr(callers, 'CallerWrapper'), 'no published source-aware caller wrapper contract'
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    sent = []
    monkeypatch.setattr(llm, '_post_json', lambda url, body, *a: sent.append(body) or {
        'choices': [{'message': {'content': 'answer'}}]})

    class Rewrites(callers.CallerWrapper):
        def complete(self, system, user, *, max_tokens=1024):
            return self.inner.complete('fixed system', 'fixed request', max_tokens=max_tokens)

    wrapper = Rewrites(llm.OpenAICompatibleCaller('fake', base_url='https://api.openai.com/v1', api_key=None))
    assert callers.destination(wrapper).provider == 'openai:api'
    with pytest.raises(llm.LLMError, match='G6_E_CLOUD_CONSENT_REQUIRED'):
        callers.guard(wrapper).complete('', 'original')
    assert sent == []
    accepted_cloud_dialog('fixture:rewrite', 'openai:api', text='original and fixed request')
    accepted_cloud_dialog('model_inputs', 'openai:api', text='original and fixed request')
    assert callers.guard(wrapper).complete('', 'original') == 'answer'
    assert sent[0]['messages'][1]['content'] == 'fixed request'


def test_opaque_rewriting_wrapper_stays_unknown_even_with_inner_provider_grant(
        tmp_path, monkeypatch, accepted_cloud_dialog):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    sent = []
    monkeypatch.setattr(llm, '_post_json', lambda *a: sent.append(a) or {})

    class Foreign:
        def __init__(self, inner):
            self.inner = inner

        def complete(self, system, user, *, max_tokens=1024):
            return self.inner.complete('fixed system', 'fixed request', max_tokens=max_tokens)

    wrapped = Foreign(llm.OpenAICompatibleCaller('fake', base_url='https://api.openai.com/v1', api_key=None))
    accepted_cloud_dialog('fixture:rewrite', 'openai:api', text='original and fixed request')
    assert callers.destination(wrapped).provider == 'cloud:unknown'
    with pytest.raises(llm.LLMError, match='G6_E_CLOUD_CONSENT_REQUIRED.*cloud:unknown'):
        callers.guard(wrapped).complete('', 'original')
    assert sent == []


def test_unknown_recipient_cannot_be_granted_through_the_dialog(tmp_path, monkeypatch):
    from g6.mcp.cloud_consent import request_grant
    from g6.core import cloud_consent as cloud
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))

    class Session:
        async def elicit_form(self, **kwargs):
            pytest.fail('an unnamed recipient must never be offered for consent')

    result = asyncio.run(request_grant(SimpleNamespace(session=Session()), 'fixture:rewrite', 'cloud:unknown'))
    assert result == {'status': 'invalid', 'code': 'G6_E_CLOUD_CONSENT_INPUT'}
    assert cloud.grants() == []


@pytest.mark.parametrize('kind', ['http', 'process'])
def test_an_instance_override_cannot_borrow_the_native_callers_identity(tmp_path, monkeypatch, kind):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    sent = []
    raw = (llm.OllamaCaller(base_url='http://127.0.0.1:11434') if kind == 'http' else llm.FakeLLM())
    raw.complete = lambda *a, **k: sent.append(a) or 'opaque recipient'
    assert callers.destination(raw).provider == 'cloud:unknown'
    with pytest.raises(llm.LLMError, match='G6_E_CLOUD_CONSENT_REQUIRED'):
        callers.guard(raw).complete('', 'PLANTED_OVERRIDE')
    assert sent == []


def test_a_proxy_cannot_borrow_the_named_endpoints_grant(tmp_path, monkeypatch, accepted_cloud_dialog):
    from g6.core import cloud_consent as cloud
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    sent = []
    monkeypatch.setattr(llm, '_post_json', lambda *a: sent.append(a) or {})
    endpoint = 'http://127.0.0.1:11434'
    accepted_cloud_dialog('fixture:proxy', 'endpoint:' + cloud.sha256(endpoint), text='PLANTED_PROXY')
    import urllib.request
    monkeypatch.setattr(urllib.request, 'getproxies', lambda: {'http': 'http://proxy.invalid'})
    monkeypatch.setattr(urllib.request, 'proxy_bypass', lambda host: False)
    raw = llm.OpenAICompatibleCaller('fake', base_url=endpoint, api_key=None)
    assert callers.destination(raw).provider == 'endpoint:' + cloud.sha256('http://proxy.invalid')
    with pytest.raises(llm.LLMError, match=cloud.REQUIRED):
        callers.guard(raw).complete('', 'PLANTED_PROXY')
    assert sent == []


@pytest.mark.parametrize('wrap', [
    lambda c: llm.EmptyRetry(c),
    lambda c: llm.TimingLLMCaller(c, lambda _: None),
    lambda c: MeteredCaller(c),
    lambda c: llm.EmptyRetry(MeteredCaller(llm.TimingLLMCaller(c, lambda _: None))),
    lambda c: llm.FallbackCaller([c]),
])
def test_g6_wrapper_locality_follows_inner_endpoint(wrap):
    inner = llm.OllamaCaller(base_url='http://127.0.0.1:11434')
    wrapped = wrap(inner)
    assert callers.destination(wrapped).local
    inner.base_url = 'https://models.invalid'
    assert not callers.destination(wrapped).local


def test_a_foreign_wrapper_cannot_borrow_g6_locality():
    class Foreign:
        def __init__(self, inner):
            self._inner = inner
    foreign = Foreign(llm.OllamaCaller(base_url='http://127.0.0.1:11434'))
    assert not callers.destination(foreign).local


def test_a_foreign_fake_subclass_cannot_hide_a_remote_caller(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    sent = []
    class Foreign(llm.FakeLLM):
        def complete(self, system, user, **kwargs):
            sent.append(user)
            return 'remote'
    assert not callers.destination(Foreign()).local
    with pytest.raises(llm.LLMError, match='G6_E_CLOUD_CONSENT_REQUIRED'):
        callers.guard(Foreign()).complete('', 'PLANTED_REMOTE')
    assert not sent


def test_cyclic_g6_wrappers_are_unknown_and_refuse(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    cyclic = llm.EmptyRetry(llm.FakeLLM())
    cyclic._inner = cyclic
    assert not callers.destination(cyclic).local
    with pytest.raises(llm.LLMError, match='G6_E_CLOUD_CONSENT_REQUIRED'):
        callers.guard(cyclic).complete('', 'PLANTED_CYCLE')


def test_a_foreign_endpoint_override_cannot_claim_loopback_locality():
    class Foreign(llm.OllamaCaller):
        def complete(self, system, user, **kwargs):
            return 'an opaque transport'
    assert not callers.destination(Foreign(base_url='http://127.0.0.1:11434')).local


def test_process_local_callers_are_classified_without_loading_a_model():
    from g6.grounding.judges.nli import LocalNLICaller
    from g6.metaharness.lora import AdapterCaller
    for cls in (LocalNLICaller, AdapterCaller):
        caller = object.__new__(cls)
        assert callers.destination(caller).local
        assert callers.guard(caller) is caller


def test_k_keeps_the_earlier_boundary_refusal(tmp_path, monkeypatch):
    from g6.harness import completion_loop as loop
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    monkeypatch.setattr(loop.repo_tests, 'checked_repo', lambda _: tmp_path)
    monkeypatch.setattr(loop.repo_tests, 'change', lambda *a: SimpleNamespace(repo=tmp_path, sources=('a.py',)))
    monkeypatch.setattr(loop, '_patch_now', lambda *a: 'patch')
    monkeypatch.setattr(loop, '_owe_row', lambda *a: None)
    monkeypatch.setattr(loop, '_write_row', lambda *a: None)
    monkeypatch.setattr(loop.proposer, 'boundary', lambda *a: 'boundary unavailable')
    monkeypatch.setattr(loop, '_scouts_chosen', lambda: False)
    from g6.core.host_models import CodexCaller
    (tmp_path / 'session').mkdir()
    result = loop._advance(tmp_path, 'task', 'base', tmp_path / 'session', None, CodexCaller(), '', True, '')
    assert result.code == 'G6_E_ISOLATION_UNAVAILABLE'
    assert result.ending == 'untested'


def test_metered_local_call_sends_without_consent(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    sent = []
    monkeypatch.setattr(llm, '_post_json', lambda url, body, *a: sent.append(body) or {'message': {'content': 'local'}})
    inner = llm.OllamaCaller(base_url='http://127.0.0.1:11434')
    wrapped = callers.guard(MeteredCaller(inner))
    assert wrapped.complete('', 'PLANTED_LOCAL') == 'local'
    assert len(sent) == 1


def test_consent_refusal_is_latched_as_a_loop_cause(tmp_path, monkeypatch):
    from g6.metaharness.loop import _Counted, _Refused, _Requests
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    sent = []
    monkeypatch.setattr(llm, '_post_json', lambda *a: sent.append(a) or {})
    requests = _Requests()
    requests.open(10)
    caller = llm.OpenAICompatibleCaller('model', base_url='https://api.openai.com/v1', api_key=None)
    with pytest.raises(_Refused):
        _Counted(caller, requests).complete('', 'PLANTED_UNGRANTED')
    assert requests.refusal['code'] == 'G6_E_CLOUD_CONSENT_REQUIRED'
    assert sent == []
    assert requests.sent == 0


def test_mcp_checks_sources_once_at_the_transport_send(tmp_path, monkeypatch, accepted_cloud_dialog):
    from fastmcp import Client
    from mcp.types import Implementation
    from g6.core import cloud_consent as cloud
    from g6.mcp import build_server
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    accepted_cloud_dialog(cloud.repository_source(tmp_path), 'openai:codex', text='NAMED_REPOSITORY')
    seen = []
    original = cloud.admission
    @contextmanager
    def counted(*args, **kwargs):
        seen.append(args[0])
        with original(*args, **kwargs):
            yield
    monkeypatch.setattr(cloud, 'admission', counted)
    server = build_server()
    @server.tool()
    def repository_content(repo: str) -> dict:
        return cloud.bind_output({'result': 'NAMED_REPOSITORY'},
            (cloud.Source.text(cloud.repository_source(repo), 'a.py', 'NAMED_REPOSITORY'),))
    async def read():
        async with Client(server, client_info=Implementation(name='codex', version='test')) as client:
            return str(await client.call_tool('repository_content', {'repo': str(tmp_path)}))
    assert 'NAMED_REPOSITORY' in asyncio.run(read())
    assert seen == ['openai:codex']


def test_compact_review_keeps_sources_and_requires_its_storage_grant(
        tmp_path, monkeypatch, accepted_cloud_dialog):
    from g6.core import cloud_consent as cloud
    from g6.core.storage import g6_home
    from g6.mcp import review_reply
    from g6.mcp.cloud_consent import protect_reply
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    refs = accepted_cloud_dialog('fixture:review', 'openai:codex', text='PLANTED_REVIEW')
    store = cloud.repository_source(g6_home() / 'reviews')
    context = SimpleNamespace(session=SimpleNamespace(client_params=SimpleNamespace(
        clientInfo=SimpleNamespace(name='codex'))))
    monkeypatch.setattr(review_reply, 'save', lambda *a: 'PLANTED_UNSHARED_STORAGE_PATH')
    with cloud.collect_sources():
        original = cloud.bind_output({'assessment': {'report': 'PLANTED_REVIEW'}}, refs)
        shown = review_reply.finish(original, 'job')
        sources = cloud.output_sources(shown)
        assert sources is not None
        denied = protect_reply(context, shown, sources)
        assert 'PLANTED' not in str(denied)
        accepted_cloud_dialog(store, 'openai:codex', text='PLANTED_UNSHARED_STORAGE_PATH')
        assert protect_reply(context, shown, sources) == shown


def test_withheld_k_reply_keeps_host_envelope_fields_and_earlier_code(tmp_path, monkeypatch):
    from g6.core import cloud_consent as cloud
    from g6.mcp import review_loop
    from g6.mcp.cloud_consent import protect_reply
    from g6.harness import completion_hook
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    monkeypatch.setattr(completion_hook, 'folder', lambda _: tmp_path / 'session')
    monkeypatch.setattr(review_loop, '_step', lambda *a: {
        'ok': False, 'action': 'stop', 'ending': 'untested', 'report': 'PLANTED_PRIVATE_REPORT',
        'error': '[G6_E_ISOLATION_UNAVAILABLE] PLANTED_HOST_PATH',
        'request_id': 'host-request', 'run_id': 'host-run'})
    context = SimpleNamespace(session=SimpleNamespace(client_params=SimpleNamespace(
        clientInfo=SimpleNamespace(name='codex'))))
    with cloud.collect_sources():
        result = review_loop.step(str(tmp_path), 'task', 'session')
        denied = protect_reply(context, result, cloud.output_sources(result))
    assert denied['action'] == 'stop' and denied['ending'] == 'untested'
    assert denied['request_id'] == 'host-request' and denied['run_id'] == 'host-run'
    assert 'report' in denied
    assert denied['warning_card']['code'] == 'G6_E_ISOLATION_UNAVAILABLE'
    assert denied['cloud_consent_code'] == 'G6_E_CLOUD_CONSENT_REQUIRED'
    assert 'PLANTED' not in str(denied)


def test_refusal_names_only_source_identifier_and_provider(tmp_path, monkeypatch):
    from g6.core import cloud_consent as cloud
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    source = cloud.Source.text('document-a', 'PLANTED_UNSHARED_PATH', 'PLANTED_CONTENT')
    with cloud.collect_sources() as capture:
        # The identifier was literally supplied in this request; its contents
        # remain ungranted. An earlier private label is tested separately.
        capture.supplied.add(cloud.input_source('document-a'))
        with pytest.raises(llm.LLMError) as caught:
            with cloud.admission('openai:codex', (source,), cloud.sha256('PLANTED_CONTENT')):
                pytest.fail('an ungranted send was admitted')
    assert 'document-a' in str(caught.value) and 'openai:codex' in str(caught.value)
    assert 'PLANTED' not in str(caught.value)
