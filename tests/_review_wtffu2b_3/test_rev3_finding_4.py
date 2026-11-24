# SPDX-License-Identifier: Apache-2.0
"""Review 3, finding 4: a repository refusal raised before anything was sent or tested ends the session's loop for good.

A git command that timed out at the start of a step is now saved as the run's ending. Over MCP the same refusal one call
earlier (in ``_asked``) leaves the session free to try again, and so did the step itself before this change; now every
repeat answers "it has already ended for this task, and its report stands. Your next prompt starts a new loop", words for
the Stop hook that name no way on for an MCP client.
"""
from g6.harness import completion_loop as loop
from g6.mcp import review_loop
from tests._review_wtffu2b_3.rev3_world import TASK, quiet_scouts
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run

GIT = "G6_E_GIT_COMMAND_FAILED"


def _git_times_out_once(monkeypatch, on_call):
    real, calls = loop.repo_tests.change, []

    def change(root, base):
        calls.append(1)
        if len(calls) == on_call:
            raise loop.repo_tests.RepoRefused(GIT, "git diff failed (TimeoutExpired)")
        return real(root, base)

    monkeypatch.setattr(loop.repo_tests, "change", change)


def test_control_a_git_failure_while_binding_the_session_can_be_tried_again(disk_run, monkeypatch):
    repo, base, session = disk_run
    quiet_scouts(monkeypatch)
    _git_times_out_once(monkeypatch, on_call=1)            # in review_loop._asked
    first = review_loop.step(str(repo), TASK, "disk-review2", base)
    second = review_loop.step(str(repo), TASK, "disk-review2", base)
    assert first["code"] == GIT and "action" not in first
    assert second["ok"] is True and "No probe set ran." in second["report"]


def test_a_git_failure_before_anything_was_sent_does_not_end_the_session(disk_run, monkeypatch):
    repo, base, session = disk_run
    quiet_scouts(monkeypatch)
    _git_times_out_once(monkeypatch, on_call=2)            # the same refusal, at the start of the step
    first = review_loop.step(str(repo), TASK, "disk-review2", base)
    assert first["code"] == GIT and first.get("requests_sent", 0) == 0
    second = review_loop.step(str(repo), TASK, "disk-review2", base)
    assert second.get("note") != loop.ALREADY_ENDED, second.get("error")
    assert "No probe set ran." in second.get("report", "")
