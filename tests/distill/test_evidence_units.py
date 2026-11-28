# SPDX-License-Identifier: Apache-2.0
"""Bug units, rather than case or event IDs, govern evidence size."""
import json
from dataclasses import replace

import pytest

from g6.core.ledger import digest
from g6.core.llm import FakeLLM
from g6.distill.meta.policy_gate import SynthesisReplay
from g6.distill.meta.records import ReplaySequence
from g6.distill.records import FailureRecord
from g6.metaharness.schema import Model
from tests.distill.test_records import bodies
from tests.distill import test_acceptance_measurements

sample = test_acceptance_measurements.sample


def failure(i, unit):
    return FailureRecord(**(bodies()[0][1] | dict(case_ref=digest(i),
        provenance={} if unit is None else {'unit_id': unit, 'cohort': 'development'})))


def sequence(row, i, target=0.):
    return ReplaySequence(case_id=str(i), failure_ids=(row.record_id,), utility_target=target)


@pytest.mark.parametrize('unit', [None, '', '   '])
def test_missing_meta_unit_is_reported_as_refused(unit):
    row = failure(1, unit)
    replay = SynthesisReplay(failures=[row], replay=lambda *args: pytest.fail('refused evidence ran'))
    workflow = replay.workflow(name='missing-unit', dev=(), held_out=(sequence(row, 1),),
        model=Model(FakeLLM(default='unused')))
    assert not workflow.suite.held_out
    assert 'unit' in workflow.suite.provenance['refused']


def test_meta_cases_are_pooled_within_one_bug_before_statistics():
    rows = [failure(i, 'one-bug') for i in range(30)]
    replay = SynthesisReplay(failures=rows, replay=lambda *args: None)
    workflow = replay.workflow(name='pooled', dev=(), held_out=tuple(sequence(row, i) for i, row in enumerate(rows)),
        model=Model(FakeLLM(default='unused')))
    assert len(workflow.suite.held_out) == 1
    request = json.loads(workflow.suite.held_out[0].input)
    assert set(request['failure_ids']) == {row.record_id for row in rows}


def test_dev_bug_never_reappears_in_held_out():
    rows = [failure(i, 'same-bug') for i in range(2)]
    replay = SynthesisReplay(failures=rows, replay=lambda *args: None)
    workflow = replay.workflow(name='overlap', dev=(sequence(rows[0], 0),), held_out=(sequence(rows[1], 1),),
        model=Model(FakeLLM(default='unused')))
    assert not workflow.suite.held_out
    assert 'overlap' in workflow.suite.provenance['refused']


def test_acceptance_rejects_unit_overlap_before_measurement(sample, monkeypatch):
    gate, candidate, frozen, _ = sample
    observation = replace(frozen.monitor[0], case=frozen.monitor[0].case.model_copy(
        update={'unit_id': frozen.acceptance[0].case.unit_id}))
    monkeypatch.setattr(gate, '_measure', lambda *args: pytest.fail('overlapping units ran'))
    with pytest.raises(ValueError, match='unit.*overlap'):
        gate._assess(candidate, [], replace(frozen, monitor=(observation,)), 1)


def test_field_fires_are_pooled_by_bug():
    from g6.distill import acceptance_gate as gate
    rows = [dict(kind='field', body=dict(candidate_id='a' * 64, fire_id=str(i), unit_id='one-bug',
        source='oracle', label='refuted' if i == 29 else 'confirmed', receipt_sha256=digest(i)))
        for i in range(30)]
    assert gate._field_counts(rows, 'a' * 64) == (0, 1, 0)


def test_field_fire_without_unit_is_refused():
    from g6.distill import acceptance_gate as gate
    rows = [dict(kind='field', body=dict(candidate_id='a' * 64, fire_id='f',
        source='oracle', label='confirmed', receipt_sha256=digest('receipt')))]
    with pytest.raises(ValueError, match='unit'):
        gate._field_counts(rows, 'a' * 64)


def test_mixed_positive_and_negative_cases_count_once_for_a2(sample):
    gate, candidate, frozen, _ = sample
    mixed = (frozen.acceptance[0], replace(frozen.acceptance[100],
        case=frozen.acceptance[100].case.model_copy(update={'unit_id': frozen.acceptance[0].case.unit_id})))
    assert gate._counts(mixed, ['concern', 'no_concern'])['n_units'] == 1
    evidence = gate._assess(candidate, [], frozen, 1)[1]
    # Enough positive/negative strata are not enough distinct bugs.
    evidence = evidence.model_copy(update={'n_units': 20})
    field = gate.FieldEvidence(fired=30, confirmed=30, refuted=0, unlabelled=0,
        precision=gate._interval(30, 30))
    with pytest.raises(ValueError, match='held-out'):
        gate._a2_bar(evidence, field)


@pytest.mark.parametrize('change', ['duplicate', 'count'])
def test_merge_duplicate_units_or_inflated_counts_are_refused(change):
    from g6.distill.merge_evidence_gate import FROZEN_POLICY
    from g6.self_training.merge_measurement_gate import check_evaluation
    from tests.self_training.test_research_merge_gate import api, fixture
    *_, evidence = fixture()
    raw = evidence['evaluation']
    if change == 'duplicate':
        raw['units'] = [dict(unit_id='one-bug', base_success=False, candidate_success=True)] * 100
    else:
        raw['units'] = [dict(unit_id='one-bug', base_success=False, candidate_success=True)]
    assert 'unit' in check_evaluation(api().Evaluation(**raw), 'improvement', FROZEN_POLICY)


def test_merge_counts_require_bound_unit_pairs():
    from g6.distill.merge_evidence_gate import FROZEN_POLICY
    from g6.self_training.merge_measurement_gate import check_evaluation
    from tests.self_training.test_research_merge_gate import api, fixture
    *_, evidence = fixture()
    raw = evidence['evaluation']
    raw.pop('units', None)
    result = check_evaluation(api().Evaluation(**raw), 'improvement', FROZEN_POLICY)
    assert 'unit' in result


def test_merge_success_rates_must_describe_the_paired_units():
    from g6.distill.merge_evidence_gate import FROZEN_POLICY
    from g6.self_training.merge_measurement_gate import check_evaluation
    from tests.self_training.test_research_merge_gate import api, fixture
    *_, evidence = fixture()
    raw = evidence['evaluation']
    raw['measures']['success'].update(before=.4, after=.6)
    assert 'unit' in check_evaluation(api().Evaluation(**raw), 'improvement', FROZEN_POLICY)


@pytest.mark.parametrize('units', [1, None])
def test_review_observation_reports_unique_units(units):
    from g6.distill.records import LibraryEntry
    from g6.harness.library_review_gate import observation
    body = bodies()[4][1]
    entry = LibraryEntry(**(body | {'evidence': body['evidence'] | {'n_units': units}}))
    assert observation(entry, 'concern')['held_out_n'] == units


