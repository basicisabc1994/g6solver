# SPDX-License-Identifier: Apache-2.0
"""Review 2, findings 7 and 9 (untrusted_input_given_authority): credit binds what was evaluated. A paired trial
carries the candidate's and the baseline's digests, and the gate refuses a trial measured on another change (a control,
likewise, names the frozen suite it ran on); a run record carries the claim it executed, and an assessment refuses a
run for another claim."""
from __future__ import annotations

from dataclasses import replace

import pytest

from g6.cognitive_bridge import ControlTrial, PairedTrial


def test_finding_7_authenticated_pairs_cannot_credit_an_unevaluated_patch(passing_adaptation):
    gate, original, pairs, controls = passing_adaptation
    evaluated_patch = original.code_digest
    assert gate.assess(original, pairs, controls, detected_failures=original.mitigates)[
        "completion_state"] == "qualified-draft"
    other_patch = replace(original, id="never-evaluated", code_digest="d" * 64, baseline_digest="e" * 64)
    assert other_patch.code_digest != evaluated_patch
    result = gate.assess(other_patch, pairs, controls, detected_failures=other_patch.mitigates)
    assert result["completion_state"] == "blocked-escalated", (
        "authenticated results for another patch and baseline recommended the unevaluated candidate")


@pytest.mark.parametrize("change", ["candidate", "baseline", "both"])
def test_a_trial_measured_on_another_candidate_or_baseline_is_refused_even_when_admitted(passing_adaptation, change):
    gate, original, pairs, controls = passing_adaptation
    other = {"candidate": {"code_digest": "d" * 64}, "baseline": {"baseline_digest": "e" * 64},
             "both": {"code_digest": "d" * 64, "baseline_digest": "e" * 64}}[change]
    result = gate.assess(replace(original, **other), pairs, controls, detected_failures=original.mitigates)
    assert result["completion_state"] == "blocked-escalated"
    assert any("candidate and baseline" in why for why in result["blocked_reasons"]), result["blocked_reasons"]


def test_a_trial_that_names_no_candidate_or_baseline_credits_nothing(passing_adaptation):
    gate, original, pairs, controls = passing_adaptation
    unbound = tuple(replace(p, candidate_digest="", baseline_digest="") for p in pairs)
    gate.admit_pair = lambda x: x in unbound
    assert gate.assess(original, unbound, controls, detected_failures=original.mitigates)[
        "completion_state"] == "blocked-escalated"


@pytest.mark.parametrize("suite", ["", "f" * 64])
def test_a_control_credits_only_the_frozen_suite_it_names(passing_adaptation, suite):
    # The same class, found walking it: a control unlocks the recommendation too, so it names the suite it ran on.
    gate, original, pairs, controls = passing_adaptation
    elsewhere = tuple(replace(c, suite_digest=suite) for c in controls)
    gate.admit_control = lambda x: x in elsewhere
    out = gate.assess(original, pairs, elsewhere, detected_failures=original.mitigates)
    assert out["completion_state"] == "blocked-escalated"
    assert any("frozen suite" in why for why in out["blocked_reasons"]), out["blocked_reasons"]


def test_a_trials_digests_are_sha256_pins():
    with pytest.raises(ValueError):
        PairedTrial("1", "c" * 64, False, True, True, "holdout", "receipt", candidate_digest="not-a-digest")
    with pytest.raises(ValueError):
        ControlTrial("p", True, "positive", "receipt", suite_digest="not-a-digest")


def test_finding_9_run_cannot_be_relabelled_as_a_different_claim(executed_case):
    c = executed_case
    run = c["orchestrator"].execute(c["compiled"], approved=True)
    assert run["completion_state"] == "qualified-draft"
    forged = replace(c["compiled"], original_claim=replace(c["compiled"].original_claim,
                                                        id="unexecuted", statement="False is true"))
    artifact = replace(c["artifacts"]["c"], claim_id="unexecuted")
    result = c["orchestrator"].assess(
        forged, standard=c["standard"], run_result=run, evaluator=c["evaluator"],
        verifiers=c["verifiers"], artifacts={"c": artifact}, documentation=c["documentation"],
        candidate_actor=c["candidate_actor"])
    assert result["completion_state"] == "blocked-escalated", (
        f"claim unexecuted credited run for {result['method_results']['claim_id']}")


def test_a_run_is_credited_only_to_the_claim_it_ran_and_still_to_that_claim(executed_case):
    c = executed_case
    run = c["orchestrator"].execute(c["compiled"], approved=True)
    common = dict(standard=c["standard"], run_result=run, evaluator=c["evaluator"], verifiers=c["verifiers"],
                  artifacts=c["artifacts"], documentation=c["documentation"], candidate_actor=c["candidate_actor"])
    assert c["orchestrator"].assess(c["compiled"], **common)["completion_state"] == "qualified-draft"
    restated = replace(c["compiled"], original_claim=replace(c["compiled"].original_claim, statement="False is true"))
    out = c["orchestrator"].assess(restated, **common)
    assert out["completion_state"] == "blocked-escalated" and out["method_results"] == {}
