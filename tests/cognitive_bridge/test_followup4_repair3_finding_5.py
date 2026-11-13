"""The bound compile hands the copier reads every model under a budget of its own, before the copier's one budget.

The walker counts every reach against one budget and spends it again for a repeated reference. ``_admit_models`` runs
before the copy and admits each model of the mapping with a new budget (``_admit_json`` -> ``view`` -> a walk), so a
mapping whose keys name one model many times is read (models x model size) before the copier's budget refuses it. On
6e39ab0 the same call was refused within the budget (95,464 reaches for the case below at 60 keys); the mapping may
hold 50,000 keys.
"""
from g6.cognitive_bridge import CognitiveAssuranceOrchestrator, MetacognitiveMethodSelector, snapshot
from tests.cognitive_bridge.followup4_repair3_support import compile_inputs, count_reaches


def test_compile_refuses_an_aliased_model_mapping_within_the_visit_budget(monkeypatch):
    model = {'formula': 'A', 'premises': ['A'] * 5000}  # about 20 kB encoded: far under the 250 kB model cap
    inputs = compile_inputs()
    inputs['variant_models'] = {f'v{i}': model for i in range(200)}  # 200 names for one model: 1,000,000 reaches
    reaches = count_reaches(monkeypatch)
    refused = ''
    try:
        CognitiveAssuranceOrchestrator(MetacognitiveMethodSelector()).compile(**inputs)
    except ValueError as exc:
        refused = str(exc)
    assert 'size exceeds' in refused
    # one admission pass and one copy pass, each within the budget, would be at most twice the budget
    assert reaches[0] <= 3 * snapshot.MAX_ITEMS, (
        f'{reaches[0]} values were reached before the refusal: the visit budget is {snapshot.MAX_ITEMS}')
