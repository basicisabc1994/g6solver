"""Repeat a run made by the real retained-state producers, over MCP and job polls."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastmcp import Client
from mcp.types import Implementation

from g6.core import cloud_consent as cloud
from g6.harness import completion_hook, completion_loop as loop
from g6.mcp import build_server, review_loop
from g6.mcp.cloud_consent import request_grant


@pytest.mark.parametrize('change', ['none', 'report', 'state'])
@pytest.mark.parametrize('detail', ['full', 'compact'])
def test_completed_review_keeps_granted_report_but_never_relabels_replacements(tmp_path, monkeypatch, change, detail):
    class Dialog:
        async def elicit_form(self, **kw):
            return SimpleNamespace(action='accept', content={'value': 'Person'})
    repo = tmp_path / 'repo'
    repo.mkdir()
    monkeypatch.setattr(loop.repo_tests, 'checked_repo', lambda *a: repo)
    monkeypatch.setattr(loop.repo_tests, 'change', lambda *a: SimpleNamespace(base_commit='HEAD'))
    monkeypatch.setenv('G6_MCP_SENSITIVE', 'patch_review.review')
    task, session = 'shared task', 'completed-session'
    folder = completion_hook.folder('mcp:' + session)
    folder.mkdir(parents=True)
    review_loop._asked(str(repo), task, 'HEAD', folder)
    source = cloud.Source.text('shared-document', 'passage', 'SHARED_COMPLETED_REPORT')
    with cloud.collect_sources() as capture:
        cloud.record_sources((source,))
        token = loop._SOURCE_STATE.set(capture)
        try:
            loop._finish(folder, {'sets': {}}, 'SHARED_COMPLETED_REPORT', ending='clean')
        finally:
            loop._SOURCE_STATE.reset(token)
    if change == 'report':
        (folder / 'report.md').write_text('PRIVATE_REPLACED_REPORT', encoding='utf-8')
    elif change == 'state':
        data = json.loads((folder / 'state.json').read_text(encoding='utf-8'))
        data['stop'] = 'PRIVATE_REPLACED_STATE'
        (folder / 'state.json').write_text(json.dumps(data), encoding='utf-8')
    args = {'repo': str(repo), 'task': task, 'session': session}
    server = build_server()
    async def run():
        context = SimpleNamespace(session=Dialog(), request_id='grant')
        for scope in ('shared-document', 'completion_history', 'host_configuration', 'jobs',
                      cloud.repository_source(repo), cloud.input_source(task),
                      cloud.input_source(json.dumps(args, sort_keys=True))):
            assert (await request_grant(context, scope, 'openai:codex'))['status'] == 'granted'
        async with Client(server, client_info=Implementation(name='codex', version='test')) as client:
            handle = (await client.call_tool('g6_review_until_clean', args)).data['job_id']
            for _ in range(100):
                result = await client.call_tool('metaharness_job', {'job_id': handle, 'detail': detail})
                if result.data.get('status') in ('completed', 'failed'):
                    return result
                await asyncio.sleep(.01)
            raise AssertionError('completed review job did not finish')
    result = asyncio.run(run())
    assert 'PRIVATE_REPLACED' not in str(result)
    if change == 'none':
        assert result.data['result']['report'] == 'SHARED_COMPLETED_REPORT'
    else:
        assert cloud.REQUIRED in str(result)
