# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""ECHA Checkpoint 1: the claim rule (spec 2.2) and ECHA-01, 02, 12 and 22.

A claim says what was established, on what, and what is still unknown, and its result is computed from the evidence,
never supplied by the person writing it. What must hold, each with a case that can fail:
- no usable oracle leaves the claim inconclusive, never supported (ECHA-01);
- a partial invariant that passes supports a claim about the cases it covered and nothing wider (ECHA-02);
- a check with no recorded negative control is a structural protection, not an active check (ECHA-12; ruling R-1);
- reviewer consensus is not independent evidence: one group counts once, and a review is a lead, not support (ECHA-22);
- an oracle whose error is not bounded leaves a claim about true correctness not identified.
"""
from __future__ import annotations

import pytest

from g6.research.echa import (
    FLAG,
    ClaimRecord,
    EvidenceRecord,
    Ledger,
    LedgerError,
    StandardRef,
    evaluate_claim,
)
from g6.research.echa.adapters import (
    evidence_from_mechanism_record,
    known_unknowns,
)

FX = "ab" * 32
STD = StandardRef.from_text("std-collateral", "A change must not alter a function it was not asked to alter.",
                            "owner-approved task document", ("p-keep",))


def ev(evidence_id: str, **over) -> EvidenceRecord:
    base = {"evidence_id": evidence_id, "predicate_id": "p-keep", "oracle_category": "INDEPENDENT_EXECUTABLE",
                "oracle_version": "probe-v1", "fixture_version": FX, "independent": True, "independence_group": "oracle-a",
                "independence_rationale": "held outside the adapting process", "result": "pass", "scope": "12 held-out inputs",
                "known_limitations": ("covers only the 12 inputs",), "source_ref": f"log:{evidence_id}",
                "standard_sha256": STD.standard_sha256, "negative_control_ref": "ctl", "purpose": "check",
                "oracle_error_bound": None}
    base.update(over)
    return EvidenceRecord(**base)


def control(evidence_id: str = "ctl", **over) -> EvidenceRecord:
    base = {"purpose": "negative_control", "result": "fail", "scope": "injected defect d1: helper h altered",
                "negative_control_ref": "", "known_limitations": ("one injected defect",)}
    base.update(over)
    return ev(evidence_id, **base)


@pytest.fixture
def led(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    ledger = Ledger()
    ledger.add_standard(STD)
    ledger.add_vault("v1", FX, files=1, roots=1)
    ledger.add_evidence(control())
    return ledger


def claim(claim_id: str = "c1", evidence_ids=("e1",), **over) -> ClaimRecord:
    base = {"claim_id": claim_id, "subject": "patch p1", "predicate_id": "p-keep", "scope": "the 12 held-out inputs",
                "breadth": "covered_cases", "estimand": "oracle_scoped", "population": "pilot task t1",
                "sample_unit": "one task", "provenance": "evaluator-only fixture fx-1", "assumptions": ("the oracle is correct",),
                "known_limitations": ("12 inputs only",), "versions": {"standard": "std-collateral", "oracle": "probe-v1"},
                "evidence_ids": tuple(evidence_ids), "standard_sha256": STD.standard_sha256, "requires_independent_groups": 1}
    base.update(over)
    return ClaimRecord(**base)


def verdict(ledger: Ledger, **over) -> dict:
    ledger.add_claim(claim(**over))
    return evaluate_claim(ledger, over.get("claim_id", "c1"))


# --- the claim cannot be written without what the rule requires -------------------------------------------------------


@pytest.mark.parametrize("over,needle", [
    ({"predicate_id": ""}, "predicate_id"),
    ({"scope": ""}, "scope"),
    ({"provenance": ""}, "provenance"),
    ({"assumptions": ()}, "assumptions"),
    ({"known_limitations": ()}, "known_limitations"),
    ({"versions": {}}, "versions"),
    ({"evidence_ids": ()}, "evidence_ids"),
    ({"breadth": "everything"}, "breadth"),
    ({"estimand": "truth"}, "estimand"),
    ({"requires_independent_groups": 0}, "requires_independent_groups"),
    ({"population": ""}, "population"),
    ({"sample_unit": ""}, "sample_unit"),
])
def test_a_claim_that_leaves_out_what_the_rule_requires_is_refused(over, needle) -> None:
    with pytest.raises(ValueError, match=needle):
        claim(**over)


def test_a_claim_carries_no_result_field_it_is_computed() -> None:
    assert "result" not in claim().to_dict()
    with pytest.raises(TypeError):
        ClaimRecord(**{**claim().to_dict(), "result": "supported"})


def test_a_claim_must_name_evidence_the_ledger_holds(led) -> None:
    with pytest.raises(LedgerError, match="missing"):
        led.add_claim(claim(evidence_ids=("missing",)))


# --- the verdict, case by case ----------------------------------------------------------------------------------------


def test_one_independent_pass_with_a_negative_control_supports_a_claim_about_the_cases_it_covered(led) -> None:
    led.add_evidence(ev("e1"))
    got = verdict(led)
    assert got["result"] == "supported"
    assert got["independent_groups"] == 1
    assert got["active_evidence"] == ["e1"]
    assert "covers only the 12 inputs" in got["limits"]
    assert any("covers only" in item for item in got["limits"])


def test_echa_01_no_usable_oracle_is_inconclusive_never_supported(led) -> None:
    led.add_evidence(ev("e1", oracle_category="NO_DISCRIMINATING_EVIDENCE", result="unknown", independent=False,
                        independence_group="", independence_rationale="", scope="no test reaches the helper",
                        negative_control_ref=""))
    got = verdict(led)
    assert got["result"] == "inconclusive"
    assert "no_active_evidence" in got["reasons"]
    assert got["active_evidence"] == []


def test_echa_01_an_unknown_result_is_not_counted_as_a_pass(led) -> None:
    led.add_evidence(ev("e1", result="unknown"))
    got = verdict(led)
    assert got["result"] == "inconclusive" and got["active_evidence"] == []


def test_echa_02_a_partial_invariant_that_passes_does_not_support_a_general_claim(led) -> None:
    led.add_evidence(ev("e1", oracle_category="PARTIAL_FALSIFIER", scope="idempotence on 30 inputs"))
    narrow = verdict(led, claim_id="narrow")
    assert narrow["result"] == "supported"
    wide = verdict(led, claim_id="wide", breadth="general", scope="the function is correct")
    assert wide["result"] == "inconclusive"
    assert "partial_evidence_cannot_support_a_general_claim" in wide["reasons"]


def test_a_formal_check_can_support_a_general_claim_but_only_relative_to_its_formalisation(led) -> None:
    led.add_evidence(ev("e1", oracle_category="FORMAL_CHECK", negative_control_ref="", scope="proof of lemma L"))
    got = verdict(led, breadth="general", scope="lemma L holds")
    assert got["result"] == "supported"
    assert any("formalisation" in item for item in got["limits"])


def test_an_independent_failure_refutes_the_claim_on_its_scope(led) -> None:
    led.add_evidence(ev("e1"))
    led.add_evidence(ev("e2", result="fail", scope="input 7"))
    got = verdict(led, evidence_ids=("e1", "e2"))
    assert got["result"] == "unsupported"
    assert got["refuting_evidence"] == ["e2"]


def test_a_failure_from_a_check_with_no_control_is_a_lead_not_a_refutation(led) -> None:
    led.add_evidence(ev("e1", result="fail", negative_control_ref=""))
    got = verdict(led)
    assert got["result"] == "inconclusive"
    assert got["refuting_evidence"] == [] and got["leads"] == ["e1"]


def test_echa_12_a_check_that_cannot_fail_is_a_structural_protection_not_an_active_check(led) -> None:
    # R-1: a byte comparison that cannot fail on a delivered patch has no negative control, so it supports nothing
    led.add_evidence(ev("e1", negative_control_ref="", scope="every test file compared byte for byte"))
    got = verdict(led)
    assert got["result"] == "inconclusive"
    assert got["structural"] == ["e1"] and got["active_evidence"] == []
    assert "not_an_active_check" in got["reasons"]


def test_echa_12_the_same_check_with_a_control_that_rejected_a_defect_is_active(led) -> None:
    led.add_evidence(ev("e1", negative_control_ref="ctl", scope="every test file compared byte for byte"))
    got = verdict(led)
    assert got["result"] == "supported" and got["structural"] == [] and got["active_evidence"] == ["e1"]


def test_echa_22_three_correlated_reviews_are_not_independent_agreement(led) -> None:
    for i in (1, 2, 3):
        led.add_evidence(ev(f"r{i}", oracle_category="HEURISTIC_REVIEW", independence_group="same-model-family",
                            negative_control_ref=""))
    got = verdict(led, evidence_ids=("r1", "r2", "r3"))
    assert got["result"] == "inconclusive"
    assert got["active_evidence"] == [] and got["independent_groups"] == 0
    assert "only_heuristic_evidence" in got["reasons"]


def test_echa_22_the_same_group_counts_once_however_many_records_it_wrote(led) -> None:
    led.add_evidence(ev("e1", independence_group="g1"))
    led.add_evidence(ev("e2", independence_group="g1", scope="another 12 inputs"))
    got = verdict(led, evidence_ids=("e1", "e2"), requires_independent_groups=2)
    assert got["independent_groups"] == 1
    assert got["result"] == "inconclusive" and "independence_not_met" in got["reasons"]
    led.add_evidence(control("ctl2", oracle_version="probe-v2"))
    led.add_evidence(ev("e3", independence_group="g2", oracle_version="probe-v2", negative_control_ref="ctl2",
                        scope="a second author's inputs"))
    two = verdict(led, claim_id="c2", evidence_ids=("e1", "e3"), requires_independent_groups=2)
    assert two["independent_groups"] == 2 and two["result"] == "supported"


def test_a_check_that_is_not_independent_supports_a_claim_but_adds_no_independent_group(led) -> None:
    led.add_evidence(ev("e1", oracle_category="PARTIAL_FALSIFIER", independent=False, independence_group="",
                        independence_rationale="the author's own test"))
    got = verdict(led)
    assert got["result"] == "inconclusive" and got["independent_groups"] == 0
    assert "independence_not_met" in got["reasons"]


def test_a_pass_about_another_predicate_or_standard_is_not_evidence_for_this_claim(led) -> None:
    other = StandardRef.from_text("std-other", "a different standard", "p", ("p-keep",))
    led.add_standard(other)
    led.add_evidence(ev("e1", standard_sha256=other.standard_sha256, negative_control_ref=""))
    got = verdict(led)
    assert got["result"] == "inconclusive" and "evidence_for_another_standard" in got["reasons"]
    led.add_evidence(ev("e2", predicate_id="p-other", negative_control_ref=""))
    got2 = verdict(led, claim_id="c2", evidence_ids=("e2",))
    assert got2["result"] == "inconclusive" and "evidence_for_another_predicate" in got2["reasons"]


# --- uncertainty: an unbounded oracle error is not assumed zero ------------------------------------------------------


def test_a_claim_about_true_correctness_is_not_identified_without_a_bound_on_the_oracle_error(led) -> None:
    led.add_evidence(ev("e1"))
    scoped = verdict(led)
    assert scoped["identification"] == "not_identified" and scoped["result"] == "supported"
    true = verdict(led, claim_id="c2", estimand="true_correctness")
    assert true["result"] == "inconclusive" and "oracle_error_not_bounded" in true["reasons"]
    led.add_evidence(ev("e2", oracle_error_bound=0.02, scope="bounded oracle on 12 inputs"))
    bounded = verdict(led, claim_id="c3", estimand="true_correctness", evidence_ids=("e2",))
    assert bounded["identification"] == "identified" and bounded["result"] == "supported"
    assert bounded["oracle_error_bound"] == pytest.approx(0.02)


def test_one_unbounded_oracle_among_bounded_ones_leaves_the_claim_not_identified(led) -> None:
    led.add_evidence(ev("e1", oracle_error_bound=0.02, independence_group="g1"))
    led.add_evidence(ev("e2", independence_group="g2", scope="a second oracle with no stated error"))
    got = verdict(led, evidence_ids=("e1", "e2"), estimand="true_correctness")
    assert got["identification"] == "not_identified" and got["oracle_error_bound"] is None
    assert got["result"] == "inconclusive" and "oracle_error_not_bounded" in got["reasons"]


def test_the_error_bounds_of_several_oracles_are_added_never_averaged(led) -> None:
    led.add_evidence(ev("e1", oracle_error_bound=0.02, independence_group="g1"))
    led.add_evidence(ev("e2", oracle_error_bound=0.03, independence_group="g2", scope="second oracle"))
    got = verdict(led, evidence_ids=("e1", "e2"), estimand="true_correctness")
    assert got["oracle_error_bound"] == pytest.approx(0.05)


# --- the adapter: what the existing mechanism records become ----------------------------------------------------------


def _mechanism(**over) -> dict:
    base = {"mechanism": "test_execution", "check": "the changed tests", "judges": "u1", "artifact_id": "a1",
            "op": "coder.run_tests", "kind": "test", "executed": True, "independent": False, "model_free": True,
            "result": "pass", "measured": {"passed": 3, "failed": 0}}
    base.update(over)
    return base


def _adapt(rec: dict, **over) -> EvidenceRecord:
    args = {"evidence_id": "m1", "predicate_id": "p-keep", "standard_sha256": STD.standard_sha256, "oracle_version": "v1",
                "fixture_version": FX, "source_ref": "run:1", "scope": "the caller's changed tests"}
    args.update(over)
    return evidence_from_mechanism_record(rec, **args)


def test_a_check_that_did_not_run_becomes_unknown_never_a_pass() -> None:
    got = _adapt(_mechanism(executed=False, result="pass"))
    assert got.result == "unknown" and got.oracle_category == "NO_DISCRIMINATING_EVIDENCE"


def test_a_result_the_check_could_not_decide_becomes_unknown() -> None:
    assert _adapt(_mechanism(result="unmeasured")).result == "unknown"
    assert _adapt(_mechanism(result="fail")).result == "fail"


def test_a_model_review_is_a_heuristic_and_a_human_review_too() -> None:
    assert _adapt(_mechanism(mechanism="independent_model_review", kind="model_critic", model_free=False,
                             independent=True), independence_group="m").oracle_category == "HEURISTIC_REVIEW"
    assert _adapt(_mechanism(mechanism="human_review", kind="human", model_free=False,
                             independent=True), independence_group="h").oracle_category == "HEURISTIC_REVIEW"


def test_a_check_that_involved_a_model_is_a_heuristic_review_however_it_is_labelled() -> None:
    got = _adapt(_mechanism(mechanism="deterministic_computation", model_free=False, independent=True),
                 independence_group="x")
    assert got.oracle_category == "HEURISTIC_REVIEW"


def test_a_proof_check_is_formal_and_an_independent_model_free_test_is_an_independent_executable() -> None:
    assert _adapt(_mechanism(mechanism="formal_proof_check", kind="formal", independent=True),
                  independence_group="f").oracle_category == "FORMAL_CHECK"
    assert _adapt(_mechanism(independent=True), independence_group="t").oracle_category == "INDEPENDENT_EXECUTABLE"


def test_an_author_controlled_test_is_never_adapted_as_an_independent_oracle() -> None:
    got = _adapt(_mechanism(independent=False))
    assert got.independent is False and got.oracle_category == "PARTIAL_FALSIFIER"


def test_a_not_independent_record_needs_no_group_and_an_independent_one_must_name_it() -> None:
    with pytest.raises(ValueError, match="independence_group"):
        _adapt(_mechanism(independent=True))


def test_the_unchecked_surfaces_and_gaps_of_an_elicitation_record_become_known_unknowns() -> None:
    rec = {"remaining_unchecked_surfaces": ["edge_case_missed"], "evidence_gaps": ["independent_model_review"]}
    assert known_unknowns(rec) == ("surface not tested: edge_case_missed", "no evidence from: independent_model_review")
    assert known_unknowns({}) == ()
