"""Review 2 of fu-3: "The publication committed" is still the reply to a restore that restored nothing.

Review 1 reported this for the look reservation; repair 1 answers that one boundary ("The evaluation reservation
committed; no admission decision was made; the method is unchanged."), keyed on ``error.reservation``, which only
``_spend`` sets. ``gate.restore`` commits three more short transactions before the activation: the evaluation receipt
(``_record`` of ``evaluated``), the acceptance report (``_save_report``) and the provisional certificate
(``_save_report(..., provisional=True)``, "It never carries authority"). When the intent of any of them cannot be
removed once, ``PartialAuthorityWrite(committed=True)`` carries no reservation and no decision, so the person who
answered the dialog is told ``publication_committed: True`` and "The publication committed; its intent still needs
cleanup." The method is still withdrawn at A0, and one look of the registered split is spent.

The controls are the two boundaries repair 1 covers (the reservation, which now says the method is unchanged, and the
activation, where the method is restored) and a removal that works.
"""
import asyncio
import json
from pathlib import Path

import pytest

from g6.distill import records as r
from g6.distill.ledger import Stores
from g6.distill.library import Library
from g6.mcp import consent, distill
from g6.mcp.distill_backend import Service
from tests.distill import test_acceptance_gate as acceptance
from tests.distill.test_records import bodies

JOURNAL = 'distill/acceptance_gate.jsonl'
REPORTS = 'distill/acceptance_gate_reports/'
LIBRARY = 'distill/library.jsonl'


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


def kind(names):
    if LIBRARY in names:
        return 'activation'
    if names == [JOURNAL]:
        return 'journal'
    if len(names) == 1 and names[0].startswith(REPORTS):
        return 'report'
    return 'other'


def cleanup_fails_once(monkeypatch, wanted, nth):
    """The ``nth`` committed intent of kind ``wanted`` cannot be removed, once (another program has it open)."""
    unlink, seen, failed = Path.unlink, [], []

    def fail(path, *args, **kwargs):
        if path.name == 'authority_intent.json' and path.exists():
            text = path.read_text(encoding='utf-8')
            body = json.loads(text)['body']
            names = [row['path'] for row in body['writes']]
            if body['committed'] and kind(names) == wanted:
                if text not in seen:
                    seen.append(text)
                    if len(seen) == nth and not failed:
                        failed.append(names)
                        raise PermissionError(13, 'The process cannot access the file because it is being used by '
                                                  'another process')
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', fail)
    return failed


@pytest.mark.parametrize('wanted, nth', [('journal', 2), ('report', 1), ('report', 2), ('journal', 1),
                                         ('activation', 1), ('none', 1)],
                         ids=['evaluation_receipt', 'acceptance_report', 'provisional_certificate',
                              'control_reservation', 'control_activation', 'control_works'])
def test_a_restore_is_said_to_have_published_only_when_the_method_is_restored(monkeypatch, tmp_path, wanted, nth):
    _, candidate, _, frozen, _ = acceptance.rig.__wrapped__(monkeypatch, tmp_path)
    withdrawn = r.LibraryEntry(**(bodies()[4][1] | {
        'candidate_id': candidate.candidate_id, 'status': 'withdrawn', 'authority': 'A0',
        'regression_suite': [{'case_ref': frozen.acceptance[0].case.case_id, 'verdict': 'concern'}]}))
    Stores().for_model(r.LibraryEntry).put(withdrawn)
    decide = tools(monkeypatch, frozen.registration_sha256)['g6_library_decide']
    failed = cleanup_fails_once(monkeypatch, wanted, nth)

    reply = asyncio.run(decide(withdrawn.entry_id, 'restore', 'the person restores it', object()))

    assert len(failed) == (0 if wanted == 'none' else 1), failed
    [current] = Library().list()
    restored = (current.status, current.authority) == ('probation', 'A1')
    words = str(reply.get('rollback_or_containment', ''))
    says_unchanged = 'method is unchanged' in words.lower()
    assert reply.get('publication_committed') is not True or restored or says_unchanged, (
        f'the method is {(current.status, current.authority)} after the dialog; the reply to the person is ok='
        f"{reply.get('ok')}, {reply.get('completion_state')}, publication_committed="
        f"{reply.get('publication_committed')!r}, {words!r}; the intent that could not be removed named {failed}")
