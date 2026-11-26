# SPDX-License-Identifier: Apache-2.0
"""Breaks caught: silent discrepancies, duplicate rows, and unsupported explanations."""
import importlib
from pathlib import Path

import pytest

from g6.self_model.model import Field, UNKNOWN


@pytest.fixture
def monitor():
    assert (Path(__file__).parents[2] / 'src/g6/self_model/monitor.py').is_file(), 'monitor API is missing'
    return importlib.import_module('g6.self_model.monitor')


def field(value, at=100, ttl=100):
    return Field.measured(value, source='fixture.measure', observed_at=at, ttl_s=ttl)


def observation(monitor, value=.2, **kw):
    return monitor.Observation(observation_id='obs-1', signal='ci_pass_rate', category='evaluate',
                               measurement=field(value), **kw)


def expectation(monitor):
    return monitor.Expectation(signal='ci_pass_rate', lower=field(.8, at=90), upper=field(1., at=90))


def test_discrepancy_is_one_failure_record_shaped_row(monitor):
    rows = monitor.compare(observation(monitor), expectation(monitor), now=100)
    assert len(rows) == 1
    row = rows[0]
    assert row.kind == row.cause == 'self_model_discrepancy'
    assert row.case_id == 'obs-1'
    assert len(row.case_ref) == 64
    assert row.independently_observed is True
    assert row.observed_by == 'independent'
    assert row.split == 'monitor'
    assert row.domain_fields()['cause'] == 'self_model_discrepancy'
    assert len(row.record_id) == 64
    assert monitor.compare(observation(monitor), expectation(monitor), now=100) == rows
    assert monitor.compare(observation(monitor), expectation(monitor), now=100, seen=('obs-1',)) == ()


def test_no_discrepancy_no_row(monitor):
    assert monitor.compare(observation(monitor, .9), expectation(monitor), now=100) == ()


def test_newer_explanatory_environment_field_yields_only_update(monitor):
    explanation = monitor.Explanation(signal='ci_pass_rate', previous=field('old', 80),
                                      current=field('new', 95), reason='CI matrix changed')
    rows = monitor.compare(observation(monitor), expectation(monitor), now=100, explanation=explanation)
    assert len(rows) == 1
    assert rows[0].kind == 'self_model_update'
    assert rows[0].previous.value == 'old'
    assert rows[0].current.value == 'new'


@pytest.mark.parametrize('signal,at,value,ttl', [('unrelated', 95, 'new', 100), ('ci_pass_rate', 85, 'new', 100),
                                              ('ci_pass_rate', 95, 'old', 100), ('ci_pass_rate', 95, 'new', 1),
                                              ('ci_pass_rate', 101, 'new', 100)])
def test_unrelated_stale_or_older_explanation_cannot_swallow_failure(monitor, signal, at, value, ttl):
    explanation = monitor.Explanation(signal=signal, previous=field('old', 80),
                                      current=field(value, at, ttl), reason='declared explanation')
    assert monitor.compare(observation(monitor), expectation(monitor), now=100,
                           explanation=explanation)[0].kind == 'self_model_discrepancy'


def test_predictions_and_stale_fields_are_not_monitor_evidence(monitor):
    predicted = Field.predicted(.2, source='g6.self_model.simulate', observed_at=100, ttl_s=10)
    obs = monitor.Observation(observation_id='p', signal='ci_pass_rate', category='predict_outcome', measurement=predicted)
    with pytest.raises(ValueError, match='measured'):
        monitor.compare(obs, expectation(monitor), now=100)
    with pytest.raises(ValueError, match='fresh'):
        monitor.compare(observation(monitor), expectation(monitor), now=300)


def test_forged_environment_explanation_cannot_hide_discrepancy(monitor):
    previous = field('old', 80).model_copy(update={'value': 'forged'})
    explanation = monitor.Explanation(signal='ci_pass_rate', previous=field('old', 80),
                                      current=field('new', 95), reason='changed').model_copy(update={'previous': previous})
    assert monitor.compare(observation(monitor), expectation(monitor), now=100,
                           explanation=explanation)[0].kind == 'self_model_discrepancy'


def test_failure_intake_rows_never_declare_prediction_evidence_independent(monitor):
    predicted = Field.predicted(.2, source='simulator', observed_at=100, ttl_s=10)
    with pytest.raises(ValueError, match='measured'):
        monitor.FailureRecord(case_id='fake', case_ref='a' * 64, cause='prediction_failed', kind='prediction_failed',
                              category='predict_outcome', note='unmeasured', observed_at=100., evidence=(predicted,))


def test_binomial_history_learns_interval_and_empty_history_stays_unknown(monitor):
    assert monitor.expectation_from_history('ci_pass_rate', (), now=100, ttl_s=10) == UNKNOWN
    learned = monitor.expectation_from_history('ci_pass_rate', tuple(field(True) for _ in range(30)), now=100, ttl_s=10)
    assert .88 < learned.lower.value < .89
    assert learned.upper.value == 1.
    assert monitor.compare(observation(monitor), learned, now=100)[0].kind == 'self_model_discrepancy'
    with pytest.raises(ValueError, match='measured'):
        monitor.expectation_from_history('x', (Field.predicted(True, source='simulator', observed_at=100, ttl_s=1),),
                                         now=100, ttl_s=10)


@pytest.mark.parametrize('category', ['identify_task', 'check_progress', 'evaluate', 'predict_outcome'])
def test_typed_monitor_categories(monitor, category):
    obs = monitor.Observation(observation_id='o', signal='ci_pass_rate', category=category, measurement=field(.2))
    assert monitor.compare(obs, expectation(monitor), now=100)[0].category == category
