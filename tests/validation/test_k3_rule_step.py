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
import re
import sys
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

    def __init__(self, task, events, sent, fail=()):
        self.task, self.events, self.sent, self.fail = task, events, sent, set(fail)
        self.files, self.tree = {}, FIRST

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
            return self.done(stdout=json.dumps({"head": self.task.parent, "tree": "tree", "dirty": 0, "commits": 1}).encode())
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
         loop_writes=False, loop_ends=None):
    """``_run_task`` for ``arm`` with fakes only: no model, no container. Returns the record, what happened in order, the
    prompts sent, the context and the task."""
    task, events, sent = we.load_tasks()[0], [], []
    docker = _Docker(task, events, sent, fail)
    if first is not SUCCESS:
        docker.popen = lambda *a, **kw: (events.append(("first session",)), SimpleNamespace(
            stdin=_Stdin(sent), stdout=io.BytesIO(json.dumps(first).encode() + b"\n"), stderr=io.BytesIO(),
            poll=lambda: 1, wait=lambda **kw: 1))[1]

    def session(ctx, run, agent, command, key, tally, text, name, *, seconds, stop_usd, codes=None):
        events.append(("resumed", name, text, list(command)))
        docker.tree = AFTER_RULE
        if agent_rewrites:          # the agent owns the container's rounds folder, and writes over the first patch there
            docker.files[f"{we.WE}/rounds/round-1.patch"] = b"the agent's own round-1"
        if not step_ends:
            tally.feed(dict(SUCCESS))
        if codes is not None:
            codes.append(0)
        return step_ends

    def review_loop(*a, finding="any", after_step=False, **kw):
        events.append(("loop", finding, after_step))
        if loop_writes:             # the loop's own capture before review 2
            we.keep_first_patch(a[0], a[1], a[3], a[4], 2)
        if loop_ends:               # a repair's own result, which the run's status is read from as K2's is
            a[7].feed(dict(loop_ends))
        return {"status": "no_counterexample_in_domain"}

    monkeypatch.setattr(we, "key_check", lambda *a, **kw: {"ok": True})
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
    rounds = ctx.archive.root / record["run"] / "rounds"
    assert (rounds / "round-1.patch").read_bytes() == FIRST and (rounds / "rule.patch").read_bytes() == AFTER_RULE
    assert record["status"] == "finished"


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
    assert [e[0] for e in events] == seen and record["status"] == we.NO_FIRST_PATCH == "no_first_patch"
    assert record["rule_step"]["ended"] == ended and record["rule_step"]["at"] == at and "repairs" not in record
    assert not we.scored_run(record) and not (ctx.archive.root / record["run"] / "rounds" / "round-1.patch").exists()
    ctx.archive.start("validate", task.id).finish({"fail_to_pass": ["t"], "pass_to_pass": []}, "finished")
    monkeypatch.setattr(we, "_oracle_run", lambda *a, **kw: pytest.fail("a run with no first patch has no baseline to score"))
    with pytest.raises(SystemExit, match="no Claude Code baseline"):
        we.check(ctx, task, record["run"])
    monkeypatch.setattr(loop, "tool", lambda *a, **kw: json.dumps(record))
    with pytest.raises(SystemExit, match="no Claude Code baseline"):
        loop.one(ctx.archive.root, "g6", "K3", task.id, 1)
    assert not (ctx.archive.root / "loop").exists()


def _row_of(monkeypatch, root, record, arm):
    monkeypatch.setattr(loop, "tool", lambda *a, **kw: json.dumps(record))
    return loop.one(root, "g6", arm, record["task"], 1)


@pytest.mark.parametrize(("k3", "k2", "labels"), [
    ({"step_ends": "cap_cost"}, {"first": EXPENSIVE}, ["round-1", "final"]),           # stopped in the rule step
    ({"loop_ends": CAPPED}, {"loop_ends": CAPPED}, ["round-1", "rule", "final"]),      # stopped in the review loop
])
def test_a_stopped_k3_run_is_saved_and_read_as_k2s_with_the_same_ending_and_check_scores_its_first_patch(monkeypatch, tmp_path,
                                                                                                        k3, k2, labels):
    ours, _, _, ctx, task = _run(monkeypatch, tmp_path, "K3", **k3)
    theirs, *_ = _run(monkeypatch, tmp_path, "K2", **k2)
    assert ours["status"] == theirs["status"] == "cap_cost" and we.scored_run(ours)
    rows = [_row_of(monkeypatch, ctx.archive.root, ours, "K3"), _row_of(monkeypatch, ctx.archive.root, theirs, "K2")]
    assert [{k: v for k, v in row.items() if k not in ("arm", "run", "cost_usd")} for row in rows] == [
        {"task": task.id, "repeat": 1, "status": "cap_cost", "why": None, "passes": False, "failing": None, "tests": []}] * 2
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
    assert record["status"] == "finished"
    assert (rounds / "round-1.patch").read_bytes() == FIRST and (rounds / "rule.patch").read_bytes() == AFTER_RULE
    assert (rounds / "round-2.patch").read_bytes() == AFTER_RULE and not (rounds / "rounds").exists()
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
    assert not (run.path / "rounds" / "round-1.patch").exists()
    body["value"] = b"+token = 'sk-ant-abcdefgh1234'\n"
    assert we.kept(ctx, task, "agent", "base", run, "rule.patch") == "finished"
    assert (run.path / "rounds" / "rule.patch").read_bytes() == body["value"]


def test_the_rule_step_resumes_the_session_on_the_runs_clock_tally_and_stop_and_never_past_its_deadline(monkeypatch, tmp_path):
    calls, tally = [], we.c.Tally()

    def session(ctx, run, agent, command, key, tally_, text, name, *, seconds, stop_usd, codes=None):
        calls.append({"command": command, "key": key, "tally": tally_, "text": text, "name": name, "seconds": seconds,
                      "stop_usd": stop_usd})
        codes.append(0)
        tally_.feed(dict(SUCCESS))
        return ""

    monkeypatch.setattr(we, "run_session", session)
    monkeypatch.setattr(we, "kept", lambda ctx, task, agent, base, run, name: (calls.append(name), "finished")[1])
    monkeypatch.setattr(we.time, "monotonic", lambda: 1000.0)
    args = ["exec", "-i", "agent", "claude", "-p", "--disallowedTools", "WebFetch"]
    assert we.rule_step(None, None, None, "agent", "base1", args, "key", tally, 7.5, 1000.0 - 100.0) == "finished"
    assert calls == [{"command": we.scout_again(args), "key": "key", "tally": tally, "text": we.RULE_STEP,
                      "name": "rule_stream.jsonl", "seconds": we.c.WALL_CLOCK_S - 100.0, "stop_usd": 7.5}, "rule.patch"]
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
    for arm, names in (("K3", ("round-2", "rule", "round-1")), ("K2", ("round-10", "round-2", "round-1", "rule"))):
        run = archive.start("run", task.id, arm=arm)
        (run.path / "rounds").mkdir()
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
