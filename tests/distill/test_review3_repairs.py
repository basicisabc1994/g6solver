"""Repair 3 class checks through the stores, service and MCP routes."""
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from g6.core.ledger import digest
from g6.distill import records as r
from g6.distill.ledger import Stores
from tests.distill.test_records import bodies
from tests.distill.test_review2_repairs import CASE, QueuedJobs, _positive


@pytest.fixture
def state_home(monkeypatch):
    # Ordinary product state, separate from pytest's owner-only tmp_path fixtures.
    path = Path(os.environ['G6_HOME']).parent / 'dr-r3-class' / uuid4().hex
    path.mkdir(parents=True)
    monkeypatch.setenv('G6_HOME', str(path))
    return path


def seed():
    values = [cls(**body) for cls, body in bodies()[:5]]
    for value in values:
        Stores().for_model(type(value)).put(value)
    return values


def test_unknown_stop_refuses_and_names_attached_keys(state_home):
    from g6.mcp.distill_backend import Service
    service = Service()
    service.runners['runner-a'] = object()
    result = service.research_stop()
    assert result.get('ok') is False
    assert result['warning_card']['code'] == 'G6_E_RESEARCH_RUNNER_UNKNOWN'
    assert 'runner-a' in result['error']
    assert not service._stop_path('').exists()


def test_legacy_unknown_stop_file_does_not_claim_a_runner_stopped(state_home):
    from g6.mcp.distill_backend import Service
    service = Service()
    path = service._stop_path('old-key')
    path.parent.mkdir(parents=True)
    path.write_text('owner stop\n', encoding='utf-8')
    assert service.research_status('old-key')['status'] != 'stopped'


def test_legacy_derived_unit_cannot_escape_unseen_history(state_home):
    from g6.core.llm import FakeLLM
    from g6.distill.held_out.oracle import OracleResult
    from g6.distill.surface import failure_record
    from g6.distill.synthesis import Synthesizer
    from g6.mcp.distill_backend import Service, _labelled, _oracle_case
    failure_record('missed_defect', 'test_weakening', json.dumps(CASE), 'source')
    [source] = Stores().for_model(r.FailureRecord).list()
    case = _oracle_case(source)
    training = _labelled(source, case, OracleResult(case_id=case.case_id, label='positive',
        reason='known_wrong_witness', policy_sha256='0' * 64))
    provenance = {key: value for key, value in training.provenance.items() if key != 'unit_id'}
    training = r.FailureRecord(**(training.model_dump(exclude={'record_id'}) | {'provenance': provenance}))
    Stores().for_model(r.FailureRecord).put(training)
    unseen = json.loads(json.dumps(CASE))
    unseen['provenance']['cohort'] = 'selection'
    legacy = r.FailureRecord(**(source.model_dump(exclude={'record_id'}) | {'family': 'another-family',
        'case_ref': digest(unseen), 'provenance': {'case': unseen}}))
    Stores().for_model(r.FailureRecord).put(legacy)
    fake = FakeLLM(default='{}')
    jobs = QueuedJobs()
    Service(jobs=jobs, synthesizer=Synthesizer(caller=fake)).distill_start('test_weakening', training.record_id)
    results = [operation() for operation in jobs.operations]
    assert fake.calls == [], results


def test_intake_refuses_conflicting_unit_cohorts(state_home):
    from g6.distill.surface import failure_record
    unseen = json.loads(json.dumps(CASE))
    unseen['provenance']['cohort'] = 'selection'
    first = failure_record('missed_defect', 'one-family', json.dumps(unseen), 'held')
    second = failure_record('missed_defect', 'another-family', json.dumps(CASE), 'relabeled')
    assert second.get('ok') is False
    assert second['warning_card']['code'] == 'G6_E_SYNTH_COHORT'
    assert [f.record_id for f in Stores().for_model(r.FailureRecord).list()] == [first['record_id']]


def test_family_start_reports_skips_and_queues_runnable_records(state_home):
    from g6.distill.surface import failure_record
    from g6.mcp.distill_backend import Service
    missing = failure_record('missed_defect', 'test_weakening', '{}', 'no runnable case')
    failure_record('missed_defect', 'test_weakening', json.dumps(CASE), 'runnable')
    jobs = QueuedJobs()
    reply = Service(jobs=jobs).distill_start('test_weakening')
    assert len(jobs.operations) == 1
    assert [row['record_id'] for row in reply['skipped']] == [missing['record_id']]


@pytest.mark.parametrize('provenance', [
    {'case': {'before': None, 'after': {}}},
    {'cohort': 'selection', 'case': {'before': {'test.py': 'pass'}, 'after': {'test.py': 'pass'}}}])
def test_family_start_skips_unusable_training_headers(state_home, provenance):
    from g6.distill.surface import failure_record
    from g6.mcp.distill_backend import Service
    unusable = r.FailureRecord(**(bodies()[0][1] | {'provenance': provenance}))
    Stores().for_model(r.FailureRecord).put(unusable)
    failure_record('missed_defect', 'test_weakening', json.dumps(CASE), 'runnable')
    jobs = QueuedJobs()
    reply = Service(jobs=jobs).distill_start('test_weakening')
    assert len(jobs.operations) == 1
    assert [row['record_id'] for row in reply['skipped']] == [unusable.record_id]


def test_unseen_unit_history_covers_other_families_and_selected_ids(state_home, monkeypatch):
    from g6.distill.surface import failure_record
    from g6.mcp.distill_backend import Service
    _positive(monkeypatch)
    unseen = json.loads(json.dumps(CASE))
    unseen['provenance']['cohort'] = 'selection'
    failure_record('missed_defect', 'another-family', json.dumps(unseen), 'unseen')
    seen = failure_record('missed_defect', 'test_weakening', json.dumps(CASE), 'changed cohort')
    jobs = QueuedJobs()
    assert seen['warning_card']['code'] == 'G6_E_SYNTH_COHORT'
    Service(jobs=jobs).distill_start('test_weakening')
    for operation in jobs.operations:
        operation()
    assert not [f for f in Stores().for_model(r.FailureRecord).list() if f.observed_by == 'independent']


def test_library_surface_redacts_every_version(state_home):
    from g6.distill.acceptance_gate import withdraw
    from g6.distill.library import Library
    from g6.distill.surface import library
    *_, entry = seed()
    withdraw(entry.entry_id, Library(), person='Dialog Person', reason='private dialog reason')
    text = json.dumps([library(), library('describe', entry.entry_id), library('history', entry.entry_id)])
    assert 'Dialog Person' not in text
    assert 'private dialog reason' not in text
    from g6.mcp import build_server
    from tests.distill.wire_declarations import issued
    from tests.workflow.kit import call
    with issued():
        server = build_server()
        replies = [call(server, 'g6_library', {'op': op, **({'entry_id': entry.entry_id} if op != 'list' else {})})
                   for op in ('list', 'describe', 'history')]
    assert 'entries' in replies[0]
    assert 'Dialog Person' not in json.dumps(replies)
    assert 'private dialog reason' not in json.dumps(replies)


def test_automatic_gate_blocks_withdrawn_identity_concept_or_contract_before_a_look(state_home):
    from g6.distill import acceptance_gate as gate
    from g6.distill.library import Library
    *_, candidate, entry = seed()
    gate.withdraw(entry.entry_id, Library(), person='Person', reason='withdraw')
    variants = [candidate,
        r.VerifierCandidate(**(candidate.model_dump(exclude={'candidate_id'}) | {'model_calls': 2})),
        r.VerifierCandidate(**(candidate.model_dump(exclude={'candidate_id'}) | {
            'contract': r.MethodContract(**(candidate.contract.model_dump() | {'assumptions': ['changed']}))}))]
    for variant in variants:
        answer = gate.decide(variant, Library(), '')
        assert answer.verdict in {'refuse', 'block'}
        assert 'withdraw' in ' '.join(answer.reasons)


def test_reuse_is_a_row_and_never_an_admission_candidate():
    from g6.distill.synthesis import Synthesizer
    from tests.distill.test_synthesis import MemoryLibrary, fixture
    concept, case = fixture()
    library = MemoryLibrary(['assert_strength.v1'])
    result = Synthesizer().synthesise(concept, [case], library=library)
    assert result.candidates == []
    assert result.reuses[0].entry_id == library.values[0].entry_id
    assert result.reuses[0].training_case_ids == [case.failure.case_ref]


@pytest.mark.parametrize('helper,body,expected', [
    ('f', 'family', False), ('ab', 'ab', False), ('marker', 'marker_extra', False),
    ('marker', 'marker', True), ('marker', 'call(marker)', True)])
def test_leak_guard_matches_identifiers(helper, body, expected):
    from dataclasses import replace
    from g6.distill import acceptance_gate as gate
    from tests.distill.test_acceptance_measurements import sample
    _, candidate, frozen, _ = sample.__wrapped__()
    obs = frozen.monitor[-1]
    case = type(obs.case).model_validate(obs.case.model_dump(mode='json') | {
        'before_tests': f'def {helper}():\n    pass\n', 'after_tests': f'def {helper}():\n    pass\n'})
    candidate = r.VerifierCandidate(**(candidate.model_dump(exclude={'candidate_id'}) | {
        'form': 'predicate', 'body': f'def solve(facts):\n    return {body!r}\n'}))
    assert gate._leaks(candidate, (replace(obs, case=case),)) is expected


def test_malformed_legacy_row_does_not_block_store_class_checks(state_home):
    from g6.distill.rectify_gate import Rectifier
    from tests.distill.test_rectify import rect
    good = r.FailureRecord(**bodies()[0][1])
    malformed = r.FailureRecord(**(bodies()[0][1] | {'case_ref': digest('legacy'), 'finding_class': 'other'}))
    store = Stores().for_model(r.FailureRecord)
    store.put(good)
    store.put(malformed)
    assert Rectifier().submit(good.record_id, rect([good])).outcome == 'held'


def test_self_report_cannot_invalidate_class_removal(state_home):
    from g6.distill.rectify_gate import Rectifier
    from tests.distill.test_rectify import rect
    good = r.FailureRecord(**bodies()[0][1])
    store = Stores().for_model(r.FailureRecord)
    store.put(good)
    held = Rectifier().submit(good.record_id, rect([good]))
    report = r.FailureRecord(**(bodies()[0][1] | {'observed_by': 'self', 'evidence': [],
        'case_ref': digest('client claim'), 'observed_at': '2026-11-22'}))
    store.put(report)
    Rectifier().reconcile()
    assert Rectifier().mode(good.finding_class).removing_check is None
    assert Rectifier().mode(good.finding_class).recurrences_after_removal == 1
    assert Stores().for_model(r.Rectification).list()[-1].pinning == held.pinning
    independent = r.FailureRecord(**(report.model_dump(exclude={'record_id'}) | {'observed_by': 'independent'}))
    store.put(independent)
    Rectifier().reconcile()
    assert Rectifier().mode(good.finding_class).removing_check is None


def test_independent_recurrence_still_demotes_a_library_removing_check(state_home):
    from g6.distill.library import Library
    from g6.distill.rectify_gate import Rectifier
    from tests.distill.test_rectify import library_check, rect
    entry = library_check()
    [first] = Stores().for_model(r.FailureRecord).list()
    pinning = rect([first]).pinning.model_dump() | {'test_ref': f'library:{entry.entry_id}'}
    held = Rectifier().submit(first.record_id, rect([first], pinning=pinning))
    store = Stores().for_model(r.FailureRecord)
    report = r.FailureRecord(**(first.model_dump(exclude={'record_id'}) | {
        'source': 'person', 'observed_by': 'self', 'evidence': [], 'case_ref': digest('client report')}))
    store.put(report)
    Rectifier().reconcile()
    assert (Library().list()[0].status, Library().list()[0].authority) == ('held', 'A0')
    assert Rectifier().mode(first.finding_class).removing_check is None
    assert Rectifier().mode(first.finding_class).recurrences_after_removal == 1
    assert Stores().for_model(r.Rectification).list()[-1].pinning == held.pinning
    recurrence = r.FailureRecord(**(report.model_dump(exclude={'record_id'}) | {
        'source': 'experiment', 'observed_by': 'independent', 'case_ref': digest('independent recurrence')}))
    store.put(recurrence)
    Rectifier().reconcile()
    assert (Library().list()[0].status, Library().list()[0].authority) == ('demoted', 'A0')


def test_restore_refusal_uses_the_decision_envelope(state_home, monkeypatch):
    from g6.distill.acceptance_gate import withdraw
    from g6.distill.library import Library
    from g6.mcp import consent, distill
    from g6.mcp.distill_backend import Service
    *_, entry = seed()
    decision = withdraw(entry.entry_id, Library(), person='Person', reason='withdraw')
    tools = {}

    class Server:
        def tool(self):
            return lambda fn: tools.setdefault(fn.__name__, fn)

    async def accept(*args):
        return 'accept', {'confirm': 'change', 'name': 'Person'}, ''

    monkeypatch.setattr(consent, 'elicit', accept)
    distill.register(Server(), object, object(), backend=Service())
    answer = asyncio.run(tools['g6_library_decide'](decision.entry_id, 'restore', 'retry', object()))
    assert answer.get('ok') is False
    assert answer['warning_card']['code'] == 'G6_E_DISTILL_DECISION'


def test_all_failure_writers_serialize_across_processes(state_home):
    import subprocess
    import sys
    code = '''
from g6.distill.surface import failure_record, record_reproduction
import sys
for i in range(8):
    failure_record('missed_defect', 'test_weakening', '{}', f'{sys.argv[1]}:{i}')
    record_reproduction({'tool_response': {'assessment': {'outcome': 'defect_detected',
        'reasons': [{'reproduction': f'tests/test_case.py::{sys.argv[1]}_{i}'}]}}})
'''
    children = [subprocess.Popen([sys.executable, '-X', 'utf8', '-c', code, str(i)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(3)]
    outputs = [child.communicate(timeout=60) for child in children]
    assert [child.returncode for child in children] == [0, 0, 0], outputs
    assert len(Stores().for_model(r.FailureRecord).list()) == 48


def test_only_dialog_restore_readmits_and_spends_a_fresh_look(state_home, monkeypatch):
    from g6.core.llm import FakeLLM
    from g6.distill import acceptance_gate as gate
    from g6.distill.library import Library
    from g6.distill.synthesis import Synthesizer
    from g6.mcp import consent, distill
    from g6.mcp.distill_backend import Service
    from tests.distill.test_acceptance_measurements import sample
    _, _, frozen, _ = sample.__wrapped__()
    monkeypatch.setattr(gate, '_load', lambda *args: frozen)
    failure = r.FailureRecord(**(bodies()[0][1] | {'provenance': {'case': {
        'before': {'tests/test_seed.py': 'def test_seed():\n    assert calc() == 2\n'},
        'after': {'tests/test_seed.py': 'def test_seed():\n    pass\n'}}}}))
    Stores().for_model(r.FailureRecord).put(failure)
    fake = FakeLLM(default=json.dumps({key: bodies()[1][1][key] for key in
        ('statement', 'observable_features', 'falsifier', 'prediction', 'worth')}))

    class Jobs:
        def start(self, operation):
            return {'result': operation()}

    service = Service(jobs=Jobs(), synthesizer=Synthesizer(caller=fake), registration=digest('registration'))
    result = service.distill_start(failure.family, failure.record_id)['result']
    assert result['decisions'][0]['verdict'] == 'admit_probation'
    [entry] = Library().list()
    withdrawn = gate.withdraw(entry.entry_id, Library(), person='Person', reason='withdraw')
    retried = service.distill_start(failure.family, failure.record_id)['result']
    assert retried['decisions'][0]['verdict'] in {'refuse', 'block'}
    assert Library().list()[0].status == 'withdrawn'
    assert len([row for row in gate._journal().entries() if row['kind'] == 'reserve']) == 1
    tools, dialogs = {}, []

    class Server:
        def tool(self):
            return lambda fn: tools.setdefault(fn.__name__, fn)

    async def accept(ctx, message, schema):
        dialogs.append(message)
        return 'accept', {'confirm': 'change', 'name': 'Person'}, ''

    monkeypatch.setattr(consent, 'elicit', accept)
    distill.register(Server(), object, service.jobs, backend=service)
    restored = asyncio.run(tools['g6_library_decide'](withdrawn.entry_id, 'restore', 'fresh admission', object()))
    assert dialogs and restored['verdict'] == 'admit_probation'
    assert (Library().list()[0].status, Library().list()[0].authority) == ('probation', 'A1')
    assert len([row for row in gate._journal().entries() if row['kind'] == 'reserve']) == 2


def test_research_dialog_names_the_attached_runner(state_home, monkeypatch):
    from g6.mcp import consent, distill
    from g6.mcp.distill_backend import Service
    from tests.distill.test_research_runner import fixture
    _, runner, _, _, _, _ = fixture()
    jobs, tools, dialogs = QueuedJobs(), {}, []
    service = Service(jobs=jobs, runners={'runner-a': runner})

    class Server:
        def tool(self):
            return lambda fn: tools.setdefault(fn.__name__, fn)

    async def accept(ctx, message, schema):
        dialogs.append(message)
        return 'accept', {'confirm': 'change', 'name': 'Person'}, ''

    monkeypatch.setattr(consent, 'elicit', accept)
    distill.register(Server(), object, jobs, backend=service)
    asyncio.run(tools['g6_research_start'](object(), 'runner-a'))
    assert len(jobs.operations) == 1 and 'runner-a' in dialogs[0]
