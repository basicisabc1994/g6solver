"""The end-of-task card reaches the MCP review reply and the Stop hook report, and claims only recorded events."""
from __future__ import annotations

import re

from g6.harness import completion_hook, completion_loop, patch_report
from g6.harness.workflow import adapters, evidence
from g6.harness.workflow.record import ProjectRecord
from tests.workflow.kit import make_repo, sha

LABELS = ("Checked", "Caught", "Repaired", "Not checked", "Needs you")
TASK = "fixture task"


def _assessment() -> dict:
    """A decided review with one failed stated check; its report is the producer's own rendering."""
    value = {"outcome": "defect_detected", "requirements": [{"id": "R1"}],
             "requirements_without_adequate_check": ["R1"], "unknowns": ["unknown"],
             "reasons": [], "not_checked": ["behaviour no stated check exercises"],
             "scope": {"artifact_digest": sha("patch"), "base_commit": "b" * 40,
                       "environment": {"isolation": {"backend": "appcontainer"}},
                       "search": {"model_calls": 0},
                       "checks_run": [{"tier": 1, "tests": {"test_a::test_one": False}, "verdict": "failed"}],
                       "checks_skipped": [{"tier": 3}]},
             "completion": {"defects_reproduced": {"stated_and_repository_checks": [{"check": "test_a::test_one"}]},
                            "open_questions": [{"requirement": "R1"}]}}
    value["report"] = patch_report.safe_render(value)
    return value


def _card_block(report: str) -> list[str]:
    return report.splitlines()[1:6]


def _poll(repo, value: dict) -> tuple[dict, dict]:
    """One g6_review_patch job as the MCP server runs it (``workflow.finishing(review_reply.finish)``), polled twice."""
    from g6.mcp import review_reply, workflow
    from g6.metaharness.jobs import Jobs

    jobs = Jobs()
    try:
        started = jobs.start(lambda: {"assessment": value}, compact=workflow.finishing(review_reply.finish, str(repo), TASK))
        jobs._jobs[started["job_id"]].result(timeout=60)
        return jobs.status(started["job_id"]), jobs.status(started["job_id"], detail="full")
    finally:
        jobs.close()


def _stored_ids(repo) -> set[str]:
    project, task = adapters.identities(repo, TASK)
    record = ProjectRecord(project)
    return {e.event_id for e in (*record.events(task=task), *record.annotations(task))}


def test_plain_review_patch_reply_carries_the_card(home, tmp_path):
    repo = make_repo(tmp_path / "repo")
    compact, full = _poll(repo, _assessment())
    for polled in (compact, full):
        result = polled["result"]
        card = result["card"]
        assert result["assessment"]["card"] == card
        lines = _card_block(result["assessment"]["report"])
        assert [line.split(":**")[0] for line in lines] == [f"- **{label}" for label in LABELS]
        assert lines == patch_report.card_lines(card)
        # Every cited event is one G6 stored for this task; the card names each of them.
        cited = {one["event_id"] for one in card["justification"]}
        assert cited and cited <= _stored_ids(repo)
        assert all(f"`{ident}`" in lines[4] for ident in cited)
        assert card["caught"]["reproduced"] == 1    # the one stated check that failed, and nothing else


def test_card_fields_are_the_cited_events_own_data(home, tmp_path):
    repo = make_repo(tmp_path / "repo")
    result = _poll(repo, _assessment())[0]["result"]
    card = result["card"]
    held = evidence.read(repo, TASK)
    finished = [body for body in evidence.matched(held) if body["kind"] == "review_finished"]
    assert len(finished) == 1
    data = finished[0]["data"]
    assert card["checked"]["runs"] == [{"what": "stated checks", "count": data["stated_checks"],
                                        "by": "repository_tests", "isolation": data["isolation"]}]
    assert card["checked"]["artifact"] == finished[0]["artifact_sha256"]
    assert card["caught"] == {"reproduced": data["reproduced"], "integrity": data["integrity_reasons"],
                              "unconfirmed": data["unconfirmed"]}
    assert card["repaired"] is None    # one review: no observation of a different artifact, so no repair is claimed
    assert card["not_checked"]["not_established"] == data["not_established"]
    assert card["not_checked"]["skipped"] == data["checks_skipped"]
    assert card["needs_you"]["questions"] == data["questions"]
    assert card["needs_you"]["unknowns"] == data["unknowns"]


def _no_claims(lines: list[str]) -> None:
    text = "\n".join(lines)
    assert not re.search(r"\b\d+\b", re.sub(r"`[^`]*`", "", text)), text
    assert "reproduced findings" not in text and "Record events: `" not in text


def test_record_off_card_claims_nothing_beyond_not_checked(home, tmp_path, monkeypatch):
    monkeypatch.setenv("G6_WORKFLOW_RECORD", "off")
    repo = make_repo(tmp_path / "repo")
    result = _poll(repo, _assessment())[0]["result"]
    card = result["card"]
    assert card["justification"] == [] and card["checked"]["runs"] == []
    assert card["caught"]["reproduced"] is None and card["repaired"] is None and card["open"] is None
    lines = _card_block(result["assessment"]["report"])
    assert lines[0].startswith("- **Checked:** not recorded.")
    _no_claims(lines)


def test_empty_record_card_claims_nothing_beyond_not_checked():
    for status in ("kept", "off", "withheld", "write_failed", "unavailable"):
        card = patch_report.card({"workflow_evidence": evidence.bundle([], status=status)}, None)
        assert card["justification"] == [] and card["caught"]["reproduced"] is None
        _no_claims(patch_report.card_lines(card))


def test_stop_hook_report_carries_the_card(home, tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "repo")
    value = _assessment()
    monkeypatch.setattr(completion_loop, "review_patch", lambda *a, **k: {"assessment": value})
    session = tmp_path / "session"
    session.mkdir()
    (session / "task.json").write_text("{}", encoding="utf-8")
    state = {"sets": {"scout": {"left_out": False, "refused": [], "when": "before"}}, "ran": {"scout": 4},
             "rounds": [{"failing": ["case_a"], "tree": None}], "boundary": "appcontainer"}
    text = completion_loop._report(repo, TASK, "HEAD", state, "no probe set could run", session)
    monkeypatch.setattr(completion_hook, "folder", lambda _: session)
    monkeypatch.setattr(completion_hook, "current", lambda *a: session)
    monkeypatch.setattr(completion_hook, "_task", lambda _: {"repo": str(repo), "task": TASK, "base": "HEAD"})
    monkeypatch.setattr(completion_loop, "advance", lambda *a, **k: completion_loop.Step("stop", text))
    monkeypatch.setattr(adapters, "observe_step", lambda *a, **k: "")
    monkeypatch.setenv("G6_WORKFLOW_VIEW", "off")
    said = completion_hook.on_stop({"session_id": "s", "cwd": str(repo)})["systemMessage"]
    lines = _card_block(said)
    assert [line.split(":**")[0] for line in lines] == [f"- **{label}" for label in LABELS]
    assert "4 model probes" in lines[0]
    cited = {one["event_id"] for one in state["workflow_evidence"]["justification"]}
    assert cited and cited <= _stored_ids(repo)
    assert all(f"`{ident}`" in lines[4] for ident in cited)
    # The producer's own card is replaced, never shown twice.
    assert said.count("- **Checked:**") == 1
