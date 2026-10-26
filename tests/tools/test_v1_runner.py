# SPDX-License-Identifier: Apache-2.0
"""The V1 study runner works end to end on its smoke fixture (which is not evaluation data)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[2] / "validation" / "v1_grounding"
_spec = importlib.util.spec_from_file_location("v1_run", HERE / "run.py")
v1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v1)


def test_v1_runner_smoke(tmp_path):
    assert v1.main([str(HERE / "SMOKE_FIXTURE_NOT_EVALUATION_DATA.csv"), "--out", str(tmp_path), "--relevance-threshold", "0.1"]) == 0
    report = json.loads((tmp_path / "v1_report.json").read_text())
    cc = report["arms"]["check_claim"]
    assert cc["false_support_rate"] == 0.0 and cc["quoted_recall"] == 1.0
    assert report["arms"]["relevance"]["false_support_rate"] == 1.0
    assert report["verdict"] == "inconclusive"   # 5 pairs can never meet the pre-registered bar


def test_wilson_bounds():
    lo, hi = v1.wilson(0, 200)
    assert lo == 0.0 and 0.0 < hi < 0.02


def test_an_arm_error_never_helps_the_arm():
    rows = [{"pair_id": "a", "stratum": "negated", "claim": "c", "source": "s", "label": "unsupported"},
            {"pair_id": "b", "stratum": "quoted", "claim": "c", "source": "s", "label": "supported"}]
    report = v1.evaluate(rows, {"broken": lambda claim, source: None})
    arm = report["arms"]["broken"]
    assert (arm["errors"], arm["false_support_rate"], arm["quoted_recall"]) == (2, 1.0, 0.0)
    assert report["per_pair"]["broken"] == {"a": None, "b": None}


def test_the_cluster_bootstrap_resamples_whole_cases():
    assert v1.cluster_bootstrap({"x": [False, False], "y": [False]}, resamples=200) == (0.0, 0.0)
    lo, hi = v1.cluster_bootstrap({str(i): [i < 10] for i in range(100)}, resamples=2000)
    assert lo < 0.1 < hi


# made-up rows in the dataset's shape (not VitaminC data)
_derive_spec = importlib.util.spec_from_file_location("v1_derive", HERE / "derive_pairs.py")
derive = importlib.util.module_from_spec(_derive_spec)
_derive_spec.loader.exec_module(derive)


def _row(uid, case, label, claim, evidence):
    return {"unique_id": uid, "case_id": case, "label": label, "claim": claim, "evidence": evidence,
            "revision_type": "real"}


def test_the_stratum_rule():
    assert derive.stratum(_row("1", "a", "NOT ENOUGH INFO", "X won .", "X played .")) == "near_topic"
    assert derive.stratum(_row("2", "b", "REFUTES", "X was n't born in Y .", "X was born in Y .")) == "negated"
    assert derive.stratum(_row("3", "c", "REFUTES", "X has 40 staff .", "X has 41 staff .")) == "wrong_figure"
    # a negation only in a long passage, or a figure contrast, is not the negated stratum (the dev-split finding)
    assert derive.stratum(_row("4", "d", "REFUTES", "X had 60 cases .", "X had 61 cases , not all counted .")) == (
        "wrong_figure")
    assert derive.stratum(_row("5", "e", "REFUTES", "X is blue .", "X is red .")) is None
    assert derive.stratum(_row("6", "f", "SUPPORTS", "X is a film .", "X is a 1950 film .")) == "quoted"
    assert derive.stratum(_row("7", "g", "SUPPORTS", "X premiered in winter .", "X opened in January .")) == (
        "paraphrased")


def test_one_pair_per_case_and_a_seeded_draw():
    rows = [_row(f"{i}", f"case{i // 2}", "NOT ENOUGH INFO", "c", "e") for i in range(10)]
    chosen, sizes = derive.derive(rows, seed=1, per_stratum=3)
    assert sizes["near_topic"] == {"qualifying_cases": 5, "drawn": 3}
    assert chosen == derive.derive(rows, seed=1, per_stratum=3)[0]
    assert len({c["case_id"] for c in chosen}) == 3
