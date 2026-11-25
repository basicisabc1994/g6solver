"""Portable undo targets, unavailable-library isolation and committed replies."""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Event

import pytest

from g6.core.ledger import ChainLedger, canonical, digest
from g6.core.storage import state_path
from g6.distill import acceptance_gate as gate, ledger, surface
from g6.distill.library import Library
from g6.distill.rectify_gate import Rectifier
from g6.harness import library_review_gate as review
from g6.mcp import distill
from tests.distill import test_runtime_identity_boundaries as runtime
from tests.distill.test_repair2_atomicity import handlers


def plant(rows, *, committed=False):
    body = {'version': 1, 'committed': committed, 'writes': rows}
    ledger._intent_path().write_text(canonical({'body': body, 'sha256': digest(body)}) + '\n', encoding='utf-8')


def undo(name, before=None):
    return {'path': name, 'operation': 'replace', 'after': None,
            'before': None if before is None else base64.b64encode(before).decode('ascii')}


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / 'home'
    monkeypatch.setenv('G6_HOME', str(root))
    (root / 'distill').mkdir(parents=True)
    return root


def test_intent_writer_uses_home_relative_targets(home):
    target = state_path('distill', 'retained.dat')
    target.write_bytes(b'before')
    with ledger.failure_writer():
        ledger.retain_before_write(target, after=b'after')
        rows = json.loads(ledger._intent_path().read_text(encoding='utf-8'))['body']['writes']
        assert [row['path'] for row in rows] == ['distill/retained.dat']
        target.write_bytes(b'after')
    assert target.read_bytes() == b'after'


def test_relative_intent_recovers_inside_this_home(home):
    target = state_path('distill', 'retained.dat')
    target.write_bytes(b'half-published')
    plant([undo('distill/retained.dat', b'before')])
    assert distill._call(surface.library) == {'entries': []}
    assert target.read_bytes() == b'before'
    assert not ledger._intent_path().exists()


@pytest.mark.parametrize('before', [None, b'before'], ids=['delete', 'restore'])
def test_absolute_inside_intent_recovers_only_its_target(home, tmp_path, before):
    target = state_path('distill', 'retained.dat')
    target.write_bytes(b'half-published')
    sibling = state_path('distill', 'untouched.dat')
    outside = tmp_path / 'outside.dat'
    sibling.write_bytes(b'keep sibling')
    outside.write_bytes(b'keep outside')
    plant([undo(str(target), before)])
    assert distill._call(surface.library) == {'entries': []}
    assert (target.read_bytes() if target.exists() else None) == before
    assert sibling.read_bytes() == b'keep sibling'
    assert outside.read_bytes() == b'keep outside'
    assert not ledger._intent_path().exists()


@pytest.mark.parametrize('committed', [False, True])
def test_absolute_outside_intent_preserves_all_bytes(home, tmp_path, committed):
    inside = state_path('distill', 'retained.dat')
    outside = tmp_path / 'outside.dat'
    inside.write_bytes(b'keep inside')
    outside.write_bytes(b'keep outside')
    # A valid last row would be undone first unless all targets are checked.
    plant([undo(str(outside), b'replaced'), undo(str(inside))], committed=committed)
    pending = ledger._intent_path().read_bytes()
    reply = distill._call(surface.library)
    assert reply['warning_card']['code'] == ledger.PartialAuthorityWrite.code
    assert 'authority_intent.json' in str(reply['durable_state'])
    assert inside.read_bytes() == b'keep inside'
    assert outside.read_bytes() == b'keep outside'
    assert ledger._intent_path().read_bytes() == pending


@pytest.mark.parametrize('committed', [False, True])
def test_moved_home_refuses_old_absolute_target(home, tmp_path, monkeypatch, committed):
    target = state_path('distill', 'retained.dat')
    target.write_bytes(b'half-published')
    plant([undo(str(target), b'before')], committed=committed)
    pending = ledger._intent_path().read_bytes()
    moved = tmp_path / 'moved'
    home.rename(moved)
    monkeypatch.setenv('G6_HOME', str(moved))
    reply = distill._call(surface.library)
    assert reply['warning_card']['code'] == ledger.PartialAuthorityWrite.code
    assert (moved / 'distill' / 'retained.dat').read_bytes() == b'half-published'
    assert ledger._intent_path().read_bytes() == pending
    assert not home.exists()


@pytest.mark.parametrize('committed', [False, True])
@pytest.mark.parametrize('name', ['absolute_outside', 'absolute_malformed', '../outside.dat',
                                'distill/../retained.dat', '/rooted', 'C:relative', '', None, 4,
                                'distill//retained.dat', 'distill/authority_intent.json',
                                'distill/authority_intent.pending'])
def test_invalid_target_refuses_entire_intent_without_writes(home, tmp_path, name, committed):
    inside, outside = state_path('distill', 'retained.dat'), tmp_path / 'outside.dat'
    inside.write_bytes(b'keep inside')
    outside.write_bytes(b'keep outside')
    if name == 'absolute_outside':
        name = str(outside)
    elif name == 'absolute_malformed':
        name = str(home) + '/distill/./retained.dat'
    plant([undo(name)], committed=committed)
    pending = ledger._intent_path().read_bytes()
    reply = distill._call(surface.library)
    assert reply.get('warning_card', {}).get('code') == ledger.PartialAuthorityWrite.code
    assert 'authority_intent.json' in str(reply['durable_state'])
    assert inside.read_bytes() == b'keep inside'
    assert outside.read_bytes() == b'keep outside'
    assert ledger._intent_path().read_bytes() == pending


def test_all_targets_are_checked_before_any_undo(home):
    target = state_path('distill', 'retained.dat')
    target.write_bytes(b'keep')
    # Recovery reverses rows: the valid row would be undone first if parsing
    # and restoration were interleaved instead of checking the whole intent.
    plant([undo('../outside.dat'), undo('distill/retained.dat', b'changed')])
    pending = ledger._intent_path().read_bytes()
    reply = distill._call(surface.library)
    assert reply['warning_card']['code'] == ledger.PartialAuthorityWrite.code
    assert target.read_bytes() == b'keep'
    assert ledger._intent_path().read_bytes() == pending


def test_writer_refuses_outside_target_before_journalling(home, tmp_path):
    target = tmp_path / 'outside.dat'
    target.write_bytes(b'keep')
    with pytest.raises(ValueError):
        with ledger.failure_writer():
            ledger.retain_before_write(target)
    assert target.read_bytes() == b'keep'
    assert not ledger._intent_path().exists()


@pytest.mark.parametrize('failing', [False, True])
def test_memory_journal_with_in_home_disk_target(home, failing):
    journal = ChainLedger()
    target = state_path('distill', 'retained.txt')
    target.write_text('before', encoding='utf-8')

    def unexpected_recovery():
        pytest.fail('an in-memory ledger has no disk recovery')

    def write():
        with ledger.failure_writer():
            ledger.retain_before_write(journal.path, recover=unexpected_recovery)
            gate._append(journal, 'decision', {'verdict': 'refuse'})
            ledger.retain_before_write(target)
            target.write_text('after', encoding='utf-8')
            if failing:
                raise OSError('abort disk transaction')

    if failing:
        with pytest.raises(OSError, match='abort disk transaction'):
            write()
    else:
        write()
    assert target.read_text(encoding='utf-8') == ('before' if failing else 'after')
    assert journal.entries()[0]['body'] == {'verdict': 'refuse'}
    assert journal.path is None
    assert not ledger._intent_path().exists()


@pytest.mark.parametrize('writer', ['retain', 'append'])
@pytest.mark.parametrize('existing', [False, True])
def test_outside_journal_target_has_named_refusal_without_writes(home, tmp_path, writer, existing):
    from g6.core.ledger import LedgerError

    target = tmp_path / 'outside.jsonl'
    chain = ChainLedger(target)
    if existing:
        chain.append('decision', 'before', {'verdict': 'refuse'})
    before = target.read_bytes() if target.exists() else None
    with ledger.failure_writer():
        with pytest.raises(LedgerError, match='journal target must be a file inside the state home') as refused:
            if writer == 'retain':
                ledger.retain_before_write(target, after=b'changed')
            else:
                ledger.append_retained(chain, 'decision', 'test', {'verdict': 'refuse'})
        assert refused.value.code == 'G6_E_DISTILL_JOURNAL_TARGET'
        assert str(target) not in str(refused.value)
        assert not ledger._intent_path().exists()
        assert not ledger._intent_path().with_suffix('.pending').exists()
    assert (target.read_bytes() if target.exists() else None) == before
    assert [row['id'] for row in chain.entries()] == (['before'] if existing else [])


def test_nested_read_preserves_current_publication(home):
    target = state_path('distill', 'retained.dat')
    target.write_bytes(b'before')
    with ledger.failure_writer():
        ledger.retain_before_write(target, after=b'after')
        target.write_bytes(b'after')
        with ledger.failure_writer(reading=True):
            assert Library().list() == []
            assert target.read_bytes() == b'after'
            assert ledger._intent_path().exists()
    assert target.read_bytes() == b'after'
    assert not ledger._intent_path().exists()


def test_library_list_recovers_intent_once_with_referenced_records(tmp_path, monkeypatch):
    frozen = runtime.disk.__wrapped__(monkeypatch, tmp_path)
    first = runtime.admit(runtime.method(), frozen)
    second = runtime.admit(runtime.method(template='weakening.v1'), frozen)
    recover, calls = ledger._recover_intent, []

    def counted():
        calls.append(True)
        return recover()

    monkeypatch.setattr(ledger, '_recover_intent', counted)
    entries = Library().list()
    assert {entry.entry_id for entry in entries} == {first.entry_id, second.entry_id}
    assert calls == [True]


def test_library_list_checks_new_pending_intent_in_next_transaction(tmp_path, monkeypatch):
    frozen = runtime.disk.__wrapped__(monkeypatch, tmp_path)
    entry = runtime.admit(runtime.method(), frozen)
    library = Library()
    assert library.list() == [entry]
    plant([undo('../outside.dat')])
    pending = ledger._intent_path().read_bytes()
    with pytest.raises(ledger.PartialAuthorityWrite, match='authority_intent.json'):
        library.list()
    assert ledger._intent_path().read_bytes() == pending


def test_commit_marker_error_reverts_before_undoing_in_home_targets(home, monkeypatch):
    target = home / 'retained.dat'
    target.write_bytes(b'before')
    replace = os.replace
    hit = []

    def fail_after_marker(source, destination):
        committed = json.loads(source.read_text(encoding='utf-8'))['body']['committed']
        replace(source, destination)
        if committed and not hit:
            hit.append(True)
            raise OSError('marker replacement failed after changing disk')

    monkeypatch.setattr(os, 'replace', fail_after_marker)
    with pytest.raises(OSError):
        with ledger.failure_writer():
            ledger.retain_before_write(target)
            target.write_bytes(b'after')
    assert hit
    assert target.read_bytes() == b'before'
    assert not ledger._intent_path().exists()


ADMISSION_CHILD = r'''
import os
from pathlib import Path
import sys
import pytest
from g6.core.ledger import ChainLedger
from g6.distill.library import Library
from tests.distill.test_acceptance_gate import rig
patch = pytest.MonkeyPatch()
gate, candidate, _, frozen, _ = rig.__wrapped__(patch, Path(sys.argv[1]))
append = ChainLedger.append
def crash(self, kind, ident, body):
    result = append(self, kind, ident, body)
    if kind == 'library':
        os._exit(73)
    return result
ChainLedger.append = crash
gate.decide(candidate, Library(), frozen.registration_sha256)
raise AssertionError('library publication boundary was not reached')
'''


@pytest.mark.parametrize('relocation', ['moved', 'copied'])
def test_relocated_home_undoes_its_own_first_admission(tmp_path, monkeypatch, relocation):
    root = tmp_path / 'original'
    env = dict(os.environ, G6_CLAUDE=r'C:\nonexistent\g6-no-host.exe',
               G6_CODEX=r'C:\nonexistent\g6-no-host.exe', PYTHONDONTWRITEBYTECODE='1')
    child = subprocess.run([sys.executable, '-c', ADMISSION_CHILD, str(root)], env=env,
                           capture_output=True, text=True, timeout=180)
    assert child.returncode == 73, child.stdout + child.stderr
    original = root / 'home'
    intent = original / 'distill' / 'authority_intent.json'
    rows = json.loads(intent.read_text(encoding='utf-8'))['body']['writes']
    original_bytes = {original / row['path']: (original / row['path']).read_bytes()
                      if (original / row['path']).exists() else None for row in rows}
    original_bytes[intent] = intent.read_bytes()
    relocated = tmp_path / relocation / 'home'
    relocated.parent.mkdir()
    if relocation == 'moved':
        original.rename(relocated)
    else:
        shutil.copytree(original, relocated)
    monkeypatch.setenv('G6_HOME', str(relocated))
    assert Library().list() == []
    certificates = state_path('distill', 'acceptance_gate_reports').glob('*.json')
    assert not any(json.loads(p.read_text(encoding='utf-8')).get('authority') == 'A1' for p in certificates)
    assert not [row for row in ChainLedger(state_path('distill', 'acceptance_gate.jsonl')).entries()
                if row['kind'] == 'decision' and row['body']['verdict'] == 'admit_probation']
    assert not ledger._intent_path().exists()
    if relocation == 'copied':
        assert {path: path.read_bytes() if path.exists() else None for path in original_bytes} == original_bytes


@pytest.mark.parametrize('boundary', ['snapshot', 'historical', 'candidate', 'execution', 'report', 'last'])
def test_pending_undo_at_every_review_read_keeps_patch_review_going(tmp_path, monkeypatch, boundary):
    frozen = runtime.disk.__wrapped__(monkeypatch, tmp_path)
    entry = runtime.admit(runtime.method(), frozen)

    def poison():
        ledger._intent_path().write_text('{"body":', encoding='utf-8')

    if boundary == 'snapshot':
        poison()
    elif boundary in {'historical', 'candidate'}:
        factory = review.Stores

        def stores():
            poison()
            return factory()

        monkeypatch.setattr(review, 'Stores', stores)
        if boundary == 'historical':
            check = review.check
            monkeypatch.setattr(review, 'check', lambda before, after, **kwargs:
                                check(before, after, entry_id=entry.entry_id, **kwargs))
    elif boundary == 'execution':
        run = gate._run

        def execute(*args, **kwargs):
            poison()
            return run(*args, **kwargs)

        monkeypatch.setattr(gate, '_run', execute)
    else:
        writer = review.failure_writer
        calls = []

        def pending_before_lock(*, reading=False):
            if reading:
                calls.append(True)
                if len(calls) == (1 if boundary == 'report' else 2):
                    poison()
            return writer(reading=reading)

        # A different writer can leave an intent before lock acquisition,
        # never between an outer transaction and its nested require_current.
        monkeypatch.setattr(review, 'failure_writer', pending_before_lock)
    reasons, runs = [], []
    review.apply_review(runtime.BEFORE, runtime.AFTER, reasons, runs)
    assert runs == []
    assert [reason['code'] for reason in reasons] == ['library_unavailable']
    assert 'authority_intent.json' in reasons[0]['detail']
    assert ledger._intent_path().exists()


def test_learned_probe_notes_unavailable_library(home, tmp_path, monkeypatch):
    from g6.workforce import bench

    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'test_value.py').write_text('def test_value():\n assert True\n', encoding='utf-8')
    monkeypatch.setattr(bench, '_repo', lambda *args: repo)
    plant([undo('../outside.dat')])
    params = {'repo': 'example', 'files': {'test_value.py': 'def test_value():\n pass\n'},
              'regression': {'learned': {'entry_id': 'a' * 64}}}
    result = review.check_learned(params)
    assert (result['status'], result['authority'], result['library']) == ('not_run', 'A0', [])
    assert result['code'] == 'library_unavailable'
    assert 'authority_intent.json' in result['reason']


def test_pending_undo_at_field_status_read_prevents_append(tmp_path, monkeypatch):
    frozen = runtime.disk.__wrapped__(monkeypatch, tmp_path)
    runtime.admit(runtime.method(), frozen)
    writer, pending = review.failure_writer, []

    def pending_before_lock(*, reading=False):
        if not reading and not pending:
            plant([undo('../outside.dat')])
            pending.append(ledger._intent_path().read_bytes())
        return writer(reading=reading)

    monkeypatch.setattr(review, 'failure_writer', pending_before_lock)
    reasons, runs = [], []
    review.apply_review(runtime.BEFORE, runtime.AFTER, reasons, runs)
    assert runs == []
    assert [reason['code'] for reason in reasons] == ['library_unavailable']
    assert 'authority_intent.json' in reasons[0]['detail']
    assert ledger._intent_path().read_bytes() == pending[0]
    assert not ChainLedger(state_path('distill', 'field_fires_ledger.jsonl')).entries()


def test_pending_recovery_at_field_read_stops_later_methods(tmp_path, monkeypatch):
    frozen = runtime.disk.__wrapped__(monkeypatch, tmp_path)
    runtime.admit(runtime.method(), frozen)
    runtime.admit(runtime.method(template='weakening.v1'), frozen)
    target = state_path('distill', 'retained.dat')
    target.write_bytes(b'half-published')
    writer, opened, run = review.failure_writer, Path.open, gate._run
    attempts, ran, pending = [], [], []

    def pending_before_lock(*, reading=False):
        if not reading and not pending:
            plant([undo('distill/retained.dat', b'before')])
            pending.append(ledger._intent_path().read_bytes())
        return writer(reading=reading)

    def fail_once(path, mode='r', *args, **kwargs):
        if path == target and mode == 'wb' and not attempts:
            attempts.append(True)
            raise OSError('transient restore failure')
        return opened(path, mode, *args, **kwargs)

    def execute(*args, **kwargs):
        ran.append(args[0].candidate_id)
        return run(*args, **kwargs)

    monkeypatch.setattr(review, 'failure_writer', pending_before_lock)
    monkeypatch.setattr(Path, 'open', fail_once)
    monkeypatch.setattr(gate, '_run', execute)
    reasons, runs = [], []
    review.apply_review(runtime.BEFORE, runtime.AFTER, reasons, runs)
    assert runs == []
    assert [reason['code'] for reason in reasons] == ['library_unavailable']
    assert 'authority_intent.json' in reasons[0]['detail']
    assert attempts == [True] and len(ran) == 1
    assert ledger._intent_path().read_bytes() == pending[0]
    assert target.read_bytes() == b'half-published'
    assert not ChainLedger(state_path('distill', 'field_fires_ledger.jsonl')).entries()
    # A later review may recover it; the review that saw it stays unavailable.
    assert Library().list()
    assert target.read_bytes() == b'before'
    assert not ledger._intent_path().exists()


@pytest.mark.parametrize('entry_id', ['', 'a' * 64], ids=['no-entry', 'invalid-repo'])
def test_learned_probe_checks_pending_undo_when_inputs_prevent_a_run(home, entry_id):
    plant([undo('../outside.dat')])
    pending = ledger._intent_path().read_bytes()
    params = {'repo': 'invalid/name', 'files': {}, 'regression': {'learned': {'entry_id': entry_id}}}
    result = review.check_learned(params)
    assert (result['status'], result['authority'], result['library']) == ('not_run', 'A0', [])
    assert result['code'] == 'library_unavailable'
    assert 'authority_intent.json' in result['reason']
    assert result['counterexamples'] == []
    assert ledger._intent_path().read_bytes() == pending


def fail_cleanup(monkeypatch):
    unlink, hit = Path.unlink, []

    def remove(path, *args, **kwargs):
        if path.name == 'authority_intent.json' and not hit and path.exists() \
                and json.loads(path.read_text(encoding='utf-8'))['body']['committed']:
            hit.append(True)
            raise OSError('private cleanup diagnostic')
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', remove)
    return hit


@pytest.mark.parametrize('action', ['restore', 'withdraw', 'demote'])
def test_dialog_reply_keeps_committed_decision_when_cleanup_fails(tmp_path, monkeypatch, action):
    frozen = runtime.disk.__wrapped__(monkeypatch, tmp_path)
    entry = runtime.admit(runtime.method(), frozen)
    if action == 'restore':
        entry = gate._change(entry, status='held', authority='A0', reason='hold')
    Rectifier().reconcile()
    tools = handlers(monkeypatch)
    hit = fail_cleanup(monkeypatch)
    reply = asyncio.run(tools['g6_library_decide'](entry.entry_id, action, 'private owner reason', object()))
    assert hit
    verdict = 'lift_hold' if action == 'restore' else action
    assert reply['verdict'] == verdict
    assert reply['publication_committed'] is True and reply['cleanup_pending'] is True
    assert 'cleanup' in reply['cleanup_note'].lower()
    assert 'before any state change' not in str(reply)
    assert 'private owner reason' not in str(reply) and 'private cleanup diagnostic' not in str(reply)
    [current] = Library().list()
    [decision] = ChainLedger(state_path('distill', 'acceptance_gate.jsonl')).entries()
    assert decision['body']['verdict'] == verdict and decision['body']['entry_id'] == current.entry_id


def test_field_reply_keeps_committed_fire_when_cleanup_fails(tmp_path, monkeypatch):
    frozen = runtime.disk.__wrapped__(monkeypatch, tmp_path)
    runtime.admit(runtime.method(), frozen)
    hit = fail_cleanup(monkeypatch)
    [reply] = review.check(runtime.BEFORE, runtime.AFTER)
    assert hit
    assert reply['field_recorded'] is True
    assert reply['publication_committed'] is True
    assert 'cleanup' in reply['field_note'].lower()
    assert 'no field evidence was added' not in reply['field_note']
    assert len(ChainLedger(state_path('distill', 'field_fires_ledger.jsonl')).entries()) == 1
    assert not ledger._intent_path().exists()


@pytest.mark.parametrize('status', ['held', 'withdrawn'])
def test_field_status_change_before_writer_never_claims_a_committed_fire(tmp_path, monkeypatch, status):
    frozen = runtime.disk.__wrapped__(monkeypatch, tmp_path)
    entry = runtime.admit(runtime.method(), frozen)
    ready, release = Event(), Event()
    record = review.record_fire

    def paused(*args):
        ready.set()
        assert release.wait(10), 'field writer was not released'
        return record(*args)

    monkeypatch.setattr(review, 'record_fire', paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(review.check, runtime.BEFORE, runtime.AFTER)
        try:
            assert ready.wait(10), 'review never reached the field writer'
            pool.submit(runtime.inactive, entry, status).result(timeout=5)
            hit = fail_cleanup(monkeypatch)
        finally:
            release.set()
        [reply] = running.result(timeout=10)
    assert (reply['authority'], reply['verdict'], reply['field_recorded']) == ('A0', 'abstain', False)
    assert reply['code'] == f'G6_E_DISTILL_METHOD_{status.upper()}_DURING_RUN'
    assert not reply.get('publication_committed')
    assert 'field_note' not in reply
    assert not hit, 'a refused fire must not reach committed-intent cleanup'
    assert not ChainLedger(state_path('distill', 'field_fires_ledger.jsonl')).entries()
    assert Library().list()[0].status == status
    assert not ledger._intent_path().exists()


def test_partial_reply_does_not_claim_a_prechange_refusal(home):
    reply = distill._refusal(ledger.PartialAuthorityWrite((ledger._intent_path(),)))
    assert 'before any state change' not in reply['rollback_or_containment']
    prechange = distill._refusal(ValueError('input'))
    assert 'before any state change' in prechange['rollback_or_containment']


def test_old_committed_cleanup_cannot_supply_a_new_decision(home, monkeypatch):
    target = state_path('distill', 'retained.dat')
    target.write_bytes(b'committed')
    plant([undo('distill/retained.dat', b'before')], committed=True)
    fail_cleanup(monkeypatch)
    reached = []
    reply = distill._call(distill._owner_decision, lambda: reached.append(True))
    assert not reached
    assert reply['publication_committed'] is True
    assert reply['warning_card']['code'] == ledger.PartialAuthorityWrite.code
    assert 'verdict' not in reply
    assert target.read_bytes() == b'committed'


def test_old_committed_cleanup_cannot_report_a_new_field_fire(tmp_path, monkeypatch):
    frozen = runtime.disk.__wrapped__(monkeypatch, tmp_path)
    runtime.admit(runtime.method(), frozen)
    target = state_path('distill', 'retained.dat')
    target.write_bytes(b'committed')
    writer, planted = review.failure_writer, []

    def old_cleanup(*, reading=False):
        if not reading and not planted:
            planted.append(True)
            plant([undo('distill/retained.dat', b'before')], committed=True)
            fail_cleanup(monkeypatch)
        return writer(reading=reading)

    monkeypatch.setattr(review, 'failure_writer', old_cleanup)
    [reply] = review.check(runtime.BEFORE, runtime.AFTER)
    assert reply.get('library_unavailable') is True
    assert reply['field_recorded'] is False
    assert not ChainLedger(state_path('distill', 'field_fires_ledger.jsonl')).entries()
    assert target.read_bytes() == b'committed'


@pytest.mark.parametrize('action', ['restore', 'withdraw', 'demote'])
def test_owner_refusal_still_rolls_back_after_a_caught_record_error(tmp_path, monkeypatch, action):
    frozen = runtime.disk.__wrapped__(monkeypatch, tmp_path)
    entry = runtime.admit(runtime.method(), frozen)
    if action == 'restore':
        entry = gate._change(entry, status='held', authority='A0', reason='hold')
    Rectifier().reconcile()
    tools = handlers(monkeypatch)
    record = gate._record

    def invalid_receipt(*args, **kwargs):
        record(*args, **kwargs)
        raise ValueError('private receipt diagnostic')

    monkeypatch.setattr(gate, '_record', invalid_receipt)
    reply = asyncio.run(tools['g6_library_decide'](entry.entry_id, action, 'decision', object()))
    assert reply['decision']['verdict'] == 'refuse'
    assert Library().list() == [entry]
    assert not ChainLedger(state_path('distill', 'acceptance_gate.jsonl')).entries()
    assert not ledger._intent_path().exists()
    assert 'private receipt diagnostic' not in str(reply)


def test_unreadable_intent_reply_survives_refused_library_diagnostic(home, monkeypatch):
    from g6.core.storage import PathRefused

    plant([undo('../outside.dat')])
    pending = ledger._intent_path().read_bytes()
    resolver = ledger.state_path

    def refused(*parts):
        if Path(*parts) == Path('distill/library.jsonl'):
            raise PathRefused('outside the home', outside=True)
        return resolver(*parts)

    monkeypatch.setattr(ledger, 'state_path', refused)

    def read():
        with ledger.failure_writer(reading=True):
            return {'read': True}

    reply = distill._call(read)
    assert reply['warning_card']['code'] == ledger.PartialAuthorityWrite.code
    assert 'library unreadable/authority unknown' in reply['durable_state']
    assert 'authority_intent.json' in str(reply['durable_state'])
    assert ledger._intent_path().read_bytes() == pending


@pytest.mark.parametrize('sibling', ['inside', 'outside'])
def test_repair1_undo_replaces_only_the_target_name(home, tmp_path, sibling):
    other = (home if sibling == 'inside' else tmp_path) / 'other.dat'
    other.write_bytes(b'keep the other name')
    target = state_path('distill', 'retained.dat')
    os.link(other, target)
    try:
        plant([undo('distill/retained.dat', b'undo bytes')])
        assert distill._call(surface.library) == {'entries': []}
        assert target.read_bytes() == b'undo bytes'
        assert other.read_bytes() == b'keep the other name'
        assert not os.path.samefile(target, other)
        assert not ledger._intent_path().exists()
    finally:
        target.unlink(missing_ok=True)


def test_repair1_failed_undo_replace_keeps_intent_and_target(home, monkeypatch):
    target = state_path('distill', 'retained.dat')
    target.write_bytes(b'half-published')
    plant([undo('distill/retained.dat', b'before')])
    pending = ledger._intent_path().read_bytes()
    replace, attempts = os.replace, []

    def refuse(source, destination):
        if Path(destination) == target:
            attempts.append(True)
            assert Path(source).parent == target.parent
            assert Path(source).read_bytes() == b'before'
            raise PermissionError('undo replacement refused')
        return replace(source, destination)

    monkeypatch.setattr(os, 'replace', refuse)
    reply = distill._call(surface.library)
    assert reply['warning_card']['code'] == ledger.PartialAuthorityWrite.code
    assert attempts == [True]
    assert target.read_bytes() == b'half-published'
    assert ledger._intent_path().read_bytes() == pending
    assert {p.name for p in target.parent.iterdir()} == {
        'retained.dat', 'authority_intent.json', 'failure_records.lock'}


@pytest.mark.parametrize('door', ['owner', 'failure_record', 'library'])
def test_repair1_earlier_cleanup_never_claims_this_request_committed(home, monkeypatch, door):
    target = state_path('distill', 'retained.dat')
    target.write_bytes(b'committed')
    plant([undo('distill/retained.dat', b'before')], committed=True)
    pending = ledger._intent_path().read_bytes()
    fail_cleanup(monkeypatch)
    reached = []
    if door == 'owner':
        reply = distill._call(distill._owner_decision, lambda: reached.append(True))
    elif door == 'failure_record':
        reply = handlers(monkeypatch)['g6_failure_record']('missed_defect', 'test_weakening', '{}', 'reported')
    else:
        reply = distill._call(surface.library)
    assert reply['publication_committed'] is False
    assert reply['earlier_publication_committed'] is True
    assert reply['completion_state'] == 'blocked-escalated'
    assert reply['warning_card']['code'] == ledger.PartialAuthorityWrite.code
    words = reply['rollback_or_containment'].lower()
    assert 'earlier publication committed' in words and 'cleanup' in words
    assert 'this request made no change' in words
    assert not reached and 'verdict' not in reply
    assert target.read_bytes() == b'committed'
    assert ledger._intent_path().read_bytes() == pending
    assert not state_path('distill', 'failure_records.jsonl').exists()


def repair1_cleanup_failures(monkeypatch, *, times=1, target=None):
    unlink, failed = Path.unlink, []

    def remove(path, *args, **kwargs):
        if path == ledger._intent_path() and path.exists() and len(failed) < times:
            body = json.loads(path.read_text(encoding='utf-8'))['body']
            names = [row['path'] for row in body['writes']]
            if body['committed'] and (target is None or target in names):
                failed.append(names)
                raise PermissionError('private cleanup diagnostic')
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', remove)
    return failed


@pytest.mark.parametrize('boundary', ['activation', 'reservation'])
def test_repair1_restore_reply_and_reservation_retry(tmp_path, monkeypatch, boundary):
    from g6.distill import records as r
    from g6.mcp import consent
    from g6.mcp.distill_backend import Service
    from tests.distill import test_acceptance_gate as acceptance
    from tests.distill.test_records import bodies

    _, candidate, _, frozen, _ = acceptance.rig.__wrapped__(monkeypatch, tmp_path)
    withdrawn = r.LibraryEntry(**(bodies()[4][1] | {
        'candidate_id': candidate.candidate_id, 'status': 'withdrawn', 'authority': 'A0',
        'regression_suite': [{'case_ref': frozen.acceptance[0].case.case_id, 'verdict': 'concern'}]}))
    ledger.Stores().for_model(r.LibraryEntry).put(withdrawn)
    tools = {}

    class Server:
        def tool(self):
            return lambda fn: tools.setdefault(fn.__name__, fn)

    async def accept(*args):
        return 'accept', {'confirm': 'change', 'name': 'Owner'}, ''

    monkeypatch.setattr(consent, 'elicit', accept)
    distill.register(Server(), object, None, backend=Service(registration=frozen.registration_sha256))
    failed = repair1_cleanup_failures(monkeypatch, target=(
        'distill/library.jsonl' if boundary == 'activation' else 'distill/acceptance_gate.jsonl'))
    reply = asyncio.run(tools['g6_library_decide'](withdrawn.entry_id, 'restore', 'restore it', object()))
    assert len(failed) == 1
    if boundary == 'activation':
        assert reply['verdict'] == 'admit_probation'
        assert reply['publication_committed'] is True and reply['cleanup_pending'] is True
        assert (Library().list()[0].status, Library().list()[0].authority) == ('probation', 'A1')
    else:
        assert Library().list() == [withdrawn]
        rows = gate._journal().entries()
        pending = {row['id'] for row in rows if row['kind'] == 'reserve'}
        pending -= {row['body']['reservation'] for row in rows if row['kind'] == 'decision'}
        assert pending == set()
        retry = asyncio.run(tools['g6_library_decide'](withdrawn.entry_id, 'restore', 'restore it', object()))
        assert retry['verdict'] == 'admit_probation'
        assert Library().list()[0].status == 'probation'


def test_repair1_real_unfinished_evaluation_still_requires_owner_recovery(home):
    with ledger.failure_writer():
        gate._append(gate._journal(), 'reserve', {'registration': 'a' * 64, 'candidate_id': 'b' * 64})
    with pytest.raises(ValueError, match='unfinished evaluation requires owner recovery'):
        gate._look_available('a' * 64)


@pytest.mark.parametrize('door', ['check', 'apply_review'])
def test_repair1_committed_fire_survives_unavailable_final_read(tmp_path, monkeypatch, door):
    frozen = runtime.disk.__wrapped__(monkeypatch, tmp_path)
    runtime.admit(runtime.method(), frozen)
    failed = repair1_cleanup_failures(monkeypatch, times=2)
    if door == 'check':
        [found] = review.check(runtime.BEFORE, runtime.AFTER)
    else:
        reasons, runs = [], []
        review.apply_review(runtime.BEFORE, runtime.AFTER, reasons, runs)
        assert len(runs) == 1
        found = runs[0]['library']
        assert any(reason['code'] == 'library_unavailable' for reason in reasons)
    assert len(failed) == 2
    assert found['field_recorded'] is True and found['publication_committed'] is True
    assert 'cleanup' in found['field_note'].lower()
    assert found['authority'] == 'A0' and found['verdict'] == 'abstain'
    assert len(ChainLedger(state_path('distill', 'field_fires_ledger.jsonl')).entries()) == 1


@pytest.mark.parametrize('door', ['apply_review', 'learned'])
def test_repair1_deep_intent_is_unavailable_without_ending_review(home, door):
    ledger._intent_path().write_text('[' * 200_000, encoding='utf-8')
    if door == 'apply_review':
        reasons, runs = [], []
        review.apply_review(runtime.BEFORE, runtime.AFTER, reasons, runs)
        assert runs == []
        assert [reason['code'] for reason in reasons] == ['library_unavailable']
        assert 'authority_intent.json' in reasons[0]['detail']
    else:
        reply = review.check_learned({'repo': 'invalid/name', 'files': {},
                                      'regression': {'learned': {'entry_id': ''}}})
        assert (reply['status'], reply['authority'], reply['library']) == ('not_run', 'A0', [])
        assert reply['code'] == 'library_unavailable'
        assert 'authority_intent.json' in reply['reason']


def test_repair1_atomic_undo_keeps_commit_marker_error_recovery(home, monkeypatch):
    target = state_path('distill', 'retained.dat')
    target.write_bytes(b'before')
    replace, marked = os.replace, []

    def fail_marker(source, destination):
        marker = (Path(destination) == ledger._intent_path()
                  and json.loads(Path(source).read_text(encoding='utf-8'))['body']['committed'])
        replace(source, destination)
        if marker and not marked:
            marked.append(True)
            raise OSError('commit marker changed before error')

    monkeypatch.setattr(os, 'replace', fail_marker)
    with pytest.raises(OSError, match='commit marker changed before error'):
        with ledger.failure_writer():
            ledger.retain_before_write(target, after=b'after')
            target.write_bytes(b'after')
    assert marked == [True]
    assert target.read_bytes() == b'before'
    assert not ledger._intent_path().exists()


@pytest.mark.parametrize('boundary', ['assessment', 'receipt'])
def test_repair1_interrupted_evaluation_closes_its_own_reserve(tmp_path, monkeypatch, boundary):
    from tests.distill import test_acceptance_gate as acceptance

    _, candidate, _, frozen, _ = acceptance.rig.__wrapped__(monkeypatch, tmp_path)
    name = '_assess' if boundary == 'assessment' else '_record'
    original, interrupted = getattr(gate, name), []

    def fail_once(*args, **kwargs):
        if not interrupted:
            interrupted.append(True)
            raise ledger.PartialAuthorityWrite((state_path('distill', 'acceptance_gate.jsonl'),))
        return original(*args, **kwargs)

    monkeypatch.setattr(gate, name, fail_once)
    with pytest.raises(ledger.PartialAuthorityWrite):
        gate.decide(candidate, Library(), frozen.registration_sha256)
    rows = gate._journal().entries()
    [reservation] = [row['id'] for row in rows if row['kind'] == 'reserve']
    assert any(row['kind'] == 'decision' and row['body']['reservation'] == reservation
               and row['body']['verdict'] == 'refuse' for row in rows)
    assert Library().list() == []
    assert gate.decide(candidate, Library(), frozen.registration_sha256).verdict == 'admit_probation'


def test_repair1_failed_atomic_recovery_stops_later_library_methods(tmp_path, monkeypatch):
    frozen = runtime.disk.__wrapped__(monkeypatch, tmp_path)
    runtime.admit(runtime.method(), frozen)
    runtime.admit(runtime.method(template='weakening.v1'), frozen)
    target = state_path('distill', 'retained.dat')
    target.write_bytes(b'half-published')
    writer, replace, run = review.failure_writer, os.replace, gate._run
    attempts, ran, pending = [], [], []

    def pending_before_lock(*, reading=False):
        if not reading and not pending:
            plant([undo('distill/retained.dat', b'before')])
            pending.append(ledger._intent_path().read_bytes())
        return writer(reading=reading)

    def fail_once(source, destination):
        if Path(destination) == target and not attempts:
            attempts.append(True)
            raise PermissionError('transient undo replacement failure')
        return replace(source, destination)

    def execute(*args, **kwargs):
        ran.append(args[0].candidate_id)
        return run(*args, **kwargs)

    monkeypatch.setattr(review, 'failure_writer', pending_before_lock)
    monkeypatch.setattr(os, 'replace', fail_once)
    monkeypatch.setattr(gate, '_run', execute)
    reasons, runs = [], []
    review.apply_review(runtime.BEFORE, runtime.AFTER, reasons, runs)
    assert runs == []
    assert [reason['code'] for reason in reasons] == ['library_unavailable']
    assert 'authority_intent.json' in reasons[0]['detail']
    assert attempts == [True] and len(ran) == 1
    assert ledger._intent_path().read_bytes() == pending[0]
    assert target.read_bytes() == b'half-published'
    assert not ChainLedger(state_path('distill', 'field_fires_ledger.jsonl')).entries()
    assert Library().list()
    assert target.read_bytes() == b'before'
    assert not ledger._intent_path().exists()
