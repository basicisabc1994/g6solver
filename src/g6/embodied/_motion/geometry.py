# SPDX-License-Identifier: Apache-2.0
"""Vector maths for the motion checks, in plain Python on lists of floats (2-D or 3-D).

Kept from the archive: the sphere-overlap arithmetic of its ``check_collision`` (distance between centres against the
sum of radii, and the overlap) and the component-wise update of its Euler step. Added: the same overlap along
a whole segment, found in closed form; a planar chain's joint positions from its own link lengths; the per-joint
displacement constants an arm's segment check steps by; and the exact motion under constant acceleration beside
Euler's step, so the step's error is a number.

:data:`TOLERANCE` is G6's own numerical tolerance (1e-9 m): a gap within it of a limit counts as reaching the limit,
so it can only make a check stricter. A term that overflows (a gap, a discriminant, a root that is not a finite
number) raises :class:`ArithmeticError` through :func:`finite`, so no check reads an overflow as "clear".
"""
from __future__ import annotations

import math
from collections.abc import Sequence

Vector = Sequence[float]

#: m. A gap within this of a limit counts as reaching it (rule ``tolerance``); it only makes a check stricter.
TOLERANCE = 1e-9


def finite(value: float, what: str) -> float:
    """``value`` when it is a finite number; otherwise :class:`ArithmeticError`, which a check answers "cannot
    assess" with, never "clear"."""
    if not math.isfinite(value):
        raise ArithmeticError(f"{what} is {value}, not a finite number: the numbers are too large to check")
    return value


def sub(a: Vector, b: Vector) -> list[float]:
    return [x - y for x, y in zip(a, b)]


def dot(a: Vector, b: Vector) -> float:
    return sum(x * y for x, y in zip(a, b))


def norm(a: Vector) -> float:
    return math.sqrt(dot(a, a))


def distance(a: Vector, b: Vector) -> float:
    return norm(sub(a, b))


def lerp(a: Vector, b: Vector, s: float) -> list[float]:
    """The point a fraction ``s`` of the way from ``a`` to ``b``."""
    return [x + s * (y - x) for x, y in zip(a, b)]


def closest_on_segment(p: Vector, a: Vector, b: Vector) -> float:
    """The fraction ``s`` in [0, 1] at which the segment from ``a`` to ``b`` comes closest to ``p``."""
    u = sub(b, a)
    length2 = finite(dot(u, u), "a segment's squared length")
    if length2 == 0.0:
        return 0.0
    return min(1.0, max(0.0, finite(dot(sub(p, a), u) / length2, "the fraction along the segment nearest a point")))


def segment_distance(p: Vector, a: Vector, b: Vector) -> float:
    """The distance from ``p`` to the nearest point of the segment from ``a`` to ``b`` (rule ``distance``)."""
    return distance(p, lerp(a, b, closest_on_segment(p, a, b)))


def overlap(position: Vector, radius: float, centre: Vector, obstacle_radius: float,
            clearance: float = 0.0) -> tuple[float, float]:
    """The archive's sphere-sphere arithmetic: ``(distance between centres, overlap)``, where the overlap is how far
    the two spheres, with ``clearance`` added, reach into each other (above zero means they do)."""
    gap = distance(position, centre)
    return gap, radius + obstacle_radius + clearance - gap


def segment_entry(a: Vector, b: Vector, centre: Vector, reach: float) -> float | None:
    """The first fraction ``s`` in [0, 1] at which the segment from ``a`` to ``b`` comes within ``reach`` (plus
    :data:`TOLERANCE`) of ``centre``, or ``None`` when no point of it does. Solved in closed form, so a segment that
    starts and ends clear but passes through an obstacle is caught."""
    limit = reach + TOLERANCE
    d = sub(a, centre)
    u = sub(b, a)
    c = finite(dot(d, d) - limit * limit, "the gap term at the segment's start")
    if c < 0.0:
        return 0.0
    length2 = finite(dot(u, u), "the segment's squared length")
    if length2 == 0.0:
        return None
    half_b = dot(d, u)
    # From the centre's perpendicular offset to the segment's line: the equal form half_b^2 - length2 * c cancels
    # catastrophically near tangency on a long segment and can miss a real penetration.
    along = half_b / length2
    perpendicular = [x - along * y for x, y in zip(d, u)]
    disc = finite(length2 * (limit * limit - dot(perpendicular, perpendicular)),
                  "the discriminant of the segment's entry")
    if disc <= 0.0:
        return None
    s = finite((-half_b - math.sqrt(disc)) / length2, "the root of the segment's entry")
    return s if 0.0 <= s <= 1.0 else None


def chain_points(base: Vector, lengths: Sequence[float], q: Sequence[float]) -> list[list[float]]:
    """A planar chain's joint positions, base first and tip last (rule ``chain``): joint ``i`` turns link ``i`` by
    ``q[i]`` relative to the link before it, and link ``i`` is ``lengths[i]`` long."""
    x, y = float(base[0]), float(base[1])
    heading = 0.0
    points = [[x, y]]
    for length, angle in zip(lengths, q):
        heading += angle
        x += length * math.cos(heading)
        y += length * math.sin(heading)
        points.append([x, y])
    return points


def reach_constants(lengths: Sequence[float]) -> list[float]:
    """For each joint, the length of the chain outward of it: no point of the arm is farther from joint ``i``, so a
    change of ``dq`` in joint ``i`` moves any point at most ``reach[i] * |dq|`` (the constants ``c_i`` of rule
    ``swept_arm``)."""
    out, total = [], 0.0
    for length in reversed(lengths):
        total += length
        out.append(total)
    return out[::-1]


def wall_gap(point: Vector, lower: Vector, upper: Vector) -> tuple[float, int, str]:
    """How far ``point`` is inside the box, and the axis and side it is nearest: ``(gap, axis, "lower"|"upper")``;
    a negative gap is outside."""
    best = (math.inf, 0, "lower")
    for axis, (p, lo, hi) in enumerate(zip(point, lower, upper)):
        for gap, side in ((p - lo, "lower"), (hi - p, "upper")):
            if gap < best[0]:
                best = (gap, axis, side)
    return best


def euler_step(position: Vector, velocity: Vector, acceleration: Vector,
               dt: float) -> tuple[list[float], list[float]]:
    """One Euler step (rule ``euler``): position by ``dt`` times the velocity at the start, velocity by ``dt`` times
    the acceleration."""
    return ([p + dt * v for p, v in zip(position, velocity)],
            [v + dt * a for v, a in zip(velocity, acceleration)])


def constant_acceleration(position: Vector, velocity: Vector, acceleration: Vector,
                          dt: float) -> tuple[list[float], list[float]]:
    """The exact motion over ``dt`` under constant acceleration (rule ``closed_form``)."""
    return ([p + v * dt + 0.5 * a * dt * dt for p, v, a in zip(position, velocity, acceleration)],
            [v + a * dt for v, a in zip(velocity, acceleration)])


__all__ = ["TOLERANCE", "Vector", "chain_points", "closest_on_segment", "constant_acceleration", "distance", "dot",
           "euler_step", "finite", "lerp", "norm", "overlap", "reach_constants", "segment_distance", "segment_entry",
           "sub", "wall_gap"]
