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
    assert not we.has_baseline(ctx.archive, record["run"]) and not we.scored_run(ctx.archive, record["run"])
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
    assert ours["status"] == theirs["status"] == status and we.scored_run(ctx.archive, ours["run"])
    rows = [_row_of(monkeypatch, ctx.archive.root, ours, "K3"), _row_of(monkeypatch, ctx.archive.root, theirs, "K2")]
    assert all(row[we.c.MADE_UNDER] for row in rows)      # each bound to its own run's ending (study follow-up review 2, 1)
    assert [{k: v for k, v in row.items() if k not in ("arm", "run", "cost_usd", we.c.MADE_UNDER)} for row in rows] == [
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
    monkeypatch.setattr(we, "run_session", lambda *a, **kw: "")
    assert we.rule_step(None, None, None, "agent", "base1", args, "key", tally, 7.5, 1000.0 - 100.0) == "error"  # no own result


def _first_tally():
    """The tally as the loop finds it: holding the first session's own success, which the loop's repairs continue."""
    tally = we.c.Tally()
    tally.feed(dict(SUCCESS))
    return tally


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
        a[5].feed(dict(SUCCESS))                 # a repair that ends cleanly: its own success and a clean exit
        return ""

    monkeypatch.setattr(we, "run_set", lambda ctx, task, agent, where: on_tree.pop(0))
    monkeypatch.setattr(we, "run_session", session)
    monkeypatch.setattr(we, "scout_again", lambda args: args)
    monkeypatch.setattr(we, "keep_first_patch", lambda ctx, task, agent, base, n=1: kept.append(n))
    monkeypatch.setattr(we, "parent_check", lambda *a: we.ProbeRun(1, {"test_critic.py::test_owed": "failed"}, {}, ""))
    got = we.reviews_until_clean(SimpleNamespace(docker=Docker(), arm="K3"), SimpleNamespace(repo="/w/r"),
                                 SimpleNamespace(write_json=lambda *a: None), "agent", "base", [], "key", _first_tally(), 1.0, 0.0,
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


def test_check_scores_the_baseline_bytes_it_checked_and_never_reads_the_path_again(monkeypatch, tmp_path):
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
        tally.feed(dict(SUCCESS))       # the session's own result, which its ending reads with its exit (study follow-up 1)
        return ""

    monkeypatch.setattr(we.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(we, "read_stream", read_stream)
    run = we.c.Archive(tmp_path / "archive").start("run", "fake-1", arm="K3")
    ctx = SimpleNamespace(docker=SimpleNamespace(popen=popen), client_env=lambda key: {}, archive=run.archive)
    ended = we.run_session(ctx, run, "agent", ["claude"], "key", we.c.Tally(), "text", "rule_stream.jsonl", seconds=20.0,
                           stop_usd=1.0, deadline=1020.0)
    assert [w for w in written if w] == sent and (ended == "" or not sent)      # its exit of 0, read by run_session itself
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
        a[5].feed(dict(SUCCESS))                 # a repair that ends cleanly: its own success and a clean exit
        return ""

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
                                 SimpleNamespace(write_json=lambda *a: None), "agent", "base", [], "key", _first_tally(), 1.0, 0.0,
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


@pytest.mark.parametrize("index", [b'{"event": "started"}\n{"event": invalid}\n', b'{"event": invalid}\n{"event": "started"}\n',
                                   b'{"event": "started"}\n{"event": "ended", "why": "\xc3"}\n'])
def test_repair_3_a_malformed_line_the_index_ends_is_an_error_last_or_not(tmp_path, index):
    """Review 1, finding 4: only a tail the file does not end with ("\\n" absent at the very end) is a write cut short."""
    archive = we.c.Archive(tmp_path)
    archive.index.write_bytes(index)
    with pytest.raises(ValueError):
        archive.rows()


@pytest.mark.parametrize("line", [" ", " ", "\x85", "\x1c", "\x0b", "\x0c", " ", ""])
def test_review_4_finding_4_a_complete_line_json_cannot_read_is_an_error_whatever_python_calls_blank(tmp_path, line):
    """Only JSON says whether a complete line is a row: U+2028 and the like, which ``str.strip`` takes for blank and
    ``str.splitlines`` for a line's end, are refused, as an empty line is; only the bytes after the last newline are a torn tail."""
    row = json.dumps({"event": "started", "run": "run/t/1"}).encode()
    archive = we.c.Archive(tmp_path)
    archive.index.write_bytes(row + b"\n" + line.encode("utf-8") + b"\n" + row + b"\n")
    with pytest.raises(ValueError):
        archive.rows()
    archive.index.write_bytes(row + b"\n" + row + b"\n" + line.encode("utf-8"))           # the same bytes as a torn tail: skipped
    assert archive.rows() == [json.loads(row)] * 2


def test_repair_3_a_restart_after_a_torn_write_moves_the_torn_bytes_aside_and_its_next_start_is_read_back(tmp_path):
    """Review 1, finding 8: the next append, under the index's lock, moves exactly the bytes after the last "\\n" to a side file
    beside the index (never written over), cuts the index back to its last "\\n", and then writes its row whole."""
    before = we.c.Archive(tmp_path)
    before.append({"event": "started", "run": "run/old/1", "kind": "run"})
    whole = before.index.read_bytes()
    torn = b'{"event": "ended", "run": "run/old/1", "sta'
    with open(before.index, "ab") as stream:
        stream.write(torn)
    restarted = we.c.Archive(tmp_path)
    assert restarted.rows() == [{"event": "started", "run": "run/old/1", "kind": "run"}]
    run = restarted.start("run", "new", arm="K3")
    durable = we.c.Archive(tmp_path).rows()
    assert [r["run"] for r in durable] == ["run/old/1", run.rel] and durable[1]["event"] == "started"
    assert restarted.index.read_bytes().startswith(whole) and restarted.index.read_bytes().endswith(b"\n")
    sides = sorted(tmp_path.glob(restarted.index.name + ".torn-*"))
    assert len(sides) == 1 and sides[0].read_bytes() == torn
    with open(restarted.index, "ab") as stream:     # torn again: a side file of its own, the first one kept as it was
        stream.write(b"\xc3")
    restarted.append({"event": "ended", "run": run.rel, "status": "error"})
    sides = sorted(tmp_path.glob(restarted.index.name + ".torn-*"))
    assert [s.read_bytes() for s in sides] == [torn, b"\xc3"] or [s.read_bytes() for s in sides] == [b"\xc3", torn]
    assert [r["event"] for r in we.c.Archive(tmp_path).rows()] == ["started", "started", "ended"]


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
    if torn == "the run's record":   # with the host's ending rule (wecommon.ending): a record cut short, and no end in the index,
        with pytest.raises(SystemExit, match="no ending on record"):     # say nothing of how the run ended, so it has no verdict
            we.check(SimpleNamespace(archive=archive), SimpleNamespace(id="fake-1"), run.rel)
        assert labels == []
        return
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


# --- the merge with the host executor: one admission rule, and a K3 run with no first patch is made -------------------------------
def test_a_k3_run_that_kept_no_first_patch_was_made_and_is_never_made_again(tmp_path):
    """The host's rule makes a run with no verdict to give again (limited, refused); a K3 run whose agent started and that kept
    no baseline has none either, and it was made, as on K3's own branch: never made again for a better result."""
    task = we.load_tasks(we.TASKS_STAGE4)[0]
    archive = we.c.Archive(tmp_path / "r3")
    archive.start("run", task.id, model=SONNET, arm="K3", repeat=1).finish({"agent_started": True, "cost_usd": 0.0},
                                                                          we.NO_FIRST_PATCH)
    ctx = SimpleNamespace(archive=archive, tasks=None, model=SONNET, arm="K3", repeat=1, g6={"commit": we.R3_LOOP_G6}, rerun_why="")
    assert "already made (no_first_patch)" in we.r3_refusal(ctx, task)[0]
    archive.start("run", task.id, model=SONNET, arm="K3", repeat=2).finish({"agent_started": True, "cost_usd": 0.0}, "limited")
    ctx.repeat = 2
    assert "already made" not in we.r3_refusal(ctx, task)[0]       # a limited run is made again, as the host's rule says


def test_one_admission_rule_every_entry_point_asks_a_saved_k3_row_whose_baseline_changed_has_no_verdict(monkeypatch, tmp_path):
    """``unscored_why`` is the one rule: a saved row is given, rescored and compared only while it admits the run now."""
    archive = we.c.Archive(tmp_path)
    run = archive.start("run", "fake-1", arm="K3", repeat=1)      # study review 1, finding 6: a row's repeat is its start's
    _keep_baseline(archive, run, b"the first session's tree")
    run.finish({"arm": "K3", "agent_started": True}, "finished")
    diff = tmp_path / "amendment.patch"
    diff.write_text("a diff", encoding="utf-8")
    monkeypatch.setattr(we, "amendment", lambda task: diff)
    monkeypatch.setattr(loop, "tool", lambda command, *a, **kw: json.dumps({"status": "finished", "run": run.rel, "arm": "K3"})
                        if command == "run" else json.dumps({"checks": [{"patch": "final", "passes": True, "failing": 0,
                                                                         "fail_to_pass_failing": []}],
                                                             "amendment_sha256": we.c.sha256_file(diff)}))   # as check names it
    assert we.scored_run(archive, run.rel)
    row = loop.one(tmp_path, "g6", "K3", "fake-1", 1)
    assert row["passes"] is True and loop.rescore(tmp_path, "g6")[0]["amended_passes"] is True
    assert compare.saved(tmp_path)[("K3", "fake-1", 1)]["maintainers"] is True
    we.baseline_of(run.path).write_bytes(b"something else")         # its bytes no longer have the digest kept with them
    assert we.unscored_why(archive, run.rel) == "its baseline changed since it was kept" and not we.scored_run(archive, run.rel)
    monkeypatch.setattr(loop, "tool", lambda *a, **kw: pytest.fail("a run the rule no longer admits is neither run nor checked"))
    with pytest.raises(SystemExit, match="baseline changed"):
        loop.one(tmp_path, "g6", "K3", "fake-1", 1)
    assert loop.rescore(tmp_path, "g6") == []
    assert compare.saved(tmp_path)[("K3", "fake-1", 1)]["maintainers"] is None


def test_repair_1_a_saved_row_naming_another_arm_is_refused_by_every_entry_point_for_a_k3_run_whose_baseline_changed(monkeypatch,
                                                                                                                    tmp_path):
    """Review 1, finding 1: the arm is one fact, and the admission rule reads it from the archive (the run's started row), never
    from a row a caller passes; so is the run's baseline and its ending (``unscored_why(archive, run_rel)``)."""
    archive, task = we.c.Archive(tmp_path), SimpleNamespace(id="fake-1")
    archive.start("validate", task.id).finish({"fail_to_pass": ["t"], "pass_to_pass": []}, "finished")
    run = archive.start("run", task.id, arm="K3", executor="host")
    _keep_baseline(archive, run, b"first patch")
    run.write("final.patch", b"final patch")
    run.finish({"arm": "K3", "executor": "host", "agent_started": True}, "finished")
    we.baseline_of(run.path).write_bytes(b"changed after capture")
    row = {"arm": "K2", "task": task.id, "repeat": 1, "run": run.rel, "status": "finished", "passes": True, "failing": 0,
           we.c.MADE_UNDER: we.c.ending_sha256(tmp_path, run.rel)}
    (tmp_path / "loop").mkdir()
    (tmp_path / "loop" / "K2-fake-1-1.json").write_text(json.dumps(row), encoding="utf-8")
    diff = tmp_path / "amendment.patch"
    diff.write_text("a diff", encoding="utf-8")
    monkeypatch.setattr(we, "amendment", lambda name: diff)
    monkeypatch.setattr(we, "_oracle_run", lambda *a, **kw: pytest.fail("a changed baseline is never scored"))
    monkeypatch.setattr(loop, "tool", lambda *a, **kw: pytest.fail("a run the rule refuses is neither run nor checked"))
    with pytest.raises(SystemExit, match="baseline changed"):
        we.check(SimpleNamespace(archive=archive), task, run.rel)
    scores = compare.saved(tmp_path).get(("K2", task.id, 1), {})      # review 4: a refused row takes no key at all
    assert (scores.get("maintainers"), scores.get("amended")) == (None, None)
    assert loop.rescore(tmp_path, "g6") == []
    with pytest.raises(SystemExit, match="baseline changed"):
        loop.one(tmp_path, "g6", "K2", task.id, 1)
    assert we.unscored_why(archive, run.rel) == "its baseline changed since it was kept" and not we.scored_run(archive, run.rel)


# --- review 2, repair 1: one function is the only source of a run's facts (wecommon.run_facts) ---------------------------------
def test_review_2_finding_3_a_saved_rows_status_never_overrides_the_runs_archived_ending(monkeypatch, tmp_path):
    archive, task = we.c.Archive(tmp_path), SimpleNamespace(id="fake-1")
    archive.start("validate", task.id).finish({"fail_to_pass": ["test_bug"], "pass_to_pass": []}, "finished")
    run = archive.start("run", task.id, arm="K2", repeat=1)      # study review 1, finding 6: a row's repeat is its start's
    run.write("final.patch", b"patch")
    run.finish({"arm": "K2", "agent_started": True}, "finished")
    (tmp_path / "loop").mkdir()
    (tmp_path / "loop" / "K2-fake-1-1.json").write_text(json.dumps({"arm": "K2", "task": task.id, "repeat": 1, "run": run.rel,
                                                                    "status": "refused", "passes": True, "failing": 0}), "utf-8")
    monkeypatch.setattr(we, "amendment", lambda *a: None)
    monkeypatch.setattr(we, "_oracle_run", lambda *a, **kw: {"results": {"test_bug": "passed"}})
    assert we.ending_of(archive, run.rel)["status"] == "finished" and we.scored_run(archive, run.rel)
    assert we.check(SimpleNamespace(archive=archive), task, run.rel)["checks"][-1]["passes"] is True
    assert compare.saved(tmp_path)[("K2", task.id, 1)]["maintainers"] is True


def test_review_2_finding_4_a_name_that_is_not_the_runs_own_reaches_no_run(monkeypatch, tmp_path):
    archive, task = we.c.Archive(tmp_path / "archive"), SimpleNamespace(id="fake-1")
    archive.start("validate", task.id).finish({"fail_to_pass": ["test_bug"], "pass_to_pass": []}, "finished")
    run = archive.start("run", task.id, arm="K3", executor="host")
    _keep_baseline(archive, run, b"first session patch")
    run.write("final.patch", b"final patch")
    run.finish({"arm": "K3", "executor": "host", "agent_started": True}, "finished")
    we.baseline_of(run.path).write_bytes(b"changed baseline")
    alias = run.rel.replace("/fake-1/", "/fake-1/../fake-1/", 1)
    assert (archive.root / alias).resolve() == run.path.resolve() and not we.scored_run(archive, run.rel)
    monkeypatch.setattr(we, "_oracle_run", lambda *a, **kw: pytest.fail("neither name is scored"))
    with pytest.raises(SystemExit, match="baseline changed"):
        we.check(SimpleNamespace(archive=archive), task, run.rel)
    with pytest.raises(SystemExit, match="no run the archive started"):
        we.check(SimpleNamespace(archive=archive), task, alias)
    assert we.c.run_facts(archive, alias) is None and we.c.ending(archive.root, alias) is None


#: The table's cases (review 2's, extended by review 3's): case -> (the name is the run's own, the run is admitted by its name, the
#: saved row is admitted). A row whose arm or task disagrees with its run's facts is refused, whatever its run.
FACTS_CASES = {
    "admitted K3": (True, True, True),
    "forged status": (True, True, True),        # a row saying "refused" for a run that finished: the row's status is no fact
    "torn index tail": (True, True, True),      # a write cut short after the run's rows: every reader skips it, as Archive.rows does
    "forged arm": (True, True, False),          # a K2 run's row says K3
    "forged task": (True, True, False),         # a row for another bug names the run
    "forged repeat": (True, True, False),       # review 4: a repeat-1 run's row says repeat 2, and is kept under repeat 2's name
    "misplaced row": (True, True, True),        # review 4: the run's own row, kept under arm A's name; it is still the run's
    "changed K3 baseline": (True, False, False),
    "alias name": (False, False, False),        # x/../x
    "dot segment": (False, False, False),       # run/./x
    "backslash form": (False, False, False),
    "trailing slash": (False, False, False),
    "other case": (False, False, False),        # the same folder on Windows, another name
    "unknown name": (False, False, False),
}


def _facts_case(monkeypatch, root, case):
    """One archive for one case of the table below: the run, its saved row, and the name every entry point is asked by, exactly as
    the row holds it."""
    import export_reviewer_cases as ex
    task, other = "black-5634", "pydantic-14052"
    archive = we.c.Archive(root)
    for bug in (task, other):
        archive.start("validate", bug).finish({"fail_to_pass": ["t"], "pass_to_pass": []}, "finished")
    arm = "K3" if case in ("admitted K3", "changed K3 baseline", "alias name") else "K2"
    run = archive.start("run", task, arm=arm, executor="host", repeat=1)      # as every round 3 run's start names its repeat
    if arm == "K3":
        _keep_baseline(archive, run, b"the first session's tree")
    run.write("final.patch", b"final " + case.encode())
    run.finish({"arm": arm, "executor": "host", "agent_started": True}, "finished")
    if case in ("alias name", "changed K3 baseline"):
        we.baseline_of(run.path).write_bytes(b"changed after capture")
    name = {"alias name": run.rel.replace(f"/{task}/", f"/{task}/../{task}/", 1), "dot segment": run.rel.replace("run/", "run/./", 1),
            "backslash form": run.rel.replace("/", "\\"), "trailing slash": run.rel + "/", "other case": run.rel.upper(),
            "unknown name": f"run/{task}/not-started"}.get(case, run.rel)
    if case == "unknown name":                      # a folder that holds a run's files, but no start in the index names it
        (root / name).mkdir(parents=True)
        for file in ("run.json", "final.patch", we.c.ENDING):
            (root / name / file).write_bytes((run.path / file).read_bytes())
    row_arm, row_task = ("K3" if case == "forged arm" else arm), (other if case == "forged task" else task)
    row_repeat = 2 if case == "forged repeat" else 1
    row = {"arm": row_arm, "task": row_task, "repeat": row_repeat, "run": name, "passes": True, "failing": 0,
           "status": "refused" if case == "forged status" else "finished", we.c.MADE_UNDER: we.c.ending_sha256(root, run.rel)}
    (root / "loop" / "amended").mkdir(parents=True)
    kept_as = "A" if case == "misplaced row" else row_arm
    (root / "loop" / f"{kept_as}-{row_task}-{row_repeat}.json").write_text(json.dumps(row), "utf-8")
    archive.start("check", row_task, checked_run=name).finish({"checks": [{"patch": "final", "passes": True}]}, "finished")
    if case == "torn index tail":
        with open(archive.index, "ab") as index:
            index.write(b'{"event": "started", "run": "check/' + row_task.encode() + b'/torn", "checked_run": "run/')
    diff = root / "amendment.patch"
    diff.write_text("a diff", encoding="utf-8")
    monkeypatch.setattr(we, "amendment", lambda name_: diff)
    monkeypatch.setattr(we, "_oracle_run", lambda *a, **kw: {"results": {"t": "passed"}})
    monkeypatch.setattr(loop, "tool", lambda command, *a, **kw: json.dumps({"run": name, "status": "finished"}) if command == "run"
                        else json.dumps({"checks": [{"patch": "final", "passes": True, "failing": 0, "fail_to_pass_failing": []}],
                                         "amendment_sha256": we.c.sha256_file(diff)}))     # the amendment the check ran, as it names it
    return archive, row_task, name, row, ex


@pytest.mark.parametrize("case", sorted(FACTS_CASES))
def test_review_2_every_entry_point_reads_a_runs_facts_through_one_function_and_gives_one_answer(monkeypatch, tmp_path, case):
    """Review 2's table, extended by review 3's cases. Each case is asked of every entry point, by the exact string the row holds:
    ``wecommon.run_facts``, ``ending``, ``ending_sha256``, ``verdict``, ``holds``, ``run.unscored_why``, ``scored_run``,
    ``ending_of``, ``check``, ``compare.saved``, ``loop.one``, ``loop.rescore`` and the export. Those that take only the name give
    the run's own answer; those that judge a saved row refuse one whose arm or task disagrees with the run's facts; ``check``,
    which takes a name and a task, refuses a task that is not the run's. The export is asked first, before anything appends to
    the index past its torn tail."""
    known, by_name, by_row = FACTS_CASES[case]
    archive, task, name, row, ex = _facts_case(monkeypatch, tmp_path, case)
    root = archive.root
    rows = {"export": any(not isinstance(patch, Path) or patch.name == "final.patch" for _, patch, _ in ex.labelled(root, task))}
    facts = {      # how the run ended, as the archive holds it: known by the run's own name only
        "wecommon.run_facts": we.c.run_facts(archive, name) is not None,
        "wecommon.ending": we.c.scoreable(we.c.ending(root, name)),
        "wecommon.ending_sha256": we.c.ending_sha256(root, name) is not None,
        "run.ending_of": we.c.scoreable(we.ending_of(archive, name)),
    }
    names = {"run.unscored_why": we.unscored_why(archive, name) == "", "run.scored_run": we.scored_run(archive, name)}
    saved = compare.saved(root)            # review 4, finding 6: a row that is no run's own takes no key at all
    rows.update({
        "wecommon.verdict": we.c.verdict(root, name, row) is not None,
        "wecommon.holds": we.c.holds(root, name, row),
        "compare.saved": saved.get((row["arm"], task, row["repeat"]), {}).get("maintainers") is not None,
        "loop.rescore": bool(loop.rescore(root, "g6")),
    })
    own = next((r["arm"], r["task"], r["repeat"]) for r in archive.rows() if r.get("event") == "started" and r.get("kind") == "run")
    assert set(saved) <= {own}             # the one key a score can have is the run's own, whatever its row or its file says
    if case == "misplaced row":            # review 4, finding 7: asked for the arm its file is named for, no run is given or made
        with monkeypatch.context() as patched:
            patched.setattr(loop, "tool", lambda *a, **kw: pytest.fail("a row of another arm's run is never given or made again"))
            with pytest.raises(SystemExit, match="holds no row"):
                loop.one(root, "g6", "A", task, 1)
    try:
        rows["loop.one"] = loop.one(root, "g6", row["arm"], task, row["repeat"])["passes"] is True
    except SystemExit:
        rows["loop.one"] = False
    try:
        checked = bool(we.check(SimpleNamespace(archive=archive), SimpleNamespace(id=task), name)["checks"])
    except SystemExit:
        checked = False
    assert facts == dict.fromkeys(facts, known)
    assert names == dict.fromkeys(names, by_name)
    assert rows == dict.fromkeys(rows, by_row)
    assert checked is (by_name and case != "forged task")


def test_review_3_finding_2_check_takes_the_name_exactly_and_never_one_made_from_a_path(monkeypatch, tmp_path):
    archive = we.c.Archive(tmp_path)
    archive.start("validate", "bug").finish({"fail_to_pass": ["test"], "pass_to_pass": []}, "finished")
    run = archive.start("run", "bug", arm="K2", executor="host")
    run.finish({"executor": "host"}, "finished")
    run.write("final.patch", b"patch")
    alias = run.rel.replace("run/", "run/./", 1)
    assert we.c.run_facts(archive, alias) is None and not we.scored_run(archive, alias)
    monkeypatch.setattr(we, "_oracle_run", lambda *a, **kw: {"results": {"test": "passed"}})
    with pytest.raises(SystemExit, match="no run the archive started"):
        we.check(SimpleNamespace(archive=archive), SimpleNamespace(id="bug"), alias)


def test_review_3_finding_3_the_export_takes_the_rows_name_exactly(tmp_path):
    import export_reviewer_cases as ex
    archive = we.c.Archive(tmp_path)
    run = archive.start("run", "bug", arm="K2", executor="host")
    run.finish({"executor": "host"}, "finished")
    run.write("final.patch", b"patch")
    alias = run.rel.replace("/", "\\")
    assert we.c.run_facts(archive, alias) is None and not we.scored_run(archive, alias)
    (tmp_path / "loop").mkdir()
    (tmp_path / "loop" / "K2-bug-1.json").write_text(json.dumps({"run": alias, "status": "finished", "passes": True}), "utf-8")
    assert ex.labelled(tmp_path, "bug") == []


def test_review_3_finding_5_compare_never_scores_a_run_under_the_arm_a_saved_row_claims(monkeypatch, tmp_path):
    archive = we.c.Archive(tmp_path)
    run = archive.start("run", "bug", arm="K3", executor="host")
    _keep_baseline(archive, run, b"first patch")
    run.finish({"executor": "host"}, "finished")
    row = {"run": run.rel, "arm": "A", "task": "bug", "repeat": 1, "passes": True, "failing": 0,
           we.c.MADE_UNDER: we.c.ending_sha256(archive, run.rel)}
    (tmp_path / "loop").mkdir()
    (tmp_path / "loop" / "A-bug-1.json").write_text(json.dumps(row), "utf-8")
    monkeypatch.setattr(compare.we, "amendment", lambda task: None)
    assert we.scored_run(archive, run.rel)                              # the run is admitted; the row that claims it is not
    assert compare.saved(tmp_path).get(("A", "bug", 1), {}).get("maintainers") is None
    assert we.c.verdict(archive, run.rel, row) is None and not we.c.holds(archive, run.rel, row)


def test_review_3_finding_6_the_export_reads_the_index_by_its_torn_tail_rule(tmp_path):
    import export_reviewer_cases as ex
    archive = we.c.Archive(tmp_path)
    run = archive.start("run", "bug", arm="K2", executor="host", repeat=1)      # study review 1, finding 6
    run.finish({"executor": "host"}, "finished")
    run.write("final.patch", b"patch")
    (tmp_path / "loop").mkdir()
    (tmp_path / "loop" / "K2-bug-1.json").write_text(json.dumps({"run": run.rel, "passes": True, "failing": 0,    # as loop.one
                                                                 we.c.MADE_UNDER: we.c.ending_sha256(archive, run.rel)}), "utf-8")
    archive.index.write_bytes(archive.index.read_bytes()       # an interrupted append of the next check's start
                              + b'{"event": "started", "run": "check/bug/torn", "checked_run": "run/bug/')
    assert archive.rows() and we.c.scored_run(archive, run.rel)
    assert ex.labelled(tmp_path, "bug") == [("bug", run.path / "final.patch", True)]


# --- review 4: attribution from a run's facts alone (wecommon.saved_rows), findings 1, 2, 6 and 7 -------------------------------
def _finished(archive, task, arm, *, repeat=1):
    run = archive.start("run", task, arm=arm, executor="host", repeat=repeat)
    run.finish({"executor": "host", "agent_started": True}, "finished")
    return run


def test_review_4_finding_1_loop_one_refuses_a_row_kept_under_its_name_that_says_another_arm_never_rewrites_it(tmp_path,
                                                                                                              monkeypatch):
    archive = we.c.Archive(tmp_path)
    run = archive.start("run", "black-5634", arm="K2", executor="host", repeat=1)
    run.finish({"executor": "host", "agent_started": True}, "error")
    row = {"run": run.rel, "arm": "A", "task": "black-5634", "repeat": 1, "status": "error", "passes": False, "failing": None,
           we.c.MADE_UNDER: we.c.ending_sha256(archive, run.rel)}
    saved = tmp_path / "loop" / "K2-black-5634-1.json"
    saved.parent.mkdir()
    saved.write_text(json.dumps(row), encoding="utf-8")
    monkeypatch.setattr(loop, "tool", lambda *a, **kw: pytest.fail("no process is needed, and none may start"))
    assert we.c.verdict(archive, run.rel, row) is None and not we.c.holds(archive, run.rel, row)
    with pytest.raises(SystemExit, match="holds no row"):
        loop.one(tmp_path, "g6", "K2", "black-5634", 1)
    assert json.loads(saved.read_text(encoding="utf-8")) == row          # refused, never re-labelled or written over
    assert compare.saved(tmp_path) == {} and loop.rescore(tmp_path, "g6") == []


def test_review_4_finding_2_the_export_never_labels_a_patch_with_another_runs_amended_verdict(tmp_path):
    import export_reviewer_cases as ex
    archive, task = we.c.Archive(tmp_path), "click-3678"          # an amended bug in the export's own configuration
    run = archive.start("run", task, arm="K2", executor="host", repeat=1)
    run.write("final.patch", b"the current run's patch")
    run.finish({"executor": "host", "agent_started": True}, "finished")
    row = {"run": run.rel, "arm": "K2", "task": task, "repeat": 1, "status": "finished", "passes": True, "failing": 0,
           we.c.MADE_UNDER: we.c.ending_sha256(archive, run.rel)}
    (tmp_path / "loop" / "amended").mkdir(parents=True)
    (tmp_path / "loop" / f"K2-{task}-1.json").write_text(json.dumps(row), encoding="utf-8")
    other = _finished(archive, task, "A")
    sha = we.c.sha256_file(we.amendment(task))
    for amended in ({**row, "run": other.rel, "arm": "A", "amended_passes": True, "amendment_sha256": sha,     # another run's
                     we.c.MADE_UNDER: we.c.ending_sha256(archive, other.rel)},
                    {**row, "amended_passes": True, "amendment_sha256": "an earlier diff"}):                 # another diff's
        (tmp_path / "loop" / "amended" / f"K2-{task}-1.json").write_text(json.dumps(amended), encoding="utf-8")
        assert we.c.verdict(archive, run.rel, amended, amendment=sha) is None
        assert ex.distinct(ex.labelled(tmp_path, task)) == {}
    amended = {**row, "amended_passes": True, "amendment_sha256": sha}                                       # its own: labelled
    (tmp_path / "loop" / "amended" / f"K2-{task}-1.json").write_text(json.dumps(amended), encoding="utf-8")
    assert [ok for _, ok in ex.distinct(ex.labelled(tmp_path, task)).values()] == [True]


@pytest.mark.parametrize("good_first", [True, False])
def test_review_4_finding_6_a_refused_row_never_changes_another_runs_score_whichever_file_holds_it(tmp_path, monkeypatch,
                                                                                                good_first):
    """Both orders of the two files: the admitted run's score stands, keyed by its own start, whichever file holds its row."""
    archive, task = we.c.Archive(tmp_path), "black-5634"
    own, other = _finished(archive, task, "A"), _finished(archive, task, "K2")
    good = {"run": own.rel, "arm": "A", "task": task, "repeat": 1, "passes": True, "failing": 0,
            we.c.MADE_UNDER: we.c.ending_sha256(archive, own.rel)}
    bad = {**good, "run": other.rel, we.c.MADE_UNDER: we.c.ending_sha256(archive, other.rel)}    # K2's run, said to be arm A's
    (tmp_path / "loop").mkdir()
    files = [tmp_path / "loop" / f"{arm}-{task}-1.json" for arm in ("A", "K2")]
    for path, row in zip(files, (good, bad) if good_first else (bad, good)):
        path.write_text(json.dumps(row), encoding="utf-8")
    monkeypatch.setattr(compare.we, "amendment", lambda task: None)
    assert we.c.verdict(archive, own.rel, good) is True and we.c.verdict(archive, other.rel, bad) is None
    assert compare.saved(tmp_path) == {("A", task, 1): {"maintainers": True, "amended": True, "said": "done"}}


def test_review_4_finding_7_loop_one_never_gives_another_arms_row_kept_under_the_asked_name(tmp_path, monkeypatch):
    archive, task = we.c.Archive(tmp_path), "black-5634"
    run = _finished(archive, task, "K2")
    row = {"run": run.rel, "arm": "K2", "task": task, "repeat": 1, "status": "finished", "passes": True, "failing": 0,
           we.c.MADE_UNDER: we.c.ending_sha256(archive, run.rel)}
    (tmp_path / "loop").mkdir()
    (tmp_path / "loop" / f"A-{task}-1.json").write_text(json.dumps(row), encoding="utf-8")
    monkeypatch.setattr(loop, "tool", lambda *a, **kw: pytest.fail("no process may start for a row of another arm's run"))
    assert we.c.holds(archive, run.rel, row)                     # the row is its run's own, and stands: for K2, never for A
    with pytest.raises(SystemExit, match="holds no row"):
        loop.one(tmp_path, "g6", "A", task, 1)
    monkeypatch.setattr(compare.we, "amendment", lambda task: None)
    assert set(compare.saved(tmp_path)) == {("K2", task, 1)}


# --- review 2, repair 2: every session of the K loop ends by its own stop, result and exit, by one rule (step_ending) ------------
def test_review_2_finding_2_a_container_repair_with_no_result_of_its_own_never_inherits_the_rule_steps_success(monkeypatch,
                                                                                                                tmp_path):
    task, events, sent = we.load_tasks()[0], [], []

    class Docker(_Docker):
        calls_to_agent = 0

        def sh(self, ctr, script, *args, **kw):
            if script.startswith("git status --porcelain"):
                return self.done(stdout=self.tree)
            return super().sh(ctr, script, *args, **kw)

        def popen(self, *args, **kw):            # the first session, the rule step, then a repair that exits 1 with no result
            self.calls_to_agent += 1
            n = self.calls_to_agent
            self.tree = [FIRST, AFTER_RULE, b"partial failed repair"][n - 1]
            data, code = (json.dumps(SUCCESS).encode() + b"\n", 0) if n < 3 else (b"", 1)
            return SimpleNamespace(stdin=_Stdin(sent), stdout=io.BytesIO(data), stderr=io.BytesIO(), poll=lambda: code,
                                   wait=lambda **kw: code)

    docker = Docker(task, events, sent)
    ctx = we.Context(archive=we.c.Archive(tmp_path / "archive"), docker=docker, key_reader=lambda: "fake-key",
                     issue_reader=lambda task: ("title", "issue"), tree_reader=lambda task: "tree", g6={"commit": "0" * 40},
                     variant=we.ROUND3, arm="K3", environ={})
    monkeypatch.setattr(we, "key_check", lambda *a, **kw: {"ok": True})
    monkeypatch.setattr(we, "start_net", lambda *a: ("net", "proxy"))
    monkeypatch.setattr(we, "read_thread", lambda *a: "")
    reviews = []

    def critic(*args, **kw):
        reviews.append(1)
        return {"verdict": "defect", "test": "def test_bug(): assert False", "rule": "r", "why": "w"} if len(reviews) == 1 \
            else {"verdict": "none"}

    probe = we.ProbeRun(1, {"test_bug.py::test_bug": "failed"}, {})
    monkeypatch.setattr(we, "run_critic", critic)
    monkeypatch.setattr(we, "run_set", lambda *a, **kw: probe)
    monkeypatch.setattr(we, "parent_check", lambda *a, **kw: probe)
    record = we._run_task(ctx, task, dry=False, cap=10.0, stop=10.5)
    assert docker.calls_to_agent == 3 and we.has_baseline(ctx.archive, record["run"]) and len(reviews) == 1
    assert record["status"] == "error" and record["repairs"]["ended"] == "error"
    assert we.scored_run(ctx.archive, record["run"])                    # a stopped K3 run with its baseline is a row


def _repairing(monkeypatch, *, result, code, stop):
    """Arm K1's loop in the container: review 1 finds a defect whose test fails, so one repair runs; the tally holds an earlier
    session's success, which the repair must never be read by. Returns the loop's result and how many reviews ran."""
    reviews, tally = [], we.c.Tally()
    tally.feed(dict(SUCCESS))

    class Docker:
        tree = 0

        def sh(self, *a, **kw):
            return SimpleNamespace(returncode=0, stdout=f"tree {Docker.tree}".encode(), stderr=b"")

    def session(ctx, run, agent, command, key, tally_, text, name, *, seconds, stop_usd, codes=None, deadline=None):
        Docker.tree += 1
        if result is not None:
            tally_.feed(dict(result))
        return we.session_ending(stop, code, dict(result) if result is not None else None)    # the session's whole ending

    def critique(n):
        reviews.append(n)
        return {"verdict": "defect", "rule": "r", "why": "w", "test": "t", "seconds": 1.0}

    monkeypatch.setattr(we, "run_set", lambda *a: we.ProbeRun(1, {"test_critic.py::test_owed": "failed"}, {}, ""))
    monkeypatch.setattr(we, "run_session", session)
    monkeypatch.setattr(we, "scout_again", lambda args: args)
    monkeypatch.setattr(we, "keep_first_patch", lambda *a: None)
    got = we.reviews_until_clean(SimpleNamespace(docker=Docker(), arm="K1"), SimpleNamespace(repo="/w/r"),
                                 SimpleNamespace(write_json=lambda *a: None), "agent", "base", [], "key", tally, 1.0, 0.0, critique)
    return got, reviews


@pytest.mark.parametrize(("result", "code", "stop", "ending"), [
    (None, 0, "", "error"),                                                          # no result of its own
    (SUCCESS, 1, "", "error"),                                                       # a success that exited 1
    ({**SUCCESS, "subtype": "future_terminal_status"}, 0, "", "error"),             # an ending nobody knows
    ({**SUCCESS, "subtype": "error_max_turns", "is_error": True}, 1, "", "cap_turns"),   # a cap its result names
    (SUCCESS, 0, "cap_cost", "cap_cost"),                                            # the stream's own stop
])
def test_review_2_a_container_repair_that_does_not_end_cleanly_ends_the_loop_and_the_run_with_its_own_ending(monkeypatch, result,
                                                                                                             code, stop, ending):
    got, reviews = _repairing(monkeypatch, result=result, code=code, stop=stop)
    assert got["ended"] == ending and got["status"] == "inconclusive" and reviews == [1]
    assert got["reason"] == f"the repair's session ended {ending}"


def test_review_2_a_container_repair_that_ends_cleanly_goes_on_to_the_next_review(monkeypatch):
    got, reviews = _repairing(monkeypatch, result=SUCCESS, code=0, stop="")
    assert "ended" not in got and reviews[:2] == [1, 2]


# --- review 3, repair 2: one session-identity rule on both executors -------------------------------------------------------------
def _container_loop(monkeypatch, first, repaired, arm="K3", after_step=True):
    """Arm ``arm``'s loop in the container, its tally holding ``first`` (the result the loop's repairs continue), each repair
    ending with the result ``repaired``. Returns the loop's result, the repairs started (their commands) and the reviews."""
    tally, started, reviews, state = we.c.Tally(), [], [], [0]
    tally.feed(first)

    def repair(ctx, run, agent, command, key, tally_, text, name, **kw):
        started.append(list(command))
        state[0] += 1
        tally_.feed(dict(repaired))
        return ""

    def review(n):
        reviews.append(n)
        return {"verdict": "defect", "rule": "r", "why": "w", "test": "test"} if n == 1 else {"verdict": "none"}

    monkeypatch.setattr(we, "run_session", repair)
    monkeypatch.setattr(we, "run_set", lambda *a: we.ProbeRun(1, {"test_critic.py::test_rule": "failed"}, {}, ""))
    monkeypatch.setattr(we, "parent_check", lambda *a: we.ProbeRun(1, {"test_critic.py::test_rule": "failed"}, {}, ""))
    monkeypatch.setattr(we, "keep_first_patch", lambda *a: None)
    got = we.reviews_until_clean(SimpleNamespace(arm=arm), SimpleNamespace(repo="/repo"), SimpleNamespace(write_json=lambda *a: None),
                                 "agent", "base", ["claude", "--disallowedTools", "x"], "key", tally, 10, time.monotonic(), review,
                                 finding="parent", tree=lambda: state[0], after_step=after_step)
    return got, started, reviews


def test_review_3_finding_1_a_container_repair_whose_result_names_another_session_ends_the_run_error(monkeypatch):
    got, started, reviews = _container_loop(monkeypatch, {**SUCCESS, "session_id": "first"}, {**SUCCESS, "session_id": "other"})
    assert got.get("ended") == "error" and len(started) == 1 and reviews == [1]
    assert all("--continue" in command for command in started)       # the container keeps --continue


def test_review_3_finding_4_a_container_first_session_whose_result_names_no_session_starts_no_repair(monkeypatch):
    plain = {k: v for k, v in SUCCESS.items() if k != "session_id"}
    got, started, _ = _container_loop(monkeypatch, plain, {**SUCCESS, "session_id": "new"}, arm="K2", after_step=False)
    assert started == [] and got.get("ended") == "error"


def test_review_3_a_container_repair_whose_result_names_its_first_session_goes_on(monkeypatch):
    got, started, reviews = _container_loop(monkeypatch, {**SUCCESS, "session_id": "first"}, {**SUCCESS, "session_id": "first"})
    assert "ended" not in got and len(started) == 1 and reviews == [1, 2]


class _Sessions(_Docker):
    """The agent's container, whose sessions each end with the result ``results`` gives in turn, at exit 0."""

    def __init__(self, task, events, sent, results):
        super().__init__(task, events, sent)
        self.results, self.calls = list(results), 0

    def sh(self, ctr, script, *args, **kw):
        if script.startswith("git status --porcelain"):
            return self.done(stdout=f"tree {self.calls}".encode())
        return super().sh(ctr, script, *args, **kw)

    def popen(self, *args, **kw):
        self.calls += 1
        self.events.append(("session", self.calls))
        data = json.dumps(self.results.pop(0)).encode() + b"\n"
        return SimpleNamespace(stdin=_Stdin(self.sent), stdout=io.BytesIO(data), stderr=io.BytesIO(), poll=lambda: 0,
                               wait=lambda **kw: 0)


def _container_run(monkeypatch, tmp_path, arm, results):
    task, events, sent = we.load_tasks()[0], [], []
    docker = _Sessions(task, events, sent, results)
    ctx = we.Context(archive=we.c.Archive(tmp_path / "archive"), docker=docker, key_reader=lambda: "fake-key",
                     issue_reader=lambda task: ("title", "issue"), tree_reader=lambda task: "tree", g6={"commit": "0" * 40},
                     variant=we.ROUND3, arm=arm, environ={})
    probe = we.ProbeRun(1, {"test_bug.py::test_bug": "failed"}, {})
    reviews = []
    monkeypatch.setattr(we, "key_check", lambda *a, **kw: {"ok": True})
    monkeypatch.setattr(we, "start_net", lambda *a: ("net", "proxy"))
    monkeypatch.setattr(we, "read_thread", lambda *a: "")
    monkeypatch.setattr(we, "run_critic", lambda *a, **kw: (reviews.append(1), {"verdict": "defect", "test": "def test_bug(): 0",
                                                                                "rule": "r", "why": "w"})[1])
    monkeypatch.setattr(we, "run_set", lambda *a, **kw: probe)
    monkeypatch.setattr(we, "parent_check", lambda *a, **kw: probe)
    return we._run_task(ctx, task, dry=False, cap=10.0, stop=10.5), docker, ctx, reviews


def test_review_3_a_container_k2_run_whose_repair_names_another_session_ends_error(monkeypatch, tmp_path):
    record, docker, _, reviews = _container_run(monkeypatch, tmp_path, "K2", [SUCCESS, {**SUCCESS, "session_id": "other"},
                                                                              SUCCESS])
    assert docker.calls == 2 and reviews == [1] and record["status"] == "error" and record["repairs"]["ended"] == "error"


def test_review_3_a_container_k3_first_session_whose_result_names_no_session_starts_no_rule_step(monkeypatch, tmp_path):
    plain = {k: v for k, v in SUCCESS.items() if k != "session_id"}
    record, docker, ctx, reviews = _container_run(monkeypatch, tmp_path, "K3", [plain, SUCCESS])
    assert docker.calls == 1 and reviews == [] and record["status"] == "error" and record["rule_step"]["at"] == "rule step"
    assert we.has_baseline(ctx.archive, record["run"]) and we.scored_run(ctx.archive, record["run"])     # a row, its baseline kept


@pytest.mark.parametrize("verdict", ["none", "defect"])
def test_review_4_finding_3_a_container_k2_loop_whose_first_result_names_no_session_is_error_with_no_review(monkeypatch, tmp_path,
                                                                                                          verdict):
    """The K loop starts only from a result that names its session: with none, no review runs, and the run is "error" even where
    the first review would have been clean (``verdict`` "none") and no repair would have been asked for."""
    task, events, sent = we.load_tasks()[0], [], []
    docker = _Sessions(task, events, sent, [{k: v for k, v in SUCCESS.items() if k != "session_id"}])
    ctx = we.Context(archive=we.c.Archive(tmp_path / "archive"), docker=docker, key_reader=lambda: "fake-key",
                     issue_reader=lambda task: ("title", "issue"), tree_reader=lambda task: "tree", g6={"commit": "0" * 40},
                     variant=we.ROUND3, arm="K2", environ={})
    reviews = []
    monkeypatch.setattr(we, "key_check", lambda *a, **kw: {"ok": True})
    monkeypatch.setattr(we, "start_net", lambda *a: ("net", "proxy"))
    monkeypatch.setattr(we, "read_thread", lambda *a: "")
    monkeypatch.setattr(we, "run_critic", lambda *a, **kw: (reviews.append(1), {"verdict": verdict, "test": "", "seconds": 0})[1])
    record = we._run_task(ctx, task, dry=False, cap=10, stop=10.5)
    assert docker.calls == 1 and reviews == [] and record["status"] == "error" and "repairs" not in record


# --- the reviewer cases' export reads K3's round-1 where K3 keeps it ----------------------------------------------------------
def test_the_reviewer_export_finds_k3s_round_1_in_its_baseline_only_while_it_has_its_kept_digest(tmp_path):
    import export_reviewer_cases as ex
    archive, task, runs = we.c.Archive(tmp_path), "black-5634", {}
    (tmp_path / "loop").mkdir()
    for arm in ("K3", "K2"):
        run = archive.start("run", task, arm=arm, repeat=1)      # study review 1, finding 6: a row's repeat is its start's
        if arm == "K3":
            _keep_baseline(archive, run, b"the first session's tree")
            run.write("rounds/rule.patch", b"after the rule step")
        else:
            run.write("rounds/round-1.patch", b"K2's first patch")
        run.write("final.patch", b"final " + arm.encode())
        run.finish({"arm": arm, "agent_started": True}, "finished")
        checks = [{"patch": "round-1", "passes": False}, *([{"patch": "rule", "passes": True}] if arm == "K3" else []),
                  {"patch": "final", "passes": True}]
        archive.start("check", task, checked_run=run.rel).finish({"checks": checks}, "finished")
        (tmp_path / "loop" / f"{arm}-{task}-1.json").write_text(json.dumps({"arm": arm, "task": task, "repeat": 1, "run": run.rel,
                                                                             "status": "finished", "passes": True,
                                                                             "failing": 0}), "utf-8")     # as loop.one keeps it
        runs[arm] = run
    k3, k2, baseline = runs["K3"].path, runs["K2"].path, we.baseline_of(runs["K3"].path)
    got = {(path, ok) for _, path, ok in ex.labelled(tmp_path, task)}         # K3's baseline as the bytes checked by its digest
    assert got == {(b"the first session's tree", False), (k3 / "rounds" / "rule.patch", True), (k3 / "final.patch", True),
                   (k2 / "rounds" / "round-1.patch", False), (k2 / "final.patch", True)}
    assert len(ex.distinct(ex.labelled(tmp_path, task))) == 5                   # the baseline is a case, with its label
    baseline.write_bytes(b"something else")       # its bytes no longer have the kept digest: the admission rule refuses the run,
    assert {path for _, path, _ in ex.labelled(tmp_path, task)} == {k2 / "rounds" / "round-1.patch", k2 / "final.patch"}   # whole


def test_repair_4_the_export_labels_the_baseline_bytes_it_checked_and_never_reads_the_path_again(monkeypatch, tmp_path):
    """Review 1, finding 5: identity from the bytes. ``labelled`` hands on the bytes it checked against the kept digest, and
    ``distinct`` (the case's id and label) and ``main`` (the case's diff, ``diff_of``) use those bytes, never the path again."""
    import export_reviewer_cases as ex
    archive, task = we.c.Archive(tmp_path), "black-5634"
    run = archive.start("run", task, arm="K3", repeat=1)      # study review 1, finding 6: a row's repeat is its start's
    _keep_baseline(archive, run, b"checked first-session patch\r\n")
    baseline = we.baseline_of(run.path)
    run.write("final.patch", b"final patch")
    run.finish({"arm": "K3", "agent_started": True}, "finished")
    archive.start("check", task, checked_run=run.rel).finish({"checks": [{"patch": "round-1", "passes": False}]}, "finished")
    (tmp_path / "loop").mkdir()
    (tmp_path / "loop" / f"K3-{task}-1.json").write_text(json.dumps({"arm": "K3", "run": run.rel, "status": "finished"}), "utf-8")
    read = we.baseline_bytes

    def replaced_after_read(*args):
        data = read(*args)
        baseline.write_bytes(b"unscored replacement patch")                    # after the check of its digest
        return data

    monkeypatch.setattr(we, "baseline_bytes", replaced_after_read)
    cases = ex.distinct(ex.labelled(tmp_path, task))
    sha = lambda data: hashlib.sha256(data).hexdigest()[:16]               # noqa: E731
    assert (task, sha(b"unscored replacement patch")) not in cases
    held, ok = cases[(task, sha(b"checked first-session patch\r\n"))]
    assert ok is False and ex.diff_of(held) == "checked first-session patch\r\n"   # the bytes as they are: git reads its names
    assert ex.shown(ex.diff_of(held)) == "checked first-session patch\n"           # (study review 3, finding 6); as a case shows
    # the diff, as read_text gave a patch's text before
