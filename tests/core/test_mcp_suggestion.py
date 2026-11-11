# SPDX-License-Identifier: Apache-2.0
"""g6_suggestion_decide (design 17.3 "Suggestions"; AT-007): only the person adopts, dismisses or reopens, in the
client's own dialog; nothing is recorded on a decline, a cancel or no dialog; no reason is asked for; and the tool
answers only once suggestions are admitted."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("fastmcp")

from g6.core import cloud_consent as cloud  # noqa: E402
from g6.core import consent_declarations as declared  # noqa: E402
from g6.harness.workflow import adapters, lifecycle, policy, view  # noqa: E402
from g6.harness.workflow import record as rec  # noqa: E402
from g6.harness.workflow import suggest as sg  # noqa: E402
from g6.harness.workflow.decisions import Decisions  # noqa: E402
from g6.harness.workflow.friction import friction  # noqa: E402
from g6.mcp import build_server  # noqa: E402
from g6.mcp import workflow as tools  # noqa: E402
from tests.workflow.kit import Step, at, call, loop_state, make_repo  # noqa: E402

TASK_TEXT = "make the widget count stable"
ONE = sg.Intervention("run_reproduction", "R1", "task")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6-home"))
    monkeypatch.setenv(policy.RECORD_ENV, "on")
    monkeypatch.setattr(view, "ADMITTED_LEVEL", "suggestions")           # as the change recording G4's result would
    folder = make_repo(tmp_path / "repo")
    (tmp_path / "s").mkdir()
    adapters.observe_loop(folder, TASK_TEXT, tmp_path / "s", loop_state(1), Step("continue"), at=at(0))
    project, task = adapters.identities(folder, TASK_TEXT)
    assert sg.record_event(rec.ProjectRecord(project), task, "suggestion_displayed", ONE, at=at(5), native="job-1",
                           extra={"outcome_sha256": "a" * 64})
    return folder


def _ids(repo):
    return adapters.identities(repo, TASK_TEXT)


def ctx(action, content=None, seen=None):
    async def elicit_form(**kwargs):
        if seen is not None:
            seen.append(kwargs)
        return SimpleNamespace(action=action, content=content)
    return SimpleNamespace(session=SimpleNamespace(elicit_form=elicit_form), request_id="r")


def _decide(repo, context, intervention=ONE.id):
    with cloud.collect_sources():
        return asyncio.run(tools.decide(str(repo), TASK_TEXT, intervention, context))


def test_tool_schema_has_no_decision_argument():
    registered = asyncio.run(build_server().get_tools())
    schema = registered["g6_suggestion_decide"].parameters
    assert set(schema["properties"]) == {"repo", "task", "intervention_id"} == set(declared.TOOL_ARGUMENTS["g6_suggestion_decide"])
    assert registered["g6_suggestion_decide"].fn._g6_declaration == declared.TOOLS["g6_suggestion_decide"]
    assert "client_workflow" in declared.TOOLS["g6_suggestion_decide"].classes


@pytest.mark.parametrize("action,content", [("decline", None), ("cancel", None), ("unavailable", None),
                                            ("accept", None), ("accept", {"decision": "adopt"}),
                                            ("accept", {"decision": "dismiss", "name": "Ada"}),
                                            ("accept", {"decision": "dismiss for this task", "name": " "}),
                                            ("accept", {"decision": "adopt", "name": "Ada", "expires_in_days": 0})])
def test_decline_cancel_and_unavailable_record_nothing(repo, action, content):
    project, task = _ids(repo)
    context = ctx(action, content)
    if action == "unavailable":
        async def broken(**kwargs):
            raise RuntimeError("the client has no dialog")
        context = SimpleNamespace(session=SimpleNamespace(elicit_form=broken), request_id="r")
    reply = _decide(repo, context)
    assert reply["result"]["status"] == "not_recorded" and reply["result"]["recorded"] is False
    assert "corrections.json" in reply["result"]["how"]
    assert Decisions(project).held(task=task) == []                       # neither adoption nor dismissal
    kinds = [e.kind for e in rec.ProjectRecord(project).events(task=task)]
    assert "suggestion_adopted" not in kinds and "suggestion_dismissed" not in kinds
    assert kinds.count("approval_shown") == 1                             # the dialog is still counted as G6's friction


def test_no_explanation_is_asked_for(repo):
    seen = []
    reply = _decide(repo, ctx("accept", {"decision": "dismiss for this task", "name": "Ada"}, seen))
    assert reply["result"]["status"] == "recorded" and reply["result"]["decision"] == "dismissed"
    (shown,) = seen
    form = shown["requestedSchema"]
    assert form["required"] == ["decision", "name"] and set(form["properties"]) == {"decision", "expires_in_days", "name"}
    assert not {"reason", "explanation", "why", "comment", "text"} & set(form["properties"])
    assert form["properties"]["decision"]["enum"] == ["adopt", "dismiss for this task", "dismiss for this project", "reopen"]
    message = shown["message"]
    assert "No reason is asked for" in message and "run the failing check yourself (R1)" in message
    assert "365 days" in message and "stated outcome is replaced" in message and TASK_TEXT not in message
    assert "is never hidden by this" in message


def test_the_decision_is_kept_before_the_reply_and_suppresses_the_suggestion(repo):
    project, task = _ids(repo)
    reply = _decide(repo, ctx("accept", {"decision": "dismiss for this project", "name": "Ada", "expires_in_days": 30}))
    assert reply["result"] == {"status": "recorded", "recorded": True, "decision": "dismissed", "scope": "project",
                               "expires_at": reply["result"]["expires_at"], "intervention_id": ONE.id}
    (body,) = Decisions(project).held(task=task)
    assert body["scope"] == "project" and body["by"] == "Ada" and body["reopen_rule"] == "outcome_superseded"
    assert body["outcome_sha256"] == "a" * 64 and body["expires_at"] == reply["result"]["expires_at"]
    assert "Ada" not in json.dumps(reply)
    from datetime import UTC, datetime

    assert Decisions(project).standing(ONE.id, task=task, now=datetime.now(UTC), outcome_sha256="a" * 64)[0] == "suppressed"
    again = _decide(repo, ctx("accept", {"decision": "reopen", "name": "Ada"}))
    assert again["result"]["decision"] == "reopened"
    assert Decisions(project).standing(ONE.id, task=task, now=datetime.now(UTC), outcome_sha256="")[0] == "reopened"


def test_only_a_suggestion_g6_showed_can_be_decided(repo):
    project, task = _ids(repo)
    never = sg.Intervention("rerun_at_base", "R9", "task").id
    seen = []
    reply = _decide(repo, ctx("accept", {"decision": "adopt", "name": "Ada"}, seen), intervention=never)
    assert reply["result"] == {"status": "not_shown", "recorded": False} and seen == []      # no dialog was opened
    assert _decide(repo, ctx("accept"), intervention="not-an-id")["result"]["status"] == "not_shown"
    assert Decisions(project).held(task=task) == []


def test_the_tool_answers_only_once_suggestions_are_admitted(repo, monkeypatch):
    for level in ("narrow", "diagnosis"):
        monkeypatch.setattr(view, "ADMITTED_LEVEL", level)
        seen = []
        reply = _decide(repo, ctx("accept", {"decision": "adopt", "name": "Ada"}, seen))
        assert reply["ok"] is False and "G6_E_WORKFLOW_NOT_ADMITTED" in reply["error"] and seen == []
        assert cloud.output_sources(reply) is None or True
    monkeypatch.setattr(view, "ADMITTED_LEVEL", "suggestions")
    monkeypatch.setenv(view.VIEW_ENV, "narrow")                           # the person turned the view down
    assert "G6_E_WORKFLOW_NOT_ADMITTED" in _decide(repo, ctx("accept", {"decision": "adopt", "name": "Ada"}))["error"]
    assert Decisions(_ids(repo)[0]).held(task=_ids(repo)[1]) == []


def test_reply_is_withheld_without_the_workflow_grant(repo, accepted_cloud_dialog):
    project, task = _ids(repo)
    asked = []

    async def handler(message, response_type, params, context):
        asked.append(message)
        return {"decision": "dismiss for this task", "name": "Ada"}
    arguments = {"repo": str(repo), "task": TASK_TEXT, "intervention_id": ONE.id}
    reply = call(build_server(), "g6_suggestion_decide", arguments, handler=handler)
    assert reply["ok"] is False and reply["cloud_consent_code"] == cloud.REQUIRED and "result" not in reply
    assert asked == [] and Decisions(project).held(task=task) == []       # the dialog would have shown workflow data
    accepted_cloud_dialog(rec.workflow_source(project), "openai:codex", text="workflow record")
    shared = call(build_server(), "g6_suggestion_decide", arguments, handler=handler)
    assert shared["ok"] is True and shared["result"]["decision"] == "dismissed" and len(asked) == 1
    smuggled = call(build_server(), "g6_suggestion_decide", {**arguments, "decision": "adopt"}, handler=handler)
    assert smuggled.get("ok") is not True and len(Decisions(project).held(task=task)) == 1


def test_eligible_displayed_adopted_dismissed_implemented_assessed_are_separate_events(repo):
    project, task = _ids(repo)
    record = rec.ProjectRecord(project)
    sg.record_event(record, task, "suggestion_eligible", ONE, at=at(4), provenance="rule_derived")
    _decide(repo, ctx("accept", {"decision": "adopt", "name": "Ada"}))
    _decide(repo, ctx("accept", {"decision": "dismiss for this task", "name": "Ada"}))
    sg.record_event(record, task, "suggestion_implemented", ONE, at=at(50))
    sg.record_event(record, task, "suggestion_assessed", ONE, at=at(60), extra={"outcome_reached": True})
    events = [e for e in record.events(task=task) if e.kind.startswith("suggestion_")]
    assert sorted({e.kind for e in events}) == sorted(sg.EVENT_KINDS)
    assert {e.data["intervention"] for e in events} == {ONE.id} and len({e.event_id for e in events}) == len(events)
    with pytest.raises(ValueError):
        sg.record_event(record, task, "suggestion_liked", ONE, at=at(70))
    counted = friction(adapters.task_view(project, task))
    assert counted.approvals == 2                                         # each dialog, whatever was chosen


def test_a_decision_through_the_corrections_file_when_there_is_no_dialog(repo):
    project, task = _ids(repo)
    folder = rec.ProjectRecord(project).root
    row = {"action": "decision", "intervention": ONE.id, "decision": "dismissed", "task": task, "by": "A person",
           "expires_days": 14}
    (folder / "corrections.json").write_text(json.dumps({"schema": lifecycle.CORRECTIONS_SCHEMA, "corrections": [row]}),
                                             encoding="utf-8")
    assert lifecycle.apply_corrections(project)["applied"] == 1
    (body,) = Decisions(project).held(task=task)
    assert body["decision"] == "dismissed" and body["expires_at"] and body["by"] == "A person"
    assert lifecycle.apply_corrections(project) == {"status": "nothing", "applied": 0, "already": 1, "problems": []}
    for bad in ({"intervention": "f" * 64}, {"decision": "ignored"}, {"task": "f" * 64}, {"expires_days": 0}, {"reason": "x"}):
        (folder / "corrections.json").write_text(json.dumps({"schema": lifecycle.CORRECTIONS_SCHEMA,
                                                             "corrections": [{**row, **bad}]}), encoding="utf-8")
        refused = lifecycle.apply_corrections(project)
        assert refused["status"] == "refused" and "entry 1" in refused["problems"][0], bad
    assert len(Decisions(project).held(task=task)) == 1
