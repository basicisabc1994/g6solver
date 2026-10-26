# SPDX-License-Identifier: Apache-2.0
"""Plan critique (audit 018): placeholders are missing, the tripwire never blocks alone, merges only tighten. Offline."""
from __future__ import annotations

import pytest

from g6.invariants import check_envelope
from g6.reliability_pipeline.plan import (CritiqueFinding, CritiqueReport, Plan, critique, merge_reports,
                                          stricter_verdict)

FULL = dict(acceptance_criteria=["the loader reads the new key"], tests=["tests/test_config.py passes"],
            rollback="git revert the commit")


def test_a_complete_honest_plan_passes_as_a_draft() -> None:
    report = critique(Plan(goal="Add a timeout setting to the HTTP client", steps=["add the field", "wire it"], **FULL))
    assert report.verdict == "pass" and report.completion_state == "qualified-draft"
    envelope = report.to_envelope(request_id="r1")
    assert check_envelope(envelope) == [] and envelope["warning_card"]["code"] == "G6_E_PLAN_STRUCTURE_ONLY"


def test_pc001_risky_words_ask_for_a_revision_and_never_block_alone() -> None:
    """The archive blocked an honest plan for saying "production" and matched "reproduction" as a substring."""
    honest = critique(Plan(goal="Update the production config loader to read the new key", **FULL))
    assert honest.verdict == "revise" and [f.source for f in honest.findings] == ["tripwire"]
    assert critique(Plan(goal="Write a reproduction script for the flaky upload test", **FULL)).verdict == "pass"


@pytest.mark.parametrize("filler", [".", "x", "TBD", "n/a", "todo"])
def test_pc002_placeholders_are_not_criteria_tests_or_rollback(filler: str) -> None:
    report = critique(Plan(goal="Add a timeout setting to the HTTP client", acceptance_criteria=[filler],
                           tests=[filler], rollback=filler))
    assert {f.category for f in report.findings} == {"missing_success_criteria", "missing_evaluation",
                                                     "missing_rollback"}
    assert report.completion_state == "blocked-escalated"


def test_pc003_anything_but_pass_tells_the_caller_to_stop() -> None:
    revise = critique(Plan(goal="Fix it", **FULL))
    assert revise.verdict == "revise" and revise.completion_state == "blocked-escalated"
    assert check_envelope(revise.to_envelope()) == []


def test_pc005_a_merged_critical_finding_blocks_whatever_the_other_verdict_said() -> None:
    floor = critique(Plan(goal="Add a timeout setting to the HTTP client", **FULL))
    model = CritiqueReport(verdict="revise", findings=[CritiqueFinding(category="unsafe_action", severity="critical",
                                                                       what="Retries could double-charge.")])
    assert merge_reports(floor, model).verdict == "block"
    lying = CritiqueReport(verdict="pass", findings=model.findings)
    assert merge_reports(floor, lying).verdict == "block"


def test_pc010_one_finding_per_category_with_the_others_text_as_a_note() -> None:
    floor = critique(Plan(goal="Add a timeout setting to the HTTP client", acceptance_criteria=["the loader works"],
                          rollback="git revert the commit"))
    other = CritiqueReport(verdict="revise", findings=[CritiqueFinding(category="missing_evaluation",
                                                                       what="No tests are listed.")])
    merged = merge_reports(floor, other)
    evaluation = [f for f in merged.findings if f.category == "missing_evaluation"]
    assert len(evaluation) == 1 and evaluation[0].notes == ("No tests are listed.",)


def test_a_merge_can_never_loosen_the_floor() -> None:
    floor = critique(Plan(goal="Fix it"))
    assert merge_reports(floor, CritiqueReport(verdict="pass")).verdict == floor.verdict
    assert stricter_verdict("pass", "maybe") == "block"
