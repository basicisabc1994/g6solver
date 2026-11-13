# SPDX-License-Identifier: Apache-2.0
"""Host-generated controls survive withholding without disclosing result content."""
import asyncio
import uuid

import pytest
from fastmcp import Client

from g6.mcp import build_server, mission


def data(reply):
    return reply.data if isinstance(reply.data, dict) else reply.structured_content


def test_busy_review_reads_no_saved_state_and_keeps_its_envelope(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from g6.harness import completion_hook, completion_loop as loop
    from g6.mcp import review_loop
    from g6.invariants import check_envelope
    from g6.workspace._envelope import WorkspaceRefused
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    folder = tmp_path / 'session'
    folder.mkdir()
    monkeypatch.setattr(completion_hook, 'folder', lambda _: folder)

    @contextmanager
    def busy(_):
        raise WorkspaceRefused(loop.BUSY, loop.ALREADY_RUNNING)
        yield

    def no_read(_):
        pytest.fail('the running step owns the saved state')

    monkeypatch.setattr(loop, '_held', busy)
    monkeypatch.setattr(loop, '_load', no_read)
    got = review_loop.step(str(tmp_path), 'client task', 'one')
    assert got['ok'] is False and got['action'] == loop.WAIT
    assert got['completion_state'] == 'blocked-escalated'
    assert got['warning_card']['code'] == loop.BUSY
    assert got['run_id'] == folder.name and got['request_id']
    assert check_envelope(got) == []


def test_withheld_review_job_names_its_reviewer_provider(tmp_path, monkeypatch):
    from g6.core import cloud_consent as cloud
    from g6.harness import completion_hook
    from g6.mcp import review_loop
    from mcp.types import Implementation
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('G6_MCP_SENSITIVE', 'patch_review')
    monkeypatch.setattr(completion_hook, 'folder', lambda _: tmp_path / 'session')

    def step(*args):
        with cloud.collect_sources():
            try:
                with cloud.admission('anthropic:api', (cloud.Source.text('review', 'patch', 'PLANTED_REVIEW'),),
                                     cloud.sha256('PLANTED_REVIEW')):
                    pytest.fail('the ungranted review was sent')
            except cloud.LLMError:
                pass
        return {'ok': False, 'action': 'stop', 'ending': 'untested', 'report': 'PLANTED_REVIEW',
                'error': f'[{cloud.REQUIRED}] review unsent', 'request_id': 'request', 'run_id': 'run'}

    monkeypatch.setattr(review_loop, '_step', step)
    async def call():
        async with Client(build_server(), client_info=Implementation(name='codex', version='test')) as client:
            made = data(await client.call_tool('g6_review_until_clean',
                {'repo': str(tmp_path), 'task': 'task', 'session': 'session'}))
            for _ in range(200):
                result = await client.call_tool('metaharness_job', {'job_id': made['job_id']})
                if data(result).get('status') not in ('pending', 'running'):
                    return result
                await asyncio.sleep(.005)
            pytest.fail('the fake review job did not finish')
    received = asyncio.run(call())
    result = data(received)['result']
    assert result['ending'] == 'untested' and result['action'] == 'stop'
    assert 'anthropic:api' in result['report'] and 'g6_cloud_grant' in result['report']
    assert 'PLANTED_REVIEW' not in str(received)


@pytest.mark.parametrize('tool,arguments', [
    ('g6_improve_start', {}),
    ('g6_mission_create', {'repo': 'unused', 'objective': 'unused'}),
    ('g6_review_patch', {'repo': 'unused', 'task': 'unused'}),
    ('g6_review_until_clean', {'repo': 'unused', 'task': 'unused', 'session': 'unused'}),
    ('metaharness_run', {'workflow': 'unused', 'task_input': 'unused'}),
    ('metaharness_evaluate', {'workflow': 'unused', 'proposal_digest': 'unused'}),
    ('metaharness_evaluate_start', {'workflow': 'unused', 'proposal_digest': 'unused'}),
])
def test_static_host_refusal_keeps_its_code_without_a_sharing_grant(tmp_path, monkeypatch, tool, arguments):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    monkeypatch.delenv('G6_MCP_SENSITIVE', raising=False)

    async def call():
        async with Client(build_server()) as client:
            return data(await client.call_tool(tool, arguments))

    result = asyncio.run(call())
    assert result['warning_card']['code'] == 'G6_E_SENSITIVE_COMPONENT_DENIED'
    assert result['completion_state'] == 'blocked-escalated'
    assert all(name in result for name in ('request_id', 'run_id', 'evidence', 'warning_card'))


def test_bad_settings_keeps_its_earlier_code_and_withholds_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('G6_MCP_SENSITIVE', 'metaharness.improve')
    folder = tmp_path / 'PLANTED_UNSHARED_SETTINGS'
    folder.mkdir()
    (folder / 'settings.json').write_text('{not json', encoding='utf-8')

    async def call():
        async with Client(build_server()) as client:
            return await client.call_tool('g6_improve_start', {'folder': str(folder)})

    received = asyncio.run(call())
    result = data(received)
    assert result['warning_card']['code'] == 'G6_E_LOOP_BAD_SETTINGS'
    assert result['cloud_consent_code'] == 'G6_E_CLOUD_CONSENT_REQUIRED'
    assert 'PLANTED' not in str(received)


def test_mission_handle_and_host_envelope_survive_withheld_content(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    server, handle, made = build_server(), uuid.uuid4().hex, []
    monkeypatch.setattr(mission.api, 'bind', lambda: object())
    monkeypatch.setattr(mission.api, 'create_coding_mission', lambda *a, **k: {
        'mission_id': handle, 'status': 'created', 'private': 'PLANTED_MISSION_CONTENT'})

    @server.tool()
    def fresh_host_receipt() -> dict:
        result = mission.create('fixture-repository', 'fixture objective')
        made.append(result)
        return result

    async def call():
        async with Client(server) as client:
            return await client.call_tool('fresh_host_receipt', {})

    received = asyncio.run(call())
    result = data(received)
    assert result['mission_id'] == handle
    assert result['request_id'] == made[0]['request_id']
    assert result['run_id'] == made[0]['run_id']
    assert result['warning_card']['code'] == 'G6_E_CLOUD_CONSENT_REQUIRED'
    assert 'PLANTED' not in str(received)


def test_result_supplied_mission_and_run_ids_are_not_host_controls(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    server, hidden_handle = build_server(), uuid.uuid4().hex

    @server.tool()
    def untrusted_receipt() -> dict:
        return mission.reply({'mission_id': hidden_handle, 'private': 'PLANTED_SOURCE_VALUE'})

    async def call():
        async with Client(server) as client:
            return await client.call_tool('untrusted_receipt', {})

    received = asyncio.run(call())
    assert hidden_handle not in str(received) and 'PLANTED' not in str(received)
    assert data(received)['warning_card']['code'] == 'G6_E_CLOUD_CONSENT_REQUIRED'


def test_interrupted_review_keeps_its_earlier_code_and_withholds_host_diagnostics(tmp_path, monkeypatch):
    from g6.core import component_process
    from g6.harness import patch_review

    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('G6_MCP_SENSITIVE', 'patch_review.review')

    def interrupted(*args, **kwargs):
        raise component_process.ContainmentFailure('PLANTED_HOST_DIAGNOSTIC')

    monkeypatch.setattr(patch_review, 'review_patch', interrupted)

    async def call():
        async with Client(build_server()) as client:
            job = data(await client.call_tool('g6_review_patch', {'repo': 'unused', 'task': 'fixture'}))
            for _ in range(400):
                received = await client.call_tool('metaharness_job', {'job_id': job['job_id']})
                if data(received).get('status') in ('completed', 'failed'):
                    return received
                await asyncio.sleep(0.01)
        raise AssertionError('the fake review did not finish')

    received = asyncio.run(call())
    result = data(received)['result']
    assert result['warning_card']['code'] == 'G6_E_PATCH_REVIEW_INTERRUPTED'
    assert result['cloud_consent_code'] == 'G6_E_CLOUD_CONSENT_REQUIRED'
    assert 'PLANTED' not in str(received)
