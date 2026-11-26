# SPDX-License-Identifier: Apache-2.0
"""Breaks caught: stale facts, mutable snapshots, invented provenance and clock drift."""
import importlib
from pathlib import Path

import pytest
from pydantic import ValidationError


@pytest.fixture
def model():
    assert (Path(__file__).parents[2] / 'src/g6/self_model/model.py').is_file(), 'self-model data API is missing'
    return importlib.import_module('g6.self_model.model')


def fact(model, value=7, **kw):
    return model.Field.measured(value, source='fixture.measure', observed_at=100, ttl_s=10, **kw)


def test_freshness_has_explicit_clock_and_no_future_facts(model):
    field = fact(model)
    assert field.read(now=100) == 7
    assert field.read(now=110) == 7
    assert field.read(now=110.001) == model.UNKNOWN
    assert field.read(now=99) == model.UNKNOWN


@pytest.mark.parametrize('kwargs', [dict(ttl_s=-1), dict(ttl_s=float('nan')), dict(observed_at=float('inf')),
                                    dict(source='')])
def test_invalid_measurement_metadata_is_refused(model, kwargs):
    options = dict(source='fixture.measure', observed_at=100, ttl_s=10)
    options.update(kwargs)
    with pytest.raises((ValidationError, ValueError)):
        model.Field.measured(7, **options)


def test_leaf_values_are_immutable_and_digest_is_checked(model):
    field = fact(model, ('a', 'b'))
    assert field.read(now=100) == ('a', 'b')
    with pytest.raises(ValidationError):
        field.value = ('changed',)
    with pytest.raises((ValidationError, ValueError)):
        fact(model, {'nested': []})
    body = field.model_dump()
    body['value'] = 'forged'
    with pytest.raises((ValidationError, ValueError), match='digest'):
        model.Field(**body)


def test_snapshot_carries_all_parts_and_digest_is_deterministic(model):
    named = model.NamedField(name='stop', field=fact(model, False))
    capability = model.NamedField(name='precision', field=fact(model, .95))
    component = model.Component(name='checker', capabilities=(capability,), limits=(named,))
    snapshot = model.SelfModel(components=(component,), state=(named,), goals=(named,), commitments=(named,),
                               environment=(named,), authority=(named,))
    assert snapshot.read(now=100)['state']['stop'] is False
    assert snapshot.read(now=111)['state']['stop'] == model.UNKNOWN
    assert snapshot.sha256(now=100) == snapshot.sha256(now=100)
    assert snapshot.sha256(now=100) != snapshot.sha256(now=111)
    with pytest.raises((ValidationError, ValueError), match='duplicate'):
        model.SelfModel(state=(named, named))
    with pytest.raises(ValidationError):
        model.SelfModel(unrecognised='silently lost')


def test_predictions_cannot_be_read_as_measurements(model):
    field = model.Field.predicted(.99, source='g6.self_model.simulate', observed_at=100, ttl_s=10)
    assert field.read(now=100) == .99
    assert field.read(now=100, evidence=True) == model.UNKNOWN
    assert field.origin == 'self_prediction'
