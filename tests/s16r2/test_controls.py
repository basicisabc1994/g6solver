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


def grant(source, provider='openai:codex', dialog=None):
    return asyncio.run(request_grant(SimpleNamespace(session=dialog or Dialog(), request_id='grant'),
                                     source, provider))


def test_identity_from_rendering():
    a, b = {'text': 'same'}, {'text': 'same'}
    first, second = cloud.Source.text('first', 'item', 'same'), cloud.Source.text('second', 'item', 'same')
    with cloud.collect_sources():
        cloud.bind_output(a, (first,))
        cloud.bind_output(b, (second,))
        assert cloud.output_sources(a) == (first,)
        assert cloud.output_sources(b) == (second,)
    assert cloud.input_source('\u00e9') != cloud.input_source('e\u0301')
    assert callers.destination(llm.OllamaCaller('same', base_url='https://remote.invalid')).local is False


def test_one_fact_two_rules_and_revocation():
    source = cloud.Source.text('document', 'passage', 'private')
    for expected in (False, True, False):
        assert cloud.is_granted('document', 'openai:codex') is expected
        try:
            with cloud.admission('openai:codex', (source,), cloud.sha256('private')):
                admitted = True
        except cloud.LLMError:
            admitted = False
        assert admitted is expected
        if not expected:
            assert grant('document')['status'] == 'granted'
        else:
            cloud.revoke('document', 'openai:codex')


@pytest.mark.parametrize('action,value', [('decline', 'Person'), ('cancel', 'Person'),
    ('unknown-status', 'Person'), ('accept', ''), ('accept', ' '), ('accept', True)])
def test_failure_read_as_success(action, value):
    class Answer:
        async def elicit_form(self, **kwargs):
            return SimpleNamespace(action=action, content={'value': value})
    assert grant('document', dialog=Answer())['status'] != 'granted'
    assert not cloud.is_granted('document', 'openai:codex')


def test_check_then_act_dialog_revision():
    class RevokeDuringDialog:
        async def elicit_form(self, **kwargs):
            cloud.revoke('document', 'openai:codex')
            return SimpleNamespace(action='accept', content={'value': 'Person'})
    assert grant('document', dialog=RevokeDuringDialog())['status'] == 'stale'
    assert not cloud.is_granted('document', 'openai:codex')


def test_check_then_act_send_and_revoke():
    assert grant('document')['status'] == 'granted'
    entered, release, revoking, revoked = (threading.Event() for _ in range(4))
    errors = []
    source = cloud.Source.text('document', 'item', 'private')

    def send():
        try:
            with cloud.admission('openai:codex', (source,), cloud.sha256('private')):
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
        assert not revoked.wait(.03)
    finally:
        release.set()
        sender.join(6)
        if revoker.ident is not None:
            revoker.join(6)
    assert not sender.is_alive() and not revoker.is_alive()
    assert not errors and revoked.is_set()
    with pytest.raises(cloud.LLMError):
        with cloud.admission('openai:codex', (source,), cloud.sha256('private')):
            pytest.fail('send admitted after revoke returned')


def test_state_not_durable_interrupted_checkpoint(monkeypatch):
    with monkeypatch.context() as interrupted:
        def fail(*args):
            raise OSError('simulated checkpoint interruption')
        interrupted.setattr(cloud.os, 'replace', fail)
        with pytest.raises(cloud.LLMError, match=cloud.STATE_UNAVAILABLE):
            grant('document')
    assert not cloud.is_granted('document', 'openai:codex')


def test_untrusted_input_given_authority_and_argument_contracts():
    server, ran = build_server(), []

    @server.tool()
    def probe() -> dict:
        ran.append(True)
        return {'text': 'PRIVATE_UNDECLARED_SOURCE', 'consent': True, 'source': 'public'}

    async def run():
        async with Client(server, client_info=types.Implementation(name='codex', version='review')) as client:
            invalid = await client.call_tool('probe', {'approved': True}, raise_on_error=False)
            assert invalid.is_error and not ran
            received = await client.call_tool('probe', {})
            assert 'PRIVATE_UNDECLARED_SOURCE' not in str(received)
            assert cloud.REQUIRED in str(received)
    asyncio.run(run())


def test_boundary_case_empty_unknown_and_other_provider():
    source = cloud.Source.text('document', 'item', 'private')
    assert grant('document')['status'] == 'granted'
    assert grant('unrecorded')['status'] == 'granted'
    for provider, refs in [('openai:codex', ()), ('openai:codex', (cloud.UNKNOWN,)),
                           ('anthropic:claude_code', (source,)), ('cloud:unknown', (source,))]:
        with pytest.raises(cloud.LLMError, match=cloud.REQUIRED):
            with cloud.admission(provider, refs, cloud.sha256('private')):
                pytest.fail('empty, unknown or other-provider source admitted')


def test_transport_mutation_and_protocol_errors():
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
            root = types.JSONRPCResponse(jsonrpc='2.0', id=request_id,
                result=response.model_dump(mode='json', by_alias=True, exclude_none=True))
            await self._write_stream.send(SessionMessage(types.JSONRPCMessage(root)))

    assert grant('document')['status'] == 'granted'
    session = Session()
    assert transport.install(session)
    result = ToolResult(structured_content={'text': 'original'})
    transport.prepare(session, 'test', result, (cloud.Source.text('document', 'item', 'original'),))
    response = types.ServerResult(types.CallToolResult(content=[types.TextContent(type='text', text='PRIVATE_MUTATION')]))
    asyncio.run(session._send_response('test', response))
    assert len(sent) == 1 and 'PRIVATE_MUTATION' not in str(sent)
    assert cloud.REQUIRED in str(sent)

    server = build_server()
    @server.tool()
    def fail() -> dict:
        raise RuntimeError('PRIVATE_EXCEPTION')
    async def run():
        async with Client(server) as client:
            return await client.call_tool('fail', {}, raise_on_error=False)
    error = asyncio.run(run())
    assert error.is_error and 'PRIVATE_EXCEPTION' not in str(error)


def test_guard_covers_less_than_it_guards_code_inventories():
    from tests.core.test_consent_inventory import caller_classes, inventory, sdk_resources
    from g6.core.consent_declarations import MODELS, TOOLS, TOOL_ARGUMENTS
    assert not caller_classes() - MODELS.keys()
    assert not caller_classes() - callers.CALLER_KINDS.keys()
    assert not [row for row in inventory() if not row[3] and not row[4]]
    assert all(declared and guarded for _, declared, guarded in sdk_resources())
    async def schemas():
        tools = await build_server().get_tools()
        assert set(tools) == set(TOOLS) == set(TOOL_ARGUMENTS)
        for name, tool in tools.items():
            assert set(tool.parameters.get('properties', {})) == set(TOOL_ARGUMENTS[name])
    asyncio.run(schemas())


def test_nested_unknown_stale_binding_and_private_labels():
    with cloud.collect_sources():
        value = cloud.bind_output({'text': 'first'}, (cloud.UNKNOWN,))
        with cloud.collect_sources():
            cloud.bind_output(value, (cloud.Source.text('declared', 'item', 'first'),))
        assert cloud.UNKNOWN in cloud.output_sources(value)
        value['text'] = 'PRIVATE_STALE'
        assert cloud.output_sources(value) is None
        cloud.bind_output(value, (cloud.Source.text('declared', 'item', 'first'),))
        assert cloud.UNKNOWN in cloud.output_sources(value)
    private = cloud.Source.text('PRIVATE_SOURCE_LABEL', 'item', 'private')
    assert 'PRIVATE_SOURCE_LABEL' not in cloud.withheld_message('openai:codex', (private,))
