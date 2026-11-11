# SPDX-License-Identifier: Apache-2.0
"""The opt-in business-test fixture uses dialog grants for actual sources."""
import asyncio
import json
from pathlib import Path

from fastmcp import Client
from mcp.types import Implementation
import pytest

from g6.core import cloud_consent as cloud
from g6.core.consent_declarations import declares
from g6.mcp import build_server
from g6.metaharness.jobs import Jobs


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))


def service():
    server, jobs = build_server(), Jobs()

    @server.tool()
    @declares('client_request', 'client_repository', 'source:host_configuration')
    def fixture_read(repo: str, requirements: list[dict]) -> dict:
        def read():
            # The same normalization used by verification_proposer.context.
            text = json.dumps(requirements)
            report = (Path(repo) / 'fixture-report.txt').read_text(encoding='utf-8')
            return cloud.bind_output({'report': report}, (
                cloud.Source.text(cloud.input_source(text), 'requirements', text),
                cloud.Source.text('host:configuration', 'diagnostic', 'SYNTHETIC_DIAGNOSTIC'),))
        return jobs.start(read)

    @server.tool()
    @declares('client_request', 'source:jobs')
    def fixture_poll(job_id: str) -> dict:
        return jobs.status(job_id)

    return server, jobs


def flow(server, arguments):
    (Path(arguments['repo']) / 'fixture-report.txt').write_text('SYNTHETIC_READ', encoding='utf-8')
    async def run():
        async with Client(server, client_info=Implementation(name='codex', version='test')) as client:
            job = (await client.call_tool('fixture_read', arguments)).data
            for _ in range(500):
                reply = (await client.call_tool('fixture_poll', {'job_id': job['job_id']})).data
                if reply.get('status') == 'completed':
                    return reply['result']
                await asyncio.sleep(.01)
        raise AssertionError('job did not complete')
    return asyncio.run(run())


def test_without_fixture_the_same_repository_flow_is_withheld(tmp_path):
    server, jobs = service()
    try:
        reply = flow(server, {'repo': str(tmp_path), 'requirements': [{'id': 'R1', 'text': 'synthetic'}]})
        assert reply['cloud_consent_code'] == cloud.REQUIRED
        assert 'SYNTHETIC_READ' not in str(reply)
        assert cloud.grants() == []
    finally:
        jobs.close()


def test_opt_in_fixture_grants_repository_and_derived_arguments(tmp_path, declared_mcp_grants):
    server, jobs = service()
    try:
        reply = flow(server, {'repo': str(tmp_path), 'requirements': [{'id': 'R1', 'text': 'synthetic'}]})
        assert reply == {'report': 'SYNTHETIC_READ'}
        sources = {row['source'] for row in cloud.grants()}
        assert cloud.repository_source(tmp_path) in sources
        assert 'host:configuration' in sources
    finally:
        jobs.close()


def test_poll_input_identity_contains_the_actual_job_handle():
    first = json.dumps({'job_id': 'first'}, sort_keys=True)
    second = json.dumps({'job_id': 'second'}, sort_keys=True)
    assert cloud.input_source(first) != cloud.input_source(second)
    assert cloud.input_source(first) == cloud.input_source(first)


def test_repeated_job_starts_keep_the_input_but_polls_name_different_handles(tmp_path):
    server, jobs = service()
    (tmp_path / 'fixture-report.txt').write_text('SYNTHETIC_READ', encoding='utf-8')

    async def run():
        async with Client(server, client_info=Implementation(name='codex', version='test')) as client:
            arguments = {'repo': str(tmp_path), 'requirements': []}
            starts = [(await client.call_tool('fixture_read', arguments)).data for _ in range(2)]
            replies = []
            for job in starts:
                for _ in range(500):
                    reply = (await client.call_tool('fixture_poll', {'job_id': job['job_id']})).data
                    if reply.get('status') == 'completed':
                        replies.append(reply['result'])
                        break
                    await asyncio.sleep(.01)
            return arguments, starts, replies

    try:
        arguments, starts, replies = asyncio.run(run())
        assert starts[0]['job_id'] != starts[1]['job_id']
        start_source = cloud.input_source(json.dumps(arguments, sort_keys=True))
        for job, reply in zip(starts, replies, strict=True):
            message = reply['warning_card']['message']
            assert start_source in message
            assert cloud.input_source(json.dumps({'job_id': job['job_id']}, sort_keys=True)) in message
        assert replies[0]['warning_card']['message'] != replies[1]['warning_card']['message']
    finally:
        jobs.close()


def test_identical_withheld_calls_keep_their_input_identity(tmp_path):
    server = build_server()
    (tmp_path / 'fixture-report.txt').write_text('SYNTHETIC_READ', encoding='utf-8')

    @server.tool()
    @declares('client_request', 'client_repository')
    def fixture_repeat(repo: str) -> dict:
        return {'report': (Path(repo) / 'fixture-report.txt').read_text(encoding='utf-8')}

    async def run():
        async with Client(server, client_info=Implementation(name='codex', version='test')) as client:
            arguments = {'repo': str(tmp_path)}
            return [(await client.call_tool('fixture_repeat', arguments)).data for _ in range(2)]
    first, second = asyncio.run(run())
    assert first['warning_card']['message'] == second['warning_card']['message']
    assert first['cloud_consent_code'] == second['cloud_consent_code'] == cloud.REQUIRED


def test_existing_loop_runs_as_a_consented_person(tmp_path, monkeypatch, declared_mcp_grants):
    from tests.core.test_mcp_review_loop import Rig, reviewer
    failures = []
    run = Jobs._run

    def observed(self, *args, **kwargs):
        try:
            return run(self, *args, **kwargs)
        except Exception as exc:
            failures.append(f'{type(exc).__name__}: {exc}')
            raise

    monkeypatch.setattr(Jobs, '_run', observed)
    rig = Rig(tmp_path, monkeypatch)
    rig.approve(reviewer('one'))
    rig.edit()
    got = rig.step()
    assert got.get('action') == 'continue', (failures, got)
