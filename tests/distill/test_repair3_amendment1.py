# SPDX-License-Identifier: Apache-2.0
"""Hold receipts stay outside evaluator custody; every typed chain stays durable."""
import builtins
import os
import subprocess
import sys

import pytest

from g6.core.ledger import ChainLedger, canonical, digest
from g6.core.storage import state_path
from g6.distill import records as r
from g6.distill.ledger import Stores
from g6.distill.library import Library
from g6.distill.rectify_gate import Rectifier
from tests.distill import test_recurrence_hold as recurrence
from tests.distill.test_records import bodies


@pytest.mark.parametrize('index', range(8), ids=list(r.RECORD_KINDS))
def test_every_record_chain_keeps_its_file_before_the_folder_exists(tmp_path, monkeypatch, index):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    model, body = bodies()[index]
    value = model(**body)
    store = Stores().for_model(model)
    assert not store.path.parent.exists()
    assert store.chain.path == store.path
    assert store._read(references=False) == []
    assert not store.path.parent.exists()
    store.path.parent.mkdir(parents=True)
    obj = store._object(value.content_id)
    obj.parent.mkdir()
    obj.write_text(canonical(value.model_dump(mode='json')) + '\n', encoding='utf-8')
    store.chain.append(store.kind, value.content_id, value.model_dump(mode='json'))
    assert store._read(references=False) == [value]
    assert Stores().for_model(model)._read(references=False) == [value]


PENDING_CHILD = r'''
import os
from pathlib import Path
import sys
from g6.distill.ledger import failure_writer, retain_before_write

path = Path(sys.argv[1])
with failure_writer():
    retain_before_write(path, after=b'partial row\n')
    with path.open('wb') as stream:
        stream.write(b'partial row\n')
        stream.flush()
        os.fsync(stream.fileno())
    os._exit(73)
'''


@pytest.mark.parametrize('first_access', ['construct', 'read', 'write'])
def test_file_backed_store_recovers_before_each_trusted_access(tmp_path, monkeypatch, first_access):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    value = r.FailureMode(**bodies()[6][1])
    store = Stores().for_model(r.FailureMode)
    store.put(value)
    before = store.path.read_bytes()
    env = dict(os.environ, G6_CLAUDE=r'C:\nonexistent\g6-no-host.exe',
               G6_CODEX=r'C:\nonexistent\g6-no-host.exe', PYTHONDONTWRITEBYTECODE='1')
    child = subprocess.run([sys.executable, '-c', PENDING_CHILD, str(store.path)],
                           env=env, capture_output=True, text=True, timeout=60)
    assert child.returncode == 73, child.stdout + child.stderr
    assert store.path.read_bytes() == b'partial row\n'
    if first_access == 'construct':
        store = Stores().for_model(r.FailureMode)
    elif first_access == 'write':
        changed = r.FailureMode(**(value.model_dump(mode='json') | {'occurrences': 2}))
        store.put(changed)
        assert store.list() == [value, changed]
        assert store.path.read_bytes().startswith(before)
    else:
        assert store.list() == [value]
    if first_access != 'write':
        assert store.path.read_bytes() == before
        assert store.list() == [value]
    assert not state_path('distill', 'authority_intent.json').exists()


@pytest.mark.parametrize('fail_receipt', [False, True], ids=['receipt', 'receipt_rollback'])
def test_independent_release_journals_without_importing_evaluator(tmp_path, monkeypatch, fail_receipt):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    _, rows, removed = recurrence.removal()
    again = recurrence.recur(rows)
    [held] = Library().list()
    check = r.RemovalCheck(**(removed.pinning.model_dump() | {
        'replayed': [row.record_id for row in [*rows, again]],
        'receipt_sha256': digest('amendment independent replay')}))
    journal_path = state_path('distill', 'acceptance_gate.jsonl')
    before = ChainLedger(journal_path).entries()
    original_import, original_append = builtins.__import__, ChainLedger.append
    published = []

    def no_evaluator(name, globals=None, locals=None, fromlist=(), level=0):
        if ('acceptance_gate' in name.split('.') or 'acceptance_gate' in (fromlist or ())):
            raise AssertionError('rectifier reached evaluator import')
        return original_import(name, globals, locals, fromlist, level)

    def append(chain, kind, ident, body):
        result = original_append(chain, kind, ident, body)
        if kind == 'decision':
            # Inspect the real stored entry after the real decision append.
            [current] = Library().list()
            assert (current.status, current.authority) == ('admitted', 'A2')
            assert body['entry_id'] == current.entry_id
            published.append(current)
            if fail_receipt:
                raise OSError('receipt write failed after append')
        return result

    with monkeypatch.context() as patch:
        patch.setattr(builtins, '__import__', no_evaluator)
        patch.setattr(ChainLedger, 'append', append)
        if fail_receipt:
            with pytest.raises(OSError, match='receipt write failed'):
                Rectifier().release_hold(held.entry_id, [check])
        else:
            changed = Rectifier().release_hold(held.entry_id, [check])
    assert len(published) == 1
    if fail_receipt:
        assert Library().list() == [held]
        assert ChainLedger(journal_path).entries() == before
    else:
        assert Library().list() == [changed]
        journal = ChainLedger(journal_path).entries()
        assert journal[:-1] == before
        receipt = journal[-1]
        assert receipt['kind'] == 'decision'
        assert receipt['body'] == {
            'verdict': 'lift_hold', 'reasons': [changed.reason],
            'candidate_id': changed.candidate_id, 'report_sha256': '', 'entry_id': changed.entry_id,
            'p_value': 1.0, 'minimum_detectable_gain': 1.0, 'p_value_upper_bound': None,
            'minimum_detectable_precision_difference': None,
            'reservation': '', 'gate_version': 'distill-acceptance-v1'}
        assert receipt['id'] == digest(['decision', len(before), receipt['body']])
