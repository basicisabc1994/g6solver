# SPDX-License-Identifier: Apache-2.0
"""Every owner decision branch keeps dialog data in local custody."""
import asyncio
import json
from dataclasses import asdict
from types import SimpleNamespace
from typing import get_args, get_type_hints

import pytest

from g6.distill import acceptance_gate as gate, library, records as r, surface
from g6.mcp import consent, distill
from tests.distill.test_records import bodies


class Server:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


def test_owner_action_inventory_matches_the_registered_interface():
    server = Server()
    distill.register(server, SimpleNamespace, SimpleNamespace(), backend=SimpleNamespace())
    actions = get_args(get_type_hints(server.tools['g6_library_decide'])['action'])
    assert set(actions) == {'demote', 'withdraw', 'restore'}


@pytest.mark.parametrize('action,status', [
    ('withdraw', 'admitted'), ('demote', 'admitted'), ('restore', 'held'), ('restore', 'withdrawn')])
@pytest.mark.parametrize('verdict', ['accepted', 'refuse', 'block'])
def test_every_owner_reply_branch_uses_public_view(monkeypatch, action, status, verdict):
    entry = r.LibraryEntry(**(bodies()[4][1] | {
        'status': status, 'authority': 'A2' if status == 'admitted' else 'A0'}))
    monkeypatch.setattr(surface, 'library', lambda *args: {'entry': library.public_entry(entry)})
    reason = 'private-owner-reason'
    person = 'Private Owner'
    async def accept(*args):
        return 'accept', {'confirm': 'change', 'name': person}, None
    monkeypatch.setattr(consent, 'elicit', accept)
    local = []
    result = gate.AcceptanceDecision(action if verdict == 'accepted' else verdict, (reason,),
                                     candidate_id=entry.candidate_id, entry_id=entry.entry_id)
    def decide(*args, **kwargs):
        local.append(kwargs)
        return result
    monkeypatch.setattr(gate, 'withdraw', decide)
    monkeypatch.setattr(gate, 'demote', decide)
    monkeypatch.setattr(gate, 'lift_hold', decide)
    backend = SimpleNamespace(restore=lambda *args: asdict(result))
    server = Server()
    distill.register(server, SimpleNamespace, SimpleNamespace(), backend=backend)
    reply = asyncio.run(server.tools['g6_library_decide'](entry.entry_id, action, reason, SimpleNamespace()))
    text = json.dumps(reply)
    assert reason not in text
    assert person not in text
    assert result.reasons == (reason,)
    if local:
        assert local == [{'reason': reason, 'person': person}]
    decision = reply.get('decision', reply)
    assert decision['verdict'] == result.verdict
    assert decision['entry_id'] == entry.entry_id


def test_public_decision_omits_all_unrecognized_dialog_fields():
    raw = {'verdict': 'withdraw', 'candidate_id': 'a' * 64, 'entry_id': 'b' * 64,
           'reasons': ('private reason',), 'reason': 'private reason', 'person': 'Private Owner',
           'decided_by': {'person': 'Private Owner'}, 'future_note': {'dialog': 'private reason'}}
    public = library.public_decision(raw)
    assert 'private reason' not in json.dumps(public)
    assert 'Private Owner' not in json.dumps(public)
    assert public['entry_id'] == raw['entry_id']
    assert raw['reason'] == 'private reason'
