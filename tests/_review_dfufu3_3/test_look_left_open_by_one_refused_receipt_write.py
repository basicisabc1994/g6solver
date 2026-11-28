"""Review 3 of fu-3: one refused write at the evaluation receipt leaves this restore's look open for good, and the
reply says nothing changed.

Repairs 1 and 2 close the look a live request reserved when a later step of that request raises
``PartialAuthorityWrite`` (``_close_interrupted_reservation``, called from the two ``except PartialAuthorityWrite``
blocks of ``_decide_batch``). A step that fails and is ROLLED BACK CLEANLY raises the original ``OSError`` instead
(``failure_writer`` re-raises it after a complete undo), and no handler in ``_decide_batch`` catches that: the
``reserve`` row this request committed one transaction earlier is never given its refusal row.

Here the intent of the evaluation receipt cannot replace ``authority_intent.json`` once (the file is open in another
program for a moment; reviews 1 and 2 used the same condition for the removal, and needed it twice). The receipt is
undone completely, so:

* the reply is the input refusal: G6_E_DISTILL_INPUT, "The operation was blocked before any state change; no rollback
  is required.", said of a request that appended a ``reserve`` row (one look of the registered split is spent);
* ``_look_available`` answers "unfinished evaluation requires owner recovery" to every later restore and admission in
  this home, and no door performs that recovery: the person's retry is refused and the method stays withdrawn.

The controls are the same refusal one transaction earlier (the reservation itself: nothing was recorded, the reply is
true and the retry restores the method) and no refusal.
"""
import asyncio
import json
import os
from pathlib import Path

import pytest

from g6.distill import acceptance_gate as gate, records as r
from g6.distill.ledger import Stores
from g6.distill.library import Library
from g6.mcp import consent, distill
from g6.mcp.distill_backend import Service
from tests.distill import test_acceptance_gate as acceptance
from tests.distill.test_records import bodies

JOURNAL = ['distill/acceptance_gate.jsonl']


def tools(monkeypatch, registration):
    found = {}

    class Server:
        def tool(self):
            return lambda fn: found.setdefault(fn.__name__, fn)

    async def accept(*args):
        return 'accept', {'confirm': 'change', 'name': 'Owner'}, ''

    monkeypatch.setattr(consent, 'elicit', accept)
    distill.register(Server(), object, None, backend=Service(registration=registration))
    return found


def one_intent_write_is_refused(monkeypatch, nth):
    """The first intent of the ``nth`` transaction that plans only an acceptance-journal row cannot replace the
    intent file, once; every other write works. ``nth`` 1 is the look reservation, 2 the evaluation receipt."""
    replace, planned, failed = os.replace, [], []

    def refuse_once(source, destination, *args, **kwargs):
        if Path(destination).name == 'authority_intent.json' and not failed:
            body = json.loads(Path(source).read_text(encoding='utf-8'))['body']
            names = [row['path'] for row in body['writes']]
            if not body['committed'] and 'undo_staging' not in body and names == JOURNAL:
                planned.append(names)
                if len(planned) == nth:
                    failed.append(names)
                    raise PermissionError(13, 'The process cannot access the file because it is being used by '
                                              'another process')
        return replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, 'replace', refuse_once)
    return failed, lambda: monkeypatch.setattr(os, 'replace', replace)


@pytest.mark.parametrize('nth', [2, 1, 0], ids=['receipt_write_refused_once', 'control_reservation_write_refused_once',
                                               'control_works'])
def test_a_restore_refused_by_one_write_leaves_no_open_look_and_says_what_it_changed(monkeypatch, tmp_path, nth):
    _, candidate, _, frozen, _ = acceptance.rig.__wrapped__(monkeypatch, tmp_path)
    withdrawn = r.LibraryEntry(**(bodies()[4][1] | {
        'candidate_id': candidate.candidate_id, 'status': 'withdrawn', 'authority': 'A0',
        'regression_suite': [{'case_ref': frozen.acceptance[0].case.case_id, 'verdict': 'concern'}]}))
    Stores().for_model(r.LibraryEntry).put(withdrawn)
    decide = tools(monkeypatch, frozen.registration_sha256)['g6_library_decide']
    before = len(gate._journal().entries())
    failed, free = one_intent_write_is_refused(monkeypatch, nth)

    reply = asyncio.run(decide(withdrawn.entry_id, 'restore', 'the person restores it', object()))
    free()                                                    # the other program has let go of the file

    assert len(failed) == (1 if nth else 0)
    rows = gate._journal().entries()
    added = [(row['kind'], row['body'].get('verdict')) for row in rows[before:]]
    said = (reply.get('warning_card', {}).get('code'), reply.get('publication_committed'),
            reply.get('rollback_or_containment') or reply.get('verdict'))
    open_looks = {row['id'] for row in rows if row['kind'] == 'reserve'}
    open_looks -= {row['body'].get('reservation') for row in rows if row['kind'] == 'decision'}
    [current] = Library().list()
    restored = (current.status, current.authority) == ('probation', 'A1')
    retry = None
    if not restored:
        # the file is free again, nothing was restored: the person asks once more
        retry = asyncio.run(decide(current.entry_id, 'restore', 'the person restores it', object()))
        [current] = Library().list()
    refusal = [row['body'].get('reasons') for row in gate._journal().entries() if row['kind'] == 'decision'][-1:]
    assert not open_looks and (current.status, current.authority) == ('probation', 'A1'), (
        f'the reply said {said}; this request added {added} to the acceptance journal and left {len(open_looks)} '
        f'look open; the retry was answered {retry and retry.get("warning_card", {}).get("code")} with {refusal} '
        f'and the method stays {(current.status, current.authority)}')
    no_change = 'before any state change' in str(reply.get('rollback_or_containment', '')).lower()
    assert not (no_change and added), (
        f'the reply said {said}, and this request added {added} to the acceptance journal (one look of the '
        f'registered split is spent)')
