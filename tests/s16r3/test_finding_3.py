import asyncio
import json
from types import SimpleNamespace

from fastmcp import Client
from mcp.types import Implementation

from g6.core import cloud_consent as cloud
from g6.harness import completion_hook, completion_loop as loop
from g6.mcp import build_server
from g6.mcp.cloud_consent import request_grant


def test_completed_loop_report_cannot_reuse_receipts_for_changed_report_bytes(tmp_path, monkeypatch):
    class Dialog:
        async def elicit_form(self, **kw):
            return SimpleNamespace(action='accept', content={'value': 'Review person'})

    repo = tmp_path / 'repo'
    repo.mkdir()
    task, session = 'Review a shared task', 'completed-session'
    folder = completion_hook.folder('mcp:' + session)
    folder.mkdir(parents=True)
    source = cloud.Source.text('shared-document', 'report-input', 'shared original report')
    with cloud.collect_sources() as capture:
        cloud.record_sources((source,))
        token = loop._SOURCE_STATE.set(capture)
        try:
            loop._finish(folder, {'sets': {}}, 'shared original report', ending='clean')
        finally:
            loop._SOURCE_STATE.reset(token)
    (folder / 'task.json').write_text(json.dumps({'repo': str(repo), 'task': task, 'base': 'HEAD'}), encoding='utf-8')
    # Simulate a retained file changing after the host saved its complete origins.
    (folder / 'report.md').write_text('PRIVATE_REPORT_REPLACED_AFTER_COMPLETION', encoding='utf-8')
    # Isolate repository validation only; the finished-run path invokes no runner.
    monkeypatch.setattr(loop.repo_tests, 'checked_repo', lambda value: repo)
    monkeypatch.setenv('G6_MCP_SENSITIVE', 'patch_review.review')
    server = build_server()

    async def run():
        context = SimpleNamespace(session=Dialog(), request_id='grant')
        for scope in ('shared-document', 'completion_history', 'host_configuration', 'jobs',
                      cloud.repository_source(repo), cloud.input_source(task)):
            assert (await request_grant(context, scope, 'openai:codex'))['status'] == 'granted'
        # The job retains the start request as a source on the later poll.
        arguments = {'repo': str(repo), 'task': task, 'session': session}
        assert (await request_grant(context, cloud.input_source(json.dumps(arguments, sort_keys=True)),
                                    'openai:codex'))['status'] == 'granted'
        async with Client(server, client_info=Implementation(name='codex', version='review')) as client:
            started = (await client.call_tool('g6_review_until_clean', arguments)).data
            for _ in range(100):
                received = await client.call_tool('metaharness_job', {'job_id': started['job_id']})
                if received.data.get('status') in ('completed', 'failed'):
                    return received
                await asyncio.sleep(.01)
            raise AssertionError('finished-loop job did not finish')

    received = asyncio.run(run())
    assert 'PRIVATE_REPORT_REPLACED_AFTER_COMPLETION' not in str(received), 'changed saved report inherited the original run receipts'
