"""Amendment 1: compatibility and status races over real persisted records.

The mutation controls remove only optimistic status rechecks. They run the same
schedule and assertions as the ordinary cases, and require an assertion failure.
Custody fixtures supply labelled synthetic cases; stores and status writers are real.
"""
import asyncio
from dataclasses import replace

import pytest

from g6.core.ledger import digest
from g6.distill import acceptance_gate as gate, execution, records as r, runtime_state
from g6.distill.ledger import Stores
from g6.distill.library import Library
from g6.distill.rectify_gate import Rectifier
from g6.harness import library_review_gate as review
from g6.mcp import consent, distill
from g6.mcp.distill_backend import Service
from tests.distill import test_runtime_identity_boundaries as boundaries
from tests.distill.test_acceptance_measurements import sample
from tests.distill.test_records import bodies
from tests.distill.test_rectify import rect
from tests.distill.test_runtime_identity_boundaries import AFTER, BEFORE, admit, inactive, method

disk = boundaries.disk


def _resolve(ref):
    entry = Stores().for_model(r.LibraryEntry).get(ref)
    return Stores().for_model(r.VerifierCandidate).get(entry.candidate_id)


def _known_registration(monkeypatch):
    registration = digest('amendment-known-registration')
    policy, settings = gate._policy()
    monkeypatch.setattr(gate, '_policy', lambda: (policy, settings | {
        'registrations': {registration: {'path': 'synthetic-custody'}}}))
    monkeypatch.setattr(gate.stop, 'engaged', lambda: None)
    return registration


@pytest.mark.parametrize('registration', ['', None], ids=['default', 'none'])
@pytest.mark.parametrize('status', ['held', 'withdrawn'])
def test_absent_anchor_keeps_the_existing_inactive_decision(disk, registration, status):
    candidate = method()
    entry = admit(candidate, disk)
    inactive(entry, status)
    answer = gate.decide(candidate, Library(), registration)
    assert answer.verdict in {'block', 'refuse'}
    assert f'G6_E_DISTILL_METHOD_{status.upper()}' in ' '.join(answer.reasons)
    assert not any(row['kind'] == 'reserve' for row in gate._journal().entries())


def test_absent_policy_port_never_reaches_tuple_unpacking(disk, monkeypatch):
    candidate = method()
    Stores().for_model(r.VerifierCandidate).put(candidate)
    monkeypatch.setattr(gate, '_policy', lambda: None)
    monkeypatch.setattr(gate.stop, 'engaged', lambda: None)
    reached = []

    def load(*args):
        reached.append(1)
        raise ValueError('custody boundary unavailable')

    monkeypatch.setattr(gate, '_load', load)
    answer = gate.decide(candidate, Library(), digest('registration'))
    assert reached == [1]
    assert answer.verdict == 'block'
    assert answer.reasons == ('custody boundary unavailable',)


def test_default_restore_refusal_stays_in_the_mcp_decision_envelope(disk, monkeypatch):
    entry = admit(method(), disk)
    withdrawn = inactive(entry, 'withdrawn')
    handlers = {}

    class Server:
        def tool(self):
            return lambda fn: handlers.setdefault(fn.__name__, fn)

    async def accept(*args):
        return 'accept', {'confirm': 'change', 'name': 'Owner'}, ''

    monkeypatch.setattr(consent, 'elicit', accept)
    distill.register(Server(), object, None, backend=Service())
    answer = asyncio.run(handlers['g6_library_decide'](withdrawn.entry_id, 'restore', 'retry', object()))
    assert answer['ok'] is False
    assert answer['warning_card']['code'] == 'G6_E_DISTILL_DECISION'
    assert answer['decision']['verdict'] in {'block', 'refuse'}
    assert Library().list() == [withdrawn]


@pytest.mark.parametrize('status', ['held', 'withdrawn'])
def test_invalid_observation_keeps_the_known_inactive_code(disk, status):
    candidate = method()
    entry = admit(candidate, disk)
    inactive(entry, status)
    with pytest.raises(ValueError, match=f'G6_E_DISTILL_METHOD_{status.upper()}'):
        gate._run(candidate, object(), Library().list())


def _remove_rechecks(monkeypatch):
    for module in (runtime_state, gate, execution, review):
        monkeypatch.setattr(module, 'require_current', lambda entries: entries)


RUN_ROUTES = ['run', 'measure', 'library_verdicts', 'train', 'apply', 'historical', 'application']
RUN_WINDOWS = ['after_check', 'after_execution', 'before_authority']


def _run_schedule(disk, monkeypatch, status, route, window, *, mutant=False, recurrence=False):
    candidate = method()
    if recurrence:
        contract = r.MethodContract(**(bodies()[2][1] | {'family': 'g6_self'}))
        Stores().for_model(r.MethodContract).put(contract)
        candidate = r.VerifierCandidate(**(candidate.model_dump(mode='json', exclude={'candidate_id'}) | {
            'contract': contract}))
    entry = admit(candidate, disk, family=candidate.contract.family,
                  contract_sha256=candidate.contract.content_id)
    if recurrence:
        failures = Stores().for_model(r.FailureRecord).list()
        pinning = rect(failures).pinning.model_dump() | {'test_ref': f'library:{entry.entry_id}'}
        Rectifier().submit(failures[-1].record_id, rect(failures, pinning=pinning))
    if mutant:
        _remove_rechecks(monkeypatch)
    changed = []
    calls = []
    detector = gate.weakening.detect_test_weakening

    def detect(*args):
        calls.append(1)
        return detector(*args)

    monkeypatch.setattr(gate.weakening, 'detect_test_weakening', detect)

    def change():
        if changed:
            return
        if recurrence:
            failure = r.FailureRecord(**(failures[0].model_dump(mode='json', exclude={'record_id'}) | {
                'observed_by': 'self', 'source': 'person', 'split': 'field', 'case_ref': disk.case_id,
                'observed_at': '2026-11-24', 'provenance': {'case': disk.model_dump(mode='json')}}))
            Stores().for_model(r.FailureRecord).put(failure)
            Rectifier().reconcile()
            held = Library().list()[0]
            assert held.status == 'held' and failure.record_id in held.held_recurrences
            changed.append(held.entry_id)
        elif status == 'held':
            changed.append(inactive(entry, status).entry_id)
        else:
            answer = gate.withdraw(entry.entry_id, Library(), reason='stop method', person='Owner')
            assert answer.verdict == 'withdraw'
            changed.append(answer.entry_id)

    module = execution if route == 'train' else gate
    target = '_guard_dependencies' if window == 'after_check' else (
        '_execute_candidate' if route == 'train' else '_execute')
    if window == 'before_authority' and route in {'apply', 'historical', 'application'}:
        # These routes have another reporting boundary after _run returned.
        # Change status after the observation is built, before it leaves check.
        module, target = review, 'observation'
    elif window == 'before_authority':
        target = 'require_current'
    original = getattr(module, target)

    def boundary(*args, **kwargs):
        if window == 'before_authority' and target == 'require_current':
            # The pre-execution recheck has no detector result yet. Inject at
            # the reporting recheck, after actual execution completed.
            if calls:
                change()
            return original(*args, **kwargs)
        result = original(*args, **kwargs)
        change()
        return result

    monkeypatch.setattr(module, target, boundary)
    try:
        if route == 'train':
            answer = execution.run_candidate(candidate, [execution.Observation(BEFORE, AFTER, {})], _resolve)
        elif route == 'run':
            answer = gate._run(candidate, disk, Library().list())
        elif route == 'measure':
            answer = gate._measure(candidate, (gate.Observation(disk, 'positive', 'acceptance'),), Library().list())
        elif route == 'library_verdicts':
            answer = gate._library_verdicts(Library().list(), (gate.Observation(disk, 'positive', 'acceptance'),))
        elif route == 'application':
            reasons, answer = [], []
            review.apply_review(BEFORE, AFTER, reasons, answer)
            assert reasons == []
            answer = [row['library'] for row in answer]
        else:
            answer = review.check(BEFORE, AFTER, entry_id=entry.entry_id if route == 'historical' else '')
    except ValueError as error:
        answer = error
    assert changed, 'schedule did not record a status change'
    assert Library().list()[0].entry_id == changed[0]
    code = f'G6_E_DISTILL_METHOD_{status.upper()}_DURING_RUN'
    if route in {'apply', 'historical', 'application'}:
        assert isinstance(answer, list) and answer, 'reporting must retain the stable race refusal'
        assert all(row['authority'] == 'A0' and row['verdict'] == 'abstain' for row in answer)
        assert all(row['outcome'] != 'defect_detected' and row.get('code') == code for row in answer)
    else:
        assert isinstance(answer, ValueError) and getattr(answer, 'code', None) == code, answer
    if window == 'after_check':
        assert calls == [], 'method executed after a completed hold or withdrawal'


@pytest.mark.parametrize('status', ['held', 'withdrawn'])
@pytest.mark.parametrize('route', RUN_ROUTES)
@pytest.mark.parametrize('window', RUN_WINDOWS)
def test_status_race_refuses_every_run_route(disk, monkeypatch, status, route, window):
    _run_schedule(disk, monkeypatch, status, route, window)


@pytest.mark.parametrize('status', ['held', 'withdrawn'])
@pytest.mark.parametrize('route', RUN_ROUTES)
@pytest.mark.parametrize('window', RUN_WINDOWS)
def test_run_race_class_rejects_the_removed_recheck_variant(disk, monkeypatch, status, route, window):
    # This is a negative control for the identical class assertion, not a second
    # implementation of the status rule. No production file is mutated.
    with pytest.raises(AssertionError):
        _run_schedule(disk, monkeypatch, status, route, window, mutant=True)


@pytest.mark.parametrize('route', ['run', 'train'])
@pytest.mark.parametrize('window', RUN_WINDOWS)
def test_reconciled_recurrence_removes_runtime_authority(disk, monkeypatch, route, window):
    _run_schedule(disk, monkeypatch, 'held', route, window, recurrence=True)


@pytest.mark.parametrize('route', ['run', 'train'])
@pytest.mark.parametrize('window', RUN_WINDOWS)
def test_recurrence_race_class_rejects_the_removed_recheck_variant(disk, monkeypatch, route, window):
    with pytest.raises(AssertionError):
        _run_schedule(disk, monkeypatch, 'held', route, window, recurrence=True, mutant=True)


ADMISSION_WINDOWS = ['after_check', 'after_load', 'after_execution', 'before_commit']


def _admission_schedule(disk, monkeypatch, status, window, route, *, mutant=False):
    registration = _known_registration(monkeypatch)
    _, candidate, frozen, _ = sample.__wrapped__()
    if route == 'restore':
        # A dialog restore starts with an existing proposal and withdrawn entry.
        # admit() records that proposal; do not append the same candidate twice.
        withdrawn = inactive(admit(candidate, disk), 'withdrawn')
    else:
        Stores().for_model(r.VerifierCandidate).put(candidate)
    if mutant:
        _remove_rechecks(monkeypatch)
    # Only restore has a baseline withdrawn entry. A concurrent request records
    # an inactive exact alias after this request's initial identity check; its ID
    # differs only in proposal metadata, so admission names a distinct candidate.
    alias = r.VerifierCandidate(**(candidate.model_dump(mode='json', exclude={'candidate_id'}) | {
        'model_calls': 1}))
    changed, loads, assessments = [], [], []

    def change():
        if not changed:
            entry = admit(alias, disk)
            if status == 'held':
                changed.append(inactive(entry, status))
            else:
                answer = gate.withdraw(entry.entry_id, Library(), reason='stop alias', person='Owner')
                assert answer.verdict == 'withdraw'
                changed.append(next(row for row in Library().list() if row.candidate_id == alias.candidate_id))

    def load(*args):
        loads.append(1)
        if window == 'after_load':
            change()
        return replace(frozen, registration_sha256=registration)

    monkeypatch.setattr(gate, '_load', load)
    check, assess, persist = gate._check_inactive, gate._assess, gate._persist
    checks = []

    def checked(*args, **kwargs):
        answer = check(*args, **kwargs)
        checks.append(1)
        if window == 'after_check' and len(checks) == 2:
            change()
        return answer

    def assessed(*args, **kwargs):
        answer = assess(*args, **kwargs)
        assessments.append(1)
        if window == 'after_execution' and len(assessments) == 2:
            change()
        return answer

    def committing(*args, **kwargs):
        if window == 'before_commit':
            change()
        return persist(*args, **kwargs)

    monkeypatch.setattr(gate, '_check_inactive', checked)
    monkeypatch.setattr(gate, '_assess', assessed)
    monkeypatch.setattr(gate, '_persist', committing)
    if route == 'restore':
        answer = gate.restore(withdrawn.entry_id, Library(), registration)
    elif route == 'decide_batch':
        answer = gate.decide_batch([candidate], Library(), registration)[0]
    else:
        answer = gate.decide(candidate, Library(), registration)
    assert changed, 'schedule did not reach the requested admission window'
    assert answer.verdict in {'block', 'refuse'}
    assert not answer.entry_id, 'refused admission reported an authority entry'
    assert all(entry.authority == 'A0' for entry in Library().list())
    if window in {'after_check', 'after_load'}:
        assert not any(row['kind'] == 'reserve' for row in gate._journal().entries())
    if window == 'after_check':
        assert loads == [], 'inactive method reached the custody loader'
    assert f'G6_E_DISTILL_METHOD_{status.upper()}_DURING_RUN' in ' '.join(answer.reasons)


@pytest.mark.parametrize('status', ['held', 'withdrawn'])
@pytest.mark.parametrize('window', ADMISSION_WINDOWS)
@pytest.mark.parametrize('route', ['decide', 'decide_batch', 'restore'])
def test_status_race_refuses_admission_before_access_or_authority(disk, monkeypatch, status, window, route):
    _admission_schedule(disk, monkeypatch, status, window, route)


@pytest.mark.parametrize('status', ['held', 'withdrawn'])
@pytest.mark.parametrize('window', ADMISSION_WINDOWS)
@pytest.mark.parametrize('route', ['decide', 'decide_batch', 'restore'])
def test_admission_race_class_rejects_the_removed_recheck_variant(disk, monkeypatch, status, window, route):
    with pytest.raises(AssertionError):
        _admission_schedule(disk, monkeypatch, status, window, route, mutant=True)
