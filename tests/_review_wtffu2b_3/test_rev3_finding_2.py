# SPDX-License-Identifier: Apache-2.0
"""Review 3, finding 2: a coded stop whose refusal never reached retained state loses its reason.

Review 2's finding 4 class (a reply worded from something other than what happened), on the path the repair opened. The
MCP reply words such a stop as ``NOTHING``: "No Python changed ... nothing was tested and nothing was spent", beside a
``requests_sent`` of 2. The Stop hook words the same state as a sharing refusal and names ``g6_cloud_grant``. The author's
own hook case (a consent stop over a run that is not settled) reads "up to 2 request(s)" in the hook and "nothing was
spent" over MCP.
"""
from g6.core import cloud_consent as cloud
from g6.core.model_calls import note_model_call
from g6.harness import completion_hook as hook, completion_loop as loop
from g6.mcp import review_loop
from g6.workspace._envelope import OUTSIDE_ROOT, WorkspaceRefused
from tests._review_wtffu2b_3.rev3_world import TASK, hard_link, hook_session, quiet_scouts
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run


def _in_flight(run, sent):
    """A run with ``sent`` requests counted and no ending."""
    with loop._writing(run):
        loop._save(run, {"attempted": sent, "sets": {}}, sources=())


def test_control_a_settled_body_refusal_keeps_its_reason_and_count(disk_run, monkeypatch):
    repo, base, session = disk_run

    def scout(*args):
        note_model_call()
        raise WorkspaceRefused(OUTSIDE_ROOT, "refused output")

    monkeypatch.setattr(loop, "_scout", scout)
    reply = review_loop.step(str(repo), TASK, "disk-review2", base)
    assert reply["code"] == OUTSIDE_ROOT and reply["requests_sent"] == 1
    assert "refused output" in reply["error"] and "No Python changed" not in reply["error"]


def test_control_no_python_changed_is_said_when_none_did(disk_run, monkeypatch):
    repo, base, session = disk_run
    (repo / "mod.py").write_text("def double(x):\n    return x + 2\n", encoding="utf-8")     # back to the base
    reply = review_loop.step(str(repo), TASK, "disk-review2", base)
    assert reply["code"] == loop.NO_PYTHON and "No Python changed" in reply["error"]
    assert "requests_sent" not in reply


def test_mcp_path_refusal_is_not_worded_as_nothing_changed_and_nothing_spent(disk_run, monkeypatch, tmp_path):
    repo, base, session = disk_run
    quiet_scouts(monkeypatch, sends=True)
    session.mkdir(parents=True)
    _in_flight(session, 2)
    hard_link(tmp_path, session / "state.json.new")
    try:
        reply = review_loop.step(str(repo), TASK, "disk-review2", base)
    finally:
        (session / "state.json.new").unlink()
    assert reply["code"] == OUTSIDE_ROOT and reply["requests_sent"] == 2
    assert "nothing was spent" not in reply["error"], reply["error"]
    assert "No Python changed" not in reply["error"], reply["error"]


def test_mcp_and_hook_agree_on_a_consent_stop_that_is_not_settled(disk_run, monkeypatch):
    """The MCP twin of tests/harness/test_completion_amendment_reply.py's first case."""
    repo, base, session = disk_run
    session.mkdir(parents=True)
    _in_flight(session, 2)
    monkeypatch.setattr(loop, "advance", lambda *a, **k:
                        loop.Step(loop.STOP, "The review was not sent.", cloud.REQUIRED, ending="untested"))
    reply = review_loop.step(str(repo), TASK, "disk-review2", base)
    assert reply["code"] == cloud.REQUIRED and reply["requests_sent"] == 2
    assert "nothing was spent" not in reply["error"], reply["error"]
    assert "up to 2 request(s) of this run may have gone out" in reply["error"] + reply["report"]


def test_stop_hook_path_refusal_is_not_worded_as_a_sharing_refusal(disk_run, monkeypatch, tmp_path):
    repo, base, _ = disk_run
    quiet_scouts(monkeypatch, sends=True)
    event, run = hook_session(monkeypatch, repo, "rev3-f2")
    _in_flight(run, 2)
    hard_link(tmp_path, run / "state.json.new")
    try:
        reply = hook.on_stop(event)
    finally:
        (run / "state.json.new").unlink()
    said = reply["systemMessage"]
    assert OUTSIDE_ROOT in said
    assert "g6_cloud_grant" not in said and "not shared with this provider" not in said, said
