"""Replay comparison keeps action refusal, failed reads and evaluated violations distinct."""
import pytest

from g6.harness import verification_formal as formal, verification_search as search


def replay(outcome, why=''):
    return {'result': 'failed' if outcome in ('broken', 'operation_failed') else 'passed', 'detail': 'replayed',
            'observations': [{'input': 0, 'output': {'outcome': outcome, 'why': why, 'broken': ['nonnegative']}}]}


@pytest.mark.parametrize('why', ['reading balance raised AttributeError', '', 'unidentified failure'])
def test_failed_base_read_or_unknown_phase_does_not_establish_step_refusal(why):
    found = formal._judge(1, 'preserve', replay('broken'), replay('operation_failed', why))
    assert found['result'] == search.CHECK_ERROR
    assert 'refused the step' not in found['detail']
    assert 'steps_refused_at_base' not in found['correspondence']


def test_evaluated_base_violation_is_not_unreplayable():
    found = formal._judge(1, 'preserve', replay('operation_failed', 'step 1 (action 1) raised ValueError'),
                          replay('broken'))
    assert found['result'] == search.CHECK_ERROR
    assert 'could not replay' not in found['detail']
    assert 'traces_not_replayable_at_base' not in found['correspondence']
    assert 'base evaluated a violation' in found['detail']
