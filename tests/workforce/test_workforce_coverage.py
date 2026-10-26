# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Failure coverage (Phase E Part 10, E10c and E10d; FE §12, §13, §17, §19, §27, §28, P0, P4; TS §16-§17, §27-§28,
§32, §42-§43, STD-004, STD-010; production-path S3, S4, S6, S9).

Pinned (E10c): each step's failure surfaces, by the kind of work it does, read tested and passed, failed, or not
tested, and one never exercised is never marked passed; only G6's own test bench reports the test files unchanged;
each check's attempt keeps one FailureElicitationRecord on its close, ids, counts and codes only, never content.
Offline.
"""
from __future__ import annotations

import json

import pytest
from g6.workforce import acceptance as wa
from g6.workforce import contracts as wc

BENCH = ("workforce", "run_tests")


def _tests(result: str = "pass", *, surfaces=None, executed: bool = True, op=BENCH, measured=None) -> dict:
    return wa.mechanism_record(check="check-patch", judges="patch", artifact_id="ev_a", kind="test", op=op,
                               model_free=True, executed=executed, independent=True, result=result,
                               measured={"passed": 2, "total": 2} if measured is None else measured, surfaces=surfaces)


def _facts(**kw) -> wa.Facts:
    base = {"do_units": ("patch",), "outputs": {"patch": {"files": {}}}, "checks": {"patch": (_tests(),)},
            "approvals": {}, "sends": {}, "degradations": {}, "classes": {"patch": ("code",)}}
    return wa.Facts(**{**base, **kw})


def _found(facts: wa.Facts) -> dict:
    return {name: status for _, name, status in wa.surfaces_of(facts)}


# --- failure surfaces (Task 8) ---------------------------------------------------------------------------------------


def test_a_surface_never_exercised_reads_not_tested_never_a_check_mark():
    """The planted control (FE §12): with only a test run, the integrity, regression and edge-case surfaces read not
    tested; nothing marks them passed."""
    assert _found(_facts()) == {"original_bug_still_present": "passed", "regression_introduced": "not_tested",
                                "test_weakened": "not_tested", "edge_case_missed": "not_tested"}


@pytest.mark.parametrize(("record", "status"), [
    (_tests("fail"), "failed"),
    (_tests(executed=False, result="unmeasured"), "not_tested"),        # a check that did not run challenged nothing
    ({**_tests("fail"), "set_aside": True}, "not_tested"),             # nor did one a person set aside
])
def test_a_check_that_ran_says_how_it_came_out_and_one_that_did_not_says_nothing(record, status):
    assert _found(_facts(checks={"patch": (record,)}))["original_bug_still_present"] == status


def test_a_structural_protection_is_never_read_as_a_check_of_the_tests():
    """Founder ruling R-1 (2026-10-09), with its negative control: the bench refuses a patch that edits a test, so its
    byte comparison cannot fail on a delivered patch. It is reported, and it never makes the test_weakened surface read
    passed; only a check that can reject an introduced defect does."""
    assert not hasattr(wa, "integrity") and not hasattr(wa, "INTEGRITY_OPS")
    reported = _tests(measured={"passed": 2, "total": 2})  # what the rung records from a bench answer: no surfaces
    assert "surfaces" not in reported
    assert _found(_facts(checks={"patch": (reported,)}))["test_weakened"] == "not_tested"
    # a check that CAN reject a weakened test fills the surface, and a weakened test reads failed
    assert _found(_facts(checks={"patch": (_tests(surfaces={"test_weakened": "passed"}),)}))["test_weakened"] == "passed"
    assert _found(_facts(checks={"patch": (_tests(surfaces={"test_weakened": "failed"}),)}))["test_weakened"] == "failed"
    assert _found(_facts(checks={"patch": (_tests(surfaces={"made_up": "passed"}),)})) == {
        "original_bug_still_present": "passed", "regression_introduced": "not_tested", "test_weakened": "not_tested",
        "edge_case_missed": "not_tested"}  # an id outside the vocabulary is dropped


def test_a_code_change_checked_by_verify_task_is_challenged_by_its_tests_and_small_deliberate_changes():
    assert wa.code_surfaces(tests_ran=True, tests_good=True, mutations_tried=4, mutations_caught=3) == {
        "original_bug_still_present": "passed", "edge_case_missed": "failed"}
    assert wa.code_surfaces(mutations_tried=4, mutations_caught=4) == {"edge_case_missed": "passed"}
    assert wa.code_surfaces() == {}
    assert wa.code_surfaces(tests_weakened=True) == {"test_weakened": "failed"}  # E10a's hook


def test_a_test_run_that_skipped_every_test_exercised_nothing():
    """Review of E10c, finding 1: a run in which nothing passed and nothing failed says nothing about the problem, so its
    surface reads not tested, not passed. Controls: one test that passed, and one that failed."""
    skipped = _tests(measured={"passed": 0, "failed": 0, "total": 3})
    assert _found(_facts(checks={"patch": (skipped,)}))["original_bug_still_present"] == "not_tested"
    one_passed = _tests(measured={"passed": 1, "failed": 0, "total": 3})
    assert _found(_facts(checks={"patch": (one_passed,)}))["original_bug_still_present"] == "passed"
    failed = _tests("fail", measured={"passed": 0, "failed": 1, "total": 1})
    assert _found(_facts(checks={"patch": (failed,)}))["original_bug_still_present"] == "failed"
    record = wa.elicitation_record(unit="patch", artifact_id="ev_a", risk_tier="R2", classes=("code",), records=(skipped,),
                                   verdict="pass")
    assert "original_bug_still_present" in record["remaining_unchecked_surfaces"]
    assert record["elicitation_strategies"] == []


OLD = {"old_passed": 0, "old_failed": 1, "old_not_run": 0}


def test_the_original_tests_rerun_on_the_new_code_report_on_weakening_never_on_the_problem():
    """Review of E10c, finding 2: the old versions of the changed tests, run against the new code, are a check that can
    reject a weakened test. Their failure fills test_weakened; they never stand in for the tests of the problem."""
    old = _tests("fail", measured=OLD, surfaces={"test_weakened": "failed"})
    alone = _found(_facts(checks={"patch": (old,)}))
    assert alone["test_weakened"] == "failed" and alone["original_bug_still_present"] == "not_tested", alone
    both = _found(_facts(checks={"patch": (_tests(), old)}))
    assert both["original_bug_still_present"] == "passed" and both["test_weakened"] == "failed", both
    record = wa.elicitation_record(unit="patch", artifact_id="ev_a", risk_tier="R2", classes=("code",), records=(old,),
                                   verdict="fail")
    assert record["failures_confirmed"] == ["test_weakened"], record


def test_a_surface_that_failed_in_any_check_stays_failed_in_either_order():
    passed, failed = _tests(), _tests("fail")
    for checks in ((passed, failed), (failed, passed)):
        assert _found(_facts(checks={"patch": checks}))["original_bug_still_present"] == "failed"


def test_a_check_that_counts_only_passed_and_total_still_counts_its_failures():
    record = wa.elicitation_record(unit="patch", artifact_id="ev_a", risk_tier="R2", classes=("code",),
                                   records=(_tests("fail", measured={"passed": 1, "total": 3}),), verdict="fail")
    assert record["counterexamples_found"] == 2


@pytest.mark.parametrize(("approvals", "sends", "found"), [
    ({"approve": "approve"}, ("sent", "sent"), {"approval_missing": "passed", "action_unconfirmed": "passed"}),
    # review of E10c, finding 4: a rejection is final, nothing went ahead, so the claim "it went ahead without approval"
    # is not made; if something did go ahead after a rejection (or with no decision at all), that is the failure
    ({"approve": "reject"}, (), {"approval_missing": "not_tested", "action_unconfirmed": "not_tested"}),
    ({"approve": "reject"}, ("sent",), {"approval_missing": "failed", "action_unconfirmed": "passed"}),
    ({}, ("sent",), {"approval_missing": "failed", "action_unconfirmed": "passed"}),
    ({}, ("refused",), {"approval_missing": "not_tested", "action_unconfirmed": "not_tested"}),  # shadow: nothing sent
    ({"approve": "approve"}, ("sent", "unknown"), {"approval_missing": "passed", "action_unconfirmed": "failed"}),
])
def test_a_step_that_acts_outside_is_challenged_on_its_approval_and_its_outcome(approvals, sends, found):
    facts = _facts(do_units=("send",), checks={}, classes={"send": ("external",)}, guards={"send": ("approve",)},
                   approvals=approvals, sends={"send": sends} if sends else {})
    assert _found(facts) == found


def test_general_work_names_no_surface_yet():
    assert wa.surfaces_of(_facts(classes={})) == ()


def test_a_receipt_check_that_failed_says_the_action_could_not_be_confirmed():
    record = wa.mechanism_record(check="receipts", judges="send", artifact_id="ev_a", kind="deterministic",
                                 op=("workforce", "check_receipts"), model_free=True, executed=True, independent=True,
                                 result="fail")
    assert record["mechanism"] == "receipt_validation"
    facts = _facts(do_units=("send",), checks={"send": (record,)}, classes={"send": ("external",)},
                   guards={"send": ("approve",)}, approvals={"approve": "approve"})
    assert _found(facts) == {"approval_missing": "passed", "action_unconfirmed": "failed"}




# --- the FailureElicitationRecord (Task 9) ---------------------------------------------------------------------------

SECRET = "line 3: `a < b` -> `a <= b` beside API_KEY=sk-live-123"


def test_the_record_keeps_ids_counts_and_codes_never_content():
    """S6 and ruling E10.16: whatever a check's record carries, the FailureElicitationRecord keeps the vocabulary's
    ids, counts and codes, never a probe's input or a counterexample, on any host."""
    records = (_tests("fail", measured={"passed": 1, "failed": 1, "total": 2, "mutations_tried": 4,
                                        "mutations_caught": 2, "note": SECRET},
                      surfaces={"edge_case_missed": "failed", SECRET: "failed"}),
               _tests(executed=False, result="unmeasured", op=("fake_tests", "run")))
    found = wa.elicitation_record(unit="patch", artifact_id="ev_a", risk_tier="R2", classes=("code",), records=records,
                                  verdict="fail")
    assert SECRET not in json.dumps(found) and "sk-live" not in json.dumps(found)
    assert set(found) == set(wa.ELICITATION_FIELDS) - {"cost", "latency_seconds"}  # the ledger adds those two
    assert found["failures_confirmed"] == ["edge_case_missed", "original_bug_still_present"]
    assert (found["counterexamples_found"], found["evidence_gaps"]) == (3, ["test_execution"])
    assert found["elicitation_strategies"] == ["mutation", "test_execution"]
    assert found["remaining_unchecked_surfaces"] == ["regression_introduced", "test_weakened"]
    assert (found["assumptions_challenged"], found["novel_failures"], found["false_alarms"]) == ([], [], [])
    allowed = (set(wa.SURFACE_IDS) | set(wa.STRATEGY_OF.values()) | set(wa.MECHANISMS) | set(wc.VERDICT_RESULTS)
               | {"patch", "ev_a", "R2", wa.STANDARD_POLICY})

    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)
        elif isinstance(value, dict):
            for item in value.values():
                yield from strings(item)

    assert set(strings(found)) <= allowed


def test_a_record_with_no_check_names_the_surfaces_it_was_to_challenge_and_none_it_did():
    found = wa.elicitation_record(unit="patch", artifact_id="ev_a", risk_tier="bogus", classes=("code",), records=(),
                                  verdict="maybe")
    assert found["risk_tier"] == "" and found["verdict"] == "unmeasured"  # a value outside the vocabulary is not kept
    assert found["known_failure_signatures_considered"] == list(wa.SURFACES["code"])
    assert found["remaining_unchecked_surfaces"] == sorted(wa.SURFACES["code"])
    assert (found["elicitation_strategies"], found["probes_executed"], found["counterexamples_found"]) == ([], [], 0)
