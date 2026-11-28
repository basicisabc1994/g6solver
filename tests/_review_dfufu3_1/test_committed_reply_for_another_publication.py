"""Review 1 of fu-3: "the publication committed" is said of a call that published nothing.

``PartialAuthorityWrite(committed=True)`` now has two sources: the caller's own transaction committed and its intent
could not be removed (``failure_writer``'s ``else`` branch), or recovery at the ENTRY of the caller's transaction could
not remove an older, already committed intent (``_recover_intent``, ``recovering=True``). In the second case the
caller's operation never started. ``g6.mcp.distill._refusal`` reads only ``exc.committed``, so both produce the same
reply: ``publication_committed: True`` and ``rollback_or_containment`` "The publication committed; its intent still
needs cleanup."

Here a review's field fire commits while the intent file cannot be removed (it is held by another program), and the
person then withdraws the method. The withdrawal is answered at its first read, before the dialog: the reply says the
publication committed, and the method is still admitted at A2 and served by the next review.

The control is the builder's own case: the removal fails at the withdrawal's own commit, the reply says committed, and
the method is withdrawn.
"""
import asyncio
import json
from pathlib import Path

import pytest

from g6.distill.library import Library
from g6.distill.rectify_gate import Rectifier
from g6.harness import library_review_gate as review
from tests.distill import test_runtime_identity_boundaries as runtime
from tests.distill.test_repair2_atomicity import handlers


def cleanup_fails(monkeypatch, when):
    """A committed intent cannot be removed while ``when(its target names, failures so far)`` holds."""
    unlink, failed = Path.unlink, []

    def fail(path, *args, **kwargs):
        if path.name == 'authority_intent.json' and path.exists():
            body = json.loads(path.read_text(encoding='utf-8'))['body']
            names = [row['path'] for row in body['writes']]
            if body['committed'] and when(names, failed):
                failed.append(names)
                raise PermissionError(13, 'The process cannot access the file because it is being used by another '
                                          'process')
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', fail)
    return failed


@pytest.mark.parametrize('held', ['since_the_review_before', 'control_at_this_withdrawal'])
def test_a_withdrawal_is_said_to_be_committed_only_when_it_is(monkeypatch, tmp_path, held):
    frozen = runtime.disk.__wrapped__(monkeypatch, tmp_path)
    entry = runtime.admit(runtime.method(), frozen)
    Rectifier().reconcile()
    tools = handlers(monkeypatch)
    if held == 'since_the_review_before':
        # the review's fire commits (1), the review's last read (2) and the withdrawal's first read (3) find the
        # committed intent still held; after that it is released
        failed = cleanup_fails(monkeypatch, lambda names, failed: len(failed) < 3)
        review.apply_review(runtime.BEFORE, runtime.AFTER, [], [])
        assert len(failed) == 2 and all('distill/field_fires_ledger.jsonl' in names for names in failed)
    else:
        failed = cleanup_fails(monkeypatch, lambda names, failed: not failed and 'distill/library.jsonl' in names)
    reply = asyncio.run(tools['g6_library_decide'](entry.entry_id, 'withdraw', 'the person withdraws it', object()))
    assert len(failed) == (3 if held == 'since_the_review_before' else 1)
    assert reply.get('publication_committed') is True       # what the person is told, in both cases
    now = [(e.status, e.authority) for e in Library().list()]
    served = [(f['authority'], f['outcome']) for f in review.check(runtime.BEFORE, runtime.AFTER)]
    assert (now, served) == ([('withdrawn', 'A0')], []), (
        f"the reply to the person's withdrawal says publication_committed={reply.get('publication_committed')!r}, "
        f"{reply.get('rollback_or_containment') or reply.get('cleanup_note')!r}; the method is {now} and the next "
        f'review reports {served}')
