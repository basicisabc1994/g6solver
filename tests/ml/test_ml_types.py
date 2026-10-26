# SPDX-License-Identifier: Apache-2.0
"""The ml family's shared code (g6.ml._types): the split the extension makes itself, and the checks run before any
score is reported (target leakage, identical rows across the split, the held-out size, the seed, class imbalance)."""
from __future__ import annotations

import dataclasses
import math
import random

import pytest
from pydantic import ValidationError

from g6.ml import _fit, _report
from g6.ml import _types as t


def _regression(n: int = 60, seed: int = 0, leak: str = "") -> list[dict]:
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        x1, x2 = rng.uniform(0, 10), rng.uniform(-5, 5)
        y = 3 * x1 - 2 * x2 + rng.gauss(0, 4)
        row = {"x1": round(x1, 3), "x2": round(x2, 3), "y": round(y, 3)}
        if leak == "shift":
            row["recorded_later"] = round(y + 17.5, 3)
        elif leak == "rank":
            row["recorded_later"] = math.exp(y / 10)
        rows.append(row)
    return rows


def _classification(n: int = 60, seed: int = 0, minority: float = 0.4) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        churned = rng.random() < minority
        rows.append({"tenure": round(rng.gauss(10 if churned else 30, 8), 2),
                     "plan": rng.choice(["basic", "plus"] if churned else ["plus", "pro"]),
                     "churned": "yes" if churned else "no"})
    return rows


def _spec(rows: list[dict], target: str, features: list[str], task: str, **kw) -> t.DatasetSpec:
    """The rows cut to the declared columns, as a caller must send them."""
    declared = {target, *features}
    rows = [{k: v for k, v in r.items() if k in declared} for r in rows]
    return t.DatasetSpec(rows=rows, target=target, features=features, task=task, **{"seed": 7, **kw})


def test_the_headline_metric_is_chosen_from_the_training_rows_only():
    # Removing split.train from prepare's class_balance call lets the held-out labels choose the comparison metric.
    rows = [{"x": i, "y": int(i % 10 == 0)} for i in range(50)]          # 45 zeros and 5 ones
    spec = _spec(rows, "y", ["x"], "classification", held_out_fraction=0.3)
    prepared = t.prepare(spec)
    assert prepared.split.class_counts["train"] == {"0": 31, "1": 3}
    assert t.class_balance(spec)["headline_metric"] == "accuracy"        # every row: 5 of 50, 10%, not under 10%
    assert prepared.balance["minority_share"] == round(3 / 34, 6)       # the training rows: 8.8%
    assert _report.headline_metric(prepared) == "balanced_accuracy"


# --- D1: target leakage ---------------------------------------------------------------------------------------------

def test_d1_a_feature_that_is_the_target_shifted_by_a_constant_is_refused_for_leakage():
    spec = _spec(_regression(leak="shift"), "y", ["x1", "x2", "recorded_later"], "regression")
    with pytest.raises(t.MLError) as caught:
        t.prepare(spec)
    assert caught.value.code == t.LEAKAGE and "recorded_later" in caught.value.reason
    probes = next(e for e in caught.value.evidence if e["kind"] == "ml_leakage_probes")
    assert [f["feature"] for f in probes["suspected"]] == ["recorded_later"]


def test_d1_cases_the_check_was_not_written_around_are_refused_too():
    # a non-linear (monotone) copy of a numeric target, caught by rank correlation
    spec = _spec(_regression(leak="rank"), "y", ["x1", "recorded_later"], "regression")
    with pytest.raises(t.MLError) as caught:
        t.prepare(spec)
    assert caught.value.code == t.LEAKAGE and "recorded_later" in caught.value.reason
    # a text label re-coded as numbers: no correlation applies, the single-feature probe on held-out rows catches it
    rows = _classification()
    for row in rows:
        row["cancel_flag"] = 1 if row["churned"] == "yes" else 0
    spec = _spec(rows, "churned", ["tenure", "cancel_flag"], "classification")
    with pytest.raises(t.MLError) as caught:
        t.prepare(spec)
    assert caught.value.code == t.LEAKAGE and "cancel_flag" in caught.value.reason
    # the target itself named as a feature
    spec = _spec(_regression(), "y", ["x1", "y"], "regression")
    with pytest.raises(t.MLError) as caught:
        t.prepare(spec)
    assert caught.value.code == t.LEAKAGE


def test_honest_features_are_not_taken_for_leaks():
    for spec in (_spec(_regression(), "y", ["x1", "x2"], "regression"),
                 _spec(_classification(), "churned", ["tenure", "plan"], "classification")):
        prepared = t.prepare(spec)
        probes = next(e for e in prepared.evidence if e["kind"] == "ml_leakage_probes")
        assert probes["suspected"] == [] and probes["features_probed"] == len(spec.features)


def test_a_strong_honest_feature_is_not_a_leak_because_a_small_held_out_part_happens_to_be_perfect():
    # tenure alone may get a small held-out part all right by chance, but not every row from the others
    rng = random.Random(11)
    rows = []
    for _ in range(64):
        churned = rng.random() < 0.4
        rows.append({"tenure": max(1, round(rng.gauss(10 if churned else 30, 9))),
                     "plan": rng.choice(["basic", "plus"] if churned else ["plus", "pro"]),
                     "churned": "yes" if churned else "no"})
    prepared = t.prepare(_spec(rows, "churned", ["tenure", "plan"], "classification"))
    assert next(e for e in prepared.evidence if e["kind"] == "ml_leakage_probes")["suspected"] == []


# --- the split ------------------------------------------------------------------------------------------------------

def test_p0_the_held_out_rows_come_from_the_rows_own_hash_never_the_callers_seed():
    spec = _spec(_classification(80), "churned", ["tenure", "plan"], "classification")
    one, two = t.make_split(spec), t.make_split(spec)
    assert one == two and set(one.train).isdisjoint(one.held_out)
    assert sorted(one.train + one.held_out) == list(range(80))
    for seed in (8, 9, 10):                         # retrying seeds never moves the held-out rows
        assert t.make_split(spec.model_copy(update={"seed": seed})).held_out == one.held_out
    said = one.evidence()
    assert "seed" not in said and said["drawn_from"] == "rows_sha256" and "never the caller" in said["made_by"]
    assert said["train_rows"] + said["held_out_rows"] == 80 and said["rows_sha256"] == spec.rows_sha256()
    assert len(said["held_out_sha256"]) == 64 and said["held_out_sha256"] != said["rows_sha256"]


def test_the_split_is_stratified_by_class():
    rows = _classification(200, minority=0.2)
    spec = _spec(rows, "churned", ["tenure", "plan"], "classification")
    split = t.make_split(spec)
    whole = sum(r["churned"] == "yes" for r in rows) / len(rows)
    held = split.class_counts["held_out"]["yes"] / len(split.held_out)
    assert abs(held - whole) < 0.03
    assert split.strategy.startswith("stratified by class")


def test_identical_rows_are_kept_on_one_side_and_a_crossing_is_caught():
    base = _regression(30)
    rows = base + [dict(r) for r in base[:15]]   # fifteen rows given twice
    spec = _spec(rows, "y", ["x1", "x2"], "regression")
    split = t.make_split(spec)
    assert split.duplicate_rows == 15 and t.duplicates_across(spec, split) == 0
    crossing = t.Split(train=(0,), held_out=(30,), fraction=.25, strategy="", duplicate_rows=15, rows_sha256="",
                       held_out_sha256="")
    assert t.duplicates_across(spec, crossing) == 1


def _grid(n: int, seed: int) -> list[dict]:
    """Rows whose two features take ten values each, so no feature alone predicts the target, nor its own copy."""
    rng = random.Random(seed)
    cells = rng.sample([(a, b) for a in range(10) for b in range(10)], n)
    return [{"a": a, "b": b, "y": round(a + b + rng.gauss(0, 3), 3)} for a, b in cells]


def test_p0_repeating_every_row_does_not_meet_the_held_out_minimum():
    rows = _grid(30, seed=1)
    with pytest.raises(t.MLError) as caught:
        t.prepare(_spec(rows, "y", ["a", "b"], "regression"))
    assert caught.value.code == t.TOO_FEW_HELD_OUT
    with pytest.raises(t.MLError) as caught:                 # the same 30 observations, each given twice
        t.prepare(_spec(rows + [dict(r) for r in rows], "y", ["a", "b"], "regression"))
    assert caught.value.code == t.TOO_FEW_HELD_OUT and "independent" in caught.value.reason


def test_p0_repeating_every_held_out_row_neither_narrows_the_interval_nor_adds_an_independent_row(monkeypatch,
                                                                                                 tmp_path):
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    rows = _grid(48, seed=4)
    prepared = t.prepare(_spec(rows, "y", ["a", "b"], "regression"))
    held, train = prepared.split.held_out, prepared.split.train
    encoded = _fit.encode(prepared)
    rng = random.Random(9)
    pred = [encoded.y[i] + rng.gauss(0, 2) for i in held]
    train_pred = [encoded.y[i] for i in train]
    one = _report.measure(prepared, encoded, pred, train_pred, op="test")["headline"]
    twice = rows + [dict(rows[i]) for i in held]               # each held-out row given again, on the held-out side
    spec = _spec(twice, "y", ["a", "b"], "regression")
    split = dataclasses.replace(prepared.split, held_out=held + tuple(range(len(rows), len(twice))))
    doubled = dataclasses.replace(prepared, spec=spec, split=split)
    two = _report.measure(doubled, _fit.encode(doubled), pred + pred, train_pred, op="test")["headline"]
    assert two["held_out_rows"] == 2 * one["held_out_rows"]
    shown = ("low", "high", "lift_low", "lift_high")
    assert {k: two["interval"][k] for k in shown} == {k: one["interval"][k] for k in shown}
    assert two["held_out_groups"] == one["held_out_groups"] == len(held)
    assert "group" in two["interval"]["method"]


def test_the_caller_cannot_hand_in_a_split():
    for extra in ({"test_rows": [0, 1]}, {"split": "given"}, {"held_out": [3]}, {"verified": True}):
        with pytest.raises(ValidationError):
            t.DatasetSpec(rows=_regression(), target="y", features=["x1"], task="regression", seed=1, **extra)


# --- the seed, the columns, the size --------------------------------------------------------------------------------

def test_a_missing_seed_or_a_second_seed_is_refused():
    spec = t.DatasetSpec(rows=_regression(), target="y", features=["x1"], task="regression")
    with pytest.raises(t.MLError) as caught:
        t.prepare(spec)
    assert caught.value.code == t.SEED_MISSING
    with pytest.raises(t.MLError) as caught:
        t.prepare(_spec(_regression(), "y", ["x1"], "regression"), {"random_state": None})
    assert caught.value.code == t.SEED_MISSING and "random_state" in caught.value.reason


def test_missing_answers_and_mixed_columns_are_refused_never_filled_in():
    rows = _regression()
    rows[3]["y"] = None
    with pytest.raises(t.MLError) as caught:
        t.prepare(_spec(rows, "y", ["x1"], "regression"))
    assert caught.value.code == t.INPUT_INVALID and "row 3" in caught.value.reason
    rows = _regression()
    rows[5]["x1"] = "n/a"
    with pytest.raises(t.MLError) as caught:
        t.prepare(_spec(rows, "y", ["x1"], "regression"))
    assert caught.value.code == t.INPUT_INVALID and "mixed" in caught.value.reason
    with pytest.raises(t.MLError) as caught:
        t.prepare(_spec(_regression(), "y", ["nowhere"], "regression"))
    assert "nowhere" in caught.value.reason


def test_too_few_held_out_rows_are_refused():
    with pytest.raises(t.MLError) as caught:
        t.prepare(_spec(_regression(20), "y", ["x1"], "regression"))
    assert caught.value.code == t.TOO_FEW_HELD_OUT and f"fewer than {t.MIN_HELD_OUT}" in caught.value.reason


def test_a_class_too_small_to_learn_and_score_is_refused():
    rows = _classification(60, minority=0.0)
    rows[0]["churned"] = "yes"
    with pytest.raises(t.MLError) as caught:
        t.prepare(_spec(rows, "churned", ["tenure"], "classification"))
    assert caught.value.code == t.INPUT_INVALID and "'yes'" in caught.value.reason


def test_integer_class_labels_keep_every_digit_and_come_back_exactly():
    big = 2 ** 53                                   # as floats, 2**53 and 2**53 + 1 are one number
    rng = random.Random(5)
    rows = [{"x": round(rng.uniform(0, 10), 3), "y": big + (i % 2)} for i in range(60)]
    encoded = _fit.encode(t.prepare(_spec(rows, "y", ["x"], "classification")))
    assert sorted(encoded.labels.values()) == [big, big + 1]
    assert [encoded.labels[k] for k in encoded.y] == [row["y"] for row in rows]
    shown = _report.predictions(encoded, "classification", encoded.y[:2])
    assert [p["prediction"] for p in shown] == [big, big + 1] and all(type(p["prediction"]) is int for p in shown)


# --- class imbalance ------------------------------------------------------------------------------------------------

def test_class_imbalance_is_reported_with_its_threshold_and_changes_the_headline():
    rows = _classification(200, seed=3, minority=0.06)
    balance = t.class_balance(_spec(rows, "churned", ["tenure"], "classification"))
    assert balance["imbalanced"] and balance["threshold"] == t.IMBALANCE_SHARE == 0.10
    assert balance["headline_metric"] == "balanced_accuracy" and balance["minority_share"] < 0.10
    even = t.class_balance(_spec(_classification(200), "churned", ["tenure"], "classification"))
    assert not even["imbalanced"] and even["headline_metric"] == "accuracy"


# --- the envelope ---------------------------------------------------------------------------------------------------

def test_g1_a_result_is_a_qualified_draft_at_best_and_a_refusal_carries_its_code():
    done = t.answer("fit_eval", "a measurement")
    assert done.completion_state == "qualified-draft" and done.warning_card["code"] == t.MEASURED
    with pytest.raises(ValidationError):
        t.MLResult(operation="x", completion_state="verified", warning_card={}, evidence=[])
    with pytest.raises(ValidationError):
        t.MLResult(operation="x", completion_state="blocked-escalated", warning_card={}, evidence=[])
    refused = t.refuse("fit_eval", t.LEAKAGE, "a leak")
    assert refused.completion_state == "blocked-escalated" and refused.evidence[-1]["code"] == t.LEAKAGE


def test_a_missing_extra_says_what_to_install():
    assert t.missing_extra("ml", ("json",), "json") == ""
    said = t.missing_extra("ml", ("no_such_module_g6",), "scikit-learn")
    assert "not installed" in said and "pip install 'g6[ml]'" in said
