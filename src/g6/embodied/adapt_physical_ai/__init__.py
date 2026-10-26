# SPDX-License-Identifier: Apache-2.0
"""Check a robot's motion plan against its world and its own limits, segment by segment, before anything moves.

An extension in the embodied family, and the door to the family's one motion package (``g6.embodied._motion``).

    from g6.embodied.adapt_physical_ai import check_plan, Plan, World, PointBody, Goal
    r = check_plan(plan, world, body, goal)
    r.completion_state, r.first_violation      # "blocked-escalated" and where the plan first goes wrong, or
                                               # "qualified-draft" and None: no violation found against what was given

What it does:

* ``check_plan``: each segment is checked whole against the obstacles, inflated by the robot's radius and clearance,
  and against the workspace (a point body in closed form; an arm in steps no point of it can cross); each waypoint's
  joints against their limits; each segment's average speed against the speed limits; the last waypoint against the
  goal; and each waypoint's grasp, if it carries one. The first violation along the plan comes back as evidence, with
  every rule applied and its source;
* ``grasp_margin``: one grasp on its own: each contact's friction margin (the coefficient of static friction times
  its normal force, less the force along the surface it must hold), and its normal force against the object's crush
  limit and the gripper's own limit;
* ``plan_astar`` and ``plan_rrt``: reference planners for a point body (A* on a grid that blocks each obstacle's whole
  inflated footprint; a seeded RRT whose edges are checked whole and whose last waypoint is the goal), each plan
  then checked by ``check_plan``;
* ``forward_kinematics`` for a planar arm of any number of links, from its own link lengths, and ``integrate_step``,
  one step under constant acceleration in closed form with Euler's step and its error beside it.

Every limit is the caller's, from its own robot's specification; missing data answers "cannot assess", never a
default; every rule names an entry in the committed source register (``g6.embodied._motion.sources``). The best
state is ``qualified-draft``: no violation found against the world and limits given, which is not a safety clearance.
Nothing here moves hardware; any command belongs to ``g6.action_gating``.

Not ported from the archive's ``adapt_physical_ai``, and why: the device ops, their driver and its self-declared
"safety envelope" (a caller's or a driver's word is not an observation, and this block never actuates); the stored
world state and saved runs (each request carries its world); the MCP servers, the planner recommender and its model
call, the patterns, skills and scaffold; the unit-length two-link arm and its inverse kinematics (an arm's plan is
given in joint space, which is what a controller carries out); the single-cell A* obstacles and the RRT that checked
only its nodes.

Merged in from two archive extensions: from ``embodiment``, its limit validators (joint position and speed, a base's
speed, the workspace, the gripper's force), which are the checks above, read from one body description in SI units;
from ``tactile_fusion``, the grasp margin and the object's crush limit. Not ported from them: random sensor readings and
anything labelled ``verified``, the robot bridges, safety-standard labels, the controller and filter helpers, the
pressure-grid scores and the surface classifier.
"""
from g6.embodied._motion import (
    BodySpec,
    Contact,
    Goal,
    Grasp,
    Gripper,
    Joint,
    MotionResult,
    Obstacle,
    Plan,
    PlanarArm,
    PointBody,
    Violation,
    Waypoint,
    World,
    check_plan,
    grasp_margin,
    register_check,
)
from g6.embodied.adapt_physical_ai.block import AdaptPhysicalAIBlock, AdaptPhysicalAIInput
from g6.embodied.adapt_physical_ai.kinematics import forward_kinematics, integrate_step
from g6.embodied.adapt_physical_ai.planners import plan_astar, plan_rrt

__g6_block__ = "AdaptPhysicalAIBlock"
__g6_sensitive_ops__: tuple[str, ...] = ()  # nothing reaches the network, writes a file or moves anything
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("infeasible_motion_plan", "grasp_failure")
__g6_introduces__: tuple[str, ...] = ("model_reality_gap",)
# Vetting (DECISIONS #39): listed once its G6 certificate is current; no qualified person has reviewed it.
__g6_vetting__ = {
    "kinds": ("physical_safety",),
    "register": "g6/embodied/_motion/data/source_register.json",
    "independent_pass": "g6/embodied/_motion/data/rule_review.json",
    "signed_review": None,
}

__all__ = ["AdaptPhysicalAIBlock", "AdaptPhysicalAIInput", "BodySpec", "Contact", "Goal", "Grasp", "Gripper", "Joint",
           "MotionResult", "Obstacle", "Plan", "PlanarArm", "PointBody", "Violation", "Waypoint", "World", "check_plan",
           "forward_kinematics", "grasp_margin", "integrate_step", "plan_astar", "plan_rrt", "register_check"]
