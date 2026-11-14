# SPDX-License-Identifier: Apache-2.0
"""The view on the three report paths (design 17.3 "Report order"; 16 for what a provider may see): a finished review
job, a loop step over MCP and the Stop hook each show the whole report first and the block after it; without the
person's grant for the workflow source the block is one withholding line and the reply's sources are unchanged."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("fastmcp")

from g6.core import cloud_consent as cloud  # noqa: E402
from g6.harness import completion_hook  # noqa: E402
from g6.harness import completion_loop as loop  # noqa: E402
from g6.harness.workflow import adapters, policy, view  # noqa: E402
from g6.harness.workflow import record as rec  # noqa: E402
from g6.mcp import workflow as tools  # noqa: E402
from tests.workflow.kit import loop_state, make_repo  # noqa: E402

TASK_TEXT = "fix this bug"
REPORT = "# Review\n\n## Reproduced findings\n- R1 fails on its stated check."
ASSESSMENT = {"report": REPORT, "outcome": "defect_detected", "reasons": [], "requirements": [
    {"id": "R1", "status": "failed", "provenance": "stated",
     "checks": [{"id": "tests/test_calc.py::test_add", "verdict": "failed", "provenance": "stated"}]}]}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6-home"))
    monkeypatch.setenv(policy.RECORD_ENV, "on")
    return make_repo(tmp_path / "repo")


def _share(source, provider):
    """A grant through the dialog path, with a session that accepts. (The shared fixture also keeps the granted source
    in force as a request source for the rest of the test, which would hide what a reply is itself bound to.)"""
    import asyncio
    from types import SimpleNamespace

    from g6.mcp.cloud_consent import request_grant

    class Accepting:
        async def elicit_form(self, **_kwargs):
            return SimpleNamespace(action="accept", content={"value": "Fixture person"})

    result = asyncio.run(request_grant(SimpleNamespace(session=Accepting(), request_id="fixture"), source, provider))
    assert result["status"] == "granted"


def _grant(repo, provider, _unused=None):
    _share(rec.workflow_source(rec.project_id(repo)), provider)


def _finish(reply, job_id):
    shown = {"assessment": {"report": reply["assessment"]["report"], "outcome": reply["assessment"]["outcome"]},
             "job_id": job_id}
    return cloud.bind_output(shown, (cloud.Source.text("fixture:review", "x", "y"),))


def _as(provider):
    """A capture as the consent egress makes one for a call from ``provider``'s client."""
    capture = cloud.current_capture()
    capture.client = (provider, None)


def _sources(reply) -> set[str]:
    return {ref.source_id for ref in cloud.output_sources(reply)}


def test_a_finished_review_shows_its_whole_report_then_the_block(repo):
    _grant(repo, "openai:codex")
    with cloud.collect_sources():
        _as("openai:codex")
        done = tools.finishing(_finish, str(repo), TASK_TEXT, {"goal": "add returns the sum"})(
            {"assessment": ASSESSMENT, "completion_state": "qualified-draft"}, "job-1")
        report = done["assessment"]["report"]
        assert report.startswith(REPORT + "\n\n" + view.HEADING) and done["assessment"]["outcome"] == "defect_detected"
        assert "`pytest tests/test_calc.py::test_add`" in report and "`add returns the sum`" in report
        shown = done["workflow"]
        assert shown["level"] == "narrow" and shown["outcome"]["provenance"] == "attributed_label"
        assert shown["request"]["shown"] == TASK_TEXT and [o["kind"] for o in shown["obstacles"]] == ["failing_accepted_check"]
        assert shown["obstacles"][0]["receipt"]["id"] == "job-1" and shown["value_result"] == "not_measured"
        assert shown["friction"]["wall_seconds"] is not None           # this job's own run, measured in one process
        assert _sources(done) == {"fixture:review", rec.workflow_source(rec.project_id(repo))}


@pytest.mark.parametrize("provider", ["openai:codex", "anthropic:claude_code", "cloud:unknown", None])
def test_without_the_workflow_grant_the_review_is_whole_and_the_view_is_one_line(repo, provider):
    with cloud.collect_sources():
        if provider:
            _as(provider)
        done = tools.finishing(_finish, str(repo), TASK_TEXT, {"goal": "PLANTED-GOAL"})(
            {"assessment": ASSESSMENT}, "job-1")
        assert done["assessment"]["report"] == REPORT + "\n\n" + view.WITHHELD
        assert done["workflow"] == {"level": "narrow", "withheld": True, "policy_version": policy.POLICY_VERSION}
        assert "PLANTED-GOAL" not in json.dumps(done)
        assert _sources(done) == {"fixture:review"}                     # no workflow source: the report is not held back
    project, task = adapters.identities(repo, TASK_TEXT)
    assert [e.kind for e in rec.ProjectRecord(project).events(task=task)][:2] == ["run_started", "review_finished"]


def test_a_grant_for_another_provider_or_the_repository_shares_no_view(repo):
    _grant(repo, "anthropic:claude_code")
    _share(cloud.repository_source(repo), "openai:codex")
    with cloud.collect_sources():
        _as("openai:codex")
        done = tools.finishing(_finish, str(repo), TASK_TEXT)({"assessment": ASSESSMENT}, "job-1")
        assert done["workflow"]["withheld"] is True
    cloud.revoke(rec.workflow_source(rec.project_id(repo)), "anthropic:claude_code")
    with cloud.collect_sources():
        _as("anthropic:claude_code")
        again = tools.finishing(_finish, str(repo), TASK_TEXT)({"assessment": ASSESSMENT}, "job-2")
        assert again["workflow"]["withheld"] is True                    # revoked: the next reply carries nothing


def test_a_refused_review_gets_no_view_and_stays_as_it_was(repo):
    refusal = {"ok": False, "completion_state": "blocked-escalated", "error": "[G6_E_GIT_REPO_INVALID] no"}
    with cloud.collect_sources():
        _as("openai:codex")
        assert tools.finishing(lambda reply, job_id: reply, str(repo), TASK_TEXT)(refusal, "job-1") is refusal


def test_the_note_and_the_view_go_on_a_detached_copy_of_the_reply_that_was_checked(repo):
    """Section 16, root A: a reply's receipt is matched against one serialisation of it, and what the workflow adds goes
    on a detached copy of that serialisation, so nothing the live reply becomes afterwards goes out under its sources."""
    with cloud.collect_sources():
        reply = cloud.bind_output({"assessment": {"report": REPORT}, "evidence": [{"id": "E1"}], "job_id": "j"},
                                  (cloud.Source.text("fixture:review", "x", "y"),), metadata={"k": 1})
        noted = tools._noted(reply, "a note")
        shown = tools.attach(reply, {"level": "narrow", "withheld": True}, ("assessment", "report"), "p" * 64)
        reply["assessment"]["report"] = reply["evidence"][0]["id"] = "CHANGED AFTER THE CHECK"
        assert noted["assessment"]["report"] == REPORT and noted["workflow"] == {"note": "a note"}
        assert shown["assessment"]["report"] == REPORT + "\n\n" + view.WITHHELD
        assert "CHANGED" not in json.dumps([noted, shown])
        assert _sources(noted) == _sources(shown) == {"fixture:review"}
        assert cloud.output_metadata(noted) == cloud.output_metadata(shown) == {"k": 1}


def test_a_loop_step_that_stops_shows_its_report_then_the_block(repo):
    _grant(repo, "openai:codex")
    folder = completion_hook.folder("mcp:session-1")
    folder.mkdir(parents=True)
    # written as the loop writes it, with its completion digest: an unproduced state is withheld on the 0.3 line
    cloud.write_retained(folder / "state.json", json.dumps(loop_state(1, done=True, ending="clean")).encode("utf-8"))
    stop = {"ok": True, "action": "stop", "report": REPORT, "ending": "clean", "run_id": folder.name}
    carry = {"ok": True, "action": "continue", "text": "repair this"}
    with cloud.collect_sources():
        _as("openai:codex")
        bound = lambda result: (lambda *a: cloud.bind_output(dict(result), (cloud.Source.text("fixture:loop", "x", "y"),),  # noqa: E731
                                                             metadata={"has_report": "report" in result}))
        done = tools.stepped(bound(stop), str(repo), TASK_TEXT, "session-1")
        assert done["report"].startswith(REPORT + "\n\n" + view.HEADING) and done["ending"] == "clean"
        assert "Observation is insufficient" in done["report"] and done["workflow"]["obstacles"] == []
        assert _sources(done) == {"fixture:loop", rec.workflow_source(rec.project_id(repo))}
        assert cloud.output_metadata(done) == {"has_report": True}
        going = tools.stepped(bound(carry), str(repo), TASK_TEXT, "session-1")
        assert going == carry and _sources(going) == {"fixture:loop"}        # a step that goes on carries no view


def test_an_unsent_review_is_shown_as_g6s_own_obstacle(repo):
    _grant(repo, "openai:codex")
    folder = completion_hook.folder("mcp:session-2")
    folder.mkdir(parents=True)
    stop = {"ok": False, "action": "stop", "report": "The review was not sent.", "ending": "untested",
            "run_id": folder.name, "error": "[G6_E_CLOUD_CONSENT_REQUIRED] Results were withheld"}
    with cloud.collect_sources():
        _as("openai:codex")
        done = tools.stepped(lambda *a: cloud.bind_output(dict(stop), ()), str(repo), TASK_TEXT, "session-2")
        assert [o["kind"] for o in done["workflow"]["obstacles"]] == ["review_unsent"]
        assert "G6's own setup" in done["report"] and done["workflow"]["next_check"]["kind"] == "grant_source"


def _stop_hook(repo, tmp_path, monkeypatch, step):
    session = completion_hook.folder("hook-session")
    session.mkdir(parents=True, exist_ok=True)
    # written as the hook's producer writes it, with its completion digest: an unproduced task is withheld on the 0.3 line
    cloud.write_retained(session / "task.json",
                         json.dumps({"repo": str(repo), "task": TASK_TEXT, "base": "a" * 40}).encode("utf-8"))
    monkeypatch.setattr(loop, "advance", lambda *a, **k: step)
    return completion_hook.on_stop({"session_id": "hook-session", "cwd": str(repo), "transcript_path": ""})


def test_the_stop_hook_shows_the_report_first_and_the_block_only_with_the_grant(repo, tmp_path, monkeypatch):
    stop = loop.Step(loop.STOP, text=REPORT, ending="clean")
    said = _stop_hook(repo, tmp_path, monkeypatch, stop)["systemMessage"]
    assert said == REPORT + "\n\n" + view.WITHHELD
    _grant(repo, "anthropic:claude_code")
    said = _stop_hook(repo, tmp_path, monkeypatch, stop)["systemMessage"]
    assert said.startswith(REPORT + "\n\n" + view.HEADING) and "G6's own cost on this task" in said
    project, task = adapters.identities(repo, TASK_TEXT)
    kinds = [e.kind for e in rec.ProjectRecord(project).events(task=task)]
    assert kinds == ["run_started", "loop_ended", "report_returned"]             # the same stop, replayed: once
    # a turn handed back to the agent, and a stop with no report, carry no block
    assert _stop_hook(repo, tmp_path, monkeypatch, loop.Step(loop.CONTINUE, text="repair this")) == {
        "decision": "block", "reason": "repair this"}
    assert _stop_hook(repo, tmp_path, monkeypatch, loop.Step(loop.STOP, note="did not run"))["systemMessage"] == "did not run"


@pytest.mark.parametrize("name,value", [(policy.RECORD_ENV, "off"), (policy.RECORD_ENV, None), (view.VIEW_ENV, "off")])
def test_with_the_record_or_the_view_off_every_path_is_exactly_as_before(repo, tmp_path, monkeypatch, name, value):
    monkeypatch.delenv(name, raising=False) if value is None else monkeypatch.setenv(name, value)
    with cloud.collect_sources():
        _as("openai:codex")
        reply = {"assessment": ASSESSMENT}
        done = tools.finishing(_finish, str(repo), TASK_TEXT)(reply, "job-1")
        assert done == {"assessment": {"report": REPORT, "outcome": "defect_detected"}, "job_id": "job-1"}
        stop = {"ok": True, "action": "stop", "report": REPORT, "ending": "clean"}
        assert tools.stepped(lambda *a: dict(stop), str(repo), TASK_TEXT, "session-1") == stop
    said = _stop_hook(repo, tmp_path, monkeypatch, loop.Step(loop.STOP, text=REPORT, ending="clean"))
    assert said == {"systemMessage": REPORT}
    if name == policy.RECORD_ENV:
        assert not (tmp_path / "g6-home" / "workflow").exists()
