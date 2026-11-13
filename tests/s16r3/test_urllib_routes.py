"""The native urllib socket boundary supplies the route oracle, with no sockets."""
import ast
import asyncio
import io
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
import urllib.request
from urllib.response import addinfourl

import pytest

from g6.core import cloud_consent as cloud, consent_callers as callers, llm
from g6.mcp.cloud_consent import request_grant

SETTINGS = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy')
KINDS = ('openai', 'openrouter', 'generic', 'ollama')
ROUTES = ('direct', *SETTINGS, *(s + '_bypass' for s in SETTINGS),
          'port_bypass', 'port_mismatch', 'case_precedence', 'loopback_proxy')


def grant(scope, provider):
    class Dialog:
        async def elicit_form(self, **kw):
            return SimpleNamespace(action='accept', content={'value': 'Person'})
    assert asyncio.run(request_grant(SimpleNamespace(session=Dialog(), request_id='grant'), scope, provider))['status'] == 'granted'


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('local', [False, True], ids=['remote', 'loopback'])
@pytest.mark.parametrize('scheme', ['http', 'https'])
@pytest.mark.parametrize('route', ROUTES)
def test_each_urllib_caller_uses_its_native_first_hop(kind, local, scheme, route, monkeypatch):
    for setting in (*SETTINGS, 'NO_PROXY', 'no_proxy', 'REQUEST_METHOD'):
        monkeypatch.delenv(setting, raising=False)
    host = '127.0.0.1:12345' if local else 'model.invalid:12345'
    base = scheme + '://' + host
    if not local and scheme == 'https' and kind in ('openai', 'openrouter'):
        base = 'https://api.openai.com/v1' if kind == 'openai' else 'https://openrouter.ai/api/v1'
        host = urllib.request.Request(base).host
    proxy = 'http://selected-proxy.invalid:8080'
    if route == 'loopback_proxy':
        proxy = 'http://127.0.0.1:8080'
    if route in SETTINGS or route.endswith('_bypass') and route not in ('port_bypass',):
        setting = route.removesuffix('_bypass')
        monkeypatch.setenv(setting, proxy)
        monkeypatch.setenv('no_proxy', host if route.endswith('_bypass') else 'unrelated.invalid')
    elif route != 'direct':
        monkeypatch.setenv(scheme + '_proxy', proxy)
        monkeypatch.setenv('no_proxy', host if route == 'port_bypass' else
                           host + '9' if route == 'port_mismatch' else 'unrelated.invalid')
        if route == 'case_precedence':
            monkeypatch.setenv(scheme.upper() + '_PROXY', 'http://ignored-proxy.invalid:9090')
    sent = []
    def fake_socket(handler, connection, request, **kwargs):
        sent.append((request.host, request.data))
        response = addinfourl(io.BytesIO(b'{"message":{"content":"answer"},"choices":[{"message":{"content":"answer"}}]}'),
                             Message(), request.full_url, 200)
        response.msg = 'OK'
        return response
    monkeypatch.setattr(urllib.request.AbstractHTTPHandler, 'do_open', fake_socket)
    raw = llm.OllamaCaller('fake', base_url=base) if kind == 'ollama' else llm.OpenAICompatibleCaller(
        'fake', base_url=base, api_key=None)
    # The unguarded, synthetic request observes native handlers before any grants.
    raw.complete('', 'routing oracle')
    assert len(sent) == 1
    first_hop = sent.pop()[0]
    proxied = first_hop != urllib.request.Request(base).host
    endpoint = {'https://api.openai.com/v1': 'openai:api',
                'https://openrouter.ai/api/v1': 'openrouter:api'}.get(base, 'endpoint:' + cloud.sha256(base))
    expected = 'endpoint:' + cloud.sha256('http://' + first_hop) if proxied else endpoint
    actual = callers.destination(raw)
    assert actual.local == (local and not proxied)
    if not actual.local:
        assert actual.provider == expected
    caller = callers.guard(raw)
    # An endpoint grant may never stand in for a different first hop.
    grant('model_inputs', endpoint)
    if proxied:
        with pytest.raises(llm.LLMError, match=cloud.REQUIRED):
            caller.complete('', 'PRIVATE_UNGRANTED_HOP')
        assert not sent
        grant('model_inputs', expected)
    assert caller.complete('', 'shared') == 'answer'
    assert len(sent) == 1
    sent.clear()
    cloud.revoke('model_inputs', expected)
    if actual.local:
        assert caller.complete('', 'local data') == 'answer'
    else:
        with pytest.raises(llm.LLMError, match=cloud.REQUIRED):
            caller.complete('', 'PRIVATE_REVOKED_HOP')
        assert not sent


def test_endpoint_inventory_comes_from_the_caller_registry_and_transport_producers():
    endpoints = {name for name, kind in callers.CALLER_KINDS.items() if kind in ('endpoint', 'sdk-endpoint')}
    assert endpoints == {'g6.core.llm.OllamaCaller', 'g6.core.llm.OpenAICompatibleCaller',
                         'g6.core.llm.AnthropicCaller'}
    root = Path(llm.__file__).resolve().parents[1]
    transports = {}
    for path in root.rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any(isinstance(call, ast.Call) and
                    isinstance(call.func, ast.Name) and call.func.id == '_post_json' for call in ast.walk(node)):
                transports[node.name] = path.relative_to(root).as_posix()
    assert transports == {'OllamaCaller': 'core/llm.py', 'OpenAICompatibleCaller': 'core/llm.py'}


def test_unreadable_native_proxy_resolution_is_unknown(monkeypatch):
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *a: (_ for _ in ()).throw(ValueError('opaque route')))
    raw = llm.OpenAICompatibleCaller('fake', base_url='https://api.openai.com/v1', api_key=None)
    assert callers.destination(raw).provider == 'cloud:unknown'
