# SPDX-License-Identifier: Apache-2.0
"""Arm S and the failure-rich analysis (PLAN.md, 2026-11-01): screening has its own runs, so a bug's bad luck in it cannot flatter a harness."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import compare  # noqa: E402
import run as we  # noqa: E402

SONNET = we.R3_MODELS[1]


def _rows(**arms):
    """``M9="a10 b11"``: bug a passes repeat 1 and fails repeat 2, on both scores."""
    rows = {}
    for arm, cells in arms.items():
        for cell in cells.split():
            for repeat, bit in enumerate(cell[1:], 1):
                rows[(arm, cell[0], repeat)] = {"maintainers": bit == "1", "amended": bit == "1", "said": "done"}
    return rows


def _unseen(monkeypatch):
    bug = type("Bug", (), {"id": "newrepo-1"})()
    dev = type("Bug", (), {"id": "devrepo-1"})()
    monkeypatch.setattr(we, "load_tasks", lambda path=we.TASKS: {we.TASKS_UNSEEN: [bug], we.TASKS_DEV: [dev]}.get(path, []))


def test_arm_s_is_arm_a_exactly():
    task = we.load_tasks()[1]
    assert we.prompt(task, "t", "body", "S") == we.prompt(task, "t", "body", "A") and not we.gets_thread("S")


def test_arm_s_runs_three_times_on_stage_2_and_the_development_pool(monkeypatch):
    _unseen(monkeypatch)
    assert we.r3_loop_key((SONNET, "newrepo-1", "S", 3)) and we.r3_loop_key((SONNET, "devrepo-1", "S", 1))
    assert not we.r3_loop_key((SONNET, "newrepo-1", "S", 4)) and not we.r3_loop_key((we.R3_MODELS[0], "newrepo-1", "S", 1))


def test_arm_s_never_runs_on_a_bug_stage_1_or_training_already_measured(monkeypatch):
    for task in ("black-5634", we.R3_HELD[0], we.load_tasks(we.TASKS_FRESH)[0].id):
        assert not we.r3_loop_key((SONNET, task, "S", 1))


def test_a_bug_is_failure_rich_on_a_score_if_any_screening_run_failed_on_it():
    rows = _rows(S="a111 b011 c100")
    rows[("S", "a", 2)] = {"maintainers": True, "amended": False, "said": "done"}
    assert compare.failure_rich(rows, "abc", "maintainers") == ["b", "c"]
    assert compare.failure_rich(rows, "abc", "amended") == ["a", "b", "c"]


def test_a_screening_run_with_no_verdict_is_not_a_failure():
    rows = _rows(S="a111")
    rows[("S", "a", 2)] = {"maintainers": None, "amended": None, "said": "done"}
    assert compare.failure_rich(rows, "a", "maintainers") == []


def test_a_screening_run_is_never_a_row_of_the_comparison():
    plain = _rows(M9="a11 b11", A="a00 b00", T="a00 b00")
    screened = {**plain, **_rows(S="a111 b111")}
    assert compare.compare(plain, "M9", "A", "ab", (1, 2), "maintainers") == compare.compare(screened, "M9", "A", "ab", (1, 2), "maintainers")


def test_the_failure_rich_result_uses_the_arms_own_runs_and_prints_its_count(capsys):
    rows = _rows(S="a111 b000", M9="a11 b11", A="a11 b00", T="a11 b00")
    compare.rich(rows, "stage 2, failure-rich", "ab", (1, 2), "M9")
    out = capsys.readouterr().out
    assert "1 of 2 bugs" in out and "M9   2/2" in out and "A    0/2" in out and "wins  2" in out


def test_a_report_can_be_held_to_one_score(capsys):
    compare.report(_rows(M9="a11", A="a00", T="a00"), "x", "a", (1, 2), scores=("amended",))
    out = capsys.readouterr().out
    assert "amended" in out and "maintainers" not in out


def test_arm_k_reads_its_own_end_for_what_it_said_of_a_run(tmp_path):
    run = tmp_path / "run" / "x" / "1"
    run.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"repairs": {"status": "no_counterexample_in_domain"}}), "utf-8")
    assert compare.said_of(tmp_path, {"arm": "K1", "run": "run/x/1"}) == "clean"
    (run / "run.json").write_text(json.dumps({"repairs": {"status": "inconclusive"}}), "utf-8")
    assert compare.said_of(tmp_path, {"arm": "K1", "run": "run/x/1"}) == "unsure"
