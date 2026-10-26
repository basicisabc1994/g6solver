---
name: g6-adapt-physical-ai
description: Use when a robot motion plan or grasp, from a model, a planner or a person, is about to be carried out and you need to know whether it collides along its segments, leaves its workspace, breaks a joint or speed limit, misses its goal, or lets an object slip or crush, against the robot's own specification and a stated world.
---

# G6 motion plan checker (adapt_physical_ai)

`adapt_physical_ai` checks a motion plan, and any grasp it carries, against its world and its robot's own limits
before anything moves: each segment whole, never only its waypoints, with the first violation as evidence. It is the
door to the embodied family's `g6.embodied._motion`, it never moves hardware, and its best state is `qualified-draft`.
No qualified person has reviewed this component; its rules are checked against their sources by G6's certificate and an
independent reader.

## When to use it

- A plan for a mobile base, a drone, a tool tip or a planar arm is about to be sent to a controller.
- A grasp is about to be made or carried: will a contact slip, or press harder than the object or gripper allows?
- You need a reference path (A* or a seeded RRT) for a point body to compare with, or to start from.

## When not to use it

- As a safety function: a plan with no violation is "no violation found against the world and limits
  given", nothing more. A qualified person signs off a real robot's motion.
- To move anything, or for live grip control: it has no device op. Send any command through `g6.action_gating`.
- Arms in 3-D, or bodies that are not one sphere or a planar chain of capsules: model them first.

## Ops

- `check_plan`: `plan`, `world`, `robot` and `goal` together: each segment whole against the obstacles (inflated by
  the robot's radius and clearance) and the workspace box; each waypoint against the joint limits and any grasp it
  carries; each segment's average speed against the speed limits; the end against the goal.
- `plan_astar`: a reference A* path for a point body in 2-D (`start`, `goal`, `cell_size`, `speed`), blocking every
  cell any part of which is within reach of an obstacle or the workspace edge; then checked.
- `plan_rrt`: a reference RRT path for a point body in 2-D or 3-D (`start`, `goal`, `step`, `seed`, `speed`, optional
  `max_iterations` and `goal_bias`), each edge checked whole, ending at the goal and repeatable by seed; then checked.
- `forward_kinematics`: a planar arm's joint positions and tip at `q`, from its own base and link lengths; refused
  when a joint is outside its limits.
- `integrate_step`: `position` and `velocity` advanced by `dt` under constant `acceleration` in closed form, with
  Euler's step and its error beside it. Gravity is part of the acceleration you give.
- `grasp_margin`: each contact of a `grasp`: its friction margin (the coefficient of static friction times its normal
  force, less the force along the surface it must hold) and its normal force against the object's crush limit and the
  `robot`'s gripper limit.

## Alone and composed

Alone over MCP: `g6_invoke("adapt_physical_ai", "check_plan", '{"plan": ..., "world": ..., "robot": ..., "goal":
...}')`. No op is sensitive: none reaches the network, writes a file or moves anything. From Python:
`from g6.embodied.adapt_physical_ai import check_plan, grasp_margin`.

The inputs, in SI units (m, rad, s, N); every value is yours, and there is no default limit:

- `robot`: `{"kind": "point", "radius", "max_speed", "clearance"}` or `{"kind": "planar_arm", "base", "link_lengths",
  "link_radius", "joints": [{"name", "lower", "upper", "max_velocity"}], "clearance"}`, either with an optional
  `"gripper": {"max_force", "rated_as"}`, `rated_as` being `"total"` (a limit on the sum of a grasp's normal forces)
  or `"per_contact"`; without it the gripper limit cannot be assessed.
- `world`: `{"lower", "upper", "obstacles": [{"name", "centre", "radius"}]}`. `obstacles: []` says there is none;
  leaving it out is refused.
- `plan`: `{"waypoints": [{"q", "t", "grasp"}]}`: `q` a point body's position or an arm's joint angles, `t` the time
  from the start (without it the speed check cannot assess), and `grasp` only where an object is held.
- `grasp`: `{"contacts": [{"name", "normal_force", "tangential_force"}], "friction_coefficient", "crush_limit"}`, with
  the coefficient of static friction measured for the pair. Each contact's `tangential_force` is its share of the
  object's weight and inertial load; G6 does not check how the load is split.
- `goal`: `{"q", "tolerance"}`, or for an arm `{"tip", "tolerance"}`.

Composed: put it between whatever proposes a plan (a model through `g6.core.llm`, a planner, a person) and the
controller; `blocked-escalated` means the plan does not go. A family member adds a check with
`g6.embodied._motion.register_check`, naming its rules' sources and codes.

## Failure modes it catches

- `infeasible_motion_plan` (a motion plan that collides, or asks a body for what it cannot do): a segment through an
  obstacle, or out of the workspace, between two clear waypoints is caught (in closed form for a point body, in steps
  no point of an arm can cross); so are a joint outside its limits, a segment faster than its limit, time that does
  not increase and a plan short of its goal.
- `grasp_failure` (an object dropped or crushed when grasped): a contact whose friction cannot hold its load slips,
  however even the grip, and a contact pressing at or above the object's crush limit or the gripper's limit is a
  violation.

Missing data (an untimed waypoint, a grasp without the gripper's limit or rating, an op without its world) answers
`G6_E_MOTION_CANNOT_ASSESS`, never a default, and a check that breaks cannot let a plan through.

## Blind spots

- `model_reality_gap` (a safety analysis of a model that differs from the real system): the check is only as good as
  the world, body, plan and forces you give it: a missing obstacle or a wrong limit or coefficient passes unseen.
- Obstacles are fixed spheres; an arm is planar and its links are not checked against each other.
- Between waypoints the robot moves in a straight line in its configuration; speeds are segment averages, so keep a
  margin below the limit.
- Not checked: acceleration and effort limits, a point body's turning rate, dynamics, contact beyond a grasp's given
  forces, sensing and localisation error, the controller's tracking error, and the move to waypoint 0.
- Friction is the source's approximate model, not accurate for lubricated surfaces or fast sliding. Each contact is
  checked alone, where a waypoint gives a grasp: twisting, a load changing between waypoints and slip under way are
  not checked.
- `obstacles: []` gives the collision check nothing to check; the result says so.
- These only tighten the answer: a gap within 1e-9 m, or a force within 1e-9 N, of a limit counts as reaching it;
  over two million distance evaluations for an arm, a coordinate over 1,000,000 m from zero, or a term that is not a
  finite number answers "cannot assess".
- A* is optimal only on its grid; the RRT drops an edge that would reach an obstacle instead of stopping it short.

## What it needs

Nothing outside G6: no extra, outside program, network or model.

## Examples

A result's `verdict` is `violation`, `cannot_assess` or `no_violation_found`; `first_violation` gives the check, code,
`at` (waypoint index plus the fraction along the next segment), time and detail. A violation is `blocked-escalated`
with its code: `G6_E_MOTION_COLLISION`, `G6_E_MOTION_OUT_OF_WORKSPACE`, `G6_E_MOTION_JOINT_LIMIT`,
`G6_E_MOTION_VELOCITY_LIMIT`, `G6_E_MOTION_TIMING`, `G6_E_MOTION_GOAL_MISSED`, `G6_E_MOTION_GRASP_SLIP`,
`G6_E_MOTION_GRASP_CRUSH` or `G6_E_MOTION_GRIPPER_FORCE_LIMIT`. A clean answer is `qualified-draft` with
`G6_E_MOTION_NO_VIOLATION_FOUND`, its evidence listing each check, assumption and sourced rule, and `source_findings`
any source past its review date.

A plan straight through a pillar: both waypoints are clear, the segment is not (`G6_E_MOTION_COLLISION`).

```g6-example op=check_plan
{"plan": {"waypoints": [{"q": [2, 5], "t": 0}, {"q": [8, 5], "t": 10}]}, "world": {"lower": [0, 0], "upper": [10, 10], "obstacles": [{"name": "pillar", "centre": [5, 5], "radius": 1}]}, "robot": {"kind": "point", "radius": 0.2, "max_speed": 1, "clearance": 0.1}, "goal": {"q": [8, 5], "tolerance": 0.01}}
```

Reference paths around the same pillar, each then checked.

```g6-example op=plan_astar
{"world": {"lower": [0, 0], "upper": [10, 10], "obstacles": [{"name": "pillar", "centre": [5, 5], "radius": 1}]}, "robot": {"kind": "point", "radius": 0.2, "max_speed": 1, "clearance": 0.1}, "start": [1, 1], "goal": {"q": [9, 9], "tolerance": 0}, "cell_size": 0.5, "speed": 0.5}
```

```g6-example op=plan_rrt
{"world": {"lower": [0, 0], "upper": [10, 10], "obstacles": [{"name": "pillar", "centre": [5, 5], "radius": 1}]}, "robot": {"kind": "point", "radius": 0.2, "max_speed": 1, "clearance": 0.1}, "start": [1, 1], "goal": {"q": [9, 9], "tolerance": 0}, "step": 0.8, "seed": 7, "speed": 0.5}
```

A two-link arm's joint positions and tip.

```g6-example op=forward_kinematics
{"robot": {"kind": "planar_arm", "base": [0, 0], "link_lengths": [0.5, 0.3], "link_radius": 0.02, "clearance": 0.01, "joints": [{"name": "shoulder", "lower": -2, "upper": 2, "max_velocity": 1.5}, {"name": "elbow", "lower": -2.5, "upper": 2.5, "max_velocity": 2}]}, "q": [0.3, 0.6]}
```

A tenth of a second of free fall from 10 m, with Euler's step beside the closed form.

```g6-example op=integrate_step
{"position": [0, 0, 10], "velocity": [1, 0, 0], "acceleration": [0, 0, -9.80665], "dt": 0.1}
```

An even grip too light for its load: each finger presses 5 N with a coefficient of 0.4, so friction holds 2 N where
3 N is needed (`G6_E_MOTION_GRASP_SLIP`).

```g6-example op=grasp_margin
{"robot": {"kind": "point", "radius": 0.05, "max_speed": 0.5, "clearance": 0.01, "gripper": {"max_force": 40, "rated_as": "total"}}, "grasp": {"contacts": [{"name": "left", "normal_force": 5, "tangential_force": 3}, {"name": "right", "normal_force": 5, "tangential_force": 3}], "friction_coefficient": 0.4, "crush_limit": 25}}
```

From Python: an arm's sweep through an obstacle between clear waypoints, then a plan without times.

```python
from g6.embodied.adapt_physical_ai import Goal, Joint, Obstacle, Plan, PlanarArm, Waypoint, World, check_plan

arm = PlanarArm(kind="planar_arm", base=[0, 0], link_lengths=[1.0], link_radius=0.0, clearance=0.0,
                joints=[Joint(name="shoulder", lower=-3, upper=3, max_velocity=2)])
world = World(lower=[-2, -2], upper=[2, 2], obstacles=[Obstacle(name="post", centre=[1.0, 0.0], radius=0.1)])
sweep = Plan(waypoints=[Waypoint(q=[-0.5], t=0.0), Waypoint(q=[0.5], t=1.0)])
r = check_plan(sweep, world, arm, Goal(q=[0.5], tolerance=0.0))
assert r.completion_state == "blocked-escalated" and r.first_violation["code"] == "G6_E_MOTION_COLLISION"
assert r.first_violation["detail"]["obstacle"] == "post" and 0.39 < r.first_violation["at"] < 0.41

untimed = Plan(waypoints=[Waypoint(q=[0.5]), Waypoint(q=[1.0])])
r = check_plan(untimed, world, arm, Goal(q=[1.0], tolerance=0.0))
assert r.verdict == "cannot_assess" and r.code == "G6_E_MOTION_CANNOT_ASSESS"
```

## The four open problems

- **Learning signals**: every rejected plan or grasp says why, by check and code, where and by how much. Not yet:
  rejections, drops and crushes are not written to a ledger, and nothing learns from them.
- **Evaluators the model cannot influence**: the checks are geometry and inequalities over your numbers; no model is
  called, and a plan's own claims (an approval, a safety envelope) are refused as unknown fields.
- **Transfer by failure mode**: violations are typed by failure mode, so a check applies to any plan's source and any
  robot the body description fits. Not yet: no record of which sources fail how.
- **Enough held-out cases**: the tests check the sweep against a dense sample, and the friction margin against exact
  arithmetic, on random cases they were not written around. Not yet: no held-out planning scenes or grasp logs.
