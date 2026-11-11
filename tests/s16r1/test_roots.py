# SPDX-License-Identifier: Apache-2.0
"""Repair-one roots: inventories are discovered from product Python, not line numbers."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp import Client
from mcp.types import Implementation

from g6.core import cloud_consent as cloud, consent_declarations as declarations
from g6.core.llm import LLMError
from g6.mcp import build_server
from g6.mcp.cloud_consent import request_grant

ROOT = Path(__file__).resolve().parents[2]


def source_sites(extra=()):
    """Resolve cloud-source aliases, then list each enclosing builder/normaliser."""
    found = set()
    for path in [*(ROOT / 'src/g6').rglob('*.py'), *extra]:
        module = ('.'.join(path.relative_to(ROOT / 'src').with_suffix('').parts)
                  if ROOT / 'src' in path.parents else 'new')
        tree = ast.parse(path.read_text(encoding='utf-8'))
        aliases = ({name: module + '.' + name for name in (
            'Source', 'boundary_sources', 'request_sources', 'bind_output', 'admission',
            'current_sources', 'output_sources', 'record_sources')}
                   if module == 'g6.core.cloud_consent' else {})
        if module == 'g6.harness.completion_loop':
            aliases.update({name: module + '.' + name for name in ('_scouts', '_scout')})
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                aliases.update({a.asname or a.name: a.name for a in node.names})
            if isinstance(node, ast.ImportFrom) and node.module:
                aliases.update({a.asname or a.name: node.module + '.' + a.name for a in node.names})

        def qualified(node):
            if isinstance(node, ast.Name):
                return aliases.get(node.id, node.id)
            if isinstance(node, ast.Attribute):
                return qualified(node.value) + '.' + node.attr
            return ''

        for _ in range(len(aliases) + 1):
            previous = dict(aliases)
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Name, ast.Attribute)):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and not any(
                                isinstance(n, ast.Name) and n.id == target.id for n in ast.walk(node.value)):
                            aliases[target.id] = qualified(node.value)
            if aliases == previous:
                break

        def visit(node, owners=()):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                owners = (*owners, node.name)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                    arg.arg in ('carried', 'directories', 'sources') for arg in (*node.args.args, *node.args.kwonlyargs)):
                # Scope this structural check to consent types, not other G6
                # document Source classes with independent contracts.
                if any(value.startswith('g6.core.cloud_consent.') or value == 'g6.core.cloud_consent'
                       for value in aliases.values()):
                    found.add(module + ('.' + '.'.join(owners) if owners else ''))
            if isinstance(node, ast.Call):
                name = qualified(node.func)
                if name in {'g6.core.consent_declarations.model_sources',
                            'g6.harness.completion_loop._scouts', 'g6.harness.completion_loop._scout'} or (
                    name.startswith('g6.core.cloud_consent.') and name.rsplit('.', 1)[-1] in {
                    'Source', 'text', 'boundary_sources', 'request_sources', 'bind_output',
                    'admission', 'current_sources', 'output_sources', 'record_sources',
                }):
                    found.add(module + ('.' + '.'.join(owners) if owners else ''))
            for child in ast.iter_child_nodes(node):
                visit(child, owners)
        visit(tree)
    return found


# A new source builder must get a planted case and an explicit review here.
SOURCE_SITES = {
    'g6.agents._runtime.consent.process_admission',
    'g6.core.consent_callers._Consent.complete',
    'g6.core.consent_callers._Consent.complete_with_images',
    'g6.core.consent_callers._Unknown.complete',
    'g6.core.consent_callers._Unknown.complete_with_images',
    'g6.core.consent_declarations.Declaration.sources',
    'g6.core.consent_declarations.model_sources',
    'g6.harness.completion_loop.ToolSession.run',
    'g6.harness.completion_loop._advance',
    'g6.harness.completion_loop._advance_inner',
    'g6.harness.completion_loop._advance_inner.check',
    'g6.harness.completion_loop._read',
    'g6.harness.completion_loop._scout',
    'g6.harness.completion_loop.harness',
    'g6.harness.completion_loop.prepare',
    'g6.harness.completion_loop.run_critic',
    'g6.harness.completion_loop.source_refs',
    'g6.harness.completion_loop.review_round.run',
    'g6.harness.completion_loop._scouts',
    'g6.harness.verification_proposer.Proposer._ask',
    'g6.harness.verification_proposer.context',
    'g6.mcp.cloud_consent._source_function.invoke',
    'g6.mcp.cloud_consent.protect_reply',
    'g6.mcp.consent_controls.host_controls',
    'g6.mcp.consent_controls.public_instructions',
    'g6.mcp.consent_transport._Stream.send',
    'g6.mcp.consent_transport._Stream.send.handoff',
    'g6.mcp.mission.reply',
    'g6.mcp.review_reply.finish',
    'g6.metaharness.jobs.Jobs._run',
    'g6.metaharness.jobs.Jobs._status_reply',
    'g6.metaharness.jobs.Jobs.start',
    'g6.metaharness.jobs.Jobs.status',
    'g6.mcp.review_loop.step',
    'g6.core.consent_callers.sdk_send',
    'g6.core.consent_callers._sdk_call',
    'g6.core.cloud_consent',
    'g6.core.cloud_consent.boundary_sources',
    'g6.core.cloud_consent.bind_output',
    'g6.core.cloud_consent.output_sources',
    'g6.core.cloud_consent.prompt_sources',
    'g6.core.cloud_consent.prompt_state',
    'g6.core.cloud_consent.freeze_output',
    'g6.core.cloud_consent.collect_sources',
    'g6.core.cloud_consent.request_sources',
    'g6.core.cloud_consent.admission',
    'g6.core.cloud_consent.record_sources',
    'g6.core.cloud_consent.withheld_message',
    'g6.mcp.cloud_consent.withheld_reply',
    'g6.mcp.consent_transport.prepare',
    # The Context Engine (section 12). Planted cases: tests/core/test_mcp_knowledge.py (an ungranted source, an
    # unknown client, a revoked grant, mixed sources) and tests/knowledge/test_engine_scope.py. _scope.access is here
    # for its `sources` argument, which narrows source registrations and builds no consent source.
    'g6.knowledge._engine._source',
    'g6.knowledge._engine.pack',
    'g6.knowledge._graph.traverse',
    'g6.knowledge._scope.access',
    'g6.knowledge._support.support_for',
    'g6.mcp.knowledge._declined',
    'g6.mcp.knowledge._ok',
    'g6.mcp.knowledge._refused',
    'g6.mcp.knowledge._summary_source',
    'g6.mcp.knowledge.register.g6_knowledge_context',
    'g6.mcp.knowledge.register.g6_knowledge_count',
    'g6.mcp.knowledge.register.g6_knowledge_search',
}


def test_every_source_builder_is_inventoried():
    assert source_sites() == SOURCE_SITES


def test_inventory_detects_a_new_alias_builder(tmp_path):
    path = tmp_path / 'fresh.py'
    path.write_text('from g6.core import cloud_consent as c\nselect = c.current_sources\n'
                    'def shrink():\n    return select()[:1]\n')
    assert source_sites((path,)) - SOURCE_SITES == {'new.shrink'}


def test_inventory_detects_a_scout_entry_without_source_annotations(tmp_path):
    path = tmp_path / 'fresh.py'
    path.write_text('from g6.harness.completion_loop import _scouts as enter\n'
                    'def unrecorded_entry():\n    return enter(private_context)\n')
    assert source_sites((path,)) - SOURCE_SITES == {'new.unrecorded_entry'}


def file_access_sites(extra=()):
    """All workspace handoffs and explicit directory grants in coding-process code."""
    paths = [*(ROOT / 'src/g6/agents').rglob('*.py'), ROOT / 'src/g6/harness/completion_loop.py', *extra]
    rows = set()
    for path in paths:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        def visit(node, owners=()):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                owners = (*owners, node.name)
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg == 'cwd':
                        rows.add(('.'.join(owners), 'cwd', ast.unparse(keyword.value)))
            if isinstance(node, (ast.List, ast.Tuple)):
                for i, child in enumerate(node.elts[:-1]):
                    if isinstance(child, ast.Constant) and child.value in ('--add-dir', '--cd', '--directory', '-C'):
                        rows.add(('.'.join(owners), child.value, ast.unparse(node.elts[i + 1])))
            for child in ast.iter_child_nodes(node):
                visit(child, owners)
        visit(tree)
    return rows


FILE_ACCESS = {
    ('consent_run', 'cwd', 'cwd'),
    ('CliAgentRunner.require.version', 'cwd', 'scratch'),
    ('CliAgentRunner.run', 'cwd', 'cwd'),
    ('ClaudeCodeRuntime.run', 'cwd', 'workspace'),
    ('CodexRuntime.run', 'cwd', 'workspace'),
    ('ToolSession.run', '--add-dir', 'host_models._plain(where)'),
}


def test_process_file_access_arguments_have_an_admission_source(tmp_path):
    assert file_access_sites() == FILE_ACCESS
    fresh = tmp_path / 'extra.py'
    fresh.write_text("def widened():\n    return ['--add-dir', private_directory]\n")
    assert file_access_sites((fresh,)) - FILE_ACCESS == {('widened', '--add-dir', 'private_directory')}


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))


def grant(scope, provider='openai:codex'):
    class Dialog:
        async def elicit_form(self, **kwargs):
            return SimpleNamespace(action='accept', content={'value': 'Repair test person'})
    assert asyncio.run(request_grant(SimpleNamespace(session=Dialog(), request_id='grant'),
                                     scope, provider))['status'] == 'granted'


@pytest.mark.parametrize('unknown', [False, True])
def test_one_join_keeps_carried_sources_and_directory_access(tmp_path, unknown):
    join = getattr(cloud, 'boundary_sources', None)
    assert join is not None, 'boundaries have no common additive source builder'
    carried = cloud.UNKNOWN if unknown else cloud.Source.text('document:revoked', 'passage', 'PRIVATE')
    declared = cloud.Source.text('model_inputs', 'producer', '')
    grant('model_inputs')
    cloud.revoke(carried.source_id, 'openai:codex')
    with cloud.request_sources((carried,)):
        refs = join((declared,), directories=(tmp_path,))
    assert carried in refs and declared in refs
    assert cloud.repository_source(tmp_path) in {s.source_id for s in refs}
    with pytest.raises(LLMError, match=cloud.REQUIRED):
        with cloud.admission('openai:codex', refs, cloud.sha256('PRIVATE')):
            pytest.fail('revoked or unknown carried content was admitted')


@pytest.mark.parametrize('builder', ['model', 'request', 'bind', 'new-binding', 'controls', 'retained', 'checkpoint'])
def test_every_source_transform_preserves_explicit_unknown(builder):
    from dataclasses import asdict
    from g6.harness.completion_loop import source_refs
    from g6.mcp.consent_controls import host_controls
    from g6.metaharness.jobs import Jobs
    with cloud.collect_sources(), cloud.request_sources((cloud.UNKNOWN,)):
        if builder == 'model':
            refs = declarations.model_sources()
        elif builder == 'request':
            with cloud.request_sources((cloud.Source.text('model_inputs', 'x', ''),)):
                refs = cloud.current_sources()
        elif builder == 'bind':
            value = {}
            cloud.bind_output(value, (cloud.UNKNOWN,))
            cloud.bind_output(value, (cloud.Source.text('model_inputs', 'x', ''),))
            refs = cloud.output_sources(value)
        elif builder == 'new-binding':
            value = cloud.bind_output({}, (cloud.Source.text('model_inputs', 'x', ''),))
            refs = cloud.output_sources(value)
        elif builder == 'controls':
            value = cloud.bind_output({}, (cloud.UNKNOWN,))
            refs = cloud.output_sources(host_controls(value, {}, sources=()))
        elif builder == 'checkpoint':
            refs = source_refs({'consent_sources': [asdict(cloud.UNKNOWN)]})
        else:
            jobs = Jobs()
            try:
                started = jobs.start(lambda: cloud.bind_output({'text': 'PRIVATE'}, (cloud.UNKNOWN,)),
                                     compact=lambda value, ident: cloud.bind_output({'text': value['text']}, ()))
                jobs.close()
                refs = cloud.output_sources(jobs.status(started['job_id']))
            finally:
                jobs.close()
        assert cloud.UNKNOWN in refs, f'{builder} erased explicit unknown provenance'


@pytest.mark.parametrize('unknown', [False, True])
@pytest.mark.parametrize('boundary', ['model', 'images', 'sdk', 'process', 'scout', 'critic', 'proposer', 'mcp'])
def test_carried_source_is_planted_on_every_content_boundary(tmp_path, monkeypatch, boundary, unknown):
    import time
    from g6.agents._runtime.consent import consent_run
    from g6.agents._runtime.model import AgentRefusal
    from g6.core import consent_callers as callers, host_models
    from g6.harness import completion_loop as loop, verification_proposer as proposer
    marker = 'PRIVATE_CARRIED_BOUNDARY'
    carried = cloud.UNKNOWN if unknown else cloud.Source.text('document:revoked', 'passage', marker)
    sent = []
    for provider in ('openai:codex', 'anthropic:claude_code', 'anthropic:api'):
        for scope in ('model_inputs', 'agent_environment', 'declared', cloud.repository_source(tmp_path),
                      cloud.input_source('task')):
            grant(scope, provider)
        cloud.revoke(carried.source_id, provider)
    monkeypatch.setattr(host_models.CodexCaller, 'complete',
                        lambda self, system, user, **kwargs: sent.append(user) or 'fake reply')
    monkeypatch.setattr(host_models.CodexCaller, 'complete_with_images',
                        lambda self, system, user, images, **kwargs: sent.append(user) or 'fake reply', raising=False)
    with cloud.request_sources((carried,)):
        try:
            if boundary == 'model':
                callers.guard(host_models.CodexCaller()).complete('', marker)
            elif boundary == 'images':
                callers.guard(host_models.CodexCaller()).complete_with_images('', marker, [])
            elif boundary == 'sdk':
                class SDK(callers.SDKClient):
                    base_url = 'https://api.anthropic.com'
                with callers.sdk_send(SDK(), {'prompt': marker}):
                    sent.append(marker)
            elif boundary == 'process':
                consent_run(SimpleNamespace(run=lambda *args, **kw: sent.append(kw['prompt'])), ['fake'],
                            provider='openai:codex', prompt=marker, cwd=tmp_path)
            elif boundary == 'scout':
                from contextlib import contextmanager
                files = tmp_path / 'files'
                files.mkdir()
                @contextmanager
                def folders():
                    yield tmp_path, files
                monkeypatch.setattr(host_models, '_folders', folders)
                monkeypatch.setattr(host_models, '_environment', lambda *args: {})
                monkeypatch.setattr(loop.ToolSession, '_reply', lambda *args: 'fake reply')
                monkeypatch.setattr(loop.ToolSession, 'locate', lambda self: 'fake')
                monkeypatch.setattr(host_models, '_run', lambda argv, text, *args: sent.append(text) or (0, '{}', ''))
                # No host scratch allocation is needed when admission refuses.
                loop.ToolSession().run(marker, tmp_path, tmp_path, 1)
            elif boundary == 'critic':
                monkeypatch.setattr(loop, '_pack', lambda *args: marker)
                monkeypatch.setattr(loop, '_masked_pack', lambda *args: (marker, False))
                loop.run_critic(SimpleNamespace(repo=tmp_path, sources=('code.py',)), 'task',
                                host_models.CodexCaller(), '', tmp_path, patch='patch', system_sources=())
            elif boundary == 'proposer':
                obj = object.__new__(proposer.Proposer)
                obj.ready = obj.stopped = ''
                obj.deadline, obj.room, obj.stage = time.monotonic() + 100, 1, ['']
                obj.model, obj.found = 'fake', SimpleNamespace(activities=[])
                obj.context = proposer.RepositoryContext(marker, (), (), ())
                obj.limits = SimpleNamespace(calls=[])
                def send(*args, **kwargs):
                    callers.guard(host_models.CodexCaller()).complete('', marker)
                    raise LLMError('synthetic end')
                monkeypatch.setattr(proposer, 'complete_structured', send)
                obj._ask('test', 'task', {}, object)
            else:
                server = build_server()
                @server.tool()
                @declarations.declares('source:declared')
                def retained() -> dict:
                    return cloud.bind_output({'text': marker}, (carried,))
                async def read():
                    async with Client(server, client_info=Implementation(name='codex', version='repair')) as client:
                        return await client.call_tool('retained', {})
                reply = asyncio.run(read())
                if marker in str(reply):
                    sent.append(marker)
        except (LLMError, AgentRefusal):
            pass
    assert sent == [], (boundary, unknown)


@pytest.mark.parametrize('missing', ['cwd', 'where', 'model_inputs', 'agent_environment'])
def test_scout_same_run_without_each_grant_starts_no_process(tmp_path, monkeypatch, missing):
    from contextlib import contextmanager
    from g6.core import host_models
    from g6.harness.completion_loop import ToolSession
    cwd, where = tmp_path / 'repository', tmp_path / 'additional'
    cwd.mkdir()
    where.mkdir()
    scopes = {'cwd': cloud.repository_source(cwd), 'where': cloud.repository_source(where),
              'model_inputs': 'model_inputs', 'agent_environment': 'agent_environment'}
    for name, scope in scopes.items():
        if name != missing:
            grant(scope, 'anthropic:claude_code')
    sent = []
    files = tmp_path / 'files'
    files.mkdir()
    @contextmanager
    def folders():
        yield tmp_path, files
    monkeypatch.setattr(host_models, '_folders', folders)
    monkeypatch.setattr(host_models, '_environment', lambda *args: {})
    monkeypatch.setattr(ToolSession, 'locate', lambda self: 'fake')
    monkeypatch.setattr(ToolSession, '_reply', lambda *args: 'fake reply')
    monkeypatch.setattr(host_models, '_run', lambda *args: sent.append('PRIVATE_SCOUT_READ') or (0, '{}', ''))
    with pytest.raises(LLMError, match=cloud.REQUIRED):
        ToolSession().run('synthetic scout request', cwd, where, 1)
    assert sent == []


def test_workspace_and_environment_do_not_attribute_an_unscoped_prompt(tmp_path):
    from g6.agents._runtime.consent import consent_run
    from g6.agents._runtime.model import AgentRefusal
    sent = []
    for scope in (cloud.repository_source(tmp_path), 'agent_environment'):
        grant(scope)
    runner = SimpleNamespace(run=lambda *args, **kwargs: sent.append(kwargs['prompt']))
    with pytest.raises(AgentRefusal, match=cloud.REQUIRED):
        consent_run(runner, ['fake'], provider='openai:codex', prompt='PRIVATE_UNSCOPED_PROMPT', cwd=tmp_path)
    assert sent == []
    grant('document:prompt')
    with cloud.request_sources((cloud.Source.text('document:prompt', 'passage', 'PRIVATE_UNSCOPED_PROMPT'),)):
        consent_run(runner, ['fake'], provider='openai:codex', prompt='PRIVATE_UNSCOPED_PROMPT', cwd=tmp_path)
    assert sent == ['PRIVATE_UNSCOPED_PROMPT']


@pytest.mark.parametrize('unknown', [False, True])
@pytest.mark.parametrize('job', [False, True])
def test_sources_recorded_at_entry_travel_through_declared_results(monkeypatch, unknown, job):
    import json
    marker = 'PRIVATE_ENTRY_SOURCE'
    source = cloud.UNKNOWN if unknown else cloud.Source.text('document:entry', 'file', marker)
    cloud.revoke(source.source_id, 'openai:codex')
    grant('declared')
    grant('workflows')
    grant('jobs')
    initial = {'workflow': 'fake', 'task_input': 'literal'}
    grant(cloud.input_source(json.dumps(initial, sort_keys=True)))
    monkeypatch.setenv('G6_MCP_SENSITIVE', 'metaharness.run')
    def read():
        cloud.record_sources((source,))
        return {'text': marker}
    class Surface:
        def run(self, *args):
            return read()
    server = build_server(Surface())
    @server.tool()
    @declarations.declares('source:declared')
    def entry() -> dict:
        return read()
    async def request():
        async with Client(server, client_info=Implementation(name='codex', version='repair')) as client:
            if not job:
                return await client.call_tool('entry', {})
            started = await client.call_tool('metaharness_run', initial)
            for _ in range(200):
                reply = await client.call_tool('metaharness_job', {'job_id': started.data['job_id'], 'detail': 'full'})
                if reply.data['status'] not in ('pending', 'running'):
                    return reply
                await asyncio.sleep(.005)
        raise AssertionError('synthetic job did not finish')
    assert marker not in str(asyncio.run(request()))


def test_scout_retry_carries_unknown_child_diagnostics(tmp_path, monkeypatch):
    import time
    from contextlib import contextmanager
    from g6.core import host_models
    from g6.harness import completion_loop as loop
    cwd, where, files = (tmp_path / name for name in ('repository', 'scout', 'files'))
    for folder in (cwd, where, files):
        folder.mkdir()
    for scope in (cloud.repository_source(cwd), cloud.repository_source(where), 'model_inputs', 'agent_environment'):
        grant(scope, 'anthropic:claude_code')
    @contextmanager
    def base(*args):
        yield cwd
    @contextmanager
    def scratch():
        yield tmp_path, files
    marker, sent = 'PRIVATE_UNKNOWN_CHILD_DIAGNOSTIC', []
    monkeypatch.setattr(loop, '_base_copy', base)
    monkeypatch.setattr(loop, 'run_set', lambda *a, **kw: loop.ProbeRun(1, {'test_probe': 'failed'}, {'test_probe': marker}))
    monkeypatch.setattr(loop, '_gate', lambda *args: ['needs more cases'])
    monkeypatch.setattr(loop, '_checks', lambda *args: ([], {}, []))
    monkeypatch.setattr(host_models, '_folders', scratch)
    monkeypatch.setattr(host_models, '_environment', lambda *args: {})
    monkeypatch.setattr(loop.ToolSession, 'locate', lambda self: 'fake')
    monkeypatch.setattr(loop.ToolSession, '_reply', lambda *args: 'fake reply')
    monkeypatch.setattr(host_models, '_run', lambda argv, text, *args: sent.append(text) or (0, '{}', ''))
    loop._scout(SimpleNamespace(repo=cwd), 'task', '{title} {text}', where, loop.ToolSession().run,
                time.monotonic() + 10000)
    assert len(sent) == 1
    assert marker not in str(sent)


def tool_arguments():
    async def collect():
        return {name: set(tool.parameters.get('properties', {}))
                for name, tool in (await build_server().get_tools()).items()}
    return asyncio.run(collect())


def test_all_mcp_arguments_classified_from_schema():
    kinds = getattr(declarations, 'TOOL_ARGUMENTS', {})
    actual = tool_arguments()
    assert actual and actual.keys() == kinds.keys()
    for name, arguments in actual.items():
        assert arguments == kinds[name].keys(), name
        assert set(kinds[name].values()) <= {'content', 'pointer'}, name


def test_supplied_pointer_and_mismatched_input_bytes_cannot_grant(tmp_path):
    for source in (cloud.Source.text(cloud.repository_source(tmp_path), 'file', 'PRIVATE_NAME'),
                   cloud.Source.text(cloud.input_source('path'), 'file', 'PRIVATE_NAME')):
        with pytest.raises(LLMError, match=cloud.REQUIRED):
            with cloud.admission('openai:codex', (source,), cloud.sha256('PRIVATE_NAME'),
                                 supplied=(source.source_id,)):
                pytest.fail('a pointer became consent for its referent')
    literal = cloud.Source.text(cloud.input_source('path'), 'argument', 'path')
    with cloud.admission('openai:codex', (literal,), cloud.sha256('path'), supplied=(literal.source_id,)):
        pass


def test_retained_literal_is_not_supplied_again_by_polling_a_handle():
    from g6.metaharness.jobs import Jobs
    from g6.mcp.cloud_consent import protect_reply
    session = SimpleNamespace(client_params=SimpleNamespace(clientInfo=SimpleNamespace(name='codex')))
    literal = cloud.Source.text(cloud.input_source('PRIVATE_EARLIER_ARGUMENT'), 'argument', 'PRIVATE_EARLIER_ARGUMENT')
    jobs = Jobs()
    try:
        with cloud.collect_sources() as capture:
            capture.client = 'openai:codex', session
            capture.supplied.add(literal.source_id)
            started = jobs.start(lambda: cloud.bind_output({'text': 'PRIVATE_EARLIER_ARGUMENT'}, (literal,)))
        jobs.close()
        with cloud.collect_sources() as capture:
            capture.client = 'openai:codex', session
            reply = jobs.status(started['job_id'])
            refused = protect_reply(SimpleNamespace(session=session), reply, cloud.output_sources(reply),
                                    supplied=capture.supplied)
            assert 'PRIVATE_EARLIER_ARGUMENT' not in str(refused)
            assert literal.source_id not in capture.supplied
        fresh = Jobs()
        try:
            assert fresh.status(started['job_id'])['ok'] is False
        finally:
            fresh.close()
    finally:
        jobs.close()


def test_sdk_enclosing_admission_cannot_hide_new_carried_sources():
    from g6.core import consent_callers as callers, llm
    sent = []
    class SDK(callers.SDKClient):
        base_url = 'https://api.anthropic.com'
    sdk = SDK()
    grant('model_inputs', 'anthropic:api')
    cloud.revoke('document:revoked', 'anthropic:api')
    carried = cloud.Source.text('document:revoked', 'late-passage', 'PRIVATE_LATE_SDK_SOURCE')
    with (cloud.admission('anthropic:api', declarations.model_sources(), cloud.sha256('original')),
          callers._sdk_call(sdk, 'anthropic:api')):
        with cloud.request_sources((carried,)):
            with pytest.raises(llm.LLMError, match=cloud.REQUIRED):
                with callers.sdk_send(sdk, {'prompt': 'PRIVATE_LATE_SDK_SOURCE'}):
                    sent.append('PRIVATE_LATE_SDK_SOURCE')
    assert sent == []


def test_local_sdk_can_use_new_unknown_sources():
    from g6.core import consent_callers as callers
    class SDK(callers.SDKClient):
        base_url = 'http://127.0.0.1:11434'
    sdk, sent = SDK(), []
    with callers._sdk_call(sdk, 'local:loopback'), cloud.request_sources((cloud.UNKNOWN,)):
        with callers.sdk_send(sdk, {'prompt': 'LOCAL_ONLY'}):
            sent.append('LOCAL_ONLY')
    assert sent == ['LOCAL_ONLY']


def test_operational_declarations_match_package_only_producers():
    operational = {name for name, contract in declarations.TOOLS.items() if contract.classes == ('g6_operational',)}
    assert operational == {'g6_help', 'g6_cloud_grant', 'g6_cloud_revoke'}
    # Read the actual producer bodies: fixed HELP, dialog adapter, ledger revocation.
    bodies = {}
    for rel in ('src/g6/mcp/__init__.py', 'src/g6/mcp/cloud_consent.py'):
        for node in ast.walk(ast.parse((ROOT / rel).read_text(encoding='utf-8'))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in operational:
                bodies[node.name] = ast.unparse(node)
    assert bodies.keys() == operational
    assert 'discovery.HELP' in bodies['g6_help']
    assert 'request_grant' in bodies['g6_cloud_grant']
    assert 'revoke' in bodies['g6_cloud_revoke']
    assert 'source:component_catalogue' in declarations.TOOLS['g6_components'].classes


def operational_factories():
    rows = set()
    for path in (ROOT / 'src/g6').rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        aliases = {a.asname or a.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                   for a in node.names if a.name == 'public_instructions'}
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            public = isinstance(node.func, ast.Name) and node.func.id in aliases
            empty = (isinstance(node.func, ast.Attribute) and node.func.attr == 'bind_output'
                     and len(node.args) > 1 and isinstance(node.args[1], (ast.Tuple, ast.List, ast.Set))
                     and not node.args[1].elts)
            if not (public or empty):
                continue
            current, owners = node, []
            while current in parents:
                current = parents[current]
                if isinstance(current, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    owners.append(current.name)
            rows.add('.'.join(path.relative_to(ROOT / 'src').with_suffix('').parts) + '.' + '.'.join(reversed(owners)))
    return rows


OPERATIONAL_FACTORIES = {
    'g6.mcp.__init__.invoke_reply',
    'g6.mcp.__init__._metaharness_tools.g6_review_patch',
    'g6.mcp.__init__._metaharness_tools.g6_review_until_clean',
    'g6.mcp.__init__._metaharness_tools.runs_code',
    'g6.mcp.cloud_consent.register.reply',
    'g6.mcp.consent_controls.public_instructions',
    'g6.mcp.improve.refused',
    'g6.mcp.mission.refused',
    'g6.mcp.review_loop.step',
    'g6.metaharness.jobs.Jobs.start',
    'g6.metaharness.jobs.Jobs.status',
    # The Context Engine's two replies that carry no source content: a refusal with a fixed reason, and a dialog the
    # person did not accept.
    'g6.mcp.knowledge._declined',
    'g6.mcp.knowledge._refused',
}


def test_operational_control_factories_have_a_closed_inventory():
    assert operational_factories() == OPERATIONAL_FACTORIES


@pytest.mark.parametrize('granted', [False, True])
def test_extension_catalogue_requires_its_own_dialog_grant(monkeypatch, registry_reset, granted):
    from g6.core import registry
    marker = 'PRIVATE_INSTALLED_EXTENSION_NAME'
    monkeypatch.setattr(registry.metadata, 'entry_points', lambda **kwargs: [SimpleNamespace(name=marker, value='fake')])
    if granted:
        grant('component_catalogue')
    async def listing():
        async with Client(build_server(), client_info=Implementation(name='codex', version='repair')) as client:
            return await client.call_tool('g6_components', {})
    assert (marker in str(asyncio.run(listing()))) is granted


def test_every_pointer_is_denied_at_mcp_egress(monkeypatch, tmp_path):
    """Plant a private filename for each schema-classified pointer before rendering."""
    from g6.mcp.cloud_consent import _source_function
    kinds = getattr(declarations, 'TOOL_ARGUMENTS', {})
    assert kinds, 'no argument classification inventory'
    pointers = [(tool, arg) for tool, args in kinds.items() for arg, kind in args.items() if kind == 'pointer']
    assert pointers
    for tool, argument in pointers:
        server = build_server()
        marker = f'PRIVATE_FILENAME_{tool}_{argument}'
        scope = cloud.repository_source(tmp_path / f'{tool}_{argument}')
        # Synthetic producer models the location read by this classified argument.
        def pointed(value: str) -> dict:
            return cloud.bind_output({'text': marker}, (cloud.Source.text(scope, 'private-file', marker),))
        pointed.__name__ = tool
        wrapped = _source_function(pointed)
        server.tool(name='pointer_probe')(wrapped)

        async def read():
            async with Client(server, client_info=Implementation(name='codex', version='repair')) as client:
                return await client.call_tool('pointer_probe', {'value': scope})
        assert marker not in str(asyncio.run(read())), (tool, argument)
