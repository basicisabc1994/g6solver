# SPDX-License-Identifier: Apache-2.0
"""Review 3, finding 1: a refused step on an ended run is answered as that run's clean stop.

The reply builders rebuild the step from retained state whenever that state says ``done`` and keep only the step's note.
A step the path gate refused before it could save carries its refusal in ``Step.code`` and ``Step.text`` and no note, so
the refusal is dropped: over MCP the reply is ``ok``, ``qualified-draft``, ending ``clean``, with the earlier report and
without the changed-code check a repeat gets; the Stop hook shows the earlier report as this turn's.
"""
from g6.harness import completion_hook as hook, completion_loop as loop
from g6.mcp import review_loop
from tests._review_wtffu2b_3.rev3_world import TASK, ended_clean, hard_link, hook_session
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run


def _moved_on(repo):
    (repo / "mod.py").write_text("def double(x):\n    return x * 3\n", encoding="utf-8")


def _mcp_ended(repo, base, session):
    first = review_loop._asked(str(repo), TASK, base, loop.store.path(session))     # binds the session, as a first call does
    session.mkdir(parents=True, exist_ok=True)
    ended_clean(repo, base, session, owed=True)
    return first


def test_control_repeat_after_the_code_changed_says_so(disk_run):
    repo, base, session = disk_run
    session.mkdir(parents=True)
    _mcp_ended(repo, base, session)
    _moved_on(repo)
    reply = review_loop.step(str(repo), TASK, "disk-review2", base)
    assert reply["ok"] is False and reply["code"] == loop.NO_CLEAN_REVIEW and reply["ending"] == "untested"
    assert reply["report"].startswith(review_loop.CHANGED)


def test_refused_repeat_of_an_ended_run_is_not_answered_as_a_clean_stop(disk_run, tmp_path):
    repo, base, session = disk_run
    session.mkdir(parents=True)
    _mcp_ended(repo, base, session)
    _moved_on(repo)
    hard_link(tmp_path, session / "state.json.new")      # the gate refuses the step's save: nothing of this call is kept
    try:
        reply = review_loop.step(str(repo), TASK, "disk-review2", base)
    finally:
        (session / "state.json.new").unlink()
    # The step was refused and the code is not the patch the clean review read: never ok, never clean.
    assert reply.get("ok") is not True, {k: reply.get(k) for k in ("ok", "ending", "reliability_label", "report")}
    assert reply.get("ending") != "clean"


def test_control_stop_hook_repeat_shows_the_no_run_notice(disk_run, monkeypatch):
    repo, base, _ = disk_run
    event, run = hook_session(monkeypatch, repo, "rev3-f1-control")
    ended_clean(repo, base, run, owed=True)
    reply = hook.on_stop(event)
    assert reply["systemMessage"] == loop.ALREADY_ENDED


def test_stop_hook_does_not_show_the_earlier_report_for_a_refused_turn(disk_run, monkeypatch, tmp_path):
    repo, base, _ = disk_run
    event, run = hook_session(monkeypatch, repo, "rev3-f1")
    ended_clean(repo, base, run, owed=True)
    hard_link(tmp_path, run / "state.json.new")
    try:
        reply = hook.on_stop(event)
    finally:
        (run / "state.json.new").unlink()
    assert "CLEAN REPORT" not in reply.get("systemMessage", ""), reply
