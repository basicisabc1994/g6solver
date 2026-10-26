# SPDX-License-Identifier: Apache-2.0
"""The embodied family's motion checks (``g6.embodied._motion``): each segment is checked whole, every limit is the
caller's, missing data cannot be assessed, a broken check never lets a plan through, and the first violation along
the plan is the evidence."""
from __future__ import annotations

import dataclasses
import itertools
import math
import random

import pytest
from pydantic import ValidationError

from g6.embodied._motion import Goal, Joint, Obstacle, Plan, PlanarArm, PointBody, Waypoint, World, check_plan
from g6.embodied._motion import checks as engine
from g6.embodied._motion import geometry as geo
from g6.embodied._motion import model as motion_model
from g6.embodied._motion.checks import Outcome, Violation, point_first_contact, register_check


def point(radius: float = 0.1, max_speed: float = 1.0, clearance: float = 0.0) -> PointBody:
    return PointBody(kind="point", radius=radius, max_speed=max_speed, clearance=clearance)


def world(obstacles=(), lower=(0.0, 0.0), upper=(10.0, 10.0)) -> World:
    return World(lower=list(lower), upper=list(upper),
                 obstacles=[Obstacle(centre=list(c), radius=r) for c, r in obstacles])


def plan(*qs, times=None) -> Plan:
    return Plan(waypoints=[Waypoint(q=list(q), t=None if times is None else times[i]) for i, q in enumerate(qs)])


def goal(q, tolerance: float = 1e-9) -> Goal:
    return Goal(q=list(q), tolerance=tolerance)


def arm(lengths=(1.0,), joints=None, link_radius: float = 0.0, clearance: float = 0.0, base=(0.0, 0.0)) -> PlanarArm:
    joints = joints or [Joint(name=f"j{i}", lower=-3.0, upper=3.0, max_velocity=10.0) for i in range(len(lengths))]
    return PlanarArm(kind="planar_arm", base=list(base), link_lengths=list(lengths), link_radius=link_radius,
                     joints=joints, clearance=clearance)


# --- D1: a plan that collides between clear waypoints is caught -------------------------------------------------------

def test_d1_a_segment_through_an_obstacle_between_clear_waypoints_is_caught() -> None:
    # The archive's own failure: a 1 m step past a 0.3 m obstacle, both ends 0.5 m from its centre.
    w = world([((5.0, 5.0), 0.3)])
    r = check_plan(plan((4.5, 5.0), (5.5, 5.0), times=(0.0, 2.0)), w, point(radius=0.0), goal((5.5, 5.0)))
    assert r.completion_state == "blocked-escalated" and r.verdict == "violation"
    assert r.first_violation["code"] == "G6_E_MOTION_COLLISION" and r.warning_card["code"] == "G6_E_MOTION_COLLISION"
    assert r.first_violation["at"] == pytest.approx(0.2)  # it enters at x = 4.7, a fifth of the way along
    assert r.first_violation["time"] == pytest.approx(0.4)


def test_d1_random_segments_with_clear_ends_never_pass_an_obstacle_unseen() -> None:
    """Cases the check was not written around: random 2-D and 3-D segments whose ends are clear; a dense sample along
    each segment is the independent oracle."""
    rng = random.Random(20261021)
    crossing = 0
    for _ in range(400):
        dims = rng.choice((2, 3))
        centre = [rng.uniform(3.0, 7.0) for _ in range(dims)]
        r_ob, body = rng.uniform(0.1, 1.0), point(radius=rng.uniform(0.0, 0.3), clearance=rng.uniform(0.0, 0.2))
        reach = r_ob + body.radius + body.clearance
        a, b = ([rng.uniform(0.0, 10.0) for _ in range(dims)] for _ in range(2))
        if min(geo.distance(a, centre), geo.distance(b, centre)) <= reach:
            continue
        w = World(lower=[0.0] * dims, upper=[10.0] * dims, obstacles=[Obstacle(centre=centre, radius=r_ob)])
        if any(geo.distance(geo.lerp(a, b, k / 4000), centre) < reach for k in range(4001)):
            crossing += 1
            assert point_first_contact(a, b, body, w) is not None, (a, b, centre, reach)
    assert crossing >= 20  # the cases did reach the check


def test_d1_an_arm_that_sweeps_through_an_obstacle_between_clear_waypoints_is_caught() -> None:
    body, w = arm(), world([((1.0, 0.0), 0.1)], lower=(-2.0, -2.0), upper=(2.0, 2.0))
    for q in (-0.5, 0.5):  # each waypoint alone is clear: the tip is 0.49 m from the obstacle
        assert check_plan(plan((q,)), w, body, goal((q,))).completion_state == "qualified-draft"
    r = check_plan(plan((-0.5,), (0.5,), times=(0.0, 1.0)), w, body, goal((0.5,)))
    assert r.first_violation["code"] == "G6_E_MOTION_COLLISION" and r.first_violation["detail"]["link"] == 0
    # contact when the link passes 0.1 m from the obstacle's centre, at an angle of -asin(0.1)
    assert r.first_violation["at"] == pytest.approx(0.5 - math.asin(0.1), abs=1e-6)


def test_d1_random_arm_sweeps_never_pass_an_obstacle_unseen() -> None:
    rng = random.Random(7)
    crossing = 0
    for _ in range(60):
        n = rng.choice((2, 3))
        lengths = [rng.uniform(0.3, 0.8) for _ in range(n)]
        body = arm(lengths=lengths, link_radius=rng.uniform(0.0, 0.05), clearance=rng.uniform(0.0, 0.05))
        ob = Obstacle(centre=[rng.uniform(-1.5, 1.5), rng.uniform(-1.5, 1.5)], radius=rng.uniform(0.05, 0.3))
        w = World(lower=[-5.0, -5.0], upper=[5.0, 5.0], obstacles=[ob])
        qa, qb = ([rng.uniform(-2.5, 2.5) for _ in range(n)] for _ in range(2))

        def gap(q):
            pts = geo.chain_points(body.base, lengths, q)
            return min(geo.segment_distance(ob.centre, pts[k], pts[k + 1]) for k in range(n)) - (
                ob.radius + body.link_radius + body.clearance)

        if min(gap(qa), gap(qb)) <= 0:
            continue
        if any(gap(geo.lerp(qa, qb, k / 2000)) < 0 for k in range(2001)):
            crossing += 1
            r = check_plan(plan(qa, qb, times=(0.0, 100.0)), w, body, goal(qb))
            assert r.first_violation and r.first_violation["code"] == "G6_E_MOTION_COLLISION", (lengths, qa, qb)
    assert crossing >= 5


# --- the limits are the caller's, checked at every waypoint and segment ---------------------------------------------

def test_p1_03_each_waypoint_is_checked_against_the_arms_own_joint_limits() -> None:
    joints = [Joint(name="shoulder", lower=-1.0, upper=1.0, max_velocity=2.0),
              Joint(name="elbow", lower=0.0, upper=2.0, max_velocity=2.0)]
    body = arm(lengths=(0.5, 0.3), joints=joints)
    w = world(lower=(-2.0, -2.0), upper=(2.0, 2.0))
    r = check_plan(plan((0.0, 0.5), (0.5, -0.2), (0.6, 0.4), times=(0.0, 1.0, 2.0)), w, body, goal((0.6, 0.4)))
    first = r.first_violation
    assert (first["code"], first["waypoint"], first["detail"]["joint"]) == ("G6_E_MOTION_JOINT_LIMIT", 1, "elbow")
    assert first["detail"]["beyond_by"] == pytest.approx(0.2)


def test_p1_03_a_joint_moving_faster_than_its_limit_is_caught() -> None:
    joints = [Joint(name="shoulder", lower=-3.0, upper=3.0, max_velocity=2.0)]
    r = check_plan(plan((0.0,), (1.5,), times=(0.0, 0.5)), world(lower=(-2, -2), upper=(2, 2)),
                   arm(joints=joints), goal((1.5,)))
    assert r.first_violation["code"] == "G6_E_MOTION_VELOCITY_LIMIT"
    assert r.first_violation["detail"] == {"joint": "shoulder", "speed": pytest.approx(3.0), "limit": 2.0}


def test_a_point_body_faster_than_its_top_speed_is_caught() -> None:
    r = check_plan(plan((1.0, 1.0), (4.0, 5.0), times=(0.0, 2.0)), world(), point(max_speed=2.0), goal((4.0, 5.0)))
    assert r.first_violation["code"] == "G6_E_MOTION_VELOCITY_LIMIT"
    assert r.first_violation["detail"]["speed"] == pytest.approx(2.5)


def test_time_that_does_not_increase_is_a_violation() -> None:
    r = check_plan(plan((1.0, 1.0), (1.5, 1.0), times=(3.0, 2.0)), world(), point(), goal((1.5, 1.0)))
    assert r.first_violation["code"] == "G6_E_MOTION_TIMING"


def test_the_goal_is_checked_so_a_plan_that_stops_short_is_caught() -> None:
    # The archive's RRT ended within 1.5 of the goal and never reached it.
    r = check_plan(plan((1.0, 1.0), (7.8, 8.0), times=(0.0, 20.0)), world(), point(), goal((9.0, 9.0), 0.01))
    assert r.first_violation["code"] == "G6_E_MOTION_GOAL_MISSED"
    assert r.first_violation["detail"]["miss"] == pytest.approx(math.hypot(1.2, 1.0))


def test_an_arms_tip_goal_is_checked_through_its_own_link_lengths() -> None:
    body, w = arm(lengths=(0.5, 0.3)), world(lower=(-2, -2), upper=(2, 2))
    ok = check_plan(plan((0.0, 0.0), (math.pi / 2, -math.pi / 2), times=(0.0, 2.0)), w, body,
                    Goal(tip=[0.3, 0.5], tolerance=1e-9))
    assert ok.completion_state == "qualified-draft"
    miss = check_plan(plan((0.0, 0.0), (math.pi / 2, 0.0), times=(0.0, 2.0)), w, body,
                      Goal(tip=[0.3, 0.5], tolerance=1e-3))
    assert miss.first_violation["code"] == "G6_E_MOTION_GOAL_MISSED"


def test_leaving_the_workspace_is_caught_for_a_point_body_with_its_radius() -> None:
    r = check_plan(plan((5.0, 5.0), (9.95, 5.0), times=(0.0, 10.0)), world(), point(radius=0.1), goal((9.95, 5.0)))
    assert r.first_violation["code"] == "G6_E_MOTION_OUT_OF_WORKSPACE"
    # the body's edge reaches x = 10 when its centre reaches 9.9
    assert r.first_violation["detail"]["side"] == "upper" and r.first_violation["at"] == pytest.approx(4.9 / 4.95)


def test_an_arm_that_sweeps_out_of_the_workspace_between_inside_waypoints_is_caught() -> None:
    w = world(lower=(-1.2, -1.2), upper=(0.9, 1.2))
    for q in (-0.6, 0.6):  # the tip is at x = 0.825 at each waypoint, inside x <= 0.9
        assert check_plan(plan((q,)), w, arm(), goal((q,))).completion_state == "qualified-draft"
    r = check_plan(plan((-0.6,), (0.6,), times=(0.0, 1.0)), w, arm(), goal((0.6,)))
    assert r.first_violation["code"] == "G6_E_MOTION_OUT_OF_WORKSPACE"
    assert r.first_violation["detail"]["side"] == "upper"


def test_a_clear_plan_is_a_qualified_draft_and_says_it_is_not_a_clearance() -> None:
    r = check_plan(plan((1.0, 1.0), (2.0, 1.0), (2.0, 2.0), times=(0.0, 2.0, 4.0)), world([((5.0, 5.0), 1.0)]),
                   point(), goal((2.0, 2.0)))
    assert (r.completion_state, r.verdict, r.first_violation) == ("qualified-draft", "no_violation_found", None)
    assert r.warning_card["code"] == "G6_E_MOTION_NO_VIOLATION_FOUND" and "not a safety clearance" in \
        r.warning_card["message"]
    # grasp.py's register_check adds the sixth; a plan that holds nothing has no grasp to check
    assert [c["check"] for c in r.checks] == ["joint_limits", "speed_limits", "workspace", "collision", "goal",
                                              "grasp_margin"]
    assert r.checks[0]["result"] == r.checks[-1]["result"] == "not applicable"
    assert {e["rule"] for e in r.evidence[0]["rules"]} >= {"distance", "swept_point", "speed_limits", "goal"}


def test_every_violation_is_listed_in_plan_order_and_the_first_is_the_evidence() -> None:
    w = world([((5.0, 5.0), 0.3)])
    r = check_plan(plan((4.5, 5.0), (5.5, 5.0), (9.0, 9.0), times=(0.0, 0.1, 0.2)), w, point(radius=0.0),
                   goal((1.0, 1.0)))
    codes = [v["code"] for v in r.violations]
    assert codes[0] == "G6_E_MOTION_VELOCITY_LIMIT"  # segment 0 is too fast before it reaches the obstacle
    assert set(codes) == {"G6_E_MOTION_VELOCITY_LIMIT", "G6_E_MOTION_COLLISION", "G6_E_MOTION_GOAL_MISSED"}
    assert [v["at"] for v in r.violations] == sorted(v["at"] for v in r.violations)
    assert r.evidence[0]["first_violation"] == r.first_violation == r.violations[0]


# --- missing data, a broken check, too much work: never a pass -------------------------------------------------------

def test_violations_past_the_listing_cap_are_counted_but_never_built_by_the_built_in_checks() -> None:
    # 300 waypoints each beyond a joint's limit. Removing Outcome.found's `if len(self.violations) < MAX_LISTED:` cap
    # builds all 300 before the result lists 200.
    joints = [Joint(name="j", lower=-1.0, upper=1.0, max_velocity=1.0)]
    body, w = arm(joints=joints), world(lower=(-2, -2), upper=(2, 2))
    many = plan(*[(1.5,)] * 300, times=[float(i) for i in range(300)])
    out = engine._CHECKS["joint_limits"].run(engine.Case(many, w, body, goal((1.5,))))
    assert (len(out.violations), out.unlisted) == (engine.MAX_LISTED, 300 - engine.MAX_LISTED)
    r = check_plan(many, w, body, goal((1.5,)))
    assert (len(r.violations), r.violations_found, r.checks[0]["violations"]) == (engine.MAX_LISTED, 300, 300)
    assert r.violations[0]["waypoint"] == 0 and "300 violations found in all" in r.reason


def test_g4_waypoints_without_times_cannot_be_assessed_for_speed() -> None:
    r = check_plan(plan((1.0, 1.0), (2.0, 1.0)), world(), point(), goal((2.0, 1.0)))
    assert (r.completion_state, r.code, r.verdict) == ("blocked-escalated", "G6_E_MOTION_CANNOT_ASSESS",
                                                       "cannot_assess")
    assert r.cannot_assess[0]["check"] == "speed_limits" and "no time" in r.cannot_assess[0]["reason"]


def test_g4_a_check_that_raises_cannot_let_the_plan_through(monkeypatch) -> None:
    monkeypatch.setattr(engine, "_CHECKS", dict(engine._CHECKS))

    def broken(case):
        raise RuntimeError("sensor table unreadable")

    register_check("broken_probe", broken, rules=("clearance",), codes=("G6_E_MOTION_COLLISION",))
    r = check_plan(plan((1.0, 1.0), (2.0, 1.0), times=(0.0, 2.0)), world(), point(), goal((2.0, 1.0)))
    assert (r.completion_state, r.code) == ("blocked-escalated", "G6_E_MOTION_CANNOT_ASSESS")
    assert "sensor table unreadable" in r.reason


def test_g4_an_arm_check_past_its_work_bound_cannot_assess(monkeypatch) -> None:
    monkeypatch.setattr(engine, "WORK_BOUND", 3)
    r = check_plan(plan((-0.5,), (0.5,), times=(0.0, 1.0)), world([((1.5, 1.5), 0.1)], lower=(-2, -2), upper=(2, 2)),
                   arm(), goal((0.5,)))
    assert (r.completion_state, r.code) == ("blocked-escalated", "G6_E_MOTION_CANNOT_ASSESS")


def test_shapes_that_do_not_fit_are_refused_with_a_code() -> None:
    r = check_plan(plan((1.0, 1.0, 1.0)), world(), point(), goal((1.0, 1.0)))
    assert (r.completion_state, r.code) == ("blocked-escalated", "G6_E_MOTION_INPUT_INVALID")
    r = check_plan(plan((0.0,)), world(lower=(-1, -1, -1), upper=(1, 1, 1)), arm(), goal((0.0,)))
    assert r.code == "G6_E_MOTION_INPUT_INVALID" and "2-D" in r.reason


# --- lengths and coordinates beyond the stated range, and arithmetic that overflows: never a pass --------------------

def _huge_arm():
    # The reviewer's case: a 1e155 m link sweeps through an obstacle at [5e154, 0]; its dot products overflow.
    body = arm(lengths=(1e155,), joints=[Joint(name="j0", lower=-1.0, upper=1.0, max_velocity=1.0)])
    w = world([((5e154, 0.0), 1.0)], lower=(-1e200, -1e200), upper=(1e200, 1e200))
    return plan((-0.1,), (0.1,), times=(0.0, 1.0)), w, body, goal((0.1,))


def _huge_point():
    # A point body straight through a 1e10 m obstacle, 1e150 m either side of it: the entry's discriminant (squared
    # length times the reach's square, about 4e320) overflows.
    w = world([((0.0, 0.0), 1e10)], lower=(-1e200, -1e200), upper=(1e200, 1e200))
    return plan((-1e150, 0.0), (1e150, 0.0), times=(0.0, 1e151)), w, point(radius=0.0), goal((1e150, 0.0))


@pytest.mark.parametrize(("case", "field"), [(_huge_arm, "robot.link_lengths"), (_huge_point, "plan.waypoints[0].q")],
                         ids=["arm", "point"])
def test_lengths_and_coordinates_beyond_the_stated_range_cannot_be_assessed(case, field) -> None:
    r = check_plan(*case())
    assert (r.completion_state, r.code) == ("blocked-escalated", "G6_E_MOTION_CANNOT_ASSESS")
    assert field in r.reason and "world.lower" in r.reason and "1,000,000 m" in r.reason


@pytest.mark.parametrize("case", [_huge_arm, _huge_point], ids=["arm", "point"])
def test_a_gap_discriminant_or_root_that_is_not_finite_cannot_assess_never_clear(case, monkeypatch) -> None:
    """With the range lifted the arithmetic itself overflows: what is not a finite number answers cannot assess."""
    monkeypatch.setattr(motion_model, "MAX_EXTENT", math.inf, raising=False)
    r = check_plan(*case())
    assert (r.completion_state, r.code) == ("blocked-escalated", "G6_E_MOTION_CANNOT_ASSESS")
    assert [u["check"] for u in r.cannot_assess] == ["collision"] and "finite" in r.cannot_assess[0]["reason"]


def test_at_the_bound_no_square_or_product_overflows_and_a_sweep_is_still_caught() -> None:
    """Every length and coordinate at the bound, in every corner: the entry's terms stay finite numbers, so a value
    inside the bound cannot make a NaN, and a sweep at the bound is caught with a finite answer."""
    m = motion_model.MAX_EXTENT
    assert m == 1e6
    corners = [list(c) for c in itertools.product((-m, m), repeat=3)]
    for a, b, centre in itertools.product(corners, repeat=3):
        for reach in (3 * m, 0.0):  # an obstacle's radius, the body's radius and its clearance each at the bound
            s = geo.segment_entry(a, b, centre, reach)
            assert s is None or 0.0 <= s <= 1.0
    w = World(lower=[-m] * 3, upper=[m] * 3, obstacles=[Obstacle(centre=[0.0, 0.0, 0.0], radius=m / 2)])
    r = check_plan(plan([1 - m] * 3, [m - 1] * 3, times=(0.0, 1e7)), w, point(radius=0.0), goal([m - 1] * 3))
    assert r.first_violation["code"] == "G6_E_MOTION_COLLISION" and r.cannot_assess == []
    body = arm(lengths=(m,) * 12, link_radius=m, clearance=m, base=(-m, -m))
    w = world([((m, m), m)], lower=(-m, -m), upper=(m, m))
    r = check_plan(plan((0.0,) * 12, (0.1,) * 12, times=(0.0, 1.0)), w, body, goal((0.1,) * 12))
    assert r.cannot_assess == [] and {v["code"] for v in r.violations} == {"G6_E_MOTION_COLLISION",
                                                                           "G6_E_MOTION_OUT_OF_WORKSPACE"}
    assert all(math.isfinite(v["detail"]["gap"]) for v in r.violations)


def test_a_passing_plan_says_plainly_what_it_did_not_check() -> None:
    r = check_plan(plan((1.0, 1.0), (2.0, 1.0), times=(0.0, 2.0)), world(), point(), goal((2.0, 1.0)))
    assert r.completion_state == "qualified-draft"
    said = " ".join(r.assumptions)
    # the archive's validate_twist checked a base's turning rate; removing it from _BASE_ASSUMPTIONS fails this
    for unchecked in ("acceleration", "effort", "a point body's turning rate", "dynamics", "contact", "sensing",
                      "localisation", "controller's tracking error", "waypoint 0 is not checked"):
        assert unchecked in said, unchecked
    assert "No obstacle was given" in said and r.evidence[0]["assumptions"] == r.assumptions
    walled = check_plan(plan((1.0, 1.0), (2.0, 1.0), times=(0.0, 2.0)), world([((5.0, 5.0), 1.0)]), point(),
                        goal((2.0, 1.0)))
    assert walled.completion_state == "qualified-draft" and "No obstacle was given" not in " ".join(walled.assumptions)


@pytest.mark.parametrize("bad", [
    {"lower": [0, 0], "upper": [1, 1]},                                    # obstacles left out: not "none"
    {"lower": [0, 0], "upper": [1, 1], "obstacles": [], "approved": True},  # a caller's word
    {"lower": [1, 0], "upper": [0, 1], "obstacles": []},
])
def test_g3_a_world_without_its_data_or_with_a_claim_is_refused(bad) -> None:
    with pytest.raises(ValidationError):
        World.model_validate(bad)


@pytest.mark.parametrize("bad", [
    {"kind": "point", "radius": 0.1, "max_speed": 1.0},                                     # no clearance given
    {"kind": "point", "radius": 0.1, "max_speed": 1.0, "clearance": 0.0, "certified": True},
    {"kind": "point", "radius": 0.1, "max_speed": 1.0, "clearance": 0.0, "safety_envelope": {"sil": 2}},
    {"kind": "point", "radius": -0.1, "max_speed": 1.0, "clearance": 0.0},
])
def test_g3_a_body_without_its_limits_or_with_a_claim_is_refused(bad) -> None:
    with pytest.raises(ValidationError):
        PointBody.model_validate(bad)


def test_a_joint_needs_its_own_limits() -> None:
    with pytest.raises(ValidationError):
        Joint(name="j", lower=1.0, upper=-1.0, max_velocity=1.0)
    with pytest.raises(ValidationError):
        Joint.model_validate({"name": "j", "lower": -1.0, "upper": 1.0})


# --- D5: a member adds its own check ----------------------------------------------------------------------------------

def test_d5_a_check_registered_from_outside_runs_in_every_check_plan(monkeypatch) -> None:
    monkeypatch.setattr(engine, "_CHECKS", dict(engine._CHECKS))

    def payload(case):
        return Outcome([Violation(check="payload", code="G6_E_MOTION_JOINT_LIMIT", at=0.0,
                                  message="the payload is above the gripper's limit")])

    register_check("payload", payload, rules=("joint_limits",), codes=("G6_E_MOTION_JOINT_LIMIT",))
    r = check_plan(plan((1.0, 1.0), (2.0, 1.0), times=(0.0, 2.0)), world(), point(), goal((2.0, 1.0)))
    assert r.first_violation["check"] == "payload" and r.checks[-1]["check"] == "payload"


def test_d5_a_check_must_declare_the_codes_it_reports_and_each_must_be_known(monkeypatch) -> None:
    monkeypatch.setattr(engine, "_CHECKS", dict(engine._CHECKS))
    # the probe's name is free (grasp_margin is a real check now); removing register_check's codes test fails this
    with pytest.raises(ValueError, match="codes"):
        register_check("payload_limit", lambda case: Outcome(), rules=("joint_limits",))
    for codes in (("G6_E_MOTION_GRASP_MARGIN",), ("G6_E_MOTION_CANNOT_ASSESS",)):  # unknown; not a violation's
        with pytest.raises(ValueError, match="codes"):
            register_check("payload_limit", lambda case: Outcome(), rules=("joint_limits",), codes=codes)
    assert "payload_limit" not in engine._CHECKS


@pytest.mark.parametrize("code", ["G6_E_MOTION_GRASP_MARGIN", "G6_E_MOTION_COLLISION"])
def test_a_check_reporting_a_code_it_did_not_declare_cannot_assess_and_never_crashes(code, monkeypatch) -> None:
    def grasp(case):
        return Outcome([Violation(check="grasp_margin", code=code, at=0.0, message="the grasp slips")])

    # set in past registration, as a member's check that reports more than it declared would run (it declares the
    # goal check's code, G6_E_MOTION_GOAL_MISSED)
    monkeypatch.setitem(engine._CHECKS, "grasp_margin",
                        dataclasses.replace(engine._CHECKS["goal"], name="grasp_margin", run=grasp))
    r = check_plan(plan((1.0, 1.0), (2.0, 1.0), times=(0.0, 2.0)), world(), point(), goal((2.0, 1.0)))
    assert (r.completion_state, r.code) == ("blocked-escalated", "G6_E_MOTION_CANNOT_ASSESS")
    assert r.cannot_assess[0]["check"] == "grasp_margin" and code in r.cannot_assess[0]["reason"]


def test_d5_a_check_must_name_known_rules_and_a_free_name() -> None:
    codes = ("G6_E_MOTION_COLLISION",)
    with pytest.raises(ValueError):
        register_check("collision", lambda case: Outcome(), rules=("distance",), codes=codes)
    with pytest.raises(ValueError):
        register_check("from_memory", lambda case: Outcome(), rules=("a_rule_nobody_wrote_down",), codes=codes)
    with pytest.raises(ValueError):
        register_check("no_rules", lambda case: Outcome(), rules=(), codes=codes)


# --- the kept vector maths --------------------------------------------------------------------------------------------

def test_the_kept_overlap_arithmetic_and_the_closed_form_entry_agree() -> None:
    gap, over = geo.overlap([0.0, 0.0], 0.5, [0.8, 0.0], 0.5)
    assert (gap, over) == (pytest.approx(0.8), pytest.approx(0.2))
    assert geo.segment_entry([-2.0, 0.0], [2.0, 0.0], [0.0, 0.0], 1.0) == pytest.approx(0.25)
    assert geo.segment_entry([-2.0, 2.0], [2.0, 2.0], [0.0, 0.0], 1.0) is None


def test_a_long_segment_just_inside_reach_near_tangency_is_caught() -> None:
    # Removing segment_entry's perpendicular-distance discriminant (half_b^2 - length2*c cancels) misses these.
    from fractions import Fraction
    a, b = [-1e6, 0.99999], [1e6, 0.99999]  # 0.99999 m from the centre: 1e-5 m inside a 1 m reach
    assert geo.segment_distance([0.0, 0.0], a, b) == pytest.approx(0.99999)
    assert geo.segment_entry(a, b, [0.0, 0.0], 1.0) == pytest.approx(0.5 - math.sqrt(1.000000001**2 - 0.99999**2) / 2e6)
    w = world([((0.0, 0.0), 1.0)], lower=(-1e6, -1e6), upper=(1e6, 1e6))
    r = check_plan(plan((1 - 1e6, 0.99999), (1e6 - 1, 0.99999), times=(0.0, 1e7)), w, point(radius=0.0),
                   goal((1e6 - 1, 0.99999)))
    assert r.first_violation["code"] == "G6_E_MOTION_COLLISION"
    rng = random.Random(20261022)
    for half in (1e4, 1e5, 1e6):  # the scales at which the old form missed 2e-9, 5e-7 and 1e-4 m
        for _ in range(300):
            angle, depth = rng.uniform(0, 2 * math.pi), 10 ** rng.uniform(-10, -3)
            ux, uy = math.cos(angle), math.sin(angle)
            a, b = [-uy * (1 - depth) - ux * half, ux * (1 - depth) - uy * half], \
                [-uy * (1 - depth) + ux * half, ux * (1 - depth) + uy * half]
            fa, fu = [Fraction(v) for v in a], [Fraction(q) - Fraction(p) for p, q in zip(a, b)]
            t = min(Fraction(1), max(Fraction(0), -(fa[0] * fu[0] + fa[1] * fu[1]) / (fu[0] ** 2 + fu[1] ** 2)))
            nearest = math.sqrt(float((fa[0] + t * fu[0]) ** 2 + (fa[1] + t * fu[1]) ** 2))  # exact, then rounded
            if nearest < 1.0 + geo.TOLERANCE:
                assert geo.segment_entry(a, b, [0.0, 0.0], 1.0) is not None, (half, depth, angle)


def test_reach_constants_bound_how_far_any_point_of_the_chain_moves() -> None:
    assert geo.reach_constants([0.5, 0.3, 0.2]) == [pytest.approx(1.0), pytest.approx(0.5), pytest.approx(0.2)]
