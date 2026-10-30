# SPDX-License-Identifier: Apache-2.0
"""The self-correction loop's stop rule, checked against a small state model (PLAN.md, 2026-10-30)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import loop_model  # noqa: E402


def test_the_old_rule_can_stop_with_a_shape_of_input_never_tried():
    got = loop_model.check(loop_model.OLD_RULE)
    assert got["verdict"] == "violated"
    assert got["violated"] == ["honest_stop"]
    assert got["trace"][-1]["done"] is True and got["trace"][-1]["untried"] > 0


def test_the_new_rule_cannot_stop_early_in_the_model():
    got = loop_model.check(loop_model.NEW_RULE)
    assert got["verdict"] == "holds_by_induction", got


def test_a_repair_makes_earlier_results_stale_in_the_model():
    got = loop_model.check(loop_model.NEW_RULE_NO_RERUN)
    assert got["verdict"] == "violated"
    assert got["trace"][-1]["stale"] is True


def test_the_new_rule_can_still_finish():
    assert loop_model.finishes(loop_model.NEW_RULE)


def _generations(monkeypatch, tmp_path, passing):
    """G6's ``cegis`` over harness generations: ``passing`` says which (arm, task) pairs the maintainers' tests accept."""
    pytest.importorskip("distill")
    import loop
    import run as we
    monkeypatch.setattr(we, "HARNESS", tmp_path)
    monkeypatch.setattr(loop, "commit", lambda *a: None)
    monkeypatch.setattr(loop, "spent", lambda root: 0.0)
    monkeypatch.setattr(loop.distill, "distill", lambda base, rows, root, caller, notes: (f"after {base}", []))
    made = []

    def rows_of(root, g6, arm, tasks, repeats=(1, 2)):
        made.append(arm)
        return [{"arm": arm, "task": t, "repeat": r, "run": "x", "passes": (arm, t) in passing} for t in tasks for r in repeats]

    monkeypatch.setattr(loop, "round_of", rows_of)
    start = [{"arm": "E", "task": t, "repeat": r, "run": "x", "passes": t == "a"} for t in "ab" for r in (1, 2)]
    return loop, loop.generations(tmp_path, "c", ["a", "b"], "E", "base", start, caller=None), made


def test_cegis_stops_when_a_generation_has_no_counterexample(monkeypatch, tmp_path):
    loop, (result, best_arm, best), made = _generations(monkeypatch, tmp_path, {("G1", "a"), ("G2", "a"), ("G2", "b")})
    assert result.status == "no_counterexample_in_domain"
    assert best_arm == "G2" and loop.score(best) == 1.0 and made == ["G1", "G2"]
    assert result.counterexamples == ({"task": "b", "repeat": 1},)   # the first failure G1 left


def test_cegis_gives_up_after_the_misses_and_keeps_the_best(monkeypatch, tmp_path):
    loop, (result, best_arm, best), made = _generations(monkeypatch, tmp_path, {("G1", "a")})
    assert result.status == "inconclusive" and best_arm == "E" and made == ["G1", "G2", "G3"]
    assert loop.score(best) == 0.5
