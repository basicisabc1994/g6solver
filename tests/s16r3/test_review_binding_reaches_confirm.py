"""A review's task binding reaches its saved copy over MCP, and ``g6_confirm_behaviour`` reads that copy under the
review's own sources (repair 1 of claude/wire-reply, after security review 1, finding 2).

A job's compact step renders a JSON copy of the review's reply, so the process-local custody that binds it to its task
(``review_binding.BoundReply``) was lost before ``review_reply.save``, and no ``<job>.binding.json`` was ever written
over MCP: ``g6_confirm_behaviour`` refused every such review as not bound. Once it is written, the confirm tool reads
the saved review, so that read carries the review's recorded sources, and the submitting call's own inputs only for
the client session and provider that submitted it, as the poll does.

Temporary homes only; every grant through ``request_grant`` and an accepting fake dialog; no model call. The review is
a stand-in (``review_patch`` returns a finished assessment of the tree, with no G6-owned probe record).
"""
import asyncio
import json
import subprocess
from types import SimpleNamespace

import pytest
from fastmcp import Client
from mcp.types import Implementation

from g6.core import cloud_consent as cloud
from g6.core.ledger import digest
from g6.core.storage import g6_home
from g6.harness import behaviour_question as bq
from g6.harness import patch_review
from g6.mcp import behaviour, build_server, review_binding
from g6.mcp.cloud_consent import request_grant
from g6.workforce import repo_tests

CODEX, CLAUDE = 'openai:codex', 'anthropic:claude_code'
CLIENT = {CODEX: 'codex', CLAUDE: 'claude-code'}
NAMED = ('jobs', 'host_configuration', 'completion_history', 'agent_environment')
REPORT = 'THE_BOUND_REVIEW_REPORT'
TASK = 'make add commutative'


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
                'artifact_digest': digest(repo_tests.snapshot(root)),
                'assessment': {'report': REPORT, 'outcome': 'supported_within_scope', 'reasons': [],
                               'next_actions': [], 'unknowns': [],
                               'completion': {'open_questions': [], 'activities': []}},
                'merge': {'status': 'requires_separate_approval'}, 'evidence': []}
    monkeypatch.setattr(patch_review, 'review_patch', finished)
    return root


def _arguments(root):
    return {'repo': str(root), 'task': TASK,
            'requirements': [{'id': 'R1', 'text': 'add adds', 'checks': ['tests/test_add.py::test_add']}]}


def _all_shared(root, *providers):
    _share((*NAMED, cloud.repository_source(root)), *providers)


def _confirmed(root, *, asker=None, before=None):
    """Review as Codex and poll it to the end; then ``g6_confirm_behaviour`` with that ``review_job``, from the same
    session, or from a new session of ``asker``. ``before`` runs between the two."""
    server = build_server()

    async def poll(client, handle):
        for _ in range(3000):
            got = await client.call_tool('metaharness_job', {'job_id': handle})
            if got.structured_content.get('status') in ('completed', 'failed'):
                return got.structured_content
            await asyncio.sleep(0.02)
        raise AssertionError('the review job did not finish')

    async def confirm(client, handle):
        got = await client.call_tool('g6_confirm_behaviour', {'repo': str(root), 'task': TASK, 'review_job': handle})
        return got.structured_content

    async def run():
        async with Client(server, client_info=Implementation(name='codex', version='test')) as client:
            handle = (await client.call_tool('g6_review_patch', _arguments(root))).structured_content['job_id']
            polled = await poll(client, handle)
            if before is not None:
                before()
            if asker is None:
                return handle, polled, await confirm(client, handle)
            async with Client(server, client_info=Implementation(name=CLIENT[asker], version='test')) as other:
                return handle, polled, await confirm(other, handle)
    return asyncio.run(run())


def _code(reply):
    return (reply.get('warning_card') or {}).get('code')


def _withheld(reply):
    return cloud.REQUIRED in json.dumps(reply) and REPORT not in json.dumps(reply)


def test_a_review_over_mcp_writes_the_binding_to_its_task(repo):
    _all_shared(repo, CODEX)
    handle, polled, _ = _confirmed(repo)
    assert polled['status'] == 'completed' and REPORT in json.dumps(polled), polled
    saved = g6_home() / 'reviews' / f'{handle}.json'
    assert saved.with_suffix('.binding.json').is_file(), sorted(p.name for p in saved.parent.iterdir())
    assert review_binding.matches(saved, behaviour.identity_of(str(repo), TASK))


def test_the_confirm_tool_takes_the_bound_review_to_its_next_gate(repo):
    # No longer refused as not bound; the stand-in review has no G6-owned probe record, so the probe gate (repair 2 of
    # the question slice) refuses it next, as intended until G6 has a channel the repository cannot write to.
    _all_shared(repo, CODEX)
    _, _, got = _confirmed(repo)
    assert _code(got) != behaviour.NOT_THIS_REVIEW, got
    assert _code(got) == bq.PROBE_UNAVAILABLE, got


@pytest.mark.parametrize('asker', [CODEX, CLAUDE], ids=['other-codex-session', 'other-provider'])
def test_the_saved_review_is_read_for_the_session_and_provider_that_asked_only(repo, asker):
    # every named source is shared with both providers: the review call's own input is the one thing told apart
    _all_shared(repo, CODEX, CLAUDE)
    _, _, got = _confirmed(repo, asker=asker)
    assert _withheld(got), got
    assert _code(got) not in (behaviour.NOT_THIS_REVIEW, bq.PROBE_UNAVAILABLE), got


@pytest.mark.parametrize('which', ['task', 'requirement', 'review-arguments'])
def test_a_revoke_of_any_input_the_review_carries_withholds_the_read(repo, which):
    _all_shared(repo, CODEX)
    text = {'task': TASK, 'requirement': 'add adds',
            'review-arguments': json.dumps(_arguments(repo), sort_keys=True)}[which]
    _, _, got = _confirmed(repo, before=lambda: cloud.revoke(cloud.input_source(text), CODEX))
    assert _withheld(got), got
    assert cloud.input_source(text) in json.dumps(got), got
