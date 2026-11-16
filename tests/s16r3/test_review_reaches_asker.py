"""A finished review's result reaches the client that asked for it, and only that client (ruling of 2026-11-16).

A person who shared every named source of a review (the repository, jobs, host_configuration, completion_history and
the agent environment) gets the completed ``g6_review_patch`` back at its ``metaharness_job`` poll. Returning a call's
own arguments to the same client and provider that sent them discloses nothing new; a saved assessment carries the
sources of the review that made it, not G6's own storage folder. Nothing is granted for the person here: each grant
goes through ``request_grant`` and an accepting client dialog, in a temporary home. No model call is made, and the
review's own work is a stand-in (``review_patch`` returns a finished assessment, unbound, as the real one does), so no
confined run is needed.
"""
import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp import Client
from mcp.types import Implementation

from g6.core import cloud_consent as cloud
from g6.core.storage import g6_home
from g6.harness import patch_review
from g6.mcp import build_server, review_reply
from g6.mcp.cloud_consent import request_grant

CODEX, CLAUDE = 'openai:codex', 'anthropic:claude_code'
CLIENT = {CODEX: 'codex', CLAUDE: 'claude-code'}
NAMED = ('jobs', 'host_configuration', 'completion_history', 'agent_environment')
REPORT = 'THE_REVIEW_REPORT_FOR_THE_ASKER'


class _Person:
    async def elicit_form(self, **kwargs):
        return SimpleNamespace(action='accept', content={'value': 'Person'})


async def _granted(scopes, *providers):
    for provider in providers:
        for scope in scopes:
            context = SimpleNamespace(session=_Person(), request_id='person-dialog')
            assert (await request_grant(context, scope, provider))['status'] == 'granted'


def _share(scopes, *providers):
    asyncio.run(_granted(scopes, *providers))


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('G6_MCP_SENSITIVE', 'patch_review.review')
    monkeypatch.setenv('G6_DISABLE_LLM', '1')
    root = tmp_path / 'repo'
    (root / 'pkg').mkdir(parents=True)
    (root / 'pkg' / '__init__.py').write_text('def add(a, b):\n    return a + b\n', encoding='utf-8')
    (root / 'tests').mkdir()
    (root / 'tests' / 'test_add.py').write_text(
        'from pkg import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n', encoding='utf-8')
    for argv in (['init', '-q'], ['add', '-A'],
                 ['-c', 'user.name=t', '-c', 'user.email=t@example.invalid', 'commit', '-qm', 'base']):
        subprocess.run(['git', *argv], cwd=root, check=True, capture_output=True)
    (root / 'pkg' / '__init__.py').write_text('def add(a, b):\n    return b + a\n', encoding='utf-8')

    def finished(repo, **kwargs):
        return {'ok': True, 'completion_state': 'qualified-draft', 'reliability_label': 'qualified-draft',
                'assessment': {'report': REPORT, 'outcome': 'supported_within_scope', 'reasons': [],
                               'next_actions': [], 'unknowns': []},
                'merge': {'status': 'requires_separate_approval'}, 'evidence': []}
    monkeypatch.setattr(patch_review, 'review_patch', finished)
    return root


def _arguments(root):
    return {'repo': str(root), 'task': 'make add commutative',
            'requirements': [{'id': 'R1', 'text': 'add adds', 'checks': ['tests/test_add.py::test_add']}]}


def _input(root):
    """The review call's own arguments, as the consent layer names them."""
    return cloud.input_source(json.dumps(_arguments(root), sort_keys=True))


def _folder():
    return cloud.repository_source(g6_home() / 'reviews')


def _review(root, *, poller=None, detail='compact', before_poll=None, again=None):
    """Submit the review as Codex; poll its job from the same session, or from a new session of ``poller``. ``again``
    runs after the first finished poll, and the job is then polled once more."""
    server = build_server()

    async def poll(client, handle):
        for _ in range(3000):
            got = await client.call_tool('metaharness_job', {'job_id': handle, 'detail': detail})
            if got.structured_content.get('status') in ('completed', 'failed'):
                return got.structured_content
            await asyncio.sleep(0.02)
        raise AssertionError('the review job did not finish')

    async def run():
        async with Client(server, client_info=Implementation(name='codex', version='test')) as client:
            handle = (await client.call_tool('g6_review_patch', _arguments(root))).structured_content['job_id']
            if before_poll is not None:
                await before_poll()
            if poller is None:
                first = await poll(client, handle)
                if again is None:
                    return first
                await again()
                return first, await poll(client, handle)
            async with Client(server, client_info=Implementation(name=CLIENT[poller], version='test')) as other:
                return await poll(other, handle)
    return asyncio.run(run())


def _delivered(reply):
    return reply['status'] == 'completed' and cloud.REQUIRED not in json.dumps(reply) and REPORT in json.dumps(reply)


def _withheld(reply, *scopes):
    text = json.dumps(reply)
    return cloud.REQUIRED in text and REPORT not in text and all(scope in text for scope in scopes)


@pytest.mark.parametrize('detail', ['compact', 'full'])
def test_the_asker_gets_its_completed_review_with_only_the_named_sources_shared(repo, detail):
    _share((*NAMED, cloud.repository_source(repo)), CODEX)
    reply = _review(repo, detail=detail)
    assert _delivered(reply), reply['result'].get('error')
    assert reply['result']['assessment']['report'] == REPORT
    if detail == 'compact':
        assert Path(reply['result']['full_assessment']['file']).parent == (g6_home() / 'reviews').resolve()
        # the saved assessment carries the review's own sources, recorded at save time
        recorded = {source.source_id for source in review_reply.carried(reply['job_id'])}
        assert {cloud.repository_source(repo), _input(repo), 'host_configuration'} <= recorded
        assert _folder() not in recorded


@pytest.mark.parametrize('poller', [None, CODEX, CLAUDE], ids=['same-session', 'other-codex-session', 'other-provider'])
def test_only_the_session_and_provider_that_asked_get_the_review_back(repo, poller):
    # every named source is shared with both providers: the call's input is the one thing told apart
    _share((*NAMED, cloud.repository_source(repo)), CODEX, CLAUDE)
    reply = _review(repo, poller=poller)
    if poller is None:
        assert _delivered(reply), reply['result'].get('error')
    else:
        assert _withheld(reply, _input(repo)), reply


@pytest.mark.parametrize('repository_shared', [True, False])
def test_the_review_carries_its_repository(repo, repository_shared):
    _share((*NAMED, *((cloud.repository_source(repo),) if repository_shared else ())), CODEX)
    reply = _review(repo)
    if repository_shared:
        assert _delivered(reply), reply['result'].get('error')
    else:
        assert _withheld(reply, cloud.repository_source(repo)), reply


@pytest.mark.parametrize('decision', ['none', 'revoked', 'declined'])
def test_a_persons_decision_on_the_input_still_wins(repo, decision):
    _share((*NAMED, cloud.repository_source(repo)), CODEX)

    async def decide():
        if decision == 'revoked':
            await _granted((_input(repo),), CODEX)
        if decision != 'none':
            # the person's explicit "no" for this input and provider (g6_cloud_revoke needs no earlier grant)
            cloud.revoke(_input(repo), CODEX)
    reply = _review(repo, before_poll=decide)
    if decision == 'none':
        assert _delivered(reply), reply['result'].get('error')
    else:
        assert _withheld(reply, _input(repo)), reply


@pytest.mark.parametrize('damage', ['no-record', 'no-sources', 'reply-changed', 'other-version'])
def test_a_saved_review_without_its_recorded_sources_is_the_folders(repo, monkeypatch, damage):
    _share((*NAMED, cloud.repository_source(repo)), CODEX)
    original = getattr(review_reply, 'carried', None)

    def carried(job_id):
        # the saved record as a reader finds it: written before this change, or changed since
        path = g6_home() / 'reviews' / f'{job_id}.json'
        side = path.with_suffix('.sources.json')
        if damage == 'no-record':
            side.unlink(missing_ok=True)
        elif damage == 'reply-changed':
            path.write_bytes(path.read_bytes() + b' ')
        else:
            record = json.loads(side.read_text(encoding='utf-8')) if side.exists() else {}
            record.update({'sources': []} if damage == 'no-sources' else {'version': 'review-sources.v0'})
            side.write_text(json.dumps(record), encoding='utf-8')
        return original(job_id) if original is not None else None
    monkeypatch.setattr(review_reply, 'carried', carried, raising=False)
    first, second = _review(repo, again=lambda: _granted((_folder(),), CODEX))
    assert _withheld(first, _folder()), first
    # the folder is the one source missing: once the person shares it, the same poll delivers
    assert _delivered(second), second['result'].get('error')


def test_the_disclosure_log_records_the_delivery_with_the_carried_sources(repo):
    _share((*NAMED, cloud.repository_source(repo)), CODEX)
    reply = _review(repo)
    assert _delivered(reply), reply['result'].get('error')
    delivered = [row for row in cloud.disclosures() if row['provider'] == CODEX
                 and any(s['source'] == _input(repo) for s in row['sources'])]
    assert delivered, cloud.disclosures()
    basis = {s['source']: s['basis'] for s in delivered[-1]['sources']}
    assert basis[_input(repo)] == 'client-supplied'
    assert basis[cloud.repository_source(repo)] == 'grant'
    assert _folder() not in basis
