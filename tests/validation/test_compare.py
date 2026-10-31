# SPDX-License-Identifier: Apache-2.0
"""The frozen comparison (PLAN.md, 2026-10-31), read from saved rows on both scores."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import compare  # noqa: E402

BUGS = [f"bug-{n}" for n in range(1, 11)]


def _rows(loop=(), other=(), *, score="maintainers"):
    """Ten bugs, one repeat each: the loop passes the bugs in ``loop``, arm A those in ``other``."""
    rows = {}
    for bug in BUGS:
        rows[("M7", bug, 1)] = {"maintainers": False, "amended": False, score: bug in loop}
        rows[("A", bug, 1)] = {"maintainers": False, "amended": False, score: bug in other}
    return rows


def test_the_sign_test_is_exact_and_one_sided():
    assert compare.sign_test(5, 0) == pytest.approx(1 / 32)
    assert compare.sign_test(4, 1) == pytest.approx(6 / 32)
    assert compare.sign_test(0, 5) == 1.0
    assert compare.sign_test(0, 0) == 1.0


def test_the_comparison_counts_only_pairs_that_differ():
    got = compare.compare(_rows(BUGS[:6], BUGS[:1]), "M7", "A", BUGS, (1,), "maintainers")
    assert (got["loop_passed"], got["other_passed"], got["wins"], got["losses"]) == (6, 1, 5, 0)
    assert got["uplift"] == pytest.approx(0.5) and got["p"] == pytest.approx(1 / 32) and got["missing"] == 0


def test_the_two_scores_are_read_apart():
    rows = _rows(BUGS[:6], score="amended")
    assert compare.compare(rows, "M7", "A", BUGS, (1,), "amended")["loop_passed"] == 6
    assert compare.compare(rows, "M7", "A", BUGS, (1,), "maintainers")["loop_passed"] == 0


def test_the_rule_needs_more_passes_and_p_below_5_percent_against_every_arm():
    win = compare.compare(_rows(BUGS[:6]), "M7", "A", BUGS, (1,), "maintainers")
    small = compare.compare(_rows(BUGS[:4]), "M7", "A", BUGS, (1,), "maintainers")   # p = 1/16
    assert win["p"] < 0.05 and compare.judged([win])
    assert not compare.judged([small])
    assert not compare.judged([win, small])
    assert not compare.judged([])


def test_a_missing_run_is_never_a_win_or_a_loss():
    rows = _rows(BUGS[:6])
    del rows[("A", BUGS[9], 1)]
    got = compare.compare(rows, "M7", "A", BUGS, (1,), "maintainers")
    assert got["pairs"] == 9 and got["missing"] == 1 and not compare.judged([got])


def _cells(**arms):
    """``M9="a11 b10"``: bug a passed twice, bug b once."""
    return {(arm, part[0], i + 1): {"maintainers": flag == "1", "amended": flag == "1"}
            for arm, cells in arms.items() for part in cells.split() for i, flag in enumerate(part[1:])}


def test_each_comparison_counts_the_bugs_as_well_as_the_runs():
    rows = _cells(M9="a11 b10 c00", A="a00 b11 c00")
    assert compare.by_bug(rows, "M9", "A", "abc", (1, 2), "amended") == {"better": 1, "worse": 1, "same": 1}


def test_a_bug_with_a_run_missing_is_counted_neither_way():
    rows = _cells(M9="a11", A="a00")
    del rows[("M9", "a", 2)]
    assert compare.by_bug(rows, "M9", "A", "a", (1, 2), "amended") == {"better": 0, "worse": 0, "same": 0}


def test_the_report_prints_the_bug_count_beside_the_rule(capsys):
    compare.report(_cells(M9="a11 b11", A="a00 b00", T="a00 b00"), "stage 2", "ab", (1, 2))
    assert "bugs better 2 worse 0 same 0" in capsys.readouterr().out


def test_stage_3_compares_the_trained_arm_with_its_untrained_parent_too(capsys):
    rows = _cells(M10="a11", M9="a00", A="a00", T="a00")
    compare.report(rows, "stage 3", "a", (1, 2), loop_arm="M10", others=("M9", "A", "T"))
    assert sum(" M10 " in line and "uplift" in line for line in capsys.readouterr().out.splitlines()) == 6


def _archive(root, monkeypatch, *rows):
    (root / "loop" / "amended").mkdir(parents=True)
    diff = root / "x-1.patch"
    diff.write_text("a diff", encoding="utf-8")
    monkeypatch.setattr(compare.we, "amendment", lambda task: diff if task == "x-1" else None)
    for row in rows:
        row = {"repeat": 1, "run": f"run/{row['task']}/{row['arm']}", **row}
        (root / "loop" / f"{row['arm']}-{row['task']}-1.json").write_text(json.dumps(row), "utf-8")
    (root / "loop" / "cegis.json").write_text('{"status": "inconclusive"}', "utf-8")
    return compare.c.sha256_file(diff)


def _amended(root, arm, sha, *, run=None, passes=True):
    (root / "loop" / "amended" / f"{arm}-x-1-1.json").write_text(json.dumps(
        {"amended_passes": passes, "amendment_sha256": sha, "run": run or f"run/x-1/{arm}"}), "utf-8")


def test_an_amended_bug_not_yet_rescored_counts_as_missing_not_as_its_old_verdict(tmp_path, monkeypatch):
    sha = _archive(tmp_path, monkeypatch, {"arm": "A", "task": "x-1", "passes": True}, {"arm": "A", "task": "y-1", "passes": True},
                   {"arm": "M7", "task": "x-1", "passes": False})
    _amended(tmp_path, "M7", sha)
    rows = compare.saved(tmp_path)
    assert rows[("A", "y-1", 1)] == {"maintainers": True, "amended": True}      # no amendment: one score
    assert rows[("M7", "x-1", 1)] == {"maintainers": False, "amended": True}
    assert rows[("A", "x-1", 1)] == {"maintainers": True, "amended": None}
    assert compare.compare(rows, "M7", "A", ["x-1"], (1,), "amended")["missing"] == 1


def test_a_verdict_from_another_diff_or_another_run_is_missing(tmp_path, monkeypatch):
    sha = _archive(tmp_path, monkeypatch, {"arm": "A", "task": "x-1", "passes": True}, {"arm": "M7", "task": "x-1", "passes": False})
    _amended(tmp_path, "A", "not-this-diff")
    _amended(tmp_path, "M7", sha, run="run/x-1/another")
    rows = compare.saved(tmp_path)
    assert rows[("A", "x-1", 1)]["amended"] is None and rows[("M7", "x-1", 1)]["amended"] is None


def test_a_finished_run_with_no_independent_verdict_is_missing_not_a_failure(tmp_path, monkeypatch):
    _archive(tmp_path, monkeypatch, {"arm": "A", "task": "y-1", "passes": False, "status": "finished", "failing": None},
             {"arm": "M7", "task": "y-1", "passes": False, "status": "error", "failing": None},
             {"arm": "T", "task": "y-1", "passes": False, "status": "finished", "failing": 3})
    rows = compare.saved(tmp_path)
    assert rows[("A", "y-1", 1)] == {"maintainers": None, "amended": None}
    assert rows[("M7", "y-1", 1)]["maintainers"] is False                       # a run that ended badly did fail
    assert rows[("T", "y-1", 1)]["maintainers"] is False
    five_missing = {("M7", f"b{n}", 1): {"maintainers": True} for n in range(5)}
    five_missing.update({("A", f"b{n}", 1): rows[("A", "y-1", 1)] for n in range(5)})
    got = compare.compare(five_missing, "M7", "A", [f"b{n}" for n in range(5)], (1,), "maintainers")
    assert got["missing"] == 5 and got["pairs"] == 0 and not compare.judged([got])


def test_the_seven_determined_fresh_bugs_are_the_audits_labels():
    fresh = [t.id for t in compare.we.load_tasks(compare.we.TASKS_FRESH)]
    assert len(fresh) == 11 and set(compare.DETERMINED) <= set(fresh) and len(compare.DETERMINED) == 7
    assert not {"black-5144", "attrs-1509", "starlette-3166", "pydantic-13133"} & set(compare.DETERMINED)
