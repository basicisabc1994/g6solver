# SPDX-License-Identifier: Apache-2.0
"""Runtime identity and status transactions over real content-addressed stores."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from g6.core.ledger import canonical, digest
from g6.distill import acceptance_gate as gate, records as r
from g6.distill.execution import ExecutionRefused, Observation, run_candidate
from g6.distill.held_out.case_gate import CaseRecord
from g6.distill.ledger import Stores, failure_writer
from g6.distill.library import Library
from g6.distill.rectify_gate import Rectifier
from g6.harness import library_review_gate
from tests.distill.test_records import bodies
from tests.distill.test_review2_repairs import CASE

BEFORE = {'tests/test_value.py': 'def test_value():\n assert 1 == 1\n'}
AFTER = {'tests/test_value.py': 'def test_value():\n pass\n'}


@pytest.fixture
def disk(monkeypatch, tmp_path):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('G6_REQUIRE_ISOLATION', '1')
    monkeypatch.setenv('G6_CLAUDE', r'C:\nonexistent\g6-no-host.exe')
    monkeypatch.setenv('G6_CODEX', r'C:\nonexistent\g6-no-host.exe')
    frozen = CaseRecord.model_validate(CASE)
    archive = tmp_path / 'home/distill/held_out/regression_cases'
    archive.mkdir(parents=True)
    (archive / f'{frozen.case_id}.json').write_text(
        canonical(frozen.model_dump(mode='json')) + '\n', encoding='utf-8')
    for cls, body in bodies()[:3]:
        Stores().for_model(cls).put(cls(**body))
    return frozen


def method(source=None, *, template='swallow.v1'):
    return r.VerifierCandidate(**(bodies()[3][1] | (
        {'body': source} if source is not None else
        {'form': 'template_instance', 'body': {'template': template, 'bindings': {}}})))


def admit(candidate, frozen, **updates):
    Stores().for_model(r.VerifierCandidate).put(candidate)
    entry = r.LibraryEntry(**(bodies()[4][1] | {
        'candidate_id': candidate.candidate_id,
        'regression_suite': [{'case_ref': frozen.case_id, 'verdict': 'concern'}], **updates}))
    Stores().for_model(r.LibraryEntry).put(entry)
    return entry


def inactive(entry, status):
    changed = r.LibraryEntry(**(entry.model_dump(mode='json', exclude={'entry_id'}) | {
        'version': entry.version + 1, 'status': status, 'authority': 'A0'}))
    Stores().for_model(r.LibraryEntry).put(changed)
    return changed


@pytest.mark.parametrize('status', ['held', 'withdrawn'])
@pytest.mark.parametrize('route', ['run', 'measure', 'evaluate', 'training', 'imported'])
def test_all_executors_refuse_current_inactive_alias(disk, status, route):
    first = method()
    original = admit(first, disk)
    alias = method(template='self_reference.v1')
    admit(alias, disk)
    stale = Library().list()
    inactive(original, status)
    assert gate._method_identity(first) == gate._method_identity(alias)
    expected = f'G6_E_DISTILL_METHOD_{status.upper()}'
    with pytest.raises((ValueError, ExecutionRefused), match=expected):
        if route == 'run':
            gate._run(alias, disk, stale)
        elif route == 'measure':
            gate._measure(alias, (gate.Observation(disk, 'positive', 'acceptance'),), stale)
        elif route == 'evaluate':
            gate._library_verdicts(stale, (gate.Observation(disk, 'positive', 'acceptance'),))
        else:
            # Importing serialized proposal data confers no execution exemption.
            if route == 'imported':
                alias = r.VerifierCandidate.model_validate_json(alias.model_dump_json())
            run_candidate(alias, [Observation(BEFORE, AFTER, {})], lambda ident: first)


@pytest.mark.parametrize('status', ['held', 'withdrawn'])
@pytest.mark.parametrize('route', ['check', 'historical', 'application'])
def test_review_routes_never_report_a2_from_inactive_alias(disk, status, route):
    entry = admit(method(), disk)
    alias = admit(method(template='self_reference.v1'), disk)
    inactive(entry, status)
    if route == 'application':
        reasons, runs = [], []
        library_review_gate.apply_review(BEFORE, AFTER, reasons, runs)
        assert runs == []
        assert reasons == []
    else:
        assert library_review_gate.check(
            BEFORE, AFTER, entry_id=alias.entry_id if route == 'historical' else '') == []


@pytest.mark.parametrize('status', ['held', 'withdrawn'])
@pytest.mark.parametrize('route', ['run', 'training'])
def test_runtime_compares_noncanonical_methods_on_recorded_cases(disk, status, route):
    first = method(template='weakening.v1')
    entry = admit(first, disk)
    other = method()
    admit(other, disk)
    inactive(entry, status)
    assert gate._method_identity(first) != gate._method_identity(other)
    with pytest.raises(ValueError, match=f'G6_E_DISTILL_METHOD_{status.upper()}'):
        if route == 'run':
            gate._run(other, disk, Library().list())
        else:
            run_candidate(other, [Observation(BEFORE, AFTER, {})], lambda ident: first)


def test_unknown_runtime_comparison_never_runs_application(disk, monkeypatch):
    entry = admit(method(template='weakening.v1'), disk, regression_suite=[])
    other = method()
    admit(other, disk)
    inactive(entry, 'withdrawn')
    monkeypatch.setattr(gate.weakening, 'detect_test_weakening',
                        lambda *args: pytest.fail('unknown identity reached application'))
    with pytest.raises(ValueError, match='G6_E_DISTILL_IDENTITY_UNKNOWN'):
        gate._run(other, disk, Library().list())
    with pytest.raises(ExecutionRefused, match='G6_E_DISTILL_IDENTITY_UNKNOWN'):
        run_candidate(other, [Observation(BEFORE, AFTER, {})], lambda ident: other)
    assert library_review_gate.check(BEFORE, AFTER) == []


def test_recorded_difference_can_still_run(disk):
    entry = admit(method(), disk)
    other = method(template='skip_gate.v1')
    admit(other, disk)
    inactive(entry, 'withdrawn')
    assert gate._run(other, disk, Library().list()) == 'no_concern'
    assert run_candidate(other, [Observation(BEFORE, AFTER, {})], lambda ident: other) == ['no_concern']


def test_restore_exemption_is_scoped_to_its_admission(disk, monkeypatch):
    first = method()
    entry = admit(first, disk)
    withdrawn = inactive(entry, 'withdrawn')
    monkeypatch.setattr(gate, '_policy', lambda: None)
    monkeypatch.setattr(gate.stop, 'engaged', lambda: None)
    reached = []

    def load(*args):
        reached.append(gate._run(first, disk, Library().list()))
        raise ValueError('test stops after restore run')

    monkeypatch.setattr(gate, '_load', load)
    gate.restore(withdrawn.entry_id, Library(), digest('registration'))
    assert reached == ['concern']
    with pytest.raises(ValueError, match='G6_E_DISTILL_METHOD_WITHDRAWN'):
        gate._run(first, disk, Library().list())


def test_mcp_restore_cannot_release_a_different_inactive_identity(disk, monkeypatch):
    from g6.mcp.distill_backend import Service

    entry = admit(method(), disk)
    alias = admit(method(template='self_reference.v1'), disk)
    inactive(entry, 'held')
    withdrawn = inactive(alias, 'withdrawn')
    monkeypatch.setattr(gate, '_policy', lambda: None)
    monkeypatch.setattr(gate.stop, 'engaged', lambda: None)
    monkeypatch.setattr(gate, '_load', lambda *args: pytest.fail('MCP restore escaped another method hold'))
    answer = Service(registration=digest('registration')).restore(withdrawn.entry_id)
    assert answer['verdict'] == 'block'
    assert 'G6_E_DISTILL_METHOD_HELD' in ' '.join(answer['reasons'])


@pytest.mark.parametrize('status', ['held', 'withdrawn'])
@pytest.mark.parametrize('form', ['reuse', 'composition'])
def test_dependency_alias_cannot_escape_runtime_identity(disk, status, form):
    entry = admit(method(), disk)
    alias = admit(method(template='self_reference.v1'), disk)
    parent = r.VerifierCandidate(**(bodies()[3][1] | {
        'form': form, 'body': alias.entry_id if form == 'reuse' else {
            'op': 'all', 'entries': [alias.entry_id, entry.entry_id]}, 'lineage': [alias.entry_id]}))
    Stores().for_model(r.VerifierCandidate).put(parent)
    stale = Library().list()
    inactive(entry, status)
    with pytest.raises(ValueError, match=f'G6_E_DISTILL_METHOD_{status.upper()}'):
        gate._run(parent, disk, stale)


@pytest.mark.parametrize('source,other', [
    ("'concern'\ndef solve(facts):\n return {'verdict': __doc__}",
     "'no_concern'\ndef solve(facts):\n return {'verdict': __doc__}"),
    ('def solve(facts):\n "first"\n return {}', 'def solve(facts):\n "second"\n return {}'),
    ('class Value:\n "first"\ndef solve(facts):\n return {}',
     'class Value:\n "second"\ndef solve(facts):\n return {}'),
    ('def solve(facts: "first"):\n return {}', 'def solve(facts: "second"):\n return {}'),
    ('def solve(facts="first"):\n return {}', 'def solve(facts="second"):\n return {}'),
], ids=['module-doc', 'function-doc', 'class-doc', 'annotation', 'default'])
def test_runtime_values_remain_in_canonical_identity(disk, source, other):
    first, second = method(source), method(other)
    entry = admit(first, disk)
    Stores().for_model(r.VerifierCandidate).put(second)
    inactive(entry, 'withdrawn')
    assert gate._method_identity(first) != gate._method_identity(second)
    assert not gate._inactive_match(second, Library().list(), 'withdrawn')


def test_comments_and_locations_still_share_identity(disk):
    first = method('def solve(facts):\n return {}')
    other = method('# comment\n\ndef solve(facts):\n    return {}\n')
    entry = admit(first, disk)
    Stores().for_model(r.VerifierCandidate).put(other)
    inactive(entry, 'withdrawn')
    assert gate._inactive_match(other, Library().list(), 'withdrawn')


def test_status_change_in_look_callback_is_rechecked_before_load(disk, monkeypatch):
    entry = admit(method(), disk)
    other = method(template='self_reference.v1')
    Stores().for_model(r.VerifierCandidate).put(other)
    monkeypatch.setattr(gate, '_policy', lambda: None)
    monkeypatch.setattr(gate.stop, 'engaged', lambda: None)
    reached = []
    monkeypatch.setattr(gate, '_load', lambda *args: reached.append(args))
    monkeypatch.setattr(gate, '_look_available', lambda registration: inactive(entry, 'held'))
    answer = gate.decide(other, Library(), digest('registration'))
    assert reached == []
    assert 'G6_E_DISTILL_METHOD_HELD' in ' '.join(answer.reasons)


@pytest.mark.parametrize('status', ['held', 'withdrawn'])
def test_registration_loader_itself_refuses_inactive_identity(disk, status, monkeypatch):
    entry = admit(method(), disk)
    other = method(template='self_reference.v1')
    Stores().for_model(r.VerifierCandidate).put(other)
    inactive(entry, status)
    monkeypatch.setattr(gate, '_policy', lambda: (
        None, {'registrations': {digest('registration'): {'path': 'unused'}}}))
    monkeypatch.setattr(gate, '_plain', lambda *args: pytest.fail('inactive alias reached registered input'))
    with pytest.raises(ValueError, match=f'G6_E_DISTILL_METHOD_{status.upper()}'):
        gate._load(digest('registration'), other, Library())


@pytest.mark.parametrize('operation', ['status-put', 'reconcile'])
def test_status_writers_wait_for_acceptance_transaction(disk, monkeypatch, operation):
    entry = admit(method(), disk)
    began, finished = Event(), Event()
    rectifier = Rectifier()
    old_logs = rectifier._logs

    def logs():
        finished.set()
        return old_logs()

    monkeypatch.setattr(rectifier, '_logs', logs)

    def update():
        began.set()
        if operation == 'status-put':
            inactive(entry, 'held')
            finished.set()
        else:
            rectifier.reconcile()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with gate._writer():
            future = pool.submit(update)
            assert began.wait(5)
            assert not finished.wait(.1), 'status writer entered the admission transaction'
        future.result(timeout=10)
    assert finished.is_set()


def test_runtime_holds_transaction_until_detector_finishes(disk, monkeypatch):
    entry = admit(method(), disk)
    began, finished = Event(), Event()
    detect = gate.weakening.detect_test_weakening

    def update():
        began.set()
        inactive(entry, 'withdrawn')
        finished.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = []

        def detector(before, after):
            pending.append(pool.submit(update))
            assert began.wait(5)
            assert not finished.wait(.1), 'status changed during method execution'
            return detect(before, after)

        monkeypatch.setattr(gate.weakening, 'detect_test_weakening', detector)
        gate._run(method(), SimpleNamespace(before_tests=next(iter(BEFORE.values())),
                                           after_tests=next(iter(AFTER.values()))), Library().list())
        pending[0].result(timeout=10)
    assert finished.is_set()


def test_failure_lock_also_excludes_library_status_put(disk):
    entry = admit(method(), disk)
    began, finished = Event(), Event()

    def update():
        began.set()
        inactive(entry, 'held')
        finished.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with failure_writer():
            future = pool.submit(update)
            assert began.wait(5)
            assert not finished.wait(.1), 'failure and library writers use different transactions'
        future.result(timeout=10)
    assert finished.is_set()


def test_unknown_anchor_precedes_status_store_and_case_reads(disk, monkeypatch):
    monkeypatch.setattr(gate, '_policy', lambda: (None, {'registrations': {}}))
    monkeypatch.setattr(gate, '_check_inactive',
                        lambda *args, **kwargs: pytest.fail('unknown anchor reached status store'))
    monkeypatch.setattr(gate, '_plain', lambda *args: pytest.fail('unknown anchor reached cases'))
    with pytest.raises(ValueError, match='owner registration anchor'):
        gate._load('f' * 64, method(), None)


@pytest.mark.parametrize('candidate', [method(), method(template='self_reference.v1'),
                                      method('def solve(facts):\n return {}'),
                                      method('"module docs"\ndef solve(facts):\n "function docs"\n return {}')])
def test_training_and_gate_canonical_identities_agree(candidate):
    from g6.distill.inactive_identity import method_identity

    assert method_identity(candidate) == gate._method_identity(candidate)


@pytest.mark.parametrize('code', ['G6_E_DISTILL_METHOD_HELD', 'G6_E_DISTILL_METHOD_WITHDRAWN',
                                  'G6_E_DISTILL_IDENTITY_UNKNOWN'])
def test_inactive_training_refusal_has_a_service_decision_without_spending(disk, code):
    from g6.distill.synthesis import Refusal, SynthesisResult
    from g6.mcp.distill_backend import Service

    failure = r.FailureRecord(**(bodies()[0][1] | {
        'provenance': {'case': {'before': BEFORE, 'after': AFTER}}}))
    concept = r.Concept(**(bodies()[1][1] | {'from_failures': [failure.record_id]}))
    Stores().for_model(r.FailureRecord).put(failure)
    Stores().for_model(r.Concept).put(concept)

    class Synthesizer:
        def synthesise(self, *args):
            return SynthesisResult(candidates=[], model_calls=0, synthesis_calls=0,
                                   refusals=[Refusal(code=code, reason='inactive method refused')])

    class Jobs:
        def start(self, operation):
            return {'result': operation()}

    result = Service(jobs=Jobs(), synthesizer=Synthesizer(), registration=digest('registration')).distill_start(
        failure.family, failure.record_id)['result']
    assert len(result['decisions']) == 1
    assert result['decisions'][0]['verdict'] == 'block'
    assert code in ' '.join(result['decisions'][0]['reasons'])
    assert result['candidates'] == []
    assert Library().list() == []
    assert not any(row['kind'] == 'reserve' for row in gate._journal().entries())
