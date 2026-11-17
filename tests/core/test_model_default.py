"""Offline model-default acceptance; host availability is synthetic, never launched."""
import asyncio
import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from g6.core import cloud_consent as policy
from g6.core import host_models, llm
from tests.host_stand_in import stand_in_hosts


@pytest.fixture(autouse=True)
def isolated_inputs(monkeypatch, tmp_path):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    monkeypatch.delenv('G6_DISABLE_LLM', raising=False)
    monkeypatch.delenv('G6_LLM_PROVIDER', raising=False)
    monkeypatch.setattr(llm.key_file, 'read', lambda: {})

    def forbidden_launch(*args, **kwargs):
        raise AssertionError('a resolution or admission check must never launch a host')

    monkeypatch.setattr(host_models, '_run', forbidden_launch)


@pytest.mark.parametrize(('client', 'installed', 'expected'), [
    ('anthropic:claude_code', ('claude_code', 'codex'), 'claude_code'),
    ('openai:codex', ('claude_code', 'codex'), 'codex'),
    ('anthropic:claude_code', ('codex',), 'codex'),
    ('openai:codex', ('claude_code',), 'claude_code'),
])
def test_default_selects_calling_host_or_installed_fallback(monkeypatch, client, installed, expected):
    """Catch a missing auto default, wrong caller preference, or missing fallback."""
    def locate(self):
        name = 'claude_code' if self.program.env == 'G6_CLAUDE' else 'codex'
        if name not in installed:
            raise llm.LLMError('G6_E_OUTSIDE_PROGRAM_MISSING: synthetic absence')
        return 'synthetic-host-never-launched'

    monkeypatch.setattr(host_models._HostCaller, 'locate', locate)
    with policy.collect_sources() as capture:
        capture.client = (client, object())  # trusted server context, never a tool argument
        caller = llm.provider_from_env(env={})
    assert isinstance(caller, llm.EmptyRetry), 'built-in default did not select an installed host'
    assert isinstance(caller._inner, host_models.CALLERS[expected])


@pytest.mark.parametrize('provider', ['anthropic:claude_code', 'openai:codex'])
def test_calling_provider_is_admitted_and_disclosed_without_grant(provider):
    """Catch missing exemption or missing durable disclosure basis."""
    source = policy.Source.text(policy.input_source('synthetic-text'), 'synthetic-item', 'synthetic-text')
    with policy.collect_sources() as capture:
        capture.client = (provider, object())
        capture.supplied.add(source.source_id)
        with policy.admission(provider, (source,), policy.sha256('synthetic-payload')):
            pass  # exercise admission only; nothing is sent
        rows = policy.disclosures()
    assert len(rows) == 1
    assert rows[0]['provider'] == provider
    assert rows[0]['sources'] == [{
        'source': source.source_id,
        'item_id': source.item_id,
        'content_sha256': source.content_sha256,
        'basis': 'calling_client',
    }]


@pytest.mark.parametrize('client', [None, 'cloud:unknown', 'anthropic:claude_code', 'openai:codex'])
@pytest.mark.parametrize('installed', [(), ('claude_code',), ('codex',), ('claude_code', 'codex')])
@pytest.mark.parametrize('setting', [None, 'auto', 'none', 'claude_code', 'codex', 'ollama'])
@pytest.mark.parametrize('disabled', [False, True])
def test_resolution_matrix_and_selection_reason(monkeypatch, client, installed, setting, disabled):
    def locate(self):
        name = 'claude_code' if self.program.env == 'G6_CLAUDE' else 'codex'
        if name not in installed:
            raise llm.LLMError('G6_E_OUTSIDE_PROGRAM_MISSING: synthetic absence')
        return 'synthetic-host-never-launched'

    monkeypatch.setattr(host_models._HostCaller, 'locate', locate)
    monkeypatch.setattr(llm.key_file, 'read', lambda: {} if setting is None else {'G6_LLM_PROVIDER': setting})
    if disabled:
        monkeypatch.setenv('G6_DISABLE_LLM', '1')
    with policy.collect_sources() as capture:
        capture.client = (client, object()) if client else None
        caller = llm.provider_from_env(env={})
    matching = {'anthropic:claude_code': 'claude_code', 'openai:codex': 'codex'}.get(client)
    expected = ('none' if disabled else setting if setting not in (None, 'auto') else
                matching if matching in installed else next(iter(installed), 'none'))
    selection = getattr(caller, 'model_selection', None)
    assert selection is not None, 'model resolution must explain its provider and reason'
    assert selection['provider'] == expected
    assert selection['reason'] == ('disabled' if disabled else 'explicit_setting' if setting not in (None, 'auto')
                                    else 'calling_client' if matching in installed else 'installed_fallback'
                                    if installed else 'no_installed_host')
    if expected in host_models.CALLERS:
        assert isinstance(caller._inner, host_models.CALLERS[expected])
    elif expected == 'none':
        assert isinstance(caller, llm.NoModel)
    else:
        assert isinstance(caller._inner, llm.OllamaCaller)


@pytest.mark.parametrize('environment', ['claude_code', 'codex'])
def test_environment_only_host_is_refused_before_availability(monkeypatch, environment):
    def forbidden_locate(self):
        pytest.fail('an environment-only host must not probe or fall back')

    monkeypatch.setattr(host_models._HostCaller, 'locate', forbidden_locate)
    caller = llm.provider_from_env(env={'G6_LLM_PROVIDER': environment})
    with pytest.raises(llm.LLMError, match='G6_E_NO_MODEL'):
        caller.complete('system', 'synthetic')


@pytest.mark.parametrize('environment', ['none', 'openai', 'unrecognised'])
def test_explicit_environment_provider_keeps_existing_validation(monkeypatch, environment):
    monkeypatch.setattr(host_models._HostCaller, 'locate', lambda self: 'synthetic-host-never-launched')
    if environment == 'unrecognised':
        with pytest.raises(llm.LLMError, match='unknown G6_LLM_PROVIDER'):
            llm.provider_from_env(env={'G6_LLM_PROVIDER': environment})
    else:
        caller = llm.provider_from_env(env={'G6_LLM_PROVIDER': environment, 'G6_LLM_MODEL': 'synthetic'})
        assert caller.model_selection['provider'] == environment
        if environment == 'none':
            assert isinstance(caller, llm.NoModel)
        else:
            with pytest.raises(llm.LLMError, match='OPENAI_API_KEY is not in'):
                caller.complete('system', 'synthetic')


def test_environment_auto_cannot_replace_key_file_opt_out(monkeypatch):
    monkeypatch.setattr(llm.key_file, 'read', lambda: {'G6_LLM_PROVIDER': 'none'})
    monkeypatch.setattr(host_models._HostCaller, 'locate', lambda self: pytest.fail('opt-out must not probe hosts'))
    caller = llm.provider_from_env(env={'G6_LLM_PROVIDER': 'auto'})
    assert isinstance(caller, llm.NoModel)
    with pytest.raises(llm.LLMError, match='G6_E_NO_MODEL'):
        caller.complete('system', 'synthetic')


def test_sequential_sessions_resolve_again_without_caching(monkeypatch):
    monkeypatch.setattr(host_models._HostCaller, 'locate', lambda self: 'synthetic-host-never-launched')
    names = []
    for client in ('openai:codex', 'anthropic:claude_code', 'openai:codex'):
        with policy.collect_sources() as capture:
            capture.client = (client, object())
            caller = llm.provider_from_env(env={})
            names.append(getattr(caller, 'model_selection', {}).get('provider'))
    assert names == ['codex', 'claude_code', 'codex']


@pytest.mark.parametrize('client', [None, 'cloud:unknown', 'openai:codex'])
def test_nonmatching_callers_still_refused_after_same_client_admission(client):
    source = policy.Source.text(policy.input_source('synthetic'), 'item', 'synthetic')
    with policy.collect_sources() as capture:
        capture.client = ('anthropic:claude_code', object())
        capture.supplied.add(source.source_id)
        with policy.admission('anthropic:claude_code', (source,), policy.sha256('payload')):
            pass
        capture.client = (client, object()) if client else None
        with pytest.raises(llm.LLMError, match=policy.REQUIRED):
            with policy.admission('anthropic:claude_code', (source,), policy.sha256('payload')):
                pytest.fail('a nonmatching caller was admitted')
        assert len(policy.disclosures()) == 1


def test_revoke_wins_and_unrecorded_sources_remain_refused():
    source = policy.Source.text(policy.input_source('synthetic'), 'item', 'synthetic')
    with policy.collect_sources() as capture:
        capture.client = ('openai:codex', object())
        capture.supplied.add(source.source_id)
        policy.refuse_unshared('openai:codex', (source,))
        policy.revoke(source.source_id, 'openai:codex')
        for refs in ((source,), (policy.UNKNOWN,)):
            with pytest.raises(llm.LLMError, match=policy.REQUIRED):
                policy.refuse_unshared('openai:codex', refs)
            with pytest.raises(llm.LLMError, match=policy.REQUIRED):
                with policy.admission('openai:codex', refs, policy.sha256('payload')):
                    pytest.fail('a negative decision or unknown source was admitted')
    assert policy.disclosures() == []


def test_decline_is_durable_and_stale_dialog_cannot_replace_new_decision(monkeypatch):
    from g6.mcp import cloud_consent as adapter
    from g6.mcp.consent import Consent

    source, provider = 'source:declined', 'openai:codex'

    async def decline(*args, **kwargs):
        return Consent('declined')

    monkeypatch.setattr(adapter, 'request_consent', decline)
    assert asyncio.run(adapter.request_grant(object(), source, provider))['status'] == 'declined'
    allowed, revision = policy.preview(source, provider)
    assert not allowed and revision, 'a person\'s decline must leave a durable negative decision'
    with policy.collect_sources() as capture:
        capture.client = (provider, object())
        with pytest.raises(llm.LLMError, match=policy.REQUIRED):
            policy.refuse_unshared(provider, (policy.Source.text(source, 'item', 'synthetic'),))
    assert policy._grant(source, provider, '', 'synthetic attribution') == 'stale'
    assert policy._grant(source, provider, revision, 'synthetic attribution') == 'granted'
    assert policy._decline(source, provider, revision) == 'stale'
    assert policy.preview(source, provider)[0]


def test_model_evidence_attached_before_output_freeze_and_tool_argument_cannot_set_client(monkeypatch):
    from g6.mcp import cloud_consent as adapter
    from g6.core import consent_callers

    monkeypatch.setattr(host_models._HostCaller, 'locate', lambda self: 'synthetic-host-never-launched')

    # Stand the host in at the launcher, the one place both the native and the watched path run a program
    # (tests/host_stand_in.py): a stand-in at ``complete`` is passed by once g6.self_maintenance.usage is imported.
    stand_in_hosts(monkeypatch, lambda system, user: 'synthetic reply')

    def tool() -> dict:
        caller = consent_callers.provider_from_env(env={})
        with policy.request_sources((policy.Source.text(policy.input_source('synthetic'), 'item', 'synthetic'),)):
            caller.complete('system', 'synthetic')
        return {'ok': True, 'result': 'synthetic reply', 'evidence': []}

    wrapped = adapter._source_function(tool)
    with policy.collect_sources() as capture:
        capture.client = ('openai:codex', object())
        capture.supplied.add(policy.input_source('synthetic'))
        capture.expected_tool = wrapped._g6_tool_identity
        result = asyncio.run(wrapped())
        assert result['evidence'][-1]['provider'] == 'codex'
        assert result['evidence'][-1]['reason'] == 'calling_client'
        assert result['evidence'][-1]['available_hosts'] == ['codex', 'claude_code']
        assert 'self-declared' in json.dumps(result['limits'])
        assert json.loads(capture.returned[0]) == result
        with pytest.raises(TypeError):
            asyncio.run(wrapped(calling_provider='anthropic:claude_code'))
        assert capture.client[0] == 'openai:codex'
    ctx = SimpleNamespace(session=SimpleNamespace(client_params=SimpleNamespace(
        clientInfo=SimpleNamespace(name='unknown-client'))))
    assert adapter.client_provider(ctx) == 'cloud:unknown'


@pytest.mark.parametrize('installed', [(), ('codex',), ('claude_code', 'codex')])
def test_doctor_explains_auto_without_launch_or_sign_in_claim(monkeypatch, installed):
    from g6.cli import doctor

    def locate(self):
        name = 'claude_code' if self.program.env == 'G6_CLAUDE' else 'codex'
        if name not in installed:
            raise llm.LLMError('G6_E_OUTSIDE_PROGRAM_MISSING: synthetic absence')
        return 'synthetic-host-never-launched'

    monkeypatch.setattr(host_models._HostCaller, 'locate', locate)
    check = doctor._model({}, {})
    assert 'calling Claude Code / Codex sign-in by default' in check.detail
    assert 'G6_LLM_PROVIDER=none' in check.fix
    assert 'different provider asks first' in check.fix
    assert 'sign-in was not checked' in check.detail
    for name in installed:
        assert name in check.detail


@pytest.mark.parametrize('relative', [
    'src/g6/core/llm.py', 'src/g6/core/host_models.py', 'src/g6/core/discovery.py',
    'src/g6/mcp/__init__.py', 'src/g6/skills/using-g6/SKILL.md', 'README.md', 'src/g6/cli/setup.py',
])
def test_model_guidance_names_default_opt_out_and_provider_consent(relative):
    text = (Path(__file__).resolve().parents[2] / relative).read_text(encoding='utf-8')
    assert 'calling Claude Code / Codex sign-in by default' in text
    assert 'G6_LLM_PROVIDER=none' in text
    assert 'different provider asks first' in text


def test_cross_provider_fallback_requires_grant_and_grant_basis_is_preserved(monkeypatch):
    def locate(self):
        if self.program.env == 'G6_CLAUDE':
            raise llm.LLMError('G6_E_OUTSIDE_PROGRAM_MISSING: synthetic absence')
        return 'synthetic-host-never-launched'

    monkeypatch.setattr(host_models._HostCaller, 'locate', locate)
    source = policy.Source.text('source:fallback', 'item', 'synthetic')
    with policy.collect_sources() as capture:
        capture.client = ('anthropic:claude_code', object())
        caller = llm.provider_from_env(env={})
        assert isinstance(caller, llm.EmptyRetry), 'the installed fallback must be selected before admission'
        assert caller.model_selection['reason'] == 'installed_fallback'
        with pytest.raises(llm.LLMError, match=policy.REQUIRED):
            policy.refuse_unshared('openai:codex', (source,))
        assert policy._grant(source.source_id, 'openai:codex', '', 'synthetic attribution') == 'granted'
        capture.client = ('openai:codex', object())
        with policy.admission('openai:codex', (source,), policy.sha256('payload')):
            pass
    assert policy.disclosures()[-1]['sources'][0]['basis'] == 'grant'


def test_auto_does_not_hide_unexpected_availability_failure(monkeypatch):
    def locate(self):
        raise llm.LLMError('G6_E_STOPPED: synthetic stop')

    monkeypatch.setattr(host_models._HostCaller, 'locate', locate)
    with pytest.raises(llm.LLMError, match='G6_E_STOPPED'):
        llm.provider_from_env(env={})


@pytest.mark.parametrize('mutated', [False, True])
def test_model_evidence_preserves_output_receipt_and_unknown_mutations(mutated):
    from g6.mcp import cloud_consent as adapter

    source = policy.Source.text('source:receipt', 'item', 'synthetic')

    def tool() -> dict:
        value = policy.bind_output({'result': 'synthetic reply'}, (source,))
        if mutated:
            value['result'] = 'changed without a new receipt'
        policy.current_capture().model_calls.append({'kind': 'model_selection', 'provider': 'codex',
                                                     'reason': 'calling_client', 'status': 'attempted'})
        return value

    wrapped = adapter._source_function(tool)
    with policy.collect_sources() as capture:
        capture.expected_tool = wrapped._g6_tool_identity
        result = asyncio.run(wrapped())
        assert result['evidence'][-1]['provider'] == 'codex'
        assert source in capture.returned[2], 'adding model evidence must retain the producer receipt'
        assert (policy.UNKNOWN in capture.returned[2]) == mutated


def test_nested_capture_keeps_current_client_and_model_evidence(monkeypatch):
    monkeypatch.setattr(host_models._HostCaller, 'locate', lambda self: 'synthetic-host-never-launched')
    with policy.collect_sources() as parent:
        parent.client = ('openai:codex', object())
        with policy.collect_sources() as child:
            caller = llm.provider_from_env(env={})
            assert isinstance(caller, llm.EmptyRetry), 'a nested source capture must retain the current calling host'
            assert caller.model_selection['provider'] == 'codex'
            assert child.client is None
            source = policy.Source.text(policy.input_source('synthetic'), 'item', 'synthetic')
            parent.supplied.add(source.source_id)
            with policy.admission('openai:codex', (source,), policy.sha256('payload')):
                pass
            parent.client = ('cloud:unknown', object())
            with pytest.raises(llm.LLMError, match=policy.REQUIRED):
                policy.refuse_unshared('openai:codex', (source,))
            child.model_calls.append(dict(caller.model_selection, status='attempted'))
        assert parent.model_calls == child.model_calls
    with policy.collect_sources() as fresh:
        assert fresh.client is None and fresh.model_calls == []


def test_python_without_capture_still_needs_grant_after_same_client_send():
    source, provider = policy.Source.text(policy.input_source('synthetic'), 'item', 'synthetic'), 'openai:codex'
    with policy.collect_sources() as capture:
        capture.client = (provider, object())
        capture.supplied.add(source.source_id)
        with policy.admission(provider, (source,), policy.sha256('payload')):
            pass
    assert policy.current_capture() is None
    with pytest.raises(llm.LLMError, match=policy.REQUIRED):
        policy.refuse_unshared(provider, (source,))
    with pytest.raises(llm.LLMError, match=policy.REQUIRED):
        with policy.admission(provider, (source,), policy.sha256('payload')):
            pytest.fail('Python without a calling client was admitted')


def test_kill_switch_keeps_disabled_error_and_never_checks_host(monkeypatch):
    def forbidden_locate(self):
        pytest.fail('disabled resolution must not check a host')

    monkeypatch.setattr(host_models._HostCaller, 'locate', forbidden_locate)
    monkeypatch.setenv('G6_DISABLE_LLM', '1')
    caller = llm.provider_from_env(env={})
    with pytest.raises(llm.LLMError, match='G6_E_LLM_DISABLED'):
        caller.complete('system', 'synthetic')


def test_owned_host_admission_records_selection_only_after_admission(monkeypatch):
    """The owned host path uses admission terms rather than the completion wrapper."""
    from g6.core import consent_callers

    monkeypatch.setattr(host_models._HostCaller, 'locate', lambda self: 'synthetic-host-never-launched')
    with policy.collect_sources() as capture:
        capture.client = ('openai:codex', object())
        caller = llm.provider_from_env(env={})
        assert isinstance(caller, llm.EmptyRetry), 'the installed calling host must be selected'
        source = policy.Source.text(policy.input_source('synthetic'), 'item', 'synthetic')
        capture.supplied.add(source.source_id)
        with policy.request_sources((source,)):
            terms = consent_callers.host_admission(caller._inner, 'system', 'synthetic')
        assert capture.model_calls == []
        with policy.admission(**terms):
            assert capture.model_calls == [dict(caller.model_selection, status='attempted')]
        capture.client = ('cloud:unknown', object())
        with pytest.raises(llm.LLMError, match=policy.REQUIRED):
            with policy.admission(**terms):
                pytest.fail('an owned host admission without a matching client or grant was admitted')
        assert len(capture.model_calls) == 1


@pytest.mark.parametrize('boundary', ['reply', 'wire'])
def test_model_exemption_does_not_release_retained_reply_without_grant(boundary):
    """A model admission must never grant a later MCP reply or transport ticket."""
    from g6.mcp import cloud_consent as adapter, consent_transport as transport
    from g6.core.ledger import digest
    from mcp import types
    from mcp.shared.message import SessionMessage

    session = SimpleNamespace(client_params=SimpleNamespace(clientInfo=SimpleNamespace(name='codex')))
    marker = 'SYNTHETIC_RETAINED_ARGUMENT'
    source = policy.Source.text(policy.input_source(marker), 'argument', marker)
    result = {'text': marker}

    def reply():
        if boundary == 'reply':
            return adapter.protect_reply(SimpleNamespace(session=session), result, (source,), supplied=())
        sent = []

        class Stream:
            async def send(self, packet):
                sent.append(packet.message.root.result)

        async def send():
            body = {'content': [{'type': 'text', 'text': marker}], 'structuredContent': result}
            token = transport._OUTBOUND.set(transport.Ticket((source,), 'openai:codex', frozenset(), digest(body)))
            try:
                packet = SessionMessage(types.JSONRPCMessage(types.JSONRPCResponse(jsonrpc='2.0', id=1, result=body)))
                await transport._Stream(Stream(), session).send(packet)
            finally:
                transport._OUTBOUND.reset(token)

        asyncio.run(send())
        assert len(sent) == 1
        return sent[0]

    with policy.collect_sources() as capture:
        capture.client = ('openai:codex', session)
        capture.supplied.add(source.source_id)
        with policy.admission('openai:codex', (source,), policy.sha256('model payload')):
            pass
        # Model admission cannot retain authority after these bytes leave the current request.
        capture.supplied.clear()
        withheld = reply()
        assert marker not in json.dumps(withheld)
        assert policy.REQUIRED in json.dumps(withheld)
        assert capture.supplied == set()
        assert len(policy.disclosures()) == 1
        assert policy._grant(source.source_id, 'openai:codex', '', 'synthetic attribution') == 'granted'
        assert marker in json.dumps(reply())
        assert policy.disclosures()[-1]['sources'][0]['basis'] == 'grant'
