# SPDX-License-Identifier: Apache-2.0
"""Measured case identities feed the existing regression comparison."""
import pytest

from g6.self_training import BenchmarkCase, RunSummary, regression_delta, run_cases


def test_benchmark_runner_01_invokes_the_supplied_callable_per_case():
    # Removal makes this fail: `actual = executor(case.input_data)` in run_cases.
    called = []
    cases = [BenchmarkCase("double", 21, 42), BenchmarkCase("zero", 0, 0)]
    result = run_cases(lambda value: called.append(value) or value * 2, cases)
    assert called == [21, 0] and isinstance(result, RunSummary)
    assert result.collected == {"double", "zero"} and not result.failing
    assert regression_delta(result, result).to_eval_report().independent_eval is False


def test_benchmark_runner_d1_swapped_failures_are_a_regression():
    # Removal makes this fail: `failing.add(case.name)` in run_cases.
    cases = [BenchmarkCase("odd", 3, 6), BenchmarkCase("even", 4, 8)]
    baseline = run_cases(lambda n: 6 if n == 3 else 7, cases)
    candidate = run_cases(lambda n: 5 if n == 3 else 8, cases)
    assert len(baseline.failing) == len(candidate.failing) == 1
    verdict = regression_delta(candidate, baseline)
    assert verdict.state == "failed" and verdict.new_failures == {"odd"}


def test_benchmark_runner_03_claimed_success_is_not_an_assertion():
    # Removal makes this fail: `if case.expected_output is _UNSET:` in run_cases.
    summary = run_cases(lambda value: {"success": True}, [BenchmarkCase("unasserted", {})])
    assert summary.skipped == {"unasserted"} and summary.evaluable is False
    assert regression_delta(summary, summary).state == "unevaluable"
    checked = run_cases(lambda value: {"success": True}, [BenchmarkCase("asserted", {}, {"success": False})])
    assert checked.failing == {"asserted"}


def test_none_is_an_explicit_assertion_and_exceptions_fail():
    # Removal makes this fail: `failing.add(case.name)` in the exception handler of run_cases.
    assert not run_cases(lambda value: None, [BenchmarkCase("none", {}, None)]).failing
    def broken(value):
        raise RuntimeError("unavailable")
    assert run_cases(broken, [BenchmarkCase("broken", {}, None)]).failing == {"broken"}


def test_duplicate_ids_are_refused_before_any_execution():
    # Removal makes this fail: `raise ValueError("G6_E_BENCHMARK_CASES: case ids must be non-empty and unique")` in run_cases.
    called = []
    with pytest.raises(ValueError, match="G6_E_BENCHMARK_CASES"):
        run_cases(lambda x: called.append(x), [BenchmarkCase("same", 1, 1), BenchmarkCase("same", 2, 2)])
    assert called == []


def test_empty_cases_are_unevaluable():
    # Removal makes this fail: `evaluable=bool(cases) and not skipped` in run_cases.
    assert run_cases(lambda x: x, []).evaluable is False
