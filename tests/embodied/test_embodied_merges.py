# SPDX-License-Identifier: Apache-2.0
"""Two tracker rows merged into g6.embodied.adapt_physical_ai: what each row's port plan keeps, and where it now lives.

* ``embodiment``: the archive's SafetyEnvelope validators (joint position, joint velocity, base speed, workspace pose,
  gripper force) are the motion package's registered checks, run by ``check_plan`` against one typed body description
  in SI units; nothing it reports is ``verified``, it has no device op or robot bridge, and it claims no standard.
* ``tactile_fusion``: ``grasp_margin`` (friction margin and crush limit), as an op and as a check in every
  ``check_plan``.

Each row's planned failure mode is now declared by adapt_physical_ai and no longer planned for a package of its own."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import g6.embodied.adapt_physical_ai as package
from g6.core import discovery
from g6.core.failure_modes import FailureModeRegistry
from g6.embodied._motion import (
    Contact,
    Goal,
    Grasp,
    Gripper,
    Joint,
    Plan,
    PlanarArm,
    PointBody,
    Waypoint,
    World,
    check_plan,
    registered_checks,
)
from g6.mcp import describe_reply, invoke_reply

NAME = "adapt_physical_ai"
WORLD = World(lower=[0.0, 0.0], upper=[10.0, 10.0], obstacles=[])


def _arm(max_force: float = 20.0) -> PlanarArm:
    return PlanarArm(kind="planar_arm", base=[5.0, 5.0], link_lengths=[1.0, 0.5], link_radius=0.02, clearance=0.0,
                     joints=[Joint(name="shoulder", lower=-1.0, upper=1.0, max_velocity=1.0),
                             Joint(name="elbow", lower=-2.0, upper=2.0, max_velocity=1.5)],
                     gripper=Gripper(max_force=max_force, rated_as="total"))


# --- embodiment: the SafetyEnvelope validators, now registered checks ------------------------------------------------

@pytest.mark.parametrize(("validator", "check", "case", "code"), [
    # validate_joint_position -> joint_limits: the shoulder at 1.2 rad, beyond its upper limit of 1 rad
    ("validate_joint_position", "joint_limits",
     lambda: (Plan(waypoints=[Waypoint(q=[0.0, 0.0], t=0.0), Waypoint(q=[1.2, 0.0], t=2.0)]), _arm(),
              Goal(q=[1.2, 0.0], tolerance=1e-9)), "G6_E_MOTION_JOINT_LIMIT"),
    # validate_joint_velocity -> speed_limits: the elbow 2 rad in 1 s, above its 1.5 rad/s
    ("validate_joint_velocity", "speed_limits",
     lambda: (Plan(waypoints=[Waypoint(q=[0.0, 0.0], t=0.0), Waypoint(q=[0.0, 2.0], t=1.0)]), _arm(),
              Goal(q=[0.0, 2.0], tolerance=1e-9)), "G6_E_MOTION_VELOCITY_LIMIT"),
    # validate_twist (its linear speed) -> speed_limits for a point body: 3 m in 1 s, above its 2 m/s
    ("validate_twist", "speed_limits",
     lambda: (Plan(waypoints=[Waypoint(q=[1.0, 1.0], t=0.0), Waypoint(q=[4.0, 1.0], t=1.0)]),
              PointBody(kind="point", radius=0.1, max_speed=2.0, clearance=0.0), Goal(q=[4.0, 1.0], tolerance=1e-9)),
     "G6_E_MOTION_VELOCITY_LIMIT"),
    # validate_pose -> workspace: the base moves to x = 10.5, outside the box (and is caught on the way there)
    ("validate_pose", "workspace",
     lambda: (Plan(waypoints=[Waypoint(q=[9.0, 1.0], t=0.0), Waypoint(q=[10.5, 1.0], t=2.0)]),
              PointBody(kind="point", radius=0.1, max_speed=2.0, clearance=0.0), Goal(q=[10.5, 1.0], tolerance=1e-9)),
     "G6_E_MOTION_OUT_OF_WORKSPACE"),
    # validate_gripper_force -> grasp_margin: 25 N at a contact, above the gripper's own 20 N
    ("validate_gripper_force", "grasp_margin",
     lambda: (Plan(waypoints=[Waypoint(q=[0.0, 0.0], t=0.0, grasp=Grasp(
         contacts=[Contact(normal_force=25.0, tangential_force=1.0)], friction_coefficient=0.5, crush_limit=100.0))]),
              _arm(max_force=20.0), Goal(q=[0.0, 0.0], tolerance=1e-9)), "G6_E_MOTION_GRIPPER_FORCE_LIMIT"),
])
def test_embodiment_each_safety_envelope_validator_is_a_registered_check(validator, check, case, code) -> None:
    # The value kept from embodiment (EMBODIMENT-02's fix); removing the named check's register_check line fails this.
    assert check in [c.name for c in registered_checks()], validator
    plan, robot, goal = case()
    r = check_plan(plan, WORLD, robot, goal)
    assert (r.completion_state, r.first_violation["check"], r.first_violation["code"]) == (
        "blocked-escalated", check, code), validator


def test_embodiment_the_body_is_one_typed_description_in_si_units_with_its_gripper() -> None:
    # The port plan's BodySpec (extra='forbid', SI units): a gripper is a field of the body, not a second description.
    # Removing model.py's `gripper` field from PointBody fails this.
    robot = {"kind": "point", "radius": 0.1, "max_speed": 1.0, "clearance": 0.0,
             "gripper": {"max_force": 40.0, "rated_as": "total"}}
    assert PointBody.model_validate(robot).gripper == Gripper(max_force=40.0, rated_as="total")
    for claim in ({"sil_level": 2}, {"safety_envelope": {"rated": True}}):
        reply = invoke_reply(NAME, "check_plan", json.dumps({
            "plan": {"waypoints": [{"q": [1, 1], "t": 0}]}, "world": WORLD.model_dump(mode="json"),
            "robot": {**robot, **claim}, "goal": {"q": [1, 1], "tolerance": 0}}))
        assert not reply["ok"] and reply["error"].startswith("[G6_E_INPUT_VALIDATION]"), claim
    with pytest.raises(ValueError):
        Gripper.model_validate({"max_force": 40.0, "rated_by": "the vendor"})


def test_embodiment_p0_01_nothing_simulated_or_checked_is_verified() -> None:
    # EMBODIMENT-01 not ported: no simulated reading exists, and every op answers qualified-draft at best.
    # Removing block.py's "grasp_margin" from AdaptPhysicalAIInput.op fails this.
    ops = discovery.ops(NAME)
    assert "grasp_margin" in ops and not [op for op in ops if "sensor" in op or "actuator" in op]
    for op in ops:
        state = invoke_reply(NAME, op, "{}")["result"]["completion_state"]
        assert state in ("qualified-draft", "blocked-escalated"), op


def test_embodiment_p1_03_there_is_no_robot_bridge_and_nothing_publishes() -> None:
    # EMBODIMENT-03 not ported: no ROS 2, PyBullet or MuJoCo import, and no op commands a device.
    # Adding an `import rclpy` (the archive bridge's) to any _motion or adapt_physical_ai module fails this.
    roots = [Path(package.__file__).parent, Path(package.__file__).parent.parent / "_motion"]
    for path in (p for root in roots for p in root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
                assert not any(n.split(".")[0] in ("rclpy", "rospy", "pybullet", "mujoco") for n in names), path.name
    assert not [op for op in discovery.ops(NAME) if op.startswith(("device_", "set_", "publish"))]


def test_embodiment_p1_04_the_skill_says_software_check_and_no_standard_is_claimed() -> None:
    # EMBODIMENT-04 not ported: the skill says it is not a safety function; no SIL or robot standard is named.
    # Removing SKILL.md's "As a safety function or a certificate" bullet fails this.
    skill = describe_reply(NAME)["skill"]
    assert "As a safety function" in skill.split("## When not to use it", 1)[1].split("\n## ", 1)[0]
    assert "SIL" not in skill and "ISO" not in skill and "IEC" not in skill


# --- tactile_fusion: grasp_margin and the crush limit ----------------------------------------------------------------

def test_tactile_fusion_grasp_margin_is_an_op_and_a_check_in_every_plan() -> None:
    # The value kept from tactile_fusion (TACTILE_FUSION-01's fix); removing block.py's "grasp_margin" op fails this.
    assert "grasp_margin" in discovery.ops(NAME) and "grasp_margin" in [c.name for c in registered_checks()]
    robot = PointBody(kind="point", radius=0.1, max_speed=1.0, clearance=0.0,
                      gripper=Gripper(max_force=50.0, rated_as="total"))
    crushed = Grasp(contacts=[Contact(normal_force=30.0, tangential_force=1.0)], friction_coefficient=0.6,
                    crush_limit=20.0)
    reply = invoke_reply(NAME, "grasp_margin", json.dumps({"robot": robot.model_dump(mode="json"),
                                                           "grasp": crushed.model_dump(mode="json")}))
    assert reply["result"]["code"] == "G6_E_MOTION_GRASP_CRUSH"


# --- the planned failure modes moved into adapt_physical_ai's declarations -------------------------------------------

def test_each_merged_rows_failure_mode_is_declared_by_adapt_physical_ai_and_no_longer_planned() -> None:
    # Removing "grasp_failure" from adapt_physical_ai's __g6_mitigates__ fails this.
    planned = FailureModeRegistry.planned()
    assert "g6.embodied.embodiment" not in planned and "g6.embodied.tactile_fusion" not in planned
    assert {"infeasible_motion_plan", "grasp_failure"} <= set(package.__g6_mitigates__)
    title = FailureModeRegistry.title("grasp_failure")
    assert NAME in [r["component"] for r in discovery.find(title, limit=5)]
