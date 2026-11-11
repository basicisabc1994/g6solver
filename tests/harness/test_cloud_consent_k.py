# SPDX-License-Identifier: Apache-2.0
"""K's reviewer must not receive a repository without a repository/provider grant."""
from types import SimpleNamespace
import asyncio
import importlib

import pytest

from g6.core import host_models
from g6.harness import completion_loop as loop


@pytest.mark.parametrize('granted_scope', [None, 'repository', 'model_inputs'])
def test_scout_session_without_all_grants_never_reaches_the_provider(tmp_path, monkeypatch,
                                                                   accepted_cloud_dialog, granted_scope):
    from g6.core import cloud_consent as cloud
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    program = tmp_path / 'claude.exe'
    program.write_text('', encoding='utf-8')
    monkeypatch.setenv('G6_CLAUDE', str(program))
    where = tmp_path / 'work'
    where.mkdir()
    marker = 'PLANTED_SCOUT_INPUT'
    (tmp_path / 'code.py').write_text('PLANTED_SCOUT_REPOSITORY', encoding='utf-8')
    sent = []
    monkeypatch.setattr(host_models, '_run', lambda *args: sent.append(args) or (0, '{}', ''))
    if granted_scope:
        scope = cloud.repository_source(tmp_path) if granted_scope == 'repository' else 'model_inputs'
        accepted_cloud_dialog(scope, 'anthropic:claude_code', text=marker)
    with pytest.raises(cloud.LLMError, match=cloud.REQUIRED):
        loop.ToolSession('a-model').run(marker, tmp_path, where, 30.0)
    assert not sent


def test_ungranted_k_patch_is_not_sent(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    sent = []
    monkeypatch.setattr(host_models.CodexCaller, 'complete', lambda self, system, user, **kw: sent.append(user) or 'VERDICT: none')
    monkeypatch.setattr(loop, '_pack', lambda *a: 'PLANTED_PATCH')
    monkeypatch.setattr(loop, '_masked_pack', lambda *a: ('PLANTED_PATCH', False))
    change = SimpleNamespace(repo=tmp_path, sources=('code.py',))
    found = loop.run_critic(change, 'task', host_models.CodexCaller(), 'instructions', tmp_path / 'review', patch='PLANTED_PATCH')
    assert not sent, 'ungranted repository patch reached the cloud reviewer'
    assert found['verdict'] == 'failed'
    assert 'G6_E_CLOUD_CONSENT_REQUIRED' in found['error']
    assert 'g6_cloud_grant' in found['error']


def test_k_without_repository_grant_ends_untested(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    session = tmp_path / 'session'
    session.mkdir()
    change = SimpleNamespace(repo=tmp_path, sources=('code.py',))
    monkeypatch.setattr(loop.repo_tests, 'checked_repo', lambda repo: tmp_path)
    monkeypatch.setattr(loop.repo_tests, 'change', lambda *a: change)
    monkeypatch.setattr(loop.repo_tests, 'snapshot', lambda *a: {})
    monkeypatch.setattr(loop, '_patch_now', lambda *a: 'patch')
    monkeypatch.setattr(loop.proposer, 'boundary', lambda *a: '')
    monkeypatch.setattr(loop, '_scouts_chosen', lambda: False)
    monkeypatch.setattr(loop, '_owe_row', lambda *a: None)
    monkeypatch.setattr(loop, '_write_row', lambda *a: None)
    monkeypatch.setattr(loop, '_review_end', lambda *a: loop.Step(loop.STOP, 'review ran', ending='clean'))
    dispatched = []
    monkeypatch.setattr(host_models.CodexCaller, 'complete',
                        lambda self, system, user, **kw: dispatched.append(user) or 'none')
    monkeypatch.setattr(loop, '_pack', lambda *a: 'PLANTED_PATCH')
    monkeypatch.setattr(loop, '_masked_pack', lambda *a: ('PLANTED_PATCH', False))
    monkeypatch.setattr(loop.repo_tests, 'diff_text', lambda *a: 'PLANTED_PATCH')
    def review(change, task, critic, shipped, session, reviews, deadline):
        from g6.core.llm import LLMError
        result = loop.run_critic(change, task, critic, shipped.critic, session / 'review',
                                 patch='PLANTED_PATCH', system_sources=())
        if result.get('error'):
            raise LLMError(result['error'])
        reviews.append({'verdict': 'none', 'patch': 'patch', 'file': 'test_review.py', 'failed': {}, 'results': {}})
        return set(), ''
    monkeypatch.setattr(loop, 'review_round', review)
    result = loop._advance(tmp_path, 'task', 'base', session, None, host_models.CodexCaller(), '', True, '')
    assert not dispatched, 'K dispatched a repository review without consent'
    assert result.ending == 'untested'
    assert 'g6_cloud_grant' in result.text


def test_repository_grant_cannot_launder_ungranted_task_input(tmp_path, monkeypatch):
    assert importlib.util.find_spec('g6.core.cloud_consent'), 'no source-aware consent admission exists'
    from g6.core import cloud_consent as cloud
    from g6.mcp.cloud_consent import request_grant
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    class Session:
        async def elicit_form(self, **kwargs):
            return SimpleNamespace(action='accept', content={'value': 'Person'})
    ctx = SimpleNamespace(session=Session(), request_id='r')
    asyncio.run(request_grant(ctx, cloud.repository_source(tmp_path), 'openai:codex'))
    sent = []
    monkeypatch.setattr(host_models.CodexCaller, 'complete', lambda self, system, user, **kw: sent.append(user) or 'none')
    monkeypatch.setattr(loop, '_pack', lambda *a: 'PLANTED_OTHER_SOURCE')
    monkeypatch.setattr(loop, '_masked_pack', lambda *a: ('PLANTED_OTHER_SOURCE', False))
    found = loop.run_critic(SimpleNamespace(repo=tmp_path, sources=('code.py',)), 'PLANTED_OTHER_SOURCE',
                            host_models.CodexCaller(), 'instructions', tmp_path / 'review', patch='patch', system_sources=())
    assert not sent
    assert 'G6_E_CLOUD_CONSENT_REQUIRED' in found['error']


def test_critic_retry_with_opaque_child_diagnostics_is_withheld(tmp_path, monkeypatch):
    from g6.core import cloud_consent as cloud
    from g6.mcp.cloud_consent import request_grant
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    class Session:
        async def elicit_form(self, **kwargs):
            return SimpleNamespace(action='accept', content={'value': 'Person'})
    ctx = SimpleNamespace(session=Session(), request_id='r')
    for source in (cloud.repository_source(tmp_path), cloud.input_source('task')):
        asyncio.run(request_grant(ctx, source, 'openai:codex'))
    sent = []
    monkeypatch.setattr(host_models.CodexCaller, 'complete', lambda self, system, user, **kw: sent.append(user) or 'none')
    monkeypatch.setattr(loop, '_pack', lambda *a: 'repository patch')
    monkeypatch.setattr(loop, '_masked_pack', lambda *a: ('repository patch', False))
    found = loop.run_critic(SimpleNamespace(repo=tmp_path, sources=('code.py',)), 'task',
                            host_models.CodexCaller(), 'instructions', tmp_path / 'review', patch='patch',
                            told='PLANTED_HOST_ENV_FROM_CHILD', system_sources=())
    assert not sent, 'opaque child diagnostics reached the reviewer under a repository grant'
    assert 'G6_E_CLOUD_CONSENT_REQUIRED' in found['error']


def test_wrapped_local_critic_keeps_k_local_capability(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    session = tmp_path / 'session'
    session.mkdir()
    change = SimpleNamespace(repo=tmp_path, sources=('code.py',))
    monkeypatch.setattr(loop.repo_tests, 'checked_repo', lambda repo: tmp_path)
    monkeypatch.setattr(loop.repo_tests, 'change', lambda *a: change)
    monkeypatch.setattr(loop.repo_tests, 'snapshot', lambda *a: {})
    monkeypatch.setattr(loop, '_patch_now', lambda *a: 'patch')
    monkeypatch.setattr(loop.proposer, 'boundary', lambda *a: '')
    monkeypatch.setattr(loop, '_scouts_chosen', lambda: False)
    monkeypatch.setattr(loop, '_owe_row', lambda *a: None)
    monkeypatch.setattr(loop, '_write_row', lambda *a: None)
    monkeypatch.setattr(loop, '_review_end', lambda *a: loop.Step(loop.STOP, 'review ran', ending='clean'))
    dispatched = []
    def review(change, task, critic, shipped, session, reviews, deadline):
        dispatched.append(True)
        reviews.append({'verdict': 'none', 'patch': 'patch', 'file': 'test_review.py', 'failed': {}, 'results': {}})
        return set(), ''
    monkeypatch.setattr(loop, 'review_round', review)
    from g6.core.llm import EmptyRetry, OllamaCaller
    result = loop._advance(tmp_path, 'task', 'base', session, None,
                           EmptyRetry(OllamaCaller(base_url='http://127.0.0.1:11434')), '', True, '')
    assert dispatched, 'a retry wrapper hid the local critic from K admission'
    assert result.ending == 'clean'

