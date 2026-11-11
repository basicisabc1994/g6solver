# SPDX-License-Identifier: Apache-2.0
"""Review 3 of PR #8's second follow-up: its five findings, as the bridge's own tests (each reproduced on 36a93dd).

Two are a value read again, or reshaped, around the boundary: the block serialized an exact ``CognitiveInput``
(``model_dump``) before the boundary read it, so a dict subclass was erased first (finding 2, ``one_fact_two_rules``),
and G6's registry rebuilt the typed input from the caller's objects after the block's raw screen had read them, so a
change made between the two reads was what got planned (finding 5, ``check_then_act_race``). Three are a
collaborator's value that entered past the boundary (``one_fact_two_rules``): a fitted classifier's leaf scores went to
``max`` unread (finding 1), a draft's card text went to G6's lax card constructor unread (finding 3), and a black box's
training predictions were counted, not read (finding 4). Each is removed at its root: one read of a caller's input at
the boundary, and one reader for every value a collaborator hands the bridge, listed from the code by
``test_read_inventory.py``."""
from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest
from returns.result import Success

from g6.cognitive_bridge.block import ClaimInput, CognitiveBridgeBlock, CognitiveInput
from g6.cognitive_bridge.codes import INPUT, settled
from g6.cognitive_bridge.snapshot import NotPlainData
from g6.cognitive_bridge.xai import distill_blackbox, extract_tree
from g6.core.registry import get_registry


class ForeignMap(dict):
    pass


@pytest.mark.parametrize("scores", [["9", "10"], [True, False], [float("nan"), 1.0], [None]])
def test_finding_1_classifier_tree_scores_pass_the_same_number_rule_as_regression(scores):
    estimator = SimpleNamespace(
        n_features_in_=1,
        n_outputs_=1,
        classes_=["first", "second"][:len(scores)],
        tree_=SimpleNamespace(children_left=[-1], children_right=[-1], value=[[scores]]),
    )
    with pytest.raises(NotPlainData):
        extract_tree(estimator, ["x"])


def test_finding_2_an_exact_input_model_is_not_serialized_before_the_boundary_reads_it():
    claim = ClaimInput(id="c", statement="s", evidence_needs=["formal"], models={"formal": {"formula": "A"}})
    data = CognitiveInput(op="plan_methods", claims=[claim])
    data.claims[0].models = ForeignMap(data.claims[0].models)
    assert type(data.claims[0].models) is ForeignMap
    result = CognitiveBridgeBlock().infer(data).value
    assert result.get("code") == INPUT, result


@pytest.mark.parametrize("message", [b"candidate draft", bytearray(b"candidate draft")])
def test_finding_3_a_draft_cards_text_is_read_by_the_boundary_before_a_card_is_made(message):
    with pytest.raises(NotPlainData):
        settled(None, draft_card=(INPUT, message))


class _Blackbox:
    def __init__(self, invalid):
        self.invalid = invalid
        self.calls = 0

    def predict(self, rows):
        self.calls += 1
        return [self.invalid if self.calls == 1 else 0.5 for _ in rows]


@pytest.mark.parametrize("invalid", ["0.5", True])
def test_finding_4_regression_training_predictions_are_numbers_before_fitting(monkeypatch, invalid):
    fitted = []

    class Regressor:
        def __init__(self, **kwargs):
            self.n_features_in_ = 1
            self.n_outputs_ = 1

        def fit(self, rows, targets):
            fitted.append(targets)
            # A fake numerical learner, converting targets as numerical learners do.
            self.tree_ = SimpleNamespace(children_left=[-1], children_right=[-1], value=[[[float(targets[0])]]])
            return self

    sklearn = ModuleType("sklearn")
    tree = ModuleType("sklearn.tree")
    tree.DecisionTreeRegressor = tree.DecisionTreeClassifier = Regressor
    sklearn.tree = tree
    monkeypatch.setitem(sys.modules, "sklearn", sklearn)
    monkeypatch.setitem(sys.modules, "sklearn.tree", tree)
    with pytest.raises(NotPlainData):
        distill_blackbox(_Blackbox(invalid), [[i] for i in range(10)], [[i] for i in range(10, 15)], ["x"],
                         task="regression")
    assert not fitted


def test_finding_5_the_registry_path_answers_only_from_the_snapshot_the_raw_screen_took(monkeypatch):
    params = {"claims": [{"id": "c", "statement": "s", "evidence_needs": ["formal"],
                          "models": {"formal": {"formula": "A"}}}]}
    screened = CognitiveBridgeBlock.screen_raw

    def change_after_screen(block, raw):
        verdict = screened(block, raw)
        # A caller changing its retained nested input after the raw check, before typed validation. No bridge
        # record or bridge memory is changed.
        params["claims"][0]["models"] = ForeignMap(formal={"formula": "B"})
        return verdict

    monkeypatch.setattr(CognitiveBridgeBlock, "screen_raw", change_after_screen)
    outcome = get_registry().invoke("cognitive_bridge", "plan_methods", params)
    if not isinstance(outcome, Success):
        assert "[G6_E_" in outcome.failure()
        return
    result = outcome.unwrap()
    if result["completion_state"] == "blocked-escalated":
        return
    assert result["steps"][0]["params"]["formula"] == "A", result
