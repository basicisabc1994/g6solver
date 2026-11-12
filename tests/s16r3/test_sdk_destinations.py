"""Use the same HTTP client's selected transport as the independent routing oracle."""

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


@pytest.mark.parametrize('local', [False, True], ids=['remote', 'loopback'])
@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('scheme', ['http', 'https'])
@pytest.mark.parametrize('route', ROUTES)
def test_all_sdk_destinations_use_the_selected_first_hop(kind, scheme, route, local, monkeypatch):
    anthropic = pytest.importorskip('anthropic')
    for setting in (*SETTINGS, 'NO_PROXY', 'no_proxy'):
        monkeypatch.delenv(setting, raising=False)
    host = '127.0.0.1' if local else 'remote-model.invalid'
    base = scheme + '://' + host + ':12345'
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
                           host if route.endswith('_bypass') else 'unrelated.invalid')
        options = {'trust_env': True}
    elif route in ('explicit', 'local_proxy'):
        options['proxy'] = proxy
    elif route == 'mount_proxy':
        options['mounts'] = {scheme + '://' + host: httpx.HTTPTransport(proxy=proxy)}
    elif route == 'mount_direct':
        options.update(proxy=proxy, mounts={scheme + '://' + host: None})
        # A None mount uses the client's default direct pool.
    elif route == 'custom':
        options['transport'] = httpx.MockTransport(lambda request: response(request))
    elif route == 'custom_mount':
        options['mounts'] = {'all://' + host: httpx.MockTransport(lambda request: response(request))}
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
            monkeypatch.setenv('NO_PROXY', host)
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
            if direct and local:
                send()
                assert sent and destination.local
            else:
                with pytest.raises(llm.LLMError, match=cloud.REQUIRED):
                    send()
                assert not sent
                assert not destination.local
                if getattr(pool, '_proxy_url', None) is not None:
                    assert destination.provider == 'endpoint:' + cloud.sha256(proxy)


                if not direct and getattr(pool, '_proxy_url', None) is None:
                    assert destination.provider == 'cloud:unknown'
                    return
                from tests.s16r3.test_urllib_routes import grant
                grant('model_inputs', destination.provider)
                send()
                assert sent
                sent.clear()
                cloud.revoke('model_inputs', destination.provider)
                with pytest.raises(llm.LLMError, match=cloud.REQUIRED):
                    send()
                assert not sent
