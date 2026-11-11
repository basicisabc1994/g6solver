import httpx2 as httpx
import pytest

from g6.core import cloud_consent as cloud
from g6.harness.verification_proposer import StudyCaller


def test_sdk_all_proxy_is_checked_before_treating_loopback_as_local(monkeypatch):
    anthropic = pytest.importorskip('anthropic')
    sent = []
    for setting in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                    'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'):
        monkeypatch.delenv(setting, raising=False)
    monkeypatch.setenv('ALL_PROXY', 'http://environment-proxy.invalid:8080')
    monkeypatch.setenv('NO_PROXY', 'unrelated.invalid')

    def fake_transport(self, request):
        sent.append((getattr(self._pool, '_proxy_url', None), request.read()))
        return httpx.Response(200, request=request, json={
            'id': 'fake-message', 'type': 'message', 'role': 'assistant', 'model': 'fake-model',
            'content': [{'type': 'text', 'text': 'fake answer'}], 'stop_reason': 'end_turn',
            'stop_sequence': None, 'usage': {'input_tokens': 1, 'output_tokens': 1}})

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', fake_transport)
    with httpx.Client(trust_env=True) as http:
        with anthropic.Anthropic(api_key='review-fake-key', base_url='http://127.0.0.1:12345',
                                 http_client=http, max_retries=0) as sdk:
            try:
                StudyCaller('fake-model', client=sdk).complete('', 'PRIVATE_ALL_PROXY_PAYLOAD')
            except cloud.LLMError:
                pass
    if sent:
        assert sent[0][0].host == b'environment-proxy.invalid'
        assert b'PRIVATE_ALL_PROXY_PAYLOAD' in sent[0][1]
    assert not sent, 'ALL_PROXY forwarded a loopback SDK request without any source grant'
