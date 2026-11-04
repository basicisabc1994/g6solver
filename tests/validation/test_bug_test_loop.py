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
    docker.sh = lambda agent, script, *a, **kw: type("Done", (), {"stdout": b"", "stderr": b"", "returncode": 125})()
    ctx = type("Ctx", (), {"docker": docker, "arm": "V4"})()
    got = we.reviews_with_bug_test(ctx, type("T", (), {"repo": "/w/r"})(), _Run(), "agent", "b", [], "k", None, 1.0, 0.0, bug())
    assert got["status"] == "inconclusive" and "failed to run" in got["reason"]


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
