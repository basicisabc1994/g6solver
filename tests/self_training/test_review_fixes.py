# SPDX-License-Identifier: Apache-2.0
"""Benchmark stop, resource and environment failures never become measured passes."""
import pytest

from g6.self_training import BenchmarkCase, regression_delta, run_cases


@pytest.mark.parametrize("pre_engaged", [True, False])
def test_case_runner_obeys_stop_and_no_untrusted_surface_reaches_it(pre_engaged, monkeypatch):
    # Removal makes this fail: the loop's `if stopped is not None:` guard.
    import g6.self_training as package

    called = []
    if pre_engaged:
        monkeypatch.setenv("G6_STOP", "review pause")

    def execute(value):
        called.append(value)
        monkeypatch.setenv("G6_STOP", "review pause")
        return value

    summary = run_cases(execute, [BenchmarkCase("first", 1, 1), BenchmarkCase("second", 2, 2)])
    assert called == ([] if pre_engaged else [1])
    assert summary.evaluable is False
    assert "G6_E_STOPPED" in " ".join(summary.diagnostics)
    assert "review pause" in " ".join(summary.diagnostics)
    assert regression_delta(summary, summary).state == "unevaluable"
    # run_cases is Python only: no op reaches it from an untrusted surface, so it is not a sensitive op (kit G5).
    from g6.core.registry import get_registry
    ops = get_registry().invoke("self_training", "ops")
    assert "run_cases" not in (ops.unwrap().get("ops", []) if ops.__class__.__name__ == "Success" else [])
    assert not getattr(package, "__g6_sensitive_ops__", ())


@pytest.mark.parametrize("limit", ["count", "bytes", "nested"])
def test_benchmark_oversized_suite_is_refused_before_copy_or_execution(limit):
    # Removal makes this fail: `check_case_bounds(len(cases), ...)` before tuple(cases).
    MAX_CASES, MAX_SUITE_BYTES = 1000, 1024 * 1024

    class UncopiedCases(list):
        def __iter__(self):
            raise AssertionError("suite copied before its count was checked")

    cases = UncopiedCases([BenchmarkCase(str(i), i, i) for i in range(MAX_CASES + 1)])
    if limit != "count":
        payload = "x" * (MAX_SUITE_BYTES + 1)
        cases = [BenchmarkCase("large", {"nested": [payload]} if limit == "nested" else payload, None)]
    called = []
    with pytest.raises(ValueError, match="G6_E_RESOURCE_LIMIT"):
        run_cases(lambda value: called.append(value), cases)
    assert called == []
    with pytest.raises(ValueError, match="G6_E_RESOURCE_LIMIT"):
        run_cases(lambda value: called.append(value), [BenchmarkCase("small", 1, 1)],
                  max_cases=0 if limit == "count" else 1,
                  max_suite_bytes=0 if limit != "count" else MAX_SUITE_BYTES)
    assert called == []


@pytest.mark.parametrize("error_type", [OSError, FileNotFoundError, PermissionError, ImportError, ModuleNotFoundError])
def test_environment_failure_cannot_pass_regression(error_type):
    # Removal makes this fail: `except (OSError, ImportError) as exc:` in run_cases.
    def unavailable(value):
        raise error_type("policy-checker is not installed; install it before retrying")

    cases = [BenchmarkCase("policy", "input", "42")]
    baseline, candidate = run_cases(unavailable, cases), run_cases(unavailable, cases)
    assert regression_delta(candidate, baseline).state == "unevaluable"
    for summary in (baseline, candidate):
        assert summary.evaluable is False and not summary.failing
        assert any(f"{error_type.__name__}: policy-checker is not installed; install it before retrying" in reason
                   for reason in summary.diagnostics)
