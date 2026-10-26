# SPDX-License-Identifier: Apache-2.0
"""What a solver run claims, against audit 028: success is a named check, not "every step returned" (SV-001,
T-01); constraints are checked or refused (SV-002, T-02); production is refused (SV-005, T-03); no cost figure
(SV-010); nothing written under home (SV-011, T-12); no exemplar "verification" (SV-007); a failing optional
subsystem is recorded, not swallowed (SV-009). Each defect test has a survival control that must still pass."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from g6.core.llm import FakeLLM, LLMError
from g6.solver import (
    STEP_REGISTRY,
    ConstraintSpec,
    LLMQualityChecker,
    SolverInput,
    SolverOutput,
    SolverPlanner,
    StepResult,
    solve,
)
from g6.solver.schema import (
    E_CHECK_FAILED,
    E_CHECKED_DRAFT,
    E_CONSTRAINT_UNEVALUABLE,
    E_PRODUCTION_UNSUPPORTED,
    E_SIGNATURE_UNVERIFIABLE,
    E_UNCHECKED,
)

GOAL = "What is 6 * 7?"


def confident(text: str) -> dict:
    """Every step answers ``text`` with confidence, right or wrong."""
    return {name: (lambda ctx, t=text: {"answer": t, "confidence": 0.99}) for name in STEP_REGISTRY.names()}


class Counting:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, ctx):
        self.calls += 1
        return {"answer": "42"}


class RaisingLLM:
    def complete(self, system: str, user: str, *, max_tokens: int = 2048) -> str:
        raise LLMError("G6_E_NO_MODEL: offline")


# --- SV-001 ------------------------------------------------------------------------------------------------------

def test_sv001_t01_confident_wrong_runs_are_unchecked_not_success():
    # Archive: success=(final_status == "completed") (engine.py:1567) was stored as Case.success and reused as an
    # exemplar. Here two confident-but-wrong runs complete every step and neither is a success.
    for _ in range(2):
        out = solve(SolverInput(goal=GOAL), confident("The answer is 41."))
        assert out.status == "completed"
        assert (out.outcome, out.success, out.completion_state) == ("unchecked", False, "qualified-draft")
        assert out.to_envelope()["warning_card"]["code"] == E_UNCHECKED


def test_sv001_survival_a_passing_named_check_is_success_and_still_a_draft():
    out = solve(SolverInput(goal=GOAL, constraints=[ConstraintSpec(raw="says 42", kind="contains", value="42")]),
                confident("The answer is 42."))
    assert (out.outcome, out.success, out.completion_state) == ("passed", True, "qualified-draft")
    assert out.constraints_satisfied == {"says 42": True}
    envelope = out.to_envelope()
    assert envelope["warning_card"]["code"] == E_CHECKED_DRAFT and envelope["completion_state"] == "qualified-draft"
    assert {"completion_state", "warning_card", "evidence", "request_id", "run_id"} <= set(envelope)


def test_sv001_a_model_pass_alone_is_not_success():
    checker = LLMQualityChecker(FakeLLM(default=json.dumps({"acceptable": True, "issues": []})))
    out = solve(SolverInput(goal=GOAL), confident("The answer is 41."), quality_checker=checker)
    assert [(c.kind, c.verdict) for c in out.checks] == [("model_judgment", "pass")]
    assert (out.outcome, out.success) == ("unchecked", False)


# --- SV-002 ------------------------------------------------------------------------------------------------------

def test_sv002_t02_a_violated_constraint_is_reported_and_blocks():
    # Archive: constraints_satisfied={} always (engine.py:1740); the violated constraint left no trace.
    out = solve(SolverInput(goal=GOAL, constraints=[ConstraintSpec(raw="says 42", kind="contains", value="42")]),
                confident("The answer is 41."))
    assert out.constraints_satisfied == {"says 42": False}
    assert (out.outcome, out.success, out.completion_state, out.error_code) == (
        "failed", False, "blocked-escalated", E_CHECK_FAILED)


def test_sv002_a_free_text_constraint_is_refused_before_any_step():
    runner = Counting()
    out = solve(SolverInput(goal=GOAL, constraints=[ConstraintSpec(raw="be concise")]),
                {"code_construction": runner})
    assert (out.status, out.completion_state, out.error_code) == ("refused", "blocked-escalated",
                                                                  E_CONSTRAINT_UNEVALUABLE)
    assert runner.calls == 0 and out.steps == []


@pytest.mark.parametrize("spec", [ConstraintSpec(raw="a", kind="contains"),
                                  ConstraintSpec(raw="b", kind="max_chars"),
                                  ConstraintSpec(raw="c", kind="max_chars", threshold=-1),
                                  ConstraintSpec(raw="d", kind="contains", value="x", threshold=3),
                                  ConstraintSpec(raw="e", kind="min_chars", threshold=3, value="x")])
def test_sv002_a_malformed_constraint_is_refused(spec):
    out = solve(SolverInput(goal=GOAL, constraints=[spec]), confident("42"))
    assert out.error_code == E_CONSTRAINT_UNEVALUABLE


@pytest.mark.parametrize("spec, satisfied", [
    (ConstraintSpec(raw="has 42", kind="contains", value="42"), True),
    (ConstraintSpec(raw="no 41", kind="excludes", value="41"), True),
    (ConstraintSpec(raw="short", kind="max_chars", threshold=20), True),
    (ConstraintSpec(raw="long", kind="min_chars", threshold=100), False),
    (ConstraintSpec(raw="has 7", kind="contains", value="7"), False),
])
def test_sv002_each_constraint_kind_is_checked(spec, satisfied):
    out = solve(SolverInput(goal=GOAL, constraints=[spec]), confident("The answer is 42."))
    assert out.constraints_satisfied == {spec.raw: satisfied}
    assert out.success is satisfied


@pytest.mark.parametrize("field, value", [("subtasks", ["a"]), ("guardrails", [{"name": "g"}]),
                                          ("checkpoints", [{"name": "c"}]), ("resource_bounds", {}),
                                          ("breakpoints", [{"after_step": 3}])])
def test_sv002_a_field_the_archive_ignored_is_a_validation_error(field, value):
    with pytest.raises(ValidationError):
        SolverInput(goal=GOAL, **{field: value})


def test_sv002_the_planner_is_told_the_real_constraints():
    # Archive: every planner call passed constraints=[] (engine.py:870, 930, 1898).
    llm = FakeLLM(default="not json")
    solve(SolverInput(goal=GOAL, constraints=[ConstraintSpec(raw="says 42", kind="contains", value="42")]),
          confident("42"), planner=SolverPlanner(llm))
    assert llm.calls and all(json.loads(user)["constraints"] == ["says 42"] for _, user in llm.calls)


# --- SV-005 ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("run_mode, signature, code", [("production", "x", E_PRODUCTION_UNSUPPORTED),
                                                       ("production", None, E_PRODUCTION_UNSUPPORTED),
                                                       ("beta", "x", E_SIGNATURE_UNVERIFIABLE)])
def test_sv005_t03_production_or_an_unverifiable_signature_is_refused(run_mode, signature, code):
    # Archive: run_mode="production" with any non-empty reviewer_signature passed the gate (block.py:276-278).
    runner = Counting()
    out = solve(SolverInput(goal=GOAL, run_mode=run_mode, reviewer_signature=signature),
                {"code_construction": runner})
    assert (out.status, out.completion_state, out.error_code) == ("refused", "blocked-escalated", code)
    assert runner.calls == 0
    assert out.to_envelope()["warning_card"]["code"] == code


def test_sv005_survival_a_beta_run_without_a_signature_runs():
    out = solve(SolverInput(goal=GOAL, run_mode="beta"), confident("42"))
    assert out.status == "completed" and out.completion_state == "qualified-draft"


# --- SV-007, SV-010 ---------------------------------------------------------------------------------------------

def test_sv007_no_exemplar_similarity_is_presented_as_verification():
    # Archive: an LLM-rated similarity against unchecked past answers was stored as exemplar_verification and
    # StepResult.exemplar_score and gated nothing (engine.py:1382). The exemplar verifier is not ported.
    assert "exemplar_verification" not in SolverOutput.model_fields
    assert "exemplar_score" not in StepResult.model_fields


def test_sv010_no_unmeasured_cost_or_token_figure_is_reported():
    # Archive: total_cost_usd = tokens / 1000 * MODEL_COSTS.get(backend, 0.001) (engine.py:1736), a rate-table
    # guess presented as a measurement. Nothing here measures cost or tokens, so neither is reported.
    for model in (SolverOutput, StepResult):
        assert not [f for f in model.model_fields if "cost" in f or "token" in f], model
    envelope = solve(SolverInput(goal=GOAL), confident("42")).to_envelope()
    assert "total_cost_usd" not in envelope and envelope["confidence"] is None


# --- SV-011 ------------------------------------------------------------------------------------------------------

def test_sv011_t12_a_run_writes_nothing_under_home_or_the_working_directory(tmp_path, monkeypatch):
    # Archive: every solve wrote ~/.g6/learning_memory.db, failure_patterns.jsonl, soar_cases.db and
    # distillation.db with no opt-in (block.py:593-613).
    dirs = {name: tmp_path / name for name in ("home", "g6_home", "cwd")}
    for d in dirs.values():
        d.mkdir()
    monkeypatch.setenv("HOME", str(dirs["home"]))
    monkeypatch.setenv("USERPROFILE", str(dirs["home"]))
    monkeypatch.setenv("G6_HOME", str(dirs["g6_home"]))
    monkeypatch.chdir(dirs["cwd"])
    llm = FakeLLM(default=json.dumps({"acceptable": True, "level": "complex", "rationale": "r",
                                      "steps_to_run": list(range(1, 16)), "selected_index": 0}))
    for _ in range(2):
        solve(SolverInput(goal=GOAL), confident("42"), planner=SolverPlanner(llm),
              quality_checker=LLMQualityChecker(llm)).to_envelope()
    assert {name: list(d.iterdir()) for name, d in dirs.items()} == {"home": [], "g6_home": [], "cwd": []}


# --- SV-009 ------------------------------------------------------------------------------------------------------

def test_sv009_a_failing_planner_is_recorded_not_swallowed():
    # Archive: learning-write failures were logged at debug while learning_loop_state reported active=True
    # (engine.py:1570, 1717). The learning stack is not ported; the optional subsystem kept here, the model
    # planner, records every failure as a degraded fallback on the output.
    out = solve(SolverInput(goal=GOAL), confident("42"), planner=SolverPlanner(RaisingLLM()))
    evidence = out.agentic_evidence
    assert evidence["degraded"] and evidence["llm_decisions"] == 0 and evidence["warnings"]
    assert all("LLMError" in d["rationale"] for d in evidence["decisions"])
    assert out.degraded and out.degradation_reason and out.completion_state == "qualified-draft"


def test_sv009_survival_no_model_supplied_is_not_a_degradation():
    out = solve(SolverInput(goal=GOAL), confident("42"))
    assert out.agentic_evidence["model_supplied"] is False and out.agentic_evidence["degraded"] is False
    assert out.degraded is False
