# SPDX-License-Identifier: Apache-2.0
"""AdaptPhysicalAIBlock: the registry and MCP door (``g6_invoke``) to the embodied family's motion checks.

Each op calls one public function and returns its labelled result unchanged, with ``component`` and ``op``; the
envelope (``completion_state``, ``warning_card``, ``evidence``, ``request_id``, ``run_id``) comes from the shared
motion package. An op missing data it needs answers ``blocked-escalated`` with ``G6_E_MOTION_CANNOT_ASSESS`` and the
fields it lacks; nothing is filled in with a default. Unknown ops and unknown fields (an ``approved``, a
``safety_envelope``) are refused by the typed input (``G6_E_INPUT_VALIDATION``).

No op reaches the network, writes a file or moves anything, so none is sensitive.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.core.ai_block import AIBlock
from g6.core.block_contract import FailureMode
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.embodied._motion.checks import check_plan
from g6.embodied._motion.envelope import CANNOT_ASSESS, MotionResult, refuse
from g6.embodied._motion.grasp import grasp_margin
from g6.embodied._motion.model import MAX_JOINTS, BodySpec, Finite, Goal, Grasp, Plan, Positive, World
from g6.embodied.adapt_physical_ai.kinematics import forward_kinematics, integrate_step
from g6.embodied.adapt_physical_ai.planners import MAX_ITERATIONS, plan_astar, plan_rrt

_OPS: dict[str, str] = {
    "check_plan": "check_plan(plan, world, robot, goal): each segment checked whole against the obstacles (inflated "
                  "by the robot's radius and clearance) and the workspace, each waypoint against the joint limits "
                  "and any grasp it carries, each segment's speed against the speed limits and the end against the "
                  "goal; the first violation comes back as evidence",
    "plan_astar": "plan_astar(world, robot, start, goal, cell_size, speed): a reference A* path for a point body in "
                  "2-D, timed at speed, then checked by check_plan",
    "plan_rrt": "plan_rrt(world, robot, start, goal, step, seed, speed, max_iterations, goal_bias): a reference RRT "
                "path for a point body in 2-D or 3-D, each edge checked whole and seeded so it repeats, then checked "
                "by check_plan",
    "forward_kinematics": "forward_kinematics(robot, q): a planar arm's joint positions and tip from its own link "
                          "lengths; refused when a joint is outside its limits",
    "integrate_step": "integrate_step(position, velocity, acceleration, dt): one step under constant acceleration in "
                      "closed form, with Euler's step and its error beside it",
    "grasp_margin": "grasp_margin(robot, grasp): each contact's friction margin (the coefficient of static friction "
                    "times its normal force, less the force along the surface it must hold), and its normal force "
                    "against the object's crush limit and the gripper's own limit",
}
_NEEDS: dict[str, tuple[str, ...]] = {
    "check_plan": ("plan", "world", "robot", "goal"),
    "plan_astar": ("world", "robot", "start", "goal", "cell_size", "speed"),
    "plan_rrt": ("world", "robot", "start", "goal", "step", "seed", "speed"),
    "forward_kinematics": ("robot", "q"),
    "integrate_step": ("position", "velocity", "acceleration", "dt"),
    "grasp_margin": ("robot", "grasp"),
}


class AdaptPhysicalAIInput(BaseModel):
    """One op's parameters, in SI units. Fields an op does not use are ignored by it; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["check_plan", "plan_astar", "plan_rrt", "forward_kinematics", "integrate_step", "grasp_margin", "ops",
                "help"] = "help"
    plan: Plan | None = None
    world: World | None = None
    robot: BodySpec | None = None
    goal: Goal | None = None
    grasp: Grasp | None = None
    start: list[Finite] | None = Field(None, min_length=2, max_length=3)
    cell_size: Positive | None = None
    step: Positive | None = None
    seed: int | None = Field(None, ge=0, le=2**63 - 1)
    speed: Positive | None = None
    max_iterations: int = Field(1000, ge=1, le=MAX_ITERATIONS)
    goal_bias: float = Field(0.1, ge=0, le=1, allow_inf_nan=False)
    q: list[Finite] | None = Field(None, min_length=1, max_length=MAX_JOINTS)
    position: list[Finite] | None = Field(None, min_length=1, max_length=3)
    velocity: list[Finite] | None = Field(None, min_length=1, max_length=3)
    acceleration: list[Finite] | None = Field(None, min_length=1, max_length=3)
    dt: Positive | None = None
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _ids(d: AdaptPhysicalAIInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


_RUN: dict[str, Callable[[AdaptPhysicalAIInput], MotionResult]] = {
    "check_plan": lambda d: check_plan(d.plan, d.world, d.robot, d.goal, **_ids(d)),
    "plan_astar": lambda d: plan_astar(d.world, d.robot, d.start, d.goal, d.cell_size, d.speed, **_ids(d)),
    "plan_rrt": lambda d: plan_rrt(d.world, d.robot, d.start, d.goal, d.step, d.seed, d.speed,
                                   max_iterations=d.max_iterations, goal_bias=d.goal_bias, **_ids(d)),
    "forward_kinematics": lambda d: forward_kinematics(d.robot, d.q, **_ids(d)),
    "integrate_step": lambda d: integrate_step(d.position, d.velocity, d.acceleration, d.dt, **_ids(d)),
    "grasp_margin": lambda d: grasp_margin(d.robot, d.grasp, **_ids(d)),
}


@block_contract(
    purpose="adapt_physical_ai",
    inputs_required_per_op={op: needs for op, needs in _NEEDS.items()},
    outputs=("MotionResult",),
    known_failure_modes=(FailureMode("model_reality_gap", detector="", severity="high"),),
    mitigates_failure_modes=("infeasible_motion_plan", "grasp_failure"),
    verification_method="tier1_review_pending",
)
@component_maturity(
    "beta",
    reason="checks a plan against the world and limits the caller gives, from a committed source register; "
           "qualified-draft at best; no qualified person has reviewed it",
)
class AdaptPhysicalAIBlock(AIBlock[AdaptPhysicalAIInput, dict, dict]):
    """Check a motion plan or a grasp, plan a reference path, or compute a chain or a step. Never ``verified``."""

    def __init__(self, name: str = "adapt_physical_ai", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: AdaptPhysicalAIInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": []})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        missing = [f for f in _NEEDS[data.op] if getattr(data, f) is None]
        result: Any
        if missing:
            result = refuse(data.op, CANNOT_ASSESS, f"{data.op} needs {', '.join(missing)}; missing data is never "
                                                    "filled in with a default", missing=missing, **_ids(data))
        else:
            result = _RUN[data.op](data)
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


__all__ = ["AdaptPhysicalAIBlock", "AdaptPhysicalAIInput"]
