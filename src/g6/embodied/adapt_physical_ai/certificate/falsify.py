# SPDX-License-Identifier: Apache-2.0
"""Seeded finite geometry and planning probes, with explicit support boundaries."""
import math
import random
from decimal import Decimal


def generate(rule, seed, count):
    rng = random.Random(seed)
    out = []
    for _ in range(count):
        if rule == "distance":
            dimension = rng.choice([2, 3])
            out.append({k: [rng.randrange(-1000000, 1000001) for _ in range(dimension)] for k in ("p", "a", "b")})
        elif rule == "swept_point" and rng.randrange(2):
            # Near tangency on a long segment, just inside a 1 m reach: cancellation once hid such a penetration.
            extent = rng.choice([1000, 10000, 100000, 1000000])
            height = rng.choice(["0.99999", "0.9999999", "0.99999999"])
            out.append({"a": [-extent, height], "b": [extent, height], "centre": [0, 0], "reach": 1})
        elif rule == "swept_point":
            extent = rng.choice([1, 10, 1000, 1000000])
            radius = str(extent/10)
            out.append({"a": [-extent, 0], "b": [extent, 0], "centre": [0, rng.choice([0, extent])], "reach": radius})
        elif rule == "swept_arm":
            # A straight arm turning its first joint by 2 * turn about the direction phi: it either sweeps through an
            # obstacle on that line, or keeps clear by the source's condition (no point moves as far as the gap).
            lengths = [rng.choice([1, 2, 5]) for _ in range(rng.randrange(1, 13))]
            total, phi, turn = sum(lengths), rng.choice([0, .5, 1.5, -2]), rng.choice([.05, .2, .5])
            if rng.randrange(2):
                distance, radius = .9*total, .05*total
            else:
                radius = rng.choice([.5, 2])
                distance = total+radius+2*total*2*turn+1
            out.append({"base": [0, 0], "lengths": lengths,
                        "qa": [str(phi-turn)]+[0]*(len(lengths)-1), "qb": [str(phi+turn)]+[0]*(len(lengths)-1),
                        "centre": [str(round(distance*math.cos(phi), 9)), str(round(distance*math.sin(phi), 9))],
                        "radius": str(radius)})
        elif rule == "chain":
            n = rng.randrange(1, 13)
            out.append({"base": [rng.randrange(-1000, 1001), rng.randrange(-1000, 1001)],
                        "lengths": [str(rng.choice([.001, 1, 1000000])) for _ in range(n)],
                        "turns": [str(rng.choice([-1, -.5, -.25, 0, .25, .5, 1])) for _ in range(n)]})
        elif rule in ("euler", "closed_form"):
            n = rng.choice([2, 3])
            out.append({**{key: [rng.randrange(-1000000, 1000001) for _ in range(n)]
                           for key in ("position", "velocity", "acceleration")}, "dt": rng.choice([1, 10, 100])})
        elif rule == "friction":
            # Coefficients across and past the source's usual 0 to 1.0, forces from zero to the checks' range.
            out.append({"friction_coefficient": str(rng.choice([0, .3, .7, 1, 2.5])),
                        "normal": str(rng.choice([0, 1, 196, 1000000])), "tangential": str(rng.choice([0, 1, 137, 500]))})
        elif rule == "average_speed":
            out.append({"a": [rng.randrange(-1000000, 1000001), 0],
                        "b": [rng.randrange(-1000000, 1000001), 0], "dt": rng.choice([1, 10, 1000])})
        else:
            world = {"lower": [0, 0], "upper": [8, 8],
                     "obstacles": [{"centre": [4, 4], "radius": "0.7"}] if rng.randrange(2) else []}
            if rule == "a_star":
                out.append({"world": world, "start": [str(rng.randrange(1, 7)+.5), "1.5"],
                            "goal": [str(rng.randrange(1, 7)+.5), "6.5"], "cell": 1})
            elif rule == "rrt":
                out.append({"world": world, "start": ["1.5", "4.5"], "goal": ["6.5", "4.5"],
                            "step": str(rng.choice([.5, 1, 2])), "seed": rng.randrange(2**32),
                            "iterations": 128, "bias": str(rng.choice([0, .1, .5]))})
            else:
                raise ValueError(rule)
    return out


def invariant(rule, inputs, value):
    if rule == "distance":
        return value >= 0
    if rule == "swept_point":
        return value == -1 or 0 <= value <= 1
    if rule == "swept_arm":
        # 5.30/5.34: no point moves farther than the sum of each joint's outward chain length times its change, and
        # no point of the arm is farther from the obstacle's centre than its distance from the base less the arm.
        lengths = [float(v) for v in inputs["lengths"]]
        moved = sum(sum(lengths[i:])*abs(float(b)-float(a)) for i, (a, b) in enumerate(zip(inputs["qa"], inputs["qb"])))
        gap = math.dist([float(v) for v in inputs["centre"]], [float(v) for v in inputs["base"]])-sum(lengths)
        return value == 1 if gap-float(inputs["radius"]) > moved else value in (0, 1)
    if rule == "chain":
        if inputs.get("view") == "direction":
            return abs(sum(float(v)**2 for v in value)-1) < 1e-6
        reach = sum(float(v) for v in inputs["lengths"])
        return math.dist([float(v) for v in value], [float(v) for v in inputs["base"]]) <= reach+1e-6
    if rule == "average_speed":
        return value["speed"] >= 0 and value["timing"] == int(float(inputs["dt"]) <= 0)
    if rule == "friction":
        # 6.2: the most friction holds is the coefficient times the normal force; the margin is that less the load.
        if inputs.get("view") == "holds":
            return value >= 0
        return value["holds"] >= 0 and abs(value["holds"] - value["margin"] - Decimal(inputs["tangential"])) <= Decimal("1e-6")
    if rule in ("a_star", "rrt"):
        return value["found"] in (0, 1) and value["length"] >= 0 and value["violations"] == 0
    return all(math.isfinite(v) for row in value for v in row)
