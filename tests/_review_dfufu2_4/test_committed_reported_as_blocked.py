"""Review 4, findings 3 and 4: a publication that committed is reported as one that did not happen.

``failure_writer`` commits (every planned record is on disk, the intent is marked committed) and then removes the
intent. When that removal fails it raises ``PartialAuthorityWrite`` on purpose: the next lock holder retires the
committed intent and the publication stands. The replies built from that error say the opposite of the disk:

* after the person's dialog answer releases a hold, the reply is blocked-escalated, "Recovery incomplete", with
  ``rollback_or_containment`` "The operation was blocked before any state change; no rollback is required." The method
  is admitted/A2 with its decision journalled, and the next review reports A2 defects from it;
* ``library_review_gate.check`` replies ``field_recorded: False`` and "no field evidence was added" while the fire's
  row is in the field ledger (the builder's test_field_recovery_retry_describes_final_disk pins the opposite case).

Each has its control: the same call with a removal that works.
"""
import asyncio
import json
from pathlib import Path

import pytest

from g6.core.ledger import ChainLedger
from g6.core.storage import state_path
from g6.distill import acceptance_gate as gate
from g6.distill.library import Library
from g6.distill.rectify_gate import Rectifier
from g6.harness import library_review_gate as review
from tests.distill import test_runtime_identity_boundaries as runtime
from tests.distill.test_repair2_atomicity import handlers


def cleanup_fails_once(monkeypatch):
    """The committed intent cannot be removed once (on Windows: another process has it open)."""
    unlink, failed = Path.unlink, []

    def fail_once(path, *args, **kwargs):
        if path.name == 'authority_intent.json' and not failed and path.exists() \
                and json.loads(path.read_text(encoding='utf-8'))['body']['committed']:
            failed.append(True)
            raise PermissionError(13, 'The process cannot access the file because it is being used by another process')
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', fail_once)
    return failed


@pytest.mark.parametrize('cleanup', ['fails_once', 'control_works'])
def test_reply_after_the_dialog_matches_the_committed_release(monkeypatch, tmp_path, cleanup):
    frozen = runtime.disk.__wrapped__(monkeypatch, tmp_path)
    entry = gate._change(runtime.admit(runtime.method(), frozen), status='held', authority='A0', reason='hold')
    Rectifier().reconcile()
    tools = handlers(monkeypatch)
    failed = cleanup_fails_once(monkeypatch) if cleanup == 'fails_once' else None
    answer = asyncio.run(tools['g6_library_decide'](entry.entry_id, 'restore', 'release it', object()))
    assert failed is None or failed == [True]
    [current] = Library().list()
    decisions = [row['body']['verdict'] for row in ChainLedger(state_path('distill', 'acceptance_gate.jsonl')).entries()]
    assert (current.status, current.authority, decisions) == ('admitted', 'A2', ['lift_hold'])   # it committed
    [served] = review.check(runtime.BEFORE, runtime.AFTER)
    assert (served['authority'], served['outcome']) == ('A2', 'defect_detected')                 # and is served
    said = (answer.get('ok'), answer.get('completion_state'), answer.get('warning_card', {}).get('code'),
            answer.get('rollback_or_containment'))
    assert 'before any state change' not in (answer.get('rollback_or_containment') or ''), (
        f'the release committed and the method is served at A2, but the person was told {said}')
    assert answer.get('verdict') == 'lift_hold'


@pytest.mark.parametrize('cleanup', ['fails_once', 'control_works'])
def test_field_reply_describes_the_committed_fire(monkeypatch, tmp_path, cleanup):
    frozen = runtime.disk.__wrapped__(monkeypatch, tmp_path)
    runtime.admit(runtime.method(), frozen)
    failed = cleanup_fails_once(monkeypatch) if cleanup == 'fails_once' else None
    [answer] = review.check(runtime.BEFORE, runtime.AFTER)
    assert failed is None or failed == [True]
    rows = ChainLedger(state_path('distill', 'field_fires_ledger.jsonl')).entries()
    assert len(rows) == 1 and not state_path('distill', 'authority_intent.json').exists()         # it committed
    assert answer['field_recorded'] is True, (
        f"the field ledger holds {len(rows)} row for this fire, but the reply says field_recorded="
        f"{answer['field_recorded']!r}, {answer.get('field_note')!r}")
