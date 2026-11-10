import asyncio
import threading
from types import SimpleNamespace

import pytest
from fastmcp import Client
from fastmcp.tools.tool import ToolResult
from mcp import types
from mcp.shared.message import SessionMessage

from g6.core import cloud_consent as cloud, consent_callers as callers, llm
from g6.mcp import build_server
from g6.mcp.cloud_consent import request_grant


class Dialog:
    async def elicit_form(self, **kwargs):
        return SimpleNamespace(action='accept', content={'value': 'Review person'})


def grant(source='document', provider='openai:codex', session=None):
    return asyncio.run(request_grant(SimpleNamespace(session=session or Dialog(), request_id='grant'), source, provider))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))


@pytest.mark.parametrize('action,value', [('decline', 'Person'), ('cancel', 'Person'),
    ('not-a-status', 'Person'), ('accept', ''), ('accept', '  '), ('accept', True)])
def test_nonaccepting_and_unknown_dialog_results_never_grant(action, value):
    class Nonaccepting:
        async def elicit_form(self, **kwargs):
            return SimpleNamespace(action=action, content={'value': value})
    assert grant(session=Nonaccepting())['status'] != 'granted'
    assert not cloud.is_granted('document', 'openai:codex')


def test_revocation_while_dialog_is_open_makes_acceptance_stale():
    class RevokeDuringDialog:
        async def elicit_form(self, **kwargs):
            cloud.revoke('document', 'openai:codex')
            return SimpleNamespace(action='accept', content={'value': 'Review person'})
    assert grant(session=RevokeDuringDialog())['status'] == 'stale'
    assert not cloud.is_granted('document', 'openai:codex')


def test_unavailable_dialog_grants_nothing():
    class Unavailable:
        async def elicit_form(self, **kwargs):
            raise RuntimeError('fake unavailable')
    assert grant(session=Unavailable())['status'] == 'unavailable'
    assert cloud.grants() == []


def test_equal_renderings_do_not_merge_source_bindings():
    a, b = {'result': 'same'}, {'result': 'same'}
    first = cloud.Source.text('first', 'first', 'same')
    second = cloud.Source.text('second', 'second', 'same')
    with cloud.collect_sources():
        cloud.bind_output(a, (first,))
        cloud.bind_output(b, (second,))
        assert cloud.output_sources(a) == (first,)
        assert cloud.output_sources(b) == (second,)
        a['result'] = 'changed'
        assert cloud.output_sources(a) is None
    assert cloud.input_source('\u00e9') != cloud.input_source('e\u0301')


def test_caller_rendering_and_wrapper_impostor_do_not_establish_identity():
    remote = llm.OllamaCaller(model='same', base_url='https://models.invalid')
    local = llm.OllamaCaller(model='same', base_url='http://127.0.0.1:11434')
    assert remote.model == local.model
    assert not callers.destination(remote).local
    assert callers.destination(local).local
    impostor = type('CallerWrapper', (), {'inner': local, 'model': 'local'})()
    assert callers.destination(impostor).provider == 'cloud:unknown'


def test_loaded_sdk_reads_actual_endpoint_not_callers_rendered_name():
    class SDK(callers.SDKClient):
        base_url = 'https://other.invalid'
    caller = llm.AnthropicCaller(client=SDK(), base_url='https://api.anthropic.com')
    assert callers.destination(caller).provider == 'endpoint:' + cloud.sha256('https://other.invalid')
    assert callers.destination(llm.AnthropicCaller(client=SimpleNamespace(base_url='http://127.0.0.1'))).provider == 'cloud:unknown'


def test_checkpoint_write_interruption_cannot_reopen_a_grant(monkeypatch):
    with monkeypatch.context() as interrupted:
        def fail(*args):
            raise OSError('fake checkpoint interruption')
        interrupted.setattr(cloud.os, 'replace', fail)
        with pytest.raises(llm.LLMError, match=cloud.STATE_UNAVAILABLE):
            grant()
    assert not cloud.is_granted('document', 'openai:codex')
    with pytest.raises(llm.LLMError):
        with cloud.admission('openai:codex', (cloud.Source.text('document', 'item', 'private'),), cloud.sha256('private')):
            pytest.fail('state interruption admitted a send')


def test_revocation_orders_after_inflight_send_and_blocks_the_next():
    assert grant()['status'] == 'granted'
    entered, release, revoking, revoked = (threading.Event() for _ in range(4))
    errors = []
    ref = cloud.Source.text('document', 'item', 'private')

    def send():
        try:
            with cloud.admission('openai:codex', (ref,), cloud.sha256('private')):
                entered.set()
                assert release.wait(5)
        except BaseException as exc:
            errors.append(exc)

    def revoke():
        try:
            revoking.set()
            cloud.revoke('document', 'openai:codex')
            revoked.set()
        except BaseException as exc:
            errors.append(exc)

    sender, revoker = threading.Thread(target=send), threading.Thread(target=revoke)
    sender.start()
    try:
        assert entered.wait(5)
        revoker.start()
        assert revoking.wait(5)
        assert not revoked.wait(.05)
    finally:
        release.set()
        sender.join(6)
        if revoker.ident is not None:
            revoker.join(6)
    assert not sender.is_alive() and not revoker.is_alive()
    assert not errors and revoked.is_set()
    with pytest.raises(llm.LLMError, match=cloud.REQUIRED):
        with cloud.admission('openai:codex', (ref,), cloud.sha256('private')):
            pytest.fail('post-revocation send was admitted')


def test_transport_rejects_mutation_after_source_ticket():
    from g6.mcp import consent_transport as transport
    sent = []

    class Stream:
        async def send(self, packet):
            sent.append(packet)

    class Session:
        client_params = SimpleNamespace(clientInfo=SimpleNamespace(name='codex'))
        def __init__(self):
            self._write_stream = Stream()
        async def _send_response(self, request_id, response):
            root = types.JSONRPCResponse(jsonrpc='2.0', id=request_id, result=response.model_dump(
                mode='json', by_alias=True, exclude_none=True))
            await self._write_stream.send(SessionMessage(types.JSONRPCMessage(root)))

    assert grant()['status'] == 'granted'
    session = Session()
    assert transport.install(session)
    result = ToolResult(structured_content={'result': 'original'})
    ref = cloud.Source.text('document', 'item', 'original')
    transport.prepare(session, 'test', result, (ref,))
    response = types.ServerResult(types.CallToolResult(content=[types.TextContent(type='text', text='PRIVATE_MUTATION')]))
    asyncio.run(session._send_response('test', response))
    assert len(sent) == 1
    assert 'PRIVATE_MUTATION' not in str(sent)
    assert cloud.REQUIRED in str(sent)


def test_protocol_error_flag_survives_without_exception_content():
    server = build_server()
    @server.tool()
    def explode() -> dict:
        raise RuntimeError('PRIVATE_EXCEPTION_DETAIL')
    async def run():
        async with Client(server) as client:
            return await client.call_tool('explode', {}, raise_on_error=False)
    result = asyncio.run(run())
    assert result.is_error
    assert 'PRIVATE_EXCEPTION_DETAIL' not in str(result)


def test_all_tool_argument_contracts(tmp_path, monkeypatch):
    from tests.core.test_mcp_argument_contract import test_every_registered_tool_refuses_undeclared_arguments
    test_every_registered_tool_refuses_undeclared_arguments(tmp_path, monkeypatch)


def test_code_derived_inventories():
    from tests.core.test_consent_inventory import caller_classes, inventory, sdk_resources
    from g6.core.consent_declarations import MODELS
    classes = caller_classes()
    assert not classes - MODELS.keys()
    assert not classes - callers.CALLER_KINDS.keys()
    assert not [row for row in inventory() if not row[3] and not row[4]]
    assert all(declared and guarded for _, declared, guarded in sdk_resources())
