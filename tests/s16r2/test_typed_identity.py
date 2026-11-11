"""Tickets use typed JSON identity, including malformed/missing-id refusal."""
import asyncio
import ast
import itertools
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp.tools.tool import ToolResult
from mcp import types
from mcp.shared.message import SessionMessage

from g6.core import cloud_consent as cloud
from g6.mcp import consent_transport as transport
from g6.mcp.cloud_consent import request_grant


IDS = (17, '17', 17.0, '17.0', None, 'None')


@pytest.mark.parametrize('first,second', list(itertools.permutations(IDS, 2)))
def test_every_typed_id_is_prepared_and_answered_in_both_orders(first, second):
    class Dialog:
        async def elicit_form(self, **kw):
            return SimpleNamespace(action='accept', content={'value': 'Person'})
    assert asyncio.run(request_grant(SimpleNamespace(session=Dialog(), request_id='grant'),
                                    'shared', 'openai:codex'))['status'] == 'granted'
    cloud.revoke('private', 'openai:codex')
    sent = []
    class Stream:
        async def send(self, message):
            sent.append(message)
    class Session:
        client_params = SimpleNamespace(clientInfo=SimpleNamespace(name='codex'))
        def __init__(self):
            self._write_stream = Stream()
        async def _send_response(self, request_id, response):
            # JSON-RPC's schema excludes floats and null. The transport still
            # must not let a malformed id overwrite a valid request's ticket.
            if type(request_id) not in (str, int):
                return
            root = types.JSONRPCResponse(jsonrpc='2.0', id=request_id,
                result=response.model_dump(mode='json', by_alias=True, exclude_none=True))
            await self._write_stream.send(SessionMessage(types.JSONRPCMessage(root)))
    session = Session()
    transport.install(session)
    result = ToolResult(structured_content={'text': 'PRIVATE_TYPED_RESULT'})
    response = types.ServerResult(types.CallToolResult(content=result.content,
        structuredContent=result.structured_content, _meta=result.meta))
    private = cloud.Source.text('private', 'passage', 'PRIVATE_TYPED_RESULT')
    shared = cloud.Source.text('shared', 'passage', 'PRIVATE_TYPED_RESULT')
    transport.prepare(session, first, result, (private,))
    transport.prepare(session, second, result, (shared,))
    asyncio.run(session._send_response(first, response))
    asyncio.run(session._send_response(second, response))
    private_packets = [p for p in sent if type(p.message.root.id) is type(first) and p.message.root.id == first]
    assert all('PRIVATE_TYPED_RESULT' not in str(p) for p in private_packets)
    if type(second) in (str, int):
        assert 'PRIVATE_TYPED_RESULT' in str(sent[-1])


def test_missing_id_never_becomes_a_ticket():
    session = SimpleNamespace(_g6_tickets={}, client_params=None)
    with pytest.raises(TypeError):
        transport.prepare(session, result=ToolResult(structured_content={}), sources=())
    assert session._g6_tickets == {}


@pytest.mark.parametrize('bad', [None, 17.0, True, [], {}, object()])
def test_invalid_id_cannot_create_or_consume_a_ticket(bad):
    session = SimpleNamespace(_g6_tickets={}, client_params=None)
    result = ToolResult(structured_content={})
    transport.prepare(session, bad, result, ())
    assert session._g6_tickets == {}


def ticket_producers(source):
    tree = ast.parse(source)
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            aliases.update({a.asname or a.name: node.module + '.' + a.name for a in node.names})
        elif isinstance(node, ast.Import):
            aliases.update({a.asname or a.name: a.name for a in node.names})
    def name(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        return name(node.value) + '.' + node.attr if isinstance(node, ast.Attribute) else ''
    return [ast.unparse(n.args[1]) for n in ast.walk(tree) if isinstance(n, ast.Call)
            and name(n.func) == 'g6.mcp.consent_transport.prepare' and len(n.args) >= 2]


def test_every_framework_ticket_producer_uses_the_native_protocol_id():
    from g6.mcp import cloud_consent as mcp_consent
    root = Path(mcp_consent.__file__).resolve().parents[1]
    producers = {p.relative_to(root).as_posix(): calls for p in root.rglob('*.py')
                 if (calls := ticket_producers(p.read_text(encoding='utf-8')))}
    # FastMCP's convenience Context.request_id property stringifies native ids.
    # The lower request_context retains their protocol type and value.
    assert producers == {'mcp/cloud_consent.py': ['ctx.request_context.request_id', 'ctx.request_context.request_id']}
    assert ticket_producers('from g6.mcp.consent_transport import prepare as ticket\n'
                            'def producer():\n    ticket(session, ctx.request_id, result, refs)\n') == ['ctx.request_id']
