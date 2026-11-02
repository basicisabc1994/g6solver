# SPDX-License-Identifier: Apache-2.0
"""What the loop adopted reaches K1's reviewer as data, word for word, and a change that is no reviewer text is reported and never forced."""
import json
import threading

import pytest

import run as we
import stage3_adopted as adopted
import stage3_tick as tick

PROFILE = {"prompt": "Read it this way.", "skill_text": None, "routing": {"answer": "reviewer"}, "retries": 0}


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setattr(we, "HARNESS", tmp_path)
    return tmp_path


def test_k2_adds_to_the_reviewer_and_k1_and_k0_stay_as_they_ran(harness):
    (harness / "K2.json").write_text(json.dumps({"critic": "Trace the caller."}), encoding="utf-8")
    assert "4. Trace the caller.\n</task>" in we.critic_text("K2")
    assert we.critic_text("K1") == we.critic_text("K0") == we.PATCH_CRITIC


def test_k2_with_no_committed_text_cannot_run(harness):
    with pytest.raises(FileNotFoundError):
        we.trained("K2")


def test_k2_may_change_only_what_training_may(harness):
    (harness / "K2.json").write_text(json.dumps({"judge": "x"}), encoding="utf-8")
    with pytest.raises(ValueError, match="judge"):
        we.trained("K2")


def test_k2_runs_on_stage_2_bugs_as_k1_does():
    bug = we.load_tasks(we.TASKS_UNSEEN)[0].id
    assert we.r3_loop_key((we.R3_MODELS[1], bug, "K2", 1)) is True
    assert we.r3_loop_key((we.R3_MODELS[1], bug, "K1", 1)) is True


def test_the_reviewer_keeps_its_reply_form_with_the_adopted_text(harness):
    (harness / "K2.json").write_text(json.dumps({"critic": adopted.text_of(PROFILE, "something else")}), encoding="utf-8")
    text = we.critic_text("K2")
    assert "VERDICT: defect or none" in text and "Read-only" in text and text.index("Read it this way.") < text.index("</task>")


def test_the_text_is_the_adopted_prompt_word_for_word_with_its_skill():
    said = adopted.text_of({**PROFILE, "skill_text": "Then check the sibling."}, "something else")
    assert "Read it this way." in said and "Then check the sibling." in said


@pytest.mark.parametrize("profile", [{**PROFILE, "prompt": "K1's own"}, {**PROFILE, "prompt": "K1's own", "retries": 1}])
def test_a_change_that_is_no_reviewer_text_has_nothing_to_carry(profile):
    assert adopted.text_of(profile, "K1's own") is None


def test_the_watcher_asks_the_folder_to_stop_at_the_limit(tmp_path):
    done = threading.Event()
    limits = {"rateLimits": {"primary": {"usedPercent": tick.CODEX_STOP}}}
    tick.EVERY_S, keep = 0.01, tick.EVERY_S
    try:
        tick.watch(tmp_path, done, read=lambda: limits)
    finally:
        tick.EVERY_S = keep
    assert (tmp_path / "STOP").is_file()


def test_the_watcher_leaves_the_folder_alone_under_the_limit(tmp_path):
    done = threading.Event()
    limits = {"rateLimits": {"primary": {"usedPercent": tick.CODEX_STOP - 1}}}

    def read():
        done.set()
        return limits

    tick.EVERY_S, keep = 0.01, tick.EVERY_S
    try:
        tick.watch(tmp_path, done, read=read)
    finally:
        tick.EVERY_S = keep
    assert not (tmp_path / "STOP").exists()


def test_a_figure_that_cannot_be_read_stops_nothing(tmp_path):
    done = threading.Event()

    def read():
        done.set()
        raise OSError("no answer")

    tick.EVERY_S, keep = 0.01, tick.EVERY_S
    try:
        tick.watch(tmp_path, done, read=read)
    finally:
        tick.EVERY_S = keep
    assert not (tmp_path / "STOP").exists()
