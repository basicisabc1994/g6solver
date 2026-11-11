# SPDX-License-Identifier: Apache-2.0
"""g6_feedback over MCP (design 17.3; 16 for what a provider may see): no argument answers or opts in, what the
person volunteered never reaches a provider without the feedback source's own grant, and neither the repository's
grant nor the workflow record's covers it."""
from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("fastmcp")

from g6.core import cloud_consent as cloud  # noqa: E402
from g6.core import consent_declarations as declared  # noqa: E402
from g6.harness.workflow import adapters, feedback as fb, policy, view  # noqa: E402
from g6.harness.workflow import record as rec  # noqa: E402
from g6.mcp import build_server  # noqa: E402
from tests.workflow.kit import Step, at, call, loop_state, make_repo  # noqa: E402

MARK = "ZEBRA-MARKER-7731"
TASK_TEXT = "make the widget count stable"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6-home"))
    monkeypatch.setenv(policy.RECORD_ENV, "on")
    monkeypatch.setattr(view, "ADMITTED_LEVEL", "suggestions")
    folder = make_repo(tmp_path / "repo")
    (tmp_path / "s").mkdir()
    done = loop_state(1, done=True, ending="clean")
    adapters.observe_loop(folder, TASK_TEXT, tmp_path / "s", done, Step("stop", "clean"), at=at(0))
    return folder


def _args(repo, **more):
    return {"repo": str(repo), "task": TASK_TEXT, **more}


def _store(repo):
    return fb.FeedbackStore(rec.project_id(repo))


def test_tool_has_no_answer_argument():
    registered = asyncio.run(build_server().get_tools())
    schema = registered["g6_feedback"].parameters
    assert set(schema["properties"]) == {"repo", "task", "volunteered"} == set(declared.TOOL_ARGUMENTS["g6_feedback"])
    assert registered["g6_feedback"].fn._g6_declaration == declared.TOOLS["g6_feedback"]
    assert declared.TOOLS["g6_feedback"].classes == ("client_request", "client_workflow_feedback")
    refs = declared.TOOLS["g6_feedback"].sources({"repo": "/somewhere"})
    assert any(ref.source_id.startswith(("workflow-feedback:", "input:")) for ref in refs)


def test_feedback_text_never_reaches_an_ungranted_reply(repo):
    server = build_server()
    for client in ("codex", "claude-code", "some-other-client"):
        reply = call(server, "g6_feedback", _args(repo, volunteered=f"the block was too long {MARK}"), client=client)
        assert reply["ok"] is False and reply["cloud_consent_code"] == cloud.REQUIRED
        assert "result" not in reply
        echoed = json.dumps({k: v for k, v in reply.items()})
        assert MARK not in echoed, client                                 # bar: zero
    kept = [e["body"]["text"] for e in _store(repo).entries() if e["kind"] == "unsolicited"]
    assert len(kept) == 3 and all(MARK in text for text in kept)          # kept locally all the same


def test_repository_grant_does_not_cover_feedback(repo, accepted_cloud_dialog):
    project = rec.project_id(repo)
    accepted_cloud_dialog(cloud.repository_source(repo), "openai:codex", text="the repository")
    accepted_cloud_dialog(rec.workflow_source(project), "openai:codex", text="workflow record")
    server = build_server()
    assert call(server, "g6_feedback", _args(repo, volunteered="useful"))["ok"] is False
    assert call(server, "g6_workflow_export", {"repo": str(repo)})["ok"] is True          # that grant is its own
    accepted_cloud_dialog(fb.feedback_source(project), "openai:codex", text="feedback")
    shared = call(server, "g6_feedback", _args(repo, volunteered="useful"))
    assert shared["ok"] is True and shared["result"]["status"] == "kept" and "useful" not in json.dumps(shared["result"])
    assert call(server, "g6_feedback", _args(repo, volunteered="useful"), client="claude-code")["ok"] is False


def test_opt_in_cannot_be_set_by_a_tool_argument(repo, accepted_cloud_dialog, monkeypatch):
    project = rec.project_id(repo)
    accepted_cloud_dialog(fb.feedback_source(project), "openai:codex", text="feedback")
    monkeypatch.delenv(fb.OPT_IN_ENV, raising=False)
    server, asked = build_server(), []

    async def handler(message, response_type, params, context):
        asked.append(message)
        return {"answer": "easier"}
    for extra in ({"opt_in": True}, {"answer": "easier"}, {"choice": "in"}, {"do_not_ask_again": False}):
        reply = call(server, "g6_feedback", _args(repo, **extra), handler=handler)
        assert reply.get("ok") is not True, extra                         # an undeclared argument is refused outright
    plain = call(server, "g6_feedback", _args(repo), handler=handler)
    assert plain["result"]["status"] == "not_asked" and "not opted in" in plain["result"]["why"] and asked == []
    assert _store(repo).opted_in()[0] is False and [e["kind"] for e in _store(repo).entries()] == []
    monkeypatch.setenv(fb.OPT_IN_ENV, "on")                               # the person's own setting, where the server runs
    answered = call(server, "g6_feedback", _args(repo), handler=handler)
    assert answered["result"] == {"status": "answered", "asked": True, "retried": False} and len(asked) == 1
    assert asked[0].startswith(fb.QUESTION)
    again = call(server, "g6_feedback", _args(repo), handler=handler)
    assert again["result"]["status"] == "not_asked" and len(asked) == 1   # once a week, once a task
