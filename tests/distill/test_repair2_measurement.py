"""Every cost-judging replay counts real executions, independent of warm memos."""
import pytest

from g6.distill import acceptance_gate as gate, execution, records as r
from g6.distill.facts import _extract_facts
from g6.distill.runtime_state import run_memo, run_scope, sample
from tests.distill import test_runtime_identity_boundaries as boundaries
from tests.distill.test_runtime_identity_boundaries import method

disk = boundaries.disk


@pytest.mark.parametrize('route', ['acceptance', 'recorded'])
@pytest.mark.parametrize('overruns,refused', [(0, False), (3, False), (4, True), (36, True)])
def test_real_cost_decision_is_invariant_to_warm_memo(disk, monkeypatch, route, overruns, refused):
    original = method(template='weakening.v1')
    contract = r.MethodContract(**(original.contract.model_dump(mode='json') | {
        'cost_bound': {'wall_s': .05, 'probe_runs': 0, 'model_calls': 0}}))
    candidate = r.VerifierCandidate(**(original.model_dump(mode='json', exclude={'candidate_id'}) | {
        'contract': contract}))
    cases = [disk.model_copy(update={'unit_id': f'unit-{i}'}) for i in range(12)]
    detector = gate.weakening.detect_test_weakening if route == 'acceptance' else execution.run_template
    execute = execution._execute_candidate
    outcomes = []
    for warm in (False, True):
        clock, calls = [0.], []

        def measured(*args, **kwargs):
            clock[0] += .2 if len(calls) < overruns else .01
            calls.append(1)
            return detector(*args, **kwargs)

        if route == 'acceptance':
            monkeypatch.setattr(gate.weakening, 'detect_test_weakening', measured)
        else:
            monkeypatch.setattr(execution, 'run_template', measured)
            def memo_off(*args, **kwargs):
                run_memo().values.clear()
                return execute(*args, **kwargs)
            monkeypatch.setattr(execution, '_execute_candidate', execute if warm else memo_off)
        monkeypatch.setattr(execution.time, 'monotonic', lambda: clock[0])
        with run_scope():
            if warm:
                for index in range(3):
                    with sample(index):
                        if route == 'acceptance':
                            gate._execute(candidate, disk, [])
                        else:
                            before = {'tests/test_case.py': disk.before_tests}
                            after = {'tests/test_case.py': disk.after_tests}
                            observation = execution.Observation(before, after, _extract_facts(before, after))
                            execution._execute_candidate(candidate, [observation], lambda ref: candidate)
                calls.clear()
                clock[0] = 0.
            try:
                if route == 'acceptance':
                    values, costs = gate._measure(candidate, [gate.Observation(case, 'positive', 'acceptance')
                                                              for case in cases], [])
                else:
                    values = execution._recorded_verdicts(candidate, [case.model_dump(mode='json') for case in cases],
                                                          lambda ref: candidate)
                    costs = None
                outcomes.append((values, costs))
            except ValueError as error:
                assert 'cost bound' in str(error)
                outcomes.append('refuse')
            assert len(calls) == 36, 'verdict memo hits replaced real cost observations'
            assert (outcomes[-1] == 'refuse') is refused
    assert outcomes[0] == outcomes[1]


def test_recorded_replay_requires_real_evidence_even_for_empty_input(disk):
    with run_scope(), pytest.raises(ValueError, match='execution.*evidence|timing.*evidence'):
        execution._recorded_verdicts(method(), [], lambda ref: method())


def test_health_cost_needs_three_independent_observations(disk):
    with pytest.raises(ValueError, match='execution.*evidence|timing.*evidence'):
        gate._health_bar(method(), [.01, .01], ())


def test_repeated_recorded_case_reuses_original_measurements_without_adding_samples(disk, monkeypatch):
    calls = []
    execute = execution.run_template

    def counted(*args, **kwargs):
        calls.append(1)
        return execute(*args, **kwargs)

    monkeypatch.setattr(execution, 'run_template', counted)
    candidate = method(template='weakening.v1')
    case = disk.model_dump(mode='json')
    with run_scope():
        assert execution._recorded_verdicts(candidate, [case] * 20, lambda ref: candidate) == ['concern'] * 20
        assert len(calls) == 3
        assert execution._recorded_verdicts(candidate, [case] * 20, lambda ref: candidate) == ['concern'] * 20
        assert len(calls) == 3
        assert run_memo().measuring is False


def test_recorded_reuse_measures_warm_dependency_again(disk, monkeypatch):
    child = method(template='weakening.v1')
    contract = r.MethodContract(**(child.contract.model_dump(mode='json') | {
        'cost_bound': {'wall_s': .05, 'probe_runs': 0, 'model_calls': 0}}))
    parent = r.VerifierCandidate(**(child.model_dump(mode='json', exclude={'candidate_id'}) | {
        'form': 'reuse', 'body': 'a' * 64, 'contract': contract}))
    before, after = {'tests/test_case.py': disk.before_tests}, {'tests/test_case.py': disk.after_tests}
    observation = execution.Observation(before, after, _extract_facts(before, after))
    clock, calls = [0.], []
    execute = execution.run_template

    def measured(*args, **kwargs):
        calls.append(1)
        clock[0] += .2
        return execute(*args, **kwargs)

    monkeypatch.setattr(execution, 'run_template', measured)
    monkeypatch.setattr(execution.time, 'monotonic', lambda: clock[0])
    with run_scope():
        for index in range(3):
            with sample(index):
                execution._execute_candidate(child, [observation], lambda ref: child)
        with pytest.raises(ValueError, match='cost bound exceeded'):
            execution._recorded_verdicts(parent, [disk.model_dump(mode='json')], lambda ref: child)
        assert len(calls) == 6


def test_recorded_flakiness_still_refuses(disk, monkeypatch):
    from types import SimpleNamespace
    values = iter(['concern', 'no_concern', 'concern'])
    monkeypatch.setattr(execution, 'run_template', lambda *args, **kwargs: SimpleNamespace(verdict=next(values)))
    with pytest.raises(ValueError, match='flaky'):
        execution._recorded_verdicts(method(), [disk.model_dump(mode='json')], lambda ref: method())
