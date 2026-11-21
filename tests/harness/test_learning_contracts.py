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
        {'result': 'counterexample', 'requirement': 'R', 'check': 'edge', 'evidence_class': 'defect',
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


def bound(raw: dict) -> dict:
    """A receipt body as learning reads it: the receipt's result, and the binding a confined run writes beside it."""
    from g6.workforce import repo_tests

    return {'source_before': 'd' * 64, 'source_after': 'd' * 64,
            'tests': {'requested': [NODE], 'collected': raw['collected_ids'], 'omitted': []},
            'result': {key: value for key, value in raw.items() if key not in {
                'test_outcomes', 'collected_ids', 'omitted_ids'}},
            **repo_tests._learning_binding(raw, [NODE], repository='a' * 64, check_digests={})}


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
    assert outcome_history._status(bound(raw), NODE) == (False, False)


@pytest.mark.parametrize('passed', [True, False])
def test_history_accepts_complete_phase_receipts_with_the_producer_completion_flag(passed):
    # A failure the live reader believes says how it failed (``verification_search.reading``).
    raw = report(PASSED if passed else FAILED, completed=passed, degraded=False,
                 **({} if passed else {'failure_messages': {NODE: 'AssertionError: add returned -1'}}))
    assert outcome_history._status(bound(raw), NODE) == (passed, not passed)


@pytest.mark.parametrize('passed', [True, False])
def test_history_keeps_node_results_after_canonical_receipt_storage(passed):
    from g6.core.ledger import canonical

    raw = report(PASSED if passed else FAILED, completed=passed, degraded=False,
                 **({} if passed else {'failure_messages': {NODE: 'AssertionError: add returned -1'}}))
    stored = json.loads(canonical(bound(raw)))
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
    assert outcome_history._status(json.loads(canonical(bound(raw))), NODE) == (False, False)


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


FILE = NODE.partition('::')[0]
CHECK = 'from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n'
SAYS = {NODE: 'AssertionError: add returned -1'}
#: One runner report for each distinction the live reader (``verification_search.reading``) makes: the check it is read
#: for, the report, and the result that reader gives it.
SHAPES = {
    'pass': (NODE, report(PASSED, completed=True), 'passed'),
    'failure_with_message': (NODE, report(FAILED, completed=False, failure_messages=SAYS), 'failed'),
    'failure_without_message': (NODE, report(FAILED, completed=False), 'error'),
    'boundary_refusal': (NODE, report(FAILED, completed=False, failure_messages=SAYS, os_errors=[NODE]), 'error'),
    'interrupted_collection': (NODE, report(FAILED, completed=False, collection_stopped=True, failure_messages={
        **SAYS, 'test_other.py': 'ImportError: collection interrupted'}), 'error'),
    'own_file_uncollected': (NODE, {**report(PASSED), 'verdict': 'collection_error', 'test_outcomes': {},
                                    'collected_ids': [], 'completed': False,
                                    'failure_messages': {FILE: 'ImportError: cannot import name add'}}, 'failed'),
    'collection_error_beside_bodies': (FILE, report(PASSED, verdict='failed', completed=False, failure_messages={
        FILE + '::TestOther': 'ImportError: cannot import name sub'}), 'error'),
    'timed_out': (NODE, {**report(PASSED), 'verdict': 'timed_out', 'test_outcomes': {}, 'collected_ids': [],
                         'completed': False}, 'error'),
    'changed_files': (NODE, report(PASSED, verdict='changed_tracked_files', changed=['calc.py'], completed=False),
                      'error'),
    'no_result_verdict': (NODE, {**report(PASSED), 'verdict': 'no_tests_collected', 'test_outcomes': {},
                                 'collected_ids': []}, 'error'),
    'no_test_of_the_check': (FILE + '::test_sub', report(PASSED, completed=True), 'error'),
    'loading_unknown': (NODE, report(PASSED, completed=True, loaded_complete=False, loaded_outside={}), 'error'),
    'setup_failed': (NODE, report({'setup': 'failed', 'teardown': 'passed'}, verdict='failed', failed_ids=[],
                                  setup_errors={NODE: 'fixture missing'}, completed=False,
                                  failure_messages={NODE: 'fixture missing'}), 'error'),
    'skipped': (NODE, report({'setup': 'passed', 'call': 'skipped', 'teardown': 'passed'}, completed=True), 'error'),
    'deselected_beside': (FILE, report(PASSED, completed=True, collected_ids=[NODE, FILE + '::test_sub'],
                                       omitted_ids=[FILE + '::test_sub']), 'error'),
    'probe_read_error': (NODE, report(PASSED, completed=True, probe_observations='probe payload unreadable'), 'error'),
    'unknown_report': (NODE, report(PASSED, completed=False), 'error'),
}
STATUS = {'passed': (True, False), 'failed': (False, True), 'error': (False, False)}
LEARNED = {'passed': 'not_refuted', 'failed': 'defect_detected', 'error': 'check_error'}
#: The one documented exception to agreement (lead ruling, 2026-11-21): a check whose own file could not be collected
#: is failed to the live reader, but learning records it as an error, because the cause can be a module this
#: environment lacks, and unusable execution evidence never becomes a reproduced defect.
LEARNED_AS = {'own_file_uncollected': 'error'}


@pytest.mark.parametrize('shape', sorted(SHAPES))
def test_learning_reads_every_report_shape_as_the_live_reader_does(tmp_path, monkeypatch, shape):
    import hashlib

    from g6.harness import verification_search
    from g6.workforce import repo_tests

    node, ran, expected = SHAPES[shape]
    live = verification_search.reading(ran, node)
    assert live['result'] == expected, live   # the shape reaches the distinction it names
    monkeypatch.setenv('G6_HOME', str(tmp_path))
    monkeypatch.setattr(receipt_store, '_executable', lambda python: {})
    monkeypatch.setattr(outcome_history, 'repository_id', lambda root: 'c' * 64)
    if not repo_tests.read_report(ran).valid:   # an unknown report gets no receipt, so nothing is learned from it
        with pytest.raises(ValueError):
            receipt_store._body(started_at='s', finished_at='f', before={}, after={}, base_commit='b' * 40,
                                python='', requested=[node], found=ran)
        return
    body = receipt_store._body(started_at='s', finished_at='f', before={}, after={}, base_commit='b' * 40,
                               python='', requested=[node], found=ran)
    # The binding exactly as a confined run writes it, stored as canonical JSON and read back.
    ident = receipt_store._record(body, learning=repo_tests._learning_binding(
        ran, [node], repository='c' * 64, check_digests={FILE: hashlib.sha256(CHECK.encode()).hexdigest()}))
    learned = receipt_store.lookup_learning(ident, artifact_digest=body['source_before'])
    agreed = LEARNED_AS.get(shape, expected)   # the live reader's result, but for the one ruled exception
    assert outcome_history._status(learned, node) == STATUS[agreed]
    # A receipt bound before readings (its phases beside it) never reads as a result the live reader does not give.
    older = {**{key: value for key, value in learned.items() if key != 'readings'}, 'test_outcomes': ran['test_outcomes']}
    assert outcome_history._status(older, node) in {STATUS[agreed], (False, False)}
    admitted = []
    monkeypatch.setattr(outcome_history, '_record', lambda root, row: admitted.append(row)
                        if outcome_history._admitted(row, 'c' * 64) else None)
    outcome_history.record_stated(SimpleNamespace(repo=Path.cwd(), files=[]), {node: CHECK},
                                  [{'receipt': ident, 'provenance': 'stated'}], artifact=body['source_before'])
    assert [row['classification'] for row in admitted] == [LEARNED[agreed]]


def test_a_collection_error_never_becomes_a_reproduced_defect(monkeypatch):
    # Lead ruling, 2026-11-21: whatever the bound reading says, a collection_error run is never a defect.
    body = {'repository_id': 'c' * 64, 'source_before': 'd' * 64, 'source_after': 'd' * 64,
            'check_digests': {FILE: 'b' * 64}, 'tests': {'requested': [NODE], 'collected': []},
            'readings': {NODE: 'failed'}, 'result': {'verdict': 'collection_error', 'completed': False}}
    assert outcome_history._status(body, NODE) == (False, False)
    monkeypatch.setattr(receipt_store, 'lookup', lambda *args, **kwargs: body)
    monkeypatch.setattr(outcome_history, 'repository_id', lambda root: 'c' * 64)
    row = {'schema': outcome_history.SCHEMA, 'repository_id': 'c' * 64, 'receipt': 'owned', 'artifact_digest': 'd' * 64,
           'source_after': 'd' * 64, 'classification': 'defect_detected', 'node': NODE, 'modules': [],
           'finding': 'stated check', 'method': 'review', 'check': {'source': '', 'file': FILE, 'sha256': 'b' * 64}}
    assert not outcome_history._admitted(row, 'c' * 64)
    assert outcome_history._admitted({**row, 'classification': 'check_error'}, 'c' * 64)
    recorded = []
    monkeypatch.setattr(outcome_history, '_record', lambda root, value: recorded.append(value['classification']))
    outcome_history.record_stated(SimpleNamespace(repo=Path.cwd(), files=[]), {NODE: ''},
                                  [{'receipt': 'owned', 'provenance': 'stated'}], artifact='d' * 64)
    assert recorded == ['check_error']


@pytest.mark.parametrize('result', ['passed', 'failed', 'error'])
def test_learning_reads_every_observation_as_the_live_reader_does(tmp_path, monkeypatch, result):
    from g6.harness import verification_search
    from g6.workforce import repo_tests

    ran = {'verdict': repo_tests.OBSERVED_VERDICTS[result], 'said': '', 'seconds': 0.0, 'source_before': 'a' * 64,
           'isolation': {'backend': 'recorded-review-fixture'},
           'g6_observation': {'result': result, 'detail': 'observed', 'observations': []}}
    assert verification_search.reading(ran, NODE)['result'] == result
    monkeypatch.setenv('G6_HOME', str(tmp_path))
    monkeypatch.setattr(receipt_store, 'repository_id', lambda root: 'c' * 64)
    ident = receipt_store._record_observation(Path.cwd(), NODE, ExecutableSource(CHECK, {'kind': 'example'}), ran)
    learned = receipt_store.lookup_learning(ident, artifact_digest='a' * 64, _observations=True)
    assert outcome_history._status(learned, NODE) == STATUS[result]
