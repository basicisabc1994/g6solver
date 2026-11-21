"""Decoder failures refuse a question before any dialog or budget write."""
import asyncio
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core import cloud_consent as cloud
from g6.core.ledger import ChainLedger
from g6.harness import question_store as qs
from g6.harness.workflow import _store
from g6.mcp import behaviour


@pytest.mark.parametrize('failure', [RecursionError, MemoryError])
def test_question_ledger_decoder_limit_becomes_store_unreadable(monkeypatch, failure):
    store = qs.TaskQuestions('a' * 64)
    monkeypatch.setattr(store, '_kept', lambda: None)
    monkeypatch.setattr(store, '_regular', lambda path: True)

    def exhausted(*args, **kwargs):
        raise failure('private decoder content')

    monkeypatch.setattr(qs, 'ChainLedger', exhausted)
    with pytest.raises(qs.StoreUnreadable) as got:
        store._open()
    assert qs.UNREADABLE in str(got.value)
    assert 'private decoder content' not in str(got.value)


@pytest.mark.parametrize('failure', [RecursionError, MemoryError])
def test_workflow_ledger_decoder_limit_becomes_record_unreadable(monkeypatch, failure):
    chain = _store.Chain(Path('unused.jsonl'))
    monkeypatch.setattr(_store, 'plain', lambda path: False)

    def exhausted(*args, **kwargs):
        raise failure('private decoder content')

    monkeypatch.setattr(_store, 'ChainLedger', exhausted)
    with pytest.raises(_store.RecordUnreadable) as got:
        chain._ledger()
    assert _store.CODE in str(got.value)
    assert 'private decoder content' not in str(got.value)


@pytest.mark.parametrize('failure', [RecursionError, MemoryError])
@pytest.mark.parametrize('surface', ['confirm', 'review'])
def test_deep_question_json_is_blocked_at_both_surfaces(monkeypatch, failure, surface):
    """Keep real ledger parsing and MCP handlers; replace only filesystem and repository admission."""
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(behaviour.repo_tests, 'checked_repo', lambda repo: root)
    monkeypatch.setattr(qs.TaskQuestions, 'exists', lambda self: True)
    monkeypatch.setattr(qs.TaskQuestions, '_kept', lambda self: None)
    monkeypatch.setattr(qs.TaskQuestions, '_regular', lambda self, path: True)
    monkeypatch.setattr(qs, '_locked', lambda path: nullcontext())
    from g6.core import ledger

    if failure is RecursionError:
        monkeypatch.setattr(Path, 'read_bytes', lambda self: b'[' * 200000 + b']' * 200000)
    else:
        def exhausted(*args, **kwargs):
            raise MemoryError('private decoder content')
        monkeypatch.setattr(ledger, '_parse', exhausted)
        monkeypatch.setattr(Path, 'read_bytes', lambda self: b'{}\n')
    monkeypatch.setattr(Path, 'exists', lambda self: True)
    original_stat = Path.stat
    monkeypatch.setattr(Path, 'stat', lambda self, **kwargs: SimpleNamespace(st_size=400000)
                        if self.suffix == '.jsonl' else original_stat(self, **kwargs))
    if surface == 'confirm':
        got = asyncio.run(behaviour.confirm(None, str(root), 'task'))
    else:
        def unreachable(*args, **kwargs):
            pytest.fail('review ran despite an unreadable question store')
        got = behaviour.reviewed(unreachable, str(root), 'task')
    assert got['completion_state'] == 'blocked-escalated'
    assert got['warning_card']['code'] == qs.UNREADABLE
    assert 'private decoder content' not in str(got)


@pytest.mark.parametrize('capture', [None, SimpleNamespace(client=None),
                                   SimpleNamespace(client=('cloud:unknown', object()))])
@pytest.mark.parametrize('kind', ['caller', 'review'])
def test_unbound_question_client_refuses_before_budget_or_scenario_writes(monkeypatch, capture, kind):
    """A missing/unknown destination must refuse before constructing or persisting a question."""
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(behaviour.repo_tests, 'checked_repo', lambda repo: root)
    monkeypatch.setattr(behaviour.repo_tests, 'snapshot', lambda root: {})
    monkeypatch.setattr(qs.TaskQuestions, 'state', lambda self: qs.State())
    monkeypatch.setattr(cloud, 'current_capture', lambda: capture)
    from g6.harness import behaviour_question as bq

    def unreachable(*args, **kwargs):
        pytest.fail('candidate processing started without a known destination')

    monkeypatch.setattr(bq, 'caller_candidates', unreachable)
    monkeypatch.setattr(qs.TaskQuestions, 'ask', unreachable)
    args = {'candidates': [{}]} if kind == 'caller' else {'review_job': 'a' * 32}
    got = asyncio.run(behaviour.confirm(None, str(root), 'task', **args))
    assert got['completion_state'] == 'blocked-escalated'
    assert got['warning_card']['code'] == cloud.REQUIRED


@pytest.mark.parametrize('failure', [RecursionError, MemoryError])
@pytest.mark.parametrize('operation', ['head', 'entries'])
def test_later_question_ledger_reads_also_refuse(monkeypatch, failure, operation):
    store = qs.TaskQuestions('b' * 64)
    monkeypatch.setattr(store, '_kept', lambda: None)
    monkeypatch.setattr(store, '_regular', lambda path: True)
    ledger = ChainLedger()
    monkeypatch.setattr(qs, 'ChainLedger', lambda *args, **kwargs: ledger)

    def exhausted(*args, **kwargs):
        raise failure('private decoder content')

    monkeypatch.setattr(ledger, operation, exhausted)
    if operation == 'entries':
        monkeypatch.setattr(ledger, 'head', lambda: 'c' * 64)
    with pytest.raises(qs.StoreUnreadable):
        store._open()


@pytest.mark.parametrize('failure', [RecursionError, MemoryError])
def test_plan_decoder_limits_keep_the_plan_refusal(monkeypatch, failure):
    from g6.workforce import plans

    def exhausted(*args, **kwargs):
        raise failure('private decoder content')

    monkeypatch.setattr(plans.json, 'loads', exhausted)
    with pytest.raises(plans.PlanRejected) as got:
        plans.parse_plan('{}', risk_tier='R0', budget_cents=0)
    assert 'private decoder content' not in str(got.value)


@pytest.mark.parametrize('failure', [RecursionError, MemoryError])
def test_bound_consent_reader_helper_keeps_the_mcp_refusal(monkeypatch, failure):
    """The unbound MCP wrapper contains a decoder failure raised by a bound state-reader helper."""
    from g6.mcp import cloud_consent

    class Surface:
        def __init__(self):
            self.tools = {}

        def tool(self):
            def register(fn):
                self.tools[fn.__name__] = fn
                return fn
            return register

    def exhausted():
        raise failure('private decoder content')

    monkeypatch.setattr(cloud, 'grants', exhausted)
    surface = Surface()
    cloud_consent.register(surface, object)
    got = surface.tools['g6_cloud_grants']()
    assert got['completion_state'] == 'blocked-escalated'
    assert got['warning_card']['code'] == cloud.STATE_UNAVAILABLE
    assert 'private decoder content' not in str(got)
