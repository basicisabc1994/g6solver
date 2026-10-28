# SPDX-License-Identifier: Apache-2.0
"""V9's statistics (PROTOCOL.md, "Analysis and hypotheses for Experiment 1", "Verdicts", "Design of Experiment 2").

Development fixtures only: every row here is made up for the test; none is V9 evaluation data."""
from __future__ import annotations

import hashlib
import math
import random

import pytest

from patch_review_utility import stats

COMMIT = "0123456789abcdef0123456789abcdef01234567"


def disposition(accepted: bool, category: str = "") -> dict:
    return {"accepted": accepted, "category": category or ("accept" if accepted else "defect_found"), "error_kind": ""}


def make_row(task, repository, label, *, tests, self_review, g6, direct, patch="non_empty", categories=()):
    return {"task_id": task, "repository": repository, "patch": patch, "label": label,
            "defect_categories": list(categories), "oracle_controls_passed": True,
            "arms": {"tests": tests, "self_review": self_review, "g6": g6, "direct_checks": direct}}


def rows_of(table):
    """``table``: (repository, label, tests, self_review, g6, direct) per task, each arm as accepted or not."""
    return [make_row(f"t{i}", repository, label, tests=disposition(a), self_review=disposition(b), g6=disposition(c),
                     direct=disposition(d))
            for i, (repository, label, a, b, c, d) in enumerate(table)]


# --- the seed ----------------------------------------------------------------------------------------------------------

def test_the_seed_is_the_first_sixteen_hex_digits_of_the_level_text_and_needs_a_full_commit():
    expected = int(hashlib.sha256(f"V9:feasibility:{COMMIT}".encode()).hexdigest()[:16], 16)
    assert stats.seed("feasibility", COMMIT) == expected
    assert stats.seed("confirmatory", COMMIT) != expected
    for level, commit in (("feasibility", COMMIT[:39]), ("feasibility", COMMIT.upper()), ("pilot", COMMIT)):
        with pytest.raises(ValueError):
            stats.seed(level, commit)


# --- intervals ---------------------------------------------------------------------------------------------------------

def test_the_wilson_interval_matches_published_values():
    low, high = stats.wilson(0, 10, z=1.959964)
    assert low == 0 and high == pytest.approx(0.2775, abs=1e-4)
    low, high = stats.wilson(5, 10, z=1.959964)
    assert (low, high) == (pytest.approx(0.2366, abs=1e-4), pytest.approx(0.7634, abs=1e-4))


def test_newcombe_paired_interval_reduces_to_the_square_and_add_form_without_correlation():
    # a = b = c = d = 5: the correlation is zero, so each limit adds the two Wilson distances in quadrature
    low, high = stats.newcombe_paired(5, 5, 5, 5)
    l1, u1 = stats.wilson(10, 20)
    assert low == pytest.approx(-math.hypot(0.5 - l1, u1 - 0.5)) and high == pytest.approx(math.hypot(u1 - 0.5,
                                                                                                         0.5 - l1))
    low, high = stats.newcombe_paired(0, 0, 0, 7)       # all concordant: symmetric about zero, Wilson's width
    assert low == -high and high == pytest.approx(stats.wilson(0, 7)[1])
    swapped = stats.newcombe_paired(3, 1, 6, 10)
    assert stats.newcombe_paired(3, 6, 1, 10) == (pytest.approx(-swapped[1]), pytest.approx(-swapped[0]))


def test_newcombe_paired_interval_covers_about_ninety_percent_on_correlated_pairs():
    generator, covered, trials, n = random.Random(20261028), 0, 1500, 80
    # both arms accept with probability 0.3 and 0.4, strongly correlated: the true difference is -0.1
    cells = [(0.25, "a"), (0.05, "b"), (0.15, "c"), (0.55, "d")]
    for _ in range(trials):
        counts = {"a": 0, "b": 0, "c": 0, "d": 0}
        for _ in range(n):
            draw, total = generator.random(), 0.0
            for share, cell in cells:
                total += share
                if draw < total:
                    counts[cell] += 1
                    break
            else:
                counts["d"] += 1
        low, high = stats.newcombe_paired(counts["a"], counts["b"], counts["c"], counts["d"])
        covered += low <= -0.1 <= high
    assert 0.86 <= covered / trials <= 0.95


def test_the_bootstrap_resamples_within_each_repository_and_is_fixed_by_its_seed():
    pairs = [("r1", True, False)] * 6 + [("r1", False, False)] * 4 + [("r2", True, True)] * 5
    one, two = stats.stratified_bootstrap(pairs, seed=7), stats.stratified_bootstrap(pairs, seed=7)
    assert one == two and one != stats.stratified_bootstrap(pairs, seed=8)
    assert one[0] < 0.4 < one[1]                        # the observed difference is 6/15
    assert stats.stratified_bootstrap([("r", True, True)] * 4, seed=1) == (0.0, 0.0)
    assert stats.stratified_bootstrap([("r", True, False)] * 4 + [("s", True, False)], seed=1) == (1.0, 1.0)
    assert stats.stratified_bootstrap([], seed=1) is None


#: What V9's bootstrap gave on these inputs before it took a confidence (commit 7ee1fb4), 2,000 resamples each.
BEFORE_CONFIDENCE = (
    ([("r1", True, False)] * 6 + [("r1", False, False)] * 4 + [("r2", True, True)] * 5, 7, (0.2, 0.5333333333333333)),
    ([("a", i % 3 == 0, i % 2 == 0) for i in range(23)] + [("b", i % 5 == 0, False) for i in range(17)], 11,
     (-0.15, 0.15)),
    ([("x", True, False), ("x", False, True), ("y", True, True), ("y", False, False), ("z", True, False)] * 4,
     20261029, (0.0, 0.4)))


@pytest.mark.parametrize("pairs, seed, before", BEFORE_CONFIDENCE)
def test_v9s_90_percent_bootstrap_is_what_it_was_before_it_took_a_confidence(pairs, seed, before):
    assert stats.stratified_bootstrap(pairs, seed=seed, resamples=2000) == before


def test_the_bootstrap_gives_a_95_percent_interval_when_asked_and_v9s_90_percent_one_otherwise():
    pairs = [("r1", True, False)] * 6 + [("r1", False, False)] * 4 + [("r2", True, True)] * 5
    ninety = stats.stratified_bootstrap(pairs, seed=7)
    assert stats.stratified_bootstrap(pairs, seed=7, confidence=0.90) == ninety
    low, high = stats.stratified_bootstrap(pairs, seed=7, confidence=0.95)
    assert low <= ninety[0] and high >= ninety[1] and (low, high) != ninety
    with pytest.raises(ValueError):
        stats.stratified_bootstrap(pairs, seed=7, confidence=1.0)


def test_mcnemar_exact_and_clopper_pearson():
    assert stats.mcnemar_exact(0, 0) == 1.0 and stats.mcnemar_exact(1, 1) == 1.0
    assert stats.mcnemar_exact(5, 0) == pytest.approx(0.0625) and stats.mcnemar_exact(0, 5) == pytest.approx(0.0625)
    assert stats.clopper_pearson_upper(0, 10) == pytest.approx(1 - 0.05 ** (1 / 10))
    assert stats.clopper_pearson_upper(10, 10) == 1.0
    upper = stats.clopper_pearson_upper(3, 40)
    assert stats.binomial_cdf(3, 40, upper) == pytest.approx(0.05, abs=1e-9)


# --- decisions ---------------------------------------------------------------------------------------------------------

def test_each_hypothesis_is_met_not_met_or_inconclusive_on_its_interval():
    assert stats.decide(-0.10, 0.05, 0.05) == "met"
    assert stats.decide(0.0500001, 0.2, 0.05) == "not met"
    assert stats.decide(0.05, 0.2, 0.05) == "inconclusive"
    assert stats.decide(-0.02, 0.06, 0.05) == "inconclusive"
    assert stats.experiment_1(["met", "met", "met"]) == "met"
    assert stats.experiment_1(["met", "not met", "inconclusive"]) == "not met"
    assert stats.experiment_1(["met", "inconclusive", "met"]) == "inconclusive"


def test_the_primary_analysis_pairs_by_patch_and_uses_the_wider_of_the_two_intervals():
    table = ([("r1", "defective", False, True, False, False)] * 6 + [("r2", "defective", False, False, False, True)] * 6
             + [("r1", "correct", True, True, True, True)] * 8 + [("r2", "correct", True, False, True, True)] * 8)
    got = stats.analyse(rows_of(table), seed=11)
    h1, h2, h3 = got["hypotheses"]
    assert (h1["name"], h1["difference"], h1["margin"]) == ("H1", 0.0, 0.05)
    assert h2["difference"] == pytest.approx(-0.5) and h2["discordant"] == {"x_only": 0, "y_only": 6}
    assert h3["difference"] == 0.0 and h3["margin"] == 0.15
    for h in (h1, h2, h3):
        low, high = h["interval"]
        assert low == min(h["newcombe"][0], h["bootstrap"][0]) and high == max(h["newcombe"][1], h["bootstrap"][1])
        assert h["result"] == stats.decide(low, high, h["margin"])
    assert got["rates"]["g6"]["false_acceptance"] == {"accepted": 0, "of": 12, "rate": 0.0}
    assert got["rates"]["tests"]["correct_acceptance"] == {"accepted": 16, "of": 16, "rate": 1.0}
    assert got["per_repository"]["r2"]["self_review"]["correct_acceptance"]["rate"] == 0.0
    assert [h["name"] for h in got["secondary_self_review"]] == ["H1", "H2", "H3"]
    assert all("result" not in h for h in got["secondary_self_review"])
    assert got["experiment_1"] == stats.experiment_1([h["result"] for h in (h1, h2, h3)])


def test_tallies_keep_every_kind_of_not_accepted_and_the_sensitivity_counts_empty_patches_as_defective():
    rows = rows_of([("r1", "defective", False, False, True, False), ("r1", "correct", True, False, False, True)])
    rows[1]["arms"]["g6"] = {"accepted": False, "category": "insufficient_evidence", "error_kind": ""}
    rows[1]["arms"]["self_review"] = {"accepted": False, "category": "tool_error_or_refusal", "error_kind": "refusal"}
    rows.append(make_row("t9", "r1", None, tests=None, self_review=None, g6=None, direct=None, patch="empty"))
    got = stats.analyse(rows, seed=3)
    assert got["counts"] == {"tasks": 3, "non_empty": 2, "empty": 1, "not_applying": 0, "unlabelled": 0,
                             "correct": 1, "defective": 1}
    assert got["tallies"]["self_review"]["tool_error_or_refusal"] == 1
    assert got["tallies"]["self_review"]["error_kinds"] == {"refusal": 1}
    assert got["correct_not_accepted"]["g6"] == {"insufficient_evidence": 1}
    sensitivity = got["sensitivity_empty_as_defective"]
    assert sensitivity["rates"]["g6"]["false_acceptance"] == {"accepted": 1, "of": 2, "rate": 0.5}
    assert [h["name"] for h in sensitivity["hypotheses"]] == ["H1", "H2"]


def test_an_unlabelled_patch_is_left_out_counted_and_counted_as_defective_in_the_sensitivity_analysis():
    rows = rows_of([("r1", "defective", False, False, False, False), ("r1", "correct", True, True, True, True),
                    ("r1", None, True, True, True, True)])
    rows[2]["label_detail"] = {"unlabelled_because": "an infrastructure error persisted after one retry"}
    got = stats.analyse(rows, seed=3)
    assert got["counts"]["unlabelled"] == 1 and got["unlabelled"] == ["t2"]
    assert got["rates"]["g6"]["false_acceptance"] == {"accepted": 0, "of": 1, "rate": 0.0}
    sensitivity = got["sensitivity_empty_as_defective"]
    assert sensitivity["counted_as_defective"] == 1
    # every arm reviewed it, so its acceptances count as false acceptances here (an empty patch has none)
    assert sensitivity["rates"]["g6"]["false_acceptance"] == {"accepted": 1, "of": 2, "rate": 0.5}


def test_g6_is_also_compared_with_the_agents_own_review_directly_with_no_verdict():
    table = [("r1", "defective", False, True, False, False)] * 4 + [("r1", "correct", True, True, False, True)] * 4
    got = stats.analyse(rows_of(table), seed=2)["secondary_g6_minus_self_review"]
    assert [(h["name"], h["x"], h["y"]) for h in got] == [("H1", "g6", "self_review"), ("H3", "self_review", "g6")]
    assert [h["difference"] for h in got] == [-1.0, 1.0]
    assert all("result" not in h and "margin" not in h for h in got)


def test_time_or_cap_reached_is_tallied_apart_whatever_the_category():
    rows = rows_of([("r1", "defective", False, False, False, False), ("r1", "correct", True, True, True, True)])
    rows[0]["arms"]["self_review"] = {"accepted": False, "category": "tool_error_or_refusal",
                                      "error_kind": "killed_at_time_limit", "time_or_cap_reached": True}
    rows[1]["arms"]["self_review"] = {"accepted": False, "category": "insufficient_evidence", "error_kind": "",
                                      "time_or_cap_reached": True}
    tally = stats.analyse(rows, seed=1)["tallies"]["self_review"]
    assert tally["time_or_cap_reached"] == {"tool_error_or_refusal": 1, "insufficient_evidence": 1}
    assert stats.feasibility(rows)["criteria"]["dispositions"]["self_review"]["tool_errors"] == 1


def test_detection_by_defect_category_counts_the_defective_patches_each_arm_did_not_accept():
    rows = rows_of([("r1", "defective", True, False, False, True), ("r1", "defective", False, False, True, True)])
    rows[0]["defect_categories"], rows[1]["defect_categories"] = ["fail_to_pass"], ["fail_to_pass", "specification"]
    got = stats.analyse(rows, seed=3)["detection_by_category"]
    assert got["fail_to_pass"]["g6"] == {"detected": 1, "of": 2}
    assert got["specification"]["tests"] == {"detected": 1, "of": 1}


# --- feasibility and the verdict ----------------------------------------------------------------------------------------

def test_feasibility_needs_dispositions_few_tool_errors_both_kinds_of_g6_result_and_the_oracle_controls():
    rows = rows_of([("r1", "defective", False, False, False, False), ("r1", "correct", True, True, True, True)])
    assert stats.feasibility(rows)["result"] == "feasible"
    no_negative = rows_of([("r1", "defective", False, False, True, False), ("r1", "correct", True, True, True, True)])
    assert stats.feasibility(no_negative)["criteria"]["g6_both_ways"] is False
    errors = [dict(r, arms={**r["arms"], "tests": {"accepted": False, "category": "tool_error_or_refusal",
                                                    "error_kind": "tool_error"}}) for r in rows]
    assert stats.feasibility(errors)["criteria"]["dispositions"]["tests"]["tool_error_rate"] == 1.0
    assert stats.feasibility(errors)["result"] == "not feasible"
    missing = [dict(rows[0], arms={**rows[0]["arms"], "g6": None}), rows[1]]
    assert stats.feasibility(missing)["result"] == "not feasible"
    oracle = [dict(rows[0], oracle_controls_passed=False), rows[1]]
    assert stats.feasibility(oracle)["criteria"]["oracle_controls"] is False


def test_the_verdict_is_the_first_that_applies():
    met = [{"name": n, "result": "met"} for n in ("H1", "H2", "H3")]
    audit = {"audited": 40, "label_errors": 0}
    base = dict(level="confirmatory", contaminated=False, audit=audit, registered_size=100, defective=120,
                correct=130, hypotheses=met)
    assert stats.verdict(**base)["verdict"] == "component_validated_no_transfer"
    assert stats.verdict(**{**base, "contaminated": True})["verdict"] == "contaminated_evaluation"
    assert stats.verdict(**{**base, "audit": None})["verdict"] == "oracle_inadequate"
    assert stats.verdict(**{**base, "audit": {"audited": 40, "label_errors": 4}})["verdict"] == "oracle_inadequate"
    assert stats.verdict(**{**base, "defective": 99})["verdict"] == "insufficient_failure_opportunities"
    unsafe = [{"name": "H1", "result": "not met"}, *met[1:]]
    got = stats.verdict(**{**base, "hypotheses": unsafe})
    assert (got["verdict"], got["which"]) == ("unsafe_or_regressive", ["unsafe"])
    assert got["label_error_bound"] == pytest.approx(1 - 0.05 ** (1 / 40))
    regressive = [*met[:2], {"name": "H3", "result": "not met"}]
    assert stats.verdict(**{**base, "hypotheses": regressive})["which"] == ["regressive"]
    assert stats.verdict(**{**base, "correct": 99})["verdict"] == "inconclusive"
    feasibility = dict(base, level="feasibility", audit=None)
    assert stats.verdict(**feasibility)["verdict"] == "inconclusive"
    assert stats.verdict(**{**feasibility, "contaminated": True})["verdict"] == "contaminated_evaluation"
    assert stats.verdict(**{**feasibility, "audit": {"audited": 10, "label_errors": 5}})["verdict"] == \
        "oracle_inadequate"


# --- Experiment 2 -------------------------------------------------------------------------------------------------------

def reviews(reviewer, method, outcomes):
    """``outcomes``: (label, decision, minutes) per review."""
    return [{"reviewer": reviewer, "method": method, "patch_id": f"{reviewer}-{method}-{i}", "label": label,
             "decision": decision, "minutes": minutes} for i, (label, decision, minutes) in enumerate(outcomes)]


def test_experiment_2_uses_time_per_correct_disposition_and_the_safety_constraint():
    data = []
    for r in range(6):
        data += reviews(f"r{r}", "g6", [("correct", "merge", 5), ("defective", "do_not_merge", 5)])
        data += reviews(f"r{r}", "tests", [("correct", "merge", 10), ("defective", "do_not_merge", 10)])
    got = stats.experiment_2(data, seed=5)
    assert got["median_ratio"] == 0.5 and got["interval"] == [0.5, 0.5]
    assert got["safety"]["holds"] is True and got["result"] == "met"
    # a fast wrong decision adds time and no correct disposition; no decision at the cap counts as do not merge
    data += reviews("r9", "g6", [("defective", "merge", 1)]) + reviews("r9", "tests", [("correct", None, 45)])
    got = stats.experiment_2(data, seed=5)
    assert got["left_out"] == ["r9"] and got["decisions_at_cap"] == 1
    assert got["safety"] == {"holds": False, "merged_defective": {"g6": 1, "tests": 0},
                             "per_reviewer_difference": got["safety"]["per_reviewer_difference"]}
    assert got["result"] == "not met"


def test_experiment_2_ratio_is_infinite_without_a_correct_disposition_under_g6():
    data = (reviews("a", "g6", [("correct", "do_not_merge", 4)]) + reviews("a", "tests", [("correct", "merge", 4)])
            + reviews("b", "g6", [("correct", "merge", 2)]) + reviews("b", "tests", [("correct", "merge", 4)]))
    got = stats.experiment_2(data, seed=1)
    assert got["ratios"] == {"a": math.inf, "b": 0.5} and got["median_ratio"] == math.inf
    assert got["result"] == "inconclusive"
