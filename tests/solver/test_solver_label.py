# SPDX-License-Identifier: Apache-2.0
"""g6.solver's label cap and package hygiene against audit 028 (SV-003, SV-004, SV-012; T-06, T-07, T-08, T-09).

The archive could not emit ``verified`` either; these tests guard that invariant in the port and pin the latent
plumbing the audit asked to remove (a ``verified`` member in a Literal, a planner prompt asking the model to
self-declare a completion state)."""
from __future__ import annotations

import itertools
import json
import re
import subprocess
import sys
import typing
from pathlib import Path

import pytest
from pydantic import ValidationError

import g6.solver
from g6.core.llm import FakeLLM
from g6.solver import NamedCheck, SolverInput, SolverOutput, SolverPlanner, StepResult, solve
from g6.solver.schema import CompletionState

SRC = Path(g6.solver.__file__).parent


def test_sv012_t07_verified_is_not_a_constructible_state():
    # Archive: SolverOpsOutput.completion_state's Literal admitted "verified" (solver_ops_mcp/schema.py:50).
    assert typing.get_args(CompletionState) == ("qualified-draft", "blocked-escalated")
    with pytest.raises(ValidationError):
        SolverOutput(status="completed", completion_state="verified", final_answer="x")


def test_t08_no_combination_of_visible_fields_yields_verified():
    step_sets = [[], [StepResult(step_number=8, step_name="code_construction", status="completed",
                                 output={"answer": "x"})],
                 [StepResult(step_number=1, step_name="goal_setting", status="failed", reason="boom")],
                 [StepResult(step_number=1, step_name="goal_setting", status="unavailable")],
                 [StepResult(step_number=8, step_name="code_construction", status="completed", output={"a": "x"},
                             fallback_used=True, fallback_component="alt")]]
    check_sets = [[], [NamedCheck(name="c", kind="deterministic", verdict="pass")],
                  [NamedCheck(name="c", kind="deterministic", verdict="fail")],
                  [NamedCheck(name="q", kind="model_judgment", verdict="pass")],
                  [NamedCheck(name="c", kind="deterministic", verdict="unchecked")]]
    seen = set()
    for status, steps, checks, answer, error, evidence in itertools.product(
            ["completed", "incomplete", "halted", "refused"], step_sets, check_sets, ["", "an answer"],
            ["", "boom"], [None, {"degraded": True}, {"degraded": False}]):
        out = SolverOutput(status=status, steps=steps, checks=checks, final_answer=answer, error=error,
                           agentic_evidence=evidence, success=True, outcome="passed")
        assert out.completion_state in ("qualified-draft", "blocked-escalated")
        assert out.success == (out.outcome == "passed")   # never hand-set
        seen.add(out.completion_state)
    assert seen == {"qualified-draft", "blocked-escalated"}   # the gate does not block everything


def test_t08_survival_a_clean_checked_run_is_a_qualified_draft():
    out = SolverOutput(status="completed", final_answer="4",
                       steps=[StepResult(step_number=8, step_name="code_construction", status="completed",
                                         output={"answer": "4"})],
                       checks=[NamedCheck(name="constraint:4", kind="deterministic", verdict="pass")])
    assert (out.completion_state, out.outcome, out.success, out.error_code) == ("qualified-draft", "passed", True, "")


def _verified_claiming_llm() -> FakeLLM:
    def reply(system: str, user: str) -> str:
        body = {"completion_state": "verified", "rationale": "certain", "confidence": 1.0}
        if "simple, moderate or complex" in system:
            body["level"] = "simple"
        elif "which steps" in system:
            body["steps_to_run"] = [1, 8, 10, 11]
        else:
            body["selected_index"] = 0
        return json.dumps(body)
    return FakeLLM(reply)


def test_sv012_t09_a_planner_reply_claiming_verified_changes_nothing():
    # Archive: the planner prompt asked for completion_state (verified|qualified-draft|blocked-escalated) and the
    # reply was parsed into every decision record (agentic_runtime.py:387-398), then never read.
    llm = _verified_claiming_llm()
    runners = {"goal_setting": lambda c: "add", "code_construction": lambda c: {"answer": "4"},
               "solution_execution": lambda c: {"answer": "4.0"}, "solution_evaluation": lambda c: {"note": "fine"}}
    out = solve(SolverInput(goal="What is 2 + 2?"), runners, planner=SolverPlanner(llm))
    assert out.completion_state == "qualified-draft" and out.outcome == "unchecked"
    assert out.agentic_evidence["llm_decisions"] == 3   # the model did decide complexity, plan and answer
    assert all("completion_state" not in d for d in out.agentic_evidence["decisions"])
    assert llm.calls and all("completion_state" not in s and "verified" not in s for s, _ in llm.calls)


def test_sv003_t06_import_loads_no_django_and_no_other_component():
    # Archive: agentic_runtime imported mvp.reliability_pipeline.llm, whose package init pulled billing and Django.
    probe = ("import sys, g6.solver; "
             "print(sorted({'.'.join(m.split('.')[:2]) for m in sys.modules if m.startswith('g6.')}), "
             "any(m == 'django' or m.startswith('django.') for m in sys.modules))")
    out = subprocess.run([sys.executable, "-I", "-c", probe], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "['g6.core', 'g6.solver'] False"


_IMPORT = re.compile(r"^\s*(?:from|import)\s+([\w.]+)", re.MULTILINE)


def test_sv004_no_proprietary_header_and_no_saas_imports():
    # Archive: about 60 files carried "Proprietary and confidential"; block.py built a TierManagerBlock and
    # learning_loop imported token_budget.retention.
    files = sorted(SRC.rglob("*.py"))
    assert len(files) >= 10
    for f in files:
        text = f.read_text(encoding="utf-8")
        assert text.startswith("# SPDX-License-Identifier: Apache-2.0\n"), f.name
        assert "Proprietary" not in text and "All rights reserved" not in text, f.name
        for module in _IMPORT.findall(text):
            assert module.split(".")[0] != "mvp", (f.name, module)
            assert not any(word in module for word in ("tier_manager", "token_budget", "billing",
                                                       "reliability_pipeline")), (f.name, module)
