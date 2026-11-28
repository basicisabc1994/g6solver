"""Observed card fields and inert rendering; no reviewer is called."""
from __future__ import annotations

import copy
import json

import pytest

from g6.harness import patch_report as report
from g6.harness.workflow import evidence
from tests.workflow.kit import event, sha


def assessment():
    return {"outcome": "defect_detected", "requirements": [{"id": "R1"}],
            "requirements_without_adequate_check": ["R1"], "unknowns": ["unknown"],
            "reasons": [], "not_checked": ["behaviour no stated check exercises"],
            "scope": {"artifact_digest": sha("patch"), "base_commit": "b" * 40,
                      "environment": {"isolation": {"backend": "appcontainer"}},
                      "search": {"model_calls": 0},
                      "checks_run": [{"tier": 1, "tests": {"test_a::test_one": False}, "verdict": "failed"}],
                      "checks_skipped": [{"tier": 3}]},
            "completion": {"defects_reproduced": {"stated_and_repository_checks": [{"check": "test_a::test_one"}]},
                           "open_questions": [{"requirement": "R1"}]}}


def recorded(value):
    one = event("a" * 32, "review_finished", data=evidence.review_data(value))
    value["workflow_evidence"] = evidence.bundle([one])
    return value, one


def test_card_counts_rederive_from_cited_event_alone():
    value, one = recorded(assessment())
    before = copy.deepcopy(value)
    card = report.card(value, None)
    assert card["caught"]["reproduced"] == one.data["reproduced"] == 1
    assert card["not_checked"]["not_established"] == one.data["not_established"] == 1
    assert card["not_checked"]["skipped"] == one.data["checks_skipped"] == 1
    assert card["needs_you"]["questions"] == one.data["questions"] == 1
    assert card["needs_you"]["unknowns"] == one.data["unknowns"] == 1
    assert card["repaired"] is None
    assert card["justification"] == evidence.bundle([one])["justification"]
    assert value == before
    assert json.dumps(report.card(value, None), sort_keys=True) == json.dumps(card, sort_keys=True)


@pytest.mark.parametrize("status", ["off", "write_failed", "unavailable"])
def test_no_citable_events_make_no_numerical_claim(status):
    value = assessment()
    value["workflow_evidence"] = evidence.bundle([], status=status)
    card = report.card(value, None)
    assert card["justification"] == [] and card["caught"]["reproduced"] is None
    assert card["checked"]["runs"] == []
    assert status in card["checked"]["reason"]
    assert "record" in "\n".join(report.card_lines(card)).lower()


def test_headline_and_standing_note_stay_and_card_is_shared():
    value, _ = recorded(assessment())
    lines = report.render(value).splitlines()
    card_lines = report.card_lines(report.card(value, None))
    assert "Qualified draft" in lines[0]
    assert lines[1:6] == card_lines
    assert report.STANDING_NOTE in lines
    text = report.completion_report({"assessment": value}, report.render(value))
    assert text.splitlines()[1:6] == card_lines


@pytest.mark.parametrize(("agent", "reviewer", "expected"), [
    ("", "codex", "a second model"), ("codex", "gpt-6", "a second model"),
    ("claude-opus-4", "codex", "another model family"),
    ("mystery-a", "mystery-b", "a second model")])
def test_family_is_only_asserted_when_known(agent, reviewer, expected):
    assert report.model_relation(agent, reviewer) == expected


def test_nothing_ran_keeps_the_existing_reason():
    value = {"unchecked_reason": "No Python changed. Nothing was tested."}
    lines = report.card_lines(report.card(value, None))
    assert lines[0] == "- **Checked:** nothing this turn. No Python changed. Nothing was tested."


def test_repository_text_only_occurs_in_code_spans():
    value, _ = recorded(assessment())
    value["scope"]["artifact_digest"] = "repo`\n# injection"
    text = "\n".join(report.card_lines(report.card(value, None)))
    assert "\n# injection" not in text
    assert "repo`\n# injection" not in text
    assert "repo` # injection" not in text
