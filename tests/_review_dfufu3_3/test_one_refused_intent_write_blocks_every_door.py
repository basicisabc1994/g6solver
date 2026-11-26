"""Review 3 of fu-3: one refused write of ``authority_intent.json`` leaves an undo G6 can never finish.

Repair 2 declares each undo staging name in the intent on disk (``_plan_undo_staging``) before it creates the copy. The
declaration is appended to whatever intent the disk holds. During the rollback of a live transaction that intent can be
one step behind the transaction: ``retain_before_write`` adds the target to the in-memory undo set first and writes the
intent second, so when that one write is refused (the file is open in another program for a moment: the condition
reviews 1 and 2 used for the removal) the target is in ``_FAILURE_LOCAL.before`` and not in the intent on disk. The
rollback then

* declares a staging copy for that target in an intent whose ``writes`` do not name it, and publishes it;
* restores that target (nothing had changed in it);
* reads the intent again for the next target that needs a staging copy, and ``_read_intent`` now refuses its own
  declaration ("invalid undo staging target"), so that earlier target keeps the bytes of the failed request.

The intent stays on disk and no reader can parse it: every later door of this home, in this process and in any other,
answers G6_E_DISTILL_PARTIAL_WRITE "Undo is incomplete" until someone deletes the file by hand, and the ledger the undo
skipped still holds the row of a request that was refused (its object file is already removed).

At 7edc330c (before the staging declaration) the same single refused write is rolled back completely.

Real disk. The only injection is one ``PermissionError`` from ``os.replace`` onto ``authority_intent.json``; it is
removed before anything is read back. The controls are the same refusal at a write whose rollback needs no second
staging copy, and no refusal.
"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from g6.core.ledger import ChainLedger
from g6.core.storage import state_path
from g6.distill import ledger
from g6.distill.ledger import Stores
from g6.distill.library import Library
from g6.distill.records import CapabilityExtension, FailureRecord
from g6.harness import library_review_gate as review
from tests.distill.test_repair2_atomicity import handlers
from tests.distill.test_runtime_identity_boundaries import AFTER, BEFORE, admit, disk, method  # noqa: F401


def one_intent_write_is_refused(monkeypatch, wanted):
    """The first uncommitted intent whose newest row is ``wanted`` cannot replace the intent file, once."""
    replace, failed = os.replace, []

    def refuse_once(source, destination, *args, **kwargs):
        if Path(destination).name == 'authority_intent.json' and not failed:
            body = json.loads(Path(source).read_text(encoding='utf-8'))['body']
            names = [row['path'] for row in body['writes']]
            if not body['committed'] and 'undo_staging' not in body and wanted(names):
                failed.append(names)
                raise PermissionError(13, 'The process cannot access the file because it is being used by another '
                                          'process')
        return replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, 'replace', refuse_once)
    return failed, lambda: monkeypatch.setattr(os, 'replace', replace)


def left_behind():
    path = ledger._intent_path()
    if not path.exists():
        return 'no intent is left'
    body = json.loads(path.read_text(encoding='utf-8'))['body']
    names = [row['path'] for row in body['writes']]
    declared = [row['target'] for row in body.get('undo_staging', [])]
    return (f'authority_intent.json is left (committed={body["committed"]}); its writes name {names}; its undo staging '
            f'declarations name the targets {declared}, of which {[t for t in declared if t not in names]} are in no '
            f'write row')


WANTED = {
    'failure_mode_log': lambda names: names[-1] == 'distill/failure_mode.jsonl',
    'capability_log': lambda names: names[-1] == 'distill/extension.jsonl',
    'control_first_new_object': lambda names: len(names) == 1,
    'control_works': lambda names: False,
}


@pytest.mark.parametrize('case', list(WANTED))
def test_a_capability_request_refused_by_one_intent_write_is_undone_and_the_home_stays_usable(
        tmp_path, monkeypatch, case):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    (tmp_path / 'home' / 'distill').mkdir(parents=True)
    door = handlers(monkeypatch)
    assert 'gap_record' in door['g6_extend']('G6 should check migrations', '')
    logs = {name: state_path('distill', name).read_bytes() for name in
            ('failure.jsonl', 'failure_mode.jsonl', 'extension.jsonl')}

    failed, free = one_intent_write_is_refused(monkeypatch, WANTED[case])
    reply = door['g6_extend']('G6 should check schema drift', '')
    free()                                                    # the other program has let go of the file

    assert len(failed) == (0 if case == 'control_works' else 1), failed
    said = (reply.get('ok'), reply.get('warning_card', {}).get('code'), reply.get('rollback_or_containment'))
    later = [door['g6_library']('list', ''), door['g6_library']('list', ''),
             door['g6_extend']('G6 should check a third thing', '')]
    codes = [item.get('warning_card', {}).get('code') for item in later]
    now = {name: state_path('distill', name).read_bytes() for name in logs}
    kept = [name for name in logs if now[name] != logs[name]]
    rows = {name: len(ChainLedger(state_path('distill', name)).entries()) for name in logs}
    assert later[0] == {'entries': []} and 'gap_record' in later[2] and not ledger._intent_path().exists(), (
        f'the request was answered {said}; with the file free again the next three calls (library list twice, a new '
        f'request) were answered {codes} {later[0].get("rollback_or_containment")!r}; {left_behind()}; '
        f'ledger rows now {rows}; logs that still hold bytes of the refused request: {kept}')
    requests = Stores().for_model(CapabilityExtension).list()
    gaps = Stores().for_model(FailureRecord).list()
    assert len(requests) == (3 if case == 'control_works' else 2)
    assert {request.gap_record for request in requests} <= {gap.record_id for gap in gaps}


FRESH_PROCESS = r'''
from g6.distill import surface
from g6.mcp import distill
reply = distill._call(surface.library)
print(reply.get('warning_card', {}).get('code') or [(e['status'], e['authority']) for e in reply['entries']])
'''

OWNER = {
    'journal_row': lambda names: names[-1] == 'distill/acceptance_gate.jsonl',
    'control_library_row': lambda names: names[-1] == 'distill/library.jsonl',
    'control_works': lambda names: False,
}


@pytest.mark.parametrize('case', list(OWNER))
def test_an_owner_withdrawal_refused_by_one_intent_write_is_undone_and_the_library_stays_readable(
        disk, monkeypatch, case):
    entry = admit(method(), disk)
    door = handlers(monkeypatch)
    demoted = asyncio.run(door['g6_library_decide'](entry.entry_id, 'demote', 'owner decision', object()))
    assert demoted.get('verdict') == 'demote', demoted             # the acceptance journal now exists
    [current] = Library().list()
    assert (current.status, current.authority) == ('demoted', 'A1')
    library = state_path('distill', 'library.jsonl').read_bytes()

    failed, free = one_intent_write_is_refused(monkeypatch, OWNER[case])
    reply = asyncio.run(door['g6_library_decide'](current.entry_id, 'withdraw', 'owner decision', object()))
    free()                                                    # the other program has let go of the file

    assert len(failed) == (0 if case == 'control_works' else 1), failed
    said = (reply.get('ok'), reply.get('verdict'), reply.get('warning_card', {}).get('code'),
            reply.get('rollback_or_containment'), reply.get('durable_state'))
    listed = door['g6_library']('list', '')
    again = door['g6_library']('list', '')
    fresh = subprocess.run([sys.executable, '-c', FRESH_PROCESS], env=dict(os.environ), capture_output=True,
                           text=True, timeout=120)
    [found] = review.check(BEFORE, AFTER) or [{}]
    raw = [(row['body']['status'], row['body']['authority'])
           for row in ChainLedger(state_path('distill', 'library.jsonl')).entries()]
    expected = [('withdrawn', 'A0')] if case == 'control_works' else [('demoted', 'A1')]
    assert [(e['status'], e['authority']) for e in listed.get('entries', [])] == expected, (
        f'the withdrawal was answered {said}; with the file free again the library was read twice and answered '
        f'{[item.get("warning_card", {}).get("code") for item in (listed, again)]} '
        f'{listed.get("rollback_or_containment")!r}; a fresh process reading the library answered '
        f'{fresh.stdout.strip()!r}; the next patch review got library_unavailable='
        f'{found.get("library_unavailable")!r} at {found.get("authority")!r}; {left_behind()}; the rows of '
        f'library.jsonl on disk are {raw}')
    assert not ledger._intent_path().exists()
    if case != 'control_works':
        assert state_path('distill', 'library.jsonl').read_bytes() == library
        retry = asyncio.run(door['g6_library_decide'](current.entry_id, 'withdraw', 'owner decision', object()))
        assert retry.get('verdict') == 'withdraw', retry
