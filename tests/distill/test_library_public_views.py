# SPDX-License-Identifier: Apache-2.0
"""Every library entry leaving local custody uses one redacted view."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from g6.core.ledger import ChainLedger
from g6.distill import library as library_module, records as r, surface
from tests.distill.test_records import bodies


@pytest.fixture
def public_entry(monkeypatch):
    failure = r.FailureRecord(**(bodies()[0][1] | {'provenance': {'scope': 'g6_public_archive'}}))
    concept = r.Concept(**(bodies()[1][1] | {'from_failures': [failure.record_id]}))
    candidate = r.VerifierCandidate(**(bodies()[3][1] | {'concept_id': concept.concept_id}))
    entry = r.LibraryEntry(**(bodies()[4][1] | dict(candidate_id=candidate.candidate_id,
        concept_id=concept.concept_id, contract_sha256=candidate.contract.content_id,
        decided_by={'gate_version': 'fixture', 'person': 'Private Person'}, reason='Private dialog reason')))
    rows = {type(row): [row] for row in (failure, concept, candidate, candidate.contract, entry)}
    def for_model(model):
        return SimpleNamespace(list=lambda: rows[model], get=lambda ident: next(
            row for row in rows[model] if row.content_id == ident))
    stores = SimpleNamespace(for_model=for_model)
    monkeypatch.setattr(library_module, 'Stores', lambda: stores)
    monkeypatch.setattr(surface, 'Stores', lambda: stores)
    monkeypatch.setattr(surface, 'ChainLedger', lambda *args: ChainLedger())
    return entry


@pytest.mark.parametrize('op', ['list', 'describe', 'history', 'export', 'mcp'])
def test_library_serializers_redact_decision_data(public_entry, op):
    if op == 'export':
        result = library_module.Library().export(public_entry.entry_id)
    elif op == 'mcp':
        from g6.mcp import distill
        class Server:
            def __init__(self):
                self.tools = {}
            def tool(self, **kwargs):
                def register(fn):
                    self.tools[fn.__name__] = fn
                    return fn
                return register
        server = Server()
        distill.register(server, SimpleNamespace, SimpleNamespace(), backend=SimpleNamespace())
        result = server.tools['g6_library']('describe', public_entry.entry_id)
    else:
        result = surface.library(op, '' if op == 'list' else public_entry.entry_id)
    if asyncio.iscoroutine(result):
        result = asyncio.run(result)
    text = json.dumps(result)
    assert 'Private Person' not in text
    assert 'Private dialog reason' not in text
    assert public_entry.decided_by.person == 'Private Person'
