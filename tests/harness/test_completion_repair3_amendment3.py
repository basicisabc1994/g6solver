# SPDX-License-Identifier: Apache-2.0
"""No-review turns and interrupted refusals keep their original boundaries."""
import pytest

from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.harness import completion_hook as hook, completion_loop as loop
from g6.harness.workflow import policy, view
from g6.mcp import review_loop
from g6.workspace._envelope import OUTSIDE_ROOT, WorkspaceRefused
from tests.harness.test_completion_amendment_reply import hook_run
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run


@pytest.mark.parametrize("note", [loop.ALREADY_READ, "did not run"])
def test_no_review_note_is_not_rendered_as_a_report(tmp_path, monkeypatch, note):
    monkeypatch.setattr(loop, "reply_state", lambda _: {"reviewed": "old patch", loop.ATTEMPTED: 0})
    step, _, why = loop.reply_for(tmp_path, loop.Step(loop.STOP, note=note), str(tmp_path), "base", hook=True)
    assert step.text == "" and step.note == note
    assert why == note


def test_no_python_mcp_keeps_empty_report_and_no_count(disk_run, monkeypatch):
    repo, base, _ = disk_run
    monkeypatch.setattr(loop, "advance", lambda *a, **k:
                        loop.Step(loop.STOP, code=loop.NO_PYTHON, note=loop.NOTHING_CHANGED, ending="untested"))
    reply = review_loop.step(str(repo), "task", "disk-review2", base)
    assert reply["report"] == ""
    assert "nothing was tested" in reply["warning_card"]["message"]
    assert "nothing was spent" in reply["error"] and "requests_sent" not in reply


def test_no_review_hook_keeps_note_without_workflow_appendix(tmp_path, monkeypatch):
    session, event = hook_run(tmp_path, monkeypatch, loop.Step(loop.STOP, note="did not run"))
    monkeypatch.setattr(policy, "enabled", lambda: (True, ""))
    monkeypatch.setattr(loop, "reply_state", lambda _: {})
    monkeypatch.setattr(view, "appended", lambda *a, **k: "unexpected workflow appendix")
    assert hook.on_stop(event) == {"systemMessage": "did not run"}
    assert (session / hook.TURN_ENDED).is_file()


def test_hook_repeat_after_an_edit_keeps_the_no_run_notice(tmp_path, monkeypatch):
    step = loop.Step(loop.STOP, note=loop.ALREADY_ENDED, ending="clean")
    session, event = hook_run(tmp_path, monkeypatch, step)
    loop._finish(session, {loop.ATTEMPTED: 1}, "earlier report", ending="clean", patch="earlier patch")
    monkeypatch.setattr(loop, "_patch_now", lambda *a: "changed patch")
    assert hook.on_stop(event) == {"systemMessage": loop.ALREADY_ENDED}


def test_mcp_repeat_keeps_parent_note_and_supplies_mcp_recovery(disk_run):
    repo, base, session = disk_run
    patch = loop._patch_now(repo, base)
    with loop._writing(session):
        loop._finish(session, {loop.ATTEMPTED: 0}, "earlier report", ending="clean", patch=patch)
    reply = review_loop.step(str(repo), "task", "disk-review2", base)
    assert reply["note"] == loop.ALREADY_ENDED and reply["report"] == "earlier report"
    assert "g6_review_until_clean" in reply["recovery"] and "new session" in reply["recovery"]


def test_interrupted_ordinary_refusal_can_resume_before_settlement(disk_run, monkeypatch):
    repo, base, session = disk_run

    def interrupted(*a, **k):
        raise KeyboardInterrupt("stopped before the ending was published")

    monkeypatch.setattr(loop, "_finish", interrupted)
    with loop._writing(session):
        state = loop._load(session)
        with pytest.raises(KeyboardInterrupt):
            loop._refused(session, state, "task", base, repo, "boundary unavailable", ISOLATION_UNAVAILABLE,
                          loop.UNCONFINED)
    assert not loop._load(session).get("done")


def test_refusal_reads_ending_patch_after_row_delivery(disk_run, monkeypatch):
    repo, base, session = disk_run
    patch = "before row"
    original = loop._write_row

    def delivered(run, state):
        nonlocal patch
        original(run, state)
        patch = "after row"

    monkeypatch.setattr(loop, "_patch_now", lambda *a: patch)
    monkeypatch.setattr(loop, "_write_row", delivered)
    with loop._writing(session):
        loop._refused(session, loop._load(session), "task", base, repo, "boundary unavailable",
                      ISOLATION_UNAVAILABLE, loop.UNCONFINED)
    assert loop._load(session)["ended_patch"] == "after row"


def test_refused_publication_keeps_its_own_step_code(disk_run, monkeypatch):
    repo, base, session = disk_run
    original = loop.store.write_bytes

    def refused_report(path, body):
        if path.name == "report.md":
            raise WorkspaceRefused(OUTSIDE_ROOT, "report publication refused")
        return original(path, body)

    monkeypatch.setattr(loop.proposer, "boundary", lambda _: "boundary unavailable")
    monkeypatch.setattr(loop.store, "write_bytes", refused_report)
    reply = review_loop.step(str(repo), "task", "disk-review2", base)
    assert reply["code"] == OUTSIDE_ROOT and "report publication refused" in reply["error"]
    state = loop._load(session)
    assert state["done"] and state["code"] == ISOLATION_UNAVAILABLE
    assert state["ending"] == "untested" and "report_sha256" not in state
