# SPDX-License-Identifier: Apache-2.0
"""Replay 3 (PLAN.md, 2026-11-04, early): one test of the issue's rule a bug, written with no patch shown, run on each of its patches."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import final_review as fr  # noqa: E402
import replay3 as r3  # noqa: E402
import run as we  # noqa: E402

TEST = "def test_rule():\n    assert 0\n"
WROTE = {"verdict": "defect", "rule": "the rule", "why": "why", "test": TEST, "error": ""}
NONE = {"verdict": "none", "rule": "no rule", "why": "nothing to test", "test": "", "error": ""}
FAILED_ASK = {"verdict": "failed", "rule": "", "why": "", "test": "", "error": "RuntimeError: the reviewer exited 1"}
FAILS = we.ProbeRun(1, {"t::test_rule": "failed", "t::test_kept": "passed"}, {"t::test_rule": "assert 1 == 2"}, "")
PASSES = we.ProbeRun(0, {"t::test_rule": "passed", "t::test_kept": "passed"}, {}, "")
BROKE_KEPT = we.ProbeRun(1, {"t::test_rule": "passed", "t::test_kept": "failed"}, {"t::test_kept": "assert 2 == 3"}, "")
WONT_RUN = we.ProbeRun(2, {}, {}, "ImportError")
NO_EXIT = we.ProbeRun(None, {}, {}, "")
CUT = we.ProbeRun(2, {"t::test_rule": "failed"}, {"t::test_rule": "assert 1 == 2"}, "KeyboardInterrupt")   # failed, then stopped                          # no pytest exit read: the container failed


@pytest.mark.parametrize(("found", "base", "now", "said"), [
    (WROTE, FAILS, FAILS, "reproduced"),            # the rule still fails with the patch: wrong
    (WROTE, FAILS, BROKE_KEPT, "reproduced"),       # fixed the rule, broke what must not change: wrong
    (WROTE, FAILS, PASSES, "none"),                 # every case holds with the patch
    (WROTE, PASSES, FAILS, "none"),                 # it reproduces nothing on the parent, so the bug has no verdict
    (WROTE, WONT_RUN, FAILS, "none"),
    (WROTE, FAILS, WONT_RUN, "none"),               # pytest could not run it with the patch: no verdict, not a defect
    (WROTE, FAILS, NO_EXIT, "failed"),             # no exit at all, as a Docker failure gives: no review, made again
    (WROTE, NO_EXIT, FAILS, "failed"),
    (WROTE, FAILS, CUT, "reproduced"),             # a failure recorded before pytest stopped is still a failure
    ({**WROTE, "test": ""}, None, None, "failed"),  # defect with no test: a reply cut short, no answer
    (NONE, None, None, "none"),                     # no test
    (FAILED_ASK, None, None, "failed"),             # no review: made again on the next run
])
def test_a_patch_is_wrong_only_when_a_case_of_a_test_that_reproduces_the_bug_fails_with_it(found, base, now, said):
    got = r3.verdict(found, base, now)
    assert got["said"] == said and got["verdict"] == {"reproduced": "defect", "none": "none", "failed": "failed"}[said]
    assert fr.counts(got) == (said != "failed")


def _rows(root: Path, n: int, task: str = "black-5634") -> list[dict[str, str]]:
    rows = []
    (root / "loop").mkdir(exist_ok=True)          # the archive's own folder of rows, which ``final_review.keep`` saves beside
    for k in range(n):
        run = root / f"run{k}"
        run.mkdir(parents=True)
        (run / "final.patch").write_text(f"patch {k}\n", "utf-8")
        rows.append({"task": task, "run": f"run{k}"})
    return rows


def _ctx(root: Path) -> SimpleNamespace:
    return SimpleNamespace(archive=SimpleNamespace(root=root))


def test_the_reviewer_is_asked_once_a_bug_and_its_test_runs_on_every_patch(tmp_path):
    asked, ran = [], []

    def ask(ctx, task, patch, title, text, prompt, where, file, *, candidate, run):
        asked.append((patch.parent.name, prompt, where, file, candidate, run))
        return WROTE, None, None

    def run(ctx, task, test, patch):
        ran.append((patch.read_text("utf-8").split()[1], test))     # the copy of the bytes read, named by its content
        return FAILS, PASSES

    rows = _rows(tmp_path, 3)
    got = r3.review_bug(_ctx(tmp_path), tmp_path, we.task_by_id("black-5634"), rows, "title", "text", ask=ask, run=run)
    assert asked == [("run0", r3.RULE_FIRST, r3.RULE_AT, r3.FILE, False, False)]     # asked, and nothing run by the ask
    assert ran == [("0", TEST), ("1", TEST), ("2", TEST)]                            # every patch runs one way
    assert [g["said"] for g in got] == ["none", "none", "none"]
    assert all(g["instruction"] == r3.INSTRUCTION for g in got)
    assert [fr.saved(tmp_path, row, r3.FOLDER)["said"] for row in rows] == ["none", "none", "none"]


def test_a_saved_test_of_the_bug_is_run_again_with_no_call_and_a_failed_ask_is_not_saved(tmp_path):
    rows = _rows(tmp_path, 2)
    task = we.task_by_id("black-5634")
    r3.review_bug(_ctx(tmp_path), tmp_path, task, rows[:1], "t", "x", ask=lambda *a, **k: (FAILED_ASK, None, None),
                  run=lambda *a: pytest.fail("no test to run"))
    assert not r3.bug_record(tmp_path, task.id).exists()
    r3.review_bug(_ctx(tmp_path), tmp_path, task, rows[:1], "t", "x", ask=lambda *a, **k: (WROTE, None, None),
                  run=lambda *a: (FAILS, PASSES))
    assert json.loads(r3.bug_record(tmp_path, task.id).read_text("utf-8"))["found"]["test"] == TEST
    got = r3.review_bug(_ctx(tmp_path), tmp_path, task, rows[1:], "t", "x", ask=lambda *a, **k: pytest.fail("asked again"),
                        run=lambda *a: (FAILS, FAILS))
    assert [g["said"] for g in got] == ["reproduced"]


def test_a_changed_instruction_finds_neither_the_bugs_test_nor_its_verdicts(tmp_path, monkeypatch):
    rows = _rows(tmp_path, 1)
    task = we.task_by_id("black-5634")
    r3.review_bug(_ctx(tmp_path), tmp_path, task, rows, "t", "x", ask=lambda *a, **k: (WROTE, None, None),
                  run=lambda *a: (FAILS, PASSES))
    assert r3.FOLDER.endswith(r3.INSTRUCTION) and fr.saved(tmp_path, rows[0], r3.FOLDER) is not None
    monkeypatch.setattr(r3, "INSTRUCTION", "0" * 16)
    monkeypatch.setattr(r3, "FOLDER", f"final_review_rule-{"0" * 16}")
    assert not r3.bug_record(tmp_path, task.id).exists() and fr.saved(tmp_path, rows[0], r3.FOLDER) is None


def test_a_bug_test_cut_short_is_no_record_and_the_bug_is_asked_again(tmp_path):
    rows = _rows(tmp_path, 1)
    task = we.task_by_id("black-5634")
    saved = r3.bug_record(tmp_path, task.id)
    saved.parent.mkdir(parents=True)
    saved.write_text('{"task": "black-5634", "found": {"te', "utf-8")             # a write cut short
    asked = []
    r3.review_bug(_ctx(tmp_path), tmp_path, task, rows, "t", "x",
                  ask=lambda *a, **k: asked.append(1) or (WROTE, None, None), run=lambda *a: (FAILS, FAILS))
    assert asked == [1] and json.loads(saved.read_text("utf-8"))["found"]["test"] == TEST


def test_a_bug_whose_reviewer_wrote_no_test_gives_each_patch_none_with_no_run(tmp_path):
    rows = _rows(tmp_path, 2)
    got = r3.review_bug(_ctx(tmp_path), tmp_path, we.task_by_id("black-5634"), rows, "t", "x",
                        ask=lambda *a, **k: (NONE, None, None), run=lambda *a: pytest.fail("nothing to run"))
    assert [g["said"] for g in got] == ["none", "none"]


def test_a_patch_whose_run_failed_is_no_review_and_the_rest_still_run(tmp_path):
    def run(ctx, task, test, patch):
        if patch.read_text("utf-8") == "patch 1\n":
            raise RuntimeError("the patch does not apply")
        return FAILS, FAILS

    rows = _rows(tmp_path, 3)
    got = r3.review_bug(_ctx(tmp_path), tmp_path, we.task_by_id("black-5634"), rows, "t", "x",
                        ask=lambda *a, **k: (WROTE, None, None), run=run)
    assert [g["said"] for g in got] == ["reproduced", "failed", "reproduced"] and "does not apply" in got[1]["error"]


def test_the_instruction_states_the_rule_in_general_words_and_names_no_bug_of_any_stage():
    tasks = {t.id for t in we.load_tasks()} | {t.id for t in we.load_tasks(we.TASKS_FRESH)} | {
        t.id for t in we.load_tasks(we.TASKS_UNSEEN)} | set(we.R3_HELD)
    text = r3.RULE_FIRST.lower()
    named = {part for task in tasks for part in [task.lower(), task.rsplit("-", 1)[0].lower()] if part in text}
    assert not named
    assert "no fix is in your cwd" in text and "```python" in r3.RULE_FIRST


def test_a_bug_with_no_waiting_patch_asks_no_one_and_runs_nothing(tmp_path):
    (tmp_path / "loop").mkdir()
    assert r3.review_bug(_ctx(tmp_path), tmp_path, we.task_by_id("black-5634"), [], "t", "x",
                         ask=lambda *a, **k: pytest.fail("asked"), run=lambda *a: pytest.fail("ran")) == []

def test_an_answer_that_says_defect_with_no_test_is_no_answer_and_is_not_kept(tmp_path):
    rows = _rows(tmp_path, 1)
    task = we.task_by_id("black-5634")
    got = r3.review_bug(_ctx(tmp_path), tmp_path, task, rows, "t", "x", ask=lambda *a, **k: ({**WROTE, "test": ""}, None, None),
                        run=lambda *a: pytest.fail("nothing to run"))
    assert [g["said"] for g in got] == ["failed"] and not r3.bug_record(tmp_path, task.id).exists()
    assert not fr.counts(fr.saved(tmp_path, rows[0], r3.FOLDER))


def test_a_verdict_is_saved_under_the_bytes_that_were_run_however_the_file_changes_after(tmp_path):
    rows = _rows(tmp_path, 1)
    path = tmp_path / rows[0]["run"] / "final.patch"
    first = hashlib.sha256(path.read_bytes()).hexdigest()

    def run(ctx, task, test, patch):
        assert patch.read_text("utf-8") == "patch 0\n" and patch != path
        path.write_text("another patch\n", "utf-8")            # the archive's file replaced while the copy runs
        return FAILS, FAILS

    r3.review_bug(_ctx(tmp_path), tmp_path, we.task_by_id("black-5634"), rows, "t", "x",
                  ask=lambda *a, **k: (WROTE, None, None), run=run)
    assert (tmp_path / "loop" / r3.FOLDER / f"{first}.json").is_file()
    assert fr.saved(tmp_path, rows[0], r3.FOLDER) is None         # the bytes now there were never run, so have no verdict
    assert not list((tmp_path / "critic_packs").glob("*.patch"))  # the copy is gone

def test_a_test_run_whose_docker_exec_failed_raises_and_every_other_call_passes_through():
    class Fake:
        def __init__(self, code):
            self.code = code

        def __call__(self, *args, **kwargs):
            return ("called", args)

        def sh(self, ctr, script, *args, **kwargs):
            return subprocess.CompletedProcess(script, self.code, b"", b"")

    with pytest.raises(RuntimeError, match="exited 125"):
        r3.Checked(Fake(125)).sh("ctr", f"pytest; echo {we.JUNIT_MARK}")
    assert r3.Checked(Fake(0)).sh("ctr", f"pytest; echo {we.JUNIT_MARK}").returncode == 0
    with pytest.raises(RuntimeError, match="exited 125"):           # staging the test copy is no test run, and is checked too
        r3.Checked(Fake(125)).sh("ctr", "cp -r /we/rule /tmp/held; true")
    with pytest.raises(RuntimeError, match="exited 1"):             # a patch that does not apply: a failed review, as before
        r3.Checked(Fake(1)).sh("ctr", "git apply /tmp/we.patch")
    assert r3.Checked(Fake(0))("rm", "-f", "x") == ("called", ("rm", "-f", "x"))


def test_a_saved_answer_that_is_no_answer_is_asked_again_and_runs_nothing(tmp_path):
    rows = _rows(tmp_path, 1)
    task = we.task_by_id("black-5634")
    saved = r3.bug_record(tmp_path, task.id)
    saved.parent.mkdir(parents=True)
    saved.write_text(json.dumps({"found": {**WROTE, "error": "a failed ask"}}), "utf-8")
    asked, ran = [], []
    r3.review_bug(_ctx(tmp_path), tmp_path, task, rows, "t", "x", ask=lambda *a, **k: asked.append(1) or (NONE, None, None),
                  run=lambda *a: ran.append(1) or (FAILS, FAILS))
    assert asked == [1] and ran == []


def test_one_run_at_a_time_holds_the_folder_and_a_second_refuses(tmp_path):
    with r3.one_runner(tmp_path / "f"):
        with pytest.raises(SystemExit, match="another replay 3 run"):
            with r3.one_runner(tmp_path / "f"):
                pass
    with r3.one_runner(tmp_path / "f"):                 # released when the first ended
        pass