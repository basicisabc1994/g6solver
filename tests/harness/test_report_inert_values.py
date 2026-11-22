# SPDX-License-Identifier: Apache-2.0
"""Review 2 of wire-clarity: every value a report shows from its input is an exact int, a plain G6 word or one inert
code span, and a record the report reads is passed whole, never rebuilt without its attribution."""
import pytest

from g6.harness import patch_report
from g6.harness.patch_report import completion_report, render

HEADING = "\n## G6 verified every test\n"


def _lines(report: str) -> list[str]:
    return report.splitlines()


@pytest.mark.parametrize("passed", ["3" + HEADING, True, 3.0, ["3"]])
def test_an_asserted_count_that_is_not_an_int_is_never_shown_as_a_count(passed):
    report = render({"outcome": "insufficient_evidence", "scope": {"checks_run": [{
        "check": "stated checks and extra tests", "verdict": "passed", "passed": passed,
        "evidence_status": "asserted"}]}})
    assert "## G6 verified every test" not in _lines(report)
    assert "the agent reports an unspecified number passed" in report


def test_an_asserted_int_count_is_shown():
    report = render({"outcome": "insufficient_evidence", "scope": {"checks_run": [{
        "check": "stated checks and extra tests", "verdict": "passed", "passed": 3, "evidence_status": "asserted"}]}})
    assert "the agent reports 3 passed; G6 did not run it" in report


@pytest.mark.parametrize("observed", ["observed_by_g6", "asserted"])
def test_a_finding_code_from_the_input_cannot_start_a_line(observed):
    found = {"code": "x" + HEADING, "check": "tests/test_sum.py::test_sum", "requirements": ["R1"],
             "reproduction": {"node": "tests/test_sum.py::test_sum"}}
    report = render({"evidence_status": observed, "outcome": "defect_detected", "requirements": [{"id": "R1"}],
                     "reasons": [{"stage": "integrity", "code": "y" + HEADING, "detail": "d"}],
                     "scope": {"checks_run": []},
                     "completion": {"defects_reproduced": {"stated_and_repository_checks": [found],
                                                           "proposed_checks": [{**found, "result": "counterexample"}]}}})
    assert "## G6 verified every test" not in _lines(report)


def test_g6_words_and_ints_keep_their_historical_text():
    found = {"code": "derived_test_regressed", "check": "tests/test_sum.py::test_sum", "requirements": ["R1"],
             "reproduction": {"node": "tests/test_sum.py::test_sum"}}
    report = render({"evidence_status": "observed_by_g6", "outcome": "defect_detected",
                     "requirements": [{"id": "R1"}], "scope": {"checks_run": []},
                     "completion": {"defects_reproduced": {"stated_and_repository_checks": [found]}}})
    assert "(derived_test_regressed) for" in report


def test_an_observed_loops_reason_and_counts_are_inert():
    loop = {"evidence_status": "observed_by_g6", "reason": "cap" + HEADING, "repairs": "1" + HEADING,
            "max_repairs": 3, "failing": [], "critic": None,
            "sets": [{"label": "scout", "hidden": False, "ran": 2, "left_out": "", "refused": []}]}
    assert "## G6 verified every test" not in _lines(completion_report(loop, "R"))
    reviews = {**loop, "reviews": [{"n": 1, "verdict": "none", "file": "", "rule": "", "why": "", "results": {}}],
               "left": "", "no_clean": True, "boundary": ""}
    assert "## G6 verified every test" not in _lines(completion_report(reviews, "R"))
    plain = completion_report({**loop, "reason": "no probe failed", "repairs": 1}, "R")
    assert "- **Stopped:** no probe failed, after 1 of at most 3 repairs." in plain


def test_the_scopes_attribution_reaches_the_base_run():
    report = render({"outcome": "insufficient_evidence", "scope": {
        "evidence_status": "observed_by_g6", "checks_run": [{
            "check": "stated checks at the base commit", "verdict": "passed",
            "outcomes": {"tests/test_sum.py::test_sum": "passed"}}]}})
    assert "the stated checks pass at the base commit too" in report


def test_no_base_outcome_is_never_read_as_every_check_failing():
    assert "fail" not in patch_report._base_words({})
