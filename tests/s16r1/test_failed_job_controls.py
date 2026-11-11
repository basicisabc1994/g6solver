# SPDX-License-Identifier: Apache-2.0
"""Opaque exception content is withheld; the host's failure code stays stable."""
import asyncio

from fastmcp import Client
from mcp.types import Implementation

from g6.core import cloud_consent as cloud
from g6.core.consent_declarations import declares
from g6.mcp import build_server
from tests.s16r1.test_roots import grant


def test_failed_job_keeps_its_host_code_without_disclosing_exception_text(tmp_path, monkeypatch):
    import g6.metaharness.jobs as module
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    grant('jobs')
    jobs = module.Jobs()
    monkeypatch.setattr(module, 'Jobs', lambda: jobs)
    marker = 'PRIVATE_EXCEPTION_FILENAME.txt'
    server = build_server()
    @server.tool()
    @declares('source:jobs')
    def fail_later() -> dict:
        def fail():
            raise RuntimeError(marker)
        return jobs.start(fail)
    async def read():
        async with Client(server, client_info=Implementation(name='codex', version='repair')) as client:
            handle = (await client.call_tool('fail_later', {})).data['job_id']
            for _ in range(200):
                reply = await client.call_tool('metaharness_job', {'job_id': handle})
                if reply.data.get('status') == 'failed':
                    return reply
                await asyncio.sleep(.005)
        raise AssertionError('the synthetic job did not finish')
    try:
        reply = asyncio.run(read())
        assert marker not in str(reply)
        assert reply.data['warning_card']['code'] == 'G6_E_JOB_FAILED'
        assert reply.data['cloud_consent_code'] == cloud.REQUIRED
    finally:
        jobs.close()
