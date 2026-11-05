# SPDX-License-Identifier: Apache-2.0
"""Arm K3 (PLAN.md, 2026-11-05, morning, later, items 1 and 4): K2 plus one step. The first session is arm A's prompt byte for
byte, checked by digest before the agent starts; its patch is kept in the archive as ``round-1.patch``; then the same session is
resumed with the rule step; the patch after it is kept as ``rule.patch`` and scored beside the others; then K2's loop runs
unchanged. Each of those steps goes on only from a recognised success (``step_ending``). A K3 run that stops is saved and read
as K2's run with the same ending is; only a run with no Claude Code baseline, refused at its first prompt's check or ended
before its first patch was kept (``no_first_patch``), is never a row and never offered to ``check`` (``scored_run``)."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import compare  # noqa: E402
import loop  # noqa: E402
import run as we  # noqa: E402

SONNET = we.R3_MODELS[1]
SUCCESS = {"type": "result", "subtype": "success", "is_error": False, "session_id": "s1"}
FIRST, AFTER_RULE = b"first-session-tree", b"after-rule-tree"


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


@pytest.mark.parametrize(("stop", "result", "code", "ending"), [
    ("", SUCCESS, 0, "finished"),
    ("timeout", SUCCESS, 0, "timeout"),
    ("cap_cost", SUCCESS, 0, "cap_cost"),
    ("", None, 0, "error"),                                                   # no result of its own
    ("", SUCCESS, 1, "error"),                                                # a success that exited 1
    ("", SUCCESS, None, "error"),                                             # an exit nobody read
    ("", {**SUCCESS, "is_error": True}, 0, "error"),
    ("", {**SUCCESS, "is_error": None}, 0, "error"),                          # no word on error is no success
    ("", {**SUCCESS, "subtype": "future_terminal_status"}, 0, "error"),       # an ending nobody knows
    ("", {**SUCCESS, "subtype": "error_max_turns", "is_error": True}, 1, "cap_turns"),
    ("", {**SUCCESS, "subtype": "error_max_budget_usd", "is_error": True}, 1, "cap_cost"),
])
def test_step_ending_lets_only_a_recognised_success_through(stop, result, code, ending):
    assert we.step_ending(stop, result, code) == ending


def test_a_capture_is_a_success_only_with_its_command_at_0_and_its_file_kept():
    assert we.step_ending("", we.CAPTURED, 0) == "finished"
    assert we.step_ending("", None, 0) == we.step_ending("", we.CAPTURED, 1) == "error"


class _Stdin(io.BytesIO):
    def __init__(self, sent):
        super().__init__()
        self.sent = sent

    def close(self):
        self.sent.append(self.getvalue())
        super().close()


class _Docker:
    """The agent's container in memory, with ``docker cp``'s own rules: a folder copied to a path that exists goes inside it."""

    def __init__(self, task, events, sent, fail=(), tree_ok=True):
        self.task, self.events, self.sent, self.fail, self.tree_ok = task, events, sent, set(fail), tree_ok
        self.files, self.tree, self.loop_kw, self.deadlines = {}, FIRST, [], []

    @staticmethod
    def done(code=0, stdout=b""):
        return SimpleNamespace(returncode=code, stdout=stdout, stderr=b"")

    def __call__(self, *a, **kw):
        if a[0] == "cp" and ":" in a[1]:
            src, dest = a[1].split(":", 1)[1], Path(a[2])
            if src in self.files:
                target = dest / Path(src).name if dest.is_dir() else dest
                if not target.parent.is_dir():
                    return self.done(1)
                target.write_bytes(self.files[src])
                return self.done()
            kids = {p: b for p, b in self.files.items() if p.startswith(src + "/")}
            if not kids or not dest.parent.is_dir():
                return self.done(1)
            root = dest / Path(src).name if dest.exists() else dest
            for path, body in kids.items():
                (root / path[len(src) + 1:]).parent.mkdir(parents=True, exist_ok=True)
                (root / path[len(src) + 1:]).write_bytes(body)
        return self.done()

    def sh(self, ctr, script, *a, **kw):
        if script == we.TREE_STATE:
            return self.done(stdout=json.dumps({"head": self.task.parent, "tree": "tree" if self.tree_ok else "another",
                                                "dirty": 0, "commits": 1}).encode())
        if script == we.ARM_STATE:
            return self.done(stdout=b'{"mcp_servers": [], "skills": []}')
        saved = re.search(r'git diff --cached --binary .* > "([^"]+)"', script)
        if saved:
            if "/rounds/" in saved[1]:
                self.events.append(("saved", Path(saved[1]).name))
            if Path(saved[1]).name in self.fail:
                return self.done(1)
            self.files[saved[1]] = self.tree
        return self.done()

    def popen(self, *a, **kw):
        self.events.append(("first session",))
        return SimpleNamespace(stdin=_Stdin(self.sent), stdout=io.BytesIO(json.dumps(SUCCESS).encode() + b"\n"),
                               stderr=io.BytesIO(), poll=lambda: 0, wait=lambda **kw: 0)


def _run(monkeypatch, tmp_path, arm, *, step_ends="", thread="", fail=(), first=SUCCESS, agent_rewrites=False,
         loop_writes=False, loop_ends=None, loop_result=None, key_ok=True, tree_ok=True, first_stop="", loop_late=False,
         rule_session_id=""):
    """``_run_task`` for ``arm`` with fakes only: no model, no container. Returns the record, what happened in order, the
    prompts sent, the context and the task."""
    task, events, sent = we.load_tasks()[0], [], []
    docker = _Docker(task, events, sent, fail, tree_ok)
    real_read = we.read_stream

    def read(*a, **kw):             # the first session's stream: as it is, or stopped as ``first_stop`` says
        docker.deadlines.append(kw.get("deadline"))
        return first_stop or real_read(*a, **kw)

    monkeypatch.setattr(we, "read_stream", read)
    if first is not SUCCESS:
        docker.popen = lambda *a, **kw: (events.append(("first session",)), SimpleNamespace(
            stdin=_Stdin(sent), stdout=io.BytesIO(json.dumps(first).encode() + b"\n"), stderr=io.BytesIO(),
            poll=lambda: 1, wait=lambda **kw: 1))[1]

    def session(ctx, run, agent, command, key, tally, text, name, *, seconds, stop_usd, codes=None, deadline=None):
        events.append(("resumed", name, text, list(command)))
        docker.tree = AFTER_RULE
        if agent_rewrites:          # the agent owns the container's rounds folder, and writes over the first patch there
            docker.files[f"{we.WE}/rounds/round-1.patch"] = b"the agent's own round-1"
        if not step_ends:
            tally.feed({**SUCCESS, **({"session_id": rule_session_id} if rule_session_id else {})})
        if codes is not None:
            codes.append(0)
        return step_ends

    def review_loop(*a, finding="any", after_step=False, **kw):
        events.append(("loop", finding, after_step))
        docker.loop_kw.append(kw)
        if loop_writes:             # the loop's own capture before review 2
            we.keep_first_patch(a[0], a[1], a[3], a[4], 2)
        if loop_ends:               # a repair's own result, which the run's status is read from as K2's is
            a[7].feed(dict(loop_ends))
        if loop_late:               # a review that ended after the run's deadline, as the watchdog records it
            kw["deadline"].ends.append(("review 1", kw["deadline"].deadline + 1.0))
        return loop_result or {"status": "no_counterexample_in_domain"}

    monkeypatch.setattr(we, "key_check", lambda *a, **kw: {"ok": key_ok})
    monkeypatch.setattr(we, "start_net", lambda *a: ("net", "proxy"))
    monkeypatch.setattr(we, "read_thread", lambda *a: thread)
    monkeypatch.setattr(we, "run_session", session)
    monkeypatch.setattr(we, "reviews_until_clean", review_loop)
    ctx = we.Context(archive=we.c.Archive(tmp_path / "archive"), docker=docker, key_reader=lambda: "dummy-key-value",
                     issue_reader=lambda task: ("title", "issue"), tree_reader=lambda task: "tree", g6={"commit": "0" * 40},
                     variant=we.ROUND3, arm=arm, environ={})
    record = we._run_task(ctx, task, dry=False, cap=10.0, stop=10.5)
    return record, events, sent, ctx, task


@pytest.mark.parametrize("thread", ["", "\n\nThe issue's discussion so far:\n\nmember on 2026-01-01: the rule is ..."])
def test_a_k3_run_sends_arm_as_prompt_keeps_round_1_resumes_with_the_rule_step_keeps_rule_then_runs_k2s_loop(monkeypatch, tmp_path,
                                                                                                            thread):
    """The discussion is read as K2 reads it (``gets_thread``), and arm A's builder is given the same text."""
    record, events, sent, ctx, task = _run(monkeypatch, tmp_path, "K3", thread=thread)
    a_prompt = we.prompt(task, "title", "issue" + thread, "A")
    assert sent == [a_prompt.encode("utf-8")]
    assert record["first_prompt_sha256"] == _sha(a_prompt)
    resumed = [e for e in events if e[0] == "resumed"]
    assert resumed and resumed[0][1:3] == ("rule_stream.jsonl", we.RULE_STEP) and "--continue" in resumed[0][3]
    assert [e[:2] if e[0] != "loop" else e for e in events] == [
        ("first session",), ("saved", "round-1.patch"), ("resumed", "rule_stream.jsonl"), ("saved", "rule.patch"),
        ("loop", "parent", True)]
    assert record["rule_step"] == {"ended": "", "at": "", "instruction": we.RULE_DIGEST}
    folder = ctx.archive.root / record["run"]
    assert we.baseline_of(folder).read_bytes() == FIRST and (folder / "rounds" / "rule.patch").read_bytes() == AFTER_RULE
    assert not (folder / "rounds" / "round-1.patch").exists() and we.has_baseline(ctx.archive, record["run"])
    assert record["first_patch_sha256"] == hashlib.sha256(FIRST).hexdigest() and record["status"] == "finished"
    deadline = ctx.docker.deadlines[0]                              # the run's one deadline, read by every step after it
    assert ctx.docker.loop_kw[0]["deadline"].deadline == deadline and not ctx.docker.loop_kw[0]["deadline"].late()
    assert [step for step, _ in record["step_ends"]] == ["first session", "capture round-1.patch", "rule session",
                                                          "capture rule.patch"]


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


EXPENSIVE = {"type": "assistant", "session_id": "s1", "message": {"id": "m1", "model": SONNET,
                                                                   "usage": {"output_tokens": 2000000}}}
CAPPED = {"type": "result", "subtype": "error_max_budget_usd", "is_error": True, "session_id": "s1"}


@pytest.mark.parametrize(("case", "at", "ended", "seen"), [
    ({"first": {**SUCCESS, "subtype": "error_max_turns", "is_error": True}}, "first session", "cap_turns", ["first session"]),
    ({"first": {**SUCCESS, "subtype": "future_terminal_status"}}, "first session", "error", ["first session"]),
    ({"first": EXPENSIVE}, "first session", "cap_cost", ["first session"]),
    ({"fail": ("round-1.patch",)}, "first patch", "error", ["first session", "saved"]),
])
def test_a_k3_run_that_ends_before_its_first_patch_is_kept_has_no_baseline_no_row_and_no_check(monkeypatch, tmp_path, case, at,
                                                                                               ended, seen):
    record, events, _, ctx, task = _run(monkeypatch, tmp_path, "K3", **case)
    assert [e[0] for e in events] == seen and record["rule_step"]["ended"] == ended and record["rule_step"]["at"] == at
    _has_no_baseline(monkeypatch, record, ctx, task)


def _has_no_baseline(monkeypatch, record, ctx, task):
    """A K3 run with no Claude Code baseline, as the archive alone says: ``no_first_patch``, no row, and no check."""
    assert record["status"] == we.NO_FIRST_PATCH == "no_first_patch" and "repairs" not in record
    assert not we.has_baseline(ctx.archive, record["run"]) and not we.scored_run(ctx.archive, record)
    assert not we.baseline_of(ctx.archive.root / record["run"]).exists()
    ctx.archive.start("validate", task.id).finish({"fail_to_pass": ["t"], "pass_to_pass": []}, "finished")
    monkeypatch.setattr(we, "_oracle_run", lambda *a, **kw: pytest.fail("a run with no first patch has no baseline to score"))
    with pytest.raises(SystemExit, match="no Claude Code baseline"):
        we.check(ctx, task, record["run"])
    monkeypatch.setattr(loop, "tool", lambda *a, **kw: json.dumps(record))
    with pytest.raises(SystemExit, match="no Claude Code baseline"):
        loop.one(ctx.archive.root, "g6", "K3", task.id, 1)
    assert not (ctx.archive.root / "loop").exists()


@pytest.mark.parametrize("how", ["key check", "tree", "copy timed out", "copy failed"])
def test_every_way_to_end_before_the_first_patch_is_kept_is_no_first_patch_read_from_the_archive(monkeypatch, tmp_path, how):
    """The preflight statuses, and an exception before the capture, end as every such run does: the one answer is the archive's."""
    if how.startswith("copy"):
        real = we.copy_out
        error = subprocess.TimeoutExpired("docker cp", 900) if how == "copy timed out" else OSError("the disk is full")

        def copy(docker, agent, src, dest):
            if src.endswith("/rounds/round-1.patch"):
                raise error
            return real(docker, agent, src, dest)

        monkeypatch.setattr(we, "copy_out", copy)
    record, events, _, ctx, task = _run(monkeypatch, tmp_path, "K3", key_ok=how != "key check", tree_ok=how != "tree")
    assert not any(e[0] in ("resumed", "loop") for e in events)
    _has_no_baseline(monkeypatch, record, ctx, task)


class _Interrupted(BaseException):
    pass


def test_a_k3_run_interrupted_before_its_first_patch_is_kept_is_never_checked_after_a_restart(monkeypatch, tmp_path):
    real = we.copy_out

    def copy(docker, agent, src, dest):
        if src.endswith("/rounds/round-1.patch"):
            raise _Interrupted()                                    # the runner itself stops, as a kill stops it
        return real(docker, agent, src, dest)

    monkeypatch.setattr(we, "copy_out", copy)
    with pytest.raises(_Interrupted):
        _run(monkeypatch, tmp_path, "K3")
    archive, task = we.c.Archive(tmp_path / "archive"), we.load_tasks()[0]   # a restart reads the archive as it was left
    rel = next(r["run"] for r in archive.rows() if r.get("kind") == "run")
    assert not (archive.root / rel / "run.json").exists() and not we.has_baseline(archive, rel)
    archive.start("validate", task.id).finish({"fail_to_pass": ["t"], "pass_to_pass": []}, "finished")
    monkeypatch.setattr(we, "_oracle_run", lambda *a, **kw: pytest.fail("no baseline, so nothing is scored"))
    with pytest.raises(SystemExit, match="no Claude Code baseline"):
        we.check(SimpleNamespace(archive=archive), task, rel)


def _row_of(monkeypatch, root, record, arm):
    monkeypatch.setattr(loop, "tool", lambda *a, **kw: json.dumps(record))
    return loop.one(root, "g6", arm, record["task"], 1)


@pytest.mark.parametrize(("k3", "k2", "labels"), [
    ({"step_ends": "cap_cost"}, {"first": EXPENSIVE}, ["round-1", "final"]),           # stopped in the rule step
    ({"loop_ends": CAPPED}, {"loop_ends": CAPPED}, ["round-1", "rule", "final"]),      # stopped in the review loop
    ({"step_ends": "timeout"}, {"first_stop": "timeout"}, ["round-1", "final"]),       # timed out, by stop symmetry
])
def test_a_stopped_k3_run_is_saved_and_read_as_k2s_with_the_same_ending_and_check_scores_its_first_patch(monkeypatch, tmp_path,
                                                                                                        k3, k2, labels):
    status = "timeout" if k3.get("step_ends") == "timeout" else "cap_cost"
    ours, _, _, ctx, task = _run(monkeypatch, tmp_path, "K3", **k3)
    theirs, *_ = _run(monkeypatch, tmp_path, "K2", **k2)
    assert ours["status"] == theirs["status"] == status and we.scored_run(ctx.archive, ours)
    rows = [_row_of(monkeypatch, ctx.archive.root, ours, "K3"), _row_of(monkeypatch, ctx.archive.root, theirs, "K2")]
    assert [{k: v for k, v in row.items() if k not in ("arm", "run", "cost_usd")} for row in rows] == [
        {"task": task.id, "repeat": 1, "status": status, "why": None, "passes": False, "failing": None, "tests": []}] * 2
    got = compare.saved(ctx.archive.root)
    assert got[("K3", task.id, 1)] == got[("K2", task.id, 1)] and got[("K3", task.id, 1)]["maintainers"] is False
    ctx.archive.start("validate", task.id).finish({"fail_to_pass": ["t"], "pass_to_pass": []}, "finished")
    monkeypatch.setattr(we, "_oracle_run", lambda *a, **kw: {"results": {"t": "passed"}})
    assert [row["patch"] for row in we.check(ctx, task, ours["run"])["checks"]] == labels


def test_the_first_patch_is_in_the_archive_before_the_step_and_nothing_after_it_replaces_it(monkeypatch, tmp_path):
    """The loop's later rounds come in beside the two captures through a folder of their own, so ``docker cp`` never puts the
    container's ``rounds`` inside the archive's, and nothing the agent leaves in the container replaces what was kept."""
    record, _, _, ctx, task = _run(monkeypatch, tmp_path, "K3", agent_rewrites=True, loop_writes=True)
    rounds = ctx.archive.root / record["run"] / "rounds"
    assert record["status"] == "finished" and we.has_baseline(ctx.archive, record["run"])
    assert we.baseline_of(rounds.parent).read_bytes() == FIRST and (rounds / "rule.patch").read_bytes() == AFTER_RULE
    assert (rounds / "round-2.patch").read_bytes() == AFTER_RULE and not (rounds / "rounds").exists()
    assert not (rounds / "round-1.patch").exists()                 # the agent's own round-1 is no baseline, and is not kept
    ctx.archive.start("validate", task.id).finish({"fail_to_pass": ["t"], "pass_to_pass": []}, "finished")
    monkeypatch.setattr(we, "_oracle_run", lambda *a, **kw: {"results": {"t": "passed"}})
    assert [row["patch"] for row in we.check(ctx, task, record["run"])["checks"]] == ["round-1", "rule", "round-2", "final"]


def test_a_capture_holding_the_runs_key_fails_its_step_and_keeps_nothing(tmp_path):
    """A kept patch is its real bytes, as the final patch is, so a token-shaped string in the code stays as it is; the run's own
    key never stays in the archive."""
    archive = we.c.Archive(tmp_path / "archive", redact=we.c.Redactor("the-runs-own-key-1234"))
    run = archive.start("run", "fake-1", arm="K3")
    body = {"value": b""}

    class Docker:
        def __call__(self, *a, **kw):
            Path(a[2]).write_bytes(body["value"])
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        def sh(self, *a, **kw):
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    ctx, task = SimpleNamespace(docker=Docker(), archive=archive), SimpleNamespace(repo="/w/r")
    body["value"] = b"+key = 'the-runs-own-key-1234'\n"
    assert we.kept(ctx, task, "agent", "base", run, "round-1.patch") == "error"
    assert not we.baseline_of(run.path).exists() and not we.has_baseline(archive, run.rel)
    body["value"] = b"+token = 'sk-ant-abcdefgh1234'\n"
    assert we.kept(ctx, task, "agent", "base", run, "rule.patch") == "finished"
    assert (run.path / "rounds" / "rule.patch").read_bytes() == body["value"]


def test_the_rule_step_resumes_the_session_on_the_runs_clock_tally_and_stop_and_never_past_its_deadline(monkeypatch, tmp_path):
    calls, tally = [], we.c.Tally()
    tally.feed(dict(SUCCESS))                                       # the first session's own result, which the step continues

    def session(ctx, run, agent, command, key, tally_, text, name, *, seconds, stop_usd, codes=None, deadline=None):
        calls.append({"command": command, "key": key, "tally": tally_, "text": text, "name": name,
                      "deadline": deadline.deadline, "stop_usd": stop_usd})
        codes.append(0)
        tally_.feed(dict(SUCCESS))
        return ""

    monkeypatch.setattr(we, "run_session", session)
    monkeypatch.setattr(we, "kept", lambda ctx, task, agent, base, run, name, deadline: (calls.append((name, deadline.deadline)),
                                                                                         "finished")[1])
    monkeypatch.setattr(we.time, "monotonic", lambda: 1000.0)
    args = ["exec", "-i", "agent", "claude", "-p", "--disallowedTools", "WebFetch"]
    assert we.rule_step(None, None, None, "agent", "base1", args, "key", tally, 7.5, 1000.0 - 100.0) == "finished"
    deadline = we.deadline_of(1000.0 - 100.0)
    assert deadline == 900.0 + we.c.WALL_CLOCK_S
    assert calls == [{"command": we.scout_again(args), "key": "key", "tally": tally, "text": we.RULE_STEP,
                      "name": "rule_stream.jsonl", "deadline": deadline, "stop_usd": 7.5}, ("rule.patch", deadline)]
    calls.clear()
    assert we.rule_step(None, None, None, "agent", "base1", args, "key", tally, 7.5, 1000.0 - we.c.WALL_CLOCK_S) == "timeout"
    assert calls == []                                              # no session, and no minute past the run's deadline
    monkeypatch.setattr(we, "run_session", lambda *a, codes=None, **kw: (codes.append(0), "")[1])
    assert we.rule_step(None, None, None, "agent", "base1", args, "key", tally, 7.5, 1000.0 - 100.0) == "error"  # no own result


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
    assert k2_kept == [1, 2] and k3_kept == [2]                     # review 1 reads the patch kept as rule.patch
    assert k3 == k2 and k3_sessions == k2_sessions and k3["rounds"] == 2 and k3["finding"] == "parent"


def test_check_scores_the_rule_patch_after_round_1_and_every_other_arms_patches_as_before(monkeypatch, tmp_path):
    archive = we.c.Archive(tmp_path)
    task = SimpleNamespace(id="fake-1")
    archive.start("validate", task.id).finish({"fail_to_pass": ["test_issue"], "pass_to_pass": []}, "finished")
    runs = {}
    for arm, names in (("K3", ("round-2", "rule")), ("K2", ("round-10", "round-2", "round-1", "rule"))):
        run = archive.start("run", task.id, arm=arm)
        if arm == "K3":
            _keep_baseline(archive, run, b"round-1")
        (run.path / "rounds").mkdir(exist_ok=True)
        for name in names:
            (run.path / "rounds" / f"{name}.patch").write_text(name, encoding="utf-8")
        run.write("final.patch", "final")
        run.finish({"arm": arm, "agent_started": True}, "finished")
        runs[arm] = run.rel
    monkeypatch.setattr(we, "_oracle_run", lambda ctx, task, *, patch, label, **kw: {
        "results": {"test_issue": "passed" if patch.read_text() == "rule" else "failed"}})
    ctx = SimpleNamespace(archive=archive)
    k3 = we.check(ctx, task, runs["K3"])
    assert [row["patch"] for row in k3["checks"]] == ["round-1", "rule", "round-2", "final"]
    assert [row["passes"] for row in k3["checks"]] == [False, True, False, False]
    k2 = we.check(ctx, task, runs["K2"])
    assert [row["patch"] for row in k2["checks"]] == ["round-1", "round-2", "round-10", "final"]


class _CopyDocker:
    """``docker`` for one capture: its command exits 0, and its copy writes ``body``, after ``takes`` seconds of ``now``."""

    def __init__(self, body, now=None, takes=0.0):
        self.body, self.now, self.takes, self.calls = body, now, takes, []

    def __call__(self, *a, **kw):
        self.calls.append(a[0])
        if self.now is not None:
            self.now[0] += self.takes
        Path(a[2]).write_bytes(self.body)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    def sh(self, *a, **kw):
        self.calls.append("sh")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")


def _keep_baseline(archive, run, body):
    ctx = SimpleNamespace(docker=_CopyDocker(body), archive=archive)
    assert we.kept(ctx, SimpleNamespace(repo="/w/r"), "agent", "base", run, we.FIRST_PATCH) == "finished"


def test_the_baseline_is_kept_once_in_a_folder_of_its_own_and_scored_only_while_it_has_its_kept_digest(monkeypatch, tmp_path):
    archive = we.c.Archive(tmp_path / "archive")
    archive.start("validate", "fake-1").finish({"fail_to_pass": ["t"], "pass_to_pass": []}, "finished")
    run = archive.start("run", "fake-1", arm="K3")
    _keep_baseline(archive, run, b"the first session's tree")
    baseline = we.baseline_of(run.path)
    assert baseline.parent.name == we.BASELINE and baseline.parent.parent == run.path and baseline.name == we.FIRST_PATCH
    assert [r["sha256"] for r in archive.rows() if r.get("event") == "kept"] == [hashlib.sha256(baseline.read_bytes()).hexdigest()]
    run.write("final.patch", "final")
    run.finish({"arm": "K3", "agent_started": True}, "finished")
    seen = []
    monkeypatch.setattr(we, "_oracle_run", lambda ctx, task, *, patch, label, **kw: (seen.append((label, patch.read_bytes())),
                                                                                    {"results": {"t": "passed"}})[1])
    ctx, task = SimpleNamespace(archive=archive), SimpleNamespace(id="fake-1")
    assert [row["patch"] for row in we.check(ctx, task, run.rel)["checks"]] == ["round-1", "final"]
    assert seen[0] == ("round-1", b"the first session's tree")
    baseline.write_bytes(b"something else")                         # its bytes no longer have the digest kept with them
    assert not we.has_baseline(archive, run.rel) and we.baseline_bytes(archive, run.rel) is None
    with pytest.raises(SystemExit, match="baseline changed"):       # a refusal, never a score on round-1
        we.check(ctx, task, run.rel)


def test_check_scores_the_baseline_bytes_it_verified_and_never_reads_the_path_again(monkeypatch, tmp_path):
    archive = we.c.Archive(tmp_path / "archive")
    archive.start("validate", "fake-1").finish({"fail_to_pass": ["t"], "pass_to_pass": []}, "finished")
    run = archive.start("run", "fake-1", arm="K3")
    _keep_baseline(archive, run, b"the first session's tree")
    run.write("final.patch", "final")
    run.finish({"arm": "K3", "agent_started": True}, "finished")
    begun = archive.start

    def start(kind, *a, **kw):
        folder = begun(kind, *a, **kw)
        if kind == "check":                                         # after the baseline was read and checked
            we.baseline_of(run.path).write_bytes(b"replaced while the check ran")
        return folder

    seen = []
    monkeypatch.setattr(archive, "start", start)
    monkeypatch.setattr(we, "_oracle_run", lambda ctx, task, *, patch, label, **kw: (seen.append((label, patch.read_bytes())),
                                                                                    {"results": {"t": "passed"}})[1])
    we.check(SimpleNamespace(archive=archive), SimpleNamespace(id="fake-1"), run.rel)
    assert seen[0] == ("round-1", b"the first session's tree")


def _link(target: Path, link: Path) -> None:
    if os.name == "nt":                                             # a junction needs no symbolic-link privilege
        import _winapi
        _winapi.CreateJunction(str(target.resolve()), str(link.absolute()))
    else:
        link.symlink_to(target.resolve(), target_is_directory=True)


@pytest.mark.parametrize("copy", ["rounds", "claude_projects"])
def test_no_late_copy_follows_a_link_or_writes_masks_or_replaces_in_the_baseline(monkeypatch, tmp_path, copy):
    archive = we.c.Archive(tmp_path / "archive")
    run = archive.start("run", "fake-1", arm="K3")
    body = b"+token = 'sk-ant-abcdefgh1234'\n"
    _keep_baseline(archive, run, body)
    baseline = we.baseline_of(run.path)

    def copy_out(docker, agent, src, dest):
        dest.mkdir()
        _link(baseline.parent, dest / "alias")                      # the container's copy holds a link into the baseline
        (dest / "round-2.patch").write_bytes(body)
        return True

    monkeypatch.setattr(we, "copy_out", copy_out)
    ctx = SimpleNamespace(archive=archive, docker=None)
    if copy == "rounds":
        we.merge_rounds(ctx, "agent", run)
    else:
        we.copy_in(ctx, "agent", "/home/tester/.claude/projects", run, copy)
    assert baseline.read_bytes() == body and we.has_baseline(archive, run.rel)
    assert (run.path / copy / "round-2.patch").read_bytes() == b"+token = '[key withheld]'\n"
    assert not (run.path / copy / "alias").exists() and sorted(p.name for p in run.path.iterdir()) == sorted([we.BASELINE, copy])


def test_a_capture_that_cannot_start_or_finish_before_the_deadline_ends_timeout_and_keeps_nothing(monkeypatch, tmp_path):
    archive = we.c.Archive(tmp_path / "archive")
    run = archive.start("run", "fake-1", arm="K3")
    now = [1000.0]
    monkeypatch.setattr(we.time, "monotonic", lambda: now[0])
    task = SimpleNamespace(repo="/w/r")
    docker = _CopyDocker(b"tree", now, takes=5.0)
    ctx = SimpleNamespace(docker=docker, archive=archive)
    assert we.kept(ctx, task, "agent", "base", run, we.FIRST_PATCH, 1000.0) == "timeout" and docker.calls == []  # reached
    assert we.kept(ctx, task, "agent", "base", run, we.FIRST_PATCH, 1002.0) == "timeout"                       # crossed in it
    assert not we.baseline_of(run.path).exists() and not we.has_baseline(archive, run.rel)
    assert we.kept(ctx, task, "agent", "base", run, we.RULE_PATCH, 1002.0) == "timeout"
    assert not (run.path / "rounds" / we.RULE_PATCH).exists()
    assert we.kept(ctx, task, "agent", "base", run, we.FIRST_PATCH, now[0] + 6.0) == "finished"
    assert we.has_baseline(archive, run.rel)


@pytest.mark.parametrize(("starts_in", "sent"), [(30.0, []), (5.0, [b"text"])])
def test_a_resumed_session_reads_the_runs_deadline_and_gets_no_prompt_once_it_has_passed(monkeypatch, tmp_path, starts_in, sent):
    now, deadlines, written = [1000.0], [], []

    class Stdin(io.BytesIO):
        def close(self):
            written.append(self.getvalue())
            super().close()

    def popen(*a, **kw):
        now[0] += starts_in                                         # 30 s: the process starts after the deadline
        return SimpleNamespace(stdin=Stdin(), stdout=io.BytesIO(), poll=lambda: 0, wait=lambda **kw: 0)

    def read_stream(lines, tally, *, deadline, **kw):
        deadlines.append(deadline)
        return ""

    monkeypatch.setattr(we.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(we, "read_stream", read_stream)
    run = we.c.Archive(tmp_path / "archive").start("run", "fake-1", arm="K3")
    ctx = SimpleNamespace(docker=SimpleNamespace(popen=popen), client_env=lambda key: {}, archive=run.archive)
    codes = []
    ended = we.run_session(ctx, run, "agent", ["claude"], "key", we.c.Tally(), "text", "rule_stream.jsonl", seconds=20.0,
                           stop_usd=1.0, codes=codes, deadline=1020.0)
    assert [w for w in written if w] == sent and codes == [0]
    assert (ended, deadlines) == (("timeout", []) if not sent else ("", [1020.0]))


def _timed_loop(monkeypatch, *, deadline, review_takes=0.0, start=0.0):
    """K2's gated loop with a clock: a review takes ``review_takes`` seconds; the parent fails the owed case, so a repair follows."""
    now, sessions, critiques = [start], [], []
    on_tree = [we.ProbeRun(1, {"test_critic.py::test_owed": "failed"}, {}, ""),
               we.ProbeRun(0, {"test_critic.py::test_owed": "passed"}, {}, "")]

    class Docker:
        tree = 0

        def sh(self, agent, script, *a, **kw):
            return SimpleNamespace(returncode=0, stdout=f"tree {Docker.tree}".encode(), stderr=b"")

    def session(*a, **kw):
        sessions.append(kw)
        Docker.tree += 1

    def critique(n):
        critiques.append(n)
        now[0] += review_takes
        return {"verdict": "defect", "rule": "r", "why": "w", "test": "t", "seconds": review_takes}

    monkeypatch.setattr(we.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(we, "run_set", lambda ctx, task, agent, where: on_tree.pop(0))
    monkeypatch.setattr(we, "run_session", session)
    monkeypatch.setattr(we, "scout_again", lambda args: args)
    monkeypatch.setattr(we, "keep_first_patch", lambda *a: None)
    monkeypatch.setattr(we, "parent_check", lambda *a: we.ProbeRun(1, {"test_critic.py::test_owed": "failed"}, {}, ""))
    got = we.reviews_until_clean(SimpleNamespace(docker=Docker(), arm="K3"), SimpleNamespace(repo="/w/r"),
                                 SimpleNamespace(write_json=lambda *a: None), "agent", "base", [], "key", None, 1.0, 0.0,
                                 critique, finding="parent", after_step=True, deadline=deadline)
    return got, sessions, critiques


def test_k2s_loop_after_the_step_reads_the_runs_one_deadline_and_no_review_follows_it(monkeypatch):
    got, sessions, critiques = _timed_loop(monkeypatch, deadline=0.0)            # reached before review 1
    assert critiques == [] and sessions == [] and got["ended"] == "timeout" and got["status"] == "inconclusive"
    got, sessions, critiques = _timed_loop(monkeypatch, deadline=50.0, review_takes=60.0)   # review 1 crosses it
    assert critiques == [1] and sessions == [] and got["ended"] == "timeout"
    got, sessions, critiques = _timed_loop(monkeypatch, deadline=500.0, review_takes=1.0)
    assert sessions[0]["deadline"].deadline == 500.0 and sessions[0]["seconds"] <= 500.0 - 1.0  # no allowance of its own
    assert critiques == [1, 2] and "ended" not in got and got["status"] == "no_counterexample_in_domain"


def test_a_k3_run_with_a_step_that_ended_after_the_deadline_ends_timeout_as_finish_reads_it_and_is_k2s_row(monkeypatch, tmp_path):
    """Whatever the step returned: the loop says it reproduced nothing, and the run is "timeout" because its end was late."""
    record, _, _, ctx, task = _run(monkeypatch, tmp_path, "K3", loop_late=True)
    assert record["status"] == "timeout" and record["status_detail"] == "finished" and we.has_baseline(ctx.archive, record["run"])
    assert ctx.docker.loop_kw[0]["deadline"].deadline == ctx.docker.deadlines[0] and record["step_ends"][-1][0] == "review 1"
    row = _row_of(monkeypatch, ctx.archive.root, record, "K3")
    assert (row["status"], row["passes"], row["failing"]) == ("timeout", False, None)        # as K2's timed-out runs are


def test_the_watchdog_fires_once_ends_every_process_it_watches_sets_its_flag_and_then_no_step_starts():
    now, ended = [0.0], []
    dog = we.Watchdog(10.0, clock=lambda: now[0])
    with dog.watching(lambda: ended.append("session")), dog.watching(lambda: ended.append("review")):
        assert dog.may_start("review 1") and not dog.end("review 1") and dog.left() == 10.0
        now[0] = 10.0                                               # the deadline, exactly
        assert dog.due() and dog.due() and sorted(ended) == ["review", "session"]
    assert dog.fired.is_set() and not dog.may_start("repair 1") and dog.late() and dog.left() == 0.0
    with dog.watching(lambda: ended.append("after")):
        pass
    assert ended.count("after") == 0 and len(ended) == 2


def test_the_watchdogs_timer_fires_by_itself_at_the_deadline():
    ended = []
    dog = we.Watchdog(time.monotonic() + 0.05)
    with dog.watching(lambda: ended.append("session")):
        dog.start()
        try:
            assert dog.fired.wait(5.0) and ended == ["session"]
        finally:
            dog.stop()


def test_the_watchdogs_docker_bounds_each_call_by_what_is_left_and_never_bounds_cleanup():
    now, calls = [0.0], []
    dog = we.Watchdog(10.0, clock=lambda: now[0])

    class Raw:
        def __call__(self, *a, timeout=None, **kw):
            calls.append((a[0], timeout))
            return SimpleNamespace(returncode=0)

        def sh(self, ctr, script, *a, timeout=600, **kw):
            calls.append(("sh", timeout))
            return SimpleNamespace(returncode=0)

    docker = dog.docker(Raw())
    docker("cp", "agent:/x", "/y", timeout=900)
    docker.sh("agent", "true", timeout=300)
    docker("exec", "agent", "true")
    assert calls == [("cp", 10.0), ("sh", 10.0), ("exec", 10.0)]
    now[0] = 10.0
    with pytest.raises(subprocess.TimeoutExpired):                  # no docker process starts after the deadline
        docker("cp", "agent:/x", "/y", timeout=900)
    docker("rm", "-f", "agent", timeout=300)
    assert calls[-1] == ("rm", 300) and dog.fired.is_set() and we.unbounded(docker) is docker.raw


def test_a_capture_is_ended_mid_way_when_the_watchdog_fires(monkeypatch, tmp_path):
    now = [100.0]
    monkeypatch.setattr(we.time, "monotonic", lambda: now[0])
    archive = we.c.Archive(tmp_path / "archive")
    run = archive.start("run", "fake-1", arm="K3")

    class Docker:
        def sh(self, *a, timeout, **kw):
            return SimpleNamespace(returncode=0)

        def __call__(self, *a, timeout, **kw):                      # the transfer needs a minute; its process ends at its timeout
            now[0] += min(60.0, timeout)
            if timeout < 60.0:
                raise subprocess.TimeoutExpired("docker cp", timeout)
            Path(a[2]).write_bytes(b"tree")
            return SimpleNamespace(returncode=0)

    dog = we.Watchdog(105.0)
    ctx = SimpleNamespace(docker=Docker(), archive=archive)
    assert we.kept(ctx, SimpleNamespace(repo="/w/r"), "agent", "base", run, we.FIRST_PATCH, dog) == "timeout"
    assert now[0] == 105.0 and dog.ends == [("capture round-1.patch", 105.0)] and dog.late()
    assert not we.has_baseline(archive, run.rel) and not dog.may_start("rule session")


def test_a_review_is_ended_mid_way_when_the_watchdog_fires(monkeypatch, tmp_path):
    import tarfile
    now, ended, asked = [1000.0], [], []
    dog = we.Watchdog(1010.0, clock=lambda: now[0])
    archive = we.c.Archive(tmp_path / "archive")
    run = archive.start("run", "fake-1", arm="K3")
    empty = io.BytesIO()
    tarfile.open(fileobj=empty, mode="w").close()

    class Docker:
        def __call__(self, *a, **kw):
            return SimpleNamespace(returncode=0, stdout=empty.getvalue(), stderr=b"")

        def sh(self, *a, **kw):
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    def codex(pack, timeout):
        asked.append(timeout)
        now[0] += 30.0                                              # the review runs past the deadline
        dog.due()                                                   # and the watchdog's timer fires while it runs
        return ""

    monkeypatch.setattr(we, "copy_out", lambda *a: True)
    monkeypatch.setattr(we, "run_codex", codex)
    monkeypatch.setattr(we, "end_codex", lambda pack: ended.append(pack.name))
    ctx = SimpleNamespace(archive=archive, docker=dog.docker(Docker()), critic=None, arm="K3", watchdog=dog)
    we.run_critic(ctx, SimpleNamespace(repo="/w/r"), run, "agent", "base", "title", "issue")
    assert asked == [10.0] and len(ended) == 1 and dog.fired.is_set() and not dog.may_start("repair 1")


def test_a_late_copy_refuses_a_destination_that_resolves_inside_the_baseline(monkeypatch, tmp_path):
    archive = we.c.Archive(tmp_path / "archive")
    run = archive.start("run", "fake-1", arm="K3")
    _keep_baseline(archive, run, b"first")
    baseline = we.baseline_of(run.path)
    (run.path / "claude_projects").mkdir()
    _link(baseline.parent, run.path / "claude_projects" / "nested")   # however it came there, it leads into baseline/

    def copy_out(docker, agent, src, dest):
        (dest / "nested").mkdir(parents=True)
        (dest / "nested" / "notes.txt").write_bytes(b"late")
        return True

    monkeypatch.setattr(we, "copy_out", copy_out)
    with pytest.raises(RuntimeError, match="baseline"):
        we.copy_in(SimpleNamespace(archive=archive, docker=None), "agent", "/projects", run, "claude_projects")
    assert baseline.read_bytes() == b"first" and not (baseline.parent / "notes.txt").exists()


def test_the_index_reader_skips_a_torn_last_line_and_still_refuses_a_torn_line_before_it(tmp_path):
    archive = we.c.Archive(tmp_path)
    archive.append({"event": "started", "run": "run/x/1"})
    for torn in (b'{"event": "ended", "run": "run/x/1", "sta', b'{"event": "ended", "why": "\xc3'):
        archive.index.write_bytes(b'{"event": "started", "run": "run/x/1"}\n' + torn)
        assert archive.rows() == [{"event": "started", "run": "run/x/1"}]
    archive.index.write_bytes(b'{"event": "started"}\n{"event": "ended", "sta\n{"event": "kept"}\n')
    with pytest.raises(ValueError):
        archive.rows()


@pytest.mark.parametrize("torn", ["the index's last line", "the run's record"])
def test_k2s_check_is_as_it_was_after_a_torn_write(monkeypatch, tmp_path, torn):
    archive = we.c.Archive(tmp_path / "archive")
    archive.start("validate", "fake-1").finish({"fail_to_pass": ["t"], "pass_to_pass": []}, "finished")
    run = archive.start("run", "fake-1", arm="K2")
    run.write("rounds/round-1.patch", b"first")
    run.write("final.patch", b"final")
    if torn == "the run's record":
        run.write("run.json", b'{"arm": "K2", "agent_started": true,')
    else:
        run.finish({"arm": "K2", "agent_started": True}, "finished")
        with open(archive.index, "ab") as index:
            index.write(b'{"event": "ended", "run": "run/fake-1/x", "sta')
    labels = []
    monkeypatch.setattr(we, "_oracle_run", lambda ctx, task, *, label, **kw: (labels.append(label), {"results": {"t": "passed"}})[1])
    we.check(SimpleNamespace(archive=archive), SimpleNamespace(id="fake-1"), run.rel)
    assert labels == ["round-1", "final"]


def test_the_rule_session_must_continue_the_first_session_and_a_run_whose_does_not_is_an_error_row(monkeypatch, tmp_path):
    assert we.step_ending("", SUCCESS, 0, session="s1") == "finished"
    assert we.step_ending("", {**SUCCESS, "session_id": "another"}, 0, session="s1") == "error"
    assert we.step_ending("", {**SUCCESS, "session_id": "s1"}, 0, session="") == "error"         # no first id to continue
    record, events, _, ctx, task = _run(monkeypatch, tmp_path, "K3", rule_session_id="another-session")
    assert record["status"] == "error" and record["rule_step"]["ended"] == "error" and record["rule_step"]["at"] == "rule step"
    assert not any(e[0] == "loop" for e in events) and we.has_baseline(ctx.archive, record["run"])
    row = _row_of(monkeypatch, ctx.archive.root, record, "K3")
    assert row["status"] == "error"                                 # the baseline is kept, so it is a row, by stop symmetry
