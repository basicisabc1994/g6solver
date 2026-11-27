"""Workflow observations are a companion to the issuer's unchanged payload."""
from __future__ import annotations

from types import SimpleNamespace

from g6.workforce import coding_certificate
from g6.harness.workflow import evidence
from tests.workflow.kit import event


def test_task_justification_is_read_only_and_explicit_when_absent(monkeypatch):
    missions = SimpleNamespace(get=lambda _: {"venture_id": "fixture", "objective": "fixture task"})
    monkeypatch.setattr(coding_certificate.bench, "_repo", lambda *a: "fixture-repository")
    monkeypatch.setattr(evidence, "read", lambda *a: evidence.bundle([]))
    assert coding_certificate.workflow_justification(missions, "mission", {"repository": "repo"}) == {
        "events": [], "note": "no recorded events for this task"}


def test_only_the_missions_repository_and_task_are_read(monkeypatch):
    seen = []
    one = event("a" * 32, "review_finished")
    missions = SimpleNamespace(get=lambda _: {"venture_id": "fixture", "objective": "fixture task"})
    monkeypatch.setattr(coding_certificate.bench, "_repo", lambda *a: "fixture-repository")
    monkeypatch.setattr(evidence, "read", lambda *a: seen.append(a) or evidence.bundle([one]))
    result = coding_certificate.workflow_justification(missions, "mission", {"repository": "repo"})
    assert seen == [("fixture-repository", "fixture task")]
    assert result["events"] == evidence.bundle([one])["justification"]
