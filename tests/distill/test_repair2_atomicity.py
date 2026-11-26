"""Interrupted authority publications agree with fresh real-disk reads."""
import asyncio
import json
from pathlib import Path

import pytest

from g6.core.ledger import ChainLedger, digest
from g6.core.storage import state_path
from g6.distill import acceptance_gate as gate, records as r
from g6.distill.ledger import Stores
from g6.distill.library import Library
from g6.distill.rectify_gate import Rectifier
from g6.harness import library_review_gate as review
from g6.mcp import consent, distill
from g6.mcp.distill_backend import Service
from tests.distill import test_acceptance_gate as acceptance
from tests.distill import test_recurrence_hold as recurrence
from tests.distill import test_runtime_identity_boundaries as boundaries
from tests.distill.test_runtime_identity_boundaries import AFTER, BEFORE, admit, method

disk = boundaries.disk
rig = acceptance.rig
state_home = recurrence.state_home


def interrupt(monkeypatch, target):
    """Fail once AFTER the selected real write has closed or fsynced."""
    append, opened = ChainLedger.append, Path.open
    hit = []

    def after_append(ledger, kind, ident, body):
        result = append(ledger, kind, ident, body)
        if not hit and target == kind:
            hit.append(True)
            raise OSError('injected after durable append')
        return result

    def after_close(path):
        if hit:
            return
        data = json.loads(path.read_text(encoding='utf-8'))
        selected = (target == 'object' and path.parent.name == 'objects') or (
            target == 'certificate' and data.get('authority') == 'A1') or (
            target == 'provisional' and data.get('publication') == 'provisional') or (
            target == 'report' and 'counts' in data) or (
            target == 'archive' and path.parent.name == 'regression_cases')
        if selected:
            hit.append(True)
            raise OSError('injected after durable object')

    def after_open(path, mode='r', *args, **kwargs):
        stream = opened(path, mode, *args, **kwargs)
        if mode != 'x' or path.suffix != '.json':
            return stream

        class InterruptedFile:
            # Path.open returns a file, including for callers outside a with.
            def __getattr__(self, name):
                return getattr(stream, name)

            def __iter__(self):
                return iter(stream)

            def __next__(self):
                return next(stream)

            def __enter__(self):
                stream.__enter__()
                return self

            def __exit__(self, exc_type, exc, tb):
                result = stream.__exit__(exc_type, exc, tb)
                if exc_type is None:
                    after_close(path)
                return result

            def close(self):
                if not stream.closed:
                    stream.close()
                    after_close(path)

        return InterruptedFile()

    monkeypatch.setattr(ChainLedger, 'append', after_append)
    monkeypatch.setattr(Path, 'open', after_open)
    return hit


def handlers(monkeypatch):
    tools = {}

    class Server:
        def tool(self):
            return lambda fn: tools.setdefault(fn.__name__, fn)

    async def accept(*args):
        return 'accept', {'confirm': 'change', 'name': 'Owner'}, ''

    monkeypatch.setattr(consent, 'elicit', accept)
    distill.register(Server(), object, None, backend=Service())
    return tools


@pytest.mark.parametrize('action', ['restore', 'withdraw', 'demote'])
@pytest.mark.parametrize('step', ['object', 'library', 'decision'])
def test_owner_failure_after_each_write_restores_disk_and_refuses(disk, monkeypatch, action, step):
    entry = admit(method(), disk)
    if action == 'restore':
        entry = gate._change(entry, status='held', authority='A0', reason='owner hold')
    Rectifier().reconcile()
    tools = handlers(monkeypatch)
    before = Stores().for_model(r.LibraryEntry).path.read_bytes()
    hit = interrupt(monkeypatch, step)
    answer = asyncio.run(tools['g6_library_decide'](entry.entry_id, action, 'owner decision', object()))
    assert hit, 'fault boundary was not exercised'
    assert answer['ok'] is False
    assert answer['warning_card']['code'] == 'G6_E_DISTILL_INPUT'
    assert Library().list() == [entry], 'refusal retained a partial authority change'
    assert Stores().for_model(r.LibraryEntry).path.read_bytes() == before
    assert not gate._journal().entries()


@pytest.mark.parametrize('restoring', [False, True])
@pytest.mark.parametrize('step', ['archive', 'report', 'provisional', 'certificate', 'object', 'library', 'decision'])
def test_admission_failure_leaves_neither_entry_nor_a1_certificate(rig, monkeypatch, restoring, step):
    _, candidate, _, frozen, _ = rig
    entry = None
    if restoring:
        # The proposal is already persisted by rig; do not append it twice.
        from tests.distill.test_records import bodies
        entry = r.LibraryEntry(**(bodies()[4][1] | {
            'candidate_id': candidate.candidate_id, 'status': 'withdrawn', 'authority': 'A0',
            'regression_suite': [{'case_ref': frozen.acceptance[0].case.case_id, 'verdict': 'concern'}]}))
        Stores().for_model(r.LibraryEntry).put(entry)
    original_append = ChainLedger.append

    def activation_only(ledger, kind, ident, body):
        # Earlier evaluated/reserve rows are deliberately retained spent looks.
        if kind == 'decision' and body.get('verdict') != 'admit_probation':
            return original_append(ledger, kind, ident, body)
        return failing_append(ledger, kind, ident, body)

    hit = interrupt(monkeypatch, step)
    failing_append = ChainLedger.append
    monkeypatch.setattr(ChainLedger, 'append', activation_only)
    call = (lambda: gate.restore(entry.entry_id, Library(), frozen.registration_sha256)) if restoring else (
        lambda: gate.decide(candidate, Library(), frozen.registration_sha256))
    answer = distill._call(call)
    assert hit
    assert answer.get('ok') is False or answer.get('verdict') in {'block', 'refuse'}
    assert Library().list() == ([entry] if entry else [])
    certificates = [json.loads(path.read_text(encoding='utf-8')) for path in
                    state_path('distill', 'acceptance_gate_reports').glob('*.json')]
    assert not any(data.get('authority') == 'A1' for data in certificates)
    assert not any(row['body'].get('verdict') == 'admit_probation' for row in gate._journal().entries())


@pytest.mark.parametrize('step', ['object', 'library', 'rectification', 'failure_mode'])
def test_reconcile_failure_rolls_back_every_derived_record(state_home, monkeypatch, step):
    entry, rows, _ = recurrence.removal()
    again = r.FailureRecord(**(rows[0].model_dump(exclude={'record_id'}) | {
        'observed_by': 'self', 'evidence': [], 'source': 'person', 'case_ref': digest('new recurrence'),
        'observed_at': '2026-11-22'}))
    Stores().for_model(r.FailureRecord).put(again)
    models = (r.LibraryEntry, r.Rectification, r.FailureMode)
    before = {model: Stores().for_model(model).path.read_bytes() for model in models}
    hit = interrupt(monkeypatch, step)
    with pytest.raises(OSError):
        Rectifier().reconcile()
    assert hit
    assert Library().list() == [entry]
    assert {model: Stores().for_model(model).path.read_bytes() for model in models} == before


def test_failed_field_append_retains_no_fire_and_reply_says_so(disk, monkeypatch):
    admit(method(), disk)
    hit = interrupt(monkeypatch, 'field_fire')
    [answer] = review.check(BEFORE, AFTER)
    assert hit
    assert answer['field_recorded'] is False
    assert not ChainLedger(state_path('distill', 'field_fires_ledger.jsonl')).entries()


@pytest.mark.parametrize('action', ['restore', 'withdraw'])
def test_failed_rollback_names_remaining_durable_state(disk, monkeypatch, action):
    entry = admit(method(), disk)
    if action == 'restore':
        entry = gate._change(entry, status='held', authority='A0', reason='hold')
    Rectifier().reconcile()
    tools = handlers(monkeypatch)
    hit = interrupt(monkeypatch, 'decision')
    opened = Path.open

    def cannot_restore(path, mode='r', *args, **kwargs):
        if hit and path.name == 'library.jsonl' and mode == 'wb':
            raise OSError('private rollback diagnostic')
        return opened(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', cannot_restore)
    answer = asyncio.run(tools['g6_library_decide'](entry.entry_id, action, 'decision', object()))
    assert answer['ok'] is False
    assert answer['warning_card']['code'] == 'G6_E_DISTILL_PARTIAL_WRITE'
    latest = ChainLedger(state_path('distill', 'library.jsonl')).entries()[-1]['body']
    assert latest['status'] in str(answer) and latest['authority'] in str(answer)
    assert 'No authority was granted' not in str(answer)
    assert 'private rollback diagnostic' not in str(answer)


def test_field_rollback_failure_reports_retained_fire(disk, monkeypatch):
    admit(method(), disk)
    hit = interrupt(monkeypatch, 'field_fire')
    unlink = Path.unlink

    def cannot_remove(path, *args, **kwargs):
        if hit and path.name == 'field_fires_ledger.jsonl':
            raise OSError('private field recovery diagnostic')
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', cannot_remove)
    [answer] = review.check(BEFORE, AFTER)
    assert answer['code'] == 'G6_E_DISTILL_PARTIAL_WRITE'
    assert len(ChainLedger(state_path('distill', 'field_fires_ledger.jsonl')).entries()) == 1
    assert '1 durable rows' in str(answer['durable_state'])
    assert 'no field evidence was added' not in str(answer)


def test_store_can_retry_after_its_append_was_rolled_back(disk, monkeypatch):
    candidate = method()
    Stores().for_model(r.VerifierCandidate).put(candidate)
    from tests.distill.test_records import bodies
    entry = r.LibraryEntry(**(bodies()[4][1] | {'candidate_id': candidate.candidate_id}))
    store = Stores().for_model(r.LibraryEntry)
    hit = interrupt(monkeypatch, 'library')
    with pytest.raises(OSError):
        store.put(entry)
    assert hit
    assert store.list() == []
    assert store.put(entry) == entry.entry_id
    assert Library().list() == [entry]
