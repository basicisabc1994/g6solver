# SPDX-License-Identifier: Apache-2.0
"""D4: missing oracle terms and missing workflow self-tests remain visible."""
from __future__ import annotations

import pytest

from v6_metaharness import analyze as a


def selftest(n=20):
    return {"eval": {"n": n, "reference_failed": [], "empty_passed": []},
            "gate": {"n": n, "reference_failed": [], "empty_passed": []}}


def favorable(oracle):
    endpoint = {"wins": 20, "losses": 0, "diff_mean": 1.0, "ci95_cluster_bootstrap": (0.8, 1.0)}
    return {"oracle": oracle, "contamination": {"evaluator_mutation": False, "leakage": False},
            "protected": {"breached": False}, "dev_gain": True,
            "b_failures": {"n": 20, "failures": 20, "share": 1.0},
            "endpoints": {"E2_false_accepts": {"rose": False, "d": 0, "b": 0},
                          "E1_success_D_vs_B": dict(endpoint), "D_vs_E": dict(endpoint), "D_vs_F": dict(endpoint),
                          "holm_adjusted": {"D>B": 0.001, "D>E": 0.001, "D>F": 0.001}}}


def test_missing_published_error_is_unknown_even_with_favorable_endpoints(monkeypatch):
    monkeypatch.setattr(a, "PUBLISHED_ANSWER_ERROR", {})
    oracle = a.oracle_check({"tasks": {"oracle_selftest": selftest()}}, {"tasks": {}})
    assert oracle["pooled_known_error_bound"] is None
    assert oracle["identification"] == "not_identified"
    result, trail = a.verdict(favorable(oracle))
    assert result == "inconclusive"
    assert any("unknown" in line for line in trail)


def test_missing_expected_selftest_stays_in_coverage(monkeypatch):
    monkeypatch.setattr(a, "PUBLISHED_ANSWER_ERROR", {"present": 0.0, "missing": 0.0})
    oracle = a.oracle_check({"present": {"oracle_selftest": selftest()}}, {"present": {}, "missing": {}})
    assert "missing" in oracle["per_workflow"]
    assert oracle["missing_selftests"] == ["missing"]
    assert oracle["coverage_complete"] is False
    assert oracle["pooled_known_error_bound"] is None
    assert a.verdict(favorable(oracle))[0] == "inconclusive"


@pytest.mark.parametrize("n", [0, None])
def test_missing_reference_measurement_is_unknown(monkeypatch, n):
    monkeypatch.setattr(a, "PUBLISHED_ANSWER_ERROR", {"tasks": 0.0})
    st = selftest(n)
    oracle = a.oracle_check({"tasks": {"oracle_selftest": st}}, {"tasks": {}})
    assert oracle["per_workflow"]["tasks"]["reference_failure_share"] is None
    assert oracle["pooled_known_error_bound"] is None


def test_explicit_measured_zero_survives_and_reports_observed_outcomes_separately(monkeypatch):
    monkeypatch.setattr(a, "PUBLISHED_ANSWER_ERROR", {"tasks": 0.0})
    oracle = a.oracle_check({"tasks": {"oracle_selftest": selftest()}}, {"tasks": {}})
    assert oracle["pooled_known_error_bound"] == 0.0
    assert oracle["coverage_complete"] is True
    ctx = favorable(oracle)
    result, trail = a.verdict(ctx)
    # A shared reference self-test does not establish errors on the two harness populations.
    assert result == "inconclusive"
    assert ctx["endpoints"]["E1_success_D_vs_B"]["diff_mean"] == 1.0
    assert any("observed evaluator-outcome" in line for line in trail)


def test_missing_arm_specific_bounds_do_not_become_true_success_even_with_known_pooled_error(monkeypatch):
    monkeypatch.setattr(a, "PUBLISHED_ANSWER_ERROR", {"tasks": 0.02})
    oracle = a.oracle_check({"tasks": {"oracle_selftest": selftest()}}, {"tasks": {}})
    assert oracle["pooled_known_error_bound"] == 0.02
    assert a.verdict(favorable(oracle))[0] == "inconclusive"


def test_an_observed_selftest_failure_still_reports_oracle_inadequacy(monkeypatch):
    monkeypatch.setattr(a, "PUBLISHED_ANSWER_ERROR", {})
    st = selftest()
    st["eval"]["empty_passed"] = ["case"]
    oracle = a.oracle_check({"tasks": {"oracle_selftest": st}}, {"tasks": {}})
    assert a.verdict(favorable(oracle))[0] == "oracle_inadequate"


def test_analyze_separates_observed_differences_from_true_success(tmp_path, monkeypatch):
    monkeypatch.setattr(a, "PUBLISHED_ANSWER_ERROR", {"tasks": 0.0})
    arms = {arm: {str(i): {"success": arm == "D", "released": True, "false_accept": False,
                          "model_calls": 1, "wall_clock_s": 1.0} for i in range(20)}
            for arm in ("B", "D", "E", "F")}
    adaptation = {"tasks": {"D": None, "F": None, "C": None, "E": None, "oracle_selftest": selftest()}}
    monkeypatch.setattr(a, "load", lambda out: ({"tasks": arms}, adaptation, {}))
    ctx = a.analyze(tmp_path)
    observed = ctx["observed_evaluator_outcomes"]
    assert observed["comparisons"]["E1_success_D_vs_B"]["diff_mean"] == 1.0
    assert observed["true_success_identification"] == "not_identified"
    assert observed["corrected_gain_lower_bound"] is None
    assert ctx["verdict"] == "inconclusive"


@pytest.mark.parametrize("section,field", [("eval", "reference_failed"), ("eval", "empty_passed"),
                                          ("gate", "reference_failed")])
def test_a_missing_selftest_field_is_not_a_measured_zero(monkeypatch, section, field):
    monkeypatch.setattr(a, "PUBLISHED_ANSWER_ERROR", {"tasks": 0.0})
    st = selftest()
    del st[section][field]
    oracle = a.oracle_check({"tasks": {"oracle_selftest": st}}, {"tasks": {}})
    assert oracle["coverage_complete"] is False
    assert oracle["pooled_known_error_bound"] is None
