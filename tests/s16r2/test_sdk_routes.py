"""Use the same HTTP client's selected transport as the independent routing oracle."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx2 as httpx
import pytest

from g6.core import cloud_consent as cloud, consent_callers as callers, llm
from g6.harness.verification_proposer import RecordingClient, StudyCaller


KINDS = ('caller', 'study', 'resource')
SETTINGS = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy')
ROUTES = ('direct', 'explicit', 'local_proxy', 'mount_proxy', 'mount_direct', 'custom', 'custom_mount',
          'native_transport', 'uds',
          'frozen_proxy_env', 'frozen_direct_env',
          *SETTINGS, *(setting + '_bypass' for setting in SETTINGS))


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('scheme', ['http', 'https'])
@pytest.mark.parametrize('route', ROUTES)
def test_all_sdk_routes_use_the_selected_first_hop(kind, scheme, route, monkeypatch):
    anthropic = pytest.importorskip('anthropic')
    for setting in (*SETTINGS, 'NO_PROXY', 'no_proxy'):
        monkeypatch.delenv(setting, raising=False)
    base = scheme + '://127.0.0.1:12345'
    proxy = 'http://selected-proxy.invalid:8080'
    if route == 'local_proxy':
        proxy = 'http://127.0.0.1:8080'
    options = {'trust_env': False}
    if route == 'frozen_proxy_env':
        monkeypatch.setenv('ALL_PROXY', proxy)
        monkeypatch.setenv('NO_PROXY', 'unrelated.invalid')
        options = {'trust_env': True}
    elif route == 'frozen_direct_env':
        options = {'trust_env': True}
    if route in SETTINGS or route.endswith('_bypass'):
        setting = route.removesuffix('_bypass')
        monkeypatch.setenv(setting, proxy)
        monkeypatch.setenv('no_proxy' if setting.islower() else 'NO_PROXY',
                           '127.0.0.1' if route.endswith('_bypass') else 'unrelated.invalid')
        options = {'trust_env': True}
    elif route in ('explicit', 'local_proxy'):
        options['proxy'] = proxy
    elif route == 'mount_proxy':
        options['mounts'] = {scheme + '://127.0.0.1': httpx.HTTPTransport(proxy=proxy)}
    elif route == 'mount_direct':
        options.update(proxy=proxy, mounts={scheme + '://127.0.0.1': None})
        # A None mount uses the client's default direct pool.
    elif route == 'custom':
        options['transport'] = httpx.MockTransport(lambda request: response(request))
    elif route == 'custom_mount':
        options['mounts'] = {'all://127.0.0.1': httpx.MockTransport(lambda request: response(request))}
    elif route == 'uds':
        options['transport'] = httpx.HTTPTransport(uds='synthetic-model.sock')
    elif route == 'native_transport':
        options['transport'] = httpx.HTTPTransport(trust_env=False)
    sent = []
    def response(request):
        sent.append(request.read())
        return httpx.Response(200, request=request, json={
            'id': 'fake', 'type': 'message', 'role': 'assistant', 'model': 'fake',
            'content': [{'type': 'text', 'text': 'answer'}], 'stop_reason': 'end_turn',
            'stop_sequence': None, 'usage': {'input_tokens': 1, 'output_tokens': 1}})
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', lambda self, request: response(request))
    with httpx.Client(**options) as http:
        if route == 'frozen_proxy_env':
            monkeypatch.delenv('ALL_PROXY')
            monkeypatch.setenv('NO_PROXY', '127.0.0.1')
        elif route == 'frozen_direct_env':
            for setting in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
                monkeypatch.setenv(setting, proxy)
            monkeypatch.setenv('NO_PROXY', 'unrelated.invalid')
        selected = http._transport_for_url(httpx.URL(base))
        pool = getattr(selected, '_pool', None)
        direct = (type(selected) is httpx.HTTPTransport and getattr(pool, '_proxy_url', None) is None
                  and getattr(pool, '_uds', None) is None)
        with anthropic.Anthropic(api_key='fake-key', base_url=base, http_client=http, max_retries=0) as sdk:
            if kind == 'caller':
                wrapped = callers.guard(llm.AnthropicCaller(client=sdk, fallbacks=False))
                def send():
                    return wrapped.complete('', 'PRIVATE_ROUTING_INVENTORY')
            elif kind == 'study':
                wrapped = StudyCaller('fake', client=sdk)
                def send():
                    return wrapped.complete('', 'PRIVATE_ROUTING_INVENTORY')
            else:
                wrapped = RecordingClient(sdk)
                def send():
                    return wrapped.messages.create(model='fake', max_tokens=1,
                                    messages=[{'role': 'user', 'content': 'PRIVATE_ROUTING_INVENTORY'}])
            destination = callers.destination(llm.AnthropicCaller(client=sdk))
            if direct:
                send()
                assert sent and destination.local
            else:
                with pytest.raises(llm.LLMError, match=cloud.REQUIRED):
                    send()
                assert not sent
                assert not destination.local
                if getattr(pool, '_proxy_url', None) is not None:
                    assert destination.provider == 'endpoint:' + cloud.sha256(proxy)


def sdk_sites(extra=()):
    root = Path(cloud.__file__).resolve().parents[1]
    found = set()
    for path in [*root.rglob('*.py'), *extra]:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        aliases = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                aliases.update({a.asname or a.name: a.name for a in n.names})
            elif isinstance(n, ast.ImportFrom) and n.module:
                aliases.update({a.asname or a.name: n.module + '.' + a.name for a in n.names})
        def name(n):
            if isinstance(n, ast.Name):
                return aliases.get(n.id, n.id)
            return name(n.value) + '.' + n.attr if isinstance(n, ast.Attribute) else ''
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and name(n.func) == 'anthropic.Anthropic':
                found.add(path.relative_to(root).as_posix() if root in path.parents else 'new.py')
    return found


def sdk_kinds():
    root = Path(cloud.__file__).resolve().parents[2]
    kinds = set()
    for path in (root / 'g6').rglob('*.py'):
        module = '.'.join(path.relative_to(root).with_suffix('').parts)
        tree = ast.parse(path.read_text(encoding='utf-8'))
        def visit(node, owners=()):
            if isinstance(node, ast.ClassDef):
                owners = (*owners, node.name)
            if owners and isinstance(node, ast.Call) and ast.unparse(node.func) in (
                    'anthropic.Anthropic', 'AnthropicCaller', 'llm.AnthropicCaller'):
                kinds.add(module + '.' + '.'.join(owners))
            for child in ast.iter_child_nodes(node):
                visit(child, owners)
        visit(tree)
    from tests.core.test_consent_inventory import sdk_resources
    kinds.update(row[0] for row in sdk_resources() if row[1] and row[0].startswith('g6.harness.'))
    return kinds


def test_code_derived_sdk_construction_inventory(tmp_path):
    assert sdk_sites() == {'core/llm.py', 'harness/verification_proposer.py'}
    assert KINDS == ('caller', 'study', 'resource')
    from g6.core.consent_declarations import SDK_SENDS
    assert set(SDK_SENDS) == {'g6.harness.verification_proposer._RecordedMessages.create'}
    assert sdk_kinds() == {
        'g6.core.llm.AnthropicCaller', 'g6.harness.verification_proposer.StudyCaller',
        'g6.harness.verification_proposer._RecordedMessages.create'}
    planted = tmp_path / 'new.py'
    planted.write_text('import anthropic as api\nclient = api.Anthropic(api_key="fake")\n')
    assert sdk_sites((planted,)) - sdk_sites() == {'new.py'}


def test_opaque_sdk_http_client_is_unknown(monkeypatch):
    anthropic = pytest.importorskip('anthropic')
    class Opaque(httpx.Client):
        def _transport_for_url(self, url):
            raise RuntimeError('unreadable routing')
    with Opaque(trust_env=False) as http:
        with anthropic.Anthropic(api_key='fake-key', base_url='http://127.0.0.1', http_client=http) as sdk:
            assert not callers.destination(llm.AnthropicCaller(client=sdk)).local


@pytest.mark.parametrize('custom', ['subclass_send', 'request_hook', 'custom_auth', 'sdk_subclass'])
def test_unobservable_request_rewriting_cannot_establish_locality(custom):
    anthropic = pytest.importorskip('anthropic')
    class CustomClient(httpx.Client):
        def _send_single_request(self, request):
            raise AssertionError('no network or model call is made')
    class Auth(httpx.Auth):
        def auth_flow(self, request):
            yield request
    class CustomSDK(anthropic.Anthropic):
        pass
    options = {'trust_env': False}
    if custom == 'request_hook':
        options['event_hooks'] = {'request': [lambda request: None]}
    elif custom == 'custom_auth':
        options['auth'] = Auth()
    client_type = CustomClient if custom == 'subclass_send' else httpx.Client
    sdk_type = CustomSDK if custom == 'sdk_subclass' else anthropic.Anthropic
    with client_type(**options) as http:
        with sdk_type(api_key='fake-key', base_url='http://127.0.0.1', http_client=http) as sdk:
            assert callers.destination(llm.AnthropicCaller(client=sdk)).provider == 'cloud:unknown'


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('base', ['http://127.0.0.1:12345', 'https://api.anthropic.com'])
def test_proxy_grants_name_the_selected_hop_and_revocation_stops_it(kind, base, monkeypatch):
    anthropic = pytest.importorskip('anthropic')
    from g6.mcp.cloud_consent import request_grant
    class Dialog:
        async def elicit_form(self, **kw):
            return SimpleNamespace(action='accept', content={'value': 'Person'})
    def grant(provider):
        assert asyncio.run(request_grant(SimpleNamespace(session=Dialog(), request_id='grant'),
                                        'model_inputs', provider))['status'] == 'granted'
    proxy = 'http://selected-proxy.invalid:8080'
    provider = 'endpoint:' + cloud.sha256(proxy)
    sent = []
    def send_http(self, request):
        sent.append(request.read())
        return httpx.Response(200, request=request, json={
            'id': 'fake', 'type': 'message', 'role': 'assistant', 'model': 'fake', 'content': [],
            'stop_reason': 'end_turn', 'stop_sequence': None, 'usage': {'input_tokens': 1, 'output_tokens': 1}})
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', send_http)
    with httpx.Client(proxy=proxy, trust_env=False) as http:
        with anthropic.Anthropic(api_key='fake-key', base_url=base, http_client=http, max_retries=0) as sdk:
            if kind == 'caller':
                caller = callers.guard(llm.AnthropicCaller(client=sdk, fallbacks=False))
                def send():
                    return caller.complete('', 'PRIVATE_PROXY_GRANT')
            elif kind == 'study':
                caller = StudyCaller('fake', client=sdk)
                def send():
                    return caller.complete('', 'PRIVATE_PROXY_GRANT')
            else:
                caller = RecordingClient(sdk)
                def send():
                    return caller.messages.create(model='fake', max_tokens=1,
                        messages=[{'role': 'user', 'content': 'PRIVATE_PROXY_GRANT'}])
            grant('anthropic:api')
            with pytest.raises(llm.LLMError, match=cloud.REQUIRED):
                send()
            assert not sent
            grant(provider)
            send()
            assert len(sent) == 1
            cloud.revoke('model_inputs', provider)
            with pytest.raises(llm.LLMError, match=cloud.REQUIRED):
                send()
            assert len(sent) == 1


@pytest.mark.parametrize('setting', SETTINGS)
@pytest.mark.parametrize('bypass', [False, True])
def test_lazy_sdk_uses_its_real_client_for_environment_routes(setting, bypass, monkeypatch):
    pytest.importorskip('anthropic')
    for name in (*SETTINGS, 'NO_PROXY', 'no_proxy'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(setting, 'http://selected-proxy.invalid:8080')
    monkeypatch.setenv('NO_PROXY', '127.0.0.1' if bypass else 'unrelated.invalid')
    scheme = 'https' if setting.lower() == 'https_proxy' else 'http'
    raw = llm.AnthropicCaller(api_key='fake-key', base_url=scheme + '://127.0.0.1:12345')
    try:
        to = callers.destination(raw)
        assert raw._client is not None, 'lazy SDK was classified without its actual HTTP client'
        selected = raw._client._client._transport_for_url(httpx.URL(str(raw._client.base_url)))
        assert to.local is (getattr(selected._pool, '_proxy_url', None) is None)
    finally:
        if raw._client is not None:
            raw._client.close()
