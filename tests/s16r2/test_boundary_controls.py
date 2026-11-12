import asyncio
from types import SimpleNamespace

import pytest
from fastmcp import Client
from mcp.types import Implementation

from g6.core import cloud_consent as cloud, consent_callers as callers
from g6.core.consent_declarations import declares
from g6.mcp import build_server
from g6.mcp.cloud_consent import request_grant


class Dialog:
    async def elicit_form(self, **kwargs):
        return SimpleNamespace(action='accept', content={'value': 'Review person'})


async def agreed(source, provider='openai:codex'):
    result = await request_grant(SimpleNamespace(session=Dialog(), request_id='grant'), source, provider)
    assert result['status'] == 'granted'


def test_process_separate_prompt_directory_and_environment(tmp_path):
    from g6.agents._runtime.consent import consent_run
    from g6.agents._runtime.model import AgentRefusal
    sent = []
    runner = SimpleNamespace(run=lambda *args, **kwargs: sent.append(kwargs['prompt']))
    marker = 'PRIVATE_PROCESS_PROMPT'
    asyncio.run(agreed(cloud.input_source(marker)))
    asyncio.run(agreed('agent_environment'))
    with pytest.raises(AgentRefusal, match=cloud.REQUIRED):
        consent_run(runner, ['fake'], provider='openai:codex', prompt=marker, cwd=tmp_path)
    assert not sent
    asyncio.run(agreed(cloud.repository_source(tmp_path)))
    consent_run(runner, ['fake'], provider='openai:codex', prompt=marker, cwd=tmp_path)
    assert sent == [marker]
    cloud.revoke(cloud.input_source(marker), 'openai:codex')
    with pytest.raises(AgentRefusal, match=cloud.REQUIRED):
        consent_run(runner, ['fake'], provider='openai:codex', prompt=marker, cwd=tmp_path)
    assert sent == [marker]


def test_scout_additional_directory_checked_before_locating_a_program(tmp_path, monkeypatch):
    from g6.harness.completion_loop import ToolSession
    cwd, additional = tmp_path / 'workspace', tmp_path / 'additional'
    for source in ('model_inputs', 'agent_environment', cloud.repository_source(cwd)):
        asyncio.run(agreed(source, 'anthropic:claude_code'))
    def no_program(self):
        pytest.fail('scout crossed admission without its additional-directory grant')
    monkeypatch.setattr(ToolSession, 'locate', no_program)
    with pytest.raises(cloud.LLMError, match=cloud.REQUIRED):
        ToolSession().run('prompt', cwd, additional, 1)


def test_sdk_enclosing_admission_cannot_expand_even_to_a_granted_source():
    class SDK(callers.SDKClient):
        base_url = 'https://api.anthropic.com'
    sdk = SDK()
    for source in ('model_inputs', 'first', 'second'):
        asyncio.run(agreed(source, 'anthropic:api'))
    first = cloud.Source.text('first', 'item', 'first')
    second = cloud.Source.text('second', 'item', 'second')
    with cloud.request_sources((first,)):
        with cloud.admission('anthropic:api', callers.model_sources(), cloud.sha256('first')):
            with callers._sdk_call(sdk, 'anthropic:api'), cloud.request_sources((second,)):
                with pytest.raises(cloud.LLMError, match='sources changed'):
                    with callers.sdk_send(sdk, {'text': 'second'}):
                        pytest.fail('enclosing SDK sources expanded')


def test_same_client_repository_pointer_requires_its_own_grant(tmp_path):
    path = tmp_path / 'document.txt'
    path.write_text('PRIVATE_POINTER_REFERENT', encoding='utf-8')
    server = build_server()
    @server.tool()
    @declares('client_request', 'client_repository')
    def read(repo: str) -> dict:
        return {'text': path.read_text(encoding='utf-8')}
    async def run():
        async with Client(server, client_info=Implementation(name='codex', version='review')) as client:
            assert 'PRIVATE_POINTER_REFERENT' not in str(await client.call_tool('read', {'repo': str(tmp_path)}))
            await agreed(cloud.repository_source(tmp_path))
            assert 'PRIVATE_POINTER_REFERENT' in str(await client.call_tool('read', {'repo': str(tmp_path)}))
    asyncio.run(run())


def test_opaque_failed_job_withholds_exception_but_keeps_its_fixed_code():
    from g6.metaharness.jobs import Jobs
    asyncio.run(agreed('jobs'))
    jobs = Jobs()
    def fail():
        raise RuntimeError('PRIVATE_OPAQUE_EXCEPTION')
    try:
        with cloud.collect_sources():
            started = jobs.start(fail)
        jobs.close()
        server = build_server()
        @server.tool()
        @declares('source:jobs')
        def poll(job_id: str) -> dict:
            return jobs.status(job_id)
        async def run():
            async with Client(server, client_info=Implementation(name='codex', version='review')) as client:
                return await client.call_tool('poll', {'job_id': started['job_id']})
        received = str(asyncio.run(run()))
        assert 'PRIVATE_OPAQUE_EXCEPTION' not in received
        assert 'G6_E_JOB_FAILED' in received and cloud.REQUIRED in received
    finally:
        jobs.close()
