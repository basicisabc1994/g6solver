import asyncio
import io
import urllib.request
from email.message import Message
from types import SimpleNamespace
from urllib.response import addinfourl

import pytest

from g6.core import cloud_consent as cloud, consent_callers as callers, llm
from g6.mcp.cloud_consent import request_grant


@pytest.mark.parametrize('backend,base,provider', [
    ('openai', 'https://api.openai.com/v1', 'openai:api'),
    ('openrouter', 'https://openrouter.ai/api/v1', 'openrouter:api'),
    ('generic', 'https://generic-model.invalid/v1', 'endpoint:' + cloud.sha256('https://generic-model.invalid/v1')),
    ('ollama', 'https://remote-ollama.invalid', 'endpoint:' + cloud.sha256('https://remote-ollama.invalid')),
])
def test_generic_endpoint_grant_does_not_authorize_an_ungranted_proxy(monkeypatch, backend, base, provider):
    class Dialog:
        async def elicit_form(self, **kw):
            return SimpleNamespace(action='accept', content={'value': 'Review person'})

    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                 'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'):
        monkeypatch.delenv(name, raising=False)
    proxy = 'http://ungranted-proxy.invalid:8080'
    monkeypatch.setenv('https_proxy', proxy)
    monkeypatch.setenv('no_proxy', 'unrelated.invalid')
    sent = []

    def fake_socket_send(handler, connection_type, request, **kwargs):
        # The native ProxyHandler already selected the actual first hop here.
        sent.append((request.host, request.data))
        response = addinfourl(io.BytesIO(b'{"message":{"content":"answer"},"choices":[{"message":{"content":"answer"}}]}'),
                             Message(), request.full_url, 200)
        response.msg = 'OK'
        return response

    monkeypatch.setattr(urllib.request.AbstractHTTPHandler, 'do_open', fake_socket_send)
    context = SimpleNamespace(session=Dialog(), request_id='grant')
    granted = asyncio.run(request_grant(context, 'model_inputs', provider))
    assert granted['status'] == 'granted'
    assert not cloud.is_granted('model_inputs', 'endpoint:' + cloud.sha256(proxy))
    raw = llm.OllamaCaller('fake', base_url=base) if backend == 'ollama' else llm.OpenAICompatibleCaller(
        'fake', base_url=base, api_key=None)
    caller = callers.guard(raw)
    try:
        caller.complete('', 'PRIVATE_SENT_TO_UNGRANTED_PROXY')
    except llm.LLMError:
        pass
    assert not sent, 'endpoint grant admitted private content to the ungranted proxy hop: ' + str(sent)
