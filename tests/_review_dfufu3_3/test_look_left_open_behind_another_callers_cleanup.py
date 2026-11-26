"""Review 3 of fu-3: a restore's own look stays open for good when the cleanup that blocks it is another caller's.

Repair 2 closes a live request's reservation when entry cleanup still fails, but only "against its exact own committed
intent" (``failure_writer(finish_committed=...)``, reached from ``_close_interrupted_reservation`` only when
``interrupted.publication_committed``). Evaluation runs outside the store lock, by design, so another caller can
publish between this request's reservation and its next store read (a failure report, an owner decision, a patch
review's field fire from the Stop hook). When THAT caller's committed intent cannot be removed for a moment, this
request's next read raises ``PartialAuthorityWrite(committed=True, recovering=True)``: not this request's publication,
so the close is attempted once through ordinary entry recovery, fails on the same file, and re-raises. Nothing closes
the ``reserve`` row afterwards: once the file is free, ``_look_available`` answers "unfinished evaluation requires
owner recovery" to every later restore and admission in this home, and no door performs that recovery.

Real disk. The other caller is a second thread that records one failure report through ``surface.failure_record``
while this restore is evaluating. The intent that report commits cannot be removed three times in a row (its own
transaction, this restore's next read, the close attempt); review 2 used the same condition twice in a row. The
controls are the same interleaving with the removal failing twice (the close's entry recovery then succeeds and the
look is closed) and not at all.
"""
import asyncio
import json
from pathlib import Path
import threading

import pytest

from g6.distill import acceptance_gate as gate, records as r, surface
from g6.distill.ledger import Stores
from g6.distill.library import Library
from g6.mcp import consent, distill
from g6.mcp.distill_backend import Service
from tests.distill import test_acceptance_gate as acceptance
from tests.distill.test_records import bodies


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


def another_caller_reports_during_evaluation(monkeypatch, times):
    """While the restore evaluates, a second thread records a failure report; the committed intent of THAT report
    cannot be removed ``times`` times in a row (another program has the file open)."""
    unlink, assess, failed, other = Path.unlink, gate._assess, [], []

    def fail(path, *args, **kwargs):
        if path.name == 'authority_intent.json' and len(failed) < times and path.exists():
            body = json.loads(path.read_text(encoding='utf-8'))['body']
            names = [row['path'] for row in body['writes']]
            if body['committed'] and 'distill/failure.jsonl' in names:
                failed.append(names)
                raise PermissionError(13, 'The process cannot access the file because it is being used by another '
                                          'process')
        return unlink(path, *args, **kwargs)

    def evaluate(*args, **kwargs):
        if not other:
            other.append(None)
            thread = threading.Thread(target=lambda: other.append(distill._call(
                surface.failure_record, 'user_request', 'capability_extension', '{"repo": ""}',
                'reported by another caller')))
            thread.start()
            thread.join(120)
        return assess(*args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', fail)
    monkeypatch.setattr(gate, '_assess', evaluate)
    return failed, other


@pytest.mark.parametrize('times', [3, 2, 0], ids=['other_cleanup_fails_three_times', 'control_fails_twice',
                                                 'control_works'])
def test_a_restore_blocked_by_another_callers_cleanup_leaves_no_open_look(monkeypatch, tmp_path, times):
    _, candidate, _, frozen, _ = acceptance.rig.__wrapped__(monkeypatch, tmp_path)
    withdrawn = r.LibraryEntry(**(bodies()[4][1] | {
        'candidate_id': candidate.candidate_id, 'status': 'withdrawn', 'authority': 'A0',
        'regression_suite': [{'case_ref': frozen.acceptance[0].case.case_id, 'verdict': 'concern'}]}))
    Stores().for_model(r.LibraryEntry).put(withdrawn)
    decide = tools(monkeypatch, frozen.registration_sha256)['g6_library_decide']
    before = len(gate._journal().entries())
    failed, other = another_caller_reports_during_evaluation(monkeypatch, times)

    reply = asyncio.run(decide(withdrawn.entry_id, 'restore', 'the person restores it', object()))

    assert len(failed) == times and len(other) == 2
    # the other caller's report is recorded either way; with a failed removal it was told so
    assert other[1].get('record_id') or other[1].get('publication_committed') is True, other[1]
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
        f'and the method stays {(current.status, current.authority)}; the intents that could not be removed named '
        f'{failed}')
