# SPDX-License-Identifier: Apache-2.0
"""The typed inputs every motion check reads: a body, a world, a timed plan and a goal, in SI units (m, rad, s).

Every model is frozen and refuses unknown fields, so a misspelt limit, or a caller's ``approved`` or ``certified``,
is refused instead of ignored. Nothing has a default limit: a body's radius, clearance, top speed, joint limits and
link lengths, and a world's bounds and obstacles, are what the caller's own specification says. A waypoint's time may
be left out; the speed check then answers "cannot assess" instead of guessing. A length or coordinate beyond
:data:`MAX_EXTENT` cannot be assessed either (:func:`range_problems`).

:data:`BodySpec` is the family's one body description. It has two kinds today, a ``point`` body (one sphere: a mobile
base, a drone, a tool tip inside its envelope) and a ``planar_arm`` (revolute joints in a plane, each link a capsule).
Either may carry a :class:`Gripper` (its own force limit), and a waypoint may carry the :class:`Grasp` it holds (the
forces at each contact, the coefficient of friction and the object's crush limit), which the grasp check reads. A
member that needs more of a body (effort limits, a payload) adds the fields here, so every check keeps reading one
type.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Finite = Annotated[float, Field(allow_inf_nan=False)]
Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]
NonNegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]

#: Size bounds on what one request may carry (they bound the work, they are not limits on a robot).
MAX_JOINTS = 12
MAX_OBSTACLES = 1000
MAX_WAYPOINTS = 5000
MAX_CONTACTS = 20
#: m (rad for a joint angle). The range the checks are made for: every length and coordinate within it of zero, so no
#: square or product the checks take can overflow. A value beyond it cannot be assessed (rule ``bounds``).
MAX_EXTENT = 1e6

_STRICT = ConfigDict(extra="forbid", frozen=True)


class Joint(BaseModel):
    """One revolute joint: its position limits (rad) and its speed limit (rad/s), from the robot's specification."""

    model_config = _STRICT

    name: str = Field(min_length=1, max_length=64)
    lower: Finite = Field(description="the lowest joint angle allowed, rad")
    upper: Finite = Field(description="the highest joint angle allowed, rad")
    max_velocity: Positive = Field(description="the highest joint speed allowed, rad/s")

    @model_validator(mode="after")
    def _ordered(self) -> Joint:
        if not self.lower < self.upper:
            raise ValueError(f"joint {self.name}: lower ({self.lower}) must be below upper ({self.upper})")
        return self


class Gripper(BaseModel):
    """The robot's gripper and its own force limit, from its specification, with what the limit is a limit on. A
    gripper whose ``rated_as`` is not given cannot be assessed against its limit."""

    model_config = _STRICT

    max_force: Positive = Field(description="the largest normal force the gripper may press with, N: at any one "
                                            "contact, or on all of a grasp's contacts together, as rated_as says")
    rated_as: Literal["total", "per_contact"] | None = Field(
        None, description="whether max_force limits the sum of a grasp's normal forces (total) or each contact's "
                          "(per_contact), as the gripper's specification states it")


class Contact(BaseModel):
    """One contact between the gripper and the object it holds, and the two forces at it."""

    model_config = _STRICT

    name: str = Field("", max_length=64)
    normal_force: NonNegative = Field(description="the force pressing the contact into the object, at right angles "
                                                  "to its surface, N")
    tangential_force: NonNegative = Field(description="the size of the force along the surface the contact must hold, "
                                                      "N: its share of the object's weight and inertial load (G6 does "
                                                      "not check how the load is split)")


class Grasp(BaseModel):
    """An object held: the forces at each contact, the coefficient of static friction between the contacts and the
    object, and the object's crush limit. Every value is the caller's, from its own measurement and the object's
    specification."""

    model_config = _STRICT

    contacts: list[Contact] = Field(min_length=1, max_length=MAX_CONTACTS)
    friction_coefficient: NonNegative = Field(description="the coefficient of static friction between the contacts' "
                                                          "surface and the object's, measured for the pair")
    crush_limit: Positive = Field(description="the largest normal force the object takes at one contact without "
                                              "damage, N")


class PointBody(BaseModel):
    """A robot that moves as one sphere (a disc in 2-D), with no orientation."""

    model_config = _STRICT

    kind: Literal["point"]
    radius: NonNegative = Field(description="the radius of the sphere that encloses the robot, m")
    max_speed: Positive = Field(description="the highest speed allowed, m/s")
    clearance: NonNegative = Field(description="the gap the robot must keep from every obstacle, m")
    gripper: Gripper | None = Field(None, description="its gripper; a grasp cannot be assessed without it")


class PlanarArm(BaseModel):
    """A serial arm of revolute joints in a plane, base first. Joint ``i`` turns link ``i`` relative to the link before
    it (the first relative to the x-axis), and each link is a capsule: its centre line widened by ``link_radius``."""

    model_config = _STRICT

    kind: Literal["planar_arm"]
    base: list[Finite] = Field(min_length=2, max_length=2, description="the first joint's position, m")
    link_lengths: list[Positive] = Field(min_length=1, max_length=MAX_JOINTS, description="each link's length, m")
    link_radius: NonNegative = Field(description="the radius of every link's capsule, m")
    joints: list[Joint] = Field(min_length=1, max_length=MAX_JOINTS)
    clearance: NonNegative = Field(description="the gap every link must keep from every obstacle, m")
    gripper: Gripper | None = Field(None, description="its gripper; a grasp cannot be assessed without it")

    @model_validator(mode="after")
    def _one_joint_per_link(self) -> PlanarArm:
        if len(self.joints) != len(self.link_lengths):
            raise ValueError(f"{len(self.joints)} joints for {len(self.link_lengths)} links: give one joint per link")
        names = [j.name for j in self.joints]
        if len(set(names)) != len(names):
            raise ValueError("joint names must be distinct")
        return self


#: The family's one body description; ``kind`` says which.
BodySpec = Annotated[PointBody | PlanarArm, Field(discriminator="kind")]


class Obstacle(BaseModel):
    """A fixed obstacle as a sphere (a disc in 2-D). Enclose any other shape in one or more spheres."""

    model_config = _STRICT

    name: str = Field("", max_length=64)
    centre: list[Finite] = Field(min_length=2, max_length=3, description="m")
    radius: Positive = Field(description="m")


class World(BaseModel):
    """The workspace box the whole robot must stay inside, and every obstacle in it. ``obstacles=[]`` says there is
    none; leaving it out is refused."""

    model_config = _STRICT

    lower: list[Finite] = Field(min_length=2, max_length=3, description="the box's lowest corner, m")
    upper: list[Finite] = Field(min_length=2, max_length=3, description="the box's highest corner, m")
    obstacles: list[Obstacle] = Field(max_length=MAX_OBSTACLES)

    @model_validator(mode="after")
    def _a_box(self) -> World:
        if len(self.lower) != len(self.upper):
            raise ValueError("lower and upper must have the same number of axes")
        if any(lo >= hi for lo, hi in zip(self.lower, self.upper)):
            raise ValueError("each lower coordinate must be below its upper one")
        for i, ob in enumerate(self.obstacles):
            if len(ob.centre) != len(self.lower):
                raise ValueError(f"obstacle {ob.name or i} has {len(ob.centre)} coordinates in a "
                                 f"{len(self.lower)}-D world")
        return self

    @property
    def dimensions(self) -> int:
        return len(self.lower)


class Waypoint(BaseModel):
    """One configuration of the plan and, when known, when the robot is to reach it."""

    model_config = _STRICT

    q: list[Finite] = Field(min_length=1, max_length=MAX_JOINTS,
                            description="a point body's position (m), or an arm's joint angles (rad), base first")
    t: Finite | None = Field(None, description="the time from the start of the plan, s")
    grasp: Grasp | None = Field(None, description="the object held at this waypoint and the forces at its contacts; "
                                                  "left out when nothing is held")


class Plan(BaseModel):
    """The waypoints in order. Between two waypoints the robot is taken to move along the straight line in its
    configuration (position for a point body, joint angles for an arm)."""

    model_config = _STRICT

    waypoints: list[Waypoint] = Field(min_length=1, max_length=MAX_WAYPOINTS)


class Goal(BaseModel):
    """Where the plan must end: a configuration ``q``, or, for an arm, the position of its tip; within
    ``tolerance`` (m for a position, rad for joint angles, measured as a straight-line distance)."""

    model_config = _STRICT

    q: list[Finite] | None = Field(None, min_length=1, max_length=MAX_JOINTS)
    tip: list[Finite] | None = Field(None, min_length=2, max_length=2)
    tolerance: NonNegative

    @model_validator(mode="after")
    def _one_target(self) -> Goal:
        if (self.q is None) == (self.tip is None):
            raise ValueError("give exactly one of q (a configuration) or tip (an arm's tip position)")
        return self


Body = PointBody | PlanarArm


def degrees_of_freedom(body: Body, world: World) -> int:
    """How many numbers one waypoint carries for this body in this world."""
    return world.dimensions if isinstance(body, PointBody) else len(body.joints)


def shape_problems(plan: Plan, world: World, body: Body, goal: Goal) -> list[str]:
    """What does not fit together (a 3-D arm, a waypoint of the wrong length, a tip goal for a point body), or ``[]``."""
    out: list[str] = []
    if isinstance(body, PlanarArm) and world.dimensions != 2:
        out.append("a planar arm needs a 2-D world")
    dof = degrees_of_freedom(body, world)
    wrong = [i for i, w in enumerate(plan.waypoints) if len(w.q) != dof]
    if wrong:
        out.append(f"waypoints {wrong[:10]} do not have {dof} numbers")
    if goal.q is not None and len(goal.q) != dof:
        out.append(f"the goal's q does not have {dof} numbers")
    if goal.tip is not None and not isinstance(body, PlanarArm):
        out.append("a tip goal needs a planar arm; give the goal as q")
    return out


def range_problems(plan: Plan, world: World, body: Body, goal: Goal) -> list[str]:
    """The fields holding a length or coordinate (or a configuration's number) beyond :data:`MAX_EXTENT` of zero, or
    ``[]``. A check made on such a value could overflow, so it cannot be assessed."""
    values: list[tuple[str, float]] = [("world.lower", v) for v in world.lower]
    values += [("world.upper", v) for v in world.upper]
    for i, ob in enumerate(world.obstacles):
        values += [(f"world.obstacles[{i}].centre", v) for v in ob.centre]
        values.append((f"world.obstacles[{i}].radius", ob.radius))
    if isinstance(body, PlanarArm):
        values += [("robot.base", v) for v in body.base] + [("robot.link_lengths", v) for v in body.link_lengths]
        values.append(("robot.link_radius", body.link_radius))
    else:
        values.append(("robot.radius", body.radius))
    values.append(("robot.clearance", body.clearance))
    for i, w in enumerate(plan.waypoints):
        values += [(f"plan.waypoints[{i}].q", v) for v in w.q]
    values += [("goal.q", v) for v in goal.q or ()] + [("goal.tip", v) for v in goal.tip or ()]
    values.append(("goal.tolerance", goal.tolerance))
    return list(dict.fromkeys(name for name, v in values if abs(v) > MAX_EXTENT))


def range_reason(beyond: list[str]) -> str:
    """Why the fields :func:`range_problems` names cannot be assessed, in words."""
    more = f" and {len(beyond) - 10} more" if len(beyond) > 10 else ""
    return (f"{', '.join(beyond[:10])}{more}: beyond the range the checks are made for, every length and coordinate "
            f"within {MAX_EXTENT:,.0f} m of zero (a joint angle within {MAX_EXTENT:,.0f} rad), where no square or "
            "product they take can overflow. The plan is not cleared.")


__all__ = ["MAX_CONTACTS", "MAX_EXTENT", "MAX_JOINTS", "MAX_OBSTACLES", "MAX_WAYPOINTS", "Body", "BodySpec", "Contact",
           "Finite", "Goal", "Grasp", "Gripper", "Joint", "NonNegative", "Obstacle", "Plan", "PlanarArm", "PointBody",
           "Positive", "Waypoint", "World", "degrees_of_freedom", "range_problems", "range_reason", "shape_problems"]
