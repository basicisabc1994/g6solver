# SPDX-License-Identifier: Apache-2.0
"""Every content-addressed distill Record survives its store's JSON boundary."""
import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from g6.core.ledger import canonical
from g6.distill import records as r
from g6.distill.ledger import Stores
from g6.distill.meta.prediction_ledger import MetaLedger, _body
from g6.distill.meta.records import MetaPrediction, SynthesisPolicy
from tests.distill.test_meta import outcome, record
from tests.distill.test_records import bodies


def test_every_content_addressed_record_has_a_store_round_trip():
    root = Path(__file__).parents[2] / 'src/g6/distill'
    declared = set()
    for path in root.glob('**/*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        bases = {'Record'} if path == root / 'records.py' else set()
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module == 'g6.distill.records':
                bases.update(alias.asname or alias.name for alias in node.names if alias.name == 'Record')
        # held_out.case_gate.Record is a separate base without content_id or a
        # RecordStore; include only descendants of distill.records.Record.
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and any(
                    isinstance(base, ast.Name) and base.id in bases for base in node.bases):
                declared.add(node.name)
                bases.add(node.name)
    covered = {*r.RECORD_KINDS.values(), MetaPrediction, SynthesisPolicy}
    assert declared == {model.__name__ for model in covered}
    assert set(r.Record.__subclasses__()) == covered
    assert {model for model, _ in bodies()} == set(r.RECORD_KINDS.values())


def test_every_record_store_append_and_read_preserves_content_id(monkeypatch, tmp_path):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    stores = Stores()
    values = [model(**body) for model, body in bodies()]
    for value in values:
        ident = value.content_id
        store = stores.for_model(type(value))
        assert store.put(value) == ident
        assert store.get(ident).content_id == ident
        assert store.list() == [value]
        reopened = Stores().for_model(type(value))
        assert reopened.get(ident).model_dump(mode='json') == value.model_dump(mode='json')
        assert reopened.list()[0].content_id == ident
        assert reopened.chain.entries()[0]['id'] == ident


@pytest.mark.parametrize('memory', [True, False], ids=['memory', 'disk'])
def test_every_meta_record_append_and_read_preserves_content_id(tmp_path, memory):
    store = MetaLedger(tmp_path / 'meta.jsonl', memory=memory)
    # On repair 1 this real append raises LedgerError: simulation.affected and
    # five other strict tuple fields receive Python lists from the wrap handler.
    prediction = record(store)
    policy = SynthesisPolicy()
    assert store.record_data(policy) == policy.content_id
    assert store.predictions() == (prediction,)
    assert store.predictions()[0].content_id == prediction.content_id
    assert store.data(SynthesisPolicy) == (policy,)
    assert store.data(SynthesisPolicy)[0].content_id == policy.content_id
    assert store.chain.entries()[0]['id'] == prediction.content_id
    scored = store.score(prediction.decision_id, outcome(prediction), now=103.)
    assert store.scores() == (scored,)
    assert store.predictions()[0].content_id == prediction.content_id
    if not memory:
        reopened = MetaLedger(store.path)
        assert reopened.predictions() == (prediction,)
        assert reopened.predictions()[0].content_id == prediction.content_id
        assert reopened.data(SynthesisPolicy)[0].content_id == policy.content_id
        assert reopened.scores() == (scored,)


@pytest.mark.parametrize('stored', [False, True])
def test_stored_text_context_keeps_nested_json_types_and_python_strictness(stored):
    prediction = record(MetaLedger(memory=True))
    body = _body(prediction)
    context = {'distill_stored_record': stored}
    restored = MetaPrediction.model_validate_json(canonical(body), context=context)
    assert restored.content_id == prediction.content_id
    assert isinstance(restored.simulation.affected, tuple)
    assert isinstance(restored.simulation.risk.reasons, tuple)
    # Stored text never grants Python lists the strict tuple type.
    with pytest.raises(ValidationError, match='tuple_type'):
        MetaPrediction.model_validate(body, context=context)
    invalid = _body(prediction)
    invalid['simulation']['affected'] = 'a'
    with pytest.raises(ValidationError):
        MetaPrediction.model_validate_json(canonical(invalid), context=context)
