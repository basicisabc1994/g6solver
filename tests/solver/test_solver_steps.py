# SPDX-License-Identifier: Apache-2.0
"""Step execution against audit 028 SV-008 (T-10): a step that returns nothing usable is not "completed", and a
step with no runner never counts as done. Plus the step table, profiles, classifier and red flags."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from g6.solver import (
    STEP_REGISTRY,
    CategoryRegistry,
    SolverInput,
    StepContext,
    TaskComplexity,
    classify_task,
    execute_step,
    profile_for,
    red_flags,
    solve,
)
from g6.solver.schema import E_NO_ANSWER, E_STEP_FAILED

GOAL = "What is 6 * 7?"   # simple: steps 1, 8, 10, 11


def every_step(fn) -> dict:
    return {name: fn for name in STEP_REGISTRY.names()}


def ctx_for(number: int) -> StepContext:
    return StepContext(goal=GOAL, context="", constraints=(), complexity="simple",
                       step=STEP_REGISTRY.by_number(number))


def test_sv008_t10_empty_outputs_are_not_a_clean_completion():
    # Archive: a step was "completed" whenever a component returned any result, {} included (engine.py:1406,
    # step_executor.py:79-107), so fifteen empty dicts made a clean completed run.
    out = solve(SolverInput(goal=GOAL), every_step(lambda ctx: {}))
    planned = [s for s in out.steps if s.status != "skipped"]
    assert planned and all(s.status == "failed" and "no non-empty value" in s.reason for s in planned)
    assert (out.status, out.completion_state, out.error_code) == ("halted", "blocked-escalated", E_STEP_FAILED)


@pytest.mark.parametrize("value", [None, "", "   ", {}, {"result": None}, {"answer": ""}, {"items": []}, 42,
                                   {1: "keyed by a number"}, "I'm sorry, but I cannot help with that request."])
def test_sv008_an_unusable_return_is_a_failed_step(value):
    result = execute_step(STEP_REGISTRY.by_number(1), ctx_for(1), lambda ctx: value)
    assert result.status == "failed" and result.attempts == STEP_REGISTRY.by_number(1).max_attempts


@pytest.mark.parametrize("value", ["a plan", {"answer": "42"}, {"score": 0}, {"items": ["x"]}])
def test_sv008_survival_a_substantive_return_completes(value):
    result = execute_step(STEP_REGISTRY.by_number(1), ctx_for(1), lambda ctx: value)
    assert result.status == "completed" and result.attempts == 1


def test_a_planned_step_with_no_runner_is_unavailable_and_never_done():
    out = solve(SolverInput(goal=GOAL), {"code_construction": lambda ctx: {"answer": "42"}})
    by_number = {s.step_number: s.status for s in out.steps}
    assert [n for n, st in by_number.items() if st == "unavailable"] == [1, 10, 11]
    assert by_number[8] == "completed" and out.status == "incomplete"
    assert {f.step_number for f in out.failures} == {1, 10, 11}
    assert all(f.failed_dep == "no runner supplied" for f in out.failures)
    assert out.degraded and out.completion_state == "qualified-draft" and out.final_answer == "42"


def test_survival_every_planned_step_with_a_runner_is_a_clean_completion():
    out = solve(SolverInput(goal=GOAL), every_step(lambda ctx: {"answer": "42"}))
    assert out.status == "completed" and not out.failures and not out.degraded
    assert out.completion_state == "qualified-draft" and out.final_answer == "42" and out.final_answer_step == 8


def test_a_run_with_no_answer_text_is_blocked():
    out = solve(SolverInput(goal=GOAL), every_step(lambda ctx: {"count": 3}))
    assert out.status == "completed" and out.final_answer == ""
    assert (out.completion_state, out.error_code) == ("blocked-escalated", E_NO_ANSWER)


def test_a_raising_runner_is_retried_then_failed():
    calls = []

    def boom(ctx):
        calls.append(ctx.step.number)
        raise RuntimeError("tool down")

    result = execute_step(STEP_REGISTRY.by_number(1), ctx_for(1), boom)
    assert result.status == "failed" and len(calls) == 3 and "RuntimeError: tool down" in result.reason


def test_a_fallback_runner_is_marked_and_recorded():
    fallbacks = CategoryRegistry()
    fallbacks.register("alt_goal", "alignment", lambda ctx: "a fallback plan")
    out = solve(SolverInput(goal=GOAL), {**every_step(lambda ctx: {"answer": "42"}),
                                         "goal_setting": lambda ctx: None}, fallbacks=fallbacks)
    step1 = out.steps[0]
    assert (step1.status, step1.fallback_used, step1.fallback_component) == ("completed", True, "alt_goal")
    assert [(f.step_number, f.chosen_fallback) for f in out.failures] == [(1, "alt_goal")]
    assert out.degraded and out.completion_state == "qualified-draft"


def test_a_fallback_alone_runs_a_step_with_no_runner():
    fallbacks = CategoryRegistry()
    fallbacks.register("alt", "standalone", lambda ctx: {"answer": "42"})
    result = execute_step(STEP_REGISTRY.by_number(8), ctx_for(8), None, fallbacks)
    assert (result.status, result.fallback_component) == ("completed", "alt")


def test_runners_see_prior_outputs_read_only():
    seen = {}

    def construct(ctx):
        seen["goal"] = ctx.outputs["goal_setting"]["result"]
        with pytest.raises(TypeError):
            ctx.outputs["goal_setting"]["result"] = "changed"   # type: ignore[index]
        return {"answer": "42"}

    out = solve(SolverInput(goal=GOAL), {**every_step(lambda ctx: {"answer": "42"}),
                                         "goal_setting": lambda ctx: "multiply", "code_construction": construct})
    assert seen == {"goal": "multiply"} and out.steps[0].output == {"result": "multiply"}


def test_an_unknown_runner_name_is_an_error():
    with pytest.raises(ValueError, match="G6_E_SOLVER_UNKNOWN_STEP"):
        solve(SolverInput(goal=GOAL), {"goal setting": lambda ctx: "x"})


def test_skip_steps_skips_and_rejects_unknown_numbers():
    out = solve(SolverInput(goal=GOAL, skip_steps=[1, 10, 11]), {"code_construction": lambda ctx: {"answer": "42"}})
    assert out.status == "completed" and not out.failures
    assert out.steps[0].reason == "skipped by the caller (skip_steps)"
    with pytest.raises(ValidationError):
        SolverInput(goal=GOAL, skip_steps=[16])


def test_the_step_table_and_profiles():
    assert len(STEP_REGISTRY) == 15 and STEP_REGISTRY.numbers() == frozenset(range(1, 16))
    assert STEP_REGISTRY.by_name("code_construction").number == 8
    assert profile_for("simple").active_steps == {1, 8, 10, 11}
    assert profile_for(TaskComplexity.MODERATE).skipped_steps == frozenset(range(1, 16)) - {1, 8, 10, 11, 12}
    assert profile_for("complex").skipped_steps == frozenset()


@pytest.mark.parametrize("goal, level", [
    ("What is 2 + 2?", "simple"),
    ("Implement a cache", "moderate"),   # an imperative opening is never simple
    ("Implement a distributed database migration", "moderate"),
    ("Optimize the database migration pipeline for performance and then deploy it with docker, and then "
     "finally add authentication", "complex"),
    ("", "moderate"),
])
def test_classify_task(goal, level):
    assert classify_task(goal).value == level


def test_red_flags_and_what_is_not_flagged():
    assert [f.name for f in red_flags("")] == ["EMPTY_OUTPUT"]
    assert [f.name for f in red_flags("As an AI, I cannot do that.")] == ["REFUSAL"]
    assert [f.name for f in red_flags("go round and " * 4)] == ["REPETITION"]
    loop = "for i in range(3):\n    print(i)\n" * 4
    assert red_flags(loop, step_number=8) == []   # parses as Python on a code step
    assert red_flags("答案是四十二，因为六乘以七等于四十二。这个结果可以直接验证。" * 2) == []   # non-Latin script is fine
