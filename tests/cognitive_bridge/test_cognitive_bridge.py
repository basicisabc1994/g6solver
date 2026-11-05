# SPDX-License-Identifier: Apache-2.0
"""Run from the G6 repository with: pytest -q tests/test_cognitive_bridge.py"""
import json
import math

import pytest

from g6.cognitive_bridge import (
    BayesianUpdater, CognitiveAgent, CognitiveBridgeBlock, Hypothesis, InformationGainSelector,
    LLMProposer, MLAcquisitionSelector, Observation, Probe, StaticProposer,
    ToolSpec, check_beliefs, distill_blackbox, expected_information_gain, extract_tree,
)


def beliefs():
    return [Hypothesis("A", "A is true", 0.5), Hypothesis("B", "B is true", 0.5)]


def probe(p=0.8, positive=None, id="test"):
    return Probe(id, "Choose the discriminating test", "read", positive or {"A": True, "B": False}, p)


def tool(positive=True, p=0.8, cost=0):
    return ToolSpec("read", lambda question, args: Observation(positive, "local:fixture", "controlled observation"), p, cost)


def test_perfect_information_and_update():
    p = probe(1.0)
    prior = check_beliefs(beliefs())
    assert expected_information_gain(prior, p) == pytest.approx(1.0)
    assert BayesianUpdater().update(prior, p, True) == {"A": 1.0, "B": 0.0}
    assert BayesianUpdater().update(prior, p, False) == {"A": 0.0, "B": 1.0}


def test_no_discrimination_and_no_implicit_normalisation():
    prior = check_beliefs(beliefs())
    nondiscriminating = probe(0.9, {"A": True, "B": True})
    assert expected_information_gain(prior, nondiscriminating) == pytest.approx(0)
    assert InformationGainSelector().rank(prior, [nondiscriminating], 10) == []
    with pytest.raises(ValueError, match="sum to one"):
        check_beliefs([Hypothesis("A", "yes", 0.3), Hypothesis("B", "no", 0.3)])


def test_bayes_update_probability_and_zero_likelihood():
    assert BayesianUpdater().update(check_beliefs(beliefs()), probe(), True)["A"] == pytest.approx(0.8)
    with pytest.raises(ValueError, match="zero probability"):
        BayesianUpdater().update({"A": 1.0, "B": 0.0}, probe(1.0), False)


def test_agent_provenance_budget_and_bounded_calls():
    called = []

    def observation(question, arguments):
        called.append((question, arguments))
        return Observation(True, "source:fixture/001")

    registered = ToolSpec("read", observation, 0.8, 1.5)
    p = Probe("run1", "Find an independent signal", "read", {"A": True, "B": False}, .8, 1.5)
    report = CognitiveAgent(StaticProposer([p]), {"read": registered}).run(
        "research a claim", beliefs(), budget=2.0, max_steps=3)
    assert len(called) == 1
    assert report["posterior"]["A"] == pytest.approx(.8)
    assert report["history"][0]["source_ref"] == "source:fixture/001"
    assert report["remaining_budget"] == pytest.approx(.5)
    assert report["completion_state"] == "qualified-draft"


def test_agent_refuses_tool_metadata_mismatch():
    p = Probe("run1", "Question", "read", {"A": True, "B": False}, 0.9, 0)
    with pytest.raises(ValueError, match="registry"):
        CognitiveAgent(StaticProposer([p]), {"read": tool()}).run("test", beliefs(), budget=10)


class FakeLLM:
    def __init__(self, reply): self.reply = reply
    def complete(self, system, user, *, max_tokens):
        assert "hypotheses" in user and "allowed_tools" in user
        return json.dumps(self.reply)


def test_llm_only_proposes_and_cannot_choose_new_tools():
    proposal = {"id": "q1", "question": "Is the constraint present?", "tool": "read",
                "arguments": {"topic": "x"}, "positive_if": {"A": True, "B": False}, "novelty": .7}
    spec = tool(p=0.75, cost=2)
    p = LLMProposer(FakeLLM({"probes": [proposal]}), {"read": spec}).propose("goal", beliefs(), [])
    assert p[0].assumed_match_probability == .75
    assert p[0].cost == 2
    bad = {**proposal, "tool": "run_shell"}
    with pytest.raises(ValueError, match="non-allowlisted"):
        LLMProposer(FakeLLM({"probes": [bad]}), {"read": spec}).propose("goal", beliefs(), [])


def test_ml_selector_plug_compatible():
    class Predictor:
        def predict(self, X): return [x[0] - x[1] for x in X]
    p = probe()
    rank = MLAcquisitionSelector(Predictor()).rank(check_beliefs(beliefs()), [p], budget=3)
    assert rank[0]["selector"] == "caller_supplied_quantitative_ml"
    assert rank[0]["id"] == "test"


def test_g6_stateless_door_validates_and_does_not_verify():
    b = CognitiveBridgeBlock()
    response = b.infer({"op": "rank", "budget": 1, "hypotheses": [
        {"id": "A", "statement": "yes", "prior": .5}, {"id": "B", "statement": "no", "prior": .5}],
        "probes": [{"id": "p", "question": "is A?", "tool": "read",
                    "positive_if": {"A": True, "B": False}, "assumed_match_probability": .9}]})
    assert response.is_ok()
    assert response.value["completion_state"] == "qualified-draft"
    assert response.value["result"][0]["id"] == "p"
    bad = b.infer({"op": "rank", "hypotheses": [
        {"id": "A", "statement": "x", "prior": .2}, {"id": "B", "statement": "y", "prior": .2}]})
    assert bad.value["completion_state"] == "blocked-escalated"


@pytest.mark.parametrize("criterion", ["gini", "entropy"])
def test_exact_tree_transcription(criterion):
    from sklearn.tree import DecisionTreeClassifier
    X = [[float(i), float(i % 3)] for i in range(50)]
    y = ["high" if i > 24 else "low" for i in range(50)]
    model = DecisionTreeClassifier(criterion=criterion, random_state=3).fit(X, y)
    explicit = extract_tree(model, ["score", "other"])
    assert explicit.predict(X) == list(model.predict(X))
    assert explicit.explain_one([27, 0])["prediction"] == "high"
    assert explicit.to_dict()["provenance"] == "tree_transcription"


def test_exact_regression_transcription():
    from sklearn.tree import DecisionTreeRegressor
    X = [[float(i)] for i in range(60)]
    y = [math.sqrt(i) for i in range(60)]
    model = DecisionTreeRegressor(max_depth=3, random_state=7).fit(X, y)
    explicit = extract_tree(model, ["n"])
    assert explicit.predict(X) == pytest.approx(model.predict(X))


def test_blackbox_surrogate_holdout_and_leakage_refusal():
    from sklearn.ensemble import RandomForestClassifier
    # no repeated rows across holdout and training
    train = [[i / 50.0, (i % 17) / 13.0] for i in range(20, 100)]
    hold = [[i / 50.0, (i % 17) / 13.0] for i in range(100, 140)]
    model = RandomForestClassifier(n_estimators=20, max_depth=2, random_state=42).fit(
        train, [int(x[0] > 1.0) for x in train])
    explicit, report = distill_blackbox(model, train, hold, ["x", "y"], max_depth=3)
    assert report["metric"] == "agreement"
    assert .8 <= report["fidelity"] <= 1.0
    assert explicit.provenance == "blackbox_surrogate"
    with pytest.raises(ValueError, match="cross the training/holdout"):
        distill_blackbox(model, train, hold + [train[0]], ["x", "y"])


def test_blue_ocean_idea_generation_proposals_are_not_priors():
    from g6.cognitive_bridge import LLMIdeaGenerator
    idea = {"id": "novel1", "statement": "New mechanism A", "cross_domain_analogy": "Queueing theory",
            "falsifying_question": "Does delay actually cause churn?"}
    class IdeaLLM:
        def complete(self, system, user, *, max_tokens):
            assert "observations" in user
            return json.dumps({"ideas": [idea]})
    result = LLMIdeaGenerator(IdeaLLM()).generate("Find a neglected bottleneck", [])
    assert result[0].cross_domain_analogy == "Queueing theory"
    assert not hasattr(result[0], "prior")


def test_quantitative_export_requires_outside_evaluator():
    from g6.cognitive_bridge import evaluated_trace_rows, sklearn_payload
    p = probe()
    experiment = CognitiveAgent(StaticProposer([p]), {"read": tool()}).run(
        "test", beliefs(), budget=2)
    with pytest.raises(ValueError, match="externally scored"):
        evaluated_trace_rows(experiment["history"])
    record = dict(experiment["history"][0], measured_utility=0.42, evaluator_ref="independent:rubric-v1")
    rows = evaluated_trace_rows([record])
    assert rows[0]["measured_utility"] == .42
    assert sklearn_payload([record])["op"] == "fit_eval"


def test_second_proposal_sees_updated_belief():
    class StatefulProposer:
        def __init__(self): self.priors = []
        def propose(self, objective, hypotheses, history):
            self.priors.append(hypotheses[0].prior)
            return [Probe(f"p{len(self.priors)}", "probe", "read",
                          {"A": True, "B": False}, .8)]
    proposer = StatefulProposer()
    CognitiveAgent(proposer, {"read": tool()}).run("research", beliefs(), budget=2, max_steps=2)
    assert proposer.priors == pytest.approx([.5, .8])


def test_no_probe_is_blocked_not_claimed_success():
    report = CognitiveAgent(StaticProposer([]), {"read": tool()}).run(
        "research", beliefs(), budget=2, max_steps=2)
    assert report["history"] == []
    assert report["completion_state"] == "blocked-escalated"
