# SPDX-License-Identifier: Apache-2.0
"""Arm K3 (PLAN.md, 2026-11-05, morning, later, items 1 and 4): K2 plus one step. The first session is arm A's prompt byte for
byte, checked by digest before the agent starts; its patch is saved as ``round-1.patch``; then the same session is resumed with
the rule step; the patch after it is saved as ``rule.patch`` and scored beside the others; then K2's loop runs unchanged."""
from __future__ import annotations

import hashlib
import io
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import run as we  # noqa: E402

SONNET = we.R3_MODELS[1]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_k3_is_k2_with_the_rule_step_and_nothing_else():
    task = we.load_tasks()[0]
    assert we.trained("K3") == we.trained("K2") == {"finding": "parent"}
    assert json.loads((we.HARNESS / "K3.json").read_text("utf-8")) == {"finding": "parent"}
    assert we.prompt(task, "t", "b", "K3") == we.prompt(task, "t", "b", "K2") == we.prompt(task, "t", "b", "A")
    assert we.gets_thread("K3") == we.gets_thread("K2") and we.critic_text("K3") == we.critic_text("K2")
    assert we.finding_rule("K3") == "parent"
    assert we.rule_led("K3") and not any(we.rule_led(arm) for arm in ("A", "K0", "K1", "K2", "V3", "V4", "R", "T"))
    for bug in ("black-5634", we.load_tasks(we.TASKS_UNSEEN)[0].id, we.load_tasks(we.TASKS_STAGE4)[0].id):
        for repeat in (1, 2, 3):
            assert we.r3_loop_key((SONNET, bug, "K3", repeat)) == we.r3_loop_key((SONNET, bug, "K2", repeat))


def test_the_rule_step_is_one_constant_pinned_by_its_digest():
    assert we.RULE_DIGEST == _sha(we.RULE_STEP)[:16] == "3e99c1c0b233732a"


class _Stdin(io.BytesIO):
    def __init__(self, sent):
        super().__init__()
        self.sent = sent

    def close(self):
        self.sent.append(self.getvalue())
        super().close()


def _run(monkeypatch, tmp_path, arm, *, step_ends="", thread=""):
    """``_run_task`` for ``arm`` with fakes only: no model, no container. Returns the record and what happened, in order."""
    task, events, sent = we.load_tasks()[0], [], []

    class Docker:
        def __call__(self, *a, **kw):
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        def sh(self, ctr, script, *a, **kw):
            if script == we.TREE_STATE:
                value = {"head": task.parent, "tree": "tree", "dirty": 0, "commits": 1}
            elif script == we.ARM_STATE:
                value = {"mcp_servers": [], "skills": []}
            else:
                saved = re.search(rf"{we.WE}/rounds/([\w.-]+\.patch)", script)
                if saved:
                    events.append(("saved", saved[1]))
                return self()
            return SimpleNamespace(returncode=0, stdout=json.dumps(value).encode(), stderr=b"")

        def popen(self, *a, **kw):
            events.append(("first session",))
            return SimpleNamespace(stdin=_Stdin(sent), stdout=io.BytesIO(), stderr=io.BytesIO(), poll=lambda: 0,
                                   wait=lambda **kw: 0)

    def session(ctx, run, agent, command, key, tally, text, name, *, seconds, stop_usd):
        events.append(("resumed", name, text, list(command)))
        return step_ends

    def loop(*a, finding="any", after_step=False, **kw):
        events.append(("loop", finding, after_step))
        return {"status": "no_counterexample_in_domain"}

    monkeypatch.setattr(we, "key_check", lambda *a, **kw: {"ok": True})
    monkeypatch.setattr(we, "start_net", lambda *a: ("net", "proxy"))
    monkeypatch.setattr(we, "read_thread", lambda *a: thread)
    monkeypatch.setattr(we, "read_stream", lambda *a, **kw: "")
    monkeypatch.setattr(we, "run_session", session)
    monkeypatch.setattr(we, "keep_first_patch", lambda ctx, task, agent, base, n=1: events.append(("saved", f"round-{n}.patch")))
    monkeypatch.setattr(we, "reviews_until_clean", loop)
    ctx = we.Context(archive=we.c.Archive(tmp_path / "archive"), docker=Docker(), key_reader=lambda: "dummy",
                     issue_reader=lambda task: ("title", "issue"), tree_reader=lambda task: "tree", g6={"commit": "0" * 40},
                     variant=we.ROUND3, arm=arm, environ={})
    record = we._run_task(ctx, task, dry=False, cap=10.0, stop=10.5)
    return record, events, sent, ctx, task


@pytest.mark.parametrize("thread", ["", "\n\nThe issue's discussion so far:\n\nmember on 2026-01-01: the rule is ..."])
def test_a_k3_run_sends_arm_as_prompt_saves_round_1_resumes_with_the_rule_step_saves_rule_then_runs_k2s_loop(monkeypatch, tmp_path,
                                                                                                            thread):
    """The discussion is read as K2 reads it (``gets_thread``), and arm A's builder is given the same text."""
    record, events, sent, _, task = _run(monkeypatch, tmp_path, "K3", thread=thread)
    a_prompt = we.prompt(task, "title", "issue" + thread, "A")
    assert sent == [a_prompt.encode("utf-8")]
    assert record["first_prompt_sha256"] == _sha(a_prompt)
    resumed = [e for e in events if e[0] == "resumed"]
    assert resumed and resumed[0][1:3] == ("rule_stream.jsonl", we.RULE_STEP) and "--continue" in resumed[0][3]
    assert [e[:2] if e[0] != "loop" else e for e in events] == [
        ("first session",), ("saved", "round-1.patch"), ("resumed", "rule_stream.jsonl"), ("saved", "rule.patch"),
        ("loop", "parent", True)]
    assert record["rule_step"] == {"ended": "", "instruction": we.RULE_DIGEST}


def test_k2s_run_is_as_it_was_no_digest_no_step_and_its_loop_saves_round_1(monkeypatch, tmp_path):
    record, events, _, _, _ = _run(monkeypatch, tmp_path, "K2")
    assert events == [("first session",), ("loop", "parent", False)]
    assert "first_prompt_sha256" not in record and "rule_step" not in record


def test_a_k3_run_whose_first_prompt_is_not_arm_as_is_refused_before_the_agent_starts_and_never_scored(monkeypatch, tmp_path):
    real = we.prompt
    monkeypatch.setattr(we, "prompt", lambda task, title, text, arm="": real(task, title, text, arm)
                        + ("\nOne line more." if arm == "K3" else ""))
    record, events, sent, ctx, task = _run(monkeypatch, tmp_path, "K3")
    assert record["status"] == "refused" and "arm A's" in record["why"] and "never scored" in record["why"]
    assert events == [] and sent == [] and record["agent_started"] is False
    assert record["first_prompt_sha256"] != _sha(we.prompt(task, "title", "issue", "A"))
    assert we.latest(ctx.archive, "run", task.id) is None            # ``check`` finds no run to score


def test_a_stop_in_the_rule_step_ends_the_run_with_no_review(monkeypatch, tmp_path):
    record, events, _, _, _ = _run(monkeypatch, tmp_path, "K3", step_ends="cap_cost")
    assert [e[0] for e in events] == ["first session", "saved", "resumed", "saved"]
    assert record["status_detail"] == "cap_cost" and "repairs" not in record
    assert record["rule_step"]["ended"] == "cap_cost"


def test_the_rule_step_resumes_the_session_on_the_runs_clock_tally_and_stop(monkeypatch):
    calls, scripts = [], []
    tally = object()

    def session(ctx, run, agent, command, key, tally_, text, name, *, seconds, stop_usd):
        calls.append({"command": command, "key": key, "tally": tally_, "text": text, "name": name, "seconds": seconds,
                      "stop_usd": stop_usd})
        return "timeout"

    monkeypatch.setattr(we, "run_session", session)
    monkeypatch.setattr(we.time, "monotonic", lambda: 1000.0)
    ctx = SimpleNamespace(docker=SimpleNamespace(sh=lambda agent, script, **kw: scripts.append(script)))
    task = SimpleNamespace(repo="/w/r")
    args = ["exec", "-i", "agent", "claude", "-p", "--disallowedTools", "WebFetch"]
    got = we.rule_step(ctx, task, None, "agent", "base1", args, "key", tally, 7.5, 1000.0 - 100.0)
    assert got == "timeout" and len(calls) == 1
    assert calls[0] == {"command": we.scout_again(args), "key": "key", "tally": tally, "text": we.RULE_STEP,
                        "name": "rule_stream.jsonl", "seconds": we.c.WALL_CLOCK_S - 100.0, "stop_usd": 7.5}
    assert scripts == [we.diff_script("/w/r", "base1", f"{we.WE}/rounds/rule.patch")]
    monkeypatch.setattr(we.time, "monotonic", lambda: 1000.0 + we.c.WALL_CLOCK_S)
    we.rule_step(ctx, task, None, "agent", "base1", args, "key", tally, 7.5, 1000.0)
    assert calls[1]["seconds"] == 60.0                              # a repair's floor, as K's repairs have


def _gated_loop(monkeypatch, after_step):
    kept, sessions = [], []
    on_tree = [we.ProbeRun(1, {"test_critic.py::test_owed": "failed"}, {}, ""),
               we.ProbeRun(0, {"test_critic.py::test_owed": "passed"}, {}, "")]

    class Docker:
        tree = 0

        def sh(self, agent, script, *a, **kw):
            return SimpleNamespace(returncode=0, stdout=f"tree {Docker.tree}".encode(), stderr=b"")

    def session(*a, **kw):
        sessions.append(a[6])
        Docker.tree += 1

    monkeypatch.setattr(we, "run_set", lambda ctx, task, agent, where: on_tree.pop(0))
    monkeypatch.setattr(we, "run_session", session)
    monkeypatch.setattr(we, "scout_again", lambda args: args)
    monkeypatch.setattr(we, "keep_first_patch", lambda ctx, task, agent, base, n=1: kept.append(n))
    monkeypatch.setattr(we, "parent_check", lambda *a: we.ProbeRun(1, {"test_critic.py::test_owed": "failed"}, {}, ""))
    got = we.reviews_until_clean(SimpleNamespace(docker=Docker(), arm="K3"), SimpleNamespace(repo="/w/r"),
                                 SimpleNamespace(write_json=lambda *a: None), "agent", "base", [], "key", None, 1.0, 0.0,
                                 lambda n: {"verdict": "defect", "rule": "r", "why": "w", "test": "t", "seconds": 1.0},
                                 finding="parent", **({"after_step": True} if after_step else {}))
    return got, kept, sessions


def test_after_the_step_the_loop_leaves_round_1_as_the_first_sessions_patch_and_runs_as_k2s(monkeypatch):
    k3, k3_kept, k3_sessions = _gated_loop(monkeypatch, True)
    k2, k2_kept, k2_sessions = _gated_loop(monkeypatch, False)
    assert k2_kept == [1, 2] and k3_kept == [2]                     # review 1 reads the patch saved as rule.patch
    assert k3 == k2 and k3_sessions == k2_sessions and k3["rounds"] == 2 and k3["finding"] == "parent"


def test_check_scores_the_rule_patch_after_round_1_and_every_other_arms_patches_as_before(monkeypatch, tmp_path):
    archive = we.c.Archive(tmp_path)
    task = SimpleNamespace(id="fake-1")
    archive.start("validate", task.id).finish({"fail_to_pass": ["test_issue"], "pass_to_pass": []}, "finished")
    runs = {}
    for arm, names in (("K3", ("round-2", "rule", "round-1")), ("K2", ("round-10", "round-2", "round-1"))):
        run = archive.start("run", task.id, arm=arm)
        (run.path / "rounds").mkdir()
        for name in names:
            (run.path / "rounds" / f"{name}.patch").write_text(name, encoding="utf-8")
        run.write("final.patch", "final")
        run.finish({"arm": arm, "agent_started": True}, "finished")
        runs[arm] = run.rel
    scored = []
    monkeypatch.setattr(we, "_oracle_run", lambda ctx, task, *, patch, label, **kw: (
        scored.append(label), {"results": {"test_issue": "passed" if patch.read_text() == "rule" else "failed"}})[1])
    ctx = SimpleNamespace(archive=archive)
    k3 = we.check(ctx, task, runs["K3"])
    assert [row["patch"] for row in k3["checks"]] == ["round-1", "rule", "round-2", "final"]
    assert [row["passes"] for row in k3["checks"]] == [False, True, False, False]
    k2 = we.check(ctx, task, runs["K2"])
    assert [row["patch"] for row in k2["checks"]] == ["round-1", "round-2", "round-10", "final"]
