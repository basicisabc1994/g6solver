# SPDX-License-Identifier: Apache-2.0
"""A current refusal and a retained ending have one public interpretation."""
import pytest

from g6.core import cloud_consent as cloud
from g6.harness import completion_hook as hook, completion_loop as loop
from g6.mcp import review_loop
from g6.workspace._envelope import OUTSIDE_ROOT
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run


@pytest.mark.parametrize("retained_code", ["", loop.NO_CLEAN_REVIEW, cloud.REQUIRED])
def test_current_refusal_outlives_every_kind_of_retained_ending(disk_run, monkeypatch, retained_code):
    repo, base, session = disk_run
    with loop._writing(session):
        loop._finish(session, {"attempted": 2}, "earlier report", retained_code, "clean")
    monkeypatch.setattr(loop, "advance", lambda *a, **k:
                        loop.Step(loop.STOP, "current output refused", OUTSIDE_ROOT, ending="untested"))
    reply = review_loop.step(str(repo), "task", "disk-review2", base)
    assert reply["code"] == OUTSIDE_ROOT
    assert reply["ok"] is False and reply["ending"] == "untested"
    assert reply["requests_sent"] == 2 and "current output refused" in reply["error"]
    assert "earlier report" not in reply["report"] and "g6_cloud_grant" not in reply["report"]


def test_mcp_repeat_explains_how_to_continue_over_mcp(disk_run):
    repo, base, session = disk_run
    with loop._writing(session):
        loop._finish(session, {"attempted": 0}, "retained report", loop.NO_CLEAN_REVIEW, "untested")
    reply = review_loop.step(str(repo), "task", "disk-review2", base)
    assert "new session" in reply["note"].lower()
    assert "next prompt" not in reply["note"].lower()


def test_hook_retains_current_refusal_when_retained_ending_is_clean(tmp_path, monkeypatch):
    from tests.harness.test_completion_amendment_reply import hook_run

    step = loop.Step(loop.STOP, "current output refused", OUTSIDE_ROOT, ending="untested")
    session, event = hook_run(tmp_path, monkeypatch, step)
    loop._finish(session, {"attempted": 2}, "earlier report", ending="clean")
    reply = hook.on_stop(event)
    assert OUTSIDE_ROOT in reply["systemMessage"] and "current output refused" in reply["systemMessage"]
    assert "earlier report" not in reply["systemMessage"]


def test_a_busy_reply_reader_cannot_replace_the_current_refusal(disk_run, monkeypatch):
    from g6.workspace._envelope import BUSY, WorkspaceRefused

    repo, base, session = disk_run
    monkeypatch.setattr(loop, 'advance', lambda *a, **k:
                        loop.Step(loop.STOP, 'output refused', OUTSIDE_ROOT, ending='untested'))

    def busy(*args):
        raise WorkspaceRefused(BUSY, 'publication in progress')

    monkeypatch.setattr(loop, 'reply_state', busy)
    reply = review_loop.step(str(repo), 'task', 'disk-review2', base)
    assert reply['code'] == OUTSIDE_ROOT and reply['ok'] is False
    assert 'requests_sent' not in reply and 'nothing was spent' not in reply['error']


def test_an_unreadable_ending_cannot_replace_the_current_refusal_reason(disk_run, monkeypatch):
    repo, base, session = disk_run
    session.mkdir(parents=True)
    (session / 'state.json').write_bytes(b'{}')
    monkeypatch.setattr(loop, 'advance', lambda *a, **k:
                        loop.Step(loop.STOP, 'output refused', OUTSIDE_ROOT, ending='untested'))
    reply = review_loop.step(str(repo), 'task', 'disk-review2', base)
    assert reply['code'] == OUTSIDE_ROOT and 'output refused' in reply['error']
    assert 'g6_cloud_grant' not in reply['report']
