# SPDX-License-Identifier: Apache-2.0
"""Convert protocol inputs and measure the actual implementation's numeric output; for a guard, whether it fires."""
import math

from g6.embodied._motion import geometry as geo
from g6.embodied._motion.checks import WORK_BOUND, Case, _Work
from g6.embodied._motion.envelope import GRASP_CRUSH, GRASP_SLIP, GRIPPER_FORCE
from g6.embodied._motion.model import (Contact, Goal, Grasp, Gripper, Joint, Obstacle, Plan, PlanarArm, PointBody,
                                       Waypoint, World)

#: The weight of a 1.00-kg object on Earth, 9.80 N (OpenStax University Physics, section 5.4): a printed friction
#: case's normal force is its printed mass times it, the product the held copy does not decode.
WEIGHT_OF_ONE_KG = 9.80


def vector(values):
    return [float(v) for v in values]


def grasp_of(x):
    """One contact: its normal force (or a printed mass's weight), the load along its surface, and the limits."""
    normal = float(x["mass_kg"]) * WEIGHT_OF_ONE_KG if "mass_kg" in x else float(x.get("normal", 0))
    return Grasp(contacts=[Contact(normal_force=normal, tangential_force=float(x.get("tangential", 0)))],
                 friction_coefficient=float(x.get("friction_coefficient", 0)),
                 crush_limit=float(x.get("crush", 1000000)))


def guard(function, rule, x):
    """A guard's observation: 1 when it fires, 0 when it holds (a removed guard returns None and holds)."""
    if rule == "tolerance":
        # A segment passing the obstacle's centre at the given offset, within or just beyond reach plus 1e-9 m.
        offset = float(x["offset"])
        return int(function([-1.0, offset], [1.0, offset], [0.0, 0.0], float(x["reach"])) is not None)
    if rule == "work_bound":
        lengths = vector(x["lengths"])
        body = PlanarArm(kind="planar_arm", base=[0, 0], link_lengths=lengths, link_radius=0, clearance=0,
                         joints=[Joint(name=f"j{i}", lower=-10, upper=10, max_velocity=1000000)
                                 for i in range(len(lengths))])
        world = World(lower=[-1000000]*2, upper=[1000000]*2,
                      obstacles=[Obstacle(centre=vector(x["centre"]), radius=float(x["radius"]))])
        out = function(vector(x["qa"]), vector(x["qb"]), body, world, "collision", _Work(x["bound"]))
        return 0 if out is None else int(out[0] == "cannot")
    if rule == "force_tolerance":
        # The grasp check's violations of one kind: a slip, a crushing force or a force at the gripper's limit.
        gripper = Gripper(max_force=float(x["limit"]), rated_as="per_contact") if "limit" in x else None
        out = function(grasp_of(x), gripper)
        code = {"slip": GRASP_SLIP, "crush": GRASP_CRUSH, "gripper": GRIPPER_FORCE}[x["check"]]
        return 0 if out is None else int(any(v.code == code for v in out[0]))
    if rule == "bounds":
        world = World(lower=[-10, -10], upper=[10, 10],
                      obstacles=[Obstacle(centre=[5, 5], radius=float(x.get("radius", 1)))])
        body = PointBody(kind="point", radius=0, clearance=0, max_speed=1)
        q = vector(x["q"])
        out = function(Plan(waypoints=[Waypoint(q=[0, 0], t=0), Waypoint(q=q, t=1)]), world, body,
                       Goal(q=q, tolerance=float(x.get("tolerance", 0))))
        return 0 if out is None else int(bool(out))
    return None


def evaluate(function, rule, inputs):
    x = inputs
    fired = guard(function, rule, x)
    if fired is not None:
        return fired
    if rule == "friction":
        row = function(grasp_of(x))[0]
        return row["holds_up_to"] if x.get("view") == "holds" else {"holds": row["holds_up_to"], "margin": row["margin"]}
    if rule == "distance":
        # The function is the closest point's fraction along the segment; segment_distance calls it by name, so the
        # whole distance it measures is observed.
        return geo.segment_distance(vector(x["p"]), vector(x["a"]), vector(x["b"]))
    if rule == "swept_point":
        result = function(vector(x["a"]), vector(x["b"]), vector(x["centre"]), float(x["reach"]))
        return -1 if result is None else result
    if rule == "swept_arm":
        # The arm's segment walked in steps no point can cross: 1 clear, 0 a collision, -1 cannot assess.
        lengths = vector(x["lengths"])
        body = PlanarArm(kind="planar_arm", base=vector(x["base"]), link_lengths=lengths, link_radius=0,
                         clearance=0, joints=[Joint(name=f"j{i}", lower=-10, upper=10, max_velocity=1000000)
                                              for i in range(len(lengths))])
        world = World(lower=[-1000000]*2, upper=[1000000]*2,
                      obstacles=[Obstacle(centre=vector(x["centre"]), radius=float(x["radius"]))])
        status, _, _ = function(vector(x["qa"]), vector(x["qb"]), body, world, "collision", _Work(WORK_BOUND))
        return {"clear": 1, "violation": 0, "cannot": -1}[status]
    if rule == "chain":
        points = function(vector(x["base"]), vector(x["lengths"]), [math.pi*float(t) for t in x["turns"]])
        if x.get("view") == "direction":
            return [(a-b)/float(x["lengths"][-1]) for a, b in zip(points[-1], points[-2])]
        return points[-1]
    if rule in ("euler", "closed_form"):
        return function(vector(x["position"]), vector(x["velocity"]), vector(x["acceleration"]), float(x["dt"]))
    if rule == "average_speed":
        a, b, dt = vector(x["a"]), vector(x["b"]), float(x["dt"])
        body = PointBody(kind="point", radius=0, clearance=0, max_speed=1e-12)
        world = World(lower=[-1000000]*len(a), upper=[1000000]*len(a), obstacles=[])
        plan = Plan(waypoints=[Waypoint(q=a, t=0), Waypoint(q=b, t=dt)])
        result = function(Case(plan, world, body, Goal(q=b, tolerance=0)))
        return {"speed": next((v.detail["speed"] for v in result.violations if "speed" in v.detail), 0),
                "timing": int(any("t_start" in v.detail for v in result.violations))}
    world = World.model_validate(x["world"])
    body = PointBody(kind="point", radius=0, clearance=0, max_speed=1000000)
    start, goal = vector(x["start"]), vector(x["goal"])
    args = {"world": world, "body": body, "start": start, "goal": Goal(q=goal, tolerance=0), "speed": 1}
    if rule == "a_star":
        args["cell_size"] = float(x["cell"])
    elif rule == "rrt":
        args.update(step=float(x["step"]), seed=x["seed"], max_iterations=x["iterations"],
                    goal_bias=float(x["bias"]))
    else:
        raise ValueError(rule)
    result = function(**args).model_dump(mode="json")
    if "plan" not in result:
        return {"found": 0, "length": 0, "violations": 0}
    points = [w["q"] for w in result["plan"]["waypoints"]]
    violations = int(math.dist(points[0], start) > 1e-7 or math.dist(points[-1], goal) > 1e-7)
    for point in points:
        violations += int(any(p <= lo or p >= hi for p, lo, hi in zip(point, world.lower, world.upper)))
    for a, b in zip(points, points[1:]):
        delta = [v-u for u, v in zip(a, b)]
        squared = sum(v*v for v in delta)
        for obstacle in world.obstacles:
            offset = [c-u for c, u in zip(obstacle.centre, a)]
            t = min(1, max(0, sum(v*w for v, w in zip(delta, offset))/squared)) if squared else 0
            nearest = [u+t*v for u, v in zip(a, delta)]
            violations += int(math.dist(nearest, obstacle.centre) <= obstacle.radius)
    return {"found": 1, "length": sum(math.dist(a, b) for a, b in zip(points, points[1:])),
            "violations": violations}
