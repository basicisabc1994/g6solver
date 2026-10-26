# SPDX-License-Identifier: Apache-2.0
"""Execution is not independence or applicability: replay the public MCP defect."""
from __future__ import annotations

import copy
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from g6.workforce import acceptance as wa
from g6.workforce import plans, view


ARCHIVE = Path(__file__).resolve().parents[3] / 'docs/validation/autonomous-business/mcp-value-baseline/raw.zip'


def observed():
    if not ARCHIVE.is_file():
        pytest.skip('the recorded MCP baseline is in the source repository only')
    with zipfile.ZipFile(ARCHIVE) as source:
        snapshot = json.loads(source.read('attempt-03/baseline.json'))['snapshots'][-1]
    return next(e['detail']['acceptance'] for e in snapshot['events'] if e['kind'] == 'mission.closed')


def measure(records, predicate='tests_passed'):
    facts = wa.Facts(do_units=('patch',), outputs={'patch': {}}, checks={'patch': tuple(records)},
                     approvals={}, sends={}, degradations={})
    return wa.measure(wa.requirement('duplicate delivery', predicate, {'unit': 'patch'}), facts)


def test_executed_caller_tests_remain_unmeasured_but_are_not_reported_absent():
    record = observed()['records'][0]
    assert record['executed'] and not record['independent']
    found = measure([record])
    assert found.status == wa.UNMEASURED
    assert 'passed' in found.why and 'not independent' in found.why
    assert 'no test ran' not in found.why
    evaluation = SimpleNamespace(requirements=(found,), verification=(), limits=(), records=(record,))
    caveats = view._caveats(evaluation, None, [], {'patch': 'Repair webhook'})
    assert any('Not established: duplicate delivery' in text for text in caveats)


@pytest.mark.parametrize(('changes', 'status', 'phrase'), [
    ({'independent': True}, wa.MET, '3 of 3 tests passed'),
    ({'result': 'fail'}, wa.UNMET, 'problem'),
    ({'executed': False}, wa.UNMEASURED, 'no test ran'),
    ({'set_aside': True}, wa.UNMEASURED, 'set aside'),
    ({'probe': {'status': 'not_applicable'}}, wa.UNMEASURED, 'not applicable'),
    ({'result': 'unmeasured'}, wa.UNMEASURED, 'inconclusive'),
])
def test_execution_and_independence_controls_preserve_the_acceptance_gate(changes, status, phrase):
    record = observed()['records'][0] | changes
    found = measure([record])
    assert found.status == status
    assert phrase in found.why


def test_nonapplicable_regression_is_visible_without_a_success_claim():
    records = tuple(observed()['records'])
    before = copy.deepcopy(records)
    checks = view._checks(records, {'patch': 'Repair webhook'}, None)
    assert any('3 of 3 passed' in c.what and c.passed is True for c in checks)
    assert not any('recomputed the result' in c.what for c in checks)
    evaluation = SimpleNamespace(requirements=(), verification=(), limits=(), records=records)
    assert any('not applicable' in c for c in view._caveats(evaluation, None, [], {'patch': 'Repair webhook'}))
    assert len(checks) == 1  # the shared 'Checks performed' count must exclude N/A
    finding = measure([records[1]], 'check_passed')
    assert finding.status == wa.UNMEASURED and 'not applicable' in finding.why
    assert records == before  # rendering never rewrites receipts


@pytest.mark.parametrize('changes', [{'executed': False}, {'probe': {'status': 'not_applicable'}}])
def test_unperformed_checks_cannot_display_a_pass_or_contribute_test_counts(changes):
    record = observed()['records'][0] | changes
    checks = view._checks((record,), {'patch': 'Repair webhook'}, None)
    assert not checks
    assert not any('3 of 3 passed' in c.what or 'Tests were run' in c.what for c in checks)
    assert view._tests((record,)) is None


def test_applicable_computation_remains_visible():
    record = observed()['records'][1] | {'probe': {'status': 'pass'}}
    checks = view._checks((record,), {'patch': 'Repair webhook'}, None)
    assert len(checks) == 1 and checks[0].passed is True
    assert 'recomputed the result' in checks[0].what


def test_old_test_receipts_cannot_explain_the_latest_unchecked_artifact_as_passed():
    record = observed()['records'][0]
    plan = plans.parse_plan({'units': [{'key': 'patch', 'kind': 'do', 'goal': 'Repair webhook',
                                       'output_schema': 'files'},
                                      {'key': 'check', 'kind': 'verify', 'verifies': 'patch', 'goal': 'Check patch',
                                       'capabilities': [['workforce', 'run_tests']],
                                       'oracle': {'kind': 'test', 'capability': ['workforce', 'run_tests'],
                                                  'bind': {'files': 'patch.files'},
                                                  'expect': {'field': 'passed', 'equals': True}}}], 'edges': []},
                            risk_tier='R0', budget_cents=0)
    facts = wa.gather(plan=plan, runs=[{'run_id': 'new', 'unit_key': 'patch', 'state': 'closed',
                                      'artifact_id': 'new-artifact'}],
                      artifacts={'new-artifact': {'content': {}}},
                      closed=[{'run_id': 'old-check', 'verification': [record]}], decisions=[], receipts=[])
    found = wa.measure(wa.requirement('duplicate delivery', 'tests_passed', {'unit': 'patch'}), facts)
    assert found.status == wa.UNMEASURED and found.why == 'no test ran on its latest result'
    assert view._tests(tuple(facts.checks.get('patch', ()))) is None
