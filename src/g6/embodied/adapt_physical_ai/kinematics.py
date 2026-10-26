# SPDX-License-Identifier: Apache-2.0
"""Two computed answers beside the plan check: a planar arm's joint positions, and one step of motion.

* :func:`forward_kinematics` places each joint of a planar arm from its own base, link lengths and joint angles (rule
  ``chain``); a configuration with any joint outside its limits is refused with the joints named.
* :func:`integrate_step` advances a position and velocity by ``dt`` under constant acceleration: the closed form
  (rule ``closed_form``), with Euler's step (rule ``euler``) beside it and its position error, so the cost of the
  cheaper step is a number. Gravity is part of the acceleration given; nothing is added.
"""
from __future__ import annotations

from typing import Any

from g6.embodied._motion import geometry as geo
from g6.embodied._motion.envelope import ESTIMATE, INPUT_INVALID, JOINT_LIMIT, MotionResult, answer, refuse
from g6.embodied._motion.model import PlanarArm
from g6.embodied._motion.sources import cited


def forward_kinematics(body: Any, q: list[float], *, request_id: str = "", run_id: str = "") -> MotionResult:
    """The joint positions (base first, tip last) of a planar arm at joint angles ``q``."""
    op, ids = "forward_kinematics", {"request_id": request_id, "run_id": run_id}
    if not isinstance(body, PlanarArm):
        return refuse(op, INPUT_INVALID, "forward_kinematics needs a planar_arm robot", **ids)
    if len(q) != len(body.joints):
        return refuse(op, INPUT_INVALID, f"q has {len(q)} angles for {len(body.joints)} joints", **ids)
    points = geo.chain_points(body.base, body.link_lengths, q)
    outside = [{"joint": j.name, "angle": a, "lower": j.lower, "upper": j.upper}
               for a, j in zip(q, body.joints) if not j.lower <= a <= j.upper]
    evidence = [{"kind": "forward_kinematics", "rules": cited(("chain", "joint_limits"))}]
    if outside:
        names = ", ".join(o["joint"] for o in outside)
        return refuse(op, JOINT_LIMIT, f"joints {names} are outside their limits at this configuration",
                      evidence=evidence, points=points, tip=points[-1], outside_limits=outside, **ids)
    return answer(op, ESTIMATE, "Joint positions computed from the base, link lengths and joint angles given.",
                  "Check a whole plan, its segments and its speeds, with check_plan before it is carried out.",
                  evidence=evidence, points=points, tip=points[-1], outside_limits=[], **ids)


def integrate_step(position: list[float], velocity: list[float], acceleration: list[float], dt: float, *,
                   request_id: str = "", run_id: str = "") -> MotionResult:
    """One step of ``dt`` seconds under constant ``acceleration``: the closed form, and Euler's step with its error."""
    op, ids = "integrate_step", {"request_id": request_id, "run_id": run_id}
    if not len(position) == len(velocity) == len(acceleration):
        return refuse(op, INPUT_INVALID, "position, velocity and acceleration must have the same number of axes",
                      **ids)
    exact_p, exact_v = geo.constant_acceleration(position, velocity, acceleration, dt)
    euler_p, euler_v = geo.euler_step(position, velocity, acceleration, dt)
    error = geo.distance(euler_p, exact_p)
    return answer(op, ESTIMATE,
                  "Exact under constant acceleration over dt; Euler's step is given beside it with its position "
                  "error. Neither knows about contact, drag or a changing force.",
                  "If the acceleration changes within the step, take smaller steps or use a physics engine.",
                  evidence=[{"kind": "integration", "dt": dt, "rules": cited(("closed_form", "euler"))}],
                  position=exact_p, velocity=exact_v, method="closed form under constant acceleration",
                  euler={"position": euler_p, "velocity": euler_v}, euler_position_error=error, **ids)


__all__ = ["forward_kinematics", "integrate_step"]
