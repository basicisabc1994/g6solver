# SPDX-License-Identifier: Apache-2.0
"""The embodied family's one motion package: a body, a world, a timed plan and the checks a plan must pass.

Shared code (``_``-prefixed, so it never registers as a component). ``adapt_physical_ai`` serves it through the
registry and MCP; another member builds on the same types (:data:`BodySpec`, :class:`World`, :class:`Plan`,
:class:`Goal`) and adds its own checks with :func:`register_check`, as :mod:`.grasp` does for a grasp's friction
margin and crush limit, so a plan is checked in one place and its first violation comes back as evidence.

The same terms hold for every check:

* every limit is the caller's, from its own robot's specification: G6 holds no default limit, and a check whose data
  is missing answers "cannot assess" (``blocked-escalated``), never a default;
* every rule the code applies names an entry in the committed source register, or the caller's fields it reads
  (:mod:`.sources`);
* a plan with no violation is ``qualified-draft``: no violation was found against the world and the limits given. It
  is not a safety clearance, and nothing here moves hardware.
"""
from g6.embodied._motion.checks import (
    Case,
    Check,
    Outcome,
    Violation,
    check_plan,
    point_first_contact,
    point_first_exit,
    point_segment_clear,
    register_check,
    registered_checks,
)
from g6.embodied._motion.envelope import MotionResult, answer, refuse
from g6.embodied._motion.grasp import grasp_margin  # registers the grasp check after the five in checks
from g6.embodied._motion.model import (
    Body,
    BodySpec,
    Contact,
    Goal,
    Grasp,
    Gripper,
    Joint,
    Obstacle,
    Plan,
    PlanarArm,
    PointBody,
    Waypoint,
    World,
    degrees_of_freedom,
    shape_problems,
)
from g6.embodied._motion.sources import RULES, register_problems, stale_sources

__all__ = ["RULES", "Body", "BodySpec", "Case", "Check", "Contact", "Goal", "Grasp", "Gripper", "Joint", "MotionResult",
           "Obstacle", "Outcome", "Plan", "PlanarArm", "PointBody", "Violation", "Waypoint", "World", "answer",
           "check_plan", "degrees_of_freedom", "grasp_margin", "point_first_contact", "point_first_exit",
           "point_segment_clear", "refuse", "register_check", "register_problems", "registered_checks",
           "shape_problems", "stale_sources"]
