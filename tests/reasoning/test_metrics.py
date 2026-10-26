# SPDX-License-Identifier: Apache-2.0
import pytest


def test_metrics_compute_values_and_use_each_direction():
    # Removing metrics.py's direction-aware threshold comparison makes this fail.
    from g6.reasoning.metrics import evaluate

    result = evaluate([2, 5], [1, 3], {"mae": 1.6, "mse": 2.4})
    assert result["metrics"]["mae"]["value"] == 1.5
    assert result["metrics"]["mae"]["passed"] is True
    assert result["metrics"]["mse"]["value"] == 2.5
    assert result["metrics"]["mse"]["passed"] is False
    assert result["completion_state"] == "qualified-draft"


@pytest.mark.parametrize("bad", ["not numeric", float("nan"), float("inf"), True])
def test_numeric_scoring_errors_are_refusals_not_perfect_scores(bad):
    # Removing _number's boolean rejection makes the True case fail (ALIGN_EVALS-01, D1).
    from g6.reasoning.metrics import evaluate

    result = evaluate([bad], [1], {"mse": 0.1, "mae": 0.1})
    assert result["completion_state"] == "blocked-escalated"
    assert result["metrics"] == {} and result["warning_card"]["code"] == "G6_E_METRIC_INPUT"


def test_exact_interval_is_not_degenerate_for_one_correct_answer():
    # Removing metrics.py's exact interval inversion makes this fail.
    from g6.reasoning.metrics import exact_interval

    assert exact_interval(1, 1) == pytest.approx((0.025, 1.0))
    assert exact_interval(0, 1) == pytest.approx((0.0, 0.975))
    low, high = exact_interval(3, 3)
    assert low == pytest.approx(0.025 ** (1 / 3)) and high == 1


def test_metric_dependency_failure_refuses(monkeypatch):
    # Removing evaluate's computation-failure branch makes this fail.
    import g6.reasoning.metrics as metrics
    monkeypatch.setattr(metrics, "score", lambda *a: (_ for _ in ()).throw(RuntimeError("broken")))
    assert metrics.evaluate([1], [1], {"accuracy": 0.9})["completion_state"] == "blocked-escalated"


def test_bundle_uses_the_shared_metric_functions(monkeypatch):
    # Removing ml_check.py's shared-score call makes this fail.
    import g6.reasoning.metrics as metrics
    from g6.bundles.ml_check import _classification
    monkeypatch.setattr(metrics, "score", lambda name, p, y: 0.25)
    assert _classification(["a"], ["a"], [0]) == {"accuracy": 0.25, "f1": 0.25}

def test_bundle_accuracy_interval_is_exact():
    # Removing _intervals' exact_interval call makes this fail.
    from g6.bundles.ml_check import _classification, _intervals
    assert _intervals(_classification, ["yes"], ["yes"])["accuracy"] == pytest.approx((1, 0.025, 1))


def test_bundle_baseline_uses_paired_gate(monkeypatch):
    # Removing _beats_baseline's compare call makes this fail.
    import importlib
    from types import SimpleNamespace
    from g6.bundles.ml_check import _beats_baseline
    module = importlib.import_module("g6.reasoning.compare")
    calls = []
    monkeypatch.setattr(module, "compare", lambda *a: calls.append(a) or SimpleNamespace(promoted=False))
    assert _beats_baseline(["a"] * 30, ["a"] * 30, ["b"] * 30, False) is False
    assert len(calls[0][2]) == 30


@pytest.mark.parametrize("dependency", ["_intervals", "_beats_baseline"])
def test_bundle_failed_scoring_dependency_blocks(monkeypatch, dependency):
    # Removing run's scoring exception branch makes this fail.
    from g6.bundles import ml_check
    monkeypatch.setattr(ml_check, dependency, lambda *a: (_ for _ in ()).throw(RuntimeError("broken")))
    result = ml_check.run({"train": "x,y\n1,a\n2,b", "test": "x,y\n3,a\n4,b",
                          "target": "y", "predictions": "a\nb"})
    assert result.completion_state == "blocked-escalated"
    assert any(row["check"] == "scoring" for row in result.rows)

def test_interval_dependency_failure_refuses(monkeypatch):
    # Removing evaluate's interval-failure refusal makes this fail.
    import g6.reasoning.metrics as metrics
    monkeypatch.setattr(metrics, "exact_interval", lambda *a: (_ for _ in ()).throw(RuntimeError("broken")))
    assert metrics.evaluate([1], [1], {"accuracy": 0.9})["metrics"] == {}

def test_metric_known_values_use_observed_labels():
    # Removing score's per-class confusion counts makes this fail.
    from g6.reasoning.metrics import score
    truth, predictions = ["a", "a", "b", "b"], ["a", "b", "b", "b"]
    assert score("precision_macro", predictions, truth) == pytest.approx(5 / 6)
    assert score("recall_macro", predictions, truth) == pytest.approx(3 / 4)
    assert score("f1_macro", predictions, truth) == pytest.approx(11 / 15)
    assert score("f1_weighted", predictions, truth) == pytest.approx(11 / 15)
    assert score("f1_micro", predictions, truth) == pytest.approx(3 / 4)
    assert score("rmse", [2, 5], [1, 3]) == pytest.approx(2.5 ** 0.5)
