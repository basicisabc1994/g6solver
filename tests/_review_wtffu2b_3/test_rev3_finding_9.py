# SPDX-License-Identifier: Apache-2.0
"""Review 3, finding 9: a new loop cannot be armed over a run folder whose state cannot be read.

``_again`` saves over what it loaded from the next run's folder. ``_save`` now refuses when the stored state is
unreadable (a publication cut between its two replacements, or one whose receipt could not be replaced), and nothing
advances the session's run number, so every later prompt of the session raises the same error (worded as a sharing
refusal that names ``g6_cloud_grant``) and the Stop hook goes on saying "Your next prompt starts a new loop".
"""
from g6.harness import completion_hook as hook, completion_loop as loop
from tests._review_wtffu2b_3.rev3_world import ended_clean, hook_session
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run


def _prompt(name, repo):
    try:
        return hook.on_prompt({"session_id": name, "cwd": str(repo), "prompt": "and now halve it"})
    except ValueError as exc:
        return exc


def test_control_a_prompt_after_an_ended_run_arms_the_next(disk_run, monkeypatch):
    repo, base, _ = disk_run
    event, run = hook_session(monkeypatch, repo, "rev3-f9-control")
    ended_clean(repo, base, run, owed=False)
    assert isinstance(_prompt("rev3-f9-control", repo), dict)
    assert hook._task(hook.folder("rev3-f9-control")).get("run") == 1


def test_a_prompt_arms_a_new_loop_over_a_run_folder_it_cannot_read(disk_run, monkeypatch):
    repo, base, _ = disk_run
    event, run = hook_session(monkeypatch, repo, "rev3-f9")
    ended_clean(repo, base, run, owed=False)
    head = loop.repo_tests._git(repo, "rev-parse", "--verify", "-q", "HEAD").decode().strip()
    torn = hook.current("rev3-f9", {"base": head, "run": 1})
    torn.mkdir(parents=True)
    (torn / "state.json").write_bytes(b'{"attempted": 0}')     # content replaced, its receipt never written
    replies = [_prompt("rev3-f9", repo) for _ in range(2)]
    assert all(isinstance(reply, dict) for reply in replies), repr(replies)      # each prompt raises at this commit
    assert hook._task(hook.folder("rev3-f9")).get("run", 0) >= 1
