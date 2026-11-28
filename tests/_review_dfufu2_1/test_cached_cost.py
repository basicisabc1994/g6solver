import time

import pytest

from g6.distill import acceptance_gate as gate, records as r
from g6.distill.held_out.case_gate import CaseRecord
from g6.distill.ledger import Stores
from tests._review_dfufu2_1.test_authority_windows import disk
from tests.distill.test_runtime_identity_boundaries import method


def test_cached_observations_cannot_hide_a_method_over_its_cost_bound(disk, monkeypatch):
    original = method()
    contract = r.MethodContract(**(
        original.contract.model_dump(mode='json') | {
            'cost_bound': {'wall_s': .05, 'probe_runs': 0, 'model_calls': 0},
        }
    ))
    candidate = r.VerifierCandidate(**(
        original.model_dump(mode='json', exclude={'candidate_id'}) | {'contract': contract}
    ))
    Stores().for_model(r.MethodContract).put(contract)
    Stores().for_model(r.VerifierCandidate).put(candidate)
    # Distinct bug units may have the same before/after tests.
    cases = [CaseRecord.model_validate(disk.model_dump(mode='json') | {
        'unit_id': f'independent-unit-{index}',
    }) for index in range(12)]
    assert len({case.case_id for case in cases}) == 12
    detector = gate.weakening.detect_test_weakening
    elapsed = []

    def slow_detector(*args):
        began = time.monotonic()
        time.sleep(.15)
        result = detector(*args)
        elapsed.append(time.monotonic() - began)
        return result

    monkeypatch.setattr(gate.weakening, 'detect_test_weakening', slow_detector)
    try:
        with pytest.raises(ValueError, match='cost bound exceeded'):
            gate._measure(candidate, tuple(
                gate.Observation(case, 'positive', 'acceptance') for case in cases
            ), [])
    finally:
        assert elapsed and all(cost > contract.cost_bound.wall_s for cost in elapsed)
