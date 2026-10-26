# SPDX-License-Identifier: Apache-2.0
"""``accepted_risky_intent`` is a flag the caller sets: G6 cannot tell whether a person set it, so it never makes a risky
task look accepted by a person. The task proceeds as a downgrade (a qualified draft at most), and the result records the
acceptance as the caller's word. Offline."""
from __future__ import annotations

from types import SimpleNamespace

from g6.reliability_pipeline import intake
from g6.reliability_pipeline.stages import StageState

RISKY = "delete production payment records"
ANSWERS = {"why": "cleanup", "success_criteria": ["records removed"]}


def test_the_flag_is_recorded_as_the_callers_word_and_never_lifts_the_task():
    result = intake.run_intake(SimpleNamespace(goal=RISKY, intent_answers=ANSWERS, accepted_risky_intent=True,
                                               declined_clarification=True))
    assert result.state == StageState.DOWNGRADE                                   # never a clean advance
    assert result.metadata["risk_acceptance"] == {"accepted_by": "caller", "authenticated": False,
                                                  "said": intake.RISK_ACCEPTED_BY_CALLER}
    assert "caller's word" in result.reason


def test_without_the_flag_the_task_is_held_and_told_whose_word_the_flag_is():
    held = intake.run_intake(SimpleNamespace(goal=RISKY, intent_answers=ANSWERS, declined_clarification=True))
    assert (held.state, held.reason) == (StageState.BLOCK, "G6_E_PIPELINE_FEASIBILITY_RISKY_CONFIRMATION_REQUIRED")
    assert "caller's word" in held.safe_next_action and "risk_acceptance" not in held.metadata


def test_a_task_that_is_not_risky_records_no_acceptance():
    """Control: the record belongs to a risky verdict, not to the flag."""
    clear = intake.run_intake(SimpleNamespace(goal="add CSV export", intent_answers=ANSWERS, accepted_risky_intent=True,
                                              declined_clarification=True))
    assert clear.metadata["feasibility_report"].classification == "clear"
    assert "risk_acceptance" not in clear.metadata
