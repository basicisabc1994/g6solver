# SPDX-License-Identifier: Apache-2.0
"""Regression tests for audit 016 (navigator -> g6.navigator): the static plan check and the run that follows it.

Each finding test names the finding it pins and says what the archive did; the archive cannot run here (it needs the
monorepo), so its behaviour is stated from the audit's source reading. Every gate has a survival control that passes.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from returns.result import Failure

import g6.grounding
from g6.core.registry import get_registry
from g6.invariants import check_envelope
from g6.navigator import PlanReport, PlanResult, check_plan, run_plan
from g6.navigator import plan as nav_plan
from g6.workforce.capabilities import load_contracts

RUN_TESTS = {"component": "workforce", "op": "run_tests", "args": {"repo": "r", "tests": ["tests/test_a.py::test_a"]}}
IN_PLACE = {"component": "workforce", "op": "run_tests_in_place", "args": {"repo": "r", "tests": ["tests/t.py::test_t"]}}
CLAIM = {"component": "grounding", "op": "check_claim",
         "args": {"claim": "The sky is blue.", "sources": {"note": "The sky is blue."}}}


def _codes(report: PlanReport) -> list[str]:
    return [issue.code for issue in report.issues]


def _cleared(cls: str):
    return lambda component, op: cls


def _contracts(tmp_path: Path, component: str, op: str, side_effect: str) -> dict:
    body = {"component": component, "op": op, "required": {}, "probe_params": {}, "expect_field": "ok",
            "side_effect": side_effect, "check": "input_model"}
    (tmp_path / f"{component}.{op}.json").write_text(json.dumps(body), encoding="utf-8")
    return load_contracts(tmp_path)


# --- NAV-001: compose / validate_pipeline labelled any non-empty step list valid and verified ----------------------


def test_nav001_an_unknown_component_and_op_is_not_valid() -> None:
    # Archive: compose_pipeline([{"component": "no_such_component", "op": "no_such_op"}]) returned valid=True and
    # completion_state="verified" (composer.py:86-100): it checked only that both names were non-empty.
    report = check_plan([{"component": "no_such_component", "op": "no_such_op"}])
    assert report.valid is False
    assert [(issue.step, issue.code) for issue in report.issues] == [(0, "G6_E_UNKNOWN_COMPONENT")]
    assert report.completion_state == "blocked-escalated" and report.steps[0].exists is False


def test_nav001_an_op_its_component_does_not_have_is_not_valid() -> None:
    # Archive: the block's own test asserted valid is True for ctx_rag.search, an op with no contract and no census
    # row (tests/mvp/navigator/test_navigator_mcp.py:276-284). Here the component is real and the op is not.
    report = check_plan([{"component": "grounding", "op": "search", "args": {"query": "x"}}], authority=_cleared("A4"))
    assert _codes(report) == ["G6_E_NAV_UNKNOWN_OP"] and report.steps[0].exists is False
    assert "check_claim" in report.issues[0].message  # it says which ops the component does have


def test_nav001_args_are_judged_against_the_ops_contract() -> None:
    # Archive: a step's args were never read; no contract was looked up (composer.py:50-71).
    report = check_plan([{"component": "workforce", "op": "run_tests", "args": {"repo": 3, "extra": True}}])
    messages = " | ".join(issue.message for issue in report.issues)
    assert set(_codes(report)) == {"G6_E_INPUT_VALIDATION"}
    assert "missing required 'tests'" in messages and "unknown key(s) ['extra']" in messages
    assert "'repo' must be str" in messages


def test_nav001_survival_a_plan_of_real_ops_with_valid_args_is_a_qualified_draft() -> None:
    report = check_plan([RUN_TESTS, IN_PLACE], request_id="req-1", run_id="run-1")
    assert report.valid is True and report.issues == ()
    assert report.completion_state == "qualified-draft" and report.warning_card["code"] == "G6_E_NAV_STATIC_CHECK"
    assert [s.authority for s in report.steps] == ["A4", "A4"] and report.effect == "read"
    assert all(s.contract_sha256 for s in report.steps)
    assert check_envelope(report.to_dict()) == []


def test_nav001_no_report_or_run_can_be_verified() -> None:
    report = check_plan([RUN_TESTS])
    with pytest.raises(ValueError, match="never 'verified'"):
        PlanReport(report.steps, (), report.effect, "verified", report.warning_card, report.evidence)
    with pytest.raises(ValueError, match="never 'verified'"):
        PlanResult(report, (), "verified", report.warning_card, report.evidence)


def test_every_problem_is_reported_with_its_step() -> None:
    report = check_plan([RUN_TESTS, {"component": "no_such", "op": "x"}, {"component": "grounding", "op": "nope"}])
    assert [(issue.step, issue.code) for issue in report.issues] == [
        (1, "G6_E_UNKNOWN_COMPONENT"), (2, "G6_E_NAV_UNKNOWN_OP")]
    assert "1 more in issues" in report.warning_card["message"]


@pytest.mark.parametrize("steps", [[], None, "[]", [{}], ["step"], [{"component": "grounding"}],
                                   [{"component": "workforce", "op": "run_tests", "args": ["repo"]}]])
def test_a_malformed_plan_is_blocked_with_a_code(steps) -> None:
    report = check_plan(steps)
    assert report.valid is False and report.completion_state == "blocked-escalated"
    assert report.warning_card["code"].startswith("G6_E_NAV_")
    assert check_envelope(report.to_dict()) == []


# --- NAV-003: the effect budget ran on invented cluster effects ------------------------------------------------------


def test_nav003_a_steps_effect_is_its_contracts_declared_side_effect(tmp_path: Path) -> None:
    # Archive: compose took a step's effect from its component's hand-assigned cluster (composer.py:58-59);
    # payments_x402 fell back to "infrastructure" (auth=∅), so payments_x402_approve_payment passed a budget with
    # auth=[] and came back "verified". Its contract declares "irreversible".
    contracts = _contracts(tmp_path, "payments_x402", "payments_x402_approve_payment", "irreversible")
    step = {"component": "payments_x402", "op": "payments_x402_approve_payment"}
    report = check_plan([step], contracts=contracts, authority=_cleared("A4"), max_effect="read")
    assert report.steps[0].effect == "irreversible" and report.effect == "irreversible"
    assert _codes(report) == ["G6_E_NAV_EFFECT_OVER_BUDGET"]
    # survival: a budget that allows the declared effect admits the same step
    assert check_plan([step], contracts=contracts, authority=_cleared("A4"), max_effect="irreversible").valid


def test_nav003_an_op_with_no_contract_fails_every_budget() -> None:
    # Archive: an unmapped op got the default cluster effect, and missing budget keys defaulted to permissive values
    # (io=True, cost=100, fail=True; composer.py:76-80).
    report = check_plan([CLAIM], authority=_cleared("A4"), max_effect="irreversible")
    assert report.steps[0].effect is None and _codes(report) == ["G6_E_NAV_EFFECT_OVER_BUDGET"]
    assert check_plan([CLAIM], authority=_cleared("A4")).valid  # survival: no budget asked for


def test_nav003_a_budget_off_the_ladder_is_refused() -> None:
    with pytest.raises(ValueError, match="max_effect"):
        check_plan([RUN_TESTS], max_effect="none")


# --- NAV-012: a step declared its own agency level ---------------------------------------------------------------


def test_nav012_a_step_cannot_declare_its_own_agency() -> None:
    # Archive: execute_plan read each step's agency_level against a caller-supplied max_agency (agency_tracker.py:79,
    # composer.py:176), so the caller set both sides and agency_level=0 passed any ceiling.
    report = check_plan([dict(RUN_TESTS, agency_level=0)])
    assert _codes(report) == ["G6_E_NAV_STEP_MALFORMED"] and "agency_level" in report.issues[0].message
    assert check_plan([RUN_TESTS]).valid  # survival: the same step without it


# --- the authority rule (the archive's door_authority, kept) -----------------------------------------------------


def test_authority_an_op_no_reading_measured_is_not_cleared() -> None:
    # The library's own table measures only the workforce bench's ops; everything else reads A0 and is refused, as
    # door_authority.plan_refusal refused it on every multi-step route.
    report = check_plan([CLAIM])
    assert _codes(report) == ["G6_E_OP_NOT_CLEARED"] and report.steps[0].authority == "A0"
    assert "no authority reading measured it" in report.issues[0].message


def test_authority_an_op_that_can_act_needs_a2(tmp_path: Path) -> None:
    contracts = _contracts(tmp_path, "mailer", "send", "external")
    step = {"component": "mailer", "op": "send"}
    assert _codes(check_plan([step], contracts=contracts, authority=_cleared("A1"))) == ["G6_E_OP_ACTS_BELOW_A2"]
    assert check_plan([step], contracts=contracts, authority=_cleared("A2")).valid  # survival


def test_authority_an_op_with_no_declared_effect_counts_as_acting() -> None:
    assert _codes(check_plan([CLAIM], authority=_cleared("A1"))) == ["G6_E_OP_ACTS_BELOW_A2"]
    assert check_plan([CLAIM], authority=_cleared("A2")).valid  # survival


def test_authority_a_class_off_the_ladder_reads_a0() -> None:
    assert _codes(check_plan([RUN_TESTS], authority=_cleared("A9"))) == ["G6_E_OP_NOT_CLEARED"]


def test_a_sensitive_op_cannot_run_through_a_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    # Archive: execute_plan refused a plan naming a sensitive component before any step ran (composer.py:162, kept).
    monkeypatch.setattr(g6.grounding, "__g6_sensitive_ops__", ("check_claim",), raising=False)
    assert _codes(check_plan([CLAIM], authority=_cleared("A4"))) == ["G6_E_SENSITIVE_COMPONENT_DENIED"]


def test_a_component_that_does_not_load_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(name: str) -> frozenset[str]:
        raise ImportError("no module named half_written")

    monkeypatch.setattr(nav_plan, "listed_ops", broken)
    report = check_plan([CLAIM], authority=_cleared("A4"))
    assert _codes(report) == ["G6_E_NAV_COMPONENT_UNLOADABLE"] and "half_written" in report.issues[0].message


# --- run_plan: judged before any step runs; nothing runs without an executor ---------------------------------------


def test_run_plan_without_an_executor_runs_nothing() -> None:
    result = run_plan([RUN_TESTS], request_id="req-1", run_id="run-1")
    assert result.runs == () and result.executed == 0
    assert result.completion_state == "blocked-escalated" and result.warning_card["code"] == "G6_E_NAV_NO_EXECUTOR"
    assert check_envelope(result.to_dict()) == []


def test_run_plan_judges_the_whole_plan_before_any_step_runs() -> None:
    # Archive: execute_plan refused a plan with one uncleared step before running any (door_authority.py:120-131).
    calls: list[tuple[str, str]] = []
    result = run_plan([RUN_TESTS, {"component": "no_such", "op": "x"}],
                      executor=lambda c, o, a: calls.append((c, o)) or {"ok": True})
    assert calls == [] and result.runs == ()
    assert result.completion_state == "blocked-escalated" and result.warning_card["code"] == "G6_E_UNKNOWN_COMPONENT"


def test_run_plan_survival_an_injected_executor_runs_every_step_in_order() -> None:
    calls: list[tuple[str, str, dict]] = []

    def executor(component: str, op: str, args: dict) -> dict:
        calls.append((component, op, args))
        return {"passed": True, "completion_state": "verified"}

    result = run_plan([RUN_TESTS, IN_PLACE], executor=executor, request_id="req-1", run_id="run-1")
    assert [(c, o) for c, o, _ in calls] == [("workforce", "run_tests"), ("workforce", "run_tests_in_place")]
    assert calls[0][2] == RUN_TESTS["args"] and result.executed == 2
    # a step's own "verified" is not carried: running a step is not checking its result
    assert result.completion_state == "qualified-draft" and result.warning_card["code"] == "G6_E_NAV_RAN_UNCHECKED"
    assert check_envelope(result.to_dict()) == []


def test_run_plan_through_the_registry_door() -> None:
    result = run_plan([CLAIM], executor=get_registry().invoke, authority=_cleared("A2"))
    assert result.executed == 1 and result.completion_state == "qualified-draft"
    assert result.runs[0].result["op"] == "check_claim"


@pytest.mark.parametrize("answer", [RuntimeError("boom"), Failure("[G6_E_INPUT_VALIDATION] bad"), {"ok": False},
                                    {"completion_state": "blocked-escalated"}],
                         ids=["raises", "failure", "ok-false", "blocked"])
def test_run_plan_stops_at_the_first_failed_step(answer) -> None:
    calls: list[str] = []

    def executor(component: str, op: str, args: dict):
        calls.append(op)
        if isinstance(answer, Exception):
            raise answer
        return answer

    result = run_plan([RUN_TESTS, IN_PLACE], executor=executor)
    assert calls == ["run_tests"] and [r.status for r in result.runs] == ["failed"]
    assert result.completion_state == "blocked-escalated" and result.warning_card["code"] == "G6_E_NAV_STEP_FAILED"
    assert "1 later step(s) did not run" in result.warning_card["message"]
    assert check_envelope(result.to_dict()) == []
