# SPDX-License-Identifier: Apache-2.0
"""Review 4, finding 4: the call that ran, sent and was refused is answered "did not run ... its report stands".

Review 3's finding 2 at its class (a reply worded from something other than what happened), on the path repair 3 opened.
When a refusal's report cannot be published, ``_refused`` saves the fallback ending and re-raises; ``_advance_inner`` then
returns the publication's refusal as the step. ``reply_for`` reads that as a refusal of its own on a settled run and adds
the repeat's notice. So the one reply of the call that did the work says, beside ``requests_sent: 1``:

* "G6's completion loop did not run" (it ran, and a request went out);
* "its report stands" (none was published: the state has no ``report_sha256``);
* "Your next prompt starts a new loop" (the hook's way on, over MCP);

and nothing of the sharing refusal that ended the run, the one thing the person can act on.
"""
from g6.core import cloud_consent as cloud
from g6.core.llm import LLMError
from g6.core.model_calls import note_model_call
from g6.harness import completion_loop as loop
from g6.mcp import review_loop
from g6.workspace._envelope import OUTSIDE_ROOT
from tests._review_wtffu2b_3.rev3_world import TASK, hard_link
from tests._review_wtffu2b4.rev4_world import SESSION
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run


def _sharing_refused_after_one_request(monkeypatch):
    def scout(change, task, text, where, host, deadline):
        note_model_call()
        raise cloud._refusal(LLMError(f"{cloud.REQUIRED}: next send refused"))

    monkeypatch.setattr(loop, "_scout", scout)


def test_control_the_refusal_that_ended_the_run_is_what_its_call_says(disk_run, monkeypatch):
    repo, base, session = disk_run
    _sharing_refused_after_one_request(monkeypatch)
    reply = review_loop.step(str(repo), TASK, SESSION, base)
    assert reply["code"] == cloud.REQUIRED
    assert reply["requests_sent"] == 1 and "note" not in reply
    assert "up to 1 request(s) of this run may have gone out" in reply["report"]


def test_control_a_repeat_of_that_run_is_what_says_it_did_not_run(disk_run, monkeypatch):
    repo, base, session = disk_run
    _sharing_refused_after_one_request(monkeypatch)
    review_loop.step(str(repo), TASK, SESSION, base)
    repeat = review_loop.step(str(repo), TASK, SESSION, base)
    assert "did not run" in repeat["note"] and repeat["requests_sent"] == 1


def test_the_call_that_ran_is_not_answered_as_a_repeat_that_did_not_run(disk_run, monkeypatch, tmp_path):
    repo, base, session = disk_run
    _sharing_refused_after_one_request(monkeypatch)
    session.mkdir(parents=True)
    victim = hard_link(tmp_path, session / "report.md")
    try:
        reply = review_loop.step(str(repo), TASK, SESSION, base)
    finally:
        (session / "report.md").unlink()
    state = loop._load(session)
    assert victim.read_bytes() == b"keep"
    assert reply["code"] == OUTSIDE_ROOT and reply["requests_sent"] == 1          # the publication's refusal, and the count
    assert state["done"] and state["code"] == cloud.REQUIRED and "report_sha256" not in state
    note = reply.get("note", "")
    assert "did not run" not in note and "its report stands" not in note, (note, reply.get("recovery"))
