# SPDX-License-Identifier: Apache-2.0
"""Replay 1 (PLAN.md, 2026-11-03, afternoon): the final review with a reproduction first, beside the frozen review, which it leaves
as it was."""
from __future__ import annotations

import hashlib
import io
import itertools
import json
import os
import re
import subprocess
import sys
import tarfile
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import final_review as fr  # noqa: E402
import run as we  # noqa: E402
import wecommon as c  # noqa: E402

TASK = "black-5634"
REPLY = "RULE: every item is kept\nVERDICT: defect\nWHY: drops the last\n```python\ndef test_x():\n    assert 0\n```\n"
QUIET = "RULE: every item is kept\nVERDICT: none\nWHY: nothing found\n"
LIMIT = "Codex Task\nYou've hit your usage limit. Try again at 3:05 PM.\n"
PASSED = we.ProbeRun(0, {"t::test_x": "passed"}, {}, "")
FAILED = we.ProbeRun(1, {"t::test_x": "failed"}, {"t::test_x": "assert 1 == 2"}, "")
BROKEN = we.ProbeRun(2, {}, {}, "ImportError")


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


class _Critic:
    """The reviewer: each call reads its pack and gives the next reply, quoting the pack's first line as the contract asks. A reply
    that is an exception is raised; ``LIMIT`` is answered as a Codex limit is, with no quote."""

    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    def __call__(self, pack):
        self.seen.append({p.relative_to(pack).as_posix(): p.read_text("utf-8") for p in pack.rglob("*") if p.is_file()})
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply if reply == LIMIT else f"READ: {self.seen[-1]['issue.txt'].splitlines()[0]}\n{reply}"


def _ctx(tmp_path, critic, docker=None):
    class Ctx:
        pass

    ctx = Ctx()
    ctx.archive, ctx.docker, ctx.g6, ctx.variant, ctx.arm = type("A", (), {"root": tmp_path})(), docker or _Docker(), {"commit": "c" * 40}, we.ROUND3, "L9"
    ctx.critic = critic
    return ctx


def _runs(monkeypatch, *results):
    """``run_set`` gives these in turn, and records where each ran."""
    got, where = iter(results), []

    def run_set(ctx, task, agent, at):
        where.append(at)
        return next(got)

    monkeypatch.setattr(we, "run_set", run_set)
    return where


def _patch(tmp_path, body="a patch"):
    patch = tmp_path / "final.patch"
    patch.write_text(body, "utf-8")
    return patch


def _review(tmp_path, critic, docker=None, earlier=None):
    return fr.reproduce_review(_ctx(tmp_path, critic, docker), we.task_by_id(TASK), _patch(tmp_path), "a title", "the issue",
                               earlier=earlier)


def _row(root, arm, task=TASK, repeat=1, body="one patch", status="finished", passes=True):
    run = root / "run" / f"{arm}{repeat}"
    run.mkdir(parents=True, exist_ok=True)
    (run / "final.patch").write_text(body, "utf-8")
    row = {"arm": arm, "task": task, "repeat": repeat, "run": f"run/{arm}{repeat}", "status": status, "passes": passes, "failing": 0}
    (root / "loop").mkdir(exist_ok=True)
    (root / "loop" / f"{arm}-{task}-{repeat}.json").write_text(json.dumps(row), "utf-8")
    (run / "run.json").write_text(json.dumps({"status": status, "agent_started": True}), "utf-8")   # the run as the archive holds it:
    c.Archive(root).append({"event": "started", "run": row["run"], "kind": "run", "arm": arm, "task": task,   # its record and start,
                            "repeat": repeat})          # whose facts alone the scheduler reads (study review 1, finding 7)
    return row


def _main(tmp_path, monkeypatch, capsys, critic, runs, argv=(), rows=(("A", "patch one", True), ("L9", "patch two", True))):
    """``main`` on saved patches, with docker, Codex, the issue and the clock all fakes."""
    root = tmp_path / "round3"
    for arm, body, passes in rows:
        _row(root, arm, body=body, passes=passes)
    docker = _Docker()
    ids, ticks, runs = itertools.count(1), itertools.count(1000.0, 1.0), itertools.cycle(runs)
    monkeypatch.setattr(fr.c, "archive_root", lambda: tmp_path)
    monkeypatch.setattr(fr.we, "Docker", lambda: docker)
    monkeypatch.setattr(fr.we, "g6_info", lambda g6: {"commit": "c" * 40})
    monkeypatch.setattr(fr.we, "read_issue", lambda task: ("a title", "the issue"))
    monkeypatch.setattr(fr.we, "read_thread", lambda task: "\n\nthe thread")
    monkeypatch.setattr(fr.we, "run_codex", critic)       # the frozen review's call
    monkeypatch.setattr(fr, "codex", critic)              # replay 1's, which reads Codex's exit too
    monkeypatch.setattr(fr.we, "run_set", lambda ctx, task, agent, where: next(runs))
    monkeypatch.setattr(fr.uuid, "uuid4", lambda: uuid.UUID(int=next(ids)))
    monkeypatch.setattr(fr.time, "monotonic", lambda: next(ticks))
    fr.main(["--tasks", TASK, *argv])
    saved = {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted((root / "loop").rglob("*.json")) if p.parent.name != "loop"}
    calls = [[str(a).replace(str(tmp_path), "<tmp>").replace("\\", "/") for a in args] for args in docker.calls]
    return {"out": capsys.readouterr().out, "saved": saved, "calls": calls, "scripts": docker.scripts, "root": root}


# --- the frozen review stays as it was ---------------------------------------------------------------------------------------

FROZEN_SAVED = """{{
 "task": "black-5634",
 "first_seen_in": "{run}",
 "verdict": "defect",
 "rule": "every item is kept",
 "why": "drops the last",
 "test": "def test_x():\\n    assert 0",
 "error": "",
 "said": "reproduced",
 "failing": [
  "critic:t::test_x"
 ],
 "at_base": {{
  "t::test_x": "passed"
 }},
 "on_patch": {{
  "t::test_x": "failed"
 }},
 "seconds": 1.0
}}"""


def _frozen_calls(sha):
    name, repo = "g6we-review-0000000000", "/home/tester/work/black"
    return [["run", "-d", "--name", name, "--label", "g6we=1", "--network", "none", "--security-opt", "seccomp=unconfined",
             "--security-opt", "apparmor=unconfined", "-u", "tester", "-e", "HOME=/home/tester", "-w", repo,
             "g6we-task:black-5634-cccccccccccc-r3", "tail", "-f", "/dev/null"],
            ["exec", "-u", "tester", "-w", repo, name, "git", "archive", "--format=tar", "c61c1db94a712346dd7029d297f3fccfb9fa4dd9"],
            ["exec", "-i", "-u", "root", name, "sh", "-c",
             'mkdir -p "$(dirname "/opt/we/critic/test_critic.py")" && cat > "/opt/we/critic/test_critic.py" && chmod 644 '
             '"/opt/we/critic/test_critic.py"'],
            ["cp", f"<tmp>/round3/critic_packs/<fresh>/held-{sha}.patch", f"{name}:/tmp/we.patch"],   # the bytes read, in a
            ["rm", "-f", name]]                     # folder made fresh for them (study follow-up review 3, finding 3)


def _fresh(calls):
    """The calls with the name of each folder made fresh for a private copy (``wecommon.fresh``) written as ``<fresh>``."""
    return [[re.sub(r"(critic_packs/)[0-9a-f]{32}(/held-)", r"\1<fresh>\2", a) for a in call] for call in calls]


def test_the_frozen_review_is_byte_for_byte_what_it_was(tmp_path, monkeypatch, capsys):
    critic = _Critic(REPLY)
    got = _main(tmp_path, monkeypatch, capsys, critic, [PASSED, FAILED])
    line = '{"task": "black-5634", "said": "reproduced", "seconds": 1.0, "error": ""}\n'
    assert got["out"] == line * 2
    assert got["saved"] == {
        "loop/final_review/59c01c68c0d0ca432c95f3c694d7f370bde83265b81007d1979ccfaf6fef7ca1.json":
            FROZEN_SAVED.format(run="run/L91").replace("\n", os.linesep).encode("utf-8"),
        "loop/final_review/5da3889b34525a0b01340507a691e293618f811c696fdbf2ecc19cfd13a2151e.json":
            FROZEN_SAVED.format(run="run/A1").replace("\n", os.linesep).encode("utf-8")}
    assert _fresh(got["calls"]) == [*_frozen_calls("59c01c68c0d0ca432c95f3c694d7f370bde83265b81007d1979ccfaf6fef7ca1"),
                                    *_frozen_calls("5da3889b34525a0b01340507a691e293618f811c696fdbf2ecc19cfd13a2151e")]
    assert got["scripts"] == ["git apply --binary --whitespace=nowarn /tmp/we.patch"] * 2
    issue = "review {}\n\na title\n\nthe issue\n\nthe thread"
    assert critic.seen == [{"candidate.patch": "patch two", "critic_prompt.md": we.PATCH_CRITIC, "parent/src/app.py": "print('parent')\n",
                            "issue.txt": issue.format(uuid.UUID(int=1).hex)},
                           {"candidate.patch": "patch one", "critic_prompt.md": we.PATCH_CRITIC, "parent/src/app.py": "print('parent')\n",
                            "issue.txt": issue.format(uuid.UUID(int=3).hex)}]


def test_the_frozen_reviewers_text_is_the_one_the_blind_experiment_measured():
    assert hashlib.sha256(we.PATCH_CRITIC.encode("utf-8")).hexdigest() == "c988f143bf895658dd0584561e410535de497c9d3eb9840e005fa8204d3dc11c"
    assert we.critic_text("L9") == we.PATCH_CRITIC


def test_the_replay_writes_nothing_where_the_frozen_review_is_kept(tmp_path, monkeypatch, capsys):
    got = _main(tmp_path, monkeypatch, capsys, _Critic(REPLY, QUIET), [FAILED, FAILED], argv=["--reproduce"])
    assert got["saved"] and all(name.startswith(f"loop/{fr.REPRODUCE}/") for name in got["saved"])


# --- step 1: the issue's own scenario, from a reviewer shown no patch -------------------------------------------------------

def test_step_1_reads_the_issue_and_the_parent_and_never_the_patch(tmp_path, monkeypatch):
    _runs(monkeypatch, FAILED, FAILED)
    critic = _Critic(REPLY, QUIET)
    _review(tmp_path, critic)
    first, second = critic.seen
    assert set(first) == {"issue.txt", "critic_prompt.md", "parent/src/app.py"}
    assert first["critic_prompt.md"] == fr.REPRODUCE_FIRST and first["issue.txt"].endswith("\n\na title\n\nthe issue")
    assert set(second) == {"issue.txt", "critic_prompt.md", "candidate.patch", "parent/src/app.py"}
    assert first["issue.txt"].splitlines()[0] != second["issue.txt"].splitlines()[0]     # each step proves it read its own pack


def test_step_1_keeps_the_frozen_reviewers_limits_reply_form_and_grounding():
    def part(text, tag):
        return text[text.index(f"<{tag}>"):text.index(f"</{tag}>")]

    assert part(fr.REPRODUCE_FIRST, "hard_limits") == part(we.PATCH_CRITIC, "hard_limits")
    assert part(fr.REPRODUCE_FIRST, "grounding_rules") == part(we.PATCH_CRITIC, "grounding_rules")
    assert fr.REPRODUCE_FIRST.endswith(we.PATCH_CRITIC[we.PATCH_CRITIC.index("<grounding_rules>"):])
    for field in ("READ:", "RULE:", "VERDICT: defect or none", "WHY:", "```python"):
        assert field in part(fr.REPRODUCE_FIRST, "structured_output_contract")
    assert "candidate" not in fr.REPRODUCE_FIRST and "patch" not in fr.REPRODUCE_FIRST


def test_step_1s_test_runs_on_the_parent_and_then_on_the_patch_in_its_own_place(tmp_path, monkeypatch):
    where = _runs(monkeypatch, FAILED, PASSED)
    docker = _Docker()
    got = _review(tmp_path, _Critic(REPLY, QUIET), docker)["reproduce"]
    assert where == [fr.REPRODUCE_AT, fr.REPRODUCE_AT]
    assert any(f"{fr.REPRODUCE_AT}/test_reproduce.py" in " ".join(args) for args in docker.calls)
    assert (got["said"], got["at_parent"], got["with_patch"]) == ("reproduced", "yes", "passed")
    assert got["at_base"] == FAILED.results and got["on_patch"] == PASSED.results
    assert sum("git apply" in script for script in docker.scripts) == 1


def test_a_test_that_does_not_fail_at_the_parent_is_no_reproduction_and_the_judge_is_told(tmp_path, monkeypatch):
    _runs(monkeypatch, PASSED, FAILED)
    critic = _Critic(REPLY, QUIET)
    got = _review(tmp_path, critic)["reproduce"]
    assert (got["said"], got["at_parent"], got["with_patch"]) == ("not reproduced", "no", "failed")
    told = critic.seen[1]["critic_prompt.md"]
    assert "- Failed on parent/: no\n" in told and fr.NOT_REPRODUCED in told and "- With the candidate: failed\n" in told


def test_a_reproducer_that_wrote_no_test_reproduced_nothing_and_ran_nothing(tmp_path, monkeypatch):
    where = _runs(monkeypatch)
    critic = _Critic(QUIET, QUIET)
    got = _review(tmp_path, critic)["reproduce"]
    assert where == [] and (got["said"], got["at_parent"], got["with_patch"]) == ("not reproduced", "no", "did not run")
    assert fr.NO_TEST in critic.seen[1]["critic_prompt.md"]


def test_a_test_that_will_not_run_on_the_patch_did_not_run(tmp_path, monkeypatch):
    _runs(monkeypatch, FAILED, BROKEN)
    critic = _Critic(REPLY, QUIET)
    got = _review(tmp_path, critic)["reproduce"]
    assert (got["said"], got["with_patch"]) == ("reproduced", "did not run")
    assert "- With the candidate: did not run\n" in critic.seen[1]["critic_prompt.md"]


# --- step 2: the frozen review's text, then the reproduction's results ------------------------------------------------------

def test_step_2s_prompt_is_the_frozen_text_exactly_then_the_results(tmp_path, monkeypatch):
    _runs(monkeypatch, FAILED, FAILED)
    critic = _Critic(REPLY, QUIET)
    _review(tmp_path, critic)
    told = critic.seen[1]["critic_prompt.md"]
    assert told.startswith(we.PATCH_CRITIC)
    note = told[len(we.PATCH_CRITIC):]
    assert note.startswith("\n<reproduction>\n") and "- Failed on parent/: yes\n" in note and "- With the candidate: failed\n" in note
    assert "- t::test_x\n" in note and "assert 1 == 2" in note and "callers" in note


def test_the_judge_sees_a_few_lines_of_each_failure_and_no_home_folder(tmp_path, monkeypatch):
    long = "\n".join(f"line {n} in /home/tester/work/black/src/x.py" for n in range(20))
    _runs(monkeypatch, FAILED, we.ProbeRun(1, {"t::test_x": "failed"}, {"t::test_x": long}, ""))
    critic = _Critic(REPLY, QUIET)
    _review(tmp_path, critic)
    note = critic.seen[1]["critic_prompt.md"][len(we.PATCH_CRITIC):]
    assert "line 19" in note and "line 15" in note and "line 14" not in note
    assert "/home/tester" not in note and "~/work/black/src/x.py" in note


@pytest.mark.parametrize(("runs", "said"), [((PASSED, FAILED), "reproduced"), ((FAILED, PASSED), "opinion"),
                                            ((BROKEN, BROKEN), "opinion")])
def test_the_judge_is_classified_as_the_frozen_review_is(tmp_path, monkeypatch, runs, said):
    where = _runs(monkeypatch, *runs)
    got = _review(tmp_path, _Critic(QUIET, REPLY))
    assert where == [we.CRITIC_AT, we.CRITIC_AT] and got["said"] == said
    assert got["at_base"] == runs[0].results and got["on_patch"] == runs[1].results
    assert got["failing"] == (["critic:t::test_x"] if said == "reproduced" else [])


def test_a_judge_that_finds_nothing_says_none(tmp_path, monkeypatch):
    _runs(monkeypatch)
    assert _review(tmp_path, _Critic(QUIET, QUIET))["said"] == "none"


def test_no_arm_and_no_verdict_reaches_either_prompt(tmp_path, monkeypatch, capsys):
    critic = _Critic(REPLY, QUIET)
    _main(tmp_path, monkeypatch, capsys, critic, [FAILED, FAILED], argv=["--reproduce"],
          rows=(("K1", "patch one", False), ("M9", "patch two", True)))
    reproduced = fr.REPRODUCTION.format(at_parent="yes", with_patch="failed",
                                        detail="Its cases that failed with the candidate:\n- t::test_x\n  assert 1 == 2\n")
    nothing = fr.REPRODUCTION.format(at_parent="no", with_patch="did not run", detail=fr.NO_TEST)
    assert [pack["critic_prompt.md"] for pack in critic.seen] == [fr.REPRODUCE_FIRST, we.PATCH_CRITIC + reproduced,
                                                                   fr.REPRODUCE_FIRST, we.PATCH_CRITIC + nothing]
    for pack in critic.seen:
        assert set(pack) <= {"issue.txt", "critic_prompt.md", "candidate.patch", "parent/src/app.py"}
        assert pack["issue.txt"].split("\n", 1)[1] == "\na title\n\nthe issue\n\nthe thread"
        assert pack.get("candidate.patch", "patch one") in ("patch one", "patch two")
        text = "\n".join(pack.values())
        for word in ("K1", "M9", "passes", "amended", "maintainers"):
            assert word not in text, word


# --- failures, limits and resuming --------------------------------------------------------------------------------------------

def test_a_limit_is_saved_as_failed_with_its_text_and_is_not_counted(tmp_path, monkeypatch, capsys):
    critic = _Critic(LIMIT)
    got = _main(tmp_path, monkeypatch, capsys, critic, [FAILED], argv=["--reproduce"], rows=(("A", "patch one", True),))
    assert len(critic.seen) == 1                                  # no judge is asked once the first step had no review
    kept = json.loads(next(iter(got["saved"].values())))
    assert kept["said"] == "failed" and kept["reproduce"]["said"] == "failed"
    assert "usage limit" in kept["reproduce"]["reply_end"] and kept["reproduce"]["verdict"] == "unread"
    row = json.loads((got["root"] / "loop" / f"A-{TASK}-1.json").read_text("utf-8"))
    assert fr.said_of(got["root"], row, fr.REPRODUCE) is None and len(fr.waiting(got["root"], [TASK], fr.REPRODUCE)) == 1


def test_a_reviewer_whose_call_failed_gave_no_review_and_a_patch_that_will_not_apply_did_not_fail_there(tmp_path, monkeypatch):
    called = _review(tmp_path, _Critic(RuntimeError("quota")))
    assert called["said"] == "failed" and called["reproduce"]["failed_at"] == "reviewer" and "quota" in called["reproduce"]["error"]
    assert fr.no_review(called["reproduce"])
    _runs(monkeypatch, FAILED)
    stuck = _review(tmp_path, _Critic(REPLY), _Docker(applies=False))
    assert stuck["said"] == "failed" and "apply" in stuck["reproduce"]["error"] and not fr.no_review(stuck["reproduce"])


def test_two_reviews_in_a_row_with_no_review_pause_the_replay(tmp_path, monkeypatch, capsys):
    rows = (("A", "patch one", True), ("G1", "patch two", True), ("L9", "patch three", True))
    got = _main(tmp_path, monkeypatch, capsys, _Critic(LIMIT), [FAILED], argv=["--reproduce"], rows=rows)
    lines = [json.loads(line) for line in got["out"].splitlines()]
    assert [line.get("said") for line in lines[:2]] == ["failed", "failed"] and "paused" in lines[2] and len(lines) == 3
    assert len(got["saved"]) == 2


def test_one_review_with_no_review_does_not_pause_it(tmp_path, monkeypatch, capsys):
    rows = (("A", "patch one", True), ("G1", "patch two", True))
    got = _main(tmp_path, monkeypatch, capsys, _Critic(LIMIT, QUIET, QUIET), [FAILED], argv=["--reproduce"], rows=rows)
    assert len(got["saved"]) == 2 and "paused" not in got["out"]


def _codex_task(code, *replies):
    """``run.codex_task`` as Codex's companion ends: what it printed, quoting the pack, and how it exited."""
    given = iter(replies)

    def task(pack):
        proof = (pack / "issue.txt").read_text("utf-8").splitlines()[0]
        return subprocess.CompletedProcess(["node"], code, f"READ: {proof}\n{next(given)}".encode(), b"the reviewer crashed")

    return task


@pytest.mark.parametrize("code", [1, -9])
def test_a_reviewer_that_exits_non_zero_gave_no_review_whatever_it_printed(tmp_path, monkeypatch, code):
    monkeypatch.setattr(we, "codex_task", _codex_task(code, QUIET, QUIET))
    got = _review(tmp_path, None)
    first = got["reproduce"]
    assert got["said"] == "failed" and first["said"] == "failed" and first["failed_at"] == "reviewer" and fr.no_review(first)
    assert f"exited {code}" in first["error"] and "crashed" in first["error"]


def test_a_reviewer_that_exits_zero_is_read_as_before(tmp_path, monkeypatch):
    monkeypatch.setattr(we, "codex_task", _codex_task(0, QUIET, QUIET))
    got = _review(tmp_path, None)
    assert got["said"] == "none" and got["reproduce"]["said"] == "not reproduced" and not got["error"]


def test_the_frozen_reviews_own_call_still_reads_only_what_codex_printed(monkeypatch):
    monkeypatch.setattr(we, "codex_task", lambda pack: subprocess.CompletedProcess(["node"], 1, b"what it printed", b""))
    assert we.run_codex(Path("pack")) == "what it printed"


@pytest.mark.parametrize("step", [1, 2])
def test_a_reply_that_quotes_the_pack_but_gives_no_verdict_is_the_reviewers_failure(tmp_path, step):
    cut = "RULE: The issue requires"
    got = _review(tmp_path, _Critic(*((cut, QUIET) if step == 1 else (QUIET, cut))))
    failed = got["reproduce"] if step == 1 else got
    assert got["said"] == "failed" and failed["failed_at"] == "reviewer" and fr.no_review(failed) and cut in failed["reply_end"]


def test_two_replies_in_a_row_with_no_verdict_pause_the_replay(tmp_path, monkeypatch, capsys):
    rows = (("A", "patch one", True), ("G1", "patch two", True), ("L9", "patch three", True))
    critic = _Critic("RULE: The issue requires")
    got = _main(tmp_path, monkeypatch, capsys, critic, [FAILED], argv=["--reproduce"], rows=rows)
    assert len(critic.seen) == 2 and "paused" in json.loads(got["out"].splitlines()[-1])


@pytest.mark.parametrize("stop", ["", "futility reproduce first, block 1 (patches 1-40 by digest): cannot be reached"])
def test_the_futility_stop_is_read_before_each_review_so_a_resumed_replay_honours_it(tmp_path, monkeypatch, capsys, stop):
    import replay_compare as rc

    asked = []
    monkeypatch.setattr(rc, "predicted", lambda: {TASK, "x-9"})
    monkeypatch.setattr(rc, "futile", lambda root, tasks, name: asked.append((name, set(tasks))) or stop)
    critic = _Critic(QUIET)
    got = _main(tmp_path, monkeypatch, capsys, critic, [FAILED], argv=["--reproduce"])
    if stop:
        assert critic.seen == [] and got["saved"] == {} and json.loads(got["out"]) == {"stopped": stop}
    else:
        assert len(critic.seen) == 4 and len(got["saved"]) == 2
    assert asked and asked[0][1] == {TASK, "x-9"} and {name for name, _ in asked} == {rc.REPRODUCE}


def test_a_training_patch_has_no_futility_stop(tmp_path, monkeypatch, capsys):
    import replay_compare as rc

    asked = []
    monkeypatch.setattr(rc, "futile", lambda root, tasks, name: asked.append(name) or "futility: cannot be reached")
    critic = _Critic(QUIET)
    got = _main(tmp_path, monkeypatch, capsys, critic, [FAILED], argv=["--reproduce"])
    assert TASK not in rc.predicted() and asked == [] and len(critic.seen) == 4 and "stopped" not in got["out"]


def test_a_saved_review_is_skipped_and_a_failed_one_is_made_again(tmp_path):
    root = tmp_path / "round3"
    row = _row(root, "A")
    fr.keep(root, row, {"said": "none"}, fr.REPRODUCE, sha=fr._kept(root, row).stem)
    assert fr.waiting(root, [TASK], fr.REPRODUCE) == [] and fr.said_of(root, row, fr.REPRODUCE) == "none"
    assert len(fr.waiting(root, [TASK])) == 1                     # the frozen review's own record is apart
    fr.keep(root, row, {"said": "failed"}, fr.REPRODUCE, sha=fr._kept(root, row).stem)
    assert len(fr.waiting(root, [TASK], fr.REPRODUCE)) == 1 and fr.saved(root, row, fr.REPRODUCE) == {
        "task": TASK, "first_seen_in": "run/A1", "said": "failed"}
    assert (root / "loop" / fr.REPRODUCE / f"{c.sha256_file(root / row['run'] / 'final.patch')}.json").is_file()


def test_a_patch_whose_judge_failed_resumes_at_the_judge(tmp_path, monkeypatch):
    _runs(monkeypatch)
    first = {"said": "reproduced", "note": "\nTHE SAVED NOTE\n", "verdict": "defect"}
    critic = _Critic(QUIET)
    got = _review(tmp_path, critic, earlier={"said": "failed", "reproduce": first})
    assert len(critic.seen) == 1 and critic.seen[0]["critic_prompt.md"] == we.PATCH_CRITIC + "\nTHE SAVED NOTE\n"
    assert got["reproduce"] == first and got["said"] == "none"


def test_a_failed_first_step_is_made_again_on_resume(tmp_path, monkeypatch):
    _runs(monkeypatch)
    critic = _Critic(QUIET, QUIET)
    got = _review(tmp_path, critic, earlier={"said": "failed", "reproduce": {"said": "failed", "note": "x"}})
    assert len(critic.seen) == 2 and critic.seen[0]["critic_prompt.md"] == fr.REPRODUCE_FIRST and got["said"] == "none"


def test_everything_a_step_used_goes(tmp_path, monkeypatch):
    _runs(monkeypatch, FAILED, FAILED)
    docker = _Docker()
    _review(tmp_path, _Critic(REPLY, QUIET), docker)
    assert not list((tmp_path / "critic_packs").iterdir())
    assert sum(args[:2] == ("rm", "-f") for args in docker.calls) == 2


@pytest.mark.parametrize("name", ["reproduce_review", "reproduction", "no_review", "saved", "ended", "codex", "read"])
def test_the_module_says_what_each_new_step_does(name):
    assert getattr(fr, name).__doc__
