"""Pending questions keep their original sources across retries, sessions and server restarts.

The confined review is an offline stand-in. Grants use the accepting person dialog in the shared helpers.
"""
import asyncio
import json

import pytest
from fastmcp import Client
from fastmcp.client.elicitation import ElicitResult
from mcp.types import Implementation

from g6.core import cloud_consent as cloud
from g6.core.ledger import digest
from g6.core.storage import g6_home
from g6.harness import behaviour_runner, patch_review, question_store as qs
from g6.harness.verification_templates import TemplateInstance, instance_digest
from g6.mcp import behaviour, build_server, review_binding, review_reply
from g6.workforce import repo_tests
from tests.s16r3.test_review_binding_reaches_confirm import (
    CLAUDE, CODEX, TASK, _all_shared, repo as _repo,
)

repo = _repo   # reuse the existing offline repository fixture

SECRET = 'pending-session-private-argument'
INSTANCE = TemplateInstance(id='D1', template='differential.v1', requirement_id='R1', intent='change',
                            bindings={'function': 'pkg.add', 'mode': 'outputs', 'calls': [{'args': [SECRET, 'z']}]})
INSTANCES = [{k: v for k, v in INSTANCE.model_dump().items() if k != 'provenance'}]
ACTIVITY = {'activity': 'template instance', 'check': 'D1', 'template': 'differential.v1',
            'result': 'behaviour_changed', 'instance_sha256': instance_digest(INSTANCE),
            'differences': [{'input': json.dumps({'args': [SECRET, 'z'], 'kwargs': {}}, sort_keys=True),
                             'base': json.dumps({'returned': SECRET + 'z'}),
                             'patch': json.dumps({'returned': 'z' + SECRET})}]}
CANDIDATE = {'requirement_id': 'R1', 'function': 'pkg.add', 'args': [SECRET, 'z'],
             'outcomes': {'A': {'returns': SECRET + 'z'}, 'B': {'returns': 'z' + SECRET}},
             'cited': 'add commutative', 'evidence': ['pkg/__init__.py: add returns a + b']}


class Dialogs:
    def __init__(self):
        self.shown = []

    async def __call__(self, message, response_type, params, context):
        self.shown.append(message)
        return ElicitResult(action='decline')


def _client(server, dialogs, provider=CODEX):
    return Client(server, client_info=Implementation(
        name='codex' if provider == CODEX else 'claude-code', version='test'), elicitation_handler=dialogs)


async def _confirm(client, root, **extra):
    return (await client.call_tool('g6_confirm_behaviour', {'repo': str(root), 'task': TASK, **extra})).structured_content


async def _review(client, root):
    handle = (await client.call_tool('g6_review_patch', {
        'repo': str(root), 'task': TASK, 'template_instances': INSTANCES,
        'requirements': [{'id': 'R1', 'text': 'add adds', 'checks': ['tests/test_add.py::test_add']}]
    })).structured_content['job_id']
    for _ in range(3000):
        polled = (await client.call_tool('metaharness_job', {'job_id': handle})).structured_content
        if polled.get('status') in ('completed', 'failed'):
            assert polled['status'] == 'completed', polled
            return handle
        await asyncio.sleep(0.02)
    raise AssertionError('review did not finish')


def _stand_in(monkeypatch, root):
    def finished(*args, **kwargs):
        one = behaviour_runner.Activity(ACTIVITY)
        one.owned_runs = [True, True, True]
        one.execution_artifacts = [{'owned-execution': 'kept-through-both-copies'}]
        return {'ok': True, 'completion_state': 'qualified-draft',
                'artifact_digest': digest(repo_tests.snapshot(root)),
                'assessment': {'report': 'offline review', 'completion': {'activities': [one]}},
                'merge': {'status': 'requires_separate_approval'}, 'evidence': []}
    monkeypatch.setattr(patch_review, 'review_patch', finished)


@pytest.mark.parametrize('kind', ['caller', 'review'])
@pytest.mark.parametrize('retry', ['same', 'other-session', 'other-provider', 'restart', 'input-revoke', 'repo-revoke'])
def test_pending_question_rechecks_its_original_sources_and_session(repo, monkeypatch, kind, retry):
    _all_shared(repo, CODEX, CLAUDE)
    _stand_in(monkeypatch, repo)
    # Isolate pending provenance from F3: the stand-in's probe is treated as G6-owned for this test only.
    monkeypatch.setattr(behaviour_runner, 'read_probes', lambda *args: {'D1': digest(ACTIVITY)})
    server, dialogs = build_server(), Dialogs()

    async def run():
        async with _client(server, dialogs) as first:
            extra = {'candidates': [CANDIDATE]} if kind == 'caller' else {
                'review_job': await _review(first, repo), 'template_instances': INSTANCES}
            asked = await _confirm(first, repo, **extra)
            assert asked.get('status') == 'pending' and SECRET in json.dumps(asked), asked
            before = len(dialogs.shown)
            if retry == 'input-revoke':
                cloud.revoke(cloud.input_source(SECRET), CODEX)
            if retry == 'repo-revoke':
                cloud.revoke(cloud.repository_source(repo), CODEX)
            if retry in ('same', 'input-revoke', 'repo-revoke'):
                return await _confirm(first, repo), dialogs.shown[before:]
        next_server = build_server() if retry == 'restart' else server
        async with _client(next_server, dialogs, CLAUDE if retry == 'other-provider' else CODEX) as other:
            return await _confirm(other, repo), dialogs.shown[before:]
    got, shown = asyncio.run(run())
    # Consent is per source and provider: the same provider may answer later in another session or after a restart;
    # another provider, or any revoke, gets the withheld reply.
    if retry in ('same', 'other-session', 'restart'):
        assert got.get('status') == 'pending' and SECRET in json.dumps(got) and shown, got
    else:
        assert shown == [] and SECRET not in json.dumps(got) and cloud.REQUIRED in json.dumps(got), got


def test_a_legacy_pending_question_without_provenance_is_withheld(repo):
    _all_shared(repo, CODEX)
    dialogs = Dialogs()
    from g6.harness import behaviour_question as bq
    from g6.harness import executable_scenario

    candidate = bq.caller_candidates(TASK, [CANDIDATE])[0]
    question, rule = candidate.questions(digest(repo_tests.snapshot(repo)))
    scenario = executable_scenario.Scenario.resolve(repo, question)
    executable_scenario.save(question, scenario)
    qs.TaskQuestions(behaviour.identity_of(str(repo), TASK)).ask(question, rule, candidate)

    async def run():
        async with _client(build_server(), dialogs) as client:
            return await _confirm(client, repo)
    got = asyncio.run(run())
    assert dialogs.shown == [] and SECRET not in json.dumps(got) and cloud.REQUIRED in json.dumps(got), got


def test_owned_probe_custody_reaches_mcp_save_and_the_next_gate(repo, monkeypatch):
    _all_shared(repo, CODEX)
    _stand_in(monkeypatch, repo)

    async def run():
        async with _client(build_server(), Dialogs()) as client:
            handle = await _review(client, repo)
            return handle, await _confirm(client, repo, review_job=handle, template_instances=INSTANCES)
    handle, got = asyncio.run(run())
    saved = g6_home() / 'reviews' / f'{handle}.json'
    assert saved.with_suffix('.observations.json').is_file()
    record = json.loads(saved.with_suffix('.observations.json').read_bytes())
    assert record['executions']['D1'] == [{'owned-execution': 'kept-through-both-copies'}]
    # The person's unanswered dialog is the intended next gate once G6's owned probe is available.
    assert got.get('status') == 'pending', got
    assert (got.get('warning_card') or {}).get('code') == 'G6_E_QUESTION_UNCONFIRMED', got


def test_swapped_review_and_source_pair_cannot_relabel_the_checked_bytes(repo, monkeypatch):
    _all_shared(repo, CODEX)
    _stand_in(monkeypatch, repo)
    monkeypatch.setattr(behaviour_runner, 'read_probes', lambda *args: {'D1': digest(ACTIVITY)})
    original = review_binding.matches

    def swapping(path, identity, *, data=None):
        matched = original(path, identity, data=data)
        if matched:
            replacement = {'assessment': {'completion': {}}, 'artifact_digest': 'another review'}
            path.write_bytes(json.dumps(replacement, ensure_ascii=False, indent=1).encode('utf-8'))
            review_reply.record(path.stem, replacement, (
                cloud.Source.text(cloud.repository_source(repo), 'replacement', 'harmless'),))
        return matched
    monkeypatch.setattr(review_binding, 'matches', swapping)
    dialogs = Dialogs()

    async def run():
        async with _client(build_server(), dialogs) as client:
            handle = await _review(client, repo)
            return await _confirm(client, repo, review_job=handle, template_instances=INSTANCES)
    got = asyncio.run(run())
    assert dialogs.shown == [] and cloud.REQUIRED in json.dumps(got) and SECRET not in json.dumps(got), got
