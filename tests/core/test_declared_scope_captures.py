# SPDX-License-Identifier: Apache-2.0
"""Capture declared scopes at actual MCP reply and client-dialog boundaries."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastmcp import Client
from mcp.types import Implementation

from g6.core.consent_declarations import TOOLS
from g6.mcp import build_server, improve, mission, review_loop, review_reply


# An independent scope oracle: positive captures must not grant whatever a
# potentially wrong declaration happens to request.
EXPECTED_SCOPES = {
    'g6_components': ('component_catalogue',),
    **{n: ('component_catalogue',) for n in ('g6_find', 'g6_describe')},
    **{n: ('component_data',) for n in ('g6_invoke', 'g6_compose')},
    'g6_review_patch': ('host_configuration',),
    'g6_review_until_clean': ('host_configuration', 'completion_history'),
    'g6_mission_submit': ('missions', 'host_configuration'),
    **{n: ('missions',) for n in ('g6_mission_create', 'g6_mission_clarify', 'g6_mission_confirm',
                                 'g6_mission_status', 'g6_mission_certificate')},
    **{n: ('improvement',) for n in ('g6_improve_start', 'g6_improve_stop', 'g6_improve_status')},
    **{n: ('workflows',) for n in ('metaharness_start', 'metaharness_confirm', 'metaharness_propose_code',
        'metaharness_evaluate_start', 'metaharness_run', 'metaharness_inspect', 'metaharness_propose',
        'metaharness_evaluate', 'metaharness_decide', 'metaharness_diagnose', 'metaharness_approve', 'metaharness_rollback')},
    'metaharness_job': ('jobs',),
    'g6_cloud_grants': ('consent_metadata',),
    'g6_disclosures': ('consent_metadata',),
}
SOURCE_TOOLS = list(EXPECTED_SCOPES)
ASYNC_TOOLS = {'g6_review_patch', 'g6_review_until_clean', 'g6_mission_create', 'g6_mission_clarify',
               'g6_mission_confirm', 'g6_mission_submit', 'metaharness_evaluate_start', 'metaharness_run'}


@pytest.mark.parametrize('name', SOURCE_TOOLS)
@pytest.mark.parametrize('granted', [False, True])
def test_each_declared_tool_path_captures_retained_content(tmp_path, monkeypatch, accepted_cloud_dialog,
                                                         name, granted, planted_scope=None):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('G6_MCP_SENSITIVE', 'metaharness,workforce,patch_review')
    planted = 'PLANTED_' + name.upper()
    scope_root = tmp_path / (planted_scope or EXPECTED_SCOPES[name][0])
    scope_root.mkdir()
    stored = scope_root / 'unshared.txt'
    stored.write_text(planted, encoding='utf-8')

    def result(*a, **kw):
        return {'ok': True, 'result': stored.read_text(encoding='utf-8')}

    class Surface:
        def __getattr__(self, method):
            return result

        def harness(self, *a):
            return SimpleNamespace(workflow=SimpleNamespace(digest=lambda: 'digest', task=planted,
                suite=SimpleNamespace(dev=[], held_out=[])))

        def approval_check(self, *a, **kw):
            return None, planted

        def rollback_check(self, *a, **kw):
            return None, planted, {}

    for module, methods in (
        (mission, ('create', 'clarify', 'confirm', 'submit', 'status', 'certificate')),
        (improve, ('start', 'stop', 'status')),
        (review_loop, ('step',)), (review_reply, ('review',))):
        for method in methods:
            monkeypatch.setattr(module, method, result)
    monkeypatch.setattr(mission, 'refused', lambda: None)
    monkeypatch.setattr(improve, 'refused', lambda: None)
    monkeypatch.setattr(mission, 'execution', lambda *a: {'name': 'fixture', 'lines': [], 'question': planted})
    monkeypatch.setattr(mission, 'asked', lambda *a: ([], 'digest', planted))
    monkeypatch.setattr(improve, 'asked', lambda *a: (tmp_path, planted, 'digest'))
    import g6.mcp
    monkeypatch.setattr(g6.mcp, 'invoke_reply', result)
    monkeypatch.setattr(g6.mcp, 'describe_reply', result)
    from g6.core import discovery
    monkeypatch.setattr(discovery, 'find', lambda *a: [{'component': stored.read_text(encoding='utf-8')}])
    if name == 'g6_components':
        from g6.core import registry
        monkeypatch.setattr(registry, 'get_registry', lambda: SimpleNamespace(
            names=lambda: [stored.read_text(encoding='utf-8')]))
    from g6.mcp import composition
    monkeypatch.setattr(composition, 'compose_reply', result)
    server = build_server(Surface())
    if name in ('g6_cloud_grants', 'g6_disclosures'):
        from g6.core import cloud_consent as cloud
        refs = accepted_cloud_dialog(planted, 'anthropic:api', text=planted)
        with cloud.admission('anthropic:api', refs, cloud.sha256(planted)):
            pass  # A fake recipient handoff records an attempted disclosure.
    assert {c[7:] for c in TOOLS[name].classes if c.startswith('source:')} == set(EXPECTED_SCOPES[name])
    if granted:
        for scope in set(EXPECTED_SCOPES[name]) | ({'jobs'} if name in ASYNC_TOOLS else set()):
            accepted_cloud_dialog(scope, 'openai:codex', text=planted)
        if name in ('g6_review_patch', 'g6_review_until_clean', 'g6_mission_create', 'g6_mission_submit'):
            from g6.core.cloud_consent import repository_source
            accepted_cloud_dialog(repository_source(tmp_path), 'openai:codex', text='synthetic repository')
        if name in ('g6_cloud_grants', 'g6_disclosures'):
            accepted_cloud_dialog(planted, 'openai:codex', text=planted)
        if name == 'metaharness_job':
            accepted_cloud_dialog('workflows', 'openai:codex', text=planted)
    arguments = {'repo': str(tmp_path), 'task': 'task', 'session': 'session', 'objective': 'objective',
        'mission_id': 'mission', 'replay_semantics': 'semantics', 'folder': str(tmp_path),
        'workflow': 'workflow', 'workflow_digest': 'digest', 'proposal_digest': 'digest', 'report_digest': 'digest',
        'decision_digest': 'digest', 'version': 0, 'specification_json': '{}', 'files_json': '{}',
        'hypothesis': 'hypothesis', 'component': 'component', 'op': 'op', 'composition_json': '{}', 'task_input': 'task'}
    arguments['query'] = 'query'
    questions = []

    async def accept(message, response_type, params, context):
        questions.append(message)
        return response_type(value='Fixture person')

    async def run():
        tools = await server.get_tools()
        if granted and (name in ASYNC_TOOLS or name == 'metaharness_job'):
            from g6.core.cloud_consent import input_source
            from g6.mcp.cloud_consent import request_grant
            async def dialog(**kwargs):
                return SimpleNamespace(action='accept', content={'value': 'Fixture person'})
            context = SimpleNamespace(session=SimpleNamespace(elicit_form=dialog), request_id='grant')
            selected = ({'workflow': 'workflow', 'task_input': 'task'} if name == 'metaharness_job' else
                        {key: arguments[key] for key in tools[name].parameters.get('required', [])})
            assert (await request_grant(context, input_source(json.dumps(selected, sort_keys=True, default=str)),
                                        'openai:codex'))['status'] == 'granted'
        async with Client(server, client_info=Implementation(name='codex', version='test'),
                          elicitation_handler=accept) as client:
            if name == 'metaharness_job':
                made = await client.call_tool('metaharness_run', {'workflow': 'workflow', 'task_input': 'task'})
                arguments['job_id'] = made.data['job_id']
            required = tools[name].parameters.get('required', [])
            args = {key: arguments[key] for key in required}
            received = await client.call_tool(name, args)
            value = received.data
            if isinstance(value, dict) and 'job_id' in value:
                for _ in range(200):
                    received = await client.call_tool('metaharness_job', {'job_id': value['job_id'], 'detail': 'full'})
                    if received.data.get('status') not in ('pending', 'running'):
                        break
                    await asyncio.sleep(.005)
            return str(received)

    captured = asyncio.run(run())
    if granted:
        assert planted in captured or planted in json.dumps(questions), (name, captured)
    else:
        assert planted not in captured and planted not in json.dumps(questions)
        assert str(stored) not in captured


@pytest.mark.parametrize('name,scope', [('g6_review_until_clean', 'completion_history'),
                                       ('g6_mission_submit', 'host_configuration')])
@pytest.mark.parametrize('granted', [False, True])
def test_secondary_declared_scopes_are_captured_independently(tmp_path, monkeypatch, accepted_cloud_dialog,
                                                           name, scope, granted):
    test_each_declared_tool_path_captures_retained_content(tmp_path, monkeypatch, accepted_cloud_dialog,
                                                         name, granted, planted_scope=scope)


@pytest.mark.parametrize('name,args', [('g6_help', {}), ('g6_components', {}),
    ('g6_cloud_revoke', {'source': 'fixture', 'provider': 'openai:codex'}),
    ('g6_cloud_grant', {'source': 'fixture', 'provider': 'openai:codex'})])
def test_request_and_operational_tools_never_read_other_scopes(tmp_path, monkeypatch, name, args):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    for scope in {c[7:] for d in TOOLS.values() for c in d.classes if c.startswith('source:')} | {
            'agent_environment', 'model_inputs'}:
        folder = tmp_path / 'home' / scope
        folder.mkdir(parents=True)
        (folder / 'private.txt').write_text('PLANTED_OTHER_SCOPE_' + scope, encoding='utf-8')
    async def accept(message, response_type, params, context):
        return response_type(value='Fixture person')
    async def run():
        if name == 'g6_components':
            from g6.mcp.cloud_consent import request_grant
            async def dialog(**kwargs):
                return SimpleNamespace(action='accept', content={'value': 'Fixture person'})
            context = SimpleNamespace(session=SimpleNamespace(elicit_form=dialog), request_id='grant')
            assert (await request_grant(context, 'component_catalogue', 'openai:codex'))['status'] == 'granted'
        async with Client(build_server(), client_info=Implementation(name='codex', version='test'),
                          elicitation_handler=accept) as client:
            return str(await client.call_tool(name, args))
    received = asyncio.run(run())
    assert 'PLANTED_OTHER_SCOPE' not in received
    assert 'G6_E_CLOUD_CONSENT_REQUIRED' not in received


@pytest.mark.parametrize('granted', [False, True])
def test_actual_catalogue_file_is_not_operational_text(tmp_path, monkeypatch, accepted_cloud_dialog, granted):
    from g6.core import discovery
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    path = tmp_path / 'skill.txt'
    path.write_text('PLANTED_CATALOGUE_FILE', encoding='utf-8')
    monkeypatch.setattr(discovery, 'skill_text', lambda *a: path.read_text(encoding='utf-8'))
    if granted:
        accepted_cloud_dialog('component_catalogue', 'openai:codex', text='PLANTED_CATALOGUE_FILE')
    async def run():
        async with Client(build_server(), client_info=Implementation(name='codex', version='test')) as client:
            return str(await client.call_tool('g6_describe', {'component': 'algorithm_design'}))
    assert ('PLANTED_CATALOGUE_FILE' in asyncio.run(run())) is granted


def test_repository_only_premortem_does_not_read_retained_scopes(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    repo = tmp_path / 'repository'
    repo.mkdir()
    for scope in {c[7:] for d in TOOLS.values() for c in d.classes if c.startswith('source:')} | {
            'agent_environment', 'model_inputs'}:
        folder = tmp_path / 'home' / scope
        folder.mkdir(parents=True)
        (folder / 'private.txt').write_text('PLANTED_PREMORTEM_OTHER_SCOPE', encoding='utf-8')
    async def run():
        async with Client(build_server(), client_info=Implementation(name='codex', version='test')) as client:
            return str(await client.call_tool('g6_premortem', {'repo': str(repo), 'task': 'client task'}))
    assert 'PLANTED_PREMORTEM_OTHER_SCOPE' not in asyncio.run(run())
