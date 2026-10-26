# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from g6.core.llm import EmptyRetry, FakeLLM, LLMError
from g6.reasoning import (
    Arm,
    Budget,
    BudgetExhausted,
    Candidate,
    Case,
    DirectStep,
    MeteredCaller,
    compare,
    first_json_object,
    self_consistency,
    weighted_vote,
)
from g6.reasoning.compare import sign_test_p


def test_metered_caller_counts_failed_attempts_and_stops():
    calls = {"n": 0}

    def flaky(system, user):
        calls["n"] += 1
        if calls["n"] == 1:
            raise LLMError("boom")
        return '{"answer": "4"}'

    m = MeteredCaller(FakeLLM(flaky), Budget(max_model_calls=2))
    with pytest.raises(LLMError):
        m.complete("s", "u")
    m.complete("s", "u")
    assert (m.calls, m.failed_calls) == (2, 1)
    with pytest.raises(BudgetExhausted):
        m.complete("s", "u")
    assert calls["n"] == 2, "an exhausted budget must not reach the model"


def test_wall_clock_ceiling():
    t = {"now": 0.0}
    m = MeteredCaller(FakeLLM(default="x"), Budget(max_wall_clock_s=1.0), clock=lambda: t["now"])
    m.complete("s", "u")
    t["now"] = 2.0
    assert m.check() == "max_wall_clock_s"


def test_empty_answer_retries_are_each_charged():
    """Audit 023 RH-007 (RH-T06): an EmptyRetry wrapper's three backend calls are three calls, not one."""
    replies = iter(["", "", '{"answer": "4"}'])
    m = MeteredCaller(EmptyRetry(FakeLLM(lambda s, u: next(replies))), Budget(max_model_calls=3))
    assert m.complete("s", "u") == '{"answer": "4"}'
    assert m.calls == 3 and m.check() == "max_model_calls"
    exhausted = MeteredCaller(EmptyRetry(FakeLLM(default="")))
    with pytest.raises(LLMError):
        exhausted.complete("s", "u")
    assert (exhausted.calls, exhausted.failed_calls) == (4, 1)


def test_a_call_that_returns_after_the_time_limit_is_detected():
    """Audit 023 RH-008: the wall clock is checked after a call returns, not only before it starts."""
    t = {"now": 0.0}

    def slow(system, user):
        t["now"] = 5.0
        return '{"answer": "4"}'

    m = MeteredCaller(FakeLLM(slow), Budget(max_wall_clock_s=1.0), clock=lambda: t["now"])
    with pytest.raises(BudgetExhausted, match="max_wall_clock_s"):
        m.complete("s", "u")
    assert (m.calls, m.output_chars) == (1, len('{"answer": "4"}'))  # the late call is still charged
    on_time = MeteredCaller(FakeLLM(default="x"), Budget(max_wall_clock_s=1.0), clock=lambda: 0.5)
    assert on_time.complete("s", "u") == "x"  # control: a call inside the limit returns its answer


def test_failed_calls_are_abstentions_and_a_lone_survivor_is_no_majority():
    """Audit 023 RH-004 (RH-T03): three of four calls raise; the survivor is a plurality, never a majority."""
    calls = {"n": 0}

    def mostly_down(system, user):
        calls["n"] += 1
        if calls["n"] < 4:
            raise LLMError("backend down")
        return '{"answer": "4"}'

    r = self_consistency(FakeLLM(mostly_down), "2+2", 4)
    assert r.winner == "4" and not r.has_majority and r.abstained == 3
    assert not weighted_vote([Candidate("4"), Candidate(""), Candidate("")]).has_majority
    assert weighted_vote([Candidate("4"), Candidate("4"), Candidate("")]).has_majority  # control: 2 of 3
    assert weighted_vote([Candidate("4", verified=True), Candidate(""), Candidate("")]).has_majority  # verified rule


def test_vote_verified_dominates_and_majority_is_strict():
    r = weighted_vote([Candidate("5"), Candidate("5"), Candidate("4", verified=True)])
    assert r.winner == "4" and r.verified_exist and r.has_majority
    r = weighted_vote([Candidate("A"), Candidate("B"), Candidate("C", weight=1.5)])
    assert r.winner == "C" and not r.has_majority
    tie = weighted_vote([Candidate("A"), Candidate("B")])
    assert tie.winner is None and not tie.has_majority
    assert weighted_vote([Candidate(""), Candidate("x", weight=float("nan"))]).winner is None
    assert weighted_vote([Candidate("Paris."), Candidate("paris")]).has_majority


def test_first_json_object_handles_prose_latex_and_skips_bad_objects():
    assert first_json_object('Sure! {"answer": "\\\\frac{1}{2}"} done')["answer"] == "\\frac{1}{2}"
    assert first_json_object('{"answer": "\\frac{1}{2}"}')["answer"] == "\\frac{1}{2}"
    assert first_json_object('{bad} then {"answer": 3}') == {"answer": 3}
    with pytest.raises(ValueError):
        first_json_object("no json here")


def test_direct_step_and_self_consistency():
    answers = iter(['{"answer": "4"}', "garbage", '{"answer": "4"}', '{"answer": "5"}'])
    llm = FakeLLM(lambda s, u: next(answers))
    assert DirectStep(llm).solve("2+2") == "4"
    assert DirectStep(llm).solve("2+2") == ""
    r = self_consistency(llm, "2+2", 2)
    assert r.winner is None  # 4 vs 5: a tie is not an answer


def _cases(n, truth="ok"):
    return [Case(f"p{i}", lambda a, t=truth: 1.0 if a == t else 0.0) for i in range(n)]


def test_compare_refuses_small_samples():
    good = Arm("cand", lambda p: ("ok", 1))
    bad = Arm("base", lambda p: ("no", 1))
    v = compare(bad, good, _cases(1))
    assert not v.promoted and v.reason == "insufficient_cases"


def test_compare_promotes_a_clear_compute_matched_win():
    v = compare(Arm("base", lambda p: ("no", 1)), Arm("cand", lambda p: ("ok", 1)), _cases(30))
    assert v.promoted and v.reason == "beats_baseline" and v.wins == 30 and v.p_value < 1e-6


def test_compare_refuses_extra_compute_unless_declared():
    base, cand = Arm("base", lambda p: ("no", 1)), Arm("cand", lambda p: ("ok", 8))
    v = compare(base, cand, _cases(30))
    assert not v.promoted and v.reason == "not_compute_matched"
    v = compare(base, cand, _cases(30), matched=False)
    assert v.promoted and v.reason == "beats_baseline_with_more_compute" and not v.compute_matched


def test_compare_equal_skill_is_not_a_win():
    """Each arm is right on a different half of the cases: no promotion."""
    even = Arm("base", lambda p: ("ok", 1) if int(p[1:]) % 2 == 0 else ("no", 1))
    odd = Arm("cand", lambda p: ("ok", 1) if int(p[1:]) % 2 else ("no", 1))
    assert compare(even, odd, _cases(30)).reason == "no_improvement"


def test_compare_small_edge_is_within_noise():
    """Candidate wins 4 more cases out of 30: a real but unproven edge."""
    base = Arm("base", lambda p: ("ok", 1) if int(p[1:]) < 10 else ("no", 1))
    cand = Arm("cand", lambda p: ("ok", 1) if int(p[1:]) < 14 else ("no", 1))
    v = compare(base, cand, _cases(30))
    assert not v.promoted and v.reason == "within_noise" and v.p_value == pytest.approx(1 / 16)


def test_crashing_arm_and_scorer_are_counted():
    def boom(p):
        raise RuntimeError("x")
    cases = _cases(30) + [Case("bad", lambda a: 1 / 0)]
    v = compare(Arm("base", boom), Arm("cand", lambda p: ("ok", 1)), cases)
    assert v.failures["base"] == 31 and v.failures["scorer"] == 1 and v.n == 30


def test_sign_test_reference_values():
    assert sign_test_p(0, 0) == 1.0
    assert sign_test_p(10, 0) == pytest.approx(1 / 1024)
    assert sign_test_p(5, 5) > 0.5
