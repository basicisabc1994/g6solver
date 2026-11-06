# SPDX-License-Identifier: Apache-2.0
"""Arm K3 on the host executor: the first session, the rule step and K2's loop all in the one host session, with K3's rules held as
in a container (the first prompt's check, the baseline kept before the step, ``rule.patch`` after it, ``step_ending``, and stop
symmetry). Fakes only: no docker, no Claude Code, no network, no model. The mirror script runs for real, with this Python, on local
folders, as in test_host_author.py, whose fakes these tests use."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import box_mcp  # noqa: E402
import loop  # noqa: E402
import run as we  # noqa: E402

_spec = importlib.util.spec_from_file_location("host_author_helpers_k3", Path(__file__).with_name("test_host_author.py"))
helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(helpers)
SONNET = helpers.SONNET
FIRST_TREE, RULE_TREE = "def f():\n    return 1\n", "def f():\n    return 2\n"
CLEAN = {"verdict": "none", "rule": "", "why": "", "test": "", "seconds": 1.0}


class K3Docker(helpers.HostDocker):
    """The host fakes' container, which also keeps each capture: a patch made in it (``diff_script``) is the container's tree as
    the last mirror left it, and ``docker cp`` of that patch copies it out."""

    def __init__(self, root: Path, task) -> None:
        super().__init__(root, task)
        self.files: dict[str, bytes] = {}

    def sh(self, ctr, script, *a, **kw):
        saved = re.search(r'git diff --cached --binary .* > "([^"]+)"', script)
        if saved:
            self.calls.append(("sh", script))
            self.files[saved[1]] = (self.repo / "src" / "mod.py").read_bytes()
            return helpers.captured()
        return super().sh(ctr, script, *a, **kw)

    def __call__(self, *args, input=None, timeout=None, env=None):
        if args and args[0] == "cp" and ":" in args[1] and args[1].split(":", 1)[1] in self.files:
            self.calls.append(args)
            Path(args[2]).write_bytes(self.files[args[1].split(":", 1)[1]])
            return helpers.ok()
        return super().__call__(*args, input=input, timeout=timeout, env=env)


def k3_host_run(monkeypatch, tmp_path, sessions, *, review=lambda n: CLEAN):
    monkeypatch.setattr(helpers, "HostDocker", K3Docker)
    asked = []
    monkeypatch.setattr(we, "run_critic", lambda ctx, task, run, agent, base, title, text, n=1: (asked.append(n), review(n))[1])
    record, docker, starts, ctx, task, workspaces = helpers.host_run(monkeypatch, tmp_path, "K3", sessions)
    return record, docker, starts, ctx, task, asked


def _captured(docker, name: str) -> int:
    return next(i for i, call in enumerate(docker.calls) if call[0] == "sh" and f"{we.WE}/rounds/{name}" in call[1])


def _ending(ctx, record) -> str:
    return json.loads((ctx.archive.root / record["run"] / we.c.ENDING).read_text("utf-8"))["status"]


# --- item 2: a host K3 run takes its rule step through the host session -------------------------------------------------------
def test_a_host_k3_run_keeps_round_1_resumes_the_host_session_with_the_rule_step_keeps_rule_then_runs_k2s_loop(monkeypatch,
                                                                                                                  tmp_path):
    record, docker, starts, ctx, task, asked = k3_host_run(monkeypatch, tmp_path, [("s1", FIRST_TREE, None), ("s1", RULE_TREE, None)])
    assert record["status"] == "finished", record.get("error")
    a_prompt = we.prompt(task, "title", "issue", "A")
    assert starts[0]["proc"].stdin.text == a_prompt and record["first_prompt_sha256"] == hashlib.sha256(a_prompt.encode()).hexdigest()
    assert len(starts) == 2 and "--resume" not in starts[0]["argv"] and starts[1]["argv"][-2:] == ["--resume", "s1"]
    assert starts[1]["proc"].stdin.text == we.RULE_STEP and starts[1]["before"] == FIRST_TREE      # the same workspace, resumed
    assert record["rule_step"] == {"ended": "", "at": "", "instruction": we.RULE_DIGEST} and asked == [1]
    folder = ctx.archive.root / record["run"]
    assert we.baseline_of(folder).read_text("utf-8") == FIRST_TREE and we.has_baseline(ctx.archive, record["run"])
    assert (folder / "rounds" / we.RULE_PATCH).read_text("utf-8") == RULE_TREE and (folder / "rule_stream.jsonl").is_file()
    mirrored = helpers.mirrors(docker)               # each capture reads the container after the session's tree went in
    assert mirrored[0] < _captured(docker, we.FIRST_PATCH) < mirrored[1] < _captured(docker, we.RULE_PATCH)
    assert [step for step, _ in record["step_ends"]] == ["first session", "capture round-1.patch", "rule session",
                                                          "capture rule.patch", "review 1"]
    assert _ending(ctx, record) == "finished" and we.scored_run(ctx.archive, record["run"])
    ctx.archive.start("validate", task.id).finish({"fail_to_pass": ["t"], "pass_to_pass": []}, "finished")
    seen = []
    monkeypatch.setattr(we, "_oracle_run", lambda ctx_, task_, *, patch, label, **kw: (seen.append((label, patch.read_bytes())),
                                                                                      {"results": {"t": "passed"}})[1])
    assert [row["patch"] for row in we.check(ctx, task, record["run"])["checks"]] == ["round-1", "rule", "final"]
    assert seen[0] == ("round-1", we.baseline_of(folder).read_bytes())      # the baseline's own bytes, checked by their digest


def test_a_host_k3_run_that_ends_before_its_first_patch_is_no_first_patch_on_record_no_row_and_made(monkeypatch, tmp_path):
    record, _, starts, ctx, task, asked = k3_host_run(monkeypatch, tmp_path, [("s1", FIRST_TREE, helpers.stream("s1")[:-1])])
    assert record["status"] == we.NO_FIRST_PATCH and _ending(ctx, record) == we.NO_FIRST_PATCH, record.get("error")
    assert len(starts) == 1 and asked == [] and record["rule_step"] == {"ended": "error", "at": "first session",
                                                                       "instruction": we.RULE_DIGEST}
    assert not we.has_baseline(ctx.archive, record["run"]) and not we.scored_run(ctx.archive, record["run"])
    ctx.archive.start("validate", task.id).finish({"fail_to_pass": ["t"], "pass_to_pass": []}, "finished")
    monkeypatch.setattr(we, "_oracle_run", lambda *a, **kw: pytest.fail("a run with no first patch has no baseline to score"))
    with pytest.raises(SystemExit, match="no Claude Code baseline"):
        we.check(ctx, task, record["run"])
    monkeypatch.setattr(loop, "tool", lambda *a, **kw: json.dumps(record))
    with pytest.raises(SystemExit, match="no Claude Code baseline"):
        loop.one(ctx.archive.root, "g6", "K3", task.id, 1)


def test_a_failure_after_a_host_k3_runs_no_first_patch_ending_never_makes_it_more_scoreable(monkeypatch, tmp_path):
    real, calls = box_mcp.mirror, []

    def mirror(workspace, docker, **kw):           # the first session's mirror goes in; the final one, after the ending, fails
        calls.append(1)
        if len(calls) == 2:
            raise box_mcp.BoxError("the mirror failed (1)")
        return real(workspace, docker, **kw)

    monkeypatch.setattr(box_mcp, "mirror", mirror)
    record, _, starts, ctx, *_ = k3_host_run(monkeypatch, tmp_path, [("s1", FIRST_TREE, helpers.stream("s1")[:-1])])
    folder = ctx.archive.root / record["run"]
    assert "the mirror failed" in record["error"] and (folder / we.c.AFTER).is_file() and len(starts) == 1
    assert record["status"] == we.NO_FIRST_PATCH and we.c.ending(ctx.archive.root, record["run"])["status"] == we.NO_FIRST_PATCH
    assert not we.c.scoreable(we.c.ending(ctx.archive.root, record["run"])) and not we.scored_run(ctx.archive, record["run"])


def test_a_host_k3_run_at_the_usage_limit_in_its_first_session_stays_limited_and_may_be_made_again(monkeypatch, tmp_path):
    limited = helpers.stream("s1", extra=({"type": "assistant", "session_id": "s1", "message": {
        "id": "lim", "model": "<synthetic>", "content": [{"type": "text", "text": helpers.LIMIT_TEXT}]}},))
    record, _, starts, ctx, *_ = k3_host_run(monkeypatch, tmp_path, [("s1", "", limited)])
    assert record["status"] == "limited" and _ending(ctx, record) == "limited" and len(starts) == 1, record.get("error")


def test_a_host_k3_rule_step_that_does_not_end_cleanly_is_a_row_with_its_own_ending_as_k2s_would_be(monkeypatch, tmp_path):
    record, _, starts, ctx, task, asked = k3_host_run(monkeypatch, tmp_path, [("s1", FIRST_TREE, None),
                                                                              ("s1", RULE_TREE, helpers.stream("s1")[:-1])])
    assert record["status"] == "error" and _ending(ctx, record) == "error", record.get("error")
    assert len(starts) == 2 and asked == [] and record["rule_step"]["at"] == "rule step"
    assert we.has_baseline(ctx.archive, record["run"]) and we.scored_run(ctx.archive, record["run"])    # stop symmetry: a row
    assert not (ctx.archive.root / record["run"] / "rounds" / we.RULE_PATCH).exists()


def test_a_host_k3_run_whose_review_ended_after_the_deadline_ends_timeout_on_record(monkeypatch, tmp_path):
    late = []

    def review(n):
        late.append(n)
        return CLEAN

    def critic(ctx, task, run, agent, base, title, text, n=1):      # a review that ended after the run's deadline
        ctx.watchdog.ends.append((f"review {n}", ctx.watchdog.deadline + 1.0))
        return review(n)

    monkeypatch.setattr(helpers, "HostDocker", K3Docker)
    monkeypatch.setattr(we, "run_critic", critic)
    record, _, starts, ctx, *_ = helpers.host_run(monkeypatch, tmp_path, "K3", [("s1", FIRST_TREE, None), ("s1", RULE_TREE, None)])
    assert record["status"] == "timeout" and _ending(ctx, record) == "timeout" and late == [1], record.get("error")
    assert we.has_baseline(ctx.archive, record["run"]) and we.scored_run(ctx.archive, record["run"])     # as K2's timed-out runs are


def test_repair_2_an_ending_written_early_stands_and_ended_as_gives_the_same_answer(monkeypatch, tmp_path):
    """Review 1, finding 3: a failed rule session writes "error" at once; its mirror then ends past the deadline. The ending
    decided first stands: the record, the ending on file and ``ended_as`` give one answer, and nothing writes over it."""
    active, late, calls = [], [], []
    monkeypatch.setattr(we.Watchdog, "start", lambda dog: active.append(dog))
    monkeypatch.setattr(we.Watchdog, "now", lambda dog: dog.deadline + (1 if late else -10))
    mirror = box_mcp.mirror

    def slow_mirror(*args, **kw):
        calls.append(1)
        result = mirror(*args, **kw)
        if len(calls) == 2:                         # the rule session has written "error"; its mirror ends past the deadline
            late.append(True)
        return result

    monkeypatch.setattr(box_mcp, "mirror", slow_mirror)
    record, _, _, ctx, *_ = k3_host_run(monkeypatch, tmp_path, [("s1", FIRST_TREE, None), ("s1", RULE_TREE, helpers.stream("s1")[:-1])])
    assert we.has_baseline(ctx.archive, record["run"])
    assert any(step == "rule session" and at > active[0].deadline for step, at in record["step_ends"])      # the step was late
    run = SimpleNamespace(rel=record["run"])
    assert record["status"] == _ending(ctx, record) == we.ended_as(ctx, run, "error", dry=False, dog=active[0]) == "error"
    assert we.ended_as(ctx, run, "finished", dry=False, dog=active[0]) == "error"       # asked again later: the first stands


DEFECT = {"verdict": "defect", "rule": "r", "why": "w", "test": helpers.TEST, "seconds": 0.0}


def _gated(monkeypatch):
    monkeypatch.setattr(we, "run_set", lambda *a, **kw: helpers.ON_TREE)
    monkeypatch.setattr(we, "parent_check", lambda *a, **kw: helpers.AT_PARENT)


def test_repair_5_a_host_k3_repair_resumes_the_first_session_by_its_own_id_and_one_naming_another_ends_the_run_error(monkeypatch,
                                                                                                                      tmp_path):
    """Review 1, finding 6: the session a repair resumes is fixed once, from the first session's own result, never read again
    from the tally; a repair whose own result names another session ends the run "error", as a rule step's does."""
    _gated(monkeypatch)
    record, _, starts, ctx, *_ = k3_host_run(monkeypatch, tmp_path, [
        ("s1", FIRST_TREE, None), ("s1", RULE_TREE, None), ("other-session", "def f():\n    return 3\n", None),
        ("other-session", "def f():\n    return 4\n", None)], review=lambda n: DEFECT if n <= 2 else CLEAN)
    assert [start["argv"][-2:] for start in starts[1:]] == [["--resume", "s1"]] * 2 and len(starts) == 3
    assert record["status"] == "error" and _ending(ctx, record) == "error" and record["repairs"]["ended"] == "error"


def test_repair_5_a_host_k2_repair_naming_another_session_ends_the_run_error_too(monkeypatch, tmp_path):
    _gated(monkeypatch)
    monkeypatch.setattr(we, "run_critic", lambda *a, **kw: DEFECT)
    record, _, starts, ctx, *_ = helpers.host_run(monkeypatch, tmp_path, "K2", [
        ("s1", FIRST_TREE, None), ("other-session", RULE_TREE, None), ("other-session", "def f():\n    return 3\n", None)])
    assert len(starts) == 2 and starts[1]["argv"][-2:] == ["--resume", "s1"]
    assert record["status"] == "error" and _ending(ctx, record) == "error" and record["repairs"]["ended"] == "error"


def _with_subtype(subtype: str, is_error=False) -> list[str]:
    lines = helpers.stream("s1")
    lines[-1] = json.dumps({**json.loads(lines[-1]), "subtype": subtype, "is_error": is_error})
    return lines


def test_repair_6_a_host_k3_repair_with_a_result_claude_code_does_not_mark_a_success_ends_the_run_error(monkeypatch, tmp_path):
    """Review 1, finding 7: one strict reader of a session's result. A subtype nobody knows is no success, in a repair too."""
    _gated(monkeypatch)
    record, _, starts, ctx, *_ = k3_host_run(monkeypatch, tmp_path, [
        ("s1", FIRST_TREE, None), ("s1", RULE_TREE, None), ("s1", "def f():\n    return 3\n", _with_subtype("future_terminal_status"))],
        review=lambda n: DEFECT if n == 1 else CLEAN)
    assert len(starts) == 3 and record["status"] == "error" and _ending(ctx, record) == "error"


@pytest.mark.parametrize(("result", "status"), [
    ({"subtype": "success", "is_error": False}, "finished"),
    ({"subtype": "success", "is_error": True}, "error"),
    ({"subtype": "success"}, "error"),                                   # no word on error is no success
    ({"subtype": "future_terminal_status", "is_error": False}, "error"),  # an ending nobody knows
    ({"subtype": "error_max_turns", "is_error": True}, "cap_turns"),
    ({"subtype": "error_max_budget_usd", "is_error": True}, "cap_cost"),
    ({"subtype": "error_during_execution", "is_error": True}, "error"),
    (None, "error")])
def test_repair_6_every_arm_and_executor_reads_a_sessions_result_by_one_rule(result, status):
    assert we.status_of("", SimpleNamespace(result=result)) == status
    assert we.step_ending("", result, 0) == status                       # K3's steps, by the same reader
    assert we.status_of("timeout", SimpleNamespace(result=result)) == we.step_ending("timeout", result, 0) == "timeout"
    if status == "finished":
        assert we.step_ending("", result, 1) == "error"                  # and a success that did not exit 0 is no success
    facts = {"init": {"why": ""}, "result": result, "refusals": []}
    assert we.host_ending("", facts, code=0, limit=None, by_g6=False)[0] == ("" if status == "finished" else status)


def _no_session_id() -> list[str]:
    lines = helpers.stream("s1")
    result = json.loads(lines[-1])
    result.pop("session_id")
    lines[-1] = json.dumps(result)
    return lines


def test_review_2_finding_1_a_first_session_whose_result_names_no_session_starts_no_rule_step_and_is_an_error_row(monkeypatch,
                                                                                                                tmp_path):
    record, _, starts, ctx, *_ = k3_host_run(monkeypatch, tmp_path, [("s1", FIRST_TREE, _no_session_id()), ("s2", RULE_TREE, None)])
    assert len(starts) == 1                                              # no prompt to any session, resumed or fresh
    assert record["status"] == "error" and _ending(ctx, record) == "error" and record["rule_step"]["at"] == "rule step"
    assert we.has_baseline(ctx.archive, record["run"]) and we.scored_run(ctx.archive, record["run"])   # a row, as every stopped K3


def test_review_2_a_host_k2_first_session_whose_result_names_no_session_starts_no_repair(monkeypatch, tmp_path):
    _gated(monkeypatch)
    monkeypatch.setattr(we, "run_critic", lambda *a, **kw: DEFECT)
    record, _, starts, ctx, *_ = helpers.host_run(monkeypatch, tmp_path, "K2", [("s1", FIRST_TREE, _no_session_id()),
                                                                                ("s2", RULE_TREE, None)])
    assert len(starts) == 1 and record["status"] == "error" and _ending(ctx, record) == "error"


# --- item 3: the run's watchdog ends a host session's processes, as it ends a container session's ----------------------------
def test_a_resumed_host_session_is_registered_with_the_runs_watchdog_which_ends_its_processes_once_when_it_fires(monkeypatch,
                                                                                                                 tmp_path):
    dog, ended, at_fire = we.Watchdog(10.0, clock=lambda: 0.0), [], []

    class Firing(io.BytesIO):                     # the session's stdout: the watchdog's timer fires while it is read
        def readline(self, *a):
            line = super().readline(*a)
            if b'"assistant"' in line and not at_fire:
                dog.fire()
                at_fire.append(len(ended))          # how many times its processes were ended by then
            return line

    def start(argv, *, cwd, env, stdin, stdout, stderr, **kw):
        proc = helpers.Proc(helpers.stream("s1"))
        proc.stdout = Firing(proc.stdout.getvalue())
        return proc

    monkeypatch.setattr(we, "host_start", lambda start_, argv, **kw: SimpleNamespace(proc=start_(argv, **kw)))
    monkeypatch.setattr(we, "end_session", lambda proc, workspace, tree=None: (ended.append((proc, workspace, tree)), "")[1])
    ctx = we.Context(archive=we.c.Archive(tmp_path / "a"), docker=None, key_reader=lambda: pytest.fail("no key"), issue_reader=None,
                     g6={}, variant=we.ROUND3, arm="K3", model=SONNET, environ={"PATH": "p"}, executor="host", starter=start)
    run, ws = ctx.archive.start("run", "t"), tmp_path / "ws"
    ws.mkdir()
    we.host_session(ctx, run, ws, tmp_path / "mcp.json", tmp_path / "settings.json", we.RULE_STEP, "rule_stream.jsonl",
                    resume="s1", seconds=60.0, tally=we.c.Tally(), deadline=dog)
    assert at_fire == [1] and len(ended) == 1 and ended[0][1] == ws      # ended by the watchdog when it fired, and only once
    assert not dog._killers                                              # and no longer watched once the session is over


def test_a_host_k3_run_gives_its_watchdog_to_each_resumed_session_as_a_container_run_does(monkeypatch, tmp_path):
    real, given = we.host_session, []

    def session(*a, deadline=None, **kw):
        given.append((a[6], deadline))
        return real(*a, **kw, **({"deadline": deadline} if deadline is not None else {}))

    monkeypatch.setattr(we, "host_session", session)
    record, *_ = k3_host_run(monkeypatch, tmp_path, [("s1", FIRST_TREE, None), ("s1", RULE_TREE, None)])
    assert record["status"] == "finished", record.get("error")
    assert [name for name, _ in given] == ["stream.jsonl", "rule_stream.jsonl"] and given[0][1] is None   # the first, as in a container
    assert isinstance(given[1][1], we.Watchdog)                          # the rule step's, the run's one watchdog
