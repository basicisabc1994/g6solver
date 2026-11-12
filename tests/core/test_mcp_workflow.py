# SPDX-License-Identifier: Apache-2.0
"""The workflow record's MCP tools and observers (design section 17): local inspection, correction and deletion, a
deletion only the person can confirm, and observers that never change the call they watch."""
from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("fastmcp")
from fastmcp.client.elicitation import ElicitResult  # noqa: E402

from g6.core import cloud_consent as cloud  # noqa: E402
from g6.core import consent_declarations as declared  # noqa: E402
from g6.harness.workflow import adapters, lifecycle, policy  # noqa: E402
from g6.harness.workflow import record as rec  # noqa: E402
from g6.mcp import build_server  # noqa: E402
from g6.mcp import workflow as tools  # noqa: E402
from tests.workflow.kit import Step, at, call, loop_state, make_repo  # noqa: E402

TASK_TEXT = "make the widget count stable"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6-home"))
    monkeypatch.setenv(policy.RECORD_ENV, "on")
    return make_repo(tmp_path / "repo")


@pytest.fixture
def granted(repo, accepted_cloud_dialog):
    accepted_cloud_dialog(rec.workflow_source(rec.project_id(repo)), "openai:codex", text="workflow record")
    return repo


def _recorded(repo, tmp_path) -> str:
    """One finished loop run in the record; its run id."""
    session = tmp_path / "session-a"
    session.mkdir(exist_ok=True)
    done = loop_state(2, done=True, ending="clean")
    assert adapters.observe_loop(repo, TASK_TEXT, session, done, Step("stop", "clean"), at=at(0)) == ""
    project, task = adapters.identities(repo, TASK_TEXT)
    return rec.ProjectRecord(project).runs(task)[0]


def _folder(repo) -> Path:
    return rec.ProjectRecord(rec.project_id(repo)).root


def answering(**content):
    async def handler(message, response_type, params, context):
        return dict(content)
    return handler


async def declined(message, response_type, params, context):
    return ElicitResult(action="decline")


async def cancelled(message, response_type, params, context):
    return ElicitResult(action="cancel")


def test_the_three_tools_are_registered_with_their_declarations():
    registered = asyncio.run(build_server().get_tools())
    for name in ("g6_workflow_export", "g6_workflow_correct", "g6_workflow_delete"):
        assert name in tools.TOOLS and registered[name].fn._g6_declaration == declared.TOOLS[name]
        assert "client_workflow" in declared.TOOLS[name].classes
        assert set(registered[name].parameters["properties"]) == set(declared.TOOL_ARGUMENTS[name])
        assert registered[name].description and "verified" not in registered[name].description


def test_the_workflow_source_of_a_declaration_is_the_projects_own(repo):
    refs = declared.TOOLS["g6_workflow_export"].sources({"repo": str(repo)})
    assert rec.workflow_source(rec.project_id(repo)) in {ref.source_id for ref in refs}
    assert cloud.repository_source(repo) not in {ref.source_id for ref in refs}       # a source of its own


def test_the_consent_egress_stays_outside_the_observer():
    names = [type(one).__name__ for one in build_server().middleware]
    assert names.index("ConsentEgress") < names.index("WorkflowObserver")       # first added is outermost


def test_help_names_the_file_convention_in_fixed_words():
    text = call(build_server(), "g6_help", {})
    said = json.dumps(text)
    assert "inspection.json" in said and "corrections.json" in said and "G6_WORKFLOW_RECORD=on" in said


def test_export_writes_the_file_and_returns_counts(granted, tmp_path):
    _recorded(granted, tmp_path)
    reply = call(build_server(), "g6_workflow_export", {"repo": str(granted)})
    assert reply["ok"] is True and reply["completion_state"] == "qualified-draft"
    result = reply["result"]
    assert result["status"] == "written" and (result["tasks"], result["runs"]) == (1, 1) and result["events"] == 5
    path = Path(result["file"])
    assert path == _folder(granted) / "inspection.json" and path.is_file()
    assert json.loads(path.read_text(encoding="utf-8"))["tasks"][0]["runs"][0]["rounds"] == 2
    assert "tasks" not in json.dumps(reply["result"]).replace('"tasks": 1', "")      # counts only, never the file


def test_export_refuses_what_is_not_a_repository_folder(repo):
    for bad in ("relative/path", str(repo / "missing")):
        reply = call(build_server(), "g6_workflow_export", {"repo": bad})
        assert reply["ok"] is False and "G6_E_INPUT_VALIDATION" in reply["error"]


def test_correct_reads_the_file_and_takes_no_correction_argument(granted, tmp_path):
    server = build_server()
    schema = asyncio.run(server.get_tools())["g6_workflow_correct"].parameters
    assert set(schema["properties"]) == {"repo"}
    run = _recorded(granted, tmp_path)
    target = rec.ProjectRecord(rec.project_id(granted)).events(run=run)[1].event_id
    assert call(server, "g6_workflow_correct", {"repo": str(granted)})["result"]["status"] == "absent"
    (_folder(granted) / "corrections.json").write_text(json.dumps({"schema": lifecycle.CORRECTIONS_SCHEMA, "corrections": [
        {"target": target, "action": "annotate", "note": "a retry", "by": "A person"}]}), encoding="utf-8")
    reply = call(server, "g6_workflow_correct", {"repo": str(granted)})
    assert reply["result"] == {"status": "applied", "applied": 1, "already": 0, "problems": []}
    smuggled = call(server, "g6_workflow_correct", {"repo": str(granted), "corrections": [{"action": "delete"}]})
    assert "result" not in smuggled or smuggled.get("ok") is not True          # an undeclared argument is refused


@pytest.mark.parametrize("handler,dialog", [(declined, "decline"), (cancelled, "cancel"), (None, "unavailable"),
                                            (answering(confirm="keep", name="Ada"), "accept"),
                                            (answering(confirm="delete", name="  "), "accept")])
def test_delete_needs_the_dialog(granted, tmp_path, handler, dialog):
    run = _recorded(granted, tmp_path)
    before = sorted(p.name for p in _folder(granted).rglob("*.jsonl"))
    reply = call(build_server(), "g6_workflow_delete", {"repo": str(granted), "scope": "run", "run": run},
                 handler=handler)
    assert reply["result"]["status"] == "not_changed" and reply["result"]["removed"] == 0
    assert reply["result"]["dialog"] == dialog and "corrections.json" in reply["result"]["how"]
    assert sorted(p.name for p in _folder(granted).rglob("*.jsonl")) == before
    assert lifecycle.receipts(rec.project_id(granted)) == ()


def test_delete_by_the_person_in_the_dialog_removes_the_task(granted, tmp_path):
    _recorded(granted, tmp_path)
    seen = []

    async def handler(message, response_type, params, context):
        seen.append(message)
        return {"confirm": "delete", "name": "Ada"}
    reply = call(build_server(), "g6_workflow_delete", {"repo": str(granted), "scope": "task", "task": TASK_TEXT},
                 handler=handler)
    assert reply["result"]["status"] == "deleted" and reply["result"]["receipt"]["scope"] == "task"
    assert rec.ProjectRecord(rec.project_id(granted)).tasks() == ()
    assert len(seen) == 1 and "cannot be undone" in seen[0] and TASK_TEXT not in seen[0]      # fixed words only
    assert "Ada" not in json.dumps(reply)                                    # who asked is not kept or echoed


def test_delete_by_agent_argument_is_impossible(granted, tmp_path):
    run = _recorded(granted, tmp_path)
    server = build_server()
    schema = asyncio.run(server.get_tools())["g6_workflow_delete"].parameters
    assert set(schema["properties"]) == {"repo", "scope", "task", "run"}          # nothing carries a confirmation
    for extra in ({"confirm": "delete"}, {"name": "Ada"}, {"answer": "delete"}, {"approved": True}):
        reply = call(server, "g6_workflow_delete", {"repo": str(granted), "scope": "run", "run": run, **extra})
        assert reply.get("ok") is not True and "result" not in reply
    assert rec.ProjectRecord(rec.project_id(granted)).runs(adapters.identities(granted, TASK_TEXT)[1]) == (run,)


def test_delete_through_the_corrections_file_when_there_is_no_dialog(granted, tmp_path):
    run = _recorded(granted, tmp_path)
    (_folder(granted) / "corrections.json").write_text(json.dumps({"schema": lifecycle.CORRECTIONS_SCHEMA, "corrections": [
        {"action": "delete", "scope": "run", "id": run, "by": "A person"}]}), encoding="utf-8")
    reply = call(build_server(), "g6_workflow_correct", {"repo": str(granted)})
    assert reply["result"]["applied"] == 1
    assert rec.ProjectRecord(rec.project_id(granted)).tasks() == ()
    assert lifecycle.receipts(rec.project_id(granted))[0]["scope"] == "run"


def test_the_observer_records_a_call_that_names_a_repository_and_a_task(repo, monkeypatch):
    server = build_server()
    call(server, "g6_premortem", {"repo": str(repo), "task": TASK_TEXT})
    call(server, "g6_premortem", {"repo": str(repo), "task": TASK_TEXT})
    call(server, "g6_workflow_export", {"repo": str(repo)})                  # names no task: not attributed
    project, task = adapters.identities(repo, TASK_TEXT)
    calls = [e for e in rec.ProjectRecord(project).events(task=task) if e.kind == "tool_call"]
    assert [e.data["tool"] for e in calls] == ["g6_premortem", "g6_premortem"]
    assert calls[0].data["args_sha256"] == calls[1].data["args_sha256"] and calls[0].data["seconds"] >= 0
    assert TASK_TEXT not in json.dumps([e.to_body() for e in calls])
    monkeypatch.setenv(policy.RECORD_ENV, "off")
    call(server, "g6_premortem", {"repo": str(repo), "task": "another task"})
    assert rec.ProjectRecord(project).tasks() == (task,)


def test_a_review_job_wrapper_changes_nothing_but_adds_one_note_on_failure(repo, monkeypatch):
    monkeypatch.setenv("G6_WORKFLOW_VIEW", "off")          # the record alone: the view has its own tests
    shown = {"assessment": {"report": "REPORT"}, "job_id": "j"}

    def finish(reply, job_id):
        return cloud.bind_output(dict(shown), (cloud.Source.text("fixture:review", "x", "y"),), metadata={"k": 1})
    with cloud.collect_sources():
        done = tools.finishing(finish, str(repo), TASK_TEXT)({"assessment": {"outcome": "x"}}, "job-1")
        assert done == shown and [s.source_id for s in cloud.output_sources(done)] == ["fixture:review"]
        project, task = adapters.identities(repo, TASK_TEXT)
        assert [e.kind for e in rec.ProjectRecord(project).events(task=task)] == [
            "run_started", "review_finished", "report_returned"]
        monkeypatch.setattr(rec.ProjectRecord, "start_run", lambda *a, **k: (_ for _ in ()).throw(OSError("full")))
        failed = tools.finishing(finish, str(repo), TASK_TEXT)({"assessment": {}}, "job-2")
        assert failed == {**shown, "workflow": {"note": "the workflow record was not written (OSError)"}}
        assert [s.source_id for s in cloud.output_sources(failed)] == ["fixture:review"]
        assert cloud.output_metadata(failed) == {"k": 1}
        monkeypatch.setenv(policy.RECORD_ENV, "off")
        assert tools.finishing(finish, str(repo), TASK_TEXT)({"assessment": {}}, "job-3") == shown


def test_a_loop_step_wrapper_changes_nothing_and_records_the_step(repo, tmp_path, monkeypatch):
    from g6.harness import completion_hook

    monkeypatch.setenv("G6_WORKFLOW_VIEW", "off")          # the record alone: the view has its own tests
    result = {"ok": True, "action": "stop", "report": "REPORT", "ending": "clean"}
    folder = completion_hook.folder("mcp:session-9")
    folder.mkdir(parents=True)
    # written as the loop writes it, with its completion digest: an unproduced state is withheld on the 0.3 line
    cloud.write_retained(folder / "state.json", json.dumps(loop_state(1, done=True, ending="clean")).encode("utf-8"))
    assert tools.stepped(lambda *a: dict(result), str(repo), TASK_TEXT, "session-9") == result
    project, task = adapters.identities(repo, TASK_TEXT)
    assert [e.kind for e in rec.ProjectRecord(project).events(task=task)] == [
        "run_started", "loop_round", "loop_ended", "report_returned"]
    waiting = {"ok": False, "action": "wait"}
    assert tools.stepped(lambda *a: dict(waiting), str(repo), TASK_TEXT, "session-9") == waiting
    refusal = {"ok": False, "error": "[G6_E_INPUT_VALIDATION] no"}                     # no step ran: nothing to record
    assert tools.stepped(lambda *a: dict(refusal), str(repo), "other task", "session-9") == refusal
    assert rec.ProjectRecord(project).tasks() == (task,)


def test_every_exit_of_a_workflow_tool_is_a_bound_reply():
    # inventory, from the code: each return in the three tool bodies goes through one of the reply makers
    tree = ast.parse(Path(tools.__file__).read_text(encoding="utf-8"))
    makers = {"_reply", "_refused", "_unreadable"}
    checked = 0
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in ("export", "correct", "delete"):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Return):
                    assert isinstance(inner.value, ast.Call) and getattr(inner.value.func, "id", "") in makers, node.name
                    checked += 1
    assert checked >= 9
