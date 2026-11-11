# SPDX-License-Identifier: Apache-2.0
"""Review 4, findings 2 and 3 (check_then_act_race, met in all four reviews), removed at its root: what a collaborator
returns is taken as a snapshot the moment it returns, checked against what was checked, and only that snapshot is used
afterwards; a collaborator is handed only a fresh copy, never a reference to what the bridge checked. Each member of
the class is here, with one the old pattern missed, malformed answers, and a stop between steps."""
from __future__ import annotations

import math
import sys
import types
from dataclasses import replace
from types import SimpleNamespace

import pytest

from g6.cognitive_bridge import (
    BayesianUpdater, ClaimIR, CognitiveAgent, CrossMethodConflictGate, EvaluatedTrial, Hypothesis,
    InformationGainSelector, LLMClaimCompiler, LLMIdeaGenerator, LLMMetacognitiveCritic, LLMProposer,
    MetacognitiveMethodSelector, MetacognitivePlanner, MethodVariant, MLAcquisitionSelector, Observation, Probe, ProblemSignature, ScopedAssertion, StaticProposer, ToolSpec, distill_blackbox,
)
from g6.cognitive_bridge import codes
from g6.core import door


def _hypotheses():
    return [Hypothesis("A", "a", .5), Hypothesis("B", "b", .5)]


def _probe(id="p", **kw):
    return Probe(id, "measure", "read", {"A": True, "B": False}, .8, **kw)


def _tool(calls=None):
    def observe(question, arguments):
        if calls is not None:
            calls.append(question)
        return Observation(True, "actual:source")
    return ToolSpec("read", observe)


# --- Review 4's two tests, as the reviewer wrote them ---------------------------------------------------------------

def test_finding_2_selector_cannot_rewrite_the_beliefs_used_to_credit_an_observation(monkeypatch):
    monkeypatch.setattr(door, "person_model", lambda: None)

    class Selector:
        def rank(self, beliefs, probes, budget):
            # Work on the selector's input; the agent must retain the prior it checked.
            beliefs.update(A=.9, B=.1)
            return [{"id": probes[0].id, "expected_information_bits": .1}]

    probe = Probe("p", "measure", "read", {"A": True, "B": False}, .8)
    try:
        out = CognitiveAgent(StaticProposer([probe]),
                             {"read": ToolSpec("read", lambda q, a: Observation(True, "actual:source"))},
                             selector=Selector()).run(
            "measure", [Hypothesis("A", "a", .5), Hypothesis("B", "b", .5)], budget=1, max_steps=1)
    except (TypeError, AttributeError):
        return  # A read-only belief map can refuse the attempted write.
    assert out["posterior"]["A"] == pytest.approx(.8), "selector rewrote the checked prior before Bayes update"


def test_finding_3_selector_cannot_insert_an_unvalidated_probe_before_tool_execution(monkeypatch):
    monkeypatch.setattr(door, "person_model", lambda: None)
    calls = []

    class Selector:
        def rank(self, beliefs, probes, budget):
            # This cannot pass _validate_probe: it does not predict hypothesis B.
            probes.append(Probe("injected", "unvalidated question", "read", {"A": True}, .8))
            return [{"id": "injected", "expected_information_bits": .1}]

    def observe(question, arguments):
        calls.append(question)
        return Observation(True, "actual:source")

    valid = Probe("p", "measure", "read", {"A": True, "B": False}, .8)
    try:
        CognitiveAgent(StaticProposer([valid]), {"read": ToolSpec("read", observe)}, selector=Selector()).run(
            "measure", [Hypothesis("A", "a", .5), Hypothesis("B", "b", .5)], budget=1, max_steps=1)
    except (TypeError, ValueError, AttributeError):
        pass  # Refusing before running the tool is safe.
    assert calls == [], "selector-inserted probe reached the tool before its validation failed"


# --- The agent's other members, and the ones the old pattern missed --------------------------------------------------

def test_a_selector_that_writes_past_the_read_only_view_changes_nothing():
    class Selector:
        def rank(self, beliefs, probes, budget):
            dict.update(beliefs, A=.9, B=.1)  # a write no read-only view refuses
            return [{"id": probes[0].id, "expected_information_bits": .1}]

    out = CognitiveAgent(StaticProposer([_probe()]), {"read": _tool()}, selector=Selector()).run(
        "measure", _hypotheses(), budget=1, max_steps=1)
    assert out["posterior"]["A"] == pytest.approx(.8), f"the selector's write reached the prior: {out['posterior']}"
    assert out["history"][0]["prior"] == {"A": .5, "B": .5}


def test_a_selector_that_keeps_its_arguments_cannot_change_them_while_the_step_runs():
    class Selector:
        def rank(self, beliefs, probes, budget):
            self.beliefs, self.probes = beliefs, probes
            return [{"id": probes[0].id, "expected_information_bits": .1}]

    selector = Selector()

    def observe(question, arguments):
        # while the observed tool is in flight, the selector's references are used to rewrite what was checked
        dict.update(selector.beliefs, A=.9, B=.1)
        object.__setattr__(selector.probes[0], "positive_if", {"A": False, "B": True})
        return Observation(True, "actual:source")

    out = CognitiveAgent(StaticProposer([_probe()]), {"read": ToolSpec("read", observe)}, selector=selector).run(
        "measure", _hypotheses(), budget=1, max_steps=1)
    assert out["posterior"]["A"] == pytest.approx(.8), f"a kept reference changed what was checked: {out['posterior']}"


def test_a_ranking_is_used_as_it_was_returned():
    class Selector:
        def rank(self, beliefs, probes, budget):
            self.rows = [{"id": probes[0].id, "expected_information_bits": .1}]
            return self.rows

    selector = Selector()

    def observe(question, arguments):
        selector.rows[0]["expected_information_bits"] = 99.0  # rewritten after it was returned
        return Observation(True, "actual:source")

    out = CognitiveAgent(StaticProposer([_probe()]), {"read": ToolSpec("read", observe)}, selector=selector).run(
        "measure", _hypotheses(), budget=1, max_steps=1)
    assert out["history"][0]["expected_information_bits"] == .1, "the ranking was read after it was rewritten"


def test_a_write_past_every_view_to_the_checked_prior_is_refused_where_it_is_used():
    # The prior the update uses is the one checked, by its digest: even a write reaching the agent's own copy (here
    # through the agent's frame) is refused, not credited.
    class Selector:
        def rank(self, beliefs, probes, budget):
            frame = sys._getframe(1)
            while frame is not None and frame.f_code.co_name != "run":
                frame = frame.f_back
            dict.update(frame.f_locals["beliefs"], A=.9, B=.1)
            return [{"id": probes[0].id, "expected_information_bits": .1}]

    with pytest.raises(ValueError, match="changed after"):
        CognitiveAgent(StaticProposer([_probe()]), {"read": _tool()}, selector=Selector()).run(
            "measure", _hypotheses(), budget=1, max_steps=1)


def test_an_updaters_posterior_is_used_as_it_was_returned():
    class Updater:
        def update(self, beliefs, probe, positive):
            self.last = BayesianUpdater().update(beliefs, probe, positive)
            return self.last

    updater = Updater()

    class Proposer:
        def __init__(self):
            self.calls = 0

        def propose(self, objective, hypotheses, history):
            self.calls += 1
            if self.calls == 2:
                updater.last.update(A=0.0, B=1.0)  # rewritten after it was returned
            return [_probe()] if self.calls == 1 else []

    out = CognitiveAgent(Proposer(), {"read": _tool()}, updater=updater).run(
        "measure", _hypotheses(), budget=1, max_steps=2)
    assert out["posterior"]["A"] == pytest.approx(.8), f"the posterior was read after it was rewritten: {out}"


@pytest.mark.parametrize("posterior", [
    {"A": 1.0}, {"A": .5, "B": .5, "C": 0.0}, {"A": math.nan, "B": .5}, {"A": 1.5, "B": -.5}, {"A": .5, "B": .2},
    {"A": True, "B": False}, [.8, .2], None,
], ids=["missing", "extra", "nan", "outside", "unnormalised", "booleans", "a-list", "none"])
def test_a_malformed_posterior_is_refused(posterior):
    class Updater:
        def update(self, beliefs, probe, positive):
            return posterior

    with pytest.raises(ValueError):
        CognitiveAgent(StaticProposer([_probe()]), {"read": _tool()}, updater=Updater()).run(
            "measure", _hypotheses(), budget=1, max_steps=1)


@pytest.mark.parametrize("ranking, error", [
    ({"id": "p", "expected_information_bits": .1}, ValueError), ([42], ValueError), ([{"id": "p"}], ValueError),
    ([{"id": "p", "expected_information_bits": math.nan}], ValueError),
    ([{"id": "p", "expected_information_bits": True}], ValueError),
    ([{"id": "p", "expected_information_bits": -1.0}], ValueError),
    ((row for row in [{"id": "p", "expected_information_bits": .1}]), TypeError),
], ids=["a-mapping", "not-rows", "no-bits", "nan-bits", "bool-bits", "negative-bits", "a-generator"])
def test_a_malformed_ranking_is_refused_before_any_tool_runs(ranking, error):
    calls = []

    class Selector:
        def rank(self, beliefs, probes, budget):
            return ranking

    with pytest.raises(error):
        CognitiveAgent(StaticProposer([_probe()]), {"read": _tool(calls)}, selector=Selector()).run(
            "measure", _hypotheses(), budget=1, max_steps=1)
    assert calls == []


class _Subprobe(Probe):
    pass


@pytest.mark.parametrize("proposal", [[_Subprobe("p", "measure", "read", {"A": True, "B": False}, .8)],
                                      {"p": "a mapping"}, None, [_probe(), "not a probe"]],
                         ids=["a-probe-subclass", "a-mapping", "none", "not-a-probe"])
def test_a_malformed_proposal_is_refused_before_any_tool_runs(proposal):
    calls = []

    class Proposer:
        def propose(self, objective, hypotheses, history):
            return proposal

    with pytest.raises(ValueError):
        CognitiveAgent(Proposer(), {"read": _tool(calls)}).run("measure", _hypotheses(), budget=1, max_steps=1)
    assert calls == []


@pytest.mark.parametrize("stop_at", ["propose", "rank", "observe", "update"])
def test_a_stop_between_two_steps_credits_only_what_the_door_passed_and_a_new_run_starts_clean(stop_at):
    class Flaky:
        def __init__(self):
            self.stop_at, self.step, self.seen = stop_at, 0, []

        def _maybe_stop(self, where):
            if self.stop_at == where and self.step == 2:
                raise RuntimeError(f"stopped at {where}")

        def propose(self, objective, hypotheses, history):
            self.step += 1
            self.seen.append((tuple(h.prior for h in hypotheses), len(history)))
            self._maybe_stop("propose")
            return [_probe(f"p{self.step}")]

        def rank(self, beliefs, probes, budget):
            self._maybe_stop("rank")
            return InformationGainSelector().rank(beliefs, probes, budget)

        def observe(self, question, arguments):
            self._maybe_stop("observe")
            return Observation(True, "actual:source")

        def update(self, beliefs, probe, positive):
            self._maybe_stop("update")
            return BayesianUpdater().update(beliefs, probe, positive)

    flaky = Flaky()
    agent = CognitiveAgent(flaky, {"read": ToolSpec("read", flaky.observe)}, selector=flaky, updater=flaky)
    if stop_at == "observe":
        out = agent.run("measure", _hypotheses(), budget=3, max_steps=3)
        assert [h["probe_id"] for h in out["history"]] == ["p1"] and out["completion_state"] == "blocked-escalated"
    else:
        with pytest.raises(RuntimeError):
            agent.run("measure", _hypotheses(), budget=3, max_steps=3)
    flaky.stop_at, flaky.step, flaky.seen = None, 0, []
    again = agent.run("measure", [Hypothesis("A", "a", .3), Hypothesis("B", "b", .7)], budget=3, max_steps=2)
    assert flaky.seen[0] == ((.3, .7), 0), "a new run saw state from the stopped one"
    assert [h["probe_id"] for h in again["history"]] == ["p1", "p2"]
    assert again["initial_beliefs"] == {"A": .3, "B": .7}


# --- The models' replies ------------------------------------------------------------------------------------------

def _llm(reply):
    class Model:
        def complete(self, system, user, *, max_tokens):
            return reply
    return Model()


_USERS = {
    "proposer": lambda m: LLMProposer(m, {"read": _tool()}).propose("goal", _hypotheses(), []),
    "ideas": lambda m: LLMIdeaGenerator(m).generate("goal", []),
    "claims": lambda m: LLMClaimCompiler(m).draft("Does A entail A?"),
    "critic": lambda m: LLMMetacognitiveCritic(m).propose("Assess the claim", "A -> A", []),
}
_VALID = {"proposer": '{"probes": []}', "ideas": '{"ideas": []}', "critic": '{"candidates": []}',
          "claims": '{"claims": [{"id": "c", "statement": "s", "evidence_needs": ["formal"], '
                    '"models": {"formal": {"formula": "A"}}}]}'}


@pytest.mark.parametrize("user", sorted(_USERS))
@pytest.mark.parametrize("kind", ["bytes", "none", "a-mapping"])
def test_a_models_reply_is_text_or_refused(user, kind):
    reply = {"bytes": _VALID[user].encode(), "none": None, "a-mapping": {"probes": []}}[kind]
    # since follow-up 2 of PR #8 a reply in bytes is refused where it returns, as what is not JSON data
    with pytest.raises(ValueError, match="text|not JSON data"):
        _USERS[user](_llm(reply))
    _USERS[user](_llm(_VALID[user]))  # the same reply as text is used


# --- The predictor's scores ---------------------------------------------------------------------------------------

def test_a_predictors_scores_are_held_as_returned_and_checked():
    numpy = pytest.importorskip("numpy")

    class Scores:
        def __init__(self, value):
            self.value = value

        def predict(self, rows):
            return self.value(rows)

    beliefs = {"A": .5, "B": .5}
    ranked = MLAcquisitionSelector(Scores(lambda rows: [.25] * len(rows))).rank(beliefs, [_probe()], 1)
    assert ranked[0]["score"] == .25 and type(ranked[0]["score"]) is float
    # since follow-up 2 of PR #8 a numpy array is not plain JSON data: it is refused where it returns, with its code
    with pytest.raises(ValueError) as refused:
        MLAcquisitionSelector(Scores(lambda rows: numpy.array([.25] * len(rows)))).rank(beliefs, [_probe()], 1)
    assert refused.value.code == codes.INPUT and "numpy.ndarray" in str(refused.value)
    for wrong in (lambda rows: (x for x in [.1] * len(rows)), lambda rows: [.1, .2], lambda rows: [math.inf],
                  lambda rows: [[.1, .2]]):
        with pytest.raises((ValueError, TypeError)):
            MLAcquisitionSelector(Scores(wrong)).rank(beliefs, [_probe()], 1)


# --- The planner's and the orchestrator's selector -----------------------------------------------------------------

class _Swapping(MetacognitiveMethodSelector):
    """A selector whose decision names an op other than the variant it was given says."""

    def select(self, context, variants, **kwargs):
        decision = super().select(context, variants, **kwargs)
        rows = tuple({**row, "op": "check_propositional"} for row in decision.ordered_variants)
        return replace(decision, ordered_variants=rows)


class _Plain(MetacognitiveMethodSelector):
    def select(self, context, variants, **kwargs):
        return {"ordered_variants": [], "missing": []}


def _compile(selector, case3):
    from g6.cognitive_bridge import CognitiveAssuranceOrchestrator

    return CognitiveAssuranceOrchestrator(selector).compile(
        claim=ClaimIR("claim", "A entails A", ("formal",), {}),
        context=ProblemSignature("review", "logic", "validity", 1, 2), standard=case3["standard"],
        variants=[MethodVariant("v", "formal", "SMT", "formal", "check_validity", 1)],
        variant_models={"v": {"formula": "A", "premises": ["A"]}})


@pytest.mark.parametrize("selector", [_Swapping(), _Plain()], ids=["another-op", "not-a-decision"])
def test_a_selection_is_checked_against_the_variants_it_was_given(selector, case3):
    with pytest.raises(ValueError):
        _compile(selector, case3)
    planner = MetacognitivePlanner(selector)
    with pytest.raises(ValueError):
        planner.plan([ClaimIR("claim", "A entails A", ("formal",), {"formal": {"formula": "A"}})],
                     ProblemSignature("review", "logic", "validity", 1, 2),
                     [MethodVariant("v", "formal", "SMT", "formal", "check_validity", 1)], required_methods=("formal",))


# --- The orchestrator's evaluator and conflict gate ----------------------------------------------------------------

def _assess(case3, **kw):
    run = case3["orchestrator"].execute(case3["compiled"], approved=True)
    return case3["orchestrator"].assess(
        case3["compiled"], standard=case3["standard"], run_result=run, verifiers=case3["verifiers"],
        artifacts=case3["artifacts"], documentation=case3["documentation"],
        candidate_actor=case3["candidate_actor"], **{"evaluator": case3["evaluator"], **kw})


def test_an_evaluator_that_does_not_return_a_dossier_is_refused(case3):
    class NoDossier(type(case3["evaluator"])):
        def evaluate(self, **kwargs):
            return {"completion_state": "qualified-draft"}

    with pytest.raises(ValueError):
        _assess(case3, evaluator=NoDossier(evaluator_id="outside"))


def test_a_conflict_report_is_used_as_it_was_returned(case3):
    pin = "a" * 64
    assertion = ScopedAssertion("claim", "truth", pin, "supports", "formal", "source1", "sig1")

    class Gate(CrossMethodConflictGate):
        def reconcile(self, assertions):
            self.report = {"completion_state": "qualified-draft", "conflicts": 0}
            return self.report

    gate = Gate()
    out = _assess(case3, scoped_assertions=[assertion], epistemic_gate=gate)
    gate.report["conflicts"] = 7  # rewritten after it was returned
    assert out["epistemic_reconciliation"]["conflicts"] == 0, "the result is a live view of the gate's report"
    with pytest.raises(ValueError):
        class NotAReport(CrossMethodConflictGate):
            def reconcile(self, assertions):
                return ["not", "a", "report"]
        _assess(case3, scoped_assertions=[assertion], epistemic_gate=NotAReport())


# --- The verdict callbacks: each receives a copy, so what it admits is what is counted ------------------------------

def test_a_pair_admission_cannot_rewrite_the_pairs_it_admits(passing_adaptation):
    gate, change, _, controls = passing_adaptation
    ties = tuple(replace(pair, adapted_passed=False) for pair in passing_adaptation[2])

    def admit(pair):
        object.__setattr__(pair, "adapted_passed", True)  # what it was handed is rewritten, then admitted
        return True

    gate.admit_pair = admit
    out = gate.assess(change, ties, controls, detected_failures=change.mitigates)
    assert out["completion_state"] == "blocked-escalated" and out["wins"] == 0, f"counted rewritten pairs: {out}"


def test_a_trial_admission_cannot_rewrite_the_trials_it_admits():
    variant = MethodVariant("v", "formal", "SMT", "formal", "check_validity", 1)
    trials = [EvaluatedTrial(f"t{i}", f"case{i}", "cohort", "d", "u", "k", "v", False, 1.0, "trusted://e", "b" * 64,
                             variant_digest=variant.definition_digest) for i in range(4)]

    def admit(trial):
        object.__setattr__(trial, "resolved", True)
        return True

    selector = MetacognitiveMethodSelector(trials, admit_trial=admit)
    row = selector.select(ProblemSignature("d", "u", "k", 1, 2), [variant], required_methods=["formal"]
                          ).ordered_variants[0]
    assert row["historical_resolutions"] == 0, f"counted rewritten trials: {row}"


def test_an_assertion_admission_cannot_rewrite_the_assertions_it_admits():
    pin = "a" * 64
    rows = [ScopedAssertion("c", "truth", pin, "supports", "formal", "source1", "sig1"),
            ScopedAssertion("c", "truth", pin, "refutes", "causal", "source2", "sig2")]

    def admit(row):
        object.__setattr__(row, "stance", "supports")
        return True

    out = CrossMethodConflictGate(admit_assertion=admit).reconcile(rows)
    assert out["completion_state"] == "blocked-escalated" and out["conflicts"] == 1, f"reconciled rewritten rows: {out}"


# --- The black box a surrogate is distilled from (found by the inventory) ------------------------------------------

def _fake_sklearn(monkeypatch, fitted):
    """A stand-in for scikit-learn's tree estimators: it records what it is fitted on and fits one leaf."""
    tree_module = types.ModuleType("sklearn.tree")

    class Tree:
        def __init__(self, **kwargs):
            pass

        def fit(self, X, y):
            fitted.append((list(X), list(y)))
            self.n_features_in_, self.n_outputs_, self.classes_ = len(X[0]), 1, [0, 1]
            self.tree_ = SimpleNamespace(children_left=[-1], children_right=[-1], feature=[-2], threshold=[-2.0],
                                         value=[[[1.0, 0.0]]])
            return self

    tree_module.DecisionTreeClassifier = tree_module.DecisionTreeRegressor = Tree
    package = types.ModuleType("sklearn")
    package.tree = tree_module
    monkeypatch.setitem(sys.modules, "sklearn", package)
    monkeypatch.setitem(sys.modules, "sklearn.tree", tree_module)


def test_a_black_box_cannot_add_the_held_out_rows_to_the_training_rows(monkeypatch):
    fitted = []
    _fake_sklearn(monkeypatch, fitted)
    rows = [[float(i), float(i % 3)] for i in range(20)]
    train, holdout = rows[:12], rows[12:]

    class Leaky:
        def predict(self, X):
            if len(X) == len(train):
                try:
                    X.extend(tuple(row) for row in holdout)  # the leakage check already ran
                except AttributeError:
                    pass
            return [0] * len(X)

    try:
        distill_blackbox(Leaky(), train, holdout, ["x", "y"])
    except (TypeError, ValueError):
        return
    trained = {tuple(row) for row in fitted[0][0]}
    assert not trained & {tuple(row) for row in holdout}, "the surrogate was fitted on held-out rows"
