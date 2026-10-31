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


def test_an_amended_bug_not_yet_rescored_counts_as_missing_not_as_its_old_verdict(tmp_path, monkeypatch):
    monkeypatch.setattr(compare.we, "amendment", lambda task: Path("d") if task == "x-1" else None)
    (tmp_path / "loop" / "amended").mkdir(parents=True)
    for arm, task, passes in (("A", "x-1", True), ("A", "y-1", True), ("M7", "x-1", False)):
        (tmp_path / "loop" / f"{arm}-{task}-1.json").write_text(
            json.dumps({"arm": arm, "task": task, "repeat": 1, "passes": passes}), "utf-8")
    (tmp_path / "loop" / "amended" / "M7-x-1-1.json").write_text(json.dumps({"amended_passes": True}), "utf-8")
    (tmp_path / "loop" / "cegis.json").write_text('{"status": "inconclusive"}', "utf-8")
    rows = compare.saved(tmp_path)
    assert rows[("A", "y-1", 1)] == {"maintainers": True, "amended": True}      # no amendment: one score
    assert rows[("M7", "x-1", 1)] == {"maintainers": False, "amended": True}
    assert rows[("A", "x-1", 1)] == {"maintainers": True, "amended": None}
    assert compare.compare(rows, "M7", "A", ["x-1"], (1,), "amended")["missing"] == 1


def test_the_seven_determined_fresh_bugs_are_the_audits_labels():
    fresh = [t.id for t in compare.we.load_tasks(compare.we.TASKS_FRESH)]
    assert len(fresh) == 11 and set(compare.DETERMINED) <= set(fresh) and len(compare.DETERMINED) == 7
    assert not {"black-5144", "attrs-1509", "starlette-3166", "pydantic-13133"} & set(compare.DETERMINED)
