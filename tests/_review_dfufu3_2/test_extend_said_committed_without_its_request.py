"""Review 2 of fu-3: ``g6_extend`` says "The publication committed" when the capability request was not recorded.

Repair 1 applies the committed-publication reply to "failure intake and the other distillation doors". ``g6_extend``
publishes in two transactions: the gap record (``surface.failure_record``), then the capability request itself
(``Rectifier().extend``). When the gap record's intent cannot be removed once, ``PartialAuthorityWrite(committed=True)``
ends the call between the two: the reply is ``publication_committed: True`` and "The publication committed; its intent
still needs cleanup.", and the request the person made is not in the capability log. Asked again, a second gap record
is written for the one request.

The control is a removal that works.
"""
import json
from pathlib import Path

import pytest

from g6.distill.ledger import Stores
from g6.distill.records import CapabilityExtension, FailureRecord
from tests.distill.test_repair2_atomicity import handlers


def cleanup_fails_once(monkeypatch):
    """The first committed intent cannot be removed, once (another program has the file open)."""
    unlink, failed = Path.unlink, []

    def fail(path, *args, **kwargs):
        if path.name == 'authority_intent.json' and not failed and path.exists():
            body = json.loads(path.read_text(encoding='utf-8'))['body']
            if body['committed']:
                failed.append([row['path'] for row in body['writes']])
                raise PermissionError(13, 'The process cannot access the file because it is being used by another '
                                          'process')
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', fail)
    return failed


@pytest.mark.parametrize('held', [True, False], ids=['removal_fails_once', 'control_works'])
def test_extend_is_said_to_have_published_only_when_the_request_is_recorded(tmp_path, monkeypatch, held):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    (tmp_path / 'home' / 'distill').mkdir(parents=True)
    tools = handlers(monkeypatch)
    failed = cleanup_fails_once(monkeypatch) if held else []

    reply = tools['g6_extend']('G6 should check migrations', '')

    assert len(failed) == (1 if held else 0)
    requests = Stores().for_model(CapabilityExtension).list()
    gaps = Stores().for_model(FailureRecord).list()
    assert reply.get('publication_committed') is not True or requests, (
        f"the reply is ok={reply.get('ok')}, publication_committed={reply.get('publication_committed')!r}, "
        f"{reply.get('rollback_or_containment')!r}; the capability log holds {len(requests)} request and the failure "
        f'log {len(gaps)} gap record; the intent that could not be removed named {failed}')
    if not held:
        assert len(requests) == 1 and len(gaps) == 1
