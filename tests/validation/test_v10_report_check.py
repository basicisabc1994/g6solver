# SPDX-License-Identifier: Apache-2.0
"""V10's report check of arms C and T (PROTOCOL.md on the verification search branch, "The report check", and
feasibility criterion 4). Fake reports only, shaped as G6's review writes them; no review is run."""
from __future__ import annotations

import copy

import pytest

from patch_review_search import driver, report_check
from patch_review_utility import report_check as a5
from patch_review_utility.dispositions import ACCEPT, ERROR, make

CHECK = "tests/test_calc.py::test_add"
COMPLETE = {"demonstrated": [{"requirement": "R1", "checks": [CHECK]}], "not_established": [],
            "defects_reproduced": {"stated_and_repository_checks": [], "proposed_checks": []}, "preservation": [],
            "test_adequacy": [], "open_questions": [], "activities": []}
FORMAL = {"specification": {"template": "state_invariant.v1"}, "model": {"verdict": "violation_found"},
          "correspondence": {"result": "reproduced"}}


def report(checks=None, completion=None, status="passed_on_stated_checks"):
    found = checks or [{"id": CHECK, "verdict": "passed", "provenance": "stated"}]
    requirement = {"id": "R1", "text": "Addition returns the sum.", "status": status, "reason": "its checks ran",
                   "checks": found}
    return {"assessment": {"requirements": [requirement],
                           "requirements_without_adequate_check": ["R1"] if status == "not_established" else [],
                           "specification_note": a5.SPECIFICATION_NOTE,
                           "completion": copy.deepcopy(COMPLETE) if completion is None else completion}}


def test_a_complete_report_passes_and_the_three_parts_are_v9s_own_check():
    got = report_check.check(report(), ["R1"])
    assert got == {"complete": True, "missing": [], "completion_problems": [], "model_originated_passing": [],
                   "formal_problems": []}
    wrong = report_check.check(report(), ["R1", "R2"])
    assert wrong["complete"] is False and wrong["missing"] == a5.check(report(), ["R1", "R2"])["missing"]


@pytest.mark.parametrize("change", ["drop demonstrated", "defects not a dict", "proposed checks gone", "no completion",
                                    "activities not a list"])
def test_the_completion_section_must_hold_every_part_the_protocol_names(change):
    completion = copy.deepcopy(COMPLETE)
    if change == "drop demonstrated":
        del completion["demonstrated"]
    elif change == "defects not a dict":
        completion["defects_reproduced"] = []
    elif change == "proposed checks gone":
        del completion["defects_reproduced"]["proposed_checks"]
    elif change == "activities not a list":
        completion["activities"] = "none"
    got = report_check.check(report(completion={} if change == "no completion" else completion), ["R1"])
    assert got["complete"] is False and "completion_section" in got["missing"] and got["completion_problems"]


@pytest.mark.parametrize("provenance", ["proposed", "adopted_proposal", "structural"])
def test_a_check_a_model_originated_listed_as_passing_a_requirement_fails_the_report(provenance):
    checks = [{"id": CHECK, "verdict": "passed", "provenance": "stated"},
              {"id": "tests/g6_check_0123456789ab/test_x.py", "verdict": "passed", "provenance": provenance}]
    got = report_check.check(report(checks=checks), ["R1"])
    assert got["complete"] is False
    assert got["model_originated_passing"] == [{"requirement": "R1", "check": "tests/g6_check_0123456789ab/test_x.py",
                                                "provenance": provenance}]
    unpassed = report_check.check(report(checks=checks, status="not_established"), ["R1"])
    assert unpassed["model_originated_passing"] == []          # only a requirement it passes counts


def test_every_formal_result_keeps_its_three_parts_and_only_a_reproduced_trace_is_a_finding():
    completion = copy.deepcopy(COMPLETE)
    completion["activities"] = [{"activity": "model check", "formal": FORMAL},
                                {"activity": "model check", "formal": {"model": {}, "correspondence": {}}}]
    completion["defects_reproduced"]["proposed_checks"] = [
        {"check": "trace-1", "formal": FORMAL},
        {"check": "trace-2", "formal": {**FORMAL, "correspondence": {"result": "not_reproduced", "reason": "model_only"}}}]
    got = report_check.check(report(completion=completion), ["R1"])
    assert got["complete"] is False and len(got["formal_problems"]) == 2
    assert "three parts" in got["formal_problems"][0] and "trace-2" in got["formal_problems"][1]


def test_the_driver_checks_each_search_arms_report_and_a_failed_one_is_a_tool_error_for_the_patch():
    bad = report(checks=[{"id": CHECK, "verdict": "passed", "provenance": "proposed"}])

    def arm(report_given):
        def run(patch, *, meter, **kwargs):
            meter.add(cents=1.0)
            return {"own": make(ACCEPT), "model_calls": 1, "spent_cents": 1.0, "report": report_given}
        return run

    def e_arm(patch, *, meter, **kwargs):
        return {"disposition": make(ACCEPT), "findings": [], "calls": {"due": 1, "made": 0}}

    got = driver.study([{"id": "p0", "draw_index": 0, "arm_a": make(ACCEPT), "contract_ids": ["R1"]}],
                       arm_c=arm(bad), arm_t=arm(report()), arm_e=e_arm, spending=driver.Spending(300.0),
                       record=driver.Record(), rerun=lambda *args: {}, excluded=[])
    c, t = got["patches"][0]["arms"]["C"], got["patches"][0]["arms"]["T"]
    assert c["own"]["category"] == ERROR and c["own"]["error_kind"] == driver.REPORT_CHECK
    assert c["disposition"]["error_kind"] == driver.REPORT_CHECK and c["report_check"]["model_originated_passing"]
    assert t["disposition"]["accepted"] is True and t["report_check"]["complete"] is True
    unreported = driver.study([{"id": "p0", "draw_index": 0, "arm_a": make(ACCEPT)}], arm_c=arm(None),
                              arm_t=arm(None), arm_e=e_arm, spending=driver.Spending(300.0), record=driver.Record(),
                              rerun=lambda *args: {}, excluded=[])
    assert unreported["patches"][0]["arms"]["C"]["report_check"] is None
