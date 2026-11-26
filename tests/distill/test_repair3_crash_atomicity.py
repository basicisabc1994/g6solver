"""Kill real writers at every durable boundary, then read through a fresh process.

Removing durable intent/recovery makes the certificate/library/decision assertions
fail: a terminated process never executes the ordinary exception rollback.
"""
import json
import os
import subprocess
import sys

import pytest


CHILD = r'''
import asyncio
import json
import os
from pathlib import Path
import sys
import pytest
from g6.core.ledger import ChainLedger, digest
from g6.core.storage import state_path
from g6.distill import acceptance_gate as gate, records as r
from g6.distill.ledger import Stores, failure_writer
from g6.distill.library import Library
from g6.distill.rectify_gate import Rectifier
from g6.harness import library_review_gate as review
from g6.mcp import distill
from tests.distill import test_acceptance_gate as acceptance
from tests.distill import test_recurrence_hold as recurrence
from tests.distill import test_runtime_identity_boundaries as runtime
from tests.distill.test_repair2_atomicity import handlers
from tests.distill.test_records import bodies

root, action, mode, stop_at = Path(sys.argv[1]), sys.argv[2], sys.argv[3], int(sys.argv[4])
os.environ['G6_HOME'] = str(root / 'home')

def observe():
    # Resolve the transaction before trusting any of its records, in a fresh process.
    with failure_writer(reading=True):
        entries = Library().list()
        def rows(name):
            return ChainLedger(state_path('distill', name + '.jsonl')).entries()
        decisions = [row['body'] for row in rows('acceptance_gate')
                     if row['kind'] == 'decision' and row['body']['verdict'] in
                     {'admit_probation', 'lift_hold', 'withdraw', 'demote'}]
        certificates = [json.loads(p.read_text(encoding='utf-8')) for p in
                        state_path('distill', 'acceptance_gate_reports').glob('*.json')]
        return {'entries': [e.model_dump(mode='json') for e in entries],
                'decisions': decisions,
                'certificates': [c for c in certificates if c.get('authority') == 'A1'],
                'rectifications': rows('rectification'), 'modes': rows('failure_mode'),
                'fires': rows('field_fires_ledger')}

if mode == 'read':
    before = json.loads((root / 'before.json').read_text(encoding='utf-8'))
    observed = observe()
    if action in {'admission', 'restore'}:
        n = len(observed['certificates'])
        active = [e for e in observed['entries'] if e['status'] == 'probation']
        assert (n, len(active), len(observed['decisions'])) in {(0, 0, 0), (1, 1, 1)}, observed
    elif action in {'lift_hold', 'independent_release', 'withdraw', 'withdraw_cascade', 'demote'}:
        if observed['entries'] != before['entries']:
            assert len(observed['decisions']) == 1, observed
            assert observed['decisions'][0]['entry_id'] == observed['entries'][0]['entry_id']
            if action == 'withdraw_cascade':
                assert [(e['status'], e['authority']) for e in observed['entries']] == [
                    ('withdrawn', 'A0'), ('demoted', 'A0')], observed
        else:
            assert observed == before, observed
    elif action in {'reconcile', 'reconcile_independent'}:
        changed = [observed[k] != before[k] for k in ('entries', 'rectifications', 'modes')]
        assert changed in ([False, False, False], [True, True, True]), observed
    else:
        assert len(observed['fires']) in {0, 1}, observed
    # A later product reply must describe the recovered library, including a held
    # method remaining unavailable. A fresh field append must not hit a stale lock.
    if action in {'lift_hold', 'field_fire'}:
        reply = review.check(runtime.BEFORE, runtime.AFTER)
        if observed['entries'][0]['status'] == 'held':
            assert reply == [], reply
        else:
            assert reply[0]['authority'] == observed['entries'][0]['authority'], reply
            assert reply[0]['field_recorded'] is True, reply
    else:
        from g6.distill.surface import library
        reply = distill._call(library)
        assert [e['status'] for e in reply['entries']] == [e['status'] for e in observed['entries']], reply
        assert [e['authority'] for e in reply['entries']] == [e['authority'] for e in observed['entries']], reply
    print(json.dumps(observed))
    sys.exit(0)

patch = pytest.MonkeyPatch()
if action in {'admission', 'restore'}:
    _, candidate, _, frozen, _ = acceptance.rig.__wrapped__(patch, root)
    if action == 'restore':
        entry = r.LibraryEntry(**(bodies()[4][1] | {
            'candidate_id': candidate.candidate_id, 'status': 'withdrawn', 'authority': 'A0',
            'regression_suite': [{'case_ref': frozen.acceptance[0].case.case_id, 'verdict': 'concern'}]}))
        Stores().for_model(r.LibraryEntry).put(entry)
    call = (lambda: gate.decide(candidate, Library(), frozen.registration_sha256)) if action == 'admission' else (
        lambda: gate.restore(entry.entry_id, Library(), frozen.registration_sha256))
elif action in {'reconcile', 'reconcile_independent', 'independent_release'}:
    entry, rows, _ = recurrence.removal()
    again = r.FailureRecord(**(rows[0].model_dump(exclude={'record_id'}) | {
        'observed_by': 'independent' if action == 'reconcile_independent' else 'self',
        'evidence': [], 'source': 'experiment' if action == 'reconcile_independent' else 'person',
        'case_ref': digest('crash recurrence'),
        'observed_at': '2026-11-22'}))
    Stores().for_model(r.FailureRecord).put(again)
    if action == 'independent_release':
        removed = Stores().for_model(r.Rectification).list()[-1]
        Rectifier().reconcile()
        held = Library().list()[0]
        check = r.RemovalCheck(**(removed.pinning.model_dump() | {
            'replayed': [row.record_id for row in [*rows, again]], 'receipt_sha256': digest('fresh replay')}))
        def call():
            return Rectifier().release_hold(held.entry_id, [check])
    else:
        call = Rectifier().reconcile
else:
    frozen = runtime.disk.__wrapped__(patch, root)
    entry = runtime.admit(runtime.method(), frozen)
    if action == 'withdraw_cascade':
        dependent = r.VerifierCandidate(**(runtime.method().model_dump(exclude={'candidate_id'}) | {
            'form': 'reuse', 'body': entry.entry_id, 'lineage': [entry.entry_id]}))
        runtime.admit(dependent, frozen)
    if action == 'lift_hold':
        entry = gate._change(entry, status='held', authority='A0', reason='hold')
    Rectifier().reconcile()
    tools = handlers(patch)
    if action == 'field_fire':
        def call():
            return review.check(runtime.BEFORE, runtime.AFTER)
    else:
        wire_action = ('restore' if action == 'lift_hold' else
                       'withdraw' if action == 'withdraw_cascade' else action)
        def call():
            return asyncio.run(tools['g6_library_decide'](entry.entry_id, wire_action, 'owner decision', object()))

root.mkdir(parents=True, exist_ok=True)
(root / 'before.json').write_text(json.dumps(observe()), encoding='utf-8')
count = 0
def boundary():
    global count
    count += 1
    if count == stop_at:
        os._exit(73)

sync, replace, unlink = os.fsync, os.replace, Path.unlink
def fsync(fd):
    sync(fd)
    boundary()
def rename(source, target):
    replace(source, target)
    boundary()
def remove(path, *args, **kwargs):
    unlink(path, *args, **kwargs)
    boundary()
os.fsync, os.replace, Path.unlink = fsync, rename, remove
answer = call()
assert not isinstance(answer, dict) or answer.get('ok') is not False, answer
if action in {'admission', 'restore'}:
    assert answer.verdict == 'admit_probation', answer
print(json.dumps({'steps': count}))
'''


def child(root, action, mode, stop_at=0):
    env = dict(os.environ, G6_HOME=str(root / 'home'),
               G6_CLAUDE=r'C:\nonexistent\g6-no-host.exe',
               G6_CODEX=r'C:\nonexistent\g6-no-host.exe', PYTHONDONTWRITEBYTECODE='1')
    return subprocess.run([sys.executable, '-c', CHILD, str(root), action, mode, str(stop_at)],
                          env=env, capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize('action', ['admission', 'restore', 'lift_hold', 'withdraw', 'demote',
                                   'withdraw_cascade', 'reconcile', 'reconcile_independent',
                                   'independent_release', 'field_fire'])
def test_kill_after_every_durable_step_then_read_and_reply(tmp_path, action):
    baseline = child(tmp_path / 'baseline', action, 'write')
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    steps = json.loads(baseline.stdout)['steps']
    assert steps > 0
    for step in range(1, steps + 1):
        root = tmp_path / str(step)
        killed = child(root, action, 'write', step)
        assert killed.returncode == 73, (step, killed.stdout, killed.stderr)
        fresh = child(root, action, 'read')
        assert fresh.returncode == 0, (step, fresh.stdout, fresh.stderr)


def test_all_store_readers_recover_before_reading_authority(tmp_path, monkeypatch):
    """Rectification/FailureMode readers must not need a prior Library.list()."""
    from g6.distill import records as r
    from g6.distill.ledger import Stores
    # The real-process class above kills reconcile; this control visits each
    # store first after the same interruption of its surrounding transaction.
    for model in (r.LibraryEntry, r.Rectification, r.FailureMode):
        root = tmp_path / model.__name__
        baseline = child(root / 'count', 'reconcile', 'write')
        assert baseline.returncode == 0, baseline.stderr
        # Appended library is preceded by its object and durable intent. Discover
        # every boundary rather than embedding the implementation's step numbers.
        for step in range(1, json.loads(baseline.stdout)['steps'] + 1):
            target = root / str(step)
            killed = child(target, 'reconcile', 'write', step)
            assert killed.returncode == 73, killed.stderr
            monkeypatch.setenv('G6_HOME', str(target / 'home'))
            Stores().for_model(model).list()
            fresh = child(target, 'reconcile', 'read')
            assert fresh.returncode == 0, fresh.stdout + fresh.stderr


def test_pending_undo_blocks_later_reply_until_recovery_succeeds(tmp_path, monkeypatch):
    from pathlib import Path

    from g6.distill import acceptance_gate as gate, surface
    from g6.distill.library import Library
    from g6.mcp import distill
    from tests.distill import test_runtime_identity_boundaries as runtime
    from tests.distill.test_repair2_atomicity import interrupt

    frozen = runtime.disk.__wrapped__(monkeypatch, tmp_path)
    entry = runtime.admit(runtime.method(), frozen)
    hit = interrupt(monkeypatch, 'decision')
    opened = Path.open

    def cannot_restore(path, mode='r', *args, **kwargs):
        if hit and path.name == 'library.jsonl' and mode == 'wb':
            raise OSError('private restore diagnostic')
        return opened(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', cannot_restore)
    refused = distill._call(gate.withdraw, entry.entry_id, Library(), reason='withdraw', person='Owner')
    assert refused['warning_card']['code'] == 'G6_E_DISTILL_PARTIAL_WRITE'
    later = distill._call(surface.library)
    assert later['warning_card']['code'] == 'G6_E_DISTILL_PARTIAL_WRITE'
    assert 'recovery incomplete' in str(later).lower()
    assert 'private restore diagnostic' not in str(later)
    monkeypatch.setattr(Path, 'open', opened)
    recovered = distill._call(surface.library)
    assert recovered['entries'][0]['entry_id'] == entry.entry_id


RECOVERY_CHILD = r'''
import os
from pathlib import Path
import sys
from g6.distill.ledger import failure_writer, retain_before_write

root, mode, stop_at = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
os.environ['G6_HOME'] = str(root)
paths = [root / 'distill' / name for name in ('one.dat', 'two.dat')]
if mode == 'write':
    paths[0].parent.mkdir(parents=True)
    for path in paths:
        path.write_bytes(b'before')
    with failure_writer():
        for path in paths:
            retain_before_write(path)
            with path.open('wb') as stream:
                stream.write(b'after')
                stream.flush()
                os.fsync(stream.fileno())
        os._exit(73)
count = 0
sync, unlink = os.fsync, Path.unlink
def boundary():
    global count
    count += 1
    if count == stop_at:
        os._exit(73)
def fsync(fd):
    sync(fd)
    boundary()
def remove(path, *args, **kwargs):
    unlink(path, *args, **kwargs)
    boundary()
os.fsync, Path.unlink = fsync, remove
with failure_writer(reading=True):
    assert [path.read_bytes() for path in paths] == [b'before', b'before']
print(count)
'''


def test_process_death_during_recovery_is_retryable(tmp_path):
    def run(root, mode, stop_at=0):
        return subprocess.run([sys.executable, '-c', RECOVERY_CHILD, str(root), mode, str(stop_at)],
                              env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
                              capture_output=True, text=True, timeout=30)

    baseline = tmp_path / 'baseline'
    assert run(baseline, 'write').returncode == 73
    recovered = run(baseline, 'read')
    assert recovered.returncode == 0, recovered.stderr
    for step in range(1, int(recovered.stdout) + 1):
        root = tmp_path / str(step)
        assert run(root, 'write').returncode == 73
        assert run(root, 'read', step).returncode == 73
        fresh = run(root, 'read')
        assert fresh.returncode == 0, fresh.stderr


def test_commit_marker_error_reverts_before_undoing_targets(tmp_path, monkeypatch):
    from g6.distill import ledger

    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    target = tmp_path / 'retained.dat'
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


def test_field_recovery_retry_describes_final_disk(tmp_path, monkeypatch):
    from pathlib import Path

    from g6.core.ledger import ChainLedger
    from g6.core.storage import state_path
    from g6.harness import library_review_gate as review
    from tests.distill import test_runtime_identity_boundaries as runtime
    from tests.distill.test_repair2_atomicity import interrupt

    frozen = runtime.disk.__wrapped__(monkeypatch, tmp_path)
    runtime.admit(runtime.method(), frozen)
    hit = interrupt(monkeypatch, 'field_fire')
    unlink = Path.unlink
    failed = []

    def fail_once(path, *args, **kwargs):
        if hit and not failed and path.name == 'field_fires_ledger.jsonl':
            failed.append(True)
            raise OSError('transient recovery failure')
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', fail_once)
    [answer] = review.check(runtime.BEFORE, runtime.AFTER)
    assert failed
    assert not ChainLedger(state_path('distill', 'field_fires_ledger.jsonl')).entries()
    assert answer['field_recorded'] is False
    assert 'incomplete' not in answer['field_note'].lower()
    assert 'durable_state' not in answer

