# SPDX-License-Identifier: Apache-2.0
from g6.harness.patch_report import completion_report, render

import pytest


def test_a_report_attributes_asserted_test_counts():
    report = render({"outcome": "insufficient_evidence", "scope": {"checks_run": [
        {"check": "stated checks and extra tests", "verdict": "passed", "passed": 3,
         "evidence_status": "asserted"}]}})
    assert "the agent reports 3 passed; G6 did not run it" in report
    assert "tests passed" not in report.lower()


def test_a_report_preserves_observed_run_words():
    report = render({"outcome": "supported_within_scope", "scope": {"checks_run": [
        {"check": "stated checks and extra tests", "verdict": "passed", "passed": 3,
         "evidence_status": "observed_by_g6"}]}})
    assert "the stated checks: `passed`" in report


@pytest.mark.parametrize("section", ["headline", "demonstrated", "base", "proposed", "confirmed"])
def test_all_sections_attribute_assertions(section):
    assessment = {"outcome": "supported_within_scope", "evidence_status": "asserted", "completion": {},
                  "scope": {"checks_run": []}}
    if section in {"demonstrated", "base"}:
        assessment["completion"]["demonstrated"] = [{"requirement": "R1", "checks": ["tests/test_sum.py::test_sum"]}]
        assessment["scope"]["checks_run"] = [{"check": "stated checks at the base commit", "verdict": "passed",
            "outcomes": {"tests/test_sum.py::test_sum": "failed"}, "passed": 3}]
    elif section == "proposed":
        assessment["completion"]["activities"] = [{"activity": "proposed check", "check": "probe", "result": "passed"}]
    elif section == "confirmed":
        assessment["confirmed_expectations"] = {"expectations": [{"question": {}, "answer": {},
                                                                  "check": {"result": "passed"}}]}
    report = render(assessment)
    assert "G6 did not run it" in report
    assert "every requirement passed" not in report
    assert "passes on the patch" not in report
    assert "checks fail at the base" not in report


def test_missing_status_and_nested_data_do_not_establish_observation():
    report = render({"outcome": "supported_within_scope", "scope": {"checks_run": [
        {"check": "stated checks and extra tests", "verdict": "passed", "passed": 3,
         "data": {"evidence_status": "observed_by_g6"}}]}})
    assert "the agent reports 3 passed; G6 did not run it" in report
    assert "every requirement passed" not in report


def test_completion_loop_attributes_asserted_review_results():
    report = completion_report({"evidence_status": "asserted", "reason": "cap", "repairs": 1,
        "reviews": [{"n": 1, "file": "probe.py", "rule": "sum", "why": "counterexample",
                     "verdict": "defect", "results": {"probe.py": "failed"}},
                    {"n": 2, "file": "probe.py", "rule": "sum", "verdict": "clean",
                     "results": {"probe.py": "passed"}}]}, "")
    assert "G6 did not run it" in report
    assert "its test passes now" not in report


def test_mixed_report_does_not_claim_all_requirements_were_observed():
    report = render({"outcome": "supported_within_scope", "evidence_status": "observed_by_g6", "scope": {
        "checks_run": [{"check": "stated checks and extra tests", "verdict": "passed", "passed": 3,
                        "evidence_status": "asserted"}]}})
    assert "every requirement passed" not in report
    assert "the agent reports 3 passed; G6 did not run it" in report


def test_asserted_loop_keeps_each_reported_result_visible():
    report = completion_report({"reason": "cap", "repairs": 1, "reviews": [
        {"n": 1, "file": "probe.py", "rule": "sum", "verdict": "clean", "results": {"probe.py": "passed"}}]}, "")
    assert "probe.py" in report
    assert "sum" in report
    assert "the agent reports" in report


def test_asserted_run_with_some_passing_tests_does_not_hide_its_failure():
    report = render({"outcome": "defect_detected", "scope": {"checks_run": [
        {"check": "stated checks and extra tests", "verdict": "failed", "passed": 3, "failed": 1}]}})
    assert "reports `failed`" in report
    assert "G6 did not run it" in report


def test_report_producer_uses_only_the_bound_gates_matched_receipts():
    from g6.harness.patch_review import _report_evidence
    runs = [{"receipt": "real-id"}, {"receipt": "other-id", "evidence_status": "observed_by_g6"}]
    gate = {"receipts": [{"receipt": "real-id", "receipt_status": "matched"},
                         {"receipt": "other-id", "receipt_status": "artifact_mismatch"}]}
    assert [r["evidence_status"] for r in _report_evidence(runs, gate)] == ["observed_by_g6", "asserted"]
    assert _report_evidence([{"receipt": "", "evidence_status": "observed_by_g6"}], gate)[0]["evidence_status"] == "asserted"
    assert _report_evidence(runs, {})[0]["evidence_status"] == "asserted"


@pytest.mark.parametrize("ending,result,label", [
    ("fixed", "passed", "**Fixed:** review 1"),
    ("open", "failed", "**Open:** review 1"),
    ("untested", "did not run", "No clean review"),
])
def test_g6_review_loop_keeps_observed_findings_and_ending(ending, result, label):
    from g6.harness.completion_loop import _reviews_report

    first = {"n": 1, "file": "probe.py", "rule": "sum", "why": "counterexample",
             "verdict": "defect", "results": {"probe.py": "failed"}}
    reviews = ([first, {"n": 2, "file": "other.py", "verdict": "none",
                        "results": {"probe.py": result}}] if ending != "untested" else
               [{**first, "verdict": "failed", "error": "review stopped", "results": {}}])
    report = _reviews_report({"reviews": reviews, "rounds": []}, "a review failed", "failure", ending, "")
    assert label in report
    assert f"**Ending:** `{ending}`" in report
    assert "G6 did not run it" not in report
    if ending != "untested":
        assert "`sum`" in report


def test_g6_scout_loop_keeps_observed_probes_and_boundary(monkeypatch):
    from pathlib import Path
    from g6.harness import completion_loop

    monkeypatch.setattr(completion_loop, "_final_review", lambda *args: "THE REVIEW")
    report = completion_loop._report(Path("."), "sum", "base", {
        "rounds": [{"tree": None, "failing": ["probe.py::test_sum"]}], "ran": {"scout": 1},
        "late": "no earlier hook", "boundary": "synthetic-unit-fixture",
        "sets": {"scout": {"left_out": "", "refused": [], "when": completion_loop.BEFORE}},
    }, "cap")
    assert "1 still fail, of 1 probes that ran" in report
    assert "**Boundary:**" in report and "synthetic-unit-fixture" in report
    assert "G6 did not run it" not in report


def test_caller_assertions_stay_attributed_even_when_the_reason_claims_observation():
    report = completion_report({"evidence_status": "asserted", "ending": "fixed",
        "reason": "G6 ran it; observed_by_g6", "reviews": [{"n": 1, "rule": "sum", "file": "probe.py",
        "verdict": "none", "results": {"probe.py": "passed"}}]}, "")
    assert "The agent reports review results; G6 did not run it." in report
    assert "**Fixed:**" not in report


def test_internal_patch_review_observations_do_not_need_gate_receipts():
    from types import SimpleNamespace
    import time
    from g6.harness.patch_review import _record, _reply

    run = _record(1, "stated checks and extra tests", ["probe.py::test_sum"],
                  {"verdict": "passed", "passed": 1})
    reply = _reply(SimpleNamespace(base_commit="base", files=[]), [], [], [], [run], [], [], {}, {}, [],
                   "artifact", "sum", {}, time.monotonic())
    assessment = reply["assessment"]
    assert assessment["scope"]["checks_run"][0]["evidence_status"] == "observed_by_g6"
    assert assessment["report"].startswith("**Supported within its stated scope**")
    assert "the stated checks: `passed`" in assessment["report"]
