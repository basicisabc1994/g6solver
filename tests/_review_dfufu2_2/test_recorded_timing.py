"""Timing failures must make recorded identity evidence unavailable."""
from g6.core.ledger import canonical
from g6.core.storage import state_path
from g6.distill import execution, records as r
from g6.distill.facts import _extract_facts
from g6.distill.ledger import Stores
from tests.distill import test_runtime_identity_boundaries as boundaries
from tests.distill.test_runtime_identity_boundaries import admit, inactive, method, BEFORE, AFTER
from tests._review_dfufu2_2.test_partial_authority import tmp_path

disk = boundaries.disk


def test_cached_recorded_verdicts_cannot_dilute_real_cost_overruns(disk, monkeypatch):
    cases = [disk.model_copy(update={'unit_id': f'recorded-unit-{index}'}) for index in range(20)]
    for case in cases:
        path = state_path('distill', 'held_out', 'regression_cases', f'{case.case_id}.json')
        path.write_text(canonical(case.model_dump(mode='json')) + '\n', encoding='utf-8')
    old = admit(method(template='weakening.v1'), disk, regression_suite=[
        {'case_ref': case.case_id, 'verdict': 'concern'} for case in cases])
    inactive(old, 'withdrawn')
    proposed = method(template='skip_gate.v1')
    contract = r.MethodContract(**(proposed.contract.model_dump(mode='json') | {
        'cost_bound': {'wall_s': .05, 'probe_runs': 0, 'model_calls': 0}}))
    proposed = r.VerifierCandidate(**(proposed.model_dump(mode='json', exclude={'candidate_id'}) | {
        'contract': contract}))
    Stores().for_model(r.MethodContract).put(contract)
    Stores().for_model(r.VerifierCandidate).put(proposed)
    clock, durations = [0.], []
    original = execution.run_template

    def measured(*args, **kwargs):
        # Every real execution exceeds the bound. Repeated bodies may reuse
        # verdicts, but the resulting cache hits are not timing observations.
        elapsed = .2
        durations.append(elapsed)
        clock[0] += elapsed
        return original(*args, **kwargs)

    monkeypatch.setattr(execution.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(execution, 'run_template', measured)
    observation = execution.Observation(BEFORE, AFTER, _extract_facts(BEFORE, AFTER))
    refusal, result = None, None
    try:
        result = execution.run_candidate(proposed, [observation], lambda ref: proposed)
    except execution.ExecutionRefused as error:
        refusal = error
    assert durations and all(cost > contract.cost_bound.wall_s for cost in durations)
    assert refusal is not None and refusal.code == 'G6_E_DISTILL_IDENTITY_UNKNOWN', (
        f'{len(durations)} real executions, all over .05s, were accepted: {result}')
