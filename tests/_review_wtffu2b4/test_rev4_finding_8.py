# SPDX-License-Identifier: Apache-2.0
"""Review 4, finding 8: the Stop hook no longer shows a coded run's report as it is kept.

``reply_for`` words every coded stop for the hook as ``[code] reason``. That suits a one-line refusal. A review-until-clean
run that ends with no clean review has a code too (``G6_E_NO_CLEAN_REVIEW``, ``G6_E_DEFECT_NOT_REPRODUCED``), and its text
is the whole report, so the person now reads ``[G6_E_NO_CLEAN_REVIEW] ## What the reviews found`` as the report's first
line, its heading no longer a line of its own, and the report passed through ``refusal_reason`` (which removes every
``[code]`` and ``code:`` it finds in it and strips its ends). At a3d8927a and at ad9e6b4d the hook showed the report as
kept, and over MCP ``report`` still is.
"""
from g6.core.llm import FakeLLM
from g6.harness import completion_hook as hook, completion_loop as loop
from g6.mcp import review_loop
from tests._review_wtffu2b_3.rev3_world import TASK, hook_session, quiet_scouts
from tests._review_wtffu2b4.rev4_world import SESSION
from tests.harness.test_completion_review2_on_disk import disk_run as disk_run


def _a_reviewer_that_cannot_be_read(key_file, monkeypatch):
    key_file("G6_LOOP_MODE=review\n")
    monkeypatch.setattr(loop, "critic_host", lambda: FakeLLM(lambda system, user: "No runnable review."))


def test_control_hook_shows_an_uncoded_report_as_kept(disk_run, monkeypatch):
    repo, base, _ = disk_run
    quiet_scouts(monkeypatch)
    event, run = hook_session(monkeypatch, repo, "rev4-f8-control")
    said = hook.on_stop(event)["systemMessage"]
    assert said == (run / "report.md").read_text(encoding="utf-8")


def test_control_mcp_gives_a_coded_report_as_kept(disk_run, key_file, monkeypatch):
    repo, base, session = disk_run
    _a_reviewer_that_cannot_be_read(key_file, monkeypatch)
    reply = review_loop.step(str(repo), TASK, SESSION, base)
    assert reply["code"] == loop.NO_CLEAN_REVIEW
    assert reply["report"] == (session / "report.md").read_text(encoding="utf-8")


def test_hook_shows_a_coded_report_with_its_first_line_as_kept(disk_run, key_file, monkeypatch):
    repo, base, _ = disk_run
    _a_reviewer_that_cannot_be_read(key_file, monkeypatch)
    event, run = hook_session(monkeypatch, repo, "rev4-f8")
    said = hook.on_stop(event)["systemMessage"]
    report = (run / "report.md").read_text(encoding="utf-8")
    assert loop._load(run)["code"] == loop.NO_CLEAN_REVIEW
    assert report.splitlines()[0] in said.splitlines(), said.splitlines()[0]
