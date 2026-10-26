# SPDX-License-Identifier: Apache-2.0
"""The ML result check: leakage found, scores recomputed from the labels, claims compared. Offline, no model."""
from __future__ import annotations

from g6.bundles import catalog
from g6.bundles.ml_check import run
from g6.bundles.server import run_bundle
from g6.invariants import check_envelope


def _label(i: int) -> str:
    return "yes" if i % 4 == 0 else "no"


def _csv(rows: range, extra=None) -> str:
    head = "tenure,plan,churned" + (f",{extra[0]}" if extra else "")
    lines = [f"{i * 1.5 + 0.25},{'pro' if i % 2 else 'basic'},{_label(i)}" + (f",{extra[1](i)}" if extra else "")
             for i in rows]
    return "\n".join([head, *lines])


TRAIN, TEST = _csv(range(80)), _csv(range(80, 120))
GOOD = "\n".join({84: "no", 96: "no", 90: "yes"}.get(i, _label(i)) for i in range(80, 120))   # 3 errors in 40


def _checks(result) -> dict[str, dict]:
    return {row["check"]: row for row in result.rows}


def test_clean_data_and_a_true_claim_is_a_qualified_draft() -> None:
    r = run({"train": TRAIN, "test": TEST, "target": "churned", "predictions": GOOD, "claims": "accuracy 92.5%"})
    rows = _checks(r)
    assert r.completion_state == "qualified-draft" and "No leakage found" in r.summary
    assert rows["accuracy (recomputed)"]["detail"].startswith("0.925")
    assert rows["claimed accuracy"]["result"] == "ok" and rows["baseline"]["result"] == "ok"


def test_a_claim_the_labels_do_not_support_is_a_conflict() -> None:
    r = run({"train": TRAIN, "test": TEST, "target": "churned", "predictions": GOOD, "claims": "accuracy 0.99, f1 0.99"})
    rows = _checks(r)
    assert rows["claimed accuracy"]["result"] == "conflict" and r.completion_state == "blocked-escalated"
    assert r.to_dict()["warning_card"]["code"] == "G6_E_ML_CHECK_PROBLEM"


def test_test_rows_copied_from_training_are_found() -> None:
    leaky = TEST + "\n" + "\n".join(TRAIN.splitlines()[1:11])
    preds = GOOD + "\n" + "\n".join(_label(i) for i in range(10))
    rows = _checks(run({"train": TRAIN, "test": leaky, "target": "churned", "predictions": preds}))
    assert rows["duplicate rows"]["detail"].startswith("10 of 50")


def test_a_copy_of_the_target_and_a_perfect_predictor_are_found() -> None:
    copy = ("outcome_flag", _label)
    rows = _checks(run({"train": _csv(range(80), copy), "test": _csv(range(80, 120), copy), "target": "churned",
                        "predictions": GOOD}))
    assert "outcome_flag" in rows["target copy"]["detail"]
    status = ("status", lambda i: "closed" if i % 4 == 0 else ("open" if i % 3 else "paused"))
    rows = _checks(run({"train": _csv(range(80), status), "test": _csv(range(80, 120), status), "target": "churned",
                        "predictions": GOOD}))
    assert "status" in rows["perfect predictor"]["detail"]


def test_an_identifier_shared_by_train_and_test_is_found_but_a_measurement_is_not() -> None:
    rows = _checks(run({"train": _csv(range(80), ("customer_id", lambda i: f"C{i:03d}")),
                        "test": _csv(range(80, 120), ("customer_id", lambda i: f"C{i - 40:03d}")),
                        "target": "churned", "predictions": GOOD}))
    assert "customer_id" in rows["identifier shared"]["detail"]
    assert not any("tenure" in row["detail"] for row in rows.values())


def test_a_script_makes_predictions_without_seeing_the_test_labels() -> None:
    script = (
        "import csv\n"
        "rows = list(csv.DictReader(open('test_features.csv')))\n"
        "assert 'churned' not in rows[0]\n"
        "import os\nassert not os.path.exists('test.csv')\n"
        "open('predictions.csv', 'w').write('prediction\\n' + '\\n'.join("
        "'yes' if round((float(r['tenure']) - 0.25) / 1.5) % 4 == 0 else 'no' for r in rows))\n")
    r = run({"train": TRAIN, "test": TEST, "target": "churned", "script": script, "claims": "accuracy 1.0"})
    rows = _checks(r)
    assert r.completion_state == "qualified-draft" and rows["claimed accuracy"]["result"] == "ok"


def test_a_failing_script_or_a_wrong_count_blocks() -> None:
    failed = run({"train": TRAIN, "test": TEST, "target": "churned", "script": "raise SystemExit('no model here')"})
    assert _checks(failed)["script"]["result"] == "problem" and failed.completion_state == "blocked-escalated"
    short = run({"train": TRAIN, "test": TEST, "target": "churned", "predictions": "yes\nno"})
    assert "2 prediction(s) for 40" in _checks(short)["predictions"]["detail"]


def test_a_numeric_target_is_scored_by_error_against_the_mean() -> None:
    train = "x,y\n" + "\n".join(f"{i},{2 * i + 1}" for i in range(60))
    test = "x,y\n" + "\n".join(f"{i},{2 * i + 1}" for i in range(60, 90))
    preds = "\n".join(str(2 * i + 1.5) for i in range(60, 90))
    rows = _checks(run({"train": train, "test": test, "target": "y", "predictions": preds, "claims": "MAE 0.5"}))
    assert rows["mae (recomputed)"]["detail"].startswith("0.5") and rows["claimed mae"]["result"] == "ok"
    assert rows["baseline"]["result"] == "ok"


def test_a_model_that_wins_most_rows_but_has_a_worse_mae_than_the_mean_does_not_pass_the_baseline() -> None:
    # Removing run's `scores["mae"][2] < base` condition makes this fail (the paired gate alone promotes it).
    train = "x,y\n" + "\n".join(f"{i % 13},{i}" for i in range(41))               # targets 0 to 40, mean 20
    labels = [19.5 if i % 2 else 20.5 for i in range(40)]
    test = "x,y\n" + "\n".join(f"{100 + i},{y}" for i, y in enumerate(labels))
    preds = "\n".join(str(y + 100 if i < 4 else y) for i, y in enumerate(labels))   # exact on 36, 100 out on 4
    rows = _checks(run({"train": train, "test": test, "target": "y", "predictions": preds}))
    assert rows["mae (recomputed)"]["detail"].startswith("10 ")
    assert rows["baseline"]["result"] == "problem" and "MAE 0.5" in rows["baseline"]["detail"]


def test_a_test_set_over_the_scoring_limit_says_why_it_was_not_scored() -> None:
    # Removing run's test-row limit check makes this fail (the generic "metric or paired comparison failed" row).
    test = _csv(range(80, 100_081))
    preds = "\n".join(_label(i) for i in range(80, 100_081))
    rows = _checks(run({"train": TRAIN, "test": test, "target": "churned", "predictions": preds}))
    assert "100,001 test rows" in rows["scoring"]["detail"] and "100,000" in rows["scoring"]["detail"]
    assert rows["scoring"]["label"] == "blocked-escalated"


def test_missing_input_is_blocked_and_the_runner_reply_is_an_envelope() -> None:
    assert run({"train": TRAIN, "test": TEST, "target": "nope"}).completion_state == "blocked-escalated"
    reply = run_bundle(catalog()["ml-check"], {"train": TRAIN, "test": TEST, "target": "churned", "predictions": GOOD})
    assert check_envelope(reply) == [] and reply["completion_state"] == "qualified-draft"
