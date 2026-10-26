# SPDX-License-Identifier: Apache-2.0
"""The optional model planner and quality checker. The planner records honestly who decided each choice; the
quality checker is tri-state and an unavailable check is never acceptance (audit 028 SV-006, T-04). Offline: every
model is a fake."""
from __future__ import annotations

import json

import pytest

from g6.core.llm import FakeLLM, LLMError
from g6.solver import (
    STEP_REGISTRY,
    LLMQualityChecker,
    SolverInput,
    SolverPlanner,
    TaskComplexity,
    plan_run,
    solve,
)
from g6.solver.schema import E_CHECK_FAILED

GOAL = "What is 6 * 7?"


class RaisingLLM:
    def complete(self, system: str, user: str, *, max_tokens: int = 2048) -> str:
        raise LLMError("connection refused")


def by_prompt(**replies: dict) -> FakeLLM:
    """A fake model answering the complexity, plan and answer prompts with the given JSON bodies."""
    def respond(system: str, user: str) -> str:
        if "simple, moderate or complex" in system:
            return json.dumps(replies.get("complexity", {}))
        if "which steps" in system:
            return json.dumps(replies.get("plan", {}))
        return json.dumps(replies.get("answer", {}))
    return FakeLLM(respond)


def test_model_decisions_are_used_and_recorded():
    llm = by_prompt(complexity={"level": "complex", "rationale": "many parts"},
                    plan={"steps_to_run": [2, 8], "rationale": "gather then answer"})
    plan = plan_run(SolverInput(goal=GOAL), planner=SolverPlanner(llm))
    assert plan.complexity is TaskComplexity.COMPLEX
    assert plan.steps_to_run == {1, 2, 8, 11}   # the model's steps plus the answer spine
    assert [(d.decision, d.llm_used, d.degraded) for d in plan.decisions] == [("complexity", True, False),
                                                                           ("step_plan", True, False)]


@pytest.mark.parametrize("complexity, plan", [({"level": "huge", "rationale": "r"}, {"steps_to_run": [99]}),
                                              ({"level": "simple"}, {"steps_to_run": "all", "rationale": "r"})])
def test_an_off_menu_or_unexplained_reply_falls_back_degraded(complexity, plan):
    result = plan_run(SolverInput(goal=GOAL), planner=SolverPlanner(by_prompt(complexity=complexity, plan=plan)))
    assert result.complexity is TaskComplexity.SIMPLE and result.steps_to_run == {1, 8, 10, 11}
    assert all(not d.llm_used and d.degraded and d.rationale for d in result.decisions)


def test_without_a_model_the_deterministic_paths_decide_undegraded():
    plan = plan_run(SolverInput(goal=GOAL))
    assert [(d.llm_used, d.degraded, d.rationale) for d in plan.decisions] == [(False, False, "no model supplied")] * 2


def test_a_caller_override_is_recorded_as_the_caller_s():
    plan = plan_run(SolverInput(goal=GOAL, complexity_override="moderate"))
    assert plan.complexity is TaskComplexity.MODERATE and plan.decisions[0].detail == "caller_override:moderate"


def _two_candidates() -> dict:
    runners = {name: (lambda ctx: {"note": "done"}) for name in STEP_REGISTRY.names()}
    runners["code_construction"] = lambda ctx: {"answer": "42"}
    runners["solution_execution"] = lambda ctx: {"answer": "forty-two"}
    return runners


def test_the_model_chooses_between_candidates_but_cannot_invent_one():
    chosen = solve(SolverInput(goal=GOAL), _two_candidates(),
                   planner=SolverPlanner(by_prompt(complexity={"level": "simple", "rationale": "r"},
                                                   plan={"steps_to_run": [1, 8, 10, 11], "rationale": "r"},
                                                   answer={"selected_index": 1, "rationale": "the step 10 one"})))
    assert (chosen.final_answer, chosen.final_answer_step) == ("forty-two", 10)
    invented = solve(SolverInput(goal=GOAL), _two_candidates(),
                     planner=SolverPlanner(by_prompt(answer={"selected_index": 5, "answer": "43", "rationale": "r"})))
    assert invented.final_answer == "42"   # priority order: step 8 first for a simple task
    answer = invented.agentic_evidence["decisions_by_type"]["answer"]
    assert answer["llm_used"] is False and answer["degraded"] is True


# --- SV-006 ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("llm, method", [(None, "no_model"), (RaisingLLM(), "model_error"),
                                         (FakeLLM(default="Looks fine to me."), "unparseable"),
                                         (FakeLLM(default='{"verdict": "ok"}'), "unparseable"),
                                         (FakeLLM(default='{"acceptable": "yes"}'), "unparseable")])
def test_sv006_t04_an_unavailable_quality_check_is_unchecked_not_acceptance(llm, method):
    # Archive: no registry -> acceptable True (0.6); a raising call -> acceptable True (0.5); any reply lacking
    # "unacceptable"/"false" -> acceptable (llm_quality_checker.py:44-49, 72-79, 95).
    verdict = LLMQualityChecker(llm).check("The answer is 42 because 6 times 7 is 42.", GOAL)
    assert (verdict.verdict, verdict.method) == ("unchecked", method)
    out = solve(SolverInput(goal=GOAL), {name: (lambda ctx: {"answer": "42"}) for name in STEP_REGISTRY.names()},
                quality_checker=LLMQualityChecker(llm))
    assert [c.verdict for c in out.checks] == ["unchecked"]
    assert (out.outcome, out.success, out.completion_state) == ("unchecked", False, "qualified-draft")


def test_sv006_survival_a_model_pass_is_a_pass_and_a_model_fail_blocks():
    good = LLMQualityChecker(FakeLLM(default=json.dumps({"acceptable": True, "issues": []})))
    assert good.check("The answer is 42.", GOAL).verdict == "pass"
    bad = LLMQualityChecker(FakeLLM(default=json.dumps({"acceptable": False, "issues": ["off topic"]})))
    out = solve(SolverInput(goal=GOAL), {name: (lambda ctx: {"answer": "Paris"}) for name in STEP_REGISTRY.names()},
                quality_checker=bad)
    assert [(c.kind, c.verdict) for c in out.checks] == [("model_judgment", "fail")]
    assert (out.outcome, out.completion_state, out.error_code) == ("failed", "blocked-escalated", E_CHECK_FAILED)


def test_sv006_a_red_flag_fails_deterministically_without_asking_the_model():
    llm = FakeLLM(default=json.dumps({"acceptable": True}))
    verdict = LLMQualityChecker(llm).check("I am not able to answer that.", GOAL)
    assert (verdict.verdict, verdict.method, verdict.as_check().kind) == ("fail", "red_flag", "deterministic")
    assert llm.calls == []
