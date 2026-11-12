import asyncio
from threading import Event, Thread
from types import SimpleNamespace

from fastmcp import Client
from mcp.types import Implementation

from g6.core import cloud_consent as cloud
from g6.core.consent_declarations import declares
from g6.mcp import build_server
from g6.mcp.cloud_consent import request_grant


def test_mcp_result_mutated_after_receipt_check_cannot_inherit_that_receipt(monkeypatch):
    class Dialog:
        async def elicit_form(self, **kw):
            return SimpleNamespace(action='accept', content={'value': 'Review person'})

    checked, changed = Event(), Event()
    result = {'text': 'shared original'}
    source = cloud.Source.text('shared-document', 'passage', result['text'])
    original = cloud.output_sources

    def scheduled_check(value):
        refs = original(value)
        if value is result:
            assert refs is not None and source in refs
            checked.set()
            assert changed.wait(5), 'mutation thread did not run'
        return refs

    def update_cached_value():
        if checked.wait(5):
            result['text'] = 'PRIVATE_AFTER_RECEIPT_CHECK'
            changed.set()

    monkeypatch.setattr(cloud, 'output_sources', scheduled_check)
    server = build_server()

    @server.tool()
    @declares('source:shared-document')
    def cached_document() -> dict:
        return cloud.bind_output(result, (source,))

    async def run():
        granted = await request_grant(SimpleNamespace(session=Dialog(), request_id='grant'),
                                      'shared-document', 'openai:codex')
        assert granted['status'] == 'granted'
        async with Client(server, client_info=Implementation(name='codex', version='review')) as client:
            return await client.call_tool('cached_document', {})

    thread = Thread(target=update_cached_value)
    thread.start()
    try:
        received = asyncio.run(run())
    finally:
        thread.join(6)
    assert not thread.is_alive()
    # A repair can replace the live receipt check with a frozen snapshot; in
    # that case the removed race hook need not be reached at all.
    if checked.is_set():
        assert changed.is_set()
    assert 'PRIVATE_AFTER_RECEIPT_CHECK' not in str(received), 'changed MCP bytes inherited the pre-mutation receipt'
