# SPDX-License-Identifier: Apache-2.0
"""Replay 4 (PLAN.md, 2026-11-04, midday): replay 3's machinery with tests that assert only the issue's own symptom is absent, on
the issue's example and its variants, so no model writes what a right result is."""
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
import run as we  # noqa: E402

TEST = "def test_symptom():\n    assert 0\n"
WROTE = {"verdict": "defect", "rule": "the symptom", "why": "why", "test": TEST, "error": ""}
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


def test_replay_3s_instruction_is_read_from_its_names_at_call_time():
    got = r3.rule()
    assert (got.text, got.digest, got.folder, got.at, got.file) == (r3.RULE_FIRST, r3.INSTRUCTION, r3.FOLDER, r3.RULE_AT, r3.FILE)


def test_replay_4_is_its_own_instruction_with_its_own_folder_and_place():
    ins = r4.SYMPTOM
    assert ins.text == r4.SYMPTOM_FIRST and ins.digest == hashlib.sha256(r4.SYMPTOM_FIRST.encode("utf-8")).hexdigest()[:16]
    assert ins.folder == f"final_review_symptom-{ins.digest}" and ins.folder != r3.FOLDER
    assert ins.at == f"{we.WE}/symptom" and ins.file == "test_symptom.py" and ins.at != r3.RULE_AT


def test_a_bug_is_asked_under_replay_4s_instruction_and_saved_apart_from_replay_3(tmp_path):
    asked, ran = [], []

    def ask(ctx, task, patch, title, text, prompt, where, file, *, candidate, run):
        asked.append((prompt, where, file, candidate, run))
        return WROTE, None, None

    def run(ctx, task, test, patch):
        ran.append(test)
        return FAILS, VARIANT_STILL           # the example fixed, a variant still shows the symptom

    rows = _rows(tmp_path, 2)
    task = we.task_by_id("black-5634")
    got = r3.review_bug(SimpleNamespace(archive=SimpleNamespace(root=tmp_path)), tmp_path, task, rows, "t", "x", ask=ask, run=run,
                        ins=r4.SYMPTOM)
    assert asked == [(r4.SYMPTOM_FIRST, r4.SYMPTOM.at, r4.SYMPTOM.file, False, False)] and ran == [TEST, TEST]
    assert [g["said"] for g in got] == ["reproduced", "reproduced"] and all(g["instruction"] == r4.SYMPTOM.digest for g in got)
    assert all(fr.saved(tmp_path, row, r4.SYMPTOM.folder)["said"] == "reproduced" for row in rows)
    assert all(fr.saved(tmp_path, row, r3.FOLDER) is None for row in rows)             # replay 3's records untouched
    assert json.loads(r3.bug_record(tmp_path, task.id, r4.SYMPTOM).read_text("utf-8"))["instruction"] == r4.SYMPTOM.digest
    assert not r3.bug_record(tmp_path, task.id).exists()


def test_the_instruction_asks_for_the_symptoms_absence_only_and_names_no_bug():
    text = r4.SYMPTOM_FIRST.lower()
    assert "symptom" in text and "variant" in text and "pytest.skip" in text
    assert "assert no other value" in text           # no model writes what the right result is
    for task in (*we.load_tasks(), *we.load_tasks(we.TASKS_FRESH), *we.load_tasks(we.TASKS_UNSEEN)):
        assert task.id not in text and task.full_name.lower() not in text
    # the reply's form is replay 3's, which ``run.critic_report`` reads
    assert r4.SYMPTOM_FIRST[r4.SYMPTOM_FIRST.index("<hard_limits>"):] == r3.RULE_FIRST[r3.RULE_FIRST.index("<hard_limits>"):]
