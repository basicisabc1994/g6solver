# SPDX-License-Identifier: Apache-2.0
"""Section 16: admission and the three measurement rows, with no live providers."""
import asyncio
import importlib
import json
from types import SimpleNamespace

import pytest

from g6.core import llm, host_models
from tests.host_stand_in import stand_in_hosts


def consent_modules():
    for name in ('g6.core.cloud_consent', 'g6.core.consent_callers'):
        assert importlib.util.find_spec(name), f'{name}: no send-time consent check exists'
    return importlib.import_module('g6.core.cloud_consent'), importlib.import_module('g6.core.consent_callers')


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))


def dialog(action='accept', content=None):
    class Session:
        async def elicit_form(self, **kwargs):
            self.question = kwargs['message']
            return SimpleNamespace(action=action, content=content or {'value': 'Person'})
    return SimpleNamespace(session=Session(), request_id='one')


def request_grant(*args, **kwargs):
    return importlib.import_module('g6.mcp.cloud_consent').request_grant(*args, **kwargs)


def grant(c, source, provider):
    ctx = dialog()
    result = asyncio.run(request_grant(ctx, source, provider))
    assert result['status'] == 'granted'
    assert 'cannot be recalled' in ctx.session.question
    # The generic provider also declares model_inputs; a positive source test
    # grants that separate contract without replacing the carried source.
    if source != 'model_inputs':
        assert asyncio.run(request_grant(dialog(), 'model_inputs', provider))['status'] == 'granted'


@pytest.mark.parametrize('kind', ['ollama_remote', 'openai', 'anthropic', 'claude_code', 'codex', 'unknown'])
def test_measurement_ungranted_never_reaches_cloud_boundary(kind, monkeypatch):
    c, callers = consent_modules()
    sent = []
    raw = make_caller(kind, monkeypatch, sent)
    wrapped = callers.guard(raw)
    with pytest.raises(llm.LLMError, match='G6_E_CLOUD_CONSENT_REQUIRED'):
        wrapped.complete('instructions', 'PLANTED_UNGRANTED')
    assert sent == []


def make_caller(kind, monkeypatch, sent):
    if kind == 'unknown':
        class Unknown:
            def complete(self, system, user, **kwargs):
                sent.append(user)
                return 'answer'
        return Unknown()
    types = {'ollama_remote': llm.OllamaCaller, 'local': llm.OllamaCaller,
             'local_openai': llm.OpenAICompatibleCaller, 'openai': llm.OpenAICompatibleCaller,
             'anthropic': llm.AnthropicCaller, 'claude_code': host_models.ClaudeCodeCaller, 'codex': host_models.CodexCaller}
    cls = types[kind]
    def send(self, system, user, **kwargs):
        sent.append(user)
        return 'answer'
    if kind in ('claude_code', 'codex'):  # at the launcher: a watched host never resolves a patched complete
        stand_in_hosts(monkeypatch, lambda system, user: send(None, system, user))
    else:
        monkeypatch.setattr(cls, 'complete', send)
    if kind in ('local', 'ollama_remote'):
        return cls(base_url='http://127.0.0.1:11434' if kind == 'local' else 'https://models.invalid')
    if kind in ('openai', 'local_openai'):
        return cls('model', base_url='http://[::1]:8000/v1' if kind == 'local_openai' else 'https://api.openai.com/v1', api_key=None)
    return cls()


@pytest.mark.parametrize('kind', ['local', 'local_openai'])
def test_measurement_local_capability_with_no_grant(kind, monkeypatch):
    c, callers = consent_modules()
    sent = []
    assert callers.guard(make_caller(kind, monkeypatch, sent)).complete('', 'PLANTED_LOCAL') == 'answer'
    assert sent == ['PLANTED_LOCAL']


def test_every_source_required_and_revocation_checked_each_send(tmp_path, monkeypatch):
    c, callers = consent_modules()
    sent = []
    wrapped = callers.guard(make_caller('codex', monkeypatch, sent))
    a = c.Source.text('source-a', 'file-a', 'PLANTED_A')
    b = c.Source.text('source-b', 'passage-b', 'PLANTED_B')
    grant(c, 'source-a', 'openai:codex')
    with c.request_sources((a, b)), pytest.raises(llm.LLMError):
        wrapped.complete('', 'PLANTED_A PLANTED_B')
    grant(c, 'source-b', 'openai:codex')
    with c.request_sources((a, b)):
        assert wrapped.complete('', 'PLANTED_A PLANTED_B') == 'answer'
        c.revoke('source-a', 'openai:codex')
        with pytest.raises(llm.LLMError):
            wrapped.complete('', 'PLANTED_A PLANTED_B')
    assert sent == ['PLANTED_A PLANTED_B']
    log = c.disclosures()
    assert log and all('provider' in row and 'time' in row for row in log)
    encoded = json.dumps(log)
    assert 'PLANTED' not in encoded
    assert a.content_sha256 in encoded and b.content_sha256 in encoded
    # A fresh authority reads durable state, not a cached grant.
    assert not c.is_granted('source-a', 'openai:codex')


@pytest.mark.parametrize('action,content', [('decline', None), ('cancel', None), ('accept', {'value': ''})])
def test_only_valid_elicitation_acceptance_grants(action, content):
    c, _ = consent_modules()
    assert asyncio.run(request_grant(dialog(action, content), 'source', 'openai:codex'))['status'] != 'granted'
    assert not c.is_granted('source', 'openai:codex')


def test_unavailable_dialog_no_terminal_or_parameter_fallback():
    c, _ = consent_modules()
    assert asyncio.run(request_grant(None, 'source', 'openai:codex'))['status'] != 'granted'
    assert not c.is_granted('source', 'openai:codex')


def test_repository_identity_canonical_once_per_provider(tmp_path):
    c, _ = consent_modules()
    repo = tmp_path / 'repo'
    repo.mkdir()
    source = c.repository_source(repo)
    assert source == c.repository_source(repo / '.')
    grant(c, source, 'openai:codex')
    assert asyncio.run(request_grant(dialog('decline'), source, 'openai:codex'))['status'] == 'granted'
    assert not c.is_granted(source, 'anthropic:claude_code')


def test_corrupt_or_unreadable_ledger_denies_even_old_grant(monkeypatch):
    c, callers = consent_modules()
    sent = []
    raw = callers.guard(make_caller('codex', monkeypatch, sent))
    grant(c, 'source', 'openai:codex')
    c.ledger_path().write_text('{broken', encoding='utf-8')
    with c.request_sources((c.Source.text('source', 'file', 'secret'),)), pytest.raises(llm.LLMError):
        raw.complete('', 'secret')
    assert not sent


@pytest.mark.parametrize('url,want', [('http://127.0.0.1:11434', True), ('http://[::1]:8080', True),
    ('http://localhost:8000', True), ('https://openrouter.ai/api', False), ('http://proxy:8000', False),
    ('http://127.0.0.1.evil.invalid', False), ('', False)])
def test_local_requires_visible_loopback(url, want):
    _, callers = consent_modules()
    assert callers.loopback(url) is want


def test_endpoint_change_cannot_reuse_local_admission(monkeypatch):
    c, callers = consent_modules()
    sent = []
    raw = make_caller('local', monkeypatch, sent)
    wrapped = callers.guard(raw)
    assert wrapped.complete('', 'local') == 'answer'
    raw.base_url = 'https://proxy.invalid'
    with pytest.raises(llm.LLMError):
        wrapped.complete('', 'PLANTED')
    assert sent == ['local']


def test_pending_dialog_cannot_undo_revocation():
    c, _ = consent_modules()
    class Session:
        async def elicit_form(self, **kwargs):
            c.revoke('source', 'openai:codex')
            return SimpleNamespace(action='accept', content={'value': 'Person'})
    result = asyncio.run(request_grant(SimpleNamespace(session=Session(), request_id='one'), 'source', 'openai:codex'))
    assert result['status'] == 'stale'
    assert not c.is_granted('source', 'openai:codex')


def test_failed_grant_write_never_authorizes(monkeypatch):
    c, _ = consent_modules()
    from g6.core.ledger import ChainLedger
    def stopped(*args, **kwargs):
        raise OSError('stopped before durable append')
    monkeypatch.setattr(ChainLedger, 'append', stopped)
    with pytest.raises(llm.LLMError):
        asyncio.run(request_grant(dialog(), 'source', 'openai:codex'))
    assert not c.is_granted('source', 'openai:codex')


def test_disclosure_failure_blocks_send(monkeypatch):
    c, callers = consent_modules()
    from g6.core.ledger import ChainLedger
    sent = []
    caller = callers.guard(make_caller('codex', monkeypatch, sent))
    grant(c, 'source', 'openai:codex')
    original = ChainLedger.append
    def stopped(self, kind, *args, **kwargs):
        if kind == 'disclosure':
            raise OSError('stopped before disclosure append')
        return original(self, kind, *args, **kwargs)
    monkeypatch.setattr(ChainLedger, 'append', stopped)
    with c.request_sources((c.Source.text('source', 'file', 'secret'),)), pytest.raises(llm.LLMError):
        caller.complete('', 'secret')
    assert not sent


def test_send_and_revoke_have_one_locked_order(monkeypatch):
    c, callers = consent_modules()
    import threading
    from concurrent.futures import ThreadPoolExecutor
    sent, entered, release = [], threading.Event(), threading.Event()
    raw = make_caller('codex', monkeypatch, sent)
    def slow(system, user):
        entered.set()
        assert release.wait(5)
        sent.append(user)
        return 'answer'
    stand_in_hosts(monkeypatch, slow)
    wrapped = callers.guard(raw)
    grant(c, 'source', 'openai:codex')
    source = c.Source.text('source', 'file', 'secret')
    def send():
        with c.request_sources((source,)):
            return wrapped.complete('', 'secret')
    with ThreadPoolExecutor(max_workers=2) as pool:
        sending = pool.submit(send)
        assert entered.wait(5)
        revoking = pool.submit(c.revoke, 'source', 'openai:codex')
        release.set()
        assert sending.result() == 'answer'
        revoking.result()
    with pytest.raises(llm.LLMError):
        send()
    assert sent == ['secret']


def test_cloud_denial_does_not_remove_local_fallback(monkeypatch):
    c, callers = consent_modules()
    cloud_sent, local_sent = [], []
    cloud = make_caller('codex', monkeypatch, cloud_sent)
    local = make_caller('local', monkeypatch, local_sent)
    fallback = callers.guard(llm.FallbackCaller([cloud, local], names=['cloud', 'local']))
    assert fallback.complete('', 'PLANTED_LOCAL_FALLBACK') == 'answer'
    assert cloud_sent == [] and local_sent == ['PLANTED_LOCAL_FALLBACK']
    assert fallback.answered == 'local'
    assert 'G6_E_CLOUD_CONSENT_REQUIRED' in fallback.failures[0][1]


def test_retry_rechecks_revocation(monkeypatch):
    c, callers = consent_modules()
    sent = []
    raw = make_caller('codex', monkeypatch, sent)
    retry = callers.guard(llm.EmptyRetry(raw))
    grant(c, 'source', 'openai:codex')
    def retries(call, max_tokens):
        assert call(max_tokens) == 'answer'
        c.revoke('source', 'openai:codex')
        return call(max_tokens)
    monkeypatch.setattr(llm.EmptyRetry, '_retry', staticmethod(retries))
    with c.request_sources((c.Source.text('source', 'file', 'secret'),)), pytest.raises(llm.LLMError):
        retry.complete('', 'secret')
    assert sent == ['secret']


def test_injected_sdk_endpoint_is_unknown_even_with_anthropic_grant(monkeypatch):
    c, callers = consent_modules()
    sent = []
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: sent.append(kw)))
    raw = llm.AnthropicCaller(client=client, fallbacks=False)
    grant(c, 'source', 'anthropic:api')
    with c.request_sources((c.Source.text('source', 'file', 'PLANTED_REDIRECTED'),)), pytest.raises(llm.LLMError):
        callers.guard(raw).complete('', 'PLANTED_REDIRECTED')
    assert not sent


def test_checkpoint_failure_after_durable_grant_fails_closed(monkeypatch):
    c, _ = consent_modules()
    original = c.os.replace
    def interrupted(*args, **kwargs):
        raise OSError('stopped between ledger and checkpoint')
    monkeypatch.setattr(c.os, 'replace', interrupted)
    with pytest.raises(llm.LLMError):
        asyncio.run(request_grant(dialog(), 'source', 'openai:codex'))
    monkeypatch.setattr(c.os, 'replace', original)
    assert not c.is_granted('source', 'openai:codex')


@pytest.mark.parametrize('path', ['proposer', 'router', 'bundle', 'critic', 'watched', 'autogen', 'smolagents', 'mcp'])
def test_measurement_actual_construction_paths_withhold_unrecorded_data(path, monkeypatch):
    c, callers = consent_modules()
    sent = []
    raw = make_caller('codex', monkeypatch, sent)
    monkeypatch.setattr(llm, 'provider_from_env', lambda *a, **kw: raw)
    if path == 'proposer':
        from g6.harness.verification_proposer import default_caller
        built = default_caller()
    elif path == 'router':
        from g6.llm_router.router import build_caller
        built = build_caller('codex', 'model')
    elif path == 'bundle':
        from g6.bundles.server import caller_from_settings
        built = caller_from_settings({'provider': 'codex', 'model': 'model'})
    elif path == 'critic':
        from g6.harness.completion_loop import critic_host
        from g6.core import keys
        monkeypatch.setattr(keys, 'get', lambda key: 'codex')
        built = critic_host()
    elif path == 'watched':
        from g6.self_maintenance.usage import WatchedClaude
        monkeypatch.setattr(host_models.ClaudeCodeCaller, 'complete',
                            lambda self, system, user, **kw: sent.append(user) or 'answer')
        built = WatchedClaude()
    elif path in ('autogen', 'smolagents'):
        module = importlib.import_module(f'g6.agents.agent_{path}.agent_{path}_block')
        built = module.provider_from_env()
    else:
        from g6.mcp import invoke_reply
        from returns.result import Failure, Success
        class Registry:
            def invoke(self, *args, **kwargs):
                try:
                    return Success(kwargs['llm'].complete('', 'PLANTED_MCP_MODEL_INPUT'))
                except llm.LLMError as exc:
                    return Failure(str(exc))
        reply = invoke_reply('fake', 'run', registry=Registry())
        assert reply['ok'] is False and 'G6_E_CLOUD_CONSENT_REQUIRED' in json.dumps(reply)
        assert not sent
        return
    with pytest.raises(llm.LLMError, match='G6_E_CLOUD_CONSENT_REQUIRED'):
        built.complete('', 'PLANTED_AT_CONSTRUCTION_PATH')
    assert not sent


def test_loopback_proxy_is_cloud(monkeypatch):
    c, callers = consent_modules()
    sent = []
    raw = make_caller('local', monkeypatch, sent)
    import urllib.request
    monkeypatch.setattr(urllib.request, 'getproxies', lambda: {'http': 'http://proxy.invalid'})
    monkeypatch.setattr(urllib.request, 'proxy_bypass', lambda host: False)
    with pytest.raises(llm.LLMError):
        callers.guard(raw).complete('', 'PLANTED_PROXY')
    assert not sent


def test_unreadable_state_never_reuses_grant(monkeypatch):
    c, callers = consent_modules()
    sent = []
    wrapped = callers.guard(make_caller('codex', monkeypatch, sent))
    grant(c, 'source', 'openai:codex')
    original = c.Path.read_text
    def unreadable(self, *args, **kwargs):
        if self.name == 'head':
            raise PermissionError('state cannot be read')
        return original(self, *args, **kwargs)
    monkeypatch.setattr(c.Path, 'read_text', unreadable)
    with c.request_sources((c.Source.text('source', 'file', 'secret'),)), pytest.raises(llm.LLMError):
        wrapped.complete('', 'secret')
    assert not sent


def test_local_computation_survives_corrupt_cloud_sharing_state(monkeypatch):
    c, callers = consent_modules()
    sent = []
    grant(c, 'source', 'openai:codex')
    c.ledger_path().write_text('{broken', encoding='utf-8')
    local = callers.guard(make_caller('local', monkeypatch, sent))
    assert local.complete('', 'PLANTED_STAYS_LOCAL') == 'answer'
    assert sent == ['PLANTED_STAYS_LOCAL']


def test_equal_output_bytes_keep_distinct_provenance():
    c, _ = consent_modules()
    a = {'result': 'same rendering'}
    b = {'result': 'same rendering'}
    source_a = c.Source.text('source-a', 'file-a', a['result'])
    source_b = c.Source.text('source-b', 'file-b', b['result'])
    with c.collect_sources():
        c.bind_output(a, (source_a,))
        c.bind_output(b, (source_b,))
        assert c.output_sources(a) == (source_a,)
        assert c.output_sources(b) == (source_b,)


def test_proposer_program_instructions_do_not_add_personal_source_scopes(monkeypatch):
    c, _ = consent_modules()
    from g6.harness import verification_proposer as proposer
    one = object.__new__(proposer.Proposer)
    one.model, one.ready, one.stopped = 'fake', '', ''
    one.deadline, one.room, one.stage = float('inf'), 1, ['']
    one.found, one.limits = SimpleNamespace(activities=[]), SimpleNamespace(calls=[])
    ref = c.Source.text('requirements', 'input', 'personal requirement')
    one.context = SimpleNamespace(text='personal requirement', sources=(ref,))
    one._record = lambda calls: None
    observed = []
    def complete(*args, **kwargs):
        observed.append(c.current_sources())
        return {'data': {'instances': []}}
    monkeypatch.setattr(proposer, 'complete_structured', complete)
    assert one._ask('stage', 'G6 program instruction', {}, dict) == {'instances': []}
    assert observed == [(ref,)]


@pytest.mark.parametrize('local', [False, True])
def test_image_entry_uses_the_same_default_deny_and_local_policy(monkeypatch, local):
    _, callers = consent_modules()
    sent = []
    monkeypatch.setattr(llm.OpenAICompatibleCaller, 'complete_with_images',
        lambda self, system, user, images, **kw: sent.append((user, images)) or 'answer')
    raw = llm.OpenAICompatibleCaller('model', api_key=None,
        base_url='http://127.0.0.1:8000/v1' if local else 'https://api.openai.com/v1')
    image = llm.Image(b'\x89PNG\r\n\x1a\nPLANTED_IMAGE', 'image/png')
    wrapped = callers.guard(raw)
    if local:
        assert wrapped.complete_with_images('', 'PLANTED_IMAGE_TEXT', [image]) == 'answer'
        assert sent == [('PLANTED_IMAGE_TEXT', [image])]
    else:
        with pytest.raises(llm.LLMError, match='G6_E_CLOUD_CONSENT_REQUIRED'):
            wrapped.complete_with_images('', 'PLANTED_IMAGE_TEXT', [image])
        assert not sent
