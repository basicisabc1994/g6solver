# SPDX-License-Identifier: Apache-2.0
"""Producer contracts, rather than value annotations, decide cloud admission."""
import asyncio
import importlib.util
import json

import pytest


def declarations():
    assert importlib.util.find_spec('g6.core.consent_declarations'), 'producer declarations are missing'
    from g6.core import consent_declarations
    return consent_declarations


def test_closed_source_classes():
    d = declarations()
    with pytest.raises(ValueError):
        d.Declaration(('anything',))
    with pytest.raises(ValueError):
        d.Declaration(('source:',))


def test_registry_covers_registered_tools():
    from g6.mcp import build_server
    d = declarations()
    tools = asyncio.run(build_server().get_tools())
    assert set(tools) == set(d.TOOLS)
    assert all(getattr(tool.fn, '_g6_declaration', None) == d.TOOLS[name] for name, tool in tools.items())


def test_code_derived_caller_declaration_inventory():
    from tests.core.test_consent_inventory import caller_classes, inventory
    d = declarations()
    assert caller_classes() == set(d.MODELS)
    assert not [row for row in inventory() if not row[3] and not row[4]]


def test_new_caller_fails_declaration_inventory(tmp_path):
    from tests.core.test_consent_inventory import caller_classes
    d = declarations()
    fresh = tmp_path / 'new_caller.py'
    fresh.write_text('class NewCaller:\n    def complete(self, system, user):\n        return user\n', encoding='utf-8')
    assert 'new.NewCaller' in caller_classes(extra=(fresh,)) - d.MODELS.keys()


def test_cli_boundary_still_uses_the_one_check():
    import ast
    import inspect
    from g6.agents._runtime.consent import process_admission
    tree = ast.parse(inspect.getsource(process_admission))
    assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'admission'
               for n in ast.walk(tree))


def test_code_derived_agent_inventory():
    import ast
    from pathlib import Path
    d = declarations()
    found = {'g6.agents._runtime.cli.CliAgentRunner'}
    root = Path(__file__).resolve().parents[2] / 'src'
    for path in (root / 'g6/agents').glob('*/adapter.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name.endswith('Runtime')):
            found.add('.'.join(path.relative_to(root).with_suffix('').parts) + '.' + cls.name)
    assert found == set(d.AGENTS)


def test_every_agent_model_send_uses_guard():
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / 'src/g6/agents'
    sends = []
    for path in root.rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                     and n.func.attr in ('complete', 'complete_with_images')):
            sends.append((path.relative_to(root).as_posix(), call.lineno))
            assert isinstance(call.func.value, ast.Call) and isinstance(call.func.value.func, ast.Name) \
                and call.func.value.func.id == 'guard', sends[-1]
    assert sends


@pytest.mark.parametrize('granted', [False, True])
def test_agent_other_provider_captures_repository(tmp_path, monkeypatch, accepted_cloud_dialog, granted):
    from g6.agents._runtime.consent import consent_run
    from g6.agents._runtime.model import AgentRefusal
    from g6.core import cloud_consent as c
    from types import SimpleNamespace
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    marker = 'PLANTED_AGENT_REPOSITORY'
    path = tmp_path / 'code.py'
    path.write_text(marker, encoding='utf-8')
    unrelated = tmp_path / 'outside-scope'
    unrelated.mkdir()
    (unrelated / 'private.txt').write_text('PLANTED_OTHER_AGENT_SCOPE', encoding='utf-8')
    sent = []
    runner = SimpleNamespace(run=lambda *a, **kw: sent.append((kw['prompt'], path.read_text(encoding='utf-8'))))
    scope = c.repository_source(tmp_path)
    if granted:
        accepted_cloud_dialog(scope, 'anthropic:claude_code', text=marker)
        accepted_cloud_dialog('agent_environment', 'anthropic:claude_code', text='synthetic CLI environment')
    with c.collect_sources() as capture:
        capture.client = 'openai:codex', object()
        capture.supplied.add(scope)
        if granted:
            consent_run(runner, ['fake'], provider='anthropic:claude_code', prompt='client request', cwd=tmp_path)
        else:
            with pytest.raises(AgentRefusal, match=c.REQUIRED):
                consent_run(runner, ['fake'], provider='anthropic:claude_code', prompt='client request', cwd=tmp_path)
    assert (marker in str(sent)) is granted
    assert 'PLANTED_OTHER_AGENT_SCOPE' not in str(sent)


def test_official_sdk_recipient_and_effective_endpoint():
    from g6.core import llm
    from g6.core.consent_callers import SDKClient, destination
    from types import SimpleNamespace
    class Client(SDKClient):
        base_url = 'https://api.anthropic.com'
    caller = llm.AnthropicCaller(client=Client())
    assert destination(caller).provider == 'anthropic:api'
    class Other(Client):
        base_url = 'https://other.invalid'
    caller = llm.AnthropicCaller(client=Other(),
                                base_url='https://api.anthropic.com')
    assert destination(caller).provider != 'anthropic:api'
    opaque = llm.AnthropicCaller(client=SimpleNamespace(base_url='http://127.0.0.1:11434'))
    assert destination(opaque).provider == 'cloud:unknown'


def test_sdk_wrapper_cycle_is_unknown():
    from g6.core.consent_callers import SDKClientWrapper, sdk_endpoint
    wrapper = SDKClientWrapper(None)
    wrapper.sdk_inner = wrapper
    assert sdk_endpoint(wrapper) is None


def test_local_sdk_endpoint_needs_no_grant(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from g6.core import llm
    from g6.core.consent_callers import SDKClient, guard
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    sent = []
    class Client(SDKClient):
        base_url = 'http://127.0.0.1:11434'
        messages = SimpleNamespace(create=lambda **kw: sent.append(kw) or
            SimpleNamespace(content=[], stop_reason='end_turn'))
    guard(llm.AnthropicCaller(client=Client(), fallbacks=False)).complete('', 'PLANTED_LOCAL_SDK')
    assert 'PLANTED_LOCAL_SDK' in str(sent)


def test_agent_revocation_stops_the_next_run(tmp_path, monkeypatch, accepted_cloud_dialog):
    from types import SimpleNamespace
    from g6.agents._runtime.consent import consent_run
    from g6.agents._runtime.model import AgentRefusal
    from g6.core import cloud_consent as c
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    scope = c.repository_source(tmp_path)
    accepted_cloud_dialog(scope, 'openai:codex', text='PLANTED_REVOKED_AGENT')
    accepted_cloud_dialog('agent_environment', 'openai:codex', text='synthetic CLI environment')
    sent = []
    runner = SimpleNamespace(run=lambda *a, **kw: sent.append(kw['prompt']))
    consent_run(runner, ['fake'], provider='openai:codex', prompt='PLANTED_REVOKED_AGENT', cwd=tmp_path)
    c.revoke(scope, 'openai:codex')
    with pytest.raises(AgentRefusal, match=c.REQUIRED):
        consent_run(runner, ['fake'], provider='openai:codex', prompt='PLANTED_REVOKED_AGENT', cwd=tmp_path)
    assert sent == ['PLANTED_REVOKED_AGENT']


@pytest.mark.parametrize('provider,component,name', [
    ('openai:codex', 'agent_codex', 'codex'), ('anthropic:claude_code', 'agent_claude', 'claude-code')])
@pytest.mark.parametrize('same_client', [False, True])
def test_actual_mcp_agent_workspace_is_the_named_repository(tmp_path, monkeypatch, accepted_cloud_dialog,
                                                          provider, component, name, same_client):
    from types import SimpleNamespace
    from fastmcp import Client
    from mcp.types import Implementation
    from returns.result import Failure
    from g6.agents._runtime.consent import consent_run
    from g6.agents._runtime.model import AgentRefusal
    from g6.core import registry
    from g6.mcp import build_server
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('G6_MCP_SENSITIVE', component)
    accepted_cloud_dialog('agent_environment', provider, text='synthetic CLI environment')
    if same_client:
        from g6.core.cloud_consent import repository_source
        accepted_cloud_dialog(repository_source(tmp_path), provider, text='synthetic workspace')
        accepted_cloud_dialog('component_data', provider, text='synthetic component request')
    sent = []
    class Registry:
        def invoke(self, *args, **kwargs):
            try:
                consent_run(SimpleNamespace(run=lambda *a, **kw: sent.append(kw['prompt'])), ['fake'],
                            provider=provider, prompt='PLANTED_NAMED_AGENT_REQUEST', cwd=tmp_path)
            except AgentRefusal:
                return Failure('[G6_E_CLOUD_CONSENT_REQUIRED] synthetic request withheld')
            return Failure('[G6_E_TEST_FINISHED] synthetic request completed')
    monkeypatch.setattr(registry, 'get_registry', lambda: Registry())
    async def run():
        recipient = name if same_client else ('codex' if name == 'claude-code' else 'claude-code')
        async with Client(build_server(), client_info=Implementation(name=recipient, version='test')) as client:
            await client.call_tool('g6_invoke', {'component': component, 'op': 'run',
                'params_json': json.dumps({'workspace': str(tmp_path), 'goal': 'client request'})})
    asyncio.run(run())
    assert sent == (['PLANTED_NAMED_AGENT_REQUEST'] if same_client else [])


@pytest.mark.parametrize('provider', ['openai:codex', 'anthropic:claude_code'])
def test_agent_named_workspace_same_provider_still_needs_a_grant(tmp_path, monkeypatch, accepted_cloud_dialog, provider):
    from types import SimpleNamespace
    from g6.agents._runtime.consent import consent_run
    from g6.agents._runtime.model import AgentRefusal
    from g6.core import cloud_consent as c
    declarations()
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    accepted_cloud_dialog('agent_environment', provider, text='synthetic CLI environment')
    sent = []
    with c.collect_sources() as capture:
        capture.client = provider, object()
        capture.supplied.add(c.repository_source(tmp_path))
        with pytest.raises(AgentRefusal, match=c.REQUIRED):
            consent_run(SimpleNamespace(run=lambda *a, **kw: sent.append(kw['prompt'])), ['fake'], provider=provider,
                        prompt='NAMED_REPOSITORY', cwd=tmp_path)
    assert sent == []
    assert c.disclosures() == []
    assert not c.is_granted(c.repository_source(tmp_path), provider)


def test_declared_reply_does_not_require_value_traces(tmp_path, monkeypatch):
    from fastmcp import Client
    from mcp.types import Implementation
    from g6.mcp import build_server
    d = declarations()
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    server = build_server()
    @server.tool()
    @d.declares('client_request', 'g6_operational')
    def echo(text: str) -> dict:
        return {'result': text}
    async def run():
        async with Client(server, client_info=Implementation(name='codex', version='test')) as client:
            return str(await client.call_tool('echo', {'text': 'CLIENT_TEXT'}))
    assert 'CLIENT_TEXT' in asyncio.run(run())


@pytest.mark.parametrize('granted', [False, True])
def test_declared_source_capture(tmp_path, monkeypatch, accepted_cloud_dialog, granted):
    from fastmcp import Client
    from mcp.types import Implementation
    from g6.mcp import build_server
    d = declarations()
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    path = tmp_path / 'private.txt'
    path.write_text('PLANTED_DECLARED_SOURCE', encoding='utf-8')
    server = build_server()
    @server.tool()
    @d.declares('source:documents')
    def documents() -> dict:
        return {'result': path.read_text(encoding='utf-8')}
    if granted:
        accepted_cloud_dialog('documents', 'openai:codex', text='PLANTED_DECLARED_SOURCE')
    async def run():
        async with Client(server, client_info=Implementation(name='codex', version='test')) as client:
            return str(await client.call_tool('documents', {}))
    result = asyncio.run(run())
    assert ('PLANTED_DECLARED_SOURCE' in result) is granted
    if not granted:
        assert str(path) not in result
    assert json.dumps(result)
