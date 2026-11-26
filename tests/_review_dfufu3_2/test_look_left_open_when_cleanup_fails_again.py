"""Review 2 of fu-3: the look this restore reserved stays open, and the reply says "this request made no change".

Repair 1 closes an interrupted look with ``_close_interrupted_reservation``. That function is itself a
``failure_writer`` transaction, so it starts with recovery of the committed intent the reservation just left behind.
When the intent still cannot be removed (review 1's accepted condition for the field fire: the file is still held by
whatever held it microseconds earlier), recovery raises ``PartialAuthorityWrite(committed=True, recovering=True)``
INSIDE the ``except`` block of ``_decide_batch``. That error replaces the reservation's own error:

* the reservation is never closed, so ``_look_available`` answers "unfinished evaluation requires owner recovery" to
  every later restore and admission in this home (no door performs that recovery);
* the error that reaches ``_refusal`` is the recovery one: ``publication_committed: False``,
  ``earlier_publication_committed: True`` and "this request made no change", said of a request that appended the
  ``reserve`` row itself.

With the removal failing twice at the evaluation receipt instead, the look is closed, and the same sentence is said of
a request that appended a ``reserve`` row and an ``evaluated`` decision row.

The controls are the builder's case (the removal fails once) and a removal that works.
"""
import asyncio
import json
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


def cleanup_fails(monkeypatch, *, nth, times):
    """The ``nth`` committed intent that names only the acceptance journal cannot be removed ``times`` times in a row
    (another program has the file open); every other removal works."""
    unlink, seen, failed = Path.unlink, [], []

    def fail(path, *args, **kwargs):
        if path.name == 'authority_intent.json' and path.exists():
            body = json.loads(path.read_text(encoding='utf-8'))['body']
            names = [row['path'] for row in body['writes']]
            if body['committed'] and names == JOURNAL:
                text = path.read_text(encoding='utf-8')
                if text not in seen:
                    seen.append(text)
                if seen.index(text) == nth - 1 and len(failed) < times:
                    failed.append(names)
                    raise PermissionError(13, 'The process cannot access the file because it is being used by '
                                              'another process')
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', fail)
    return failed


@pytest.mark.parametrize('boundary, times', [('reservation', 2), ('receipt', 2), ('reservation', 1), ('reservation', 0)],
                         ids=['reservation_fails_twice', 'receipt_fails_twice', 'control_fails_once', 'control_works'])
def test_a_restore_whose_cleanup_fails_again_leaves_no_open_look_and_says_what_it_changed(
        monkeypatch, tmp_path, boundary, times):
    _, candidate, _, frozen, _ = acceptance.rig.__wrapped__(monkeypatch, tmp_path)
    withdrawn = r.LibraryEntry(**(bodies()[4][1] | {
        'candidate_id': candidate.candidate_id, 'status': 'withdrawn', 'authority': 'A0',
        'regression_suite': [{'case_ref': frozen.acceptance[0].case.case_id, 'verdict': 'concern'}]}))
    Stores().for_model(r.LibraryEntry).put(withdrawn)
    decide = tools(monkeypatch, frozen.registration_sha256)['g6_library_decide']
    before = len(gate._journal().entries())
    failed = cleanup_fails(monkeypatch, nth=1 if boundary == 'reservation' else 2, times=times)

    reply = asyncio.run(decide(withdrawn.entry_id, 'restore', 'the person restores it', object()))

    assert len(failed) == times
    rows = gate._journal().entries()
    added = [(row['kind'], row['body'].get('verdict')) for row in rows[before:]]
    said = (reply.get('publication_committed'), reply.get('earlier_publication_committed'),
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
    no_change = 'this request made no change' in str(reply.get('rollback_or_containment', '')).lower()
    assert not (no_change and added), (
        f'the reply said {said}, and this request added {added} to the acceptance journal (one look of the '
        f'registered split is spent)')
