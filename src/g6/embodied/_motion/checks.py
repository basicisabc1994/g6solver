# SPDX-License-Identifier: Apache-2.0
"""The plan checks, registered by name, and :func:`check_plan`, which runs every one and returns the first violation.

Five checks are registered here, in this order: ``joint_limits`` (each waypoint's joints inside their limits),
``speed_limits`` (each segment's average speed inside the body's limits; untimed waypoints cannot be assessed),
``workspace`` and ``collision`` (each segment checked whole, never only at its waypoints) and ``goal`` (the last
waypoint within the goal's tolerance); :mod:`.grasp` registers a sixth, ``grasp_margin`` (each waypoint's grasp
against friction, the object's crush limit and the gripper's). A member adds its own with :func:`register_check`; it
then runs in every
``check_plan``, its rules must be in :data:`~g6.embodied._motion.sources.RULES`, and it declares the violation codes
it reports (a code it did not declare answers "cannot assess").

The answer is the first violation along the plan (the lowest position: waypoint index plus the fraction of the way
along the next segment), every violation found, what each check assumed and the rules it applied with their sources.
A check that raises, lacks data or runs out of work answers "cannot assess", and the plan is not let through; so does
a length or coordinate beyond the range the checks are made for, and any term of theirs that is not a finite number.
"""
from __future__ import annotations

import datetime as _dt
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from g6.embodied._motion import geometry as geo
from g6.embodied._motion.envelope import (
    CANNOT_ASSESS,
    COLLISION,
    GOAL_MISSED,
    INPUT_INVALID,
    JOINT_LIMIT,
    NO_VIOLATION_FOUND,
    OUT_OF_WORKSPACE,
    TIMING,
    VELOCITY_LIMIT,
    VIOLATION_CODES,
    MotionResult,
    answer,
    refuse,
)
from g6.embodied._motion.model import (
    Body,
    Goal,
    Plan,
    PlanarArm,
    PointBody,
    World,
    range_problems,
    range_reason,
    shape_problems,
)
from g6.embodied._motion.sources import RULES, cited, stale_sources

#: Distance evaluations the arm checks of one plan may make between them (rule ``work_bound``); past it they cannot
#: assess.
WORK_BOUND = 2_000_000
#: Violations a result lists in full (rule ``bounds``; ``violations_found`` counts them all).
MAX_LISTED = 200
#: The rules each built-in check applies, by the kind of body (or goal) it applies them to.
_POINT_SWEPT = ("swept_point", "tolerance")
_ARM_SWEPT = ("swept_arm", "chain", "tolerance", "work_bound")

_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
_BASE_ASSUMPTIONS = (
    "Obstacles are the fixed spheres in world.obstacles; anything not listed there is not checked, and nothing moves "
    "but the robot.",
    "The robot is the body given: one sphere, or an arm of capsules; an arm's links are not checked against each "
    "other.",
    "Between two waypoints the robot moves along the straight line in its configuration (position, or joint angles).",
    "Not checked: acceleration and effort limits, a point body's turning rate (it is taken to have no orientation), "
    "dynamics, contact beyond the forces a waypoint's grasp gives, "
    "sensing and localisation error, and the controller's tracking error. The plan is checked as geometry, joint "
    "limits, average speeds, its goal and any grasp given, only.",
    "The plan is taken to start at the robot's current configuration: the move from where the robot is now to "
    "waypoint 0 is not checked.",
)
_NO_OBSTACLE = ("No obstacle was given (world.obstacles is empty), so the collision check had nothing to check the "
                "plan against.",)


class Violation(BaseModel):
    """One thing the plan does that its world or its body does not allow, and where along the plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    check: str
    code: str
    at: float = Field(description="waypoint index, plus the fraction of the way along the next segment")
    time: float | None = None
    waypoint: int | None = None
    segment: int | None = None
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class _Work:
    """The distance evaluations left for one plan's arm checks."""

    def __init__(self, bound: int) -> None:
        self.left = bound


@dataclass(frozen=True)
class Case:
    """What a check reads: the plan, its world, its body and its goal, all already shape-checked."""

    plan: Plan
    world: World
    body: Body
    goal: Goal
    work: _Work = field(default_factory=lambda: _Work(WORK_BOUND), compare=False)


@dataclass
class Outcome:
    """What one check found: its violations (at most :data:`MAX_LISTED` built; past that only counted, in
    ``unlisted``); or why it cannot assess, or why it does not apply; what it assumed; which of its registered rules it
    applied to this case (empty means all of them); and any further fields its row in the result carries (``row``)."""

    violations: list[Violation] = field(default_factory=list)
    cannot_assess: str = ""
    not_applicable: str = ""
    assumptions: tuple[str, ...] = ()
    rules: tuple[str, ...] = ()
    unlisted: int = 0
    row: dict[str, Any] = field(default_factory=dict)

    def found(self, **fields: Any) -> None:
        """Record one violation: built while fewer than :data:`MAX_LISTED` are kept, and only counted past that, so a
        plan with many violations never holds more than the result lists. A check reports in plan order, so the ones
        kept are its earliest."""
        if len(self.violations) < MAX_LISTED:
            self.violations.append(Violation(**fields))
        else:
            self.unlisted += 1


@dataclass(frozen=True)
class Check:
    name: str
    run: Callable[[Case], Outcome]
    rules: tuple[str, ...]
    codes: tuple[str, ...]


_CHECKS: dict[str, Check] = {}


def register_check(name: str, run: Callable[[Case], Outcome], *, rules: tuple[str, ...],
                   codes: tuple[str, ...] = ()) -> Check:
    """Add a check every :func:`check_plan` then runs, after those already registered. ``rules`` name the rules it
    applies (each in ``sources.RULES``, so each traces to the register or to the caller's own fields); ``codes`` are
    the violation codes it reports (each in ``envelope.VIOLATION_CODES``), and a code it did not declare answers
    "cannot assess" when it runs. A name already taken, a malformed name, an unknown rule, or no code or an unknown
    one is refused with ``ValueError``."""
    if not _NAME.fullmatch(name or ""):
        raise ValueError(f"{name!r} is not a check name (lower case letters, digits and underscores)")
    if name in _CHECKS:
        raise ValueError(f"a check named {name!r} is already registered")
    unknown = [r for r in rules if r not in RULES]
    if not rules or unknown:
        raise ValueError(f"check {name!r} must name the rules it applies, each one in sources.RULES: {unknown}")
    stray = [c for c in codes if c not in VIOLATION_CODES]
    if not codes or stray:
        raise ValueError(f"check {name!r} must declare the codes it reports, each one in envelope.VIOLATION_CODES: "
                         f"{stray}")
    check = Check(name, run, tuple(rules), tuple(codes))
    _CHECKS[name] = check
    return check


def registered_checks() -> tuple[Check, ...]:
    """The registered checks, in the order they run."""
    return tuple(_CHECKS.values())


def _time_at(plan: Plan, segment: int, s: float) -> float | None:
    a, b = plan.waypoints[segment].t, plan.waypoints[min(segment + 1, len(plan.waypoints) - 1)].t
    return None if a is None or b is None else a + s * (b - a)


def _segments(plan: Plan) -> list[tuple[int, list[float], list[float]]]:
    """Each segment as ``(index, start, end)``; a plan of one waypoint is one segment that does not move."""
    q = [w.q for w in plan.waypoints]
    if len(q) == 1:
        return [(0, q[0], q[0])]
    return [(i, q[i], q[i + 1]) for i in range(len(q) - 1)]


# --- a point body: closed form along each segment ---------------------------------------------------------------------

def point_first_contact(a: list[float], b: list[float], body: PointBody,
                        world: World) -> tuple[float, dict[str, Any]] | None:
    """The first fraction along the segment from ``a`` to ``b`` at which the body comes within its clearance of an
    obstacle, with which obstacle and how deep the segment goes; ``None`` when no point of the segment does."""
    best: tuple[float, dict[str, Any]] | None = None
    for index, ob in enumerate(world.obstacles):
        s = geo.segment_entry(a, b, ob.centre, ob.radius + body.radius + body.clearance)
        if s is not None and (best is None or s < best[0]):
            deepest = geo.lerp(a, b, geo.closest_on_segment(ob.centre, a, b))
            gap, over = geo.overlap(deepest, body.radius, ob.centre, ob.radius, body.clearance)
            best = (s, {"obstacle": ob.name or index, "entry": geo.lerp(a, b, s), "deepest": deepest,
                        "distance_between_centres": gap, "overlap": over})
    return best


def point_first_exit(a: list[float], b: list[float], body: PointBody,
                     world: World) -> tuple[float, dict[str, Any]] | None:
    """The first fraction along the segment at which the body, its radius included, leaves the workspace box. Each
    side's gap changes linearly along a segment, so it is solved exactly."""
    best: tuple[float, dict[str, Any]] | None = None
    for axis, (lo, hi) in enumerate(zip(world.lower, world.upper)):
        for side, g0, g1 in (("lower", a[axis] - lo - body.radius, b[axis] - lo - body.radius),
                             ("upper", hi - body.radius - a[axis], hi - body.radius - b[axis])):
            g0, g1 = (geo.finite(g, f"the gap to the {side} side of axis {axis}") for g in (g0, g1))
            if g0 < geo.TOLERANCE:
                s = 0.0
            elif g1 < geo.TOLERANCE:
                s = (g0 - geo.TOLERANCE) / (g0 - g1)
            else:
                continue
            if best is None or s < best[0]:
                best = (s, {"axis": axis, "side": side, "at_point": geo.lerp(a, b, s)})
    return best


def point_segment_clear(a: list[float], b: list[float], body: PointBody, world: World) -> bool:
    """Whether the whole segment keeps the body inside the workspace and clear of every obstacle (what the reference
    planners test each edge with)."""
    return point_first_contact(a, b, body, world) is None and point_first_exit(a, b, body, world) is None


# --- an arm: steps no point of the arm can cross ----------------------------------------------------------------------

def _arm_gap(q: list[float], body: PlanarArm, world: World, what: str) -> tuple[float, dict[str, Any], int]:
    """The arm's smallest gap at ``q`` (to an obstacle less its clearance, or to the workspace's walls), what it is
    to, and the distance evaluations it took. A gap that is not a finite number raises ``ArithmeticError``."""
    points = geo.chain_points(body.base, body.link_lengths, q)
    best, where = math.inf, {}
    if what == "collision":
        for index, ob in enumerate(world.obstacles):
            for link in range(len(points) - 1):
                gap = geo.finite(geo.segment_distance(ob.centre, points[link], points[link + 1]) - ob.radius
                                 - body.link_radius - body.clearance,
                                 f"the gap from link {link} to obstacle {ob.name or index}")
                if gap < best:
                    best, where = gap, {"obstacle": ob.name or index, "link": link}
        return best, where, max(1, len(world.obstacles) * (len(points) - 1))
    for index, point in enumerate(points):
        gap, axis, side = geo.wall_gap(point, world.lower, world.upper)
        gap = geo.finite(gap - body.link_radius, f"the gap from joint point {index} to the workspace's walls")
        if gap < best:
            best, where = gap, {"joint_point": index, "axis": axis, "side": side}
    return best, where, len(points)


def _arm_segment(qa: list[float], qb: list[float], body: PlanarArm, world: World, what: str,
                 work: _Work) -> tuple[str, float, dict[str, Any]]:
    """Walk the segment from ``qa`` to ``qb`` in steps no point of the arm can cross (rule ``swept_arm``): from a gap
    ``g`` the arm may move ``g / bound`` of the way, where ``bound`` is the most any point moves over the whole
    segment. Answers ``("violation", s, where)``, ``("clear", 1, {})`` or ``("cannot", s, {})``."""
    if what == "collision" and not world.obstacles:
        return "clear", 1.0, {}  # no obstacle was given: nothing to reach (the result says so)
    delta = [b - a for a, b in zip(qa, qb)]
    bound = geo.finite(sum(abs(d) * c for d, c in zip(delta, geo.reach_constants(body.link_lengths))),
                       "the most any point of the arm moves along the segment")
    s = 0.0
    while True:
        gap, where, cost = _arm_gap(geo.lerp(qa, qb, s), body, world, what)
        work.left -= cost
        if work.left < 0:  # rule work_bound: past the plan's distance evaluations, it cannot assess
            return "cannot", s, {}
        if gap < geo.TOLERANCE:
            return "violation", s, {**where, "gap": gap}
        if s >= 1.0 or bound == 0.0:
            return "clear", 1.0, {}
        s = min(1.0, s + gap / bound)


def _arm_swept(case: Case, what: str, code: str, rules: tuple[str, ...]) -> Outcome:
    body = case.body
    assert isinstance(body, PlanarArm)
    out = Outcome(rules=rules)
    for i, qa, qb in _segments(case.plan):
        status, s, where = _arm_segment(qa, qb, body, case.world, what, case.work)
        if status == "cannot":
            out.cannot_assess = (f"this plan's arm checks needed more than {WORK_BOUND} distance evaluations between "
                                 f"them; the {what} check stopped {s:.3g} of the way along segment {i}, and the rest "
                                 "of the plan is not checked")
            break
        if status == "violation":
            to = (f"obstacle {where['obstacle']}" if what == "collision"
                  else f"the {where['side']} wall of axis {where['axis']}")
            subject = f"link {where['link']}" if what == "collision" else f"joint point {where['joint_point']}"
            out.found(
                check=what, code=code, at=i + s, time=_time_at(case.plan, i, s), segment=i,
                message=f"{subject} reaches {to} {s:.3g} of the way along segment {i}",
                detail={**where, "configuration": geo.lerp(qa, qb, s)})
    return out


# --- the five checks --------------------------------------------------------------------------------------------------

def _joint_limits(case: Case) -> Outcome:
    body = case.body
    if not isinstance(body, PlanarArm):
        return Outcome(not_applicable="a point body has no joints")
    out = Outcome(assumptions=("A segment whose two waypoints are inside the joint limits stays inside them, since "
                               "the joints move along a straight line between waypoints.",))
    for i, w in enumerate(case.plan.waypoints):
        for angle, joint in zip(w.q, body.joints):
            if angle < joint.lower or angle > joint.upper:
                side, limit = ("lower", joint.lower) if angle < joint.lower else ("upper", joint.upper)
                out.found(
                    check="joint_limits", code=JOINT_LIMIT, at=float(i), time=w.t, waypoint=i,
                    message=f"joint {joint.name} is at {angle:.6g} rad at waypoint {i}, beyond its {side} limit "
                            f"{limit:.6g} rad",
                    detail={"joint": joint.name, "angle": angle, "lower": joint.lower, "upper": joint.upper,
                            "beyond_by": abs(angle - limit)})
    return out


def _speed_limits(case: Case) -> Outcome:
    wps = case.plan.waypoints
    if len(wps) < 2:
        return Outcome(not_applicable="a plan of one waypoint does not move")
    untimed = [i for i, w in enumerate(wps) if w.t is None]
    if untimed:
        return Outcome(cannot_assess=f"waypoints {untimed[:10]} have no time t, so speed against the limit cannot be "
                                     "assessed; give every waypoint its time")
    out = Outcome(assumptions=("The speed checked is the average over each segment; a controller can go faster "
                               "inside a segment, so any margin below the limit is the caller's to choose.",))
    body = case.body
    for i in range(len(wps) - 1):
        a, b = wps[i], wps[i + 1]
        dt = b.t - a.t
        if dt <= 0:
            out.found(
                check="speed_limits", code=TIMING, at=float(i), time=a.t, segment=i,
                message=f"time does not increase from waypoint {i} (t={a.t:.6g} s) to {i + 1} (t={b.t:.6g} s)",
                detail={"t_start": a.t, "t_end": b.t})
            continue
        if isinstance(body, PointBody):
            speed = geo.distance(a.q, b.q) / dt
            if speed > body.max_speed:
                out.found(
                    check="speed_limits", code=VELOCITY_LIMIT, at=float(i), time=a.t, segment=i,
                    message=f"segment {i} needs {speed:.6g} m/s, above the limit {body.max_speed:.6g} m/s",
                    detail={"speed": speed, "limit": body.max_speed})
            continue
        for qa, qb, joint in zip(a.q, b.q, body.joints):
            rate = abs(qb - qa) / dt
            if rate > joint.max_velocity:
                out.found(
                    check="speed_limits", code=VELOCITY_LIMIT, at=float(i), time=a.t, segment=i,
                    message=f"joint {joint.name} needs {rate:.6g} rad/s on segment {i}, above its limit "
                            f"{joint.max_velocity:.6g} rad/s",
                    detail={"joint": joint.name, "speed": rate, "limit": joint.max_velocity})
    return out


def _workspace(case: Case) -> Outcome:
    body = case.body
    if isinstance(body, PlanarArm):
        return _arm_swept(case, "workspace", OUT_OF_WORKSPACE, ("workspace", *_ARM_SWEPT))
    out = Outcome(rules=("workspace", *_POINT_SWEPT))
    for i, a, b in _segments(case.plan):
        found = point_first_exit(a, b, body, case.world)
        if found:
            s, where = found
            out.found(
                check="workspace", code=OUT_OF_WORKSPACE, at=i + s, time=_time_at(case.plan, i, s), segment=i,
                message=f"the body leaves the workspace by the {where['side']} side of axis {where['axis']} {s:.3g} "
                        f"of the way along segment {i}", detail=where)
    return out


def _collision(case: Case) -> Outcome:
    body = case.body
    said = () if case.world.obstacles else _NO_OBSTACLE
    if isinstance(body, PlanarArm):
        out = _arm_swept(case, "collision", COLLISION, ("distance", "clearance", *_ARM_SWEPT))
        out.assumptions = said
        return out
    out = Outcome(rules=("distance", "clearance", *_POINT_SWEPT), assumptions=said)
    for i, a, b in _segments(case.plan):
        found = point_first_contact(a, b, body, case.world)
        if found:
            s, where = found
            out.found(
                check="collision", code=COLLISION, at=i + s, time=_time_at(case.plan, i, s), segment=i,
                message=f"the body comes within its clearance of obstacle {where['obstacle']} {s:.3g} of the way "
                        f"along segment {i}", detail=where)
    return out


def _goal(case: Case) -> Outcome:
    last_index = len(case.plan.waypoints) - 1
    last = case.plan.waypoints[last_index]
    goal, body = case.goal, case.body
    if goal.q is not None:
        end, target, rules = list(last.q), list(goal.q), ("goal",)
    else:
        assert isinstance(body, PlanarArm) and goal.tip is not None
        end, target = geo.chain_points(body.base, body.link_lengths, last.q)[-1], list(goal.tip)
        rules = ("goal", "chain")
    miss = geo.distance(end, target)
    if miss <= goal.tolerance:
        return Outcome(rules=rules)
    return Outcome(rules=rules, violations=[Violation(
        check="goal", code=GOAL_MISSED, at=float(last_index), time=last.t, waypoint=last_index,
        message=f"the plan ends {miss:.6g} from the goal, outside its tolerance {goal.tolerance:.6g}",
        detail={"end": end, "target": target, "miss": miss, "tolerance": goal.tolerance,
                "measured_as": "joint angles" if goal.q is not None and isinstance(body, PlanarArm)
                else "position"})])


register_check("joint_limits", _joint_limits, rules=("joint_limits",), codes=(JOINT_LIMIT,))
register_check("speed_limits", _speed_limits, rules=("average_speed", "speed_limits"), codes=(TIMING, VELOCITY_LIMIT))
register_check("workspace", _workspace, rules=("workspace", "swept_point", "swept_arm", "chain", "tolerance",
                                               "work_bound"), codes=(OUT_OF_WORKSPACE,))
register_check("collision", _collision, rules=("distance", "clearance", "swept_point", "swept_arm", "chain",
                                               "tolerance", "work_bound"), codes=(COLLISION,))
register_check("goal", _goal, rules=("goal", "chain"), codes=(GOAL_MISSED,))


# --- the answer -------------------------------------------------------------------------------------------------------

def check_plan(plan: Plan, world: World, body: Body, goal: Goal, *, operation: str = "check_plan",
               request_id: str = "", run_id: str = "", today: _dt.date | None = None) -> MotionResult:
    """Run every registered check on the plan and say what was found. A violation gives ``blocked-escalated`` with
    the code of the first one along the plan; a check that could not be made, or a length or coordinate beyond the
    range the checks are made for, gives ``blocked-escalated`` with ``G6_E_MOTION_CANNOT_ASSESS``; otherwise
    ``qualified-draft``: no violation was found against the world and the limits given, which is not a safety
    clearance."""
    ids = {"request_id": request_id, "run_id": run_id}
    problems = shape_problems(plan, world, body, goal)
    if problems:
        return refuse(operation, INPUT_INVALID, "; ".join(problems), **ids)
    beyond = range_problems(plan, world, body, goal)
    if beyond:
        return refuse(operation, CANNOT_ASSESS, range_reason(beyond), verdict="cannot_assess", beyond_range=beyond,
                      **ids)
    case = Case(plan, world, body, goal)
    registered = registered_checks()
    order = {c.name: k for k, c in enumerate(registered)}
    rows: list[dict[str, Any]] = []
    violations: list[Violation] = []
    unlisted = 0  # violations counted past each check's MAX_LISTED, never built or kept
    unassessed: list[dict[str, str]] = []
    assumptions: list[str] = list(_BASE_ASSUMPTIONS)
    applied: list[str] = []
    for check in registered:
        try:
            outcome = check.run(case)
        except Exception as exc:  # noqa: BLE001 - a check that breaks cannot let the plan through
            outcome = Outcome(cannot_assess=f"the check raised {type(exc).__name__}: {exc}"[:300])
        stray = sorted({v.code for v in outcome.violations} - (set(check.codes) & VIOLATION_CODES))
        if stray:  # a check that reports more than it declared is not trusted with any of it
            why = (f"the check reported {', '.join(stray)}, which it did not declare when it was registered (it "
                   f"declares {', '.join(check.codes)}); its findings are not used")
            outcome = Outcome(cannot_assess=why[:300])
        if outcome.not_applicable:
            rows.append({"check": check.name, "result": "not applicable", "reason": outcome.not_applicable})
            continue
        applied += [r for r in (outcome.rules or check.rules) if r in check.rules]  # what this case applied
        assumptions += [a for a in outcome.assumptions if a not in assumptions]
        kept = outcome.violations[:MAX_LISTED]  # a member's check that built more keeps its earliest only
        counted = len(outcome.violations) + outcome.unlisted
        unlisted += counted - len(kept)
        violations += [v if v.check == check.name else v.model_copy(update={"check": check.name}) for v in kept]
        if outcome.cannot_assess:
            unassessed.append({"check": check.name, "reason": outcome.cannot_assess})
        result = ("violation" if outcome.violations else "cannot assess" if outcome.cannot_assess
                  else "no violation found")
        rows.append({"check": check.name, "result": result, "violations": counted,
                     **({"reason": outcome.cannot_assess} if outcome.cannot_assess else {}), **outcome.row})
    violations.sort(key=lambda v: (v.at, order.get(v.check, len(order))))
    total = len(violations) + unlisted
    rules = tuple(dict.fromkeys([*applied, "bounds"]))
    findings = stale_sources(today, rules)
    first = violations[0].model_dump(mode="json") if violations else None
    evidence = [{"kind": "plan_check", "checks": rows, "first_violation": first, "cannot_assess": unassessed,
                 "assumptions": assumptions, "rules": cited(rules)}, *findings]
    fields = {"verdict": "violation" if violations else "cannot_assess" if unassessed else "no_violation_found",
              "first_violation": first, "violations": [v.model_dump(mode="json") for v in violations[:MAX_LISTED]],
              "violations_found": total, "checks": rows, "cannot_assess": unassessed,
              "assumptions": assumptions, "source_findings": findings}
    if violations:
        more = f" ({total} violations found in all)" if total > 1 else ""
        return refuse(operation, violations[0].code, violations[0].message + more, evidence=evidence, **fields, **ids)
    if unassessed:
        why = "; ".join(f"{u['check']}: {u['reason']}" for u in unassessed)
        return refuse(operation, CANNOT_ASSESS, f"the plan is not cleared: {why}", evidence=evidence, **fields, **ids)
    return answer(operation, NO_VIOLATION_FOUND,
                  "No violation found against the world and the limits given. This is not a safety clearance: it "
                  "holds only as far as the world, the body and the plan given match the real ones.",
                  "Before anything moves, have a person check the world model, the limits and the plan against the "
                  "real robot, and send any command through g6.action_gating.",
                  evidence=evidence, **fields, **ids)


__all__ = ["MAX_LISTED", "WORK_BOUND", "Case", "Check", "Outcome", "Violation", "check_plan",
           "point_first_contact", "point_first_exit", "point_segment_clear", "register_check", "registered_checks"]
