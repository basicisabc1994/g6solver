# SPDX-License-Identifier: Apache-2.0
"""Arms V3 and V4 (PLAN.md, 2026-11-04): arm K's loop, with the reviewer's test written once from the issue and the parent before
the agent starts, under replay 3's or replay 4's instruction, run on the untouched tree, then on the agent's tree after each
session, its failures sent back for repair as arm K sends its reviewer's."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import replay3 as r3  # noqa: E402
import replay4 as r4  # noqa: E402
import run as we  # noqa: E402

SONNET = we.R3_MODELS[1]
AT_PARENT = we.ProbeRun(1, {"t::test_example": "failed", "t::test_variant": "failed", "t::test_kept": "passed"}, {}, "")
NOTHING_AT_PARENT = we.ProbeRun(0, {"t::test_example": "passed"}, {}, "")
VARIANT_FAILS = we.ProbeRun(1, {"t::test_example": "passed", "t::test_variant": "failed", "t::test_kept": "passed"},
                            {"t::test_variant": "AssertionError: symptom"}, "")
ALL_HOLD = we.ProbeRun(0, {"t::test_example": "passed", "t::test_variant": "passed", "t::test_kept": "passed"}, {}, "")
WONT_RUN = we.ProbeRun(2, {}, {}, "ImportError")
TEST = "def test_variant():\n    assert 0\n"


def bug(at_parent=AT_PARENT, **kw):
    return {"verdict": "defect", "rule": "the symptom", "why": "the variants", "test": TEST, "error": "", "at_parent": at_parent,
            "instruction": r4.SYMPTOM.digest, "arm": "V4", **kw}


class _Run:
    def __init__(self):
        self.saved = []

    def write_json(self, name, body):
        self.saved.append(name)


class _Docker:
    def __init__(self, moves):
        self.moves, self.tree, self.scripts = moves, 0, []

    def sh(self, agent, script, *args, **kw):
        self.scripts.append(script)
        return type("Done", (), {"stdout": f"tree {self.tree}".encode(), "stderr": b"", "returncode": 0})()


def _loop(monkeypatch, found, sets, *, moves=True):
    docker, asked, written = _Docker(moves), [], []
    ctx = type("Ctx", (), {"docker": docker, "arm": "V4"})()
    left = list(sets)

    def session(ctx_, run, agent, args, key, tally, text, name, **kw):
        asked.append((text, name))
        docker.tree += 1 if moves else 0

    monkeypatch.setattr(we, "run_set", lambda ctx_, task, agent, where: (written.append(("run", where)), left.pop(0))[1])
    monkeypatch.setattr(we, "write_in", lambda d, ctr, dest, text, **kw: written.append((dest, text)))
    monkeypatch.setattr(we, "run_session", session)
    monkeypatch.setattr(we, "scout_again", lambda args: args)
    task = type("T", (), {"repo": "/w/r"})()
    got = we.reviews_with_bug_test(ctx, task, _Run(), "agent", "base1", [], "key", None, 1.0, 0.0, found)
    return got, asked, written


@pytest.mark.parametrize("arm", ["V3", "V4"])
def test_the_bug_test_arms_are_plain_claude_code_with_the_discussion_and_run_where_arm_k_runs(arm):
    task = we.load_tasks()[1]
    assert we.prompt(task, "t", "body", arm) == we.prompt(task, "t", "body", "A") and we.gets_thread(arm) and we.trained(arm) == {}
    unseen = we.load_tasks(we.TASKS_UNSEEN)[0].id
    assert we.r3_loop_key((SONNET, unseen, arm, 2)) and not we.r3_loop_key((SONNET, unseen, arm, 3))
    assert we.r3_loop_key((SONNET, "black-5634", arm, 1)) and we.bug_led(arm) and not we.bug_led("K1")


def test_stage_4s_bugs_load_are_fresh_and_new_and_take_the_unseen_bugs_arms_twice():
    tasks = we.load_tasks(we.TASKS_STAGE4)
    earlier = {t.id for path in (we.TASKS, we.TASKS_FRESH, we.TASKS_UNSEEN, we.TASKS_DEV) for t in we.load_tasks(path)}
    assert tasks and not {t.id for t in tasks} & earlier and len({t.id for t in tasks}) == len(tasks)
    assert all(we.is_fresh(t.id) and we.task_by_id(t.id) == t for t in tasks)
    one = tasks[0].id
    assert we.r3_loop_key((SONNET, one, "A", 2)) and we.r3_loop_key((SONNET, one, "V4", 1))
    assert not we.r3_loop_key((SONNET, one, "A", 3)) and not we.r3_loop_key((SONNET, one, "S", 1))


def test_each_bug_test_arm_asks_under_its_replays_own_instruction():
    assert we.bug_instruction("V3") == r3.rule() and we.bug_instruction("V4") == r4.SYMPTOM
    with pytest.raises(KeyError):
        we.bug_instruction("V9")


def test_the_bug_test_runs_on_the_parent_in_a_container_of_its_own_and_never_in_the_agents(monkeypatch):
    """Review v1 findings 2 and 3: the agent's container never holds the test before its first check, and its tree is never
    touched by the parent run. The parent run is replay 3's (``final_review.container`` and ``parent_run``, docker checked)."""
    import final_review as fr
    calls, removed = [], []
    monkeypatch.setattr(we, "write_in", lambda *a, **kw: pytest.fail("nothing is written in the agent's container"))
    monkeypatch.setattr(we, "run_set", lambda *a, **kw: pytest.fail("nothing runs in the agent's container"))
    monkeypatch.setattr(fr, "container", lambda ctx, task, name: calls.append(("container", type(ctx.docker).__name__)))
    monkeypatch.setattr(fr, "parent_run", lambda ctx, task, name, test, where, file: (calls.append((where, file, test)), AT_PARENT)[1])
    from types import SimpleNamespace
    ctx = SimpleNamespace(docker=lambda *a, **kw: removed.append(a))
    got = we.parent_check(ctx, type("T", (), {"repo": "/w/r"})(), {"test": TEST}, r4.SYMPTOM)
    assert calls == [("container", "Checked"), (r4.SYMPTOM.at, r4.SYMPTOM.file, TEST)] and got is AT_PARENT
    assert removed and removed[0][:2] == ("rm", "-f")


def test_the_bug_test_is_asked_as_replay_4_asks_it_with_no_patch(monkeypatch):
    """Review v1 finding 1: the reviewer is the measured one (``final_review._step``), which reads Codex's exit."""
    import final_review as fr
    asked = []
    monkeypatch.setattr(fr, "_step", lambda ctx, task, patch, title, text, prompt, where, file, *, candidate, run:
                        (asked.append((prompt, where, file, candidate, run)), {**bug(), "error": ""}, None, None)[1:])
    got = we.ask_bug_test(type("Ctx", (), {})(), None, "title", "text", r4.SYMPTOM)
    assert asked == [(r4.SYMPTOM_FIRST, r4.SYMPTOM.at, r4.SYMPTOM.file, False, False)] and got["test"] == TEST


@pytest.mark.parametrize("at_parent", [we.ProbeRun(None, {}, {}, ""), None])
def test_a_parent_run_with_no_result_is_a_failed_review(monkeypatch, at_parent):
    got, asked, _ = _loop(monkeypatch, bug(at_parent=at_parent), [])
    assert asked == [] and got["status"] == "inconclusive"


def test_a_check_whose_docker_step_fails_or_gives_no_exit_is_no_clean_review(monkeypatch):
    """Review v1 finding 4."""
    got, asked, _ = _loop(monkeypatch, bug(), [we.ProbeRun(None, {}, {}, "")])
    assert asked == [] and got["status"] == "inconclusive"

    def broken(ctx, task, agent, where):
        ctx.docker.sh(agent, "probe")
        return ALL_HOLD
    monkeypatch.setattr(we, "write_in", lambda *a, **kw: None)
    monkeypatch.setattr(we, "run_set", broken)
    docker = _Docker(True)
    status = {"code": 0}

    def sh(agent, script, *a, **kw):      # the probes' docker step fails; the round's capture before it succeeds
        return type("Done", (), {"stdout": b"", "stderr": b"", "returncode": 125 if script == "probe" else status["code"]})()

    docker.sh = sh
    ctx = type("Ctx", (), {"docker": docker, "arm": "V4"})()
    got = we.reviews_with_bug_test(ctx, type("T", (), {"repo": "/w/r"})(), _Run(), "agent", "b", [], "k", None, 1.0, 0.0, bug())
    assert got["status"] == "inconclusive" and "failed to run" in got["reason"]
    status["code"] = 125                  # study review 3: a round's capture that failed fails its step, never a missing patch
    with pytest.raises(RuntimeError, match="capture of round 1"):
        we.reviews_with_bug_test(ctx, type("T", (), {"repo": "/w/r"})(), _Run(), "agent", "b", [], "k", None, 1.0, 0.0, bug())


def test_a_test_that_reproduces_nothing_on_the_parent_sends_nothing_back(monkeypatch):
    got, asked, _ = _loop(monkeypatch, bug(at_parent=NOTHING_AT_PARENT), [])
    assert asked == [] and got["rounds"] == 0 and "reproduces nothing on the parent" in got["reason"]
    assert got["status"] == "no_counterexample_in_domain"


@pytest.mark.parametrize(("found", "status"), [({"test": "", "verdict": "none", "error": ""}, "no_counterexample_in_domain"),
                                               ({"test": "", "verdict": "failed", "error": "timeout"}, "inconclusive")])
def test_no_test_sends_nothing_back_and_a_failed_reviewer_is_no_clean_review(monkeypatch, found, status):
    got, asked, _ = _loop(monkeypatch, {**bug(), **found, "at_parent": None}, [])
    assert asked == [] and got["status"] == status and got["rounds"] == 0


def test_a_patch_the_bug_test_holds_on_ends_with_no_repair(monkeypatch):
    got, asked, _ = _loop(monkeypatch, bug(), [ALL_HOLD])
    assert asked == [] and got["rounds"] == 1 and got["status"] == "no_counterexample_in_domain"


def test_a_variant_still_failing_goes_back_with_the_test_and_the_failing_case_then_is_checked_again(monkeypatch):
    got, asked, written = _loop(monkeypatch, bug(), [VARIANT_FAILS, ALL_HOLD])
    assert len(asked) == 1 and asked[0][1] == "repair_stream_1.jsonl"
    note = asked[0][0]
    assert "the symptom" in note and TEST.strip() in note and "test_variant" in note and "not your patch" in note
    assert got["status"] == "no_counterexample_in_domain" and got["rounds"] == 2
    tests = [w for w in written if w[0] == f"{r4.SYMPTOM.at}/{r4.SYMPTOM.file}"]
    assert len(tests) == 2 and all(text == TEST + "\n" for _, text in tests)       # a fresh copy before each check


def test_the_repairs_stop_at_the_cap_with_the_defect_left_open(monkeypatch):
    got, asked, _ = _loop(monkeypatch, bug(), [VARIANT_FAILS] * (we.MAX_REPAIRS + 1))
    assert len(asked) == we.MAX_REPAIRS and got["status"] == "inconclusive" and got["failing"] == ["t::test_variant"]


def test_an_agent_that_leaves_the_code_as_it_was_has_decided(monkeypatch):
    got, asked, _ = _loop(monkeypatch, bug(), [VARIANT_FAILS, VARIANT_FAILS], moves=False)
    assert len(asked) == 1 and "left the code" in got["reason"] and got["status"] == "inconclusive"


def test_a_test_that_will_not_run_on_the_patch_says_nothing_of_it(monkeypatch):
    got, asked, _ = _loop(monkeypatch, bug(), [WONT_RUN])
    assert asked == [] and got["status"] == "no_counterexample_in_domain" and "would not run" in got["reason"]


def test_the_bug_tests_folder_holds_only_what_the_reviewer_wrote_at_each_check(monkeypatch, tmp_path):
    """Review v2 finding 3: a check copies the test's whole folder, so a file the agent leaves there (a ``conftest.py`` that
    replaces each case) would change what the cases run. Before each check the folder is removed as root, then the reviewer's
    test written alone. Real pytest behind a fake docker; the fake only translates the folder's removal, ``run_set``'s copy,
    its pytest run and the tree's digest."""
    import dataclasses
    import hashlib
    import os
    import shutil
    import subprocess
    from types import SimpleNamespace

    source, copied, repo = tmp_path / "symptom", tmp_path / "held", tmp_path / "repo"
    repo.mkdir()
    module = repo / "buggy.py"
    module.write_text("def answer():\n    return 0\n", encoding="utf-8")
    test = (f"import sys\nsys.path.insert(0, {str(repo)!r})\nfrom buggy import answer\n"
            "def test_bug():\n    assert answer() == 1\n")
    ins = dataclasses.replace(r4.SYMPTOM, at=source.as_posix())
    xml, seen, sessions = tmp_path / "results.xml", [], []

    class Docker:
        def sh(self, ctr, script, **kw):
            output = b""
            if "cp -r " in script:                 # run_set's copy, which also begins with a removal
                shutil.rmtree(copied, ignore_errors=True)
                shutil.copytree(source, copied)
                seen.append(sorted(p.name for p in copied.iterdir()))
            elif script.startswith("rm -rf ") and ins.at in script:
                shutil.rmtree(source, ignore_errors=True)
            elif "--junitxml=" in script:
                env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1"}
                done = subprocess.run([sys.executable, "-m", "pytest", str(copied / ins.file), "-q", "-p", "no:cacheprovider",
                                       f"--rootdir={copied}", f"--confcutdir={copied}", f"--junitxml={xml}",
                                       "-o", "junit_family=xunit2"], cwd=repo, env=env, capture_output=True, timeout=60)
                output = done.stdout + f"\nexit={done.returncode}\n{we.JUNIT_MARK}\n".encode() + xml.read_bytes()
            elif "sha256sum " in script:
                output = hashlib.sha256(module.read_bytes()).hexdigest().encode()
            elif script.startswith(f"rm -rf {we.HELD_COPY}"):
                shutil.rmtree(copied, ignore_errors=True)
            else:
                raise AssertionError(f"unexpected step: {script[:80]}")
            return SimpleNamespace(returncode=0, stdout=output, stderr=b"")

    def write_in(docker, ctr, dest, body, **kw):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_text(body, encoding="utf-8")

    def session(*a, **kw):                    # the first repair plants a conftest.py beside the test and fixes nothing
        sessions.append(1)
        if len(sessions) == 1:
            (source / "conftest.py").write_text("def pytest_collection_modifyitems(items):\n    for item in items:\n"
                                                "        item.obj = lambda: None\n", encoding="utf-8")
            module.write_text("def answer():\n    return 0\n# touched\n", encoding="utf-8")

    monkeypatch.setattr(we, "HELD_COPY", copied.as_posix())
    monkeypatch.setattr(we, "bug_instruction", lambda arm: ins)
    monkeypatch.setattr(we, "write_in", write_in)
    monkeypatch.setattr(we, "keep_first_patch", lambda *a: None)
    monkeypatch.setattr(we, "run_session", session)
    monkeypatch.setattr(we, "scout_again", lambda args: args)
    ctx, task = SimpleNamespace(docker=Docker(), arm="V4"), SimpleNamespace(repo=repo.as_posix())
    got = we.reviews_with_bug_test(ctx, task, SimpleNamespace(write_json=lambda *a: None), "agent", "base", [], "", None, 1.0, 0.0,
                                   bug(test=test))
    assert seen[:2] == [[ins.file], [ins.file]], seen
    assert got["status"] == "inconclusive" and got["failing"], got


def test_the_bug_test_before_the_agent_comes_out_of_the_runs_one_allowance(monkeypatch, tmp_path):
    """Review v2 finding 2: arm V's pre-agent review is on the run's clock, as arm K's reviews are, so the first session's
    deadline counts from before it, as the repairs' allowance and the recorded time do."""
    import io
    import json
    from types import SimpleNamespace

    task, clock, deadlines = we.load_tasks()[0], [100.0], []

    class Docker:
        def __call__(self, *a, **kw):
            if a[:1] == ("cp",) and str(a[1]).endswith("/final.patch"):     # the final patch's copy leaves its file, as docker's 0 says
                Path(a[2]).write_bytes(b"")
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

    def bug_test(*a):
        clock[0] += 90.0
        return {"verdict": "none", "seconds": 90.0}

    def read_stream(*a, deadline, **kw):
        deadlines.append(deadline)
        return "timeout"

    monkeypatch.setattr(we.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(we, "key_check", lambda *a, **kw: {"ok": True})
    monkeypatch.setattr(we, "start_net", lambda *a: ("net", "proxy"))
    monkeypatch.setattr(we, "read_thread", lambda *a: "")
    monkeypatch.setattr(we, "bug_test", bug_test)
    monkeypatch.setattr(we, "read_stream", read_stream)
    ctx = we.Context(archive=we.c.Archive(tmp_path / "archive"), docker=Docker(), key_reader=lambda: "dummy",
                     issue_reader=lambda task: ("title", "issue"), tree_reader=lambda task: "tree", g6={"commit": "0" * 40},
                     variant=we.ROUND3, arm="V4", environ={})
    record = we._run_task(ctx, task, dry=False, cap=10.0, stop=10.5)
    assert record["status"] == "timeout" and deadlines == [100.0 + we.c.WALL_CLOCK_S] and record["seconds"] == 90.0, record