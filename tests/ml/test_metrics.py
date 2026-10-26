# SPDX-License-Identifier: Apache-2.0
"""The family's scores (g6.ml._metrics) against the worked examples of their own sources (the scikit-learn User
Guide entries in g6/ml/_data/source_register.json), and against scikit-learn itself where it is installed."""
from __future__ import annotations

import importlib.util
import math
import random

import pytest

from g6.ml import _metrics as m


def test_known_values_from_the_sources_worked_examples():
    assert m.accuracy([0, 1, 2, 3], [0, 2, 1, 3]) == 0.5                      # 3.4.4.2
    truth, pred = [3, -0.5, 2, 7], [2.5, 0.0, 2, 8]                            # 3.4.6.1 to 3.4.6.3
    assert math.floor(m.r2(truth, pred) * 1000) / 1000 == 0.948             # the guide prints 0.948...
    assert m.mae(truth, pred) == 0.5
    assert math.isclose(m.rmse(truth, pred) ** 2, 0.375)


def test_balanced_accuracy_is_the_mean_recall_per_class():
    # from 3.4.4.4's definition: recall 3/4 for class 0 and 1/2 for class 1
    assert m.balanced_accuracy([0, 1, 0, 0, 1, 0], [0, 1, 0, 0, 0, 1]) == 0.625
    # it equals accuracy on balanced data, and drops to 1 / n_classes for a predictor that exploits imbalance
    assert m.balanced_accuracy(["a", "b", "a", "b"], ["a", "a", "a", "b"]) == m.accuracy(
        ["a", "b", "a", "b"], ["a", "a", "a", "b"])
    truth = ["no"] * 95 + ["yes"] * 5
    assert m.accuracy(truth, ["no"] * 100) == 0.95 and m.balanced_accuracy(truth, ["no"] * 100) == 0.5


def test_r_squared_is_undefined_for_constant_targets_and_zero_for_the_mean():
    assert m.r2([2.0, 2.0, 2.0], [1.0, 2.0, 3.0]) is None
    truth = [1.0, 2.0, 4.0, 7.0]
    assert m.r2(truth, [3.5] * 4) == 0.0


def test_r_squared_is_undefined_for_constant_float_targets_whose_float_mean_is_off():
    # Removing r2's exact all-equal test leaves the float mean's rounding as a tiny positive total sum of squares.
    assert math.fsum([0.1] * 3) / 3 != 0.1                  # the float mean is not the value itself
    assert m.r2([0.1] * 3, [0.1] * 3) is None               # was 1.0
    assert m.r2([0.1] * 3, [0.2] * 3) is None               # was about -5.2e31
    rng = random.Random(0)
    scored = 0
    for _ in range(2000):
        value, n = rng.uniform(-1000, 1000), rng.randrange(10, 60)
        scored += m.r2([value] * n, [value + 1] * n) is not None
    assert scored == 0                                      # every constant target is undefined, whatever its value


def test_the_baseline_ignores_the_features():
    assert m.baseline("classification", ["b", "a", "b", "a", "c"], 3) == ["a"] * 3   # a tie goes to the first sorted
    assert m.baseline("regression", [1.0, 2.0, 6.0], 2) == [3.0, 3.0]


def test_compare_reports_the_lift_its_interval_and_the_gap_reproducibly():
    rng = random.Random(1)
    truth = [rng.choice("ab") for _ in range(60)]
    good = [t if rng.random() < 0.9 else ("b" if t == "a" else "a") for t in truth]
    base = m.baseline("classification", truth, len(truth))
    out = m.compare("classification", "accuracy", truth, good, base, truth, truth, seed=3)
    again = m.compare("classification", "accuracy", truth, good, base, truth, truth, seed=3)
    assert out == again and out["clearly_better"] and out["interval"]["lift_low"] > 0
    assert out["interval"]["low"] <= out["held_out"] <= out["interval"]["high"]
    assert out["train"] == 1.0 and out["gap"] == round(1.0 - out["held_out"], 6)
    same = m.compare("classification", "accuracy", truth, base, base, seed=3)
    assert same["lift"] == 0 and not same["clearly_better"]


def test_resampling_the_largest_held_out_set_allowed_stays_under_its_cap_and_says_so():
    from g6.ml import _types

    n = int(_types.MAX_ROWS * 0.5)                       # held_out_fraction is at most 0.5
    rng = random.Random(2)
    truth = [rng.choice("ab") for _ in range(n)]
    pred = [t if rng.random() < 0.8 else ("b" if t == "a" else "a") for t in truth]
    out = m.compare("classification", "accuracy", truth, pred, ["a"] * n, seed=1)
    interval = out["interval"]
    assert interval["resamples"] * n <= m.MAX_RESAMPLE_WORK and interval["resamples_asked"] == m.RESAMPLES
    assert f"{interval['resamples']} resamples, not {m.RESAMPLES}" in interval["lowered"]
    small = m.compare("classification", "accuracy", truth[:200], pred[:200], ["a"] * 200, seed=1)["interval"]
    assert small["resamples"] == m.RESAMPLES and "lowered" not in small


def test_lower_is_better_metrics_lift_the_right_way():
    assert m.lift("mae", 1.0, 3.0) == 2.0 and m.lift("r2", 0.8, 0.0) == 0.8


@pytest.mark.skipif(importlib.util.find_spec("sklearn") is None, reason="scikit-learn (the ml extra) is not installed")
def test_g6s_scores_agree_with_scikit_learns_own():
    from sklearn import metrics as sk

    rng = random.Random(5)
    truth = [rng.choice(["x", "y", "z"]) for _ in range(200)]
    pred = [rng.choice(["x", "y", "z"]) for _ in range(200)]
    assert math.isclose(m.accuracy(truth, pred), sk.accuracy_score(truth, pred))
    assert math.isclose(m.balanced_accuracy(truth, pred), sk.balanced_accuracy_score(truth, pred))
    yt = [rng.gauss(0, 3) for _ in range(200)]
    yp = [v + rng.gauss(0, 1) for v in yt]
    assert math.isclose(m.r2(yt, yp), sk.r2_score(yt, yp))
    assert math.isclose(m.mae(yt, yp), sk.mean_absolute_error(yt, yp))
    assert math.isclose(m.rmse(yt, yp), sk.root_mean_squared_error(yt, yp))
