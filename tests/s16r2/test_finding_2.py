import httpx2 as httpx
import pytest

from g6.core import cloud_consent as cloud
from g6.harness.verification_proposer import StudyCaller


def test_sdk_loopback_url_does_not_exempt_a_remote_explicit_proxy(monkeypatch):
    anthropic = pytest.importorskip('anthropic')
    sent = []
    for setting in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        monkeypatch.delenv(setting, raising=False)

    def fake_transport(self, request):
        # This is the actual transport selected by httpx for the SDK request.
        proxy = getattr(self._pool, '_proxy_url', None)
        sent.append((proxy, request.read()))
        return httpx.Response(200, request=request, json={
            'id': 'fake-message', 'type': 'message', 'role': 'assistant', 'model': 'fake-model',
            'content': [{'type': 'text', 'text': 'fake answer'}], 'stop_reason': 'end_turn',
            'stop_sequence': None, 'usage': {'input_tokens': 1, 'output_tokens': 1}})

    # No network: replace the HTTP transport before constructing the SDK client.
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', fake_transport)
    with httpx.Client(proxy='http://external-proxy.invalid:8080', trust_env=False) as http:
        with anthropic.Anthropic(api_key='review-fake-key', base_url='http://127.0.0.1:12345',
                                 http_client=http, max_retries=0) as sdk:
            caller = StudyCaller('fake-model', client=sdk)
            try:
                caller.complete('', 'PRIVATE_EXPLICIT_PROXY_PAYLOAD')
            except cloud.LLMError:
                pass
    if sent:
        assert sent[0][0].host == b'external-proxy.invalid'
        assert b'PRIVATE_EXPLICIT_PROXY_PAYLOAD' in sent[0][1]
    assert not sent, 'an SDK request reached its configured remote proxy without any source grant'
