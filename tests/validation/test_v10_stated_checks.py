# SPDX-License-Identifier: Apache-2.0
"""V10's comparison of each search arm's stated-check results with arm A's (PROTOCOL.md on the verification search
branch, feasibility criterion 5), and the driver recording it before the labels. Fake results only."""
from __future__ import annotations

from patch_review_search import driver, stated_checks
from patch_review_utility import report_check as a5
from patch_review_utility.dispositions import ACCEPT, DEFECT, make

OK, NO = make(ACCEPT), make(DEFECT, "a check failed")


def row(patch, arm_a, a_checks, c=None, t=None, c_own=OK, t_own=OK):
    return {"patch": patch, "arm_a": arm_a, "arm_a_stated_checks": a_checks,
            "arms": {"C": {"own": c_own, "stated_checks": c}, "T": {"own": t_own, "stated_checks": t}}}


def test_the_list_names_each_patch_whose_stated_check_results_differ_from_arm_as():
    rows = [row("p0", OK, {"a::t": "passed"}, {"a::t": "passed"}, {"a::t": "passed"}),
            row("p1", NO, {"a::t": "failed"}, {"a::t": "passed"}, {"a::t": "failed"}, t_own=NO),
            row("p2", NO, {"a::t": "passed", "b::t": "timed_out"}, {"a::t": "passed"}, {"a::t": "passed",
                                                                                          "b::t": "timed_out"},
                t_own=NO)]
    got = stated_checks.compare(rows)
    assert got["differ"] == {"C": ["p1", "p2"], "T": []}
    assert got["must_hold"] == {"C": ["p1", "p2"], "T": []} and got["not_on_list"] == {"C": [], "T": []}
    assert got["unread"] == {"C": [], "T": []} and got["holds"] is True


def test_a_patch_its_own_review_accepted_and_arm_a_did_not_off_the_list_means_the_rule_does_not_hold():
    rows = [row("p0", NO, {"a::t": "passed"}, {"a::t": "passed"}, {"a::t": "passed"}, t_own=NO)]
    got = stated_checks.compare(rows)
    assert got["must_hold"] == {"C": ["p0"], "T": []} and got["not_on_list"] == {"C": ["p0"], "T": []}
    assert got["holds"] is False


def test_results_that_cannot_be_read_leave_the_comparison_undecided():
    rows = [row("p0", OK, None, {"a::t": "passed"}, {"a::t": "passed"}),
            row("p1", OK, {"a::t": "passed"}, None, {"a::t": "passed"})]
    got = stated_checks.compare(rows)
    assert got["unread"] == {"C": ["p0", "p1"], "T": ["p0"]} and got["holds"] is None


def test_the_driver_reads_each_search_arms_stated_checks_from_its_report_and_records_the_comparison():
    def report(verdict):
        status = "passed_on_stated_checks" if verdict == "passed" else "failed"
        return {"assessment": {"requirements": [{"id": "R1", "text": "x", "status": status,
                                                 "reason": "ran", "checks": [{"id": "a::t", "verdict": verdict,
                                                                              "provenance": "stated"}]}],
                               "requirements_without_adequate_check": [], "specification_note": a5.SPECIFICATION_NOTE,
                               "completion": {"demonstrated": [], "not_established": [], "preservation": [],
                                              "defects_reproduced": {"stated_and_repository_checks": [],
                                                                     "proposed_checks": []},
                                              "test_adequacy": [], "open_questions": [], "activities": []}}}

    def arm(verdict):
        def run(patch, *, meter, **kwargs):
            return {"own": OK if verdict == "passed" else NO, "model_calls": 0, "report": report(verdict)}
        return run

    def e_arm(patch, *, meter, **kwargs):
        return {"disposition": OK, "findings": [], "calls": {"due": 1, "made": 0}}

    record = driver.Record()
    got = driver.study([{"id": "p0", "draw_index": 0, "arm_a": NO, "contract_ids": ["R1"],
                         "arm_a_stated_checks": {"a::t": "failed"}}], arm_c=arm("passed"), arm_t=arm("failed"),
                       arm_e=e_arm, spending=driver.Spending(300.0), record=record, rerun=lambda *args: {}, excluded=[])
    assert got["patches"][0]["arms"]["C"]["stated_checks"] == {"a::t": "passed"}
    assert got["comparison"]["differ"] == {"C": ["p0"], "T": []} and got["comparison"]["holds"] is True
    kinds = [entry["kind"] for entry in record.entries]
    assert kinds.count("comparison") == 1 and kinds.index("comparison") > max(
        i for i, kind in enumerate(kinds) if kind == "arm")
