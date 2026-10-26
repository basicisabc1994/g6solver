# SPDX-License-Identifier: Apache-2.0
"""Regression coverage for the core-r3 review corrections."""
import pytest

from g6.harness import Check, Memory, Standard, evaluate_cases, run
from g6.reliability_pipeline.task_contract import SuccessCriterion


def _standard(check=None):
    return Standard("Summarise the refund policy", (check or Check(
        "states the right value", lambda output: output == "42", check_id="value",
        known_bad=("41",), known_good="42"),), family="review")


@pytest.mark.parametrize("unavailable", ["baseline", "candidate", "unknown"])
def test_unavailable_comparison_never_earns_publishable(unavailable):
    # Removal makes this fail: `compared = bool(... and not unavailable)` in readiness_report.
    def missing(contract, value):
        raise FileNotFoundError("policy-checker is not installed")

    check = Check("states the right value", lambda output: "unknown" if output == "pending" else output == "42",
                  check_id="value", known_bad=("41",), known_good="42")
    candidate = missing if unavailable == "candidate" else lambda contract, value: "42"
    baseline = ((lambda contract, value: "42") if unavailable == "candidate" else
                (lambda contract, value: "pending") if unavailable == "unknown" else missing)
    memory = Memory(":memory:")
    try:
        report = evaluate_cases(_standard(check), {"held": "new policy"}, candidate,
                                baseline_executor=baseline, memory=memory)
        assert report["tier"] != "publishable"
        assert report["baseline_compared"] is False and report["outperforms_baseline"] is None
        assert report["baseline_pass_rate"] == (1.0 if unavailable == "candidate" else None)
        assert report["comparison_unavailable"]
        diagnostic = " ".join(report["comparison_unavailable"])
        if unavailable == "unknown":
            assert "unknown" in diagnostic
        else:
            assert "FileNotFoundError: policy-checker is not installed" in diagnostic
        if unavailable == "candidate":
            assert report["held_out_pass_rate"] is None
    finally:
        memory.close()


@pytest.mark.parametrize("mutator", ["candidate", "baseline"])
def test_mutated_control_is_not_frozen_or_reusable(mutator):
    # Removal makes this fail: `stable = stable and controls_still_fail` in evaluate_cases.
    accepted = {"42"}
    check = Check("states the right value", lambda output: output in accepted,
                  check_id="value", known_bad=("41",), known_good="42")

    def mutate(contract, value):
        accepted.add("41")
        return "41"

    memory = Memory(":memory:")
    try:
        report = evaluate_cases(_standard(check), {"held": "new policy"},
                                mutate if mutator == "candidate" else lambda contract, value: "42",
                                baseline_executor=mutate if mutator == "baseline" else None, memory=memory)
        assert report["checks_frozen"] is False and report["known_bad_tested"] is False
        assert report["tier"] not in {"reusable", "publishable"}
    finally:
        memory.close()


@pytest.mark.parametrize("limit", ["count", "bytes"])
def test_harness_oversized_suite_is_refused_before_copy_or_execution(limit):
    # Removal makes this fail: `check_case_bounds(len(cases), lambda: cases.items(), ...)` before dict(cases).
    MAX_CASES, MAX_SUITE_BYTES = 1000, 1024 * 1024

    class UncopiedCases(dict):
        def keys(self):
            raise AssertionError("suite copied before its count was checked")

        def __iter__(self):
            raise AssertionError("suite iterated before its count was checked")

    cases = UncopiedCases({str(i): str(i) for i in range(MAX_CASES + 1)}) if limit == "count" else {
        "large": "x" * (MAX_SUITE_BYTES + 1)}
    called = []
    memory = Memory(":memory:")
    try:
        with pytest.raises(ValueError, match="G6_E_RESOURCE_LIMIT"):
            evaluate_cases(_standard(), cases, lambda contract, value: called.append(value), memory=memory)
        assert called == [] and memory._conn is None
        with pytest.raises(ValueError, match="G6_E_RESOURCE_LIMIT"):
            evaluate_cases(_standard(), {"small": "policy"}, lambda contract, value: called.append(value),
                           max_cases=0 if limit == "count" else 1,
                           max_suite_bytes=0 if limit == "bytes" else MAX_SUITE_BYTES, memory=memory)
        assert called == [] and memory._conn is None
    finally:
        memory.close()


def test_duplicate_predicates_with_conflicting_criticality_are_refused():
    # Removal makes this fail: `if duplicate_predicates:` in _run before intake.
    checks = (Check("has a source", lambda output: True, check_id="source-critical"),
              Check("has a source", lambda output: True, check_id="source-optional"))
    standard = Standard("Summarise the refund policy", checks, criteria=(
        SuccessCriterion("has a source", critical=True), SuccessCriterion("has a source", critical=False)))
    called = []
    memory = Memory(":memory:")
    try:
        report = run(standard, lambda contract: called.append(contract) or "policy", memory=memory)
        assert called == [] and report["completion_state"] == "blocked-escalated"
        assert report["warning_card"]["code"] == "G6_E_HARNESS_CRITERIA"
        assert all(check.key in report["warning_card"]["message"] for check in checks)
    finally:
        memory.close()
