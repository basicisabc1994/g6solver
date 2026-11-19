"""Compatibility and refusal boundaries of receipt-backed learning."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core.component_process import ContainmentFailure, ExecutionInterrupted
from g6.harness import outcome_history, patch_report, patch_review, resolution
from g6.harness.behaviour_runner import ExecutableSource
from g6.workforce import receipt_store
from tests.harness.efu2_cases import FAILED, NODE, PASSED, report


def test_resolution_serializes_compiler_source_without_custody():
    source = ExecutableSource('def test_edge(): pass\n', {'kind': 'example'})
    assessment = {'requirements': [], 'completion': {'defects_reproduced': {'proposed_checks': [
        {'result': 'counterexample', 'requirement': 'R', 'check': 'edge',
         'reproduction': {'text': source}}]}}}
    got = resolution.build(assessment, repo='fixture', task='keep behavior')
    text = got[0]['cheapest_check']['arguments']['proposed_checks'][0]['source']
    assert type(text) is str and text == source
    assert assessment['completion']['defects_reproduced']['proposed_checks'][0]['reproduction']['text'] is source


@pytest.mark.parametrize('signal', [ExecutionInterrupted('stop', stopped=True), ContainmentFailure('cleanup')])
@pytest.mark.parametrize('operation', ['read', 'disclose', 'check_sources'])
def test_history_io_keeps_execution_control_signals(monkeypatch, signal, operation):
    def fail(*args, **kwargs):
        raise signal

    monkeypatch.setattr(outcome_history, 'repository_id', lambda root: 'a' * 64)
    if operation == 'read':
        monkeypatch.setattr(outcome_history, 'ChainLedger', fail)
        def invoke():
            return outcome_history.read(Path('fixture'))
    elif operation == 'disclose':
        monkeypatch.setattr(outcome_history, 'take_notice', fail)
        def invoke():
            return outcome_history.disclose({}, Path('fixture'))
    else:
        from g6.workforce import repo_tests

        file = SimpleNamespace(is_symlink=lambda: False, resolve=lambda: Path('fixture').resolve(),
                               is_file=lambda: True, stat=lambda: SimpleNamespace(st_size=1), read_bytes=fail)
        monkeypatch.setattr(repo_tests, '_disk', lambda *args: file)
        def invoke():
            return outcome_history.check_sources(Path('fixture'), ['test.py'])
    with pytest.raises(type(signal)) as caught:
        invoke()
    assert caught.value is signal


@pytest.mark.parametrize('signal', [ExecutionInterrupted('stop', stopped=True), ContainmentFailure('cleanup')])
@pytest.mark.parametrize('operation', ['repository_id', 'lookup_learning', 'list', 'forget'])
def test_learning_boundaries_keep_execution_control_signals(monkeypatch, signal, operation):
    def fail(*args, **kwargs):
        raise signal

    from g6.workforce import repo_tests

    if operation == 'repository_id':
        monkeypatch.setattr(repo_tests, '_git', fail)
        def invoke():
            return receipt_store.repository_id(Path('fixture'))
    elif operation == 'lookup_learning':
        monkeypatch.setattr(receipt_store, 'lookup', lambda *args, **kwargs: {'result': {}})
        monkeypatch.setattr(receipt_store, 'ChainLedger', fail)
        def invoke():
            return receipt_store.lookup_learning('owned', artifact_digest='a' * 64)
    elif operation == 'list':
        from g6.learning_history.block import HistoryInput, LearningHistoryBlock

        monkeypatch.setattr(repo_tests, 'checked_repo', lambda *args: Path('fixture'))
        monkeypatch.setattr(outcome_history, 'read', fail)
        def invoke():
            return LearningHistoryBlock().infer(HistoryInput(op='list', repo='fixture'))
    else:
        import asyncio

        from g6.mcp.learning_history import forget_reply

        monkeypatch.setattr(repo_tests, 'checked_repo', lambda *args: Path('fixture'))
        monkeypatch.setattr(outcome_history, 'read', fail)
        def invoke():
            return asyncio.run(forget_reply(None, '{"repo":"fixture"}',
                                            allow=frozenset({'learning_history.forget'})))
    with pytest.raises(type(signal)) as caught:
        invoke()
    assert caught.value is signal


@pytest.mark.parametrize('corruption', ['extra_phase', 'missing_teardown', 'false_completed', 'wrong_node'])
def test_history_phase_receipts_use_the_report_reader(corruption):
    raw = report(PASSED, completed=True, degraded=False)
    if corruption == 'extra_phase':
        raw['test_outcomes'][NODE]['unlisted'] = 'passed'
    elif corruption == 'missing_teardown':
        del raw['test_outcomes'][NODE]['teardown']
    elif corruption == 'false_completed':
        raw['completed'] = 1
    else:
        raw['collected_ids'] = ['test_other.py::test_other']
    body = {'source_before': 'd' * 64, 'source_after': 'd' * 64,
            'tests': {'collected': raw['collected_ids'], 'omitted': []},
            'test_outcomes': raw['test_outcomes'],
            'result': {key: value for key, value in raw.items() if key not in {
                'test_outcomes', 'collected_ids', 'omitted_ids'}}}
    assert outcome_history._status(body, NODE) == (False, False)


@pytest.mark.parametrize('passed', [True, False])
def test_history_accepts_complete_phase_receipts_with_the_producer_completion_flag(passed):
    raw = report(PASSED if passed else FAILED, completed=passed, degraded=False)
    body = {'source_before': 'd' * 64, 'source_after': 'd' * 64,
            'tests': {'collected': [NODE], 'omitted': []}, 'test_outcomes': raw['test_outcomes'],
            'result': {key: value for key, value in raw.items() if key not in {
                'test_outcomes', 'collected_ids', 'omitted_ids'}}}
    assert outcome_history._status(body, NODE) == (passed, not passed)


@pytest.mark.parametrize('passed', [True, False])
@pytest.mark.parametrize('location', ['body', 'result'])
def test_history_keeps_node_results_after_canonical_receipt_storage(passed, location):
    from g6.core.ledger import canonical

    raw = report(PASSED if passed else FAILED, completed=passed, degraded=False)
    body = {'source_before': 'd' * 64, 'source_after': 'd' * 64,
            'tests': {'collected': [NODE], 'omitted': []},
            'result': {key: value for key, value in raw.items() if key not in {
                'test_outcomes', 'collected_ids', 'omitted_ids'}}}
    target = body if location == 'body' else body['result']
    target['test_outcomes'] = raw['test_outcomes']
    stored = json.loads(canonical(body))
    assert outcome_history._status(stored, NODE) == (passed, not passed)


@pytest.mark.parametrize('corruption', ['extra_phase', 'missing_teardown', 'wrong_type', 'wrong_node',
                                     'degraded', 'false_completed'])
def test_stored_receipt_normalization_keeps_bad_evidence_unknown(corruption):
    from g6.core.ledger import canonical

    raw = report(PASSED, completed=True, degraded=False)
    if corruption == 'extra_phase':
        raw['test_outcomes'][NODE]['unknown'] = 'passed'
    elif corruption == 'missing_teardown':
        del raw['test_outcomes'][NODE]['teardown']
    elif corruption == 'wrong_type':
        raw['test_outcomes'][NODE] = ['passed']
    elif corruption == 'wrong_node':
        raw['collected_ids'] = ['test_other.py::test_other']
    elif corruption == 'degraded':
        raw['degraded'] = True
    else:
        raw['completed'] = 1
    body = {'source_before': 'd' * 64, 'source_after': 'd' * 64,
            'tests': {'collected': raw['collected_ids'], 'omitted': []},
            'test_outcomes': raw['test_outcomes'],
            'result': {key: value for key, value in raw.items() if key not in {
                'test_outcomes', 'collected_ids', 'omitted_ids'}}}
    assert outcome_history._status(json.loads(canonical(body)), NODE) == (False, False)


def test_history_summary_without_per_node_phases_establishes_no_node_result():
    body = {'source_before': 'd' * 64, 'source_after': 'd' * 64,
            'result': {'verdict': 'passed', 'completed': True, 'degraded': False, 'total': 1, 'not_passed': 0}}
    assert outcome_history._status(body, NODE) == (False, False)


def test_stale_retained_check_displays_missing_name_by_the_git_rule(monkeypatch):
    from g6.core.git_name import GitName
    from g6.workforce.repo_tests import shown_name

    name = GitName(b'gone\nfile.py')
    row = {'modules': [name], 'finding': 'edge', 'classification': 'defect_detected',
           'check': {'sha256': 'b' * 64, 'file': 'test_edge.py'}}
    monkeypatch.setattr(outcome_history, 'read', lambda root: outcome_history.History(rows=(row,)))
    change = SimpleNamespace(repo=Path('absent-fixture'), files=[])
    checks, origins, skipped = outcome_history.retained_checks(change, 'R')
    assert checks == [] and origins == {}
    assert skipped[0]['reason'] == 'exercised file gone: ' + shown_name(name)


@pytest.mark.parametrize('reason', [None, ('integrity', 'test_weakened'), ('tier_1', 'check_failed'),
                                  ('evidence', 'requirement_not_established')])
@pytest.mark.parametrize('text', ['caf\u00e9 \u20ac # < > &', '  a   b  ', 'unknown\nfield'])
def test_reply_preserves_all_historical_fields_and_report_text(monkeypatch, reason, text):
    from types import FunctionType
    from uuid import UUID

    from tests.repair_question5 import renderer_68ca0eb
    from tests.repair_question6 import review_entry_68ca0eb

    monkeypatch.setattr(outcome_history, 'read', lambda root: outcome_history.History())
    monkeypatch.setattr(outcome_history, 'disclose', lambda reply, root: reply)
    monkeypatch.setattr(patch_review, 'time', SimpleNamespace(monotonic=lambda: 1.0))
    monkeypatch.setattr(patch_review, 'uuid', SimpleNamespace(uuid4=lambda: UUID(int=1)))
    change = SimpleNamespace(repo=Path('fixture'), base_commit='0' * 40, files=['calc.py'])
    args = [change, [{'id': 'R', 'text': text, 'checks': [], 'status': 'not_established',
                      'kind': 'requirement', 'provenance': 'stated', 'reason': text, 'suggested_test': text}],
            [] if reason is None else [{'stage': reason[0], 'code': reason[1], 'detail': text}],
            [text], [], [], [], {}, {}, [], 'a' * 64, '', {}, 0.0]
    actual = patch_review._reply(*args)
    historical = FunctionType(review_entry_68ca0eb._reply.__code__, vars(patch_review),
                              argdefs=review_entry_68ca0eb._reply.__defaults__)
    with monkeypatch.context() as old:
        old.setattr(patch_report, 'render', renderer_68ca0eb.render)
        expected = historical(*args)
    assert set(actual) - set(expected) == {'learned'}
    assert set(actual['assessment']) - set(expected['assessment']) <= {'resolution'}
    existing = {key: value for key, value in actual.items() if key != 'learned'}
    existing['assessment'] = {key: value for key, value in actual['assessment'].items() if key != 'resolution'}
    assert existing == expected
