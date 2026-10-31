# SPDX-License-Identifier: Apache-2.0
"""Arms X1 and X2 (PLAN.md, 2026-11-01): can a right finding become a right fix? Development, on saved patches of training bugs."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import run as we  # noqa: E402
import wecommon as c  # noqa: E402

SONNET = we.R3_MODELS[1]
FOUND = {"rule": "every item is kept", "why": "drops the last", "test": "def test_x():\n    assert {1: 2}\n", "said": "reproduced",
         "at_base": {"a": "passed"}, "on_patch": {"a": "failed"}, "seconds": 3.0, "first_seen_in": "run/x/1", "verdict": "defect"}


def _saved(root, *, task="black-5634", arm="A", repeat=1, said="reproduced"):
    run = root / "run" / task / "1"
    run.mkdir(parents=True)
    (run / "final.patch").write_text("a patch", "utf-8")
    (root / "loop" / "final_review").mkdir(parents=True)
    (root / "loop" / f"{arm}-{task}-{repeat}.json").write_text(json.dumps({"run": f"run/{task}/1"}), "utf-8")
    review = root / "loop" / "final_review" / f"{c.sha256_file(run / 'final.patch')}.json"
    review.write_text(json.dumps({**FOUND, "said": said}), "utf-8")


def _cases(monkeypatch, *cases):
    monkeypatch.setattr(we, "convert_cases", lambda: list(cases))


CASE = {"task": "black-5634", "repeat": 1, "arm": "A", "of": 1, "start": "wrong"}


def test_a_conversion_run_starts_from_the_saved_patch_and_its_saved_review(tmp_path, monkeypatch):
    _saved(tmp_path)
    _cases(monkeypatch, CASE)
    patch, found = we.convert_start(tmp_path, "black-5634", 1)
    assert patch == tmp_path / "run" / "black-5634" / "1" / "final.patch"
    assert found == {k: FOUND[k] for k in ("rule", "why", "test")}


def test_a_patch_the_review_did_not_reproduce_a_defect_on_has_no_finding_to_start_from(tmp_path, monkeypatch):
    _saved(tmp_path, said="none")
    _cases(monkeypatch, CASE)
    with pytest.raises(ValueError, match="no reproduced finding"):
        we.convert_start(tmp_path, "black-5634", 1)


def test_the_agent_reads_the_reviewers_rule_reasons_and_test_and_nothing_else_of_the_review():
    found = {k: FOUND[k] for k in ("rule", "why", "test")}
    for arm in we.CONVERT_ARMS:
        note = we.convert_note(arm, found)
        assert all(found[k].strip() in note for k in found)
        assert "passed" not in note and "run/x/1" not in note and "reproduced" not in note


def test_x1_says_the_change_is_in_the_tree_and_x2_says_it_is_not():
    found = {k: FOUND[k] for k in ("rule", "why", "test")}
    assert "already in the working tree" in we.convert_note("X1", found)
    assert "not in the tree" in we.convert_note("X2", found)


@pytest.mark.parametrize("arm", we.CONVERT_ARMS)
def test_a_conversion_arm_is_plain_claude_code_with_the_issues_discussion(arm):
    task = we.load_tasks()[1]
    assert we.prompt(task, "t", "body", arm) == we.prompt(task, "t", "body", "A") and we.gets_thread(arm)


def test_a_conversion_run_is_scheduled_only_for_its_listed_cases(monkeypatch):
    _cases(monkeypatch, CASE)
    assert we.r3_loop_key((SONNET, "black-5634", "X1", 1)) and we.r3_loop_key((SONNET, "black-5634", "X2", 1))
    assert not we.r3_loop_key((SONNET, "black-5634", "X1", 2))
    assert not we.r3_loop_key((we.R3_MODELS[0], "black-5634", "X1", 1))
    assert not we.r3_loop_key((SONNET, "black-5634", "X3", 1))


def test_no_conversion_run_can_be_scheduled_on_a_bug_kept_for_measurement(monkeypatch):
    for task in (we.R3_HELD[0], we.load_tasks(we.TASKS_FRESH)[0].id):
        _cases(monkeypatch, {**CASE, "task": task})
        assert not we.r3_loop_key((SONNET, task, "X1", 1))


def test_the_case_list_holds_training_bugs_only_and_a_wrong_patch_for_each_control():
    cases = we.convert_cases()
    assert cases and all(not we.is_fresh(x["task"]) and x["task"] not in we.R3_HELD for x in cases)
    assert {x["start"] for x in cases} == {"wrong", "right"}
    assert len({(x["task"], x["repeat"]) for x in cases}) == len(cases)


def test_arm_r_keeps_the_patch_it_had_before_its_review_where_the_check_scores_every_round(tmp_path):
    ran = []

    class Docker:
        def sh(self, agent, script, *args, **kw):
            ran.append(script)
            return type("Done", (), {"stdout": b"", "stderr": b"", "returncode": 0})()

    class Ctx:
        docker = Docker()

    we.keep_first_patch(Ctx(), type("T", (), {"repo": "/w/r"})(), "agent", "base1")
    assert len(ran) == 1 and f"{we.WE}/rounds/round-1.patch" in ran[0] and "base1" in ran[0]
