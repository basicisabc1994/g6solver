"""A declared bound's reads spend the copier's budget three to five times over, so values inside every declared
bound are refused (a regression of repair 3), and the message's "reached twice, about half" is not what the code does.

The router's bound (``_admit_claims``) reads every model of every claim, the ClaimIR's own declared check reads them
again, and the copy reads them a third time, all against one budget of 100,000 reaches and 4,000,000 characters. A
plan's steps hold the models once more, and a compilation holds the claim twice: five reads where b683e40 made three
copies. So: six claims with a 240 kB model each (20 claims and 250 kB each are the declared caps) are no longer
planned, from Python or through the block; and ``compile`` issues a compilation that ``execute`` then cannot take.
Each case below passes on b683e40.
"""
import json

from g6.cognitive_bridge import (
    ClaimIR, CognitiveAssuranceOrchestrator, MetacognitiveMethodSelector, MethodRouter, snapshot,
)
from g6.cognitive_bridge.block import CognitiveBridgeBlock
from g6.cognitive_bridge.mixed_methods import _admit_claims
from tests.cognitive_bridge.followup5_support import FakeRegistry, predictive_inputs, raw_claim, row_model

FORMULA = 'x' * 240_000  # one model of 240 kB: under the 250 kB model cap


def test_six_claims_each_with_a_model_under_the_cap_are_planned():
    claims = [ClaimIR(f'c{i}', 's', ('formal',), {'formal': {'formula': FORMULA}}) for i in range(6)]
    try:
        steps = len(MethodRouter().plan(claims).steps)
        refused = ''
    except ValueError as exc:
        steps, refused = 0, str(exc)
    assert steps == 6, f'six claims inside every declared bound were refused: {refused}'


def test_the_block_plans_six_claims_each_with_a_model_under_the_cap():
    raw = {'op': 'plan_methods', 'claims': [raw_claim(f'c{i}', {'formal': {'formula': FORMULA}}) for i in range(6)]}
    answer = CognitiveBridgeBlock().infer(raw).unwrap()
    assert answer['completion_state'] == 'qualified-draft', answer.get('reason')
    assert len(answer['steps']) == 6


def test_a_compilation_the_orchestrator_issued_can_be_taken_by_its_own_execute():
    inputs = predictive_inputs(1700)  # 1,700 rows of 6 numbers: 140 kB, under the 250 kB model cap
    assert len(json.dumps(row_model(1700))) < 250_000
    registry = FakeRegistry()
    orchestrator = CognitiveAssuranceOrchestrator(MetacognitiveMethodSelector(), registry=registry)
    compiled = orchestrator.compile(**inputs)
    assert compiled.completion_state == 'qualified-draft'
    answer = orchestrator.execute(compiled, approved=True)
    assert 'cannot be taken' not in answer.get('reason', ''), answer['reason']
    assert answer['code'] != 'G6_E_COGNITIVE_EXECUTION_NOT_APPROVED'


def test_a_value_under_a_declared_size_bound_is_reached_twice_as_the_message_says():
    model = {'formula': 'A', 'premises': ['A'] * 10_000}
    own = [0, 0]
    snapshot._walk(model, 'a model', budget=own)
    spent = [0, 0]
    snapshot._walk([ClaimIR('c', 's', ('formal',), {'formal': model})], 'claims', budget=spent, bound=_admit_claims)
    # the bound's read and the copy: twice the model, and a few reaches for the claim's other fields
    assert spent[0] <= 2 * own[0] + 100, (
        f'the copier spent {spent[0]} reaches on a claim whose model is {own[0]}: {spent[0] / own[0]:.1f} times')
