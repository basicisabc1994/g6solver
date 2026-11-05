# SPDX-License-Identifier: Apache-2.0
"""The selector can learn from a study's records: a variant may be any named method (a solver engine op, a reviewer,
an arm), repeated trials of one variant on one case are grouped by case rather than dropped, and the splits it learns
from are an explicit parameter whose default is the PR's choice (held-out and shadow)."""
from __future__ import annotations

from dataclasses import replace

import pytest

from g6.cognitive_bridge import EvaluatedTrial, MetacognitiveMethodSelector, MethodVariant, ProblemSignature

PIN = "a" * 64


def context(budget: float = 10) -> ProblemSignature:
    return ProblemSignature("software_engineering", "bug_fix", "patch", 1, budget)


def trial(case: str, variant: str, *, resolved: bool, n: int = 0, split: str = "heldout") -> EvaluatedTrial:
    return EvaluatedTrial(f"{variant}-{case}-{n}", case, "frozen-suite-1", "software_engineering", "bug_fix", "patch",
                          variant, resolved, 1, "trusted://evaluator", PIN, split)


def test_a_variant_may_be_any_named_method():
    # 9ebfbbe accepted only the five solver engines in ENGINE_SPECS, so a reviewer or a study arm could not be ranked
    reviewer = MethodVariant("claude-review", "review", "diff", "claude_code", "review", 1, kind="reviewer")
    arm = MethodVariant("loop-arm", "repair", "patch", "g6_loop", "repair", 2, kind="arm")
    baseline = MethodVariant("plain-arm", "repair", "patch", "plain_agent", "repair", 1, kind="arm")
    trials = [trial(f"c{i}", "loop-arm", resolved=True) for i in range(6)] + \
             [trial(f"c{i}", "plain-arm", resolved=i < 2) for i in range(6)]
    selector = MetacognitiveMethodSelector(trials, admit_trial=lambda t: True)
    decision = selector.select(context(), [reviewer, arm, baseline], required_methods=["review", "repair"])
    assert decision.completion_state == "qualified-draft"
    assert [x["variant_id"] for x in decision.ordered_variants] == ["claude-review", "loop-arm"]
    assert {x["kind"] for x in decision.ordered_variants} == {"reviewer", "arm"}


def test_the_engine_specs_stay_one_kind_of_variant():
    assert MethodVariant("smt", "formal", "SMT", "formal", "check_validity", 2).kind == "engine"
    with pytest.raises(ValueError, match="arbitrarily substitute"):
        MethodVariant("escape", "formal", "SMT", "graph_reasoning", "explain", 0, kind="engine")
    with pytest.raises(ValueError, match="solver method"):  # only an engine variant meets a G6 solver method
        MethodVariant("review-as-formal", "formal", "SMT", "claude_code", "review", 0, kind="reviewer")
    with pytest.raises(ValueError, match="kind"):
        MethodVariant("blank", "review", "diff", "claude_code", "review", 0, kind="")


def test_repeated_trials_of_one_variant_on_one_case_are_grouped_by_case_not_dropped():
    # 9ebfbbe's admission key kept the first trial of a variant on a case and rejected the rest
    variant = MethodVariant("arm", "repair", "patch", "g6_loop", "repair", 1, kind="arm")
    repeats = [trial("c1", "arm", resolved=r, n=i) for i, r in enumerate((True, True, False))]
    selector = MetacognitiveMethodSelector([*repeats, trial("c2", "arm", resolved=True)], admit_trial=lambda t: True)
    assert selector.rejected_trials == 0 and len(selector.trials) == 4
    row = selector.select(context(), [variant], required_methods=["repair"]).ordered_variants[0]
    assert row["training_cases"] == 2                       # two cases, not four
    assert row["historical_resolutions"] == pytest.approx(2 / 3 + 1)
    assert row["repeated_trials"] == 2
    assert sorted(row["admitted_trial_ids"]) == sorted(t.trial_id for t in [*repeats, trial("c2", "arm", resolved=True)])
    # the same record twice is still one record
    again = MetacognitiveMethodSelector([repeats[0], repeats[0]], admit_trial=lambda t: True)
    assert again.rejected_trials == 1 and len(again.trials) == 1


def test_the_splits_it_learns_from_are_an_explicit_parameter():
    # 9ebfbbe hard-coded heldout and shadow; the default is still that choice
    records = [trial("c1", "arm", resolved=True, split="training"), trial("c2", "arm", resolved=True),
               trial("c3", "arm", resolved=True, split="shadow")]
    default = MetacognitiveMethodSelector(records, admit_trial=lambda t: True)
    assert default.learn_from == ("heldout", "shadow")
    assert default.rejected_trials == 1 and {t.split for t in default.trials} == {"heldout", "shadow"}
    training = MetacognitiveMethodSelector(records, admit_trial=lambda t: True, learn_from=("training",))
    assert [t.split for t in training.trials] == ["training"] and training.rejected_trials == 2
    variant = MethodVariant("arm", "repair", "patch", "g6_loop", "repair", 1, kind="arm")
    assert default.select(context(), [variant], required_methods=["repair"]).policy_digest != \
        training.select(context(), [variant], required_methods=["repair"]).policy_digest
    with pytest.raises(ValueError, match="learn_from"):
        MetacognitiveMethodSelector(records, learn_from=("everything",))
    with pytest.raises(ValueError, match="learn_from"):
        MetacognitiveMethodSelector(records, learn_from=())


def test_an_engine_variant_still_compiles_and_a_non_engine_one_is_never_executed():
    from g6.cognitive_bridge import ClaimIR, CognitiveAssuranceOrchestrator, Criterion, DomainStandard

    standard = DomainStandard("software_engineering", "v1", "AU", "rubric", PIN,
                              (Criterion("c1", "derivation", "checker1", "v1:1", required_methods=("formal",)),))
    engine = MethodVariant("smt", "formal", "SMT", "formal", "check_validity", 1)
    reviewer = MethodVariant("claude-review", "review", "diff", "claude_code", "review", 1, kind="reviewer")
    compiled = CognitiveAssuranceOrchestrator(MetacognitiveMethodSelector()).compile(
        claim=ClaimIR("claim-1", "entailment", ("formal",), {}),
        context=replace(context(), domain="software_engineering"), standard=standard, variants=[engine, reviewer],
        variant_models={"smt": {"formula": "B", "premises": ["(=> A B)", "A"], "sorts": {"A": "Bool", "B": "Bool"}}},
        max_supplemental=1)
    assert [s.component for s in compiled.executable_plan.steps] == ["formal"]
    assert {x["obligation"] for x in compiled.decision.ordered_variants if x["kind"] == "reviewer"} <= {"supplemental"}
