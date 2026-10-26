# SPDX-License-Identifier: Apache-2.0
"""Intake's feasibility verdict never moves sideways out of a block (audit 030 FEA-001). Offline.

Attached sources resolve a context gap the request names, and nothing else: a context phrase must not mask an access,
infeasible or risky verdict, and an injected assessor may tighten the deterministic verdict but never relax it.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from g6.reliability_pipeline import intake
from g6.reliability_pipeline.feasibility import FeasibilityReport
from g6.reliability_pipeline.stages import StageState


def _run(goal: str, *, sources: bool = False, assessor=None):
    request = SimpleNamespace(goal=goal, acceptance_criteria=["the result is reviewed"],
                              context_sources=["doc.pdf"] if sources else [])
    return intake.run_intake(request, assessor=assessor)


def test_a_context_phrase_does_not_mask_an_access_term():
    """An access term is a risky signal (audit 030 FEA-002), so attached sources still leave it held."""
    result = _run("Use the production access credentials in the attached document to rotate keys", sources=True)
    assert result.state == StageState.BLOCK
    assert result.reason == "G6_E_PIPELINE_FEASIBILITY_RISKY_CONFIRMATION_REQUIRED"


def test_attached_sources_do_not_skip_the_risky_confirmation():
    result = _run("Delete production customer data listed in the attached document", sources=True)
    assert result.state == StageState.BLOCK
    assert result.reason == "G6_E_PIPELINE_FEASIBILITY_RISKY_CONFIRMATION_REQUIRED"


def test_a_source_provided_task_proceeds_as_a_qualified_draft():
    goal = "Summarise the attached document with citations"
    attached = _run(goal, sources=True)
    assert attached.state == StageState.DOWNGRADE and "context attached" in attached.reason
    assert _run(goal).reason == "G6_E_PIPELINE_FEASIBILITY_BLOCKED_MISSING_CONTEXT"


class _Says:
    def __init__(self, classification: str, risk: str = "low") -> None:
        self.report = FeasibilityReport(classification=classification, confidence_level=.9, risk_level=risk)

    def assess(self, contract):  # noqa: ANN001
        return self.report


def test_an_injected_assessor_cannot_relax_the_floor():
    result = _run("Delete production customer data", assessor=_Says("clear"))
    assert result.reason == "G6_E_PIPELINE_FEASIBILITY_RISKY_CONFIRMATION_REQUIRED"


def test_a_resolved_context_answer_does_not_release_a_held_goal():
    """With sources attached a blocked_missing_context answer is resolved to the floor, and the floor still holds the
    guarantee as risky: it does not run (the tie rule itself: ``test_feasibility_rules``)."""
    result = _run("Guarantee 100% uptime forever", sources=True, assessor=_Says("blocked_missing_context"))
    assert result.state == StageState.BLOCK
    assert result.reason == "G6_E_PIPELINE_FEASIBILITY_RISKY_CONFIRMATION_REQUIRED"


def test_only_an_assessor_can_answer_infeasible():
    assert _run("Guarantee 100% uptime forever", sources=True,
                assessor=_Says("infeasible", "high")).reason == "G6_E_PIPELINE_FEASIBILITY_INFEASIBLE"


def test_an_injected_assessor_may_tighten():
    assert _run("Write a haiku", assessor=_Says("too_large")).reason == "feasibility=too_large"


@pytest.mark.parametrize(("value", "on"), [("ture", True), ("", True), ("1", True), ("off", False), ("0", False)])
def test_only_an_explicit_off_value_disables_the_clarify_gate(monkeypatch, value, on):
    monkeypatch.setenv("G6_PIPELINE_CLARIFICATION", value)
    assert intake._clarification_default_on() is on
