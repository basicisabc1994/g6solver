"""Restore keeps proposal identity and explicit-anchor refusal envelopes."""
import asyncio
from dataclasses import replace

import pytest

from g6.core.ledger import digest
from g6.distill import acceptance_gate as gate, records as r
from g6.distill.ledger import RecordStore, Stores
from g6.distill.library import Library
from g6.mcp import consent, distill
from g6.mcp.distill_backend import Service
from tests.distill import test_runtime_identity_boundaries as boundaries
from tests.distill.test_acceptance_measurements import sample
from tests.distill.test_review4_amendment1 import _known_registration
from tests.distill.test_runtime_identity_boundaries import admit

disk = boundaries.disk


@pytest.mark.parametrize('registration', [digest('unknown-registration'), 'not-a-digest'],
                         ids=['unknown', 'malformed'])
def test_service_restore_rejects_explicit_anchor_before_gate_decision(disk, monkeypatch, registration):
    _, candidate, _, _ = sample.__wrapped__()
    entry = admit(candidate, disk)
    withdrawn = gate.withdraw(entry.entry_id, Library(), reason='owner stopped method', person='Owner')
    reached = []
    restore = gate.restore

    def restoring(*args):
        reached.append(1)
        return restore(*args)

    monkeypatch.setattr(gate, 'restore', restoring)
    answer = distill._call(Service(registration=registration).restore, withdrawn.entry_id)
    assert answer.get('ok') is False
    assert answer['warning_card']['code'] == 'G6_E_DISTILL_INPUT'
    assert reached == [], 'invalid explicit anchor reached the restore decision path'


def test_dialog_restore_readmits_existing_identity_without_recording_proposal_again(disk, monkeypatch):
    registration = _known_registration(monkeypatch)
    _, candidate, frozen, _ = sample.__wrapped__()
    entry = admit(candidate, disk)
    withdrawn = gate.withdraw(entry.entry_id, Library(), reason='owner stopped method', person='Owner')
    proposals = Stores().for_model(r.VerifierCandidate).list()
    assert proposals == [candidate]
    assert not any(row['kind'] == 'reserve' for row in gate._journal().entries())
    monkeypatch.setattr(gate, '_load', lambda *args: replace(frozen, registration_sha256=registration))
    writes, dialogs, handlers = [], [], {}
    put = RecordStore.put

    def recording(self, value):
        if self.model is r.VerifierCandidate:
            writes.append(value.candidate_id)
        return put(self, value)

    class Server:
        def tool(self):
            return lambda fn: handlers.setdefault(fn.__name__, fn)

    async def accept(*args):
        dialogs.append(1)
        return 'accept', {'confirm': 'change', 'name': 'Owner'}, ''

    monkeypatch.setattr(RecordStore, 'put', recording)
    monkeypatch.setattr(consent, 'elicit', accept)
    distill.register(Server(), object, None, backend=Service(registration=registration))
    answer = asyncio.run(handlers['g6_library_decide'](
        withdrawn.entry_id, 'restore', 'fresh admission', object()))
    assert dialogs == [1]
    assert answer['verdict'] == 'admit_probation'
    assert answer['candidate_id'] == candidate.candidate_id
    [current] = Library().list()
    assert (current.candidate_id, current.status, current.authority, current.version) == (
        candidate.candidate_id, 'probation', 'A1', 3)
    assert writes == []
    assert Stores().for_model(r.VerifierCandidate).list() == proposals
    assert len([row for row in gate._journal().entries() if row['kind'] == 'reserve']) == 1
