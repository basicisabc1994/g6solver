import asyncio
from types import SimpleNamespace

from fastmcp import Client
from mcp.types import Implementation

from g6.core import cloud_consent as cloud
from g6.mcp import build_server
from g6.mcp.cloud_consent import request_grant


def test_job_poll_preserves_revoked_repository_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('G6_MCP_SENSITIVE', 'metaharness.run')
    document = tmp_path / 'document.txt'
    document.write_text('PRIVATE_REVOKED_JOB_CONTENT', encoding='utf-8')
    scope = cloud.repository_source(tmp_path)

    class Dialog:
        async def elicit_form(self, **kwargs):
            return SimpleNamespace(action='accept', content={'value': 'Review person'})

    class Surface:
        def run(self, workflow, task_input):
            text = document.read_text(encoding='utf-8')
            return cloud.bind_output({'result': text}, (cloud.Source.text(scope, 'document.txt', text),))

    async def run():
        context = SimpleNamespace(session=Dialog(), request_id='grant')
        assert (await request_grant(context, 'jobs', 'openai:codex'))['status'] == 'granted'
        cloud.revoke(scope, 'openai:codex')
        async with Client(build_server(Surface()), client_info=Implementation(name='codex', version='review')) as client:
            started = await client.call_tool('metaharness_run', {'workflow': 'fake', 'task_input': 'literal'})
            for _ in range(200):
                reply = await client.call_tool('metaharness_job', {'job_id': started.data['job_id'], 'detail': 'full'})
                if reply.data['status'] not in ('pending', 'running'):
                    return reply
                await asyncio.sleep(.005)
        raise AssertionError('fake job did not finish')

    reply = asyncio.run(run())
    assert not cloud.is_granted(scope, 'openai:codex')
    assert 'PRIVATE_REVOKED_JOB_CONTENT' not in str(reply), 'job declaration discarded revoked repository provenance'
