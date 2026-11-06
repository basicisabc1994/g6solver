# SPDX-License-Identifier: Apache-2.0
"""Arm K2 (PLAN.md, 2026-11-04, evening): K1's loop, with one structural change a generation may make: a reviewer's finding goes
back to the agent only on a case that failed on the parent and still fails on the patch. Measured on stage 1 and 2's recorded
reviews before any run: on determined bugs it keeps K1's one win and turns its one loss into a win."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import run as we  # noqa: E402

TEST = "def test_rule():\n    assert 0\n"
ON_TREE = we.ProbeRun(1, {"test_critic.py::test_owed": "failed", "test_critic.py::test_kept": "failed",
                          "test_critic.py::test_ok": "passed"}, {}, "")
AT_PARENT = we.ProbeRun(1, {"test_critic.py::test_owed": "failed", "test_critic.py::test_kept": "passed",
                            "test_critic.py::test_ok": "passed"}, {}, "")
KEPT_ONLY = we.ProbeRun(1, {"test_critic.py::test_kept": "failed", "test_critic.py::test_ok": "passed"}, {}, "")
ALL_HOLD = we.ProbeRun(0, {"test_critic.py::test_owed": "passed", "test_critic.py::test_kept": "passed"}, {}, "")


def _loop(monkeypatch, arm, on_tree, at_parent, *, parent_raises=False):
    sessions, parents, writes = [], [], []
    left = list(on_tree)

    class Docker:
        tree = 0

        def sh(self, agent, script, *a, **kw):
            if we.PATCH_BYTES in script:         # the patch's size (``run.patch_bytes``): the tree holds a change
                return SimpleNamespace(returncode=0, stdout=f"{we.PATCH_BYTES}=100\n".encode(), stderr=b"")
            return SimpleNamespace(returncode=0, stdout=f"tree {Docker.tree}".encode(), stderr=b"")

    def session(*a, **kw):
        sessions.append(a[6] if len(a) > 6 else kw)
        Docker.tree += 1
        a[5].feed({"type": "result", "subtype": "success", "is_error": False, "session_id": "s1"})   # a repair that ends cleanly:
        kw["codes"].append(0)                                              # its own success, of the first session, at exit 0

    def parent_check(ctx, task, found, ins):
        parents.append((found["test"], ins.at, ins.file))
        if parent_raises:
            raise RuntimeError("container failed")
        return at_parent.pop(0) if isinstance(at_parent, list) else at_parent

    monkeypatch.setattr(we, "run_set", lambda ctx, task, agent, where: left.pop(0))
    monkeypatch.setattr(we, "run_session", session)
    monkeypatch.setattr(we, "scout_again", lambda args: args)
    monkeypatch.setattr(we, "keep_first_patch", lambda *a: None)
    monkeypatch.setattr(we, "parent_check", parent_check)
    ctx = SimpleNamespace(docker=Docker(), arm=arm)
    run = SimpleNamespace(write_json=lambda name, body: writes.append(name))

    def critic(n):
        return {"verdict": "defect", "rule": "r", "why": "w", "test": TEST, "seconds": 1.0}
    first = we.c.Tally()
    first.feed({"type": "result", "subtype": "success", "is_error": False, "session_id": "s1"})    # the first session's own
    got = we.reviews_until_clean(ctx, SimpleNamespace(repo="/w/r"), run, "agent", "base", [], "key", first, 1.0, 0.0, critic,
                                 finding=we.finding_rule(arm))
    return got, sessions, parents, writes


def test_k2_is_a_generation_of_k1_whose_harness_file_sets_only_the_finding_rule():
    assert we.trained("K2") == {"finding": "parent"}
    assert json.loads((we.HARNESS / "K2.json").read_text("utf-8")) == {"finding": "parent"}
    assert we.prompt(we.load_tasks()[0], "t", "b", "K2") == we.prompt(we.load_tasks()[0], "t", "b", "K1")
    assert we.gets_thread("K2") and we.critic_text("K2") == we.critic_text("K1")


def test_a_structural_field_takes_only_its_listed_values(tmp_path, monkeypatch):
    monkeypatch.setattr(we, "HARNESS", tmp_path)
    (tmp_path / "K7.json").write_text(json.dumps({"finding": "whatever"}), "utf-8")
    with pytest.raises(ValueError):
        we.trained("K7")
    (tmp_path / "K8.json").write_text(json.dumps({"stop": "never"}), "utf-8")
    with pytest.raises(ValueError):
        we.trained("K8")


def test_k1_sends_back_every_failing_case_and_never_runs_the_parent(monkeypatch):
    got, sessions, parents, _ = _loop(monkeypatch, "K1", [KEPT_ONLY, ALL_HOLD], AT_PARENT)
    assert parents == [] and len(sessions) == 1 and got["status"] == "no_counterexample_in_domain"


def test_k2_sends_back_only_the_cases_that_failed_on_the_parent_too(monkeypatch):
    got, sessions, parents, writes = _loop(monkeypatch, "K2", [ON_TREE, ALL_HOLD], [AT_PARENT, AT_PARENT])
    assert parents and parents[0] == (TEST, we.CRITIC_AT, "test_critic.py")
    assert len(sessions) == 1 and "repair_check_1_critic_parent.json" in writes
    assert got["status"] == "no_counterexample_in_domain" and got["rounds"] == 2


def test_k2_sends_nothing_back_when_every_failing_case_passed_on_the_parent(monkeypatch):
    got, sessions, _, _ = _loop(monkeypatch, "K2", [KEPT_ONLY], AT_PARENT)
    assert sessions == [] and got["status"] == "no_counterexample_in_domain"
    assert "passed on the parent" in got["reason"] and got["rounds"] == 1


@pytest.mark.parametrize("raises", [True, False])
def test_k2_sends_nothing_back_and_is_inconclusive_when_the_parent_gives_no_result(monkeypatch, raises):
    got, sessions, _, _ = _loop(monkeypatch, "K2", [ON_TREE], we.ProbeRun(None, {}, {}, ""), parent_raises=raises)
    assert sessions == [] and got["status"] == "inconclusive" and "parent" in got["reason"]


def test_k2_reads_each_rounds_new_test_on_the_parent_and_keeps_the_earlier_rounds_results(monkeypatch):
    second = we.ProbeRun(1, {"test_critic.py::test_owed": "failed", "test_critic_2.py::test_new": "failed"}, {}, "")
    second_parent = we.ProbeRun(1, {"test_critic_2.py::test_new": "passed"}, {}, "")
    got, sessions, parents, _ = _loop(monkeypatch, "K2", [ON_TREE, second, ALL_HOLD], [AT_PARENT, second_parent])
    assert [p[2] for p in parents] == ["test_critic.py", "test_critic_2.py"]
    assert len(sessions) == 2                   # round 2 still owes test_owed, failing on the parent in round 1's run


@pytest.mark.parametrize(("arm", "rule"), [("K2", "parent"), ("K1", "any")])
def test_the_run_hands_the_loop_its_arms_finding_rule(monkeypatch, tmp_path, arm, rule):
    import io
    task, seen = we.load_tasks()[0], []

    class Docker:
        def __call__(self, *a, **kw):
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        def sh(self, ctr, script, *a, **kw):
            if script == we.TREE_STATE:
                value = {"head": task.parent, "tree": "tree", "dirty": 0, "commits": 1}
            elif script == we.ARM_STATE:
                value = {"mcp_servers": [], "skills": []}
            else:
                return self()
            return SimpleNamespace(returncode=0, stdout=json.dumps(value).encode(), stderr=b"")

        def popen(self, *a, **kw):
            return SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(), stderr=io.BytesIO(), poll=lambda: 0,
                                   wait=lambda **kw: 0)

    def loop(*a, finding="any", **kw):
        seen.append(finding)
        return {"status": "no_counterexample_in_domain"}

    monkeypatch.setattr(we, "key_check", lambda *a, **kw: {"ok": True})
    monkeypatch.setattr(we, "start_net", lambda *a: ("net", "proxy"))
    monkeypatch.setattr(we, "read_thread", lambda *a: "")
    monkeypatch.setattr(we, "read_stream", lambda lines, tally, **kw: (tally.feed(     # a first session that ends cleanly, by
        {"type": "result", "subtype": "success", "is_error": False, "session_id": "s1"}), "")[1])   # its own result
    monkeypatch.setattr(we, "reviews_until_clean", loop)
    ctx = we.Context(archive=we.c.Archive(tmp_path / "archive"), docker=Docker(), key_reader=lambda: "dummy",
                     issue_reader=lambda task: ("title", "issue"), tree_reader=lambda task: "tree", g6={"commit": "0" * 40},
                     variant=we.ROUND3, arm=arm, environ={})
    we._run_task(ctx, task, dry=False, cap=10.0, stop=10.5)
    assert seen == [rule]