# SPDX-License-Identifier: Apache-2.0
"""No ungranted workflow datum reaches a cloud client (design 17.3 and 16; local contract 4 of 17.5): the lifecycle
tools do their local work and the reply is withheld, with one fixed sentence about the files and nothing else."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("fastmcp")

from g6.core import cloud_consent as cloud  # noqa: E402
from g6.harness.workflow import adapters, lifecycle, policy  # noqa: E402
from g6.harness.workflow import record as rec  # noqa: E402
from g6.mcp import build_server  # noqa: E402
from g6.mcp import workflow as tools  # noqa: E402
from tests.workflow.kit import Step, at, call, loop_state, make_repo  # noqa: E402

MARK = "ZEBRA-MARKER-7731"
TASK_TEXT = f"make the {MARK} count stable"
CALLS = (("g6_workflow_export", {}), ("g6_workflow_correct", {}),
         ("g6_workflow_delete", {"scope": "task", "task": TASK_TEXT}),
         ("g6_workflow_delete", {"scope": "project"}), ("g6_workflow_delete", {"scope": "run", "run": "f" * 32}))


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / f"home-{MARK}"))
    monkeypatch.setenv(policy.RECORD_ENV, "on")
    folder = make_repo(tmp_path / f"repo-{MARK}")
    session = tmp_path / "session-a"
    session.mkdir()
    done = loop_state(2, done=True, ending=MARK)            # the marker in a run's data
    assert adapters.observe_loop(folder, TASK_TEXT, session, done, Step("stop", MARK), at=at(0)) == ""
    return folder


def _project(repo) -> str:
    return rec.project_id(repo)


def _plant_correction(repo) -> None:
    record = rec.ProjectRecord(_project(repo))
    target = record.events()[1].event_id
    (record.root / "corrections.json").write_text(json.dumps({"schema": lifecycle.CORRECTIONS_SCHEMA, "corrections": [
        {"target": target, "action": "annotate", "note": MARK, "by": MARK}]}), encoding="utf-8")


def test_planted_marker_never_reaches_an_ungranted_reply(repo, tmp_path):
    _plant_correction(repo)
    server = build_server()
    for client in ("codex", "claude-code", "some-other-client"):
        for tool, arguments in CALLS:
            reply = call(server, tool, {"repo": str(repo), **arguments}, client=client)
            said = json.dumps(reply)
            assert MARK not in said, (client, tool)                    # bar: zero
            assert reply.get("ok") is False and reply["completion_state"] == "blocked-escalated"
            assert "result" not in reply
    # and nothing was deleted by an agent that could not show a dialog
    assert rec.ProjectRecord(_project(repo)).tasks() != ()


def test_withheld_reply_names_no_path_and_no_project_id(repo, tmp_path):
    reply = call(build_server(), "g6_workflow_export", {"repo": str(repo)})
    said = json.dumps(reply)
    assert reply["workflow_files"] == tools.FILES and reply["cloud_consent_code"] == cloud.REQUIRED
    assert "withheld from a source not shared with this provider" in said
    for hidden in (_project(repo), str(tmp_path), "g6-home", "home-", "\\\\", "/workflow/", rec.workflow_source(_project(repo))):
        assert hidden not in said.replace(tools.FILES, ""), hidden
    assert _project(repo) not in tools.FILES and "/" not in tools.FILES and "\\" not in tools.FILES


def test_local_work_still_happens_without_a_grant(repo):
    _plant_correction(repo)
    server = build_server()
    folder = rec.ProjectRecord(_project(repo)).root
    assert call(server, "g6_workflow_export", {"repo": str(repo)})["ok"] is False
    assert (folder / "inspection.json").is_file()                      # the person can look without sharing
    assert call(server, "g6_workflow_correct", {"repo": str(repo)})["ok"] is False
    task = adapters.identities(repo, TASK_TEXT)[1]
    assert len(rec.ProjectRecord(_project(repo)).annotations(task)) == 1


def test_revocation_withholds_the_next_reply_and_the_cached_export_path(repo, accepted_cloud_dialog):
    source = rec.workflow_source(_project(repo))
    accepted_cloud_dialog(source, "openai:codex", text="workflow record")
    server = build_server()
    shared = call(server, "g6_workflow_export", {"repo": str(repo)})
    assert shared["ok"] is True and shared["result"]["file"].endswith("inspection.json")
    assert cloud.disclosures()[-1]["provider"] == "openai:codex"
    cloud.revoke(source, "openai:codex")
    after = call(server, "g6_workflow_export", {"repo": str(repo)})
    assert after["ok"] is False and "inspection.json" not in json.dumps(after).replace(tools.FILES, "")
    assert shared["result"]["file"] not in json.dumps(after)
    other = call(server, "g6_workflow_export", {"repo": str(repo)}, client="claude-code")
    assert other["ok"] is False                                         # a grant for one provider covers no other


def test_grant_for_the_repository_does_not_cover_the_workflow_source(repo, accepted_cloud_dialog):
    accepted_cloud_dialog(cloud.repository_source(repo), "openai:codex", text="the repository")
    for tool, arguments in CALLS[:2]:
        assert call(build_server(), tool, {"repo": str(repo), **arguments})["ok"] is False


def test_grant_for_one_project_does_not_cover_another(repo, tmp_path, accepted_cloud_dialog):
    other = make_repo(tmp_path / "second-repo")
    accepted_cloud_dialog(rec.workflow_source(_project(other)), "openai:codex", text="workflow record")
    assert call(build_server(), "g6_workflow_export", {"repo": str(other)})["ok"] is True
    assert call(build_server(), "g6_workflow_export", {"repo": str(repo)})["ok"] is False


def test_every_workflow_reply_field_is_declared(repo):
    """The class "a producer returns data with no declared source": every reply the tools make, on every branch a
    record can take them, is bound to the project's workflow source (or is the fixed refusal of a bad argument)."""
    import asyncio
    from types import SimpleNamespace

    source = rec.workflow_source(_project(repo))

    def ctx(action, content=None):
        async def elicit_form(**kwargs):
            return SimpleNamespace(action=action, content=content)
        return SimpleNamespace(session=SimpleNamespace(elicit_form=elicit_form), request_id="r")

    def replies():
        yield tools.export(str(repo))
        yield tools.correct(str(repo))                                  # no corrections file
        _plant_correction(repo)
        yield tools.correct(str(repo))                                  # applied
        yield asyncio.run(tools.delete(str(repo), ctx("decline"), scope="project"))
        yield asyncio.run(tools.delete(str(repo), ctx("accept", {"confirm": "delete", "name": "P"}), scope="task",
                                       task=TASK_TEXT))
        run = rec.ProjectRecord(_project(repo)).root / "tasks"
        run.mkdir(parents=True, exist_ok=True)
        (run / ("a" * 64)).mkdir()
        (run / ("a" * 64) / "runs").mkdir()
        (run / ("a" * 64) / "runs" / ("b" * 32 + ".jsonl")).write_text("torn", encoding="utf-8")
        yield tools.export(str(repo))                                   # a part of the record is unreadable
        yield tools.correct(str(repo))

    with cloud.collect_sources():
        seen = 0
        for reply in replies():
            bound = cloud.output_sources(reply)
            assert bound is not None and source in {ref.source_id for ref in bound}, reply.get("result")
            assert cloud.output_metadata(reply) == {"workflow_files": tools.FILES}
            assert set(reply) >= {"ok", "result", "completion_state", "warning_card", "evidence"}
            seen += 1
        assert seen == 7
        for bad in (tools.export("relative"), tools.correct(""),
                    asyncio.run(tools.delete(str(repo), ctx("accept"), scope="everything"))):
            assert cloud.output_sources(bad) == () and bad["ok"] is False and "G6_E_INPUT_VALIDATION" in bad["error"]
