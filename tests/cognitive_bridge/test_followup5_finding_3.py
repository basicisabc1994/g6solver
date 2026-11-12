"""The block's new admission of a claim reads each model under a budget of its own (review 3's finding 5, through
the door repair 3 added for finding 2), and so does the router's own first check.

Repair 3 charges what a declared bound reads to the budget of the walk that runs it. The block's admission of its raw
arguments (``_admit_input`` -> ``_admit_claim`` -> ``_admit_models``) runs the same bound outside any walk, so every
model is read under a new budget before the copier's one budget refuses the whole. On b683e40 the two block cases
below were refused within the budget (95,648 and 99,620 reaches). The second needs no aliasing: it is plain JSON, as
the MCP door hands it. The router's own first check (``MethodRouter.plan`` -> ``_admit_claims``) has the same shape
(720,267 reaches on b683e40, where only needed models were read; more now).
"""
from g6.cognitive_bridge import ClaimIR, MethodRouter, snapshot
from g6.cognitive_bridge.block import CognitiveBridgeBlock
from tests.cognitive_bridge.followup5_support import count_reaches, raw_claim

METHODS = ('graph', 'causal', 'formal', 'predictive', 'optimisation')


def _model(items):
    return {'formula': 'A', 'premises': ['A'] * items}


def test_the_block_refuses_an_aliased_model_mapping_within_the_visit_budget(monkeypatch):
    model = _model(5000)  # about 20 kB encoded: far under the 250 kB model cap
    raw = {'op': 'plan_methods', 'claims': [raw_claim('c', {f'm{i}': model for i in range(200)})]}
    reaches = count_reaches(monkeypatch)
    answer = CognitiveBridgeBlock().screen_raw(raw).unwrap()
    assert 'size exceeds' in answer['reason']
    # one admission pass and one copy pass, each within the budget, would be at most twice the budget
    assert reaches[0] <= 3 * snapshot.MAX_ITEMS, (
        f'{reaches[0]} values were reached before the refusal: the visit budget is {snapshot.MAX_ITEMS}')


def test_the_block_refuses_plain_json_past_the_budget_within_the_visit_budget(monkeypatch):
    # 20 claims, five models each, 9,000 items a model: no object is shared, as JSON from the MCP door
    raw = {'op': 'plan_methods',
           'claims': [raw_claim(f'c{i}', {method: _model(9000) for method in METHODS}) for i in range(20)]}
    reaches = count_reaches(monkeypatch)
    answer = CognitiveBridgeBlock().infer(raw).unwrap()
    assert 'size exceeds' in answer['reason']
    assert reaches[0] <= 3 * snapshot.MAX_ITEMS, (
        f'{reaches[0]} values were reached before the refusal: the visit budget is {snapshot.MAX_ITEMS}')


def test_the_router_refuses_twenty_claims_past_the_budget_within_the_visit_budget(monkeypatch):
    claim = ClaimIR('c', 's', ('formal',), {method: _model(15_000) for method in METHODS})  # 75,000 reaches: one record
    reaches = count_reaches(monkeypatch)
    refused = ''
    try:
        MethodRouter().plan([claim] * 20)
    except ValueError as exc:
        refused = str(exc)
    assert 'size exceeds' in refused
    assert reaches[0] <= 3 * snapshot.MAX_ITEMS, (
        f'{reaches[0]} values were reached before the refusal: the visit budget is {snapshot.MAX_ITEMS}')
