# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from types import SimpleNamespace

from g6.reliability_pipeline.intake import run_intake
from g6.reliability_pipeline.stages import StageState


def test_intake_uses_intent_answers_and_feasibility() -> None:
    result = run_intake(SimpleNamespace(
        goal="add CSV export",
        intent_answers={
            "why": "users need reports",
            "success_criteria": ["selected rows download"],
            "risk_tolerance": "low",
        },
    ))

    assert result.state == StageState.ADVANCE
    assert result.metadata["task_contract"].intent.success_criteria == ["selected rows download"]
    assert result.metadata["feasibility_report"].classification == "clear"


def test_intake_blocks_risky_task_until_user_accepts_intent() -> None:
    result = run_intake(SimpleNamespace(
        goal="delete production payment records",
        intent_answers={"why": "cleanup", "success_criteria": ["records removed"]},
    ))

    assert result.state == StageState.BLOCK
    assert result.reason == "G6_E_PIPELINE_FEASIBILITY_RISKY_CONFIRMATION_REQUIRED"
    assert result.metadata["feasibility_report"].classification == "risky"
    assert len(result.metadata["reduction_to_practice_questions"]) == 9


def test_intake_holds_a_destructive_task_named_with_one_risk_axis() -> None:
    result = run_intake(SimpleNamespace(goal="wipe the staging disk and drop table users",
                                        acceptance_criteria=["the disk is empty"], declined_clarification=True))
    assert result.reason == "G6_E_PIPELINE_FEASIBILITY_RISKY_CONFIRMATION_REQUIRED"


def test_the_premortem_names_each_failure_mode_by_its_canonical_code() -> None:
    import g6.reliability_pipeline as rp
    from g6.core.failure_modes import FailureModeRegistry
    from g6.reliability_pipeline.premortem import PREMORTEM_FAILURE_MODES, premortem_failure_mode_questions

    assert [c for c in PREMORTEM_FAILURE_MODES if str(FailureModeRegistry.get(c).code) != c] == []
    assert set(PREMORTEM_FAILURE_MODES) == set(rp.__g6_mitigates__) and len(PREMORTEM_FAILURE_MODES) == 13
    assert list(premortem_failure_mode_questions()) == list(PREMORTEM_FAILURE_MODES)


def test_intake_surfaces_reduction_to_practice_after_risky_intent_acceptance() -> None:
    result = run_intake(SimpleNamespace(
        goal="delete production payment records",
        intent_answers={"why": "cleanup", "success_criteria": ["records removed"]},
        accepted_risky_intent=True,
    ))

    assert result.state == StageState.DOWNGRADE
    assert result.metadata["feasibility_report"].classification == "risky"
    assert len(result.metadata["reduction_to_practice_questions"]) == 9
