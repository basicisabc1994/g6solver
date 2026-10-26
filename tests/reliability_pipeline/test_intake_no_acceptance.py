# SPDX-License-Identifier: Apache-2.0
"""A request with no acceptance criteria stops at intake (audit 009 RP-002). Offline.

The archive's request type defaulted ``acceptance_criteria`` to a non-empty sentinel, so the block could not fire from
any surface; the library has no default, and an empty list is the shape a surface sends.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from g6.reliability_pipeline import intake
from g6.reliability_pipeline.stages import StageState


@pytest.mark.parametrize("request_", [
    SimpleNamespace(goal="ship feature", acceptance_criteria=[]),
    SimpleNamespace(goal="ship feature", acceptance_criteria="  \n "),
    SimpleNamespace(goal="ship feature"),
    SimpleNamespace(goal="ship feature", acceptance_criteria=["x"]),        # IE-002: a placeholder is no criterion
    SimpleNamespace(goal="ship feature", acceptance_criteria=["TBD", "."]),
])
def test_rp002_no_acceptance_criteria_blocks_before_feasibility(request_):
    result = intake.run_intake(request_)
    assert result.state == StageState.BLOCK and result.reason == "G6_E_PIPELINE_NO_ACCEPTANCE"
    assert result.metadata["components_used"] == ("intent_engine",)


@pytest.mark.parametrize("answer", [["TBD"], "TBD", ["x", "n/a"], "todo\n."])
def test_a_placeholder_success_criterion_in_the_answers_is_no_criterion(answer):
    result = intake.run_intake(SimpleNamespace(goal="ship feature", intent_answers={"success_criteria": answer},
                                               declined_clarification=True))
    assert result.state == StageState.BLOCK and result.reason == "G6_E_PIPELINE_NO_ACCEPTANCE"


def test_a_placeholder_answer_never_stands_in_for_real_acceptance_criteria():
    result = intake.run_intake(SimpleNamespace(goal="ship feature", acceptance_criteria=["the export downloads"],
                                               intent_answers={"success_criteria": ["TBD", "the rows match"]},
                                               declined_clarification=True))
    assert result.state != StageState.BLOCK, result.reason
    assert result.metadata["task_contract"].intent.success_criteria == ["the rows match"]
