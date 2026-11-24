# SPDX-License-Identifier: Apache-2.0
"""Review 4, finding 7: a refused step on an ended run is still answered as that run's ending when the two codes are the same.

Review 3's finding 1 at its class (another refusal code, another ended state). ``reply_for`` knows a step's refusal as its
own only by ``step.code != state.get('code')``. A run that a path refusal ended (``completion_body_refused``, the ending
repair 3 added) keeps ``G6_E_WORKSPACE_OUTSIDE_ROOT``; a later step refused at another path carries the same code, so it is
read as that ending restated: ``repeat`` is false (the step has text), the earlier report is this call's ``report`` and
``error``, this call's own reason is dropped, there is no "already ended" notice, and the changed-code check a repeat gets
is skipped. The Stop hook shows the earlier report as this turn's. The builder's docstring says the opposite: "A repeat and
the changed-patch check depend on the saved ending, never on whether a refused step managed to carry the usual repeat note."

Both answers are refusals with one code, so nothing reads as more certain than it is: only the words and the notice are
another call's.
"""
import json

from g6.harness import completion_hook as hook, completion_loop as loop
from g6.mcp import review_loop
from g6.workspace._envelope import OUTSIDE_ROOT
from tests._review_wtffu2b_3.rev3_world import TASK, hard_link, hook_session
from tests._review_wtffu2b4.rev4_world import SESSION
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run

EARLIER = f"[{OUTSIDE_ROOT}] The review stopped: refused output"


def _ended_by_a_path_refusal(repo, base, run):
    """A run a body refusal ended after one request, as ``_refused`` settles it, its row still owed."""
    row = json.dumps({"session": run.name, "cause": "completion_body_refused"})
    with loop._writing(run):
        loop._finish(run, {"attempted": 1, "stop": EARLIER, "row_due": row}, EARLIER, OUTSIDE_ROOT, "untested",
                     patch=loop._patch_now(repo, base))


def _mcp_session(repo, base, session):
    session.mkdir(parents=True, exist_ok=True)
    review_loop._asked(str(repo), TASK, base, loop.store.path(session))      # binds the session, as a first call does
    _ended_by_a_path_refusal(repo, base, session)
    (repo / "mod.py").write_text("def double(x):\n    return x * 3\n", encoding="utf-8")     # the code moves on


def test_control_mcp_repeat_of_that_run_says_the_code_has_changed(disk_run):
    repo, base, session = disk_run
    _mcp_session(repo, base, session)
    reply = review_loop.step(str(repo), TASK, SESSION, base)
    assert reply["code"] == OUTSIDE_ROOT and reply["note"] == review_loop.CHANGED


def test_mcp_refused_repeat_with_the_endings_own_code_is_this_calls_refusal(disk_run, tmp_path):
    repo, base, session = disk_run
    _mcp_session(repo, base, session)
    hard_link(tmp_path, session / "state.json.new")          # the repeat's save of the delivered row is refused
    try:
        reply = review_loop.step(str(repo), TASK, SESSION, base)
    finally:
        (session / "state.json.new").unlink()
    assert reply["code"] == OUTSIDE_ROOT and reply["requests_sent"] == 1
    assert "state.json.new" in reply["error"], reply["error"]                 # this call's reason, not the earlier one's
    assert reply.get("note") == review_loop.CHANGED, reply.get("note")        # and the check every repeat gets


def test_control_hook_repeat_of_that_run_says_it_did_not_run(disk_run, monkeypatch):
    repo, base, _ = disk_run
    event, run = hook_session(monkeypatch, repo, "rev4-f7-control")
    _ended_by_a_path_refusal(repo, base, run)
    assert hook.on_stop(event) == {"systemMessage": loop.ALREADY_ENDED}


def test_hook_refused_repeat_does_not_show_the_earlier_report_as_this_turns(disk_run, monkeypatch, tmp_path):
    repo, base, _ = disk_run
    event, run = hook_session(monkeypatch, repo, "rev4-f7")
    _ended_by_a_path_refusal(repo, base, run)
    hard_link(tmp_path, run / "state.json.new")
    try:
        said = hook.on_stop(event)["systemMessage"]
    finally:
        (run / "state.json.new").unlink()
    assert OUTSIDE_ROOT in said
    assert "refused output" not in said and "state.json.new" in said, said
