import asyncio
from types import SimpleNamespace

import pytest
from fastmcp import Client
from mcp.types import Implementation

from g6.core import cloud_consent as cloud
from g6.core.consent_declarations import declares
from g6.mcp import build_server
from g6.mcp.cloud_consent import request_grant
from g6.metaharness.jobs import Jobs


@pytest.mark.parametrize('detail', ['full', 'compact'])
def test_retained_job_rechecks_that_its_receipt_matches_the_returned_bytes(detail):
    class Dialog:
        async def elicit_form(self, **kwargs):
            return SimpleNamespace(action='accept', content={'value': 'Review person'})

    async def grant(scope):
        got = await request_grant(SimpleNamespace(session=Dialog(), request_id='grant'), scope, 'openai:codex')
        assert got['status'] == 'granted'

    async def run():
        await grant('original-document')
        await grant('jobs')
        cached = {'text': 'original shared data'}
        compact_cached = dict(cached)
        source = cloud.Source.text('original-document', 'passage', cached['text'])
        jobs = Jobs()
        try:
            with cloud.collect_sources():
                started = jobs.start(lambda: cloud.bind_output(cached, (source,)),
                    compact=lambda value, ident: cloud.bind_output(compact_cached, (source,)))
            jobs.close()
            # A retained result is a live reference. Its producer updates the cache
            # after the job has finished, outside the completed job's capture.
            (cached if detail == 'full' else compact_cached)['text'] = 'PRIVATE_UNRECORDED_CACHE_UPDATE'
            server = build_server()

            @server.tool()
            @declares('source:jobs')
            def retained_job(job_id: str) -> dict:
                return jobs.status(job_id, detail=detail)

            async with Client(server, client_info=Implementation(name='codex', version='review')) as client:
                return await client.call_tool('retained_job', {'job_id': started['job_id']})
        finally:
            jobs.close()

    received = asyncio.run(run())
    assert 'PRIVATE_UNRECORDED_CACHE_UPDATE' not in str(received), 'mutated retained bytes inherited the old job receipt'
