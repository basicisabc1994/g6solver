"""Citations resolve to task-local record entries before rendering."""
from __future__ import annotations

import json

from g6.harness import patch_report
from g6.harness.workflow import adapters, evidence
from g6.harness.workflow.record import ProjectRecord
from tests.harness.test_evidence_card import assessment
from tests.workflow.kit import Step, at, make_repo, sha


def test_finished_review_records_counts_without_task_or_check_text(home, tmp_path):
    repo = make_repo(tmp_path / "repo")
    value = assessment()
    reply = {"assessment": value, "artifact_digest": sha("patch")}
    assert adapters.observe_review(repo, "fixture task", "job", reply, started_at=at(), finished_at=at(1)) == ""
    project, task = adapters.identities(repo, "fixture task")
    rows = ProjectRecord(project).events(task=task)
    snapshot = evidence.read(repo, "fixture task")
    value["workflow_evidence"] = snapshot
    cited = patch_report.card(value, None)["justification"]
    known = {e.event_id: e for e in rows}
    assert cited and all(c["event_id"] in known for c in cited)
    assert cited == evidence.bundle(rows)["justification"]
    text = json.dumps([e.to_body() for e in rows])
    assert "fixture task" not in text and "test_a::test_one" not in text


def test_unchecked_replay_is_one_event_with_a_stable_code(home, tmp_path):
    repo = make_repo(tmp_path / "repo")
    session = tmp_path / "session"
    step = Step("stop", "untested", "G6_E_NO_BOUNDARY")
    for _ in range(2):
        assert adapters.observe_unchecked(repo, "fixture task", session, step, at=at()) == ""
    rows = evidence.read(repo, "fixture task")["events"]
    unchecked = [e for e in rows if e["kind"] == "turn_unchecked"]
    assert len(unchecked) == 1 and unchecked[0]["data"]["reason"] == step.code


def test_failed_write_has_no_citations(home, tmp_path, monkeypatch):
    from g6.mcp import workflow

    repo = make_repo(tmp_path / "repo")
    reply = {"assessment": assessment()}
    monkeypatch.setattr(adapters, "observe_review", lambda *a, **k: "write failed")
    seen = []
    workflow.finishing(lambda result, job: seen.append(result) or result, str(repo), "fixture task")(reply, "job")
    assert seen[0]["assessment"]["card"]["justification"] == []
    assert "write_failed" in seen[0]["assessment"]["card"]["checked"]["reason"]


def test_unshared_record_does_not_withhold_the_mandatory_review(home, tmp_path):
    from g6.core import cloud_consent as cloud

    repo = make_repo(tmp_path / "repo")
    assert adapters.observe_review(repo, "fixture task", "job", {"assessment": assessment()},
                                   started_at=at(), finished_at=at(1)) == ""
    with cloud.collect_sources() as outer:
        outer.client = ("openai:codex", None)
        with cloud.collect_sources():
            held = evidence.read(repo, "fixture task")
        assert held["justification"] == []
        assert held["events"] == []
        assert held["status"] == "withheld"
        assert not any(s.source_id.startswith("workflow:") for s in outer.sources)
    project, task = adapters.identities(repo, "fixture task")
    assert len(ProjectRecord(project).events(task=task)) == 3
