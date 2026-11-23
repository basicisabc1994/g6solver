# SPDX-License-Identifier: Apache-2.0
"""Inventory every persisted record and every text field affected by normalization."""
import json

import pytest
from pydantic import ValidationError

from g6.core.ledger import canonical, digest
from g6.distill import records as r
from g6.distill.ledger import RecordStore, Stores
from tests.distill.test_records import bodies

TEXT_FIELDS = [
    ('concept', 'statement'), ('concept', 'falsifier'),
    ('contract', 'assumptions'), ('contract', 'escape_family'), ('contract', 'withdrawal_conditions'),
    ('candidate', 'assumptions'), ('candidate', 'escape_family'), ('candidate', 'withdrawal_conditions'),
]


def legacy_body(kind, field):
    index = {'concept': 1, 'contract': 2, 'candidate': 3}[kind]
    model, data = bodies()[index]
    data = json.loads(canonical(data))
    target = data['contract'] if kind == 'candidate' else data
    value = target[field]
    target[field] = ['  ' + value[0].replace(' ', '  ') + '\n'] if isinstance(value, list) else (
        '  ' + value.replace(' ', '  ') + '\n')
    ident = digest(data)
    if model.identity_field:
        data[model.identity_field] = ident
    return model, data, ident


@pytest.mark.parametrize('kind,field', TEXT_FIELDS)
def test_all_legacy_text_fields_retain_ledger_and_object_identity(monkeypatch, tmp_path, kind, field):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    model, body, ident = legacy_body(kind, field)
    store = Stores().for_model(model)
    assert store._validate(kind, ident, body, {}) == []
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.chain.append(kind, ident, body)
    obj = store._object(ident)
    obj.parent.mkdir(parents=True, exist_ok=True)
    obj.write_text(canonical(body) + '\n', encoding='utf-8')
    value, = store._read(references=False)
    assert value.content_id == ident
    assert value.model_dump(mode='json') == body
    assert store._validate(kind, digest('wrong'), body, {})
    # Explicit identities preserve nested historical contracts outside the reader too.
    if model.identity_field:
        assert model.model_validate_json(value.model_dump_json()).content_id == ident


def test_every_record_kind_still_checks_its_stored_digest():
    inventory = {model: body for model, body in bodies()}
    assert set(inventory) == set(r.RECORD_KINDS.values())
    for kind, model in r.RECORD_KINDS.items():
        value = model(**inventory[model])
        store = object.__new__(RecordStore)
        store.kind, store.model = kind, model
        body = value.model_dump(mode='json')
        assert store._validate(kind, value.content_id, body, {}) == []
        assert store._validate(kind, digest('wrong'), body, {})
        assert store._validate('unknown-kind', value.content_id, body, {})
        assert store._validate(kind, value.content_id, body | {'unknown': True}, {})


@pytest.mark.parametrize('kind,field', TEXT_FIELDS)
def test_legacy_mode_never_accepts_a_changed_body(kind, field):
    model, body, ident = legacy_body(kind, field)
    store = object.__new__(RecordStore)
    store.kind, store.model = kind, model
    assert store._validate(kind, ident, body | {'family': 'changed'}, {})


@pytest.mark.parametrize('kind,field', TEXT_FIELDS)
def test_normalization_cannot_rescue_an_identified_body(kind, field):
    model, body, _ = legacy_body(kind, field)
    fresh_body = {key: value for key, value in body.items() if key != model.identity_field}
    fresh = model.model_validate_json(canonical(fresh_body))
    assert fresh.content_id != digest(fresh_body)
    if model.identity_field:
        # These bytes hash to a different identity, even though their fresh
        # normalized proposal would match the supplied canonical identity.
        with pytest.raises(ValidationError, match='identity'):
            model.model_validate_json(canonical(body | {model.identity_field: fresh.content_id}))
    store = object.__new__(RecordStore)
    store.kind, store.model = kind, model
    unidentified = {key: value for key, value in body.items() if key != model.identity_field}
    assert store._validate(kind, fresh.content_id, unidentified, {})


def test_explicit_candidate_identity_preserves_only_its_nested_contract():
    _, body, ident = legacy_body('candidate', 'assumptions')
    row = r.VerifierCandidate(**body)
    assert row.content_id == ident
    assert row.contract.assumptions == body['contract']['assumptions']
    fresh = r.VerifierCandidate(**{key: value for key, value in body.items() if key != 'candidate_id'})
    assert fresh.contract.assumptions == ['static only']
    with pytest.raises(ValidationError, match='identity'):
        r.VerifierCandidate(**(body | {'candidate_id': digest('wrong')}))
    assert r.MethodContract(**body['contract']).assumptions == ['static only']


@pytest.mark.parametrize('mode', ['json', 'python'])
@pytest.mark.parametrize('stored', [False, True])
def test_contract_text_context_preserves_nested_strict_counts(mode, stored):
    body = json.loads(canonical(bodies()[3][1]))
    body['contract']['cost_bound']['probe_runs'] = True
    context = {'distill_stored_record': stored}
    with pytest.raises(ValidationError, match='int_type'):
        if mode == 'json':
            r.VerifierCandidate.model_validate_json(canonical(body), context=context)
        else:
            r.VerifierCandidate.model_validate(body, context=context)


def test_historical_references_and_new_library_versions_remain_readable(monkeypatch, tmp_path):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    stores = Stores()
    failure = r.FailureRecord(**bodies()[0][1])
    concept_model, concept_body, concept_id = legacy_body('concept', 'statement')
    contract_model, contract_body, contract_id = legacy_body('contract', 'assumptions')
    candidate_body = bodies()[3][1] | {'concept_id': concept_id, 'contract': contract_body}
    candidate_id = digest(candidate_body)
    candidate_body['candidate_id'] = candidate_id
    entry_body = bodies()[4][1] | {'concept_id': concept_id, 'candidate_id': candidate_id,
                                  'contract_sha256': contract_id}
    entry_id = digest(entry_body)
    entry_body['entry_id'] = entry_id
    rows = [(r.FailureRecord, failure.model_dump(mode='json'), failure.content_id),
            (concept_model, concept_body, concept_id), (contract_model, contract_body, contract_id),
            (r.VerifierCandidate, candidate_body, candidate_id), (r.LibraryEntry, entry_body, entry_id)]
    # A failure store opened before its directory exists has an in-memory read
    # chain until the public writer acquires custody. Persist that prerequisite
    # through put; historical imports below only exercise the changed text.
    stores.for_model(r.FailureRecord).put(failure)
    for model, body, ident in rows[1:]:
        store = stores.for_model(model)
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.chain.append(store.kind, ident, body)
        obj = store._object(ident)
        obj.parent.mkdir(parents=True, exist_ok=True)
        obj.write_text(canonical(body) + '\n', encoding='utf-8')
    for model, _, ident in rows:
        assert stores.for_model(model).get(ident).content_id == ident
    entry = stores.for_model(r.LibraryEntry).get(entry_id)
    newer = r.LibraryEntry(**(entry.model_dump(mode='json') | {
        'entry_id': None, 'version': 2, 'status': 'withdrawn', 'authority': 'A0'}))
    stores.for_model(r.LibraryEntry).put(newer)
    assert stores.for_model(r.LibraryEntry).get(newer.entry_id) == newer
    # Context from either successful or failed historical reads cannot escape.
    assert r.Concept(**(bodies()[1][1] | {'statement': ' Lost  assertions weaken tests '})).content_id == (
        r.Concept(**bodies()[1][1]).content_id)
