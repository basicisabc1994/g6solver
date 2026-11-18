# SPDX-License-Identifier: Apache-2.0
"""Breaks caught: self-predictions clearing the risk API and incomplete protection."""
import pytest

from g6.self_model.model import Field
from g6.self_model.risk_gate import GateReadout, PlanReadout, ProtectedManifest, assess


def fact(value, source='fixture.measure'):
    return Field.measured(value, source=source, observed_at=100, ttl_s=10)


def inputs():
    source = 'g6.action_gating.evaluate_action'
    return dict(action_id='a', action_sha256='a' * 64, touched_paths=('docs/x.md',), affected=('component',),
                manifest=ProtectedManifest(patterns=fact(('gate',)), complete=fact(True)),
                gate=GateReadout(action_sha256='a' * 64, action_id='a', decision=fact('allow', source),
                                 reversibility=fact('reversible', source), external_effect=fact(False, source)),
                plan=None, has_plan=False, regression_history=tuple(fact(False) for _ in range(30)), now=100)


def test_direct_risk_api_requires_measured_reference_outcomes():
    data = inputs()
    assert assess(**data).decision == 'proceed'
    data['regression_history'] = tuple(Field.predicted(False, source='fixture.measure', observed_at=100, ttl_s=10)
                                       for _ in range(30))
    assert assess(**data).decision == 'more_evidence'


@pytest.mark.parametrize('part', ['manifest', 'gate', 'plan'])
def test_no_gate_evidence_position_accepts_self_predictions(part):
    data = inputs()
    def prediction(value, source):
        return Field.predicted(value, source=source, observed_at=100, ttl_s=10)
    if part == 'manifest':
        data['manifest'] = ProtectedManifest(patterns=prediction(('gate',), 'fixture.reader'), complete=fact(True))
    elif part == 'gate':
        data['gate'] = data['gate'].model_copy(update={'decision': prediction('allow', 'g6.action_gating.evaluate_action')})
    else:
        data['has_plan'] = True
        data['plan'] = PlanReadout(action_sha256='a' * 64, valid=prediction(True, 'g6.navigator.check_plan'),
                                   effect=fact('read', 'g6.navigator.check_plan'))
    assert assess(**data).decision in {'owner', 'more_evidence'}


def test_broad_impact_and_upper_risk_bound_do_not_clear():
    data = inputs()
    data['affected'] = tuple('abcdefg')
    assert assess(**data).decision == 'more_evidence'
    data['affected'] = ('a',)
    data['regression_history'] = tuple(fact(True) for _ in range(30))
    assert assess(**data).decision == 'owner'
    data['regression_history'] = tuple(fact(False) for _ in range(5))
    assert assess(**data).decision == 'owner'  # zero observed events still has a wide upper bound
    data['affected'] = ()
    assert assess(**data).decision == 'more_evidence'


@pytest.mark.parametrize('path', ['src/g6/core/stop.py', 'src/g6/action_gating/schema.py',
    'src/g6/navigator/authority.py', 'src/g6/workforce/receipt_store.py', 'tests/a.py',
    'src/g6/metaharness/schema.py', 'src/g6/ml/adapt_sklearn/__init__.py', 'src/g6/learning/promotion.py'])
def test_union_read_from_existing_sources_protects_all_categories(path):
    # Only the external reader imports these writer-containing modules. The
    # self-model receives the union as data; no list is copied into product code.
    from g6._agentic.protected import TRUST_DOORS, DOOR_WORDS
    from g6.core.adaptation_gate import PROTECTED_FRAGMENTS
    from g6.metaharness.api import JUDGING_PATTERNS
    from g6.self_training.patch_promotion_gate import PROTECTED_PATH_FRAGMENTS

    patterns = (*PROTECTED_FRAGMENTS, *PROTECTED_PATH_FRAGMENTS, *DOOR_WORDS,
                *(name.replace('.', '/') for name in TRUST_DOORS), *JUDGING_PATTERNS,
                'g6/ml/adapt_sklearn/')  # certificate-bound package supplied by the reader
    data = inputs()
    data['manifest'] = ProtectedManifest(patterns=fact(patterns), complete=fact(True))
    data['touched_paths'] = (path,)
    assert assess(**data).decision == 'owner'
