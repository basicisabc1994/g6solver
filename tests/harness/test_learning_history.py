# SPDX-License-Identifier: Apache-2.0
"""Learning history is visible through the component and erased only by a client dialog."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core.registry import ComponentRegistry
from g6.harness import outcome_history as h
from g6.workforce import receipt_store


def test_repeated_execution_receipt_is_idempotent(monkeypatch):
    from g6.core.ledger import ChainLedger, LedgerError

    entries = []
    class MemoryLedger:
        def __init__(self, path):
            pass
        def entries(self):
            return entries.copy()
        def append(self, kind, ident, body):
            if any(e['id'] == ident for e in entries):
                raise LedgerError('duplicate')
            entries.append({'kind': kind, 'id': ident, 'body': body})
    from contextlib import nullcontext
    monkeypatch.setattr(receipt_store, 'ChainLedger', MemoryLedger)
    monkeypatch.setattr(receipt_store, '_locked', lambda path: nullcontext())
    monkeypatch.setattr(receipt_store, 'store_path', lambda: Path('synthetic'))
    body = {'source_before': 'artifact', 'result': {'verdict': 'failed'}}
    first = receipt_store._record(body)
    assert receipt_store._record(body) == first
    assert len(entries) == 1
    assert ChainLedger is not MemoryLedger


def test_component_lists_counts_without_retained_source(monkeypatch):
    registry = ComponentRegistry()
    assert 'learning_history' in registry.names()
    monkeypatch.setattr(h, 'repository_id', lambda root: 'a' * 64)
    row = {'classification': 'defect_detected', 'check': {'sha256': 'b' * 64, 'file': 'test_edge.py',
           'source': 'PRIVATE_CHECK_SOURCE'}, 'modules': []}
    monkeypatch.setattr(h, 'read', lambda root: h.History('a' * 64, (row,), 2))
    from g6.workforce import repo_tests
    monkeypatch.setattr(repo_tests, 'checked_repo', lambda root: Path('fixture'))
    reply = registry.invoke('learning_history', 'list', {'repo': 'fixture'}).unwrap()
    assert (reply['retained_checks'], reply['admitted_trials'], reply['rejected_trials']) == (1, 1, 2)
    assert reply['checks'][0]['file'] == 'test_edge.py'
    assert reply['trials'][0]['classification'] == 'defect_detected'
    assert reply['store_path'].endswith('a' * 64 + '.jsonl')
    assert 'PRIVATE_CHECK_SOURCE' not in json.dumps(reply)
    denied = registry.invoke('learning_history', 'forget', {'repo': 'fixture'})
    assert denied.__class__.__name__ == 'Failure'


@pytest.mark.parametrize('action,content,status', [
    ('decline', None, 'declined'), ('cancel', None, 'cancelled'),
    ('unavailable', None, 'unavailable'), ('accept', {'value': ' '}, 'invalid')])
def test_forget_without_client_agreement_removes_nothing(monkeypatch, action, content, status):
    from g6.mcp.learning_history import forget_reply
    from g6.workforce import repo_tests

    monkeypatch.setattr(repo_tests, 'checked_repo', lambda root: Path('fixture'))
    monkeypatch.setattr(h, 'read', lambda root: h.History('a' * 64))
    monkeypatch.setattr(h, 'forget', lambda *args, **kw: pytest.fail('history removed without agreement'))
    class Session:
        async def elicit_form(self, **kwargs):
            assert 'repository_id: ' + 'a' * 64 in kwargs['message']
            if action == 'unavailable':
                raise NotImplementedError('no elicitation')
            return SimpleNamespace(action=action, content=content)
    ctx = SimpleNamespace(session=Session(), request_id='fixture')
    got = asyncio.run(forget_reply(ctx, '{"repo":"fixture"}', allow=frozenset({'learning_history.forget'})))
    assert got['consent_status'] == status
    assert not got['ok']


def test_forget_refuses_caller_consent_before_repository_access(monkeypatch):
    from g6.mcp.learning_history import forget_reply
    from g6.workforce import repo_tests
    monkeypatch.setattr(repo_tests, 'checked_repo', lambda root: pytest.fail('invalid input accessed repository'))
    got = asyncio.run(forget_reply(None, '{"repo":"fixture","approved":true}',
                                  allow=frozenset({'learning_history.forget'})))
    assert not got['ok'] and 'G6_E_INPUT_VALIDATION' in got['error']


@pytest.mark.parametrize('params', [{}, {'repo': ''}, {'repo': '   '}])
def test_history_operations_require_an_explicit_repository_before_access(monkeypatch, params):
    from g6.mcp.learning_history import forget_reply
    from g6.workforce import repo_tests
    monkeypatch.setattr(repo_tests, 'checked_repo', lambda root: pytest.fail('implicit repository accessed'))
    got = asyncio.run(forget_reply(None, json.dumps(params), allow=frozenset({'learning_history.forget'})))
    assert not got['ok'] and 'G6_E_INPUT_VALIDATION' in got['error']
    listed = ComponentRegistry().invoke('learning_history', 'list', params)
    assert listed.__class__.__name__ == 'Failure'


def test_forget_requires_sensitive_allow_before_dialog(monkeypatch):
    from g6.mcp.learning_history import forget_reply
    from g6.workforce import repo_tests
    monkeypatch.setattr(repo_tests, 'checked_repo', lambda root: pytest.fail('disallowed op accessed repository'))
    got = asyncio.run(forget_reply(None, '{"repo":"fixture"}', allow=frozenset()))
    assert not got['ok'] and 'G6_E_SENSITIVE_COMPONENT_DENIED' in got['error']


def test_registered_invoke_asks_dialog_for_forget_and_keeps_tool_schema(monkeypatch):
    from g6.mcp import build_server
    from g6.mcp.registrations import _listed
    from g6.workforce import repo_tests

    monkeypatch.setenv('G6_MCP_SENSITIVE', 'learning_history.forget')
    monkeypatch.setattr(repo_tests, 'checked_repo', lambda root: Path('fixture'))
    monkeypatch.setattr(h, 'read', lambda root: h.History('a' * 64))
    monkeypatch.setattr(h, 'fingerprint', lambda repo: 'b' * 64)
    erased = []
    monkeypatch.setattr(h, 'forget', lambda root, repo, **kwargs: erased.append((root, repo, kwargs)))
    class Session:
        async def elicit_form(self, **kwargs):
            assert 'Erase G6 learning history' in kwargs['message']
            return SimpleNamespace(action='accept', content={'value': 'Fixture person'})
    tool = dict(_listed(build_server()))['g6_invoke']
    assert set(tool.parameters['properties']) == {'component', 'op', 'params_json'}
    ctx = SimpleNamespace(session=Session(), request_id='fixture')
    got = asyncio.run(tool.fn(component='learning_history', op='forget', params_json='{"repo":"fixture"}', ctx=ctx))
    assert got['ok'] and got['result']['forgotten']
    assert erased == [(Path('fixture'), 'a' * 64, {'expected': 'b' * 64})]


@pytest.mark.usefixtures('declared_mcp_grants')
def test_learning_history_list_and_declined_forget_over_mcp(monkeypatch):
    from fastmcp import Client
    from fastmcp.client.elicitation import ElicitResult
    from g6.core import door
    from g6.mcp import build_server
    from g6.workforce import repo_tests

    monkeypatch.setenv('G6_MCP_SENSITIVE', 'learning_history.forget')
    monkeypatch.setattr(door, 'person_model', lambda: None)
    monkeypatch.setattr(repo_tests, 'checked_repo', lambda root: Path('fixture'))
    monkeypatch.setattr(h, 'read', lambda root: h.History('a' * 64, rejected=3))
    monkeypatch.setattr(h, 'fingerprint', lambda repo: 'b' * 64)
    monkeypatch.setattr(h, 'forget', lambda *args, **kwargs: pytest.fail('declined deletion happened'))
    async def decline(message, response_type, params, context):
        assert 'Erase G6 learning history' in message
        return ElicitResult(action='decline')
    async def run():
        async with Client(build_server(), elicitation_handler=decline) as client:
            listed = await client.call_tool('g6_invoke', {'component': 'learning_history', 'op': 'list',
                                                       'params_json': '{"repo":"fixture"}'})
            value = json.loads(listed.content[0].text)
            assert value['ok'] and value['result']['rejected_trials'] == 3
            refused = await client.call_tool('g6_invoke', {'component': 'learning_history', 'op': 'forget',
                                                        'params_json': '{"repo":"fixture"}'})
            value = json.loads(refused.content[0].text)
            assert value['ok'] is False and value['consent_status'] == 'declined'
    asyncio.run(run())


def test_accepted_forget_io_failure_does_not_claim_no_deletion(monkeypatch):
    from g6.mcp.learning_history import forget_reply
    from g6.workforce import repo_tests

    monkeypatch.setattr(repo_tests, 'checked_repo', lambda root: Path('fixture'))
    monkeypatch.setattr(h, 'read', lambda root: h.History('a' * 64))
    monkeypatch.setattr(h, 'fingerprint', lambda repo: 'b' * 64)
    def interrupted(*args, **kwargs):
        raise OSError('interrupted during deletion')
    monkeypatch.setattr(h, 'forget', interrupted)
    class Session:
        async def elicit_form(self, **kwargs):
            return SimpleNamespace(action='accept', content={'value': 'Fixture person'})
    ctx = SimpleNamespace(session=Session(), request_id='fixture')
    got = asyncio.run(forget_reply(ctx, '{"repo":"fixture"}', allow=frozenset({'learning_history.forget'})))
    assert not got['ok']
    assert 'no deletion authorized' not in got['error']
    assert 'could not finish' in got['error']


def test_forget_removes_only_one_repository_after_dialog(tmp_path, monkeypatch):
    from g6.core.ledger import ChainLedger, digest
    from g6.mcp.learning_history import forget_reply
    from g6.workforce import repo_tests
    monkeypatch.setenv('G6_HOME', str(tmp_path))
    monkeypatch.setattr(repo_tests, 'checked_repo', lambda root: Path('fixture'))
    monkeypatch.setattr(h, 'repository_id', lambda root: 'a' * 64)
    for repository in ('a' * 64, 'b' * 64):
        value = {'schema': 'deliberately rejected row'}
        path = h.store_path(repository)
        path.parent.mkdir(parents=True, exist_ok=True)
        ChainLedger(path).append(h.KIND, digest(value), value)
    other = h.store_path('b' * 64).read_bytes()
    class Session:
        async def elicit_form(self, **kwargs):
            return SimpleNamespace(action='accept', content={'value': 'Fixture person'})
    ctx = SimpleNamespace(session=Session(), request_id='fixture')
    got = asyncio.run(forget_reply(ctx, '{"repo":"fixture"}', allow=frozenset({'learning_history.forget'})))
    assert got['ok'] and got['result']['forgotten']
    assert not h.store_path('a' * 64).exists()
    assert h.store_path('b' * 64).read_bytes() == other


def test_notice_is_once_after_recording_and_resets_after_forget(tmp_path, monkeypatch):
    from g6.core.ledger import ChainLedger, digest
    monkeypatch.setenv('G6_HOME', str(tmp_path))
    root = Path('fixture')
    monkeypatch.setattr(h, 'repository_id', lambda root: 'a' * 64)
    assert h.take_notice(root) == ''
    value = {'schema': 'row'}
    ChainLedger(h.store_path('a' * 64)).append(h.KIND, digest(value), value)
    notice = h.take_notice(root)
    assert 'locally' in notice and 'g6_invoke' in notice and 'forget' in notice
    assert h.take_notice(root) == ''
    h.forget(root, 'a' * 64)
    ChainLedger(h.store_path('a' * 64)).append(h.KIND, digest(value), value)
    assert h.take_notice(root) == notice


def test_learning_bindings_are_beside_unchanged_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path))
    body = {'schema': receipt_store.SCHEMA, 'source_before': 'artifact', 'source_after': 'artifact',
            'result': {'verdict': 'passed'}}
    binding = {'repository_id': 'a' * 64, 'check_digests': {'test_a.py': hashlib.sha256(b'check').hexdigest()},
               'test_outcomes': {'test_a.py::test_a': {'call': 'passed'}}}
    ident = receipt_store._record(body, learning=binding)
    assert receipt_store.lookup(ident, artifact_digest='artifact') == body
    learned = receipt_store.lookup_learning(ident, artifact_digest='artifact')
    assert learned['repository_id'] == binding['repository_id']
    assert learned['test_outcomes'] == binding['test_outcomes']
    assert learned['result'] == body['result']


def test_original_receipt_result_shape_stays_pinned(monkeypatch):
    from tests.harness.test_proposed_checks import result

    monkeypatch.setattr(receipt_store, '_executable', lambda python: {'path': python})
    ran = result(['test_edge.py::test_edge'])
    body = receipt_store._body(started_at='start', finished_at='end', before={}, after={},
                               base_commit='base', python='fixture', requested=['test_edge.py::test_edge'], found=ran)
    assert set(body['result']) == {'verdict', 'passed', 'failed', 'not_passed', 'total',
                                  'failed_ids', 'completed', 'degraded'}
    assert not {'repository_id', 'check_digests', 'test_outcomes'} & body.keys()


def test_learning_binding_never_overrides_receipt_outcome_and_ambiguous_bindings_fail_closed(monkeypatch):
    from g6.core.ledger import digest

    body = {'source_before': 'artifact', 'result': {'verdict': 'failed'}}
    binding = {'receipt': 'owned', 'repository_id': 'a' * 64, 'check_digests': {},
               'result': {'verdict': 'passed'}}
    entries = [{'kind': 'test-learning-binding', 'id': digest(binding), 'body': binding}]
    monkeypatch.setattr(receipt_store, 'lookup', lambda *args, **kwargs: body)
    monkeypatch.setattr(receipt_store, 'ChainLedger', lambda path: SimpleNamespace(entries=lambda: entries))
    learned = receipt_store.lookup_learning('owned', artifact_digest='artifact')
    assert learned['result'] == {'verdict': 'failed'}
    entries.append({'kind': 'test-learning-binding', 'id': 'forged', 'body': binding})
    assert receipt_store.lookup_learning('owned', artifact_digest='artifact') == learned
    other = {**binding, 'repository_id': 'b' * 64}
    entries.append({'kind': 'test-learning-binding', 'id': digest(other), 'body': other})
    assert receipt_store.lookup_learning('owned', artifact_digest='artifact') is None


def _stops():
    from g6.core.component_process import ContainmentFailure, ExecutionInterrupted
    return [ExecutionInterrupted('operator stop', stopped=True), ExecutionInterrupted('allowance ended'),
            ContainmentFailure('cleanup unconfirmed')]


@pytest.mark.parametrize('stop', _stops(), ids=lambda e: type(e).__name__)
def test_worker_stop_reaches_the_door_through_every_history_reader(monkeypatch, stop):
    """Both are OSError subclasses: an OSError handler around a git call must let them through."""
    from g6.learning_history.block import HistoryInput, LearningHistoryBlock
    from g6.mcp.learning_history import forget_reply
    from g6.workforce import repo_tests

    def git(*args, **kwargs):
        raise stop

    monkeypatch.setattr(repo_tests, '_git', git)
    monkeypatch.setattr(repo_tests, 'checked_repo', lambda root: Path('fixture'))
    with pytest.raises(type(stop)):
        receipt_store.repository_id(Path('fixture'))
    with pytest.raises(type(stop)):
        LearningHistoryBlock().infer(HistoryInput(op='list', repo='fixture'))
    ctx = SimpleNamespace(session=None, request_id='fixture')
    with pytest.raises(type(stop)):
        asyncio.run(forget_reply(ctx, '{"repo":"fixture"}', allow=frozenset({'learning_history.forget'})))
