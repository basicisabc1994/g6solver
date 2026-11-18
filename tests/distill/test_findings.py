# SPDX-License-Identifier: Apache-2.0
"""Product ledger preserves the original RCA's counting and class semantics."""
import importlib
import importlib.util
from pathlib import Path

import pytest


def original():
    path = Path(__file__).resolve().parents[2] / "validation/own_code/ledger.py"
    spec = importlib.util.spec_from_file_location("original_findings", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample():
    rows = [dict(folder=f"review-{branch}{round_}", finding=n, branch=branch, round=round_)
            for branch in ("rp", "k") for round_ in (1, 2, 3) for n in (1, 2)]
    labels = {(row["folder"], row["finding"]): dict(mechanism=(
        "one_fact_two_rules" if row["finding"] == 1 else "other"), other=f"named-{row['round'] % 2}")
        for row in rows}
    return rows, labels


def test_catalogue_and_met_before_match_original():
    product = importlib.import_module("g6.distill.findings")
    old = original()
    assert product.CLASSES == old.CLASSES
    assert product.CHECKS == old.CHECKS
    rows, labels = sample()
    for row in rows:
        assert product.met_before(rows, labels, row) == old.met_before(rows, labels, row)
    # Another branch counts; peers in the same round do not.
    assert ("review-rp1", 1) not in product.met_before(rows, labels, rows[0])
    assert ("review-k1", 1) in product.met_before(rows, labels, rows[0])


@pytest.mark.parametrize("seed", [0, 1, 42])
def test_shuffle_statistic_is_exactly_the_original(seed):
    product = importlib.import_module("g6.distill.findings")
    rows, labels = sample()
    assert product.by_chance(rows, labels, draws=5000, seed=seed) == original().by_chance(
        rows, labels, draws=5000, seed=seed)


def test_report_reader_preserves_rows_and_clean_reports(tmp_path):
    product = importlib.import_module("g6.distill.findings")
    for name, text in (("review-rp1", "## Finding 1\nRule: once\nClass: boundary_case\n"
                       "Why: a.py:2\nTest: tests/a.py\n\n## Looked at\nall\n"),
                       ("review-rp2", "No reproduced findings.\n")):
        folder = tmp_path / name / ".review"
        folder.mkdir(parents=True)
        (folder / "report.md").write_text(text, encoding="utf-8")
    assert product.findings([tmp_path]) == original().findings([tmp_path])


def test_removed_classes_are_below_chance():
    product = importlib.import_module("g6.distill.findings")
    rows = [dict(folder=f"r{k}", finding=n, branch="b", round=k) for k in (1, 2, 3) for n in (1, 2)]
    labels = {(row["folder"], row["finding"]): dict(mechanism=(
        "boundary_case", "state_not_durable", "one_fact_two_rules")[row["round"] - 1]) for row in rows}
    got = product.by_chance(rows, labels, draws=500, seed=1)
    assert got["observed"] == 0 and got["mean"] > 1 and got["share_at_or_below"] < .2
