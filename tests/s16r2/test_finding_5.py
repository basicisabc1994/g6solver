import asyncio
from types import SimpleNamespace

from fastmcp.tools.tool import ToolResult
from mcp import types
from mcp.shared.message import SessionMessage

from g6.core import cloud_consent as cloud
from g6.mcp import consent_transport as transport
from g6.mcp.cloud_consent import request_grant


def test_string_and_integer_mcp_request_ids_cannot_borrow_each_others_sources():
    class Dialog:
        async def elicit_form(self, **kwargs):
            return SimpleNamespace(action='accept', content={'value': 'Review person'})

    context = SimpleNamespace(session=Dialog(), request_id='grant')
    assert asyncio.run(request_grant(context, 'shared-document', 'openai:codex'))['status'] == 'granted'
    cloud.revoke('private-document', 'openai:codex')
    sent = []

    class Stream:
        async def send(self, message):
            sent.append(message)

    class Session:
        client_params = SimpleNamespace(clientInfo=SimpleNamespace(name='codex'))

        def __init__(self):
            self._write_stream = Stream()

        async def _send_response(self, request_id, response):
            root = types.JSONRPCResponse(jsonrpc='2.0', id=request_id,
                result=response.model_dump(mode='json', by_alias=True, exclude_none=True))
            await self._write_stream.send(SessionMessage(types.JSONRPCMessage(root)))

    session = Session()
    assert transport.install(session)
    # JSON-RPC integer 17 and string "17" are different valid request IDs.
    # The two producers happen to return the same bytes from different sources.
    result = ToolResult(structured_content={'text': 'PRIVATE_FIRST_REQUEST_RESULT'})
    private = cloud.Source.text('private-document', 'passage', 'PRIVATE_FIRST_REQUEST_RESULT')
    shared = cloud.Source.text('shared-document', 'passage', 'PRIVATE_FIRST_REQUEST_RESULT')
    transport.prepare(session, 17, result, (private,))
    transport.prepare(session, '17', result, (shared,))
    response = types.ServerResult(types.CallToolResult(content=result.content,
        structuredContent=result.structured_content, _meta=result.meta))
    asyncio.run(session._send_response(17, response))
    assert len(sent) == 1 and sent[0].message.root.id == 17
    assert 'PRIVATE_FIRST_REQUEST_RESULT' not in str(sent), 'integer request borrowed the string request source ticket'
