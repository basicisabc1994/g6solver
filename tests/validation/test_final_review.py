# SPDX-License-Identifier: Apache-2.0
"""The final review (PLAN.md, 2026-10-31, late): a second reader of a finished patch, which changes no run and no verdict."""
from __future__ import annotations

import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import compare  # noqa: E402
import final_review as fr  # noqa: E402
import run as we  # noqa: E402
import wecommon as c  # noqa: E402

REPLY = "RULE: every item is kept\nVERDICT: defect\nWHY: drops the last\n```python\ndef test_x():\n    assert 0\n```\n"
QUIET = "RULE: every item is kept\nVERDICT: none\nWHY: nothing found\n"


def _tar() -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        body = b"print('parent')\n"
        info = tarfile.TarInfo("src/app.py")
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
    return raw.getvalue()


class _Docker:
    """Records every call. ``applies``: whether the patch goes on."""

    def __init__(self, *, applies=True):
        self.calls, self.scripts, self.applies = [], [], applies

    def __call__(self, *args, input=None, timeout=None, env=None):
        self.calls.append(args)
        out = _tar() if "archive" in args else b""
        return type("Done", (), {"stdout": out, "stderr": b"", "returncode": 0})()

    def sh(self, ctr, script, *args, **kw):
        self.scripts.append(script)
        code = 0 if self.applies or "git apply" not in script else 1
        return type("Done", (), {"stdout": b"", "stderr": b"", "returncode": code})()


def _ctx(tmp_path, reply, docker=None):
    class Ctx:
        pass

    ctx = Ctx()
    ctx.archive, ctx.docker, ctx.g6, ctx.variant, ctx.arm, ctx.seen = type("A", (), {"root": tmp_path})(), docker or _Docker(), {"commit": "c" * 40}, we.ROUND3, "L9", {}

    def critic(pack):
        ctx.seen = {p.relative_to(pack).as_posix(): p.read_text("utf-8") for p in pack.rglob("*") if p.is_file()}
        ctx.pack = pack
        if isinstance(reply, Exception):
            raise reply
        return f"READ: {ctx.seen['issue.txt'].splitlines()[0]}\n{reply}"

    ctx.critic = critic
    return ctx


def _patch(tmp_path, body="a patch"):
    patch = tmp_path / "final.patch"
    patch.write_text(body, "utf-8")
    return patch


def _runs(monkeypatch, *results):
    """``run_set`` gives these in turn: first on the parent, then on the patch."""
    got = iter(results)
    monkeypatch.setattr(we, "run_set", lambda ctx, task, agent, where: next(got))


def _probe(results, code=1):
    return we.ProbeRun(code, results, {}, "")


def test_the_review_says_reproduced_only_for_a_test_that_ran_and_failed_on_the_patch():
    defect = {"verdict": "defect", "error": ""}
    assert fr.said(defect, {"critic:t"}) == "reproduced"
    assert fr.said(defect, set()) == "opinion"
    assert fr.said({"verdict": "none", "error": ""}, set()) == "none"


def test_a_review_that_did_not_happen_has_no_verdict():
    assert fr.said({"verdict": "unread", "error": "the reply does not quote it"}, set()) == "failed"
    assert fr.said({"verdict": "defect", "error": "OSError: gone"}, {"critic:t"}) == "failed"
    assert fr.said({"verdict": "unreadable", "error": ""}, set()) == "failed"


def test_the_reviewer_sees_the_task_the_parent_and_the_patch_and_nothing_else(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, QUIET)
    fr.review(ctx, we.load_tasks()[0], _patch(tmp_path), "a title", "the issue")
    assert set(ctx.seen) == {"issue.txt", "candidate.patch", "critic_prompt.md", "parent/src/app.py"}
    assert "the issue" in ctx.seen["issue.txt"] and ctx.seen["candidate.patch"] == "a patch"
    assert ctx.seen["critic_prompt.md"] == we.critic_text("L9")


def test_the_review_uses_the_agents_image_with_no_network_and_never_the_oracles(tmp_path):
    ctx = _ctx(tmp_path, QUIET)
    task = we.load_tasks()[0]
    fr.review(ctx, task, _patch(tmp_path), "a title", "the issue")
    started = next(args for args in ctx.docker.calls if args[0] == "run")
    agent, oracle = we.image_names(task, "c" * 40, we.ROUND3)
    assert agent in started and oracle not in started
    assert started[started.index("--network") + 1] == "none"
    assert not any("/oracle" in " ".join(map(str, args)) for args in ctx.docker.calls)
    assert not any("/oracle" in script for script in ctx.docker.scripts)


def test_the_reviewers_test_runs_on_the_parent_and_then_on_the_patch(tmp_path, monkeypatch):
    _runs(monkeypatch, _probe({"t::test_x": "passed"}, 0), _probe({"t::test_x": "failed"}))
    ctx = _ctx(tmp_path, REPLY)
    got = fr.review(ctx, we.load_tasks()[0], _patch(tmp_path), "a title", "the issue")
    assert got["said"] == "reproduced" and got["failing"] == ["critic:t::test_x"]
    assert got["at_base"] == {"t::test_x": "passed"} and got["on_patch"] == {"t::test_x": "failed"}
    assert sum("git apply" in script for script in ctx.docker.scripts) == 1


def test_a_test_that_will_not_run_says_nothing_of_the_patch(tmp_path, monkeypatch):
    _runs(monkeypatch, _probe({}, 2), _probe({}, 2))
    got = fr.review(_ctx(tmp_path, REPLY), we.load_tasks()[0], _patch(tmp_path), "a title", "the issue")
    assert got["said"] == "opinion" and got["failing"] == []


def test_a_test_that_passes_on_the_patch_leaves_an_opinion(tmp_path, monkeypatch):
    _runs(monkeypatch, _probe({"t::test_x": "failed"}), _probe({"t::test_x": "passed"}, 0))
    assert fr.review(_ctx(tmp_path, REPLY), we.load_tasks()[0], _patch(tmp_path), "a title", "the issue")["said"] == "opinion"


def test_a_patch_that_will_not_apply_is_a_failed_review_not_a_finding(tmp_path, monkeypatch):
    _runs(monkeypatch, _probe({"t::test_x": "passed"}, 0))
    got = fr.review(_ctx(tmp_path, REPLY, _Docker(applies=False)), we.load_tasks()[0], _patch(tmp_path), "a title", "the issue")
    assert got["said"] == "failed" and "apply" in got["error"]


def test_a_reviewer_that_fails_is_recorded_and_everything_it_used_goes(tmp_path):
    ctx = _ctx(tmp_path, RuntimeError("quota"))
    got = fr.review(ctx, we.load_tasks()[0], _patch(tmp_path), "a title", "the issue")
    assert got["said"] == "failed" and "quota" in got["error"]
    assert not ctx.pack.exists() and any(args[:2] == ("rm", "-f") for args in ctx.docker.calls)


def _row(root, arm, task="x-1", repeat=1, body="one patch", status="finished"):
    run = root / "run" / f"{arm}{repeat}"
    run.mkdir(parents=True, exist_ok=True)
    (run / "final.patch").write_text(body, "utf-8")
    row = {"arm": arm, "task": task, "repeat": repeat, "run": f"run/{arm}{repeat}", "status": status, "passes": True, "failing": 0}
    (root / "loop").mkdir(exist_ok=True)
    (root / "loop" / f"{arm}-{task}-{repeat}.json").write_text(json.dumps(row), "utf-8")
    return row


def test_each_distinct_patch_is_reviewed_once_and_identical_ones_share_it(tmp_path):
    _row(tmp_path, "A", body="same")
    _row(tmp_path, "G1", body="same")
    _row(tmp_path, "L9", body="another")
    assert len(fr.waiting(tmp_path, ["x-1"])) == 2


def test_a_run_that_did_not_finish_or_left_no_patch_is_not_reviewed(tmp_path):
    _row(tmp_path, "A", status="error")
    _row(tmp_path, "G1", body="")
    assert fr.waiting(tmp_path, ["x-1"]) == []


def test_only_the_bugs_named_are_reviewed(tmp_path):
    _row(tmp_path, "A", task="x-1")
    _row(tmp_path, "A", task="y-1", body="other")
    assert [row["task"] for row in fr.waiting(tmp_path, ["y-1"])] == ["y-1"]


def test_the_default_is_the_training_bugs_so_no_held_out_patch_is_read():
    assert not set(fr.TRAINING) & set(we.R3_HELD) and len(fr.TRAINING) == 7


def test_a_saved_review_stands_and_a_failed_one_is_made_again(tmp_path):
    row = _row(tmp_path, "A")
    fr.keep(tmp_path, row, {"said": "none"})
    assert fr.waiting(tmp_path, ["x-1"]) == [] and fr.said_of(tmp_path, row) == "none"
    fr.keep(tmp_path, row, {"said": "failed"})
    assert len(fr.waiting(tmp_path, ["x-1"])) == 1 and fr.said_of(tmp_path, row) is None


def test_a_changed_patch_has_no_review(tmp_path):
    row = _row(tmp_path, "A")
    fr.keep(tmp_path, row, {"said": "none"})
    (tmp_path / row["run"] / "final.patch").write_text("changed", "utf-8")
    assert fr.said_of(tmp_path, row) is None


def test_the_comparison_reads_the_review_beside_the_runs_and_only_where_there_is_one(tmp_path, monkeypatch):
    monkeypatch.setattr(compare.we, "amendment", lambda task: None)
    reviewed = _row(tmp_path, "A")
    fr.keep(tmp_path, reviewed, {"said": "reproduced"})
    _row(tmp_path, "T", body="another")
    rows = compare.saved(tmp_path)
    assert rows[("A", "x-1", 1)]["review"] == "reproduced" and "review" not in rows[("T", "x-1", 1)]


def test_the_review_changes_no_verdict_and_no_rule(tmp_path, monkeypatch):
    monkeypatch.setattr(compare.we, "amendment", lambda task: None)
    reviewed = _row(tmp_path, "A")
    before = compare.saved(tmp_path)[("A", "x-1", 1)]
    fr.keep(tmp_path, reviewed, {"said": "reproduced"})
    after = compare.saved(tmp_path)[("A", "x-1", 1)]
    assert {k: after[k] for k in ("maintainers", "amended", "said")} == {k: before[k] for k in ("maintainers", "amended", "said")}


def test_the_report_says_how_often_a_reproduced_defect_meant_wrong(capsys):
    rows = {("M9", "a", 1): {"maintainers": False, "amended": False, "said": "clean", "review": "reproduced"},
            ("M9", "b", 1): {"maintainers": True, "amended": True, "said": "clean", "review": "reproduced"},
            ("M9", "c", 1): {"maintainers": True, "amended": True, "said": "clean", "review": "none"},
            ("A", "a", 1): {"maintainers": True, "amended": True, "said": "done"}}
    compare.report(rows, "stage 2", "abc", (1,), others=("A",))
    out = capsys.readouterr().out
    assert "M9 final review reproduced: right 1 wrong 1 (50%)" in out and "M9 final review none: right 1 wrong 0 (100%)" in out
    assert "A final review" not in out


def test_the_trust_table_leaves_out_runs_with_no_review():
    rows = {("M9", "a", 1): {"amended": True, "said": "clean"}}
    assert compare.trust(rows, "M9", "a", (1,), "amended", key="review") == {}


def test_review_results_are_saved_outside_the_rows_the_comparison_counts(tmp_path):
    row = _row(tmp_path, "A")
    fr.keep(tmp_path, row, {"said": "none"})
    assert [p.name for p in (tmp_path / "loop").glob("*-*.json")] == ["A-x-1-1.json"]
    assert (tmp_path / "loop" / fr.FOLDER / f"{c.sha256_file(tmp_path / row['run'] / 'final.patch')}.json").is_file()


@pytest.mark.parametrize("name", ["said", "review", "waiting", "keep", "said_of"])
def test_the_module_says_what_each_step_does(name):
    assert getattr(fr, name).__doc__
