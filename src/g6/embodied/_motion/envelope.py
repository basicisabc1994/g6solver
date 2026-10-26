# SPDX-License-Identifier: Apache-2.0
"""The stable codes of the motion checks and the one labelled result every op returns.

A plan with no violation is ``qualified-draft`` with the card :data:`NO_VIOLATION_FOUND`: no violation was found
against the world and limits the caller gave. A computed answer (a chain's joint positions, one integration step) is
``qualified-draft`` with :data:`ESTIMATE`. A plan with a violation is ``blocked-escalated`` with the code of its first
violation, and a check that could not be made is ``blocked-escalated`` with :data:`CANNOT_ASSESS`. Nothing is
``verified``: :class:`MotionResult` has no such state, because a check against a model of the world says nothing
about the real one.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from g6.core.reliability_envelope import WarningCard

NO_VIOLATION_FOUND = "G6_E_MOTION_NO_VIOLATION_FOUND"
ESTIMATE = "G6_E_MOTION_ESTIMATE"
COLLISION = "G6_E_MOTION_COLLISION"
OUT_OF_WORKSPACE = "G6_E_MOTION_OUT_OF_WORKSPACE"
JOINT_LIMIT = "G6_E_MOTION_JOINT_LIMIT"
VELOCITY_LIMIT = "G6_E_MOTION_VELOCITY_LIMIT"
TIMING = "G6_E_MOTION_TIMING"
GOAL_MISSED = "G6_E_MOTION_GOAL_MISSED"
GRASP_SLIP = "G6_E_MOTION_GRASP_SLIP"
GRASP_CRUSH = "G6_E_MOTION_GRASP_CRUSH"
GRIPPER_FORCE = "G6_E_MOTION_GRIPPER_FORCE_LIMIT"
CANNOT_ASSESS = "G6_E_MOTION_CANNOT_ASSESS"
INPUT_INVALID = "G6_E_MOTION_INPUT_INVALID"
NO_PATH = "G6_E_MOTION_NO_PATH"
#: The codes a violation may carry. A member that needs a new one adds it here and its action to ``_ACTIONS``; a check
#: declares the ones it reports when it is registered.
VIOLATION_CODES = frozenset({COLLISION, OUT_OF_WORKSPACE, JOINT_LIMIT, VELOCITY_LIMIT, TIMING, GOAL_MISSED, GRASP_SLIP,
                             GRASP_CRUSH, GRIPPER_FORCE})

_ACTIONS = {
    COLLISION: "Change the plan around the obstacle named in first_violation, then check it again.",
    OUT_OF_WORKSPACE: "Keep the whole robot, its radius included, inside the workspace box, then check again.",
    JOINT_LIMIT: "Keep every joint inside its limits at every waypoint, then check again.",
    VELOCITY_LIMIT: "Slow the plan down (later times) where first_violation says, then check again.",
    TIMING: "Give the waypoints times that increase from one to the next.",
    GOAL_MISSED: "End the plan at the goal, within its tolerance, then check again.",
    GRASP_SLIP: "Press harder at the contact first_violation names, within the object's and the gripper's limits, or "
                "give it less load to hold, then check again.",
    GRASP_CRUSH: "Press more lightly at the contact first_violation names, keeping its friction margin above zero, "
                 "then check again.",
    GRIPPER_FORCE: "Keep every contact's normal force within the gripper's own limit, then check again.",
    CANNOT_ASSESS: "Supply the missing data the reason names; nothing is assumed in its place.",
    INPUT_INVALID: "Correct the input as the reason says and ask again.",
    NO_PATH: "Try a finer cell_size, more iterations, another seed or a different start or goal; a planner that "
             "finds nothing has not shown that no path exists.",
}

State = Literal["qualified-draft", "blocked-escalated"]


class MotionResult(BaseModel):
    """One op's labelled outcome. Op-specific fields sit beside the envelope's."""

    model_config = ConfigDict(extra="allow", frozen=True)

    operation: str
    code: str = ""
    reason: str = ""
    completion_state: State
    warning_card: dict[str, Any]
    evidence: list[dict[str, Any]]
    request_id: str = ""
    run_id: str = ""


def answer(operation: str, card: str, message: str, action: str, *, evidence: list[dict[str, Any]],
           request_id: str = "", run_id: str = "", **fields: Any) -> MotionResult:
    """A ``qualified-draft`` answer whose card (``card``) says what it rests on."""
    warning = WarningCard(code=card, message=message, severity="amber", suggested_action=action).model_dump()
    return MotionResult.model_validate({**fields, "operation": operation, "completion_state": "qualified-draft",
                                        "warning_card": warning, "evidence": evidence, "request_id": request_id,
                                        "run_id": run_id})


def refuse(operation: str, code: str, reason: str, *, evidence: list[dict[str, Any]] | None = None,
           request_id: str = "", run_id: str = "", **fields: Any) -> MotionResult:
    """A ``blocked-escalated`` result with its stable code and the reason in words."""
    warning = WarningCard(code=code, message=reason, severity="blocked", suggested_action=_ACTIONS[code]).model_dump()
    return MotionResult.model_validate({**fields, "operation": operation, "code": code, "reason": reason,
                                        "completion_state": "blocked-escalated", "warning_card": warning,
                                        "evidence": [*(evidence or []), {"code": code, "reason": reason}],
                                        "request_id": request_id, "run_id": run_id})


__all__ = ["CANNOT_ASSESS", "COLLISION", "ESTIMATE", "GOAL_MISSED", "GRASP_CRUSH", "GRASP_SLIP", "GRIPPER_FORCE",
           "INPUT_INVALID", "JOINT_LIMIT", "NO_PATH", "NO_VIOLATION_FOUND", "OUT_OF_WORKSPACE", "TIMING",
           "VELOCITY_LIMIT", "VIOLATION_CODES", "MotionResult", "answer", "refuse"]
