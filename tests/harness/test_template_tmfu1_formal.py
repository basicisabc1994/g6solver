"""Base comparison identifies the failed operation and describes only listed traces."""
import pytest

from g6.harness import verification_formal as formal, verification_search as search
from tests.harness.test_template_followup_formal import replay


@pytest.mark.parametrize('base_why', ['reading balance raised ValueError', 'step 2 (action 1) raised ValueError',
                                     'unidentified failure', ''])
def test_different_or_unknown_operation_is_not_the_same_base_failure(base_why):
    found = formal._judge(1, 'preserve', replay('operation_failed', 'step 1 (action 1) raised ValueError'),
                          replay('operation_failed', base_why))
    assert found['result'] == search.CHECK_ERROR
    assert 'not established at the base commit' in found['detail']
    assert found['correspondence']['at_base_too'] == []


def test_same_read_failure_ignores_only_exception_type():
    found = formal._judge(1, 'preserve', replay('operation_failed', 'reading balance raised ValueError'),
                          replay('operation_failed', 'reading balance raised AttributeError'))
    assert found['result'] == search.FAILS_ON_BASE_TOO


def test_mixed_traces_describe_only_the_unknown_operation_failure():
    patch = replay('broken')
    patch['observations'].append({'input': 1, 'output': {'outcome': 'operation_failed',
                                                       'why': 'step 1 (action 1) raised ValueError'}})
    base = replay('broken')
    base['observations'].append({'input': 1, 'output': {'outcome': 'not_replayable', 'why': 'unknown'}})
    found = formal._judge(2, 'preserve', patch, base)
    assert 'trace(s) 1 fail during an operation (their invariants were not evaluated)' in found['detail']
    assert 'or break evaluated invariants' not in found['detail']
