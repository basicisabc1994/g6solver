"""Timing decisions use real executions, independently of verdict memoisation."""
import pytest

from g6.distill import acceptance_gate as gate, records as r
from g6.distill.ledger import Stores
from g6.distill.library import Library
from g6.distill.runtime_state import run_memo, run_scope, sample
from tests.distill import test_runtime_identity_boundaries as boundaries
from tests.distill.test_runtime_identity_boundaries import admit, method

disk = boundaries.disk


@pytest.mark.parametrize('overruns,refused', [(0, False), (3, False), (4, True), (36, True)])
def test_memo_never_changes_cost_or_timing_decisions(disk, monkeypatch, overruns, refused):
    original = method()
    contract = r.MethodContract(**(original.contract.model_dump(mode='json') | {
        'cost_bound': {'wall_s': .05, 'probe_runs': 0, 'model_calls': 0}}))
    candidate = r.VerifierCandidate(**(original.model_dump(mode='json', exclude={'candidate_id'}) | {
        'contract': contract}))
    observations = tuple(gate.Observation(disk.model_copy(update={'unit_id': f'unit-{i}'}),
                                         'positive', 'acceptance') for i in range(12))
    detector, execute = gate.weakening.detect_test_weakening, gate._execute
    results = []
    for memo_enabled in (False, True):
        clock, calls = [0.], []

        def detect(*args):
            clock[0] += .2 if len(calls) < overruns else .01
            calls.append(1)
            return detector(*args)

        def uncached(*args, **kwargs):
            run_memo().values.clear()
            return execute(*args, **kwargs)

        monkeypatch.setattr(gate.time, 'monotonic', lambda: clock[0])
        monkeypatch.setattr(gate.weakening, 'detect_test_weakening', detect)
        monkeypatch.setattr(gate, '_execute', execute if memo_enabled else uncached)
        with run_scope():
            if refused:
                with pytest.raises(ValueError, match='cost bound exceeded'):
                    gate._measure(candidate, observations, [])
                results.append('refuse')
            else:
                verdicts, costs = gate._measure(candidate, observations, [])
                assert len(costs) == 36, 'cache lookups replaced independent execution evidence'
                assert len(calls) == 36
                # Reusing these verdicts must add no execution or timing sample.
                again = gate._measure(candidate, observations, [])
                assert again == (verdicts, costs)
                assert len(calls) == 36
                results.append((verdicts, costs))
    assert results[0] == results[1]


def test_duplicate_observation_adds_no_timing_sample(disk, monkeypatch):
    candidate = method()
    detector = gate.weakening.detect_test_weakening
    calls = []

    def detect(*args):
        calls.append(1)
        return detector(*args)

    monkeypatch.setattr(gate.weakening, 'detect_test_weakening', detect)
    observation = gate.Observation(disk, 'positive', 'acceptance')
    verdicts, costs = gate._measure(candidate, (observation,) * 12, [])
    assert verdicts == ['concern'] * 12
    assert len(costs) == len(calls) == 3


def test_timing_requires_three_real_samples_and_still_detects_flakiness(disk, monkeypatch):
    from types import SimpleNamespace

    candidate = method(template='weakening.v1')
    outcomes = iter([['changed check'], [], ['changed check']])
    monkeypatch.setattr(gate.weakening, 'detect_test_weakening', lambda *args: SimpleNamespace(
        reasons=next(outcomes), checked=True))
    with pytest.raises(ValueError, match='flaky'):
        gate._measure(candidate, (gate.Observation(disk, 'positive', 'acceptance'),), [])


def test_empty_measurement_cannot_supply_cost_evidence(disk):
    with pytest.raises(ValueError, match='execution.*evidence|timing.*evidence'):
        gate._measure(method(), (), [])


def test_cached_measurement_cannot_skip_candidate_validation(disk):
    candidate = method()
    observation = gate.Observation(disk, 'positive', 'acceptance')
    forged = candidate.model_copy(update={'form': 'predicate', 'body': 'invalid Python! supersecret'})
    with run_scope():
        gate._measure(candidate, (observation,), [])
        with pytest.raises(ValueError):
            gate._measure(forged, (observation,), [])


def test_warm_dependency_cache_cannot_lower_composition_cost(disk, monkeypatch):
    original = method()
    contract = r.MethodContract(**(original.contract.model_dump(mode='json') | {
        'cost_bound': {'wall_s': .05, 'probe_runs': 0, 'model_calls': 0}}))
    child = r.VerifierCandidate(**(original.model_dump(mode='json', exclude={'candidate_id'}) | {
        'contract': contract}))
    Stores().for_model(r.MethodContract).put(contract)
    entry = admit(child, disk, contract_sha256=contract.content_id)
    parent = r.VerifierCandidate(**(child.model_dump(mode='json', exclude={'candidate_id'}) | {
        'form': 'reuse', 'body': entry.entry_id}))
    detector, clock, calls = gate.weakening.detect_test_weakening, [0.], []

    def detect(*args):
        clock[0] += .2
        calls.append(1)
        return detector(*args)

    monkeypatch.setattr(gate.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(gate.weakening, 'detect_test_weakening', detect)
    entries = Library().list()
    with run_scope():
        for index in range(3):
            with sample(index):
                assert gate._run(child, disk, entries) == 'concern'
        assert len(calls) == 3
        with pytest.raises(ValueError, match='cost bound exceeded'):
            gate._measure(parent, (gate.Observation(disk, 'positive', 'acceptance'),), entries)
        assert len(calls) == 6, 'timing evidence must execute the warm dependency again'
