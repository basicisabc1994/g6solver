# SPDX-License-Identifier: Apache-2.0
"""adapt_physical_ai through the registry and MCP: the motion checker, its reference planners and its two computed
answers. The planners' paths are clear along every edge and end at the goal, A* blocks each obstacle's whole
footprint, kinematics use the arm's own link lengths, nothing is ever ``verified``, a caller's word changes nothing,
missing data is never filled in, and no op reaches outside the process."""
from __future__ import annotations

import ast
import json
import math
import re
from pathlib import Path

import pytest

import g6.embodied.adapt_physical_ai as package
from g6.core import discovery
from g6.core.failure_modes import FailureModeRegistry
from g6.core.registry import get_registry
from g6.embodied._motion import geometry as geo
from g6.embodied.adapt_physical_ai import (
    Goal,
    Joint,
    Obstacle,
    PlanarArm,
    PointBody,
    World,
    forward_kinematics,
    integrate_step,
    plan_astar,
    plan_rrt,
)
from g6.mcp import describe_reply, invoke_reply

NAME = "adapt_physical_ai"
SRC = Path(package.__file__).resolve().parents[3]
WORLD = {"lower": [0, 0], "upper": [10, 10], "obstacles": [{"name": "pillar", "centre": [5, 5], "radius": 1.0}]}
POINT = {"kind": "point", "radius": 0.2, "max_speed": 1.0, "clearance": 0.1}


def _invoke(op: str, **params) -> dict:
    return invoke_reply(NAME, op, json.dumps(params))


def _edges_clear(points, world: World, body: PointBody, samples: int = 400) -> bool:
    """An independent oracle: a dense sample along every edge stays inside the workspace and out of every obstacle's
    reach."""
    for a, b in zip(points, points[1:]):
        for k in range(samples + 1):
            p = geo.lerp(a, b, k / samples)
            if any(geo.distance(p, ob.centre) < ob.radius + body.radius + body.clearance for ob in world.obstacles):
                return False
            if any(v - body.radius < lo or v + body.radius > hi for v, lo, hi in zip(p, world.lower, world.upper)):
                return False
    return True


def test_it_is_registered_in_the_embodied_family_with_no_sensitive_op() -> None:
    info = describe_reply(NAME)
    assert info["family"] == "embodied" and info["sensitive_ops"] == []
    ops = get_registry().invoke(NAME, "ops").unwrap()["ops"]
    # removing block.py's "grasp_margin" entry from _OPS fails this
    assert ops == ["check_plan", "plan_astar", "plan_rrt", "forward_kinematics", "integrate_step", "grasp_margin"]
    assert len(get_registry().invoke(NAME, "help").unwrap()["help"]) == len(ops)


def test_d6_g6_find_finds_it_by_its_failure_mode_and_describe_serves_its_skill() -> None:
    title = FailureModeRegistry.title("infeasible_motion_plan")
    assert NAME in [r["component"] for r in discovery.find(title, limit=5)]
    assert describe_reply(NAME)["skill"].startswith("---\nname: g6-adapt-physical-ai")


# --- G1: a wrong plan is never verified, a clear one is a qualified draft at best -------------------------------------

def test_g1_a_colliding_plan_is_blocked_and_a_clear_one_is_only_a_qualified_draft() -> None:
    through = {"waypoints": [{"q": [2, 5], "t": 0}, {"q": [8, 5], "t": 10}]}
    around = {"waypoints": [{"q": [2, 2], "t": 0}, {"q": [8, 2], "t": 10}]}
    bad = _invoke("check_plan", plan=through, world=WORLD, robot=POINT, goal={"q": [8, 5], "tolerance": 0.01})
    good = _invoke("check_plan", plan=around, world=WORLD, robot=POINT, goal={"q": [8, 2], "tolerance": 0.01})
    assert bad["completion_state"] == bad["result"]["completion_state"] == "blocked-escalated"
    assert bad["result"]["first_violation"]["detail"]["obstacle"] == "pillar"
    assert good["completion_state"] == good["result"]["completion_state"] == "qualified-draft"
    for reply in (bad, good):
        assert "verified" not in json.dumps(reply["result"]["completion_state"])


# --- G3: a caller's word is a claim -----------------------------------------------------------------------------------

@pytest.mark.parametrize("claim", [{"approved": True}, {"approval_confirmed": True, "dry_run": False},
                                   {"reviewer": "a named engineer"}])
def test_g3_a_callers_approval_is_refused_and_changes_nothing(claim) -> None:
    through = {"waypoints": [{"q": [2, 5], "t": 0}, {"q": [8, 5], "t": 10}]}
    reply = _invoke("check_plan", plan=through, world=WORLD, robot=POINT, goal={"q": [8, 5], "tolerance": 0.01},
                    **claim)
    assert not reply["ok"] and reply["error"].startswith("[G6_E_INPUT_VALIDATION]")
    assert reply["completion_state"] == "blocked-escalated"


def test_g3_a_robot_that_declares_its_own_safety_envelope_is_refused() -> None:
    robot = {**POINT, "safety_envelope": {"rated": True}}
    reply = _invoke("check_plan", plan={"waypoints": [{"q": [2, 2], "t": 0}]}, world=WORLD, robot=robot,
                    goal={"q": [2, 2], "tolerance": 0})
    assert not reply["ok"] and "safety_envelope" in reply["error"]


# --- G4: missing data is never filled in ------------------------------------------------------------------------------

def test_g4_an_op_missing_its_data_cannot_assess() -> None:
    reply = _invoke("check_plan", plan={"waypoints": [{"q": [2, 2], "t": 0}, {"q": [3, 2], "t": 2}]})
    result = reply["result"]
    assert (result["completion_state"], result["code"]) == ("blocked-escalated", "G6_E_MOTION_CANNOT_ASSESS")
    assert result["missing"] == ["world", "robot", "goal"]
    # removing block.py's `"grasp_margin": ("robot", "grasp")` from _NEEDS fails this
    for op in ("plan_astar", "plan_rrt", "forward_kinematics", "integrate_step", "grasp_margin"):
        assert _invoke(op)["result"]["code"] == "G6_E_MOTION_CANNOT_ASSESS", op


# --- G5: nothing reaches outside, so nothing is sensitive -------------------------------------------------------------

def test_g5_no_op_opens_a_file_a_socket_or_a_program() -> None:
    assert package.__g6_sensitive_ops__ == ()
    roots = [Path(package.__file__).parent, Path(package.__file__).parent.parent / "_motion"]
    called = set()
    for path in (p for root in roots for p in root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                fn = node.func
                called.add(fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else "")
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
                assert not any(n.split(".")[0] in ("socket", "subprocess", "urllib", "http", "os") for n in names), \
                    path.name
    assert not called & {"open", "write_text", "write_bytes", "mkdir", "unlink", "rename", "Popen"}


def test_the_vetting_declaration_is_a_literal_naming_files_that_exist_and_no_signed_review() -> None:
    tree = ast.parse(Path(package.__file__).read_text(encoding="utf-8"))
    nodes = [n.value for n in tree.body if isinstance(n, ast.Assign)
             and [getattr(t, "id", "") for t in n.targets] == ["__g6_vetting__"]]
    assert len(nodes) == 1 and isinstance(nodes[0], ast.Dict)
    declared = ast.literal_eval(nodes[0])
    assert declared == package.__g6_vetting__
    assert set(declared) == {"kinds", "register", "independent_pass", "signed_review"}
    assert declared["kinds"] == ("physical_safety",) and declared["signed_review"] is None
    for key in ("register", "independent_pass"):
        assert (SRC / declared[key]).is_file(), (key, declared[key])


def test_the_mcp_result_and_the_skill_say_plainly_what_is_not_checked() -> None:
    reply = _invoke("check_plan", plan={"waypoints": [{"q": [2, 2], "t": 0}, {"q": [8, 2], "t": 10}]},
                    world={**WORLD, "obstacles": []}, robot=POINT, goal={"q": [8, 2], "tolerance": 0.01})
    assert reply["ok"] and reply["result"]["completion_state"] == "qualified-draft"
    said = " ".join(reply["result"]["assumptions"]).lower()
    blind = describe_reply(NAME)["skill"].split("## Blind spots", 1)[1].split("\n## ", 1)[0].lower()
    for unchecked in ("acceleration", "effort", "dynamics", "contact", "sensing", "localisation", "tracking error",
                      "waypoint 0"):
        assert unchecked in said and unchecked in blind, unchecked
    assert "no obstacle was given" in said and "obstacles: []" in blind


@pytest.mark.parametrize("planner", ["plan_rrt", "plan_astar"])
def test_a_planner_beyond_the_stated_range_cannot_assess_and_never_raises(planner) -> None:
    huge = World(lower=[-1e200, -1e200], upper=[1e200, 1e200], obstacles=[Obstacle(centre=[0.0, 0.0], radius=1.0)])
    body = PointBody(kind="point", radius=0.0, max_speed=2.0, clearance=0.0)
    args = (huge, body, [-1e150, 0.0], Goal(q=[1e150, 0.0], tolerance=0.0))
    r = (plan_rrt(*args, step=1e150, seed=1, speed=1.0) if planner == "plan_rrt"
         else plan_astar(*args, cell_size=1e199, speed=1.0))
    assert (r.completion_state, r.code) == ("blocked-escalated", "G6_E_MOTION_CANNOT_ASSESS")


def test_no_safety_standard_is_claimed_in_the_package_or_its_skill() -> None:
    root = Path(package.__file__).parent
    for path in [*root.rglob("*.py"), root / "skill" / "SKILL.md"]:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"ISO[ /]?(TS )?(10218|15066|13482)|IEC 61508|\bSIL[ _-]?\d", text), path.name
        assert not re.search(r"\b(certified|compliant|cleared) (for|to|as)\b", text), path.name


# --- ADAPT_PHYSICAL_AI-01: the RRT checks every edge and ends at the goal ---------------------------------------------

def test_p1_01_every_rrt_path_is_clear_along_its_edges_and_ends_at_the_goal() -> None:
    # The archive's own geometry: obstacles of radius 0.5 and a step of 1.0, which its node-only check jumped through;
    # three columns of them stand across the straight line from start to goal.
    w = World(lower=[0, 0], upper=[10, 10], obstacles=[Obstacle(centre=[x, y], radius=0.5)
                                                      for x in (3.0, 5.0, 7.0) for y in (1.0, 3.0, 5.0, 7.0, 9.0)])
    body = PointBody(kind="point", radius=0.0, max_speed=10.0, clearance=0.0)
    target = [9.5, 5.0]
    planned = 0
    for seed in range(12):
        r = plan_rrt(w, body, [0.5, 5.0], Goal(q=target, tolerance=0.0), step=1.0, seed=seed, speed=1.0)
        if r.completion_state == "blocked-escalated" and r.code == "G6_E_MOTION_NO_PATH":
            continue
        planned += 1
        points = [wp["q"] for wp in r.plan["waypoints"]]
        assert points[-1] == target, seed
        assert _edges_clear(points, w, body), seed
        assert r.completion_state == "qualified-draft" and r.verdict == "no_violation_found", seed
    assert planned >= 8


def test_p1_01_an_rrt_is_repeated_exactly_from_its_seed() -> None:
    args = (World.model_validate(WORLD), PointBody.model_validate(POINT), [1.0, 1.0], Goal(q=[9.0, 9.0], tolerance=0))
    one, two = (plan_rrt(*args, step=0.7, seed=42, speed=1.0) for _ in range(2))
    assert one.plan == two.plan and one.planner["seed"] == 42
    assert plan_rrt(*args, step=0.7, seed=43, speed=1.0).plan != one.plan


# --- ADAPT_PHYSICAL_AI-02: A* blocks each obstacle's whole inflated footprint -----------------------------------------

def test_p1_02_astar_keeps_out_of_each_obstacles_whole_inflated_footprint() -> None:
    w = World(lower=[0, 0], upper=[10, 10], obstacles=[Obstacle(centre=[5.0, 5.0], radius=2.0)])
    body = PointBody(kind="point", radius=0.3, max_speed=1.0, clearance=0.1)
    r = plan_astar(w, body, [1.0, 5.0], Goal(q=[9.0, 5.0], tolerance=0.0), cell_size=0.5, speed=0.5)
    points = [wp["q"] for wp in r.plan["waypoints"]]
    assert points[0] == [1.0, 5.0] and points[-1] == [9.0, 5.0]
    assert _edges_clear(points, w, body)
    assert r.completion_state == "qualified-draft" and r.planner["planner"] == "a_star"


def test_astar_says_when_it_finds_no_path_and_never_that_none_exists() -> None:
    wall = World(lower=[0, 0], upper=[10, 10], obstacles=[Obstacle(centre=[5.0, y], radius=0.6) for y in range(11)])
    body = PointBody(kind="point", radius=0.2, max_speed=1.0, clearance=0.0)
    r = plan_astar(wall, body, [1.0, 5.0], Goal(q=[9.0, 5.0], tolerance=0.0), cell_size=0.5, speed=0.5)
    assert (r.completion_state, r.code) == ("blocked-escalated", "G6_E_MOTION_NO_PATH")


# --- ADAPT_PHYSICAL_AI-03: kinematics from the arm's own link lengths and limits --------------------------------------

def _arm(lengths, lower=-3.0, upper=3.0) -> PlanarArm:
    return PlanarArm(kind="planar_arm", base=[0.0, 0.0], link_lengths=list(lengths), link_radius=0.0, clearance=0.0,
                     joints=[Joint(name=f"j{i}", lower=lower, upper=upper, max_velocity=1.0)
                             for i in range(len(lengths))])


def test_p1_03_forward_kinematics_uses_the_arms_own_link_lengths() -> None:
    # The source's Example 3.3 configuration: three links, each offset by pi/4 from the one before.
    r = forward_kinematics(_arm((1.0, 1.0, 1.0)), [0.0, math.pi / 4, math.pi / 4])
    assert r.tip == [pytest.approx(1 + math.sqrt(0.5)), pytest.approx(math.sqrt(0.5) + 1)]
    r = forward_kinematics(_arm((0.5, 0.3)), [math.pi / 2, -math.pi / 2])
    assert r.tip == [pytest.approx(0.3), pytest.approx(0.5)] and r.completion_state == "qualified-draft"


def test_p1_03_a_configuration_outside_the_joint_limits_is_refused() -> None:
    r = forward_kinematics(_arm((0.5, 0.3), lower=-1.0, upper=1.0), [0.2, 1.4])
    assert (r.completion_state, r.code) == ("blocked-escalated", "G6_E_MOTION_JOINT_LIMIT")
    assert [o["joint"] for o in r.outside_limits] == ["j1"]


# --- ADAPT_PHYSICAL_AI-04: no device op, no self-declared label -------------------------------------------------------

def test_p1_04_there_is_no_device_op_and_no_op_can_say_verified() -> None:
    ops = get_registry().invoke(NAME, "ops").unwrap()["ops"]
    assert not [op for op in ops if op.startswith("device_") or op in ("sim_save", "sim_load", "set_position")]
    for op in ops:
        assert _invoke(op)["result"]["completion_state"] in ("qualified-draft", "blocked-escalated"), op


# --- the kept step, against the source's worked case ------------------------------------------------------------------

def test_integrate_step_matches_the_closed_form_worked_case() -> None:
    # From rest under a constant action of 1, the source gives t^2 / 2: at t = 2, a position of 2.
    r = integrate_step([0.0], [0.0], [1.0], 2.0)
    assert (r.position, r.velocity) == ([pytest.approx(2.0)], [pytest.approx(2.0)])
    assert r.euler == {"position": [0.0], "velocity": [2.0]} and r.euler_position_error == pytest.approx(2.0)
    assert r.completion_state == "qualified-draft" and r.warning_card["code"] == "G6_E_MOTION_ESTIMATE"


def test_every_op_round_trips_over_mcp() -> None:
    replies = {
        "check_plan": _invoke("check_plan", plan={"waypoints": [{"q": [2, 2], "t": 0}, {"q": [8, 2], "t": 10}]},
                              world=WORLD, robot=POINT, goal={"q": [8, 2], "tolerance": 0.01}),
        "plan_astar": _invoke("plan_astar", world=WORLD, robot=POINT, start=[1, 1], goal={"q": [9, 9], "tolerance": 0},
                              cell_size=0.5, speed=0.5),
        "plan_rrt": _invoke("plan_rrt", world=WORLD, robot=POINT, start=[1, 1], goal={"q": [9, 9], "tolerance": 0},
                            step=0.8, seed=7, speed=0.5),
        "forward_kinematics": _invoke("forward_kinematics", robot=_arm((0.5, 0.3)).model_dump(), q=[0.1, 0.2]),
        "integrate_step": _invoke("integrate_step", position=[0, 0, 10], velocity=[1, 0, 0],
                                  acceleration=[0, 0, -9.80665], dt=0.1),
        # removing block.py's "grasp_margin" entry from _RUN fails this
        "grasp_margin": _invoke("grasp_margin", robot={**POINT, "gripper": {"max_force": 40, "rated_as": "total"}},
                                grasp={"contacts": [{"normal_force": 10, "tangential_force": 2}],
                                       "friction_coefficient": 0.5, "crush_limit": 30}),
    }
    for op, reply in replies.items():
        assert reply["ok"] and reply["result"]["completion_state"] == "qualified-draft", (op, reply.get("result"))
        assert reply["result"]["warning_card"]["code"].startswith("G6_E_MOTION_"), op
