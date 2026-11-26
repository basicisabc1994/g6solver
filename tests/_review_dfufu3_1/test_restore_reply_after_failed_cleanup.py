"""Review 1 of fu-3: the fourth owner action, restoring a withdrawn method, is not covered by the committed-reply fix.

``g6_library_decide`` routes ``lift_hold``, ``withdraw`` and ``demote`` through ``_owner_decision``; ``restore`` of a
withdrawn or demoted method still goes through ``_call(backend.restore, ...)``. ``gate.restore`` publishes in several
short transactions (the look reservation, the report, the provisional certificate, the activation). When the intent of
any of them cannot be removed after its commit, ``PartialAuthorityWrite(committed=True)`` leaves ``_decide_batch``
(it catches ``ValueError``, ``KeyError`` and ``FileNotFoundError``) and the reply is ``_refusal``'s blocked envelope:

* at the activation: the method is restored (probation/A1, certificate and ``admit_probation`` row on disk) and the
  reply to the person's dialog answer is ``ok: False``, ``blocked-escalated``, with no decision: the outcome
  ``test_dialog_reply_keeps_committed_decision_when_cleanup_fails`` fixed for the other three actions;
* at the look reservation: the reply says "The publication committed", nothing the person asked for happened (the
  method is still withdrawn), and the reservation is never closed, so every later restore or admission in this home
  is blocked: "unfinished evaluation requires owner recovery". One removal that failed once did that.

The control is the same restore with a removal that works.
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


def cleanup_fails_once(monkeypatch, when):
    """The first committed intent whose target names satisfy ``when`` cannot be removed, once."""
    unlink, failed = Path.unlink, []

    def fail(path, *args, **kwargs):
        if path.name == 'authority_intent.json' and not failed and path.exists():
            body = json.loads(path.read_text(encoding='utf-8'))['body']
            names = [row['path'] for row in body['writes']]
            if body['committed'] and when(names):
                failed.append(names)
                raise PermissionError(13, 'The process cannot access the file because it is being used by another '
                                          'process')
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', fail)
    return failed


@pytest.mark.parametrize('at', ['activation', 'look_reservation', 'control_works'])
def test_restore_reply_and_retry_after_one_failed_cleanup(monkeypatch, tmp_path, at):
    _, candidate, _, frozen, _ = acceptance.rig.__wrapped__(monkeypatch, tmp_path)
    withdrawn = r.LibraryEntry(**(bodies()[4][1] | {
        'candidate_id': candidate.candidate_id, 'status': 'withdrawn', 'authority': 'A0',
        'regression_suite': [{'case_ref': frozen.acceptance[0].case.case_id, 'verdict': 'concern'}]}))
    Stores().for_model(r.LibraryEntry).put(withdrawn)
    decide = tools(monkeypatch, frozen.registration_sha256)['g6_library_decide']
    failed = cleanup_fails_once(monkeypatch, {
        'activation': lambda names: 'distill/library.jsonl' in names,
        'look_reservation': lambda names: names == ['distill/acceptance_gate.jsonl'],
        'control_works': lambda names: False}[at])
    reply = asyncio.run(decide(withdrawn.entry_id, 'restore', 'the person restores it', object()))
    assert len(failed) == (0 if at == 'control_works' else 1)
    [current] = Library().list()
    restored = (current.status, current.authority) == ('probation', 'A1')
    said = (reply.get('ok'), reply.get('completion_state'), reply.get('verdict'),
            reply.get('rollback_or_containment'))
    # the builder's own standard for the other three owner actions: a committed decision is in the reply
    assert (reply.get('verdict') == 'admit_probation') == restored, (
        f'the method is {(current.status, current.authority)} after the dialog, and the person was told {said}')
    if not restored:
        # nothing was restored and the file is free again: the person asks once more
        again = asyncio.run(decide(current.entry_id, 'restore', 'the person restores it', object()))
        [current] = Library().list()
        rows = gate._journal().entries()
        assert (current.status, current.authority) == ('probation', 'A1'), (
            f'the first reply said {said}; the retry is refused with '
            f"{[row['body'].get('reasons') for row in rows if row['kind'] == 'decision'][-1:]} "
            f"({again.get('warning_card', {}).get('code')}); the journal holds "
            f"{[(row['kind'], row['body'].get('verdict')) for row in rows]} and the method stays "
            f'{(current.status, current.authority)}')
