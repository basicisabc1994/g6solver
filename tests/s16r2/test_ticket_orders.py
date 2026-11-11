"""Typed identity survives independent preparation and response ordering."""
import asyncio
import itertools
from types import SimpleNamespace

import pytest
from fastmcp.tools.tool import ToolResult
from mcp import types
from mcp.shared.message import SessionMessage

from g6.core import cloud_consent as cloud
from g6.mcp import consent_transport as transport
from g6.mcp.cloud_consent import request_grant

MISSING = object()
IDS = (17, '17', 17.0, '17.0', None, 'None', MISSING)


@pytest.mark.parametrize('private_id,shared_id', list(itertools.permutations(IDS, 2)))
@pytest.mark.parametrize('prepare_reverse', [False, True])
@pytest.mark.parametrize('answer_reverse', [False, True])
def test_ticket_authority_survives_both_independent_orders(private_id, shared_id,
                                                         prepare_reverse, answer_reverse):
    class Dialog:
        async def elicit_form(self, **kwargs):
            return SimpleNamespace(action='accept', content={'value': 'Person'})
    assert asyncio.run(request_grant(SimpleNamespace(session=Dialog(), request_id='grant'),
                                    'shared', 'openai:codex'))['status'] == 'granted'
    cloud.revoke('private', 'openai:codex')
    sent = []
    class Stream:
        async def send(self, message):
            sent.append(message.message.root)
    class Session:
        client_params = SimpleNamespace(clientInfo=SimpleNamespace(name='codex'))
        def __init__(self):
            self._write_stream = Stream()
        async def _send_response(self, request_id, response):
            # An absent or invalid MCP id has no response packet to serialize.
            if type(request_id) not in (int, str):
                return
            root = types.JSONRPCResponse(jsonrpc='2.0', id=request_id,
                result=response.model_dump(mode='json', by_alias=True, exclude_none=True))
            await self._write_stream.send(SessionMessage(types.JSONRPCMessage(root)))
    session = Session()
    assert transport.install(session)
    result = ToolResult(structured_content={'text': 'PRIVATE_ORDERED_RESULT'})
    response = types.ServerResult(types.CallToolResult(content=result.content,
        structuredContent=result.structured_content, _meta=result.meta))
    private = cloud.Source.text('private', 'passage', 'PRIVATE_ORDERED_RESULT')
    shared = cloud.Source.text('shared', 'passage', 'PRIVATE_ORDERED_RESULT')
    prepared = [(private_id, private), (shared_id, shared)]
    answered = [private_id, shared_id]
    for ident, source in prepared[::(-1 if prepare_reverse else 1)]:
        transport.prepare(session, ident, result, (source,))
    for ident in answered[::(-1 if answer_reverse else 1)]:
        asyncio.run(session._send_response(ident, response))
    private_packets = [p for p in sent if type(p.id) is type(private_id) and p.id == private_id]
    shared_packets = [p for p in sent if type(p.id) is type(shared_id) and p.id == shared_id]
    assert all('PRIVATE_ORDERED_RESULT' not in str(p) for p in private_packets)
    if type(shared_id) in (int, str):
        assert len(shared_packets) == 1 and 'PRIVATE_ORDERED_RESULT' in str(shared_packets[0])
    else:
        assert not shared_packets
    assert not session._g6_tickets
