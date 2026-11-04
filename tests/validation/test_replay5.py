# SPDX-License-Identifier: Apache-2.0
"""Replay 5 (PLAN.md, 2026-11-04, afternoon): replay 3's machinery with tests that assert only a relation between the code's own
outputs, on the issue's example and its variants, so no model writes what a right result is, and a wrong value can still be seen."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import final_review as fr  # noqa: E402
import replay3 as r3  # noqa: E402
import replay4 as r4  # noqa: E402
import replay5 as r5  # noqa: E402
import replay_compare as rc  # noqa: E402
import run as we  # noqa: E402

TEST = "def test_relation():\n    assert 0\n"
WROTE = {"verdict": "defect", "rule": "the relation", "why": "why", "test": TEST, "error": ""}
FAILS = we.ProbeRun(1, {"t::test_example": "failed", "t::test_variant": "failed"}, {}, "")
VARIANT_STILL = we.ProbeRun(1, {"t::test_example": "passed", "t::test_variant": "failed"}, {}, "")


def _rows(root: Path, n: int, task: str = "black-5634") -> list[dict[str, str]]:
    (root / "loop").mkdir(exist_ok=True)
    rows = []
    for k in range(n):
        (root / f"run{k}").mkdir(parents=True)
        (root / f"run{k}" / "final.patch").write_text(f"patch {k}\n", "utf-8")
        rows.append({"task": task, "run": f"run{k}"})
    return rows


def test_replay_5_is_its_own_instruction_with_its_own_folder_and_place():
    ins = r5.RELATION
    assert ins.text == r5.RELATION_FIRST and ins.digest == hashlib.sha256(r5.RELATION_FIRST.encode("utf-8")).hexdigest()[:16]
    assert ins.folder == f"final_review_relation-{ins.digest}" and ins.folder not in (r3.FOLDER, r4.FOLDER)
    assert ins.at == f"{we.WE}/relation" and ins.file == "test_relation.py" and ins.at not in (r3.RULE_AT, r4.SYMPTOM.at)


def test_a_bug_is_asked_under_replay_5s_instruction_and_saved_apart_from_replays_3_and_4(tmp_path):
    asked, ran = [], []

    def ask(ctx, task, patch, title, text, prompt, where, file, *, candidate, run):
        asked.append((prompt, where, file, candidate, run))
        return WROTE, None, None

    def run(ctx, task, test, patch):
        ran.append(test)
        return FAILS, VARIANT_STILL

    rows = _rows(tmp_path, 2)
    task = we.task_by_id("black-5634")
    got = r3.review_bug(SimpleNamespace(archive=SimpleNamespace(root=tmp_path)), tmp_path, task, rows, "t", "x", ask=ask, run=run,
                        ins=r5.RELATION)
    assert asked == [(r5.RELATION_FIRST, r5.RELATION.at, r5.RELATION.file, False, False)] and ran == [TEST, TEST]
    assert [g["said"] for g in got] == ["reproduced", "reproduced"] and all(g["instruction"] == r5.RELATION.digest for g in got)
    assert all(fr.saved(tmp_path, row, r5.RELATION.folder)["said"] == "reproduced" for row in rows)
    assert all(fr.saved(tmp_path, row, folder) is None for row in rows for folder in (r3.FOLDER, r4.FOLDER))
    assert json.loads(r3.bug_record(tmp_path, task.id, r5.RELATION).read_text("utf-8"))["instruction"] == r5.RELATION.digest


def test_the_instruction_asks_for_relations_between_outputs_only_and_names_no_bug():
    text = r5.RELATION_FIRST.lower()
    assert "relation" in text and "variant" in text and "pytest.skip" in text
    assert "write no expected value of your own" in text      # every compared value comes from the code or is an input
    for task in (*we.load_tasks(), *we.load_tasks(we.TASKS_FRESH), *we.load_tasks(we.TASKS_UNSEEN), *we.load_tasks(we.TASKS_STAGE4)):
        assert task.id not in text and task.full_name.lower() not in text
    assert r5.RELATION_FIRST[r5.RELATION_FIRST.index("<hard_limits>"):] == r3.RULE_FIRST[r3.RULE_FIRST.index("<hard_limits>"):]


def test_the_relation_test_is_counted_and_paired_with_the_others():
    assert (rc.RELATION, r5.FOLDER) in rc.REVIEWERS and rc.RELATION in rc.PAIRED
    assert len({folder for _, folder in rc.REVIEWERS}) == len(rc.REVIEWERS)
