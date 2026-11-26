"""The three existing routes carry the same card bytes for the same recorded state."""
from __future__ import annotations

import json

from g6.harness import completion_hook, completion_loop, patch_report
from g6.harness.workflow import adapters, evidence
from g6.mcp import review_reply
from tests.harness.test_evidence_card import assessment, recorded
from tests.workflow.kit import Step, at, event, make_repo, sha


def test_review_reply_compaction_keeps_the_card_whole():
    value, _ = recorded(assessment())
    card = patch_report.card(value, None)
    value.update(card=card, report=patch_report.render(value))
    reply = review_reply.compact({"assessment": value, "card": card}, "job", None)
    assert json.dumps(reply["card"]) == json.dumps(reply["assessment"]["card"])
    assert reply["assessment"]["report"].splitlines()[1:6] == patch_report.card_lines(card)


def test_mcp_loop_route_keeps_the_same_card_bytes(tmp_path, monkeypatch):
    from contextlib import nullcontext

    from g6.mcp import review_loop

    value, _ = recorded(assessment())
    expected = patch_report.card(value, None)
    text = patch_report.render(value)
    session = tmp_path / "session"
    state = {"reviews": [], "final_assessment": value, "workflow_evidence": value["workflow_evidence"]}
    monkeypatch.setattr(completion_hook, "folder", lambda _: session)
    monkeypatch.setattr(completion_loop, "_held", lambda _: nullcontext())
    monkeypatch.setattr(review_loop, "_asked", lambda *a: {"repo": "fixture", "task": "task", "base": "base"})
    monkeypatch.setattr(completion_loop, "advance", lambda *a, **k: completion_loop.Step("stop", text, ending="open"))
    monkeypatch.setattr(completion_loop, "_load", lambda _: state)
    result = review_loop._step("fixture", "task", "session")
    assert json.dumps(result["card"]) == json.dumps(expected)
    assert result["report"].splitlines()[1:6] == patch_report.card_lines(expected)


def test_same_report_reaches_the_hook_without_card_rewriting(tmp_path, monkeypatch):
    value, _ = recorded(assessment())
    text = patch_report.render(value)
    session = tmp_path / "session"
    session.mkdir()
    (session / "task.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(completion_hook, "folder", lambda _: session)
    monkeypatch.setattr(completion_hook, "current", lambda *a: session)
    monkeypatch.setattr(completion_hook, "_task", lambda _: {"repo": "fixture", "task": "task", "base": "base"})
    monkeypatch.setattr(completion_loop, "advance", lambda *a, **k: completion_loop.Step("stop", text))
    monkeypatch.setattr(adapters, "observe_step", lambda *a, **k: "")
    monkeypatch.setenv("G6_WORKFLOW_VIEW", "off")
    assert completion_hook.on_stop({"session_id": "s", "cwd": "fixture"})["systemMessage"] == text


def test_loop_card_counts_and_same_case_rule(home, tmp_path):
    repo = make_repo(tmp_path / "repo")
    first = {"n": 1, "file": "case", "patch": sha("first"), "results": {"case": "failed"},
             "verdict": "defect", "reviewer": "codex", "boundary": "appcontainer"}
    last = {"n": 2, "file": "second", "patch": sha("second"), "results": {"case": "passed"},
            "verdict": "clean", "reviewer": "codex", "boundary": "appcontainer"}
    state = {"reviews": [first, last], "rounds": [], "agent": "claude-opus-4"}
    session = tmp_path / "session"
    adapters.observe_loop(repo, "fixture task", session, state, Step("stop", "fixed"), at=at())
    held = evidence.read(repo, "fixture task")
    loop = {**state, "workflow_evidence": held}
    card = patch_report.card({}, loop)
    assert card["caught"]["reproduced"] == card["repaired"] == 1
    assert card["open"] == 0 and card["checked"]["runs"][0]["count"] == 2
    assert card["justification"] == held["justification"]


def test_mismatched_event_body_is_never_cited():
    held = evidence.bundle([event("a" * 32, "review_finished", data={"reproduced": 1})])
    held["events"][0]["data"]["reproduced"] = 99
    card = patch_report.card({"workflow_evidence": held}, None)
    assert card["justification"] == [] and card["caught"]["reproduced"] is None


def test_unset_display_does_not_disable_internal_analysis(home, tmp_path, monkeypatch):
    from g6.harness.workflow import view

    repo = make_repo(tmp_path / "repo")
    project, task = adapters.identities(repo, "fixture task")
    monkeypatch.delenv(view.VIEW_ENV, raising=False)
    built = view.build(project, task, provider=None)
    assert built == {"level": "narrow", "withheld": True, "policy_version": "workflow-storage.v1"}
    assert view.appended(repo, "fixture task", "MANDATORY REPORT", provider=None) == "MANDATORY REPORT"


def test_refresh_preserves_a_producers_report_and_custody(home, tmp_path):
    from g6.core import cloud_consent as cloud
    from g6.mcp import workflow

    repo = make_repo(tmp_path / "repo")
    value = assessment()
    value["report"] = "# Custom report\n\nEvery original finding remains here."
    with cloud.collect_sources():
        reply = cloud.bind_output({"assessment": value}, (cloud.Source.text("fixture:review", "review", "data"),))
        shown = workflow.finishing(lambda result, job: result, str(repo), "fixture task")(reply, "job")
        assert shown["assessment"]["report"].startswith("# Custom report\n- **Checked:**")
        assert "Every original finding remains here." in shown["assessment"]["report"]
        assert "card" not in value
        assert any(s.source_id == "fixture:review" for s in cloud.output_sources(shown))
