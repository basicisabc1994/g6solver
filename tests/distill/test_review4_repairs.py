"""Review 4 class checks: revocation, bounded replay and admission preflight."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from g6.core.ledger import digest
from g6.distill import acceptance_gate as gate, records as r
from g6.distill.execution import ExecutionRefused, Observation, run_candidate
from g6.distill.ledger import RecordStore, Stores
from g6.distill.library import Library
from g6.harness import library_review_gate
from g6.mcp.distill_backend import Service
from tests.distill.test_records import bodies
from tests.distill import test_runtime_identity_boundaries as boundaries
from tests.distill.test_runtime_identity_boundaries import (
    AFTER, BEFORE, admit, inactive, method,
)

disk = boundaries.disk


def resolve(ref):
    return Stores().for_model(r.VerifierCandidate).get(
        Stores().for_model(r.LibraryEntry).get(ref).candidate_id)


@pytest.mark.parametrize('status', ['held', 'withdrawn'])
@pytest.mark.parametrize('route', ['evaluate', 'train', 'apply', 'admit'])
def test_status_change_completes_during_execution_and_removes_authority(disk, monkeypatch, status, route):
    entry = admit(method(), disk)
    entered, release = Event(), Event()
    detector = gate.weakening.detect_test_weakening

    def paused(before, after):
        entered.set()
        assert release.wait(10), 'detector release deadline'
        return detector(before, after)

    monkeypatch.setattr(gate.weakening, 'detect_test_weakening', paused)

    def run():
        try:
            if route == 'apply':
                return library_review_gate.check(BEFORE, AFTER)
            if route == 'train':
                return run_candidate(method(), [Observation(BEFORE, AFTER, {})], resolve)
            if route == 'admit':
                registration = digest('known-registration')
                policy, settings = gate._policy()
                monkeypatch.setattr(gate, '_policy', lambda: (policy, settings | {
                    'registrations': {registration: {'path': 'unused'}}}))

                def load(*args):
                    gate._run(method(), disk, Library().list())
                    raise ValueError('stop after application')

                monkeypatch.setattr(gate, '_load', load)
                candidate = method(template='skip_gate.v1')
                Stores().for_model(r.VerifierCandidate).put(candidate)
                return gate.decide(candidate, Library(), registration)
            return gate._run(method(), disk, Library().list())
        except (ValueError, ExecutionRefused) as error:
            return error

    def change():
        if status == 'held':
            return inactive(entry, status)
        answer = gate.withdraw(entry.entry_id, Library(), reason='owner stopped method', person='Owner')
        assert answer.verdict == 'withdraw'
        return answer

    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(run)
        try:
            assert entered.wait(5)
            pending = pool.submit(change)
            changed_while_running = Event()
            pending.add_done_callback(lambda _: changed_while_running.set())
            completed = changed_while_running.wait(1)
        finally:
            release.set()
        answer = running.result(timeout=10)
        pending.result(timeout=10)
    assert completed, 'owner status change waited for detector execution'
    code = f'G6_E_DISTILL_METHOD_{status.upper()}_DURING_RUN'
    if route == 'apply':
        assert answer and all(row['authority'] == 'A0' for row in answer)
        assert all(row['outcome'] != 'defect_detected' for row in answer)
        assert any(row.get('code') == code for row in answer)
    elif route == 'admit':
        assert answer.verdict in {'block', 'refuse'}
        assert code in ' '.join(answer.reasons)
    else:
        assert isinstance(answer, ValueError) and answer.code == code


@pytest.mark.parametrize('depth', [1, 2, 3, 4])
@pytest.mark.parametrize('route', ['train', 'evaluate'])
def test_recorded_replay_detector_work_is_linear_in_reuse_depth(disk, monkeypatch, depth, route):
    retired = admit(method(template='skip_gate.v1'), disk,
                    regression_suite=[{'case_ref': disk.case_id, 'verdict': 'no_concern'}])
    inactive(retired, 'withdrawn')
    contract = r.MethodContract(**(bodies()[2][1] | {
        'cost_bound': {'wall_s': 120., 'probe_runs': 0, 'model_calls': 0}}))
    Stores().for_model(r.MethodContract).put(contract)
    leaf = r.VerifierCandidate(**(method().model_dump(mode='json', exclude={'candidate_id'}) | {
        'contract': contract}))
    entry = admit(leaf, disk, contract_sha256=contract.content_id)
    for _ in range(depth):
        parent = r.VerifierCandidate(**(bodies()[3][1] | {
            'form': 'reuse', 'body': entry.entry_id, 'lineage': [entry.entry_id], 'contract': contract}))
        entry = admit(parent, disk, contract_sha256=contract.content_id)
    root = Stores().for_model(r.VerifierCandidate).get(entry.candidate_id)
    calls = []
    detector = gate.weakening.detect_test_weakening

    def counted(before, after):
        calls.append(1)
        return detector(before, after)

    monkeypatch.setattr(gate.weakening, 'detect_test_weakening', counted)
    if route == 'train':
        assert run_candidate(root, [Observation(BEFORE, AFTER, {})], resolve) == ['concern']
    else:
        assert gate._run(root, disk, Library().list()) == 'concern'
    assert len(calls) <= 3 * (depth + 1) + 1, f'depth {depth}: {len(calls)} detector calls'


@pytest.mark.parametrize('route', ['decide', 'decide_batch', 'load', 'restore', 'recheck', 'ablate', 'promote',
                                    'mcp_restore', 'mcp_train', 'mcp_decide'])
def test_unknown_anchor_precedes_all_status_and_recorded_reads(disk, monkeypatch, route):
    entry = admit(method(), disk)
    inactive(entry, 'withdrawn')
    candidate = method(template='skip_gate.v1')
    Stores().for_model(r.VerifierCandidate).put(candidate)
    registration = digest('unknown-registration')
    reads = []
    for module, name in [(gate, '_recorded_case'), (gate, '_inactive_record')]:
        old = getattr(module, name)

        def read(*args, reader=old, **kwargs):
            reads.append(1)
            return reader(*args, **kwargs)

        monkeypatch.setattr(module, name, read)
    status_reads = []
    listing = Library.list

    def status(self):
        status_reads.append(1)
        return listing(self)

    monkeypatch.setattr(Library, 'list', status)
    store_reads = []
    read_store = RecordStore._read

    def stored(self, **kwargs):
        store_reads.append(self.model.__name__)
        return read_store(self, **kwargs)

    monkeypatch.setattr(RecordStore, '_read', stored)
    service = Service(registration=registration)
    try:
        if route == 'decide':
            answer = gate.decide(candidate, Library(), registration)
        elif route == 'decide_batch':
            answer = gate.decide_batch([candidate], Library(), registration)[0]
        elif route == 'load':
            answer = gate._load(registration, candidate, Library())
        elif route.startswith('mcp_'):
            from g6.mcp.distill import _call, register
            if route == 'mcp_restore':
                answer = _call(service.restore, entry.entry_id)
            elif route == 'mcp_train':
                answer = _call(service.distill_start, 'test_weakening')
            else:
                handlers = {}

                class Server:
                    def tool(self):
                        def retain(fn):
                            handlers[fn.__name__] = fn
                            return fn
                        return retain

                register(Server(), object, None, backend=service)
                answer = asyncio.run(handlers['g6_library_decide'](
                    entry.entry_id, 'restore', 'owner restore', SimpleNamespace()))
        else:
            answer = getattr(gate, route)(entry.entry_id, Library(), registration)
    except ValueError as error:
        answer = error
    assert reads == [], 'unknown anchor read recorded case bodies'
    assert status_reads == [], 'unknown anchor read status before preflight'
    assert store_reads == [], 'unknown anchor read stored bodies before preflight'
    if isinstance(answer, gate.AcceptanceDecision):
        assert answer.verdict in {'block', 'refuse'}
    elif isinstance(answer, dict):
        assert answer.get('ok') is False
    else:
        assert isinstance(answer, ValueError)


@pytest.mark.parametrize('route', ['evaluate', 'train', 'apply'])
def test_invalid_runtime_input_precedes_status_reads(disk, monkeypatch, route):
    reads = []
    monkeypatch.setattr(Library, 'list', lambda self: reads.append(1) or [])
    with pytest.raises((ValueError, TypeError)):
        if route == 'evaluate':
            gate._run(method(), SimpleNamespace(before_tests=None, after_tests=''), [])
        elif route == 'train':
            run_candidate(method(), [Observation({'test.py': None}, AFTER, {})], resolve)
        else:
            library_review_gate.check({'test.py': None}, AFTER)
    assert reads == []


@pytest.mark.parametrize('route', ['train', 'evaluate'])
def test_shared_dependency_executes_once_per_observation_and_cache_ends_with_run(disk, monkeypatch, route):
    leaf = admit(method(), disk)
    reused = r.VerifierCandidate(**(bodies()[3][1] | {
        'form': 'reuse', 'body': leaf.entry_id, 'lineage': [leaf.entry_id]}))
    branch = admit(reused, disk)
    root = r.VerifierCandidate(**(bodies()[3][1] | {
        'form': 'composition', 'body': {'op': 'all', 'entries': [leaf.entry_id, branch.entry_id]},
        'lineage': [leaf.entry_id, branch.entry_id]}))
    calls = []
    detector = gate.weakening.detect_test_weakening

    def counted(before, after):
        calls.append(1)
        return detector(before, after)

    monkeypatch.setattr(gate.weakening, 'detect_test_weakening', counted)
    for expected_calls in (1, 2):
        if route == 'train':
            observation = Observation(BEFORE, AFTER, {})
            assert run_candidate(root, [observation, observation], resolve) == ['concern', 'concern']
        else:
            assert gate._run(root, disk, Library().list()) == 'concern'
        assert len(calls) == expected_calls


def test_memoisation_keeps_real_detector_flakiness_visible(disk, monkeypatch):
    reports = iter([['changed check'], [], ['changed check']])
    monkeypatch.setattr(gate.weakening, 'detect_test_weakening', lambda *args: SimpleNamespace(
        reasons=next(reports), checked=True, not_checked=[]))
    with pytest.raises(ValueError, match='flaky'):
        gate._measure(method(template='weakening.v1'), (gate.Observation(disk, 'positive', 'acceptance'),), [])
