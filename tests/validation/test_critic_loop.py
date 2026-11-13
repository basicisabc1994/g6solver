# SPDX-License-Identifier: Apache-2.0
"""Arm K (PLAN.md, 2026-11-01): plain Claude Code, reviewed again by another model family after each repair, until nothing reproduces."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation" / "worked_examples"))

import run as we  # noqa: E402

SONNET = we.R3_MODELS[1]
FAILS = we.ProbeRun(exit=1, results={"t": "failed"}, texts={"t": "AssertionError"})
PASSES = we.ProbeRun(exit=0, results={"t": "passed"}, texts={})
BROKEN = we.ProbeRun(exit=2, results={}, texts={})
QUIET = {"verdict": "none", "test": "", "rule": "", "why": ""}


def defect(n=1):
    return {"verdict": "defect", "rule": f"rule {n}", "why": f"reason {n}", "test": f"def test_{n}(): assert 0"}


class _Run:
    def __init__(self):
        self.saved = []

    def write_json(self, name, body):
        self.saved.append(name)


class _Docker:
    """The tree moves on with each repair unless ``moves`` is false."""

    def __init__(self, moves):
        self.moves, self.tree, self.scripts = moves, 0, []

    def sh(self, agent, script, *args, **kw):
        self.scripts.append(script)
        return type("Done", (), {"stdout": f"tree {self.tree}".encode(), "stderr": b"", "returncode": 0})()


def _loop(monkeypatch, reviews, sets, *, moves=True):
    """``reviews``: what each review says. ``sets``: what the reviewers' tests do on the patch, for each review that wrote one."""
    docker, asked, seen = _Docker(moves), [], []
    ctx = type("Ctx", (), {"docker": docker})()
    left = list(sets)

    def critique(n):
        seen.append(n)
        return reviews[n - 1]

    def session(ctx_, run, agent, args, key, tally, text, name, **kw):
        asked.append((text, name))
        docker.tree += 1 if moves else 0

    monkeypatch.setattr(we, "run_set", lambda ctx_, task, agent, where: left.pop(0))
    monkeypatch.setattr(we, "run_session", session)
    monkeypatch.setattr(we, "scout_again", lambda args: args)
    task = type("T", (), {"repo": "/w/r"})()
    got = we.reviews_until_clean(ctx, task, _Run(), "agent", "base1", [], "key", None, 1.0, 0.0, critique)
    return got, asked, seen, docker


def test_a_review_that_finds_nothing_at_once_ends_the_run_with_no_repair(monkeypatch):
    got, asked, seen, _ = _loop(monkeypatch, [QUIET], [])
    assert asked == [] and seen == [1] and got["rounds"] == 1 and got["status"] == "no_counterexample_in_domain"


def test_the_patch_is_reviewed_again_after_each_repair_until_a_review_finds_nothing(monkeypatch):
    """The point of the arm: on black-5634 one review saw one layer of the defect, and the repair stopped there."""
    got, asked, seen, _ = _loop(monkeypatch, [defect(1), defect(2), QUIET], [FAILS, FAILS])
    assert seen == [1, 2, 3] and [a[1] for a in asked] == ["repair_stream_1.jsonl", "repair_stream_2.jsonl"]
    assert "rule 1" in asked[0][0] and "rule 2" in asked[1][0] and "rule 1" not in asked[1][0]
    assert got["status"] == "no_counterexample_in_domain" and got["rounds"] == 3


def test_each_review_reads_what_the_last_repair_left_and_each_patch_is_kept_for_the_check(monkeypatch):
    _, _, _, docker = _loop(monkeypatch, [defect(1), QUIET], [FAILS])
    kept = [s for s in docker.scripts if f"{we.WE}/rounds/round-" in s]
    assert [f"round-{n}.patch" in kept[n - 1] for n in (1, 2)] == [True, True] and all("base1" in s for s in kept)


def test_the_repairs_stop_at_the_cap_and_a_defect_left_open_says_so(monkeypatch):
    reviews = [defect(n) for n in range(1, we.MAX_REPAIRS + 2)]
    got, asked, seen, _ = _loop(monkeypatch, reviews, [FAILS] * len(reviews))
    assert len(asked) == we.MAX_REPAIRS and len(seen) == we.MAX_REPAIRS + 1
    assert got["status"] == "inconclusive" and got["failing"]


def test_an_agent_that_leaves_the_code_as_it_was_has_decided(monkeypatch):
    """It may reject a finding with a reason, as the reviewer can be wrong. Asking again would only spend money."""
    got, asked, seen, _ = _loop(monkeypatch, [defect(1), defect(2)], [FAILS], moves=False)
    assert len(asked) == 1 and seen == [1] and "left the code" in got["reason"] and got["status"] == "inconclusive"


def test_a_test_that_passes_on_the_patch_is_no_finding_and_sends_nothing_back(monkeypatch):
    got, asked, _, _ = _loop(monkeypatch, [defect(1)], [PASSES])
    assert asked == [] and got["status"] == "no_counterexample_in_domain"


def test_a_test_that_will_not_run_says_nothing_of_the_patch(monkeypatch):
    got, asked, _, _ = _loop(monkeypatch, [defect(1)], [BROKEN])
    assert asked == [] and got["status"] == "no_counterexample_in_domain"


def test_a_review_with_no_test_sends_nothing_back(monkeypatch):
    got, asked, _, _ = _loop(monkeypatch, [{**defect(1), "test": ""}], [])
    assert asked == [] and got["rounds"] == 1


def test_a_review_that_failed_ends_the_loop_and_is_recorded_as_no_clean_review(monkeypatch):
    got, asked, _, _ = _loop(monkeypatch, [{"verdict": "failed", "test": "", "error": "timeout"}], [])
    assert asked == [] and got["status"] == "inconclusive" and got["critic"]["verdicts"] == ["failed"]


@pytest.mark.parametrize("arm", ["K1"])
def test_arm_k_is_plain_claude_code_with_the_issues_discussion_and_no_trained_text(arm):
    task = we.load_tasks()[1]
    assert we.prompt(task, "t", "body", arm) == we.prompt(task, "t", "body", "A") and we.gets_thread(arm) and we.trained(arm) == {}


def test_arm_k_runs_on_training_held_out_and_unseen_bugs_with_their_repeats():
    assert we.r3_loop_key((SONNET, "black-5634", "K1", 2)) and not we.r3_loop_key((SONNET, "black-5634", "K1", 3))
    assert we.r3_loop_key((SONNET, we.R3_HELD[0], "K1", 5)) and not we.r3_loop_key((SONNET, we.R3_HELD[0], "K1", 6))
    assert we.r3_loop_key((SONNET, "newrepo-1", "K1", 2)) and not we.r3_loop_key((SONNET, "newrepo-1", "K1", 3))
    assert not we.r3_loop_key((we.R3_MODELS[0], "black-5634", "K1", 1))


def test_a_later_review_saves_its_reply_and_test_beside_the_earlier_ones(tmp_path):
    """The archive refuses to write a file twice, and an earlier reviewer's test must stay to catch a repair that undoes it."""
    assert we.critic_files(1) == ("critic_reply.txt", "test_critic.py")
    assert we.critic_files(2) == ("critic_reply_2.txt", "test_critic_2.py")
