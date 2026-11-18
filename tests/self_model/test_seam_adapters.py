# SPDX-License-Identifier: Apache-2.0
"""Integration uses the real existing gates, with no executor or durable audit."""
from g6.self_model.risk_gate import GateReadout, PlanReadout
import pytest


def test_action_gate_serialised_readout_is_preserved():
    from g6.action_gating import evaluate_action
    from g6.action_gating.audit import ActionGateAuditLogger

    evaluation = evaluate_action({'action_id': 'read', 'action_type': 'read_file', 'target_backend': 'local',
                                  'target_resource': 'synthetic.txt'}, audit_logger=ActionGateAuditLogger(available=False))
    data = GateReadout.from_evaluation('a' * 64, evaluation.to_response_dict(), now=100, ttl_s=10)
    assert data.action_id == 'read'
    assert data.decision.read(now=100, evidence=True) == evaluation.gate_decision.decision
    assert data.reversibility.read(now=100, evidence=True) == 'reversible'
    assert data.external_effect.read(now=100, evidence=True) is False


def test_navigator_serialised_static_check_is_preserved():
    from g6.navigator import check_plan

    report = check_plan([], contracts={}, max_effect='read')
    data = PlanReadout.from_report('a' * 64, report.to_dict(), now=100, ttl_s=10)
    assert data.valid.read(now=100, evidence=True) is False
    assert data.effect.value == report.effect


def test_canonical_hash_compatibility_with_existing_ledger():
    from g6.core.ledger import digest as existing
    from g6.self_model.model import digest

    assert digest({'b': (1, 'a'), 'a': False}) == existing({'b': [1, 'a'], 'a': False})


def test_extraction_preserves_existing_public_symbols():
    import g6.metaharness as package
    from g6.metaharness.schema import Profile

    assert package.Profile is Profile
    assert callable(package.evaluate)
    assert set(package.__all__) <= set(dir(package))
    assert all(getattr(package, name) is not None for name in package.__all__)


@pytest.mark.parametrize('adapter,payload', [
    (GateReadout.from_evaluation, {'origin': 'self_prediction', 'action_capsule': {
        'action_id': 'fake', 'reversibility': 'reversible', 'external_side_effect': False, 'real_world_effect': False},
        'gate_decision': {'decision': 'allow'}}),
    (PlanReadout.from_report, {'origin': 'self_prediction', 'valid': True, 'effect': 'read', 'issues': []}),
])
def test_serialised_predictions_cannot_be_relabelled_as_gate_readouts(adapter, payload):
    with pytest.raises(ValueError, match='prediction'):
        adapter('a' * 64, payload, now=100, ttl_s=10)
