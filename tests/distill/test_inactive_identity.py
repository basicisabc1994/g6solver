# SPDX-License-Identifier: Apache-2.0
"""Inactive methods retain their identity across descriptive changes."""
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from g6.core.ledger import ChainLedger, digest
from g6.distill import acceptance_gate as gate, records as r
from g6.distill.library import Library
from tests.distill.test_records import bodies


def candidate(**updates):
    return r.VerifierCandidate(**(bodies()[3][1] | updates))


@pytest.mark.parametrize('status', ['withdrawn', 'held'])
@pytest.mark.parametrize('change', ['whitespace', 'keys', 'comment', 'oracle_name', 'observation'])
def test_equivalent_method_blocks_before_any_look(monkeypatch, status, change):
    first = candidate(form='template_instance', body={'template': 'swallow.v1', 'bindings': {}})
    data = first.model_dump(exclude={'candidate_id'})
    # Always defeat the old metadata comparisons; executable terms remain fixed.
    data['concept_id'] = digest('new observation')
    data['training_case_ids'] = [digest('new case')]
    data['contract']['escape_family'] = 'different descriptive escape'
    if change == 'whitespace':
        data['contract']['assumptions'] = [' static\n  only ']
    elif change == 'keys':
        data['body'] = dict(reversed(list(data['body'].items())))
    elif change == 'comment':
        first = candidate()
        data.update(form='predicate', body=first.body + '\n# same method\n')
    elif change == 'oracle_name':
        # These registered names have identical detector bindings.
        data['body']['template'] = 'self_reference.v1'
    second = r.VerifierCandidate(**data)
    entry = r.LibraryEntry(**(bodies()[4][1] | dict(candidate_id=first.candidate_id,
        concept_id=first.concept_id, contract_sha256=first.contract.content_id,
        status=status, authority='A0')))
    rows = {r.LibraryEntry: [entry], r.VerifierCandidate: [first]}

    def for_model(model):
        values = rows.get(model, [])
        return SimpleNamespace(list=lambda: values,
            get=lambda ident: next(row for row in values if row.content_id == ident))

    stores = SimpleNamespace(for_model=for_model)
    monkeypatch.setattr(gate, 'Stores', lambda: stores)
    monkeypatch.setattr(Library, 'list', lambda self: [entry])
    monkeypatch.setattr(gate, '_writer', nullcontext)
    monkeypatch.setattr(gate, '_journal', lambda: ChainLedger())
    monkeypatch.setattr(gate, '_policy', lambda: None)
    monkeypatch.setattr(gate.stop, 'engaged', lambda: None)
    looks = []
    monkeypatch.setattr(gate, '_look_available', lambda *args: looks.append(args))
    result = gate.decide(second, Library(), digest('registration'))
    assert not looks, 'equivalent inactive method reached the look boundary'
    assert status in ' '.join(result.reasons)


@pytest.mark.parametrize('status', ['withdrawn', 'held'])
def test_different_execution_does_not_match_inactive_method(monkeypatch, status):
    first = candidate()
    second = candidate(body="def solve(facts):\n return {'verdict': 'concern'}")
    entry = r.LibraryEntry(**(bodies()[4][1] | dict(candidate_id=first.candidate_id,
        concept_id=first.concept_id, contract_sha256=first.contract.content_id,
        status=status, authority='A0')))
    def for_model(model):
        return SimpleNamespace(list=lambda: [entry], get=lambda ident: first)
    monkeypatch.setattr(gate, 'Stores', lambda: SimpleNamespace(for_model=for_model))
    assert not gate._inactive_match(second, [entry], status)


def test_single_child_composition_cannot_disguise_withdrawal(monkeypatch):
    first = candidate()
    entry = r.LibraryEntry(**(bodies()[4][1] | dict(candidate_id=first.candidate_id,
        concept_id=first.concept_id, contract_sha256=first.contract.content_id,
        status='withdrawn', authority='A0')))
    second = candidate(form='composition', body={'op': 'any', 'entries': [entry.entry_id], 'k': None})
    def for_model(model):
        return SimpleNamespace(get=lambda ident: entry if model is r.LibraryEntry else first)
    monkeypatch.setattr(gate, 'Stores', lambda: SimpleNamespace(for_model=for_model))
    assert gate._inactive_match(second, [entry], 'withdrawn')


def test_concept_and_contract_text_have_canonical_digests():
    concept = r.Concept(**bodies()[1][1])
    padded = r.Concept(**(bodies()[1][1] | dict(
        statement=' Lost\n assertions   weaken tests ',
        falsifier=' Equivalent assertions moved\tto a helper ')))
    assert concept.content_id == padded.content_id
    contract = r.MethodContract(**bodies()[2][1])
    padded = r.MethodContract(**(bodies()[2][1] | dict(assumptions=[' static\n only '],
        escape_family=' helper\tchanges ', withdrawal_conditions=[' regression '])))
    assert contract.content_id == padded.content_id
