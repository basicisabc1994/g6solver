# SPDX-License-Identifier: Apache-2.0
"""Cloud clients are recipients too; source data must not escape in any reply field."""
import asyncio
import importlib
import json
from types import SimpleNamespace

import pytest
from mcp.types import Implementation


def modules():
    assert importlib.util.find_spec('g6.mcp.cloud_consent'), 'no cloud-client reply boundary exists'
    return importlib.import_module('g6.core.cloud_consent'), importlib.import_module('g6.mcp.cloud_consent')


def ctx(name):
    return SimpleNamespace(session=SimpleNamespace(client_params=SimpleNamespace(clientInfo=SimpleNamespace(name=name))),
                           request_id='request')


@pytest.mark.parametrize('name,provider', [('claude-code', 'anthropic:claude_code'), ('codex', 'openai:codex'),
                                        ('unknown-client', 'cloud:unknown')])
def test_provider_comes_from_mcp_client_information(name, provider):
    _, m = modules()
    assert m.client_provider(ctx(name)) == provider


def test_unshared_source_reply_contains_only_generic_refusal(tmp_path, monkeypatch):
    c, m = modules()
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    planted = 'PLANTED_FROM_UNSHARED_DOCUMENT'
    source = c.Source.text('document', 'passage', planted)
    result = {'ok': True, 'result': planted, 'evidence': [{'summary': planted}], 'warning_card': planted,
              'request_id': 'request', 'run_id': 'run'}
    reply = m.protect_reply(ctx('codex'), result, (source,))
    assert planted not in json.dumps(reply)
    assert reply['completion_state'] == 'blocked-escalated'
    assert 'request_id' in reply and 'run_id' in reply
    assert reply['evidence'] == []
    assert 'withheld from a source not shared with this provider' in json.dumps(reply)
    assert 'g6_cloud_grant' in json.dumps(reply)


def test_client_named_repository_requires_a_dialog_grant_and_is_logged(tmp_path, monkeypatch, accepted_cloud_dialog):
    c, m = modules()
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    repo = c.repository_source(tmp_path)
    source = c.Source.text(repo, 'code.py', 'PLANTED_NAMED_REPO')
    result = {'ok': True, 'result': 'PLANTED_NAMED_REPO'}
    assert m.protect_reply(ctx('codex'), result, (source,), supplied=(repo,))['ok'] is False
    assert not c.is_granted(repo, 'openai:codex')
    assert m.protect_reply(ctx('codex'), result, (source,))['ok'] is False
    accepted_cloud_dialog(repo, 'openai:codex', text='PLANTED_NAMED_REPO')
    assert m.protect_reply(ctx('codex'), result, (source,), supplied=(repo,)) == result
    assert c.disclosures()[-1]['provider'] == 'openai:codex'


def test_unknown_source_withheld_even_when_repository_was_named(tmp_path, monkeypatch):
    c, m = modules()
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    result = {'result': 'PLANTED_UNRECORDED'}
    assert 'PLANTED_UNRECORDED' not in json.dumps(m.protect_reply(ctx('codex'), result, (), supplied=('repo',)))


def test_tools_add_only_and_no_agent_answer_field():
    modules()
    from g6.mcp import build_server
    tools = asyncio.run(build_server().get_tools())
    assert {'g6_cloud_grant', 'g6_cloud_revoke', 'g6_cloud_grants', 'g6_disclosures'} <= set(tools)
    schema = tools['g6_cloud_grant'].parameters
    assert set(schema['properties']) == {'source', 'provider'}


def test_actual_mcp_reply_cannot_return_automatically_read_document(tmp_path, monkeypatch):
    modules()
    from fastmcp import Client
    from g6.mcp import build_server
    path = tmp_path / 'document.txt'
    path.write_text('PLANTED_AUTOMATIC_READ', encoding='utf-8')
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    server = build_server()
    @server.tool()
    def read_implicit_document() -> dict:
        return {'result': path.read_text(encoding='utf-8')}
    async def run():
        async with Client(server) as client:
            reply = await client.call_tool('read_implicit_document', {})
            return str(reply)
    assert 'PLANTED_AUTOMATIC_READ' not in asyncio.run(run())


def test_job_keeps_source_context_and_poll_rechecks_revocation(tmp_path, monkeypatch):
    c, m = modules()
    from g6.metaharness.jobs import Jobs
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    source = c.Source.text('document', 'passage', 'PLANTED_JOB')
    jobs = Jobs()
    seen = []
    with c.collect_sources():
        def work():
            c.record_sources((source,))
            seen.append(c.current_sources())
            return {'result': 'PLANTED_JOB'}
        started = jobs.start(work)
    jobs.close()
    with c.collect_sources() as poll:
        result = jobs.status(started['job_id'])
        reply = m.protect_reply(ctx('codex'), result, poll.sources)
    assert source in poll.sources
    assert 'PLANTED_JOB' not in json.dumps(reply)


@pytest.mark.parametrize('read_named_repo', [False, True])
def test_preloaded_data_cannot_be_laundered_by_partial_read_tracking(tmp_path, monkeypatch, read_named_repo):
    c, m = modules()
    from fastmcp import Client
    from g6.mcp import build_server
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    path = tmp_path / 'code.py'
    path.write_text('public repository data', encoding='utf-8')
    cached = 'PLANTED_PRELOADED_OTHER_SOURCE'
    server = build_server()
    @server.tool()
    def combine(repo: str) -> dict:
        text = path.read_text(encoding='utf-8') if read_named_repo else ''
        return {'result': text + cached}
    async def run():
        async with Client(server, client_info=Implementation(name='codex', version='test')) as client:
            return str(await client.call_tool('combine', {'repo': str(tmp_path)}))
    assert cached not in asyncio.run(run())


def test_source_text_in_identifier_fields_is_also_withheld(tmp_path, monkeypatch):
    c, m = modules()
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    result = {'result': 'secret', 'request_id': 'PLANTED_ID_SECRET', 'run_id': 'PLANTED_RUN_SECRET'}
    source = c.Source.text('source', 'file', 'secret')
    reply = m.protect_reply(ctx('codex'), result, (source,))
    assert 'PLANTED' not in json.dumps(reply)


def test_named_working_tree_cannot_override_explicit_revocation(tmp_path, monkeypatch):
    c, m = modules()
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    repo = c.repository_source(tmp_path)
    c.revoke(repo, 'openai:codex')
    source = c.Source.text(repo, 'code.py', 'PLANTED_REVOKED')
    reply = m.protect_reply(ctx('codex'), {'result': 'PLANTED_REVOKED'}, (source,), supplied=(repo,))
    assert 'PLANTED_REVOKED' not in json.dumps(reply)


@pytest.mark.parametrize('unshared', [False, True])
def test_actual_source_bound_reply_only_shares_named_working_tree(tmp_path, monkeypatch, unshared, accepted_cloud_dialog):
    c, m = modules()
    from fastmcp import Client
    from g6.mcp import build_server
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    repo = tmp_path / 'repo'
    repo.mkdir()
    source = tmp_path / 'document.txt' if unshared else repo / 'code.py'
    source.write_text('PLANTED_BOUND_RESULT', encoding='utf-8')
    if not unshared:
        accepted_cloud_dialog(c.repository_source(repo), 'openai:codex', text='PLANTED_BOUND_RESULT')
    server = build_server()
    @server.tool()
    def read_source(repo: str) -> dict:
        value = source.read_text(encoding='utf-8')
        scope = c.source_id(str(source)) if unshared else c.repository_source(repo)
        return c.bind_output({'result': value}, (c.Source.text(scope, source.name, value),))
    async def run():
        async with Client(server, client_info=Implementation(name='codex', version='test')) as client:
            return str(await client.call_tool('read_source', {'repo': str(repo)}))
    result = asyncio.run(run())
    assert ('PLANTED_BOUND_RESULT' in result) is not unshared
    if not unshared:
        assert c.disclosures()[-1]['provider'] == 'openai:codex'


def test_equal_rendered_value_cannot_borrow_another_objects_sources(tmp_path, monkeypatch):
    c, m = modules()
    from fastmcp import Client
    from g6.mcp import build_server
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    repo = tmp_path / 'repo'
    repo.mkdir()
    server = build_server()
    @server.tool()
    def wrong_value(repo: str) -> dict:
        unknown = {'result': 'PLANTED_EQUAL_VALUE'}
        other = {'result': 'PLANTED_EQUAL_VALUE'}
        c.bind_output(other, (c.Source.text(c.repository_source(repo), 'file', other['result']),))
        return unknown
    async def run():
        async with Client(server, client_info=Implementation(name='codex', version='test')) as client:
            return str(await client.call_tool('wrong_value', {'repo': str(repo)}))
    assert 'PLANTED_EQUAL_VALUE' not in asyncio.run(run())


def test_discovery_tool_name_cannot_authorize_a_replacement_body(tmp_path, monkeypatch):
    c, m = modules()
    from fastmcp import Client
    from g6.mcp import build_server
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    server = build_server()
    @server.tool(name='g6_help')
    def replacement() -> dict:
        return {'result': 'PLANTED_SHADOW_BODY'}
    async def run():
        async with Client(server) as client:
            return str(await client.call_tool('g6_help', {}))
    assert 'PLANTED_SHADOW_BODY' not in asyncio.run(run())


def test_legacy_saved_report_cannot_rebind_old_reason_to_repository(tmp_path, monkeypatch):
    c, m = modules()
    from g6.mcp import review_loop
    from g6.harness import completion_hook, completion_loop as loop
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    folder = tmp_path / 'session'
    folder.mkdir()
    monkeypatch.setattr(completion_hook, 'folder', lambda name: folder)
    monkeypatch.setattr(loop.repo_tests, 'checked_repo', lambda repo: tmp_path)
    monkeypatch.setattr(loop, '_held', lambda folder: __import__('contextlib').nullcontext())
    monkeypatch.setattr(loop, 'advance', lambda *a, **kw: loop.Step(loop.STOP, ending='contested'))
    (folder / 'task.json').write_text(json.dumps({'repo': str(tmp_path), 'task': 'task', 'base': 'base'}), encoding='utf-8')
    (folder / 'state.json').write_text(json.dumps({'done': True, 'ending': 'contested',
        'left': 'PLANTED_OLD_REASON', 'stop': loop.LEFT}), encoding='utf-8')
    (folder / 'report.md').write_text('PLANTED_OLD_REASON', encoding='utf-8')
    c.revoke(c.input_source('PLANTED_OLD_REASON'), 'openai:codex')
    with c.collect_sources():
        result = review_loop.step(str(tmp_path), 'task', 'one', reason='')
        refs = c.output_sources(result)
        reply = m.protect_reply(ctx('codex'), result, refs or (),
            supplied=(c.repository_source(tmp_path), c.input_source('task')))
    assert 'PLANTED_OLD_REASON' not in json.dumps(reply)


def test_revocation_after_middleware_approval_stops_transport_send(tmp_path, monkeypatch):
    c, m = modules()
    from fastmcp import Client
    from g6.mcp import build_server
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    class Session:
        async def elicit_form(self, **kwargs):
            return SimpleNamespace(action='accept', content={'value': 'Person'})
    asyncio.run(m.request_grant(SimpleNamespace(session=Session(), request_id='r'), 'source-a', 'openai:codex'))
    server = build_server()
    @server.tool()
    def read_source() -> dict:
        return c.bind_output({'result': 'PLANTED_AFTER_REVOCATION'},
            (c.Source.text('source-a', 'file', 'PLANTED_AFTER_REVOCATION'),))
    from g6.mcp import consent_transport
    original = consent_transport.prepare
    def revoke_after_approval(session, request_id, result, refs, *args, **kwargs):
        approved = original(session, request_id, result, refs, *args, **kwargs)
        if result.structured_content.get('result') == 'PLANTED_AFTER_REVOCATION':
            c.revoke('source-a', 'openai:codex')
        return approved
    monkeypatch.setattr(consent_transport, 'prepare', revoke_after_approval)
    async def run():
        async with Client(server, client_info=Implementation(name='codex', version='test')) as client:
            return str(await client.call_tool('read_source', {}))
    assert 'PLANTED_AFTER_REVOCATION' not in asyncio.run(run())


def test_real_client_dialog_grants_once_and_revocation_stops_next_reply(tmp_path, monkeypatch):
    c, _ = modules()
    from fastmcp import Client
    from g6.mcp import build_server
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    server, questions = build_server(), []
    @server.tool()
    def document() -> dict:
        return c.bind_output({'result': 'PLANTED_DIALOG_DOCUMENT'},
            (c.Source.text('document-a', 'passage', 'PLANTED_DIALOG_DOCUMENT'),))
    async def accept(message, response_type, params, context):
        questions.append(message)
        return {'value': 'Person'}
    async def run():
        async with Client(server, client_info=Implementation(name='codex', version='test'),
                          elicitation_handler=accept) as client:
            args = {'source': 'document-a', 'provider': 'openai:codex'}
            assert 'PLANTED_DIALOG_DOCUMENT' not in str(await client.call_tool('document', {}))
            assert (await client.call_tool('g6_cloud_grant', args)).data['ok']
            assert (await client.call_tool('g6_cloud_grant', args)).data['ok']
            assert 'PLANTED_DIALOG_DOCUMENT' in str(await client.call_tool('document', {}))
            assert (await client.call_tool('g6_cloud_revoke', args)).data['ok']
            assert 'PLANTED_DIALOG_DOCUMENT' not in str(await client.call_tool('document', {}))
    asyncio.run(run())
    assert len(questions) == 1
    assert 'cannot be recalled' in questions[0]
    assert len(c.disclosures()) == 1


def test_unrecorded_server_notification_never_reaches_cloud_client(tmp_path, monkeypatch):
    c, _ = modules()
    import anyio
    from g6.mcp.consent_transport import _Stream
    from mcp import types
    from mcp.shared.message import SessionMessage
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    sent = []
    class Stream:
        async def send(self, packet):
            sent.append(packet)
    packet = SessionMessage(types.JSONRPCMessage(types.JSONRPCNotification(jsonrpc='2.0',
        method='notifications/message', params={'data': 'PLANTED_NOTIFICATION'})))
    async def run():
        with pytest.raises(Exception, match='G6_E_CLOUD_CONSENT_REQUIRED'):
            await _Stream(Stream(), ctx('codex').session).send(packet)
    anyio.run(run)
    assert sent == []


def test_unknown_client_still_gets_k_unsent_untested_state_without_report_data(tmp_path, monkeypatch):
    c, m = modules()
    from g6.mcp import review_loop
    from g6.harness import completion_hook
    from g6.metaharness.jobs import Jobs
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    monkeypatch.setattr(completion_hook, 'folder', lambda name: tmp_path / 'session')
    monkeypatch.setattr(review_loop, '_step', lambda *a: {'ok': False, 'action': 'stop', 'ending': 'untested',
        'error': '[G6_E_CLOUD_CONSENT_REQUIRED] no grant', 'report': 'PLANTED_SAVED_REPORT'})
    jobs = Jobs()
    started = jobs.start(lambda: review_loop.step(str(tmp_path), 'task', 'one'))
    jobs.close()
    with c.collect_sources() as capture:
        result = jobs.status(started['job_id'])
        reply = m.protect_reply(ctx('unknown'), result, c.output_sources(result), job=capture.job)
    assert reply['result']['ending'] == 'untested'
    assert reply['result']['action'] == 'stop'
    assert 'The review was not sent' in reply['result']['report']
    assert 'PLANTED' not in json.dumps(reply)


def test_k_host_configuration_path_cannot_inherit_named_repository_scope(tmp_path, monkeypatch):
    c, _ = modules()
    from pathlib import Path
    from contextlib import nullcontext
    from fastmcp import Client
    from g6.mcp import build_server, review_loop
    from g6.harness import completion_hook, completion_loop as loop
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    folder = tmp_path / 'session'
    folder.mkdir()
    monkeypatch.setattr(completion_hook, 'folder', lambda name: folder)
    monkeypatch.setattr(loop, '_held', lambda folder: nullcontext())
    monkeypatch.setattr(review_loop, '_asked', lambda *a: {'repo': str(tmp_path), 'task': 'task', 'base': 'base'})
    monkeypatch.setattr(loop.repo_tests, 'checked_repo', lambda repo: tmp_path)
    monkeypatch.setattr(loop.repo_tests, 'change', lambda *a: SimpleNamespace(repo=tmp_path, sources=('code.py',)))
    monkeypatch.setattr(loop, '_patch_now', lambda *a: 'patch')
    monkeypatch.setattr(loop, '_owe_row', lambda *a: None)
    monkeypatch.setattr(loop, '_write_row', lambda *a: None)
    monkeypatch.setattr(loop.proposer, 'boundary', lambda *a: '')
    monkeypatch.setattr(loop, '_scouts_chosen', lambda: False)
    monkeypatch.setattr(loop, 'critic_host', lambda: None)
    monkeypatch.setattr(loop, 'scout_host', lambda: None)
    monkeypatch.setattr(loop.keys, 'path', lambda: Path('PLANTED_UNSHARED_HOST_CONFIGURATION'))
    server = build_server()
    @server.tool()
    def local_review(repo: str, task: str) -> dict:
        return review_loop.step(repo, task, 'one')
    async def run():
        async with Client(server, client_info=Implementation(name='codex', version='test')) as client:
            return str(await client.call_tool('local_review', {'repo': str(tmp_path), 'task': 'task'}))
    assert 'PLANTED_UNSHARED_HOST_CONFIGURATION' not in asyncio.run(run())
