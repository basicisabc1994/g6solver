# SPDX-License-Identifier: Apache-2.0
"""Independent projection, Decimal intersections, matrix products and graph search, in plain Python."""
from decimal import Decimal, localcontext
import math
import random


def vector(values):
    return [float(v) for v in values]


def product(a, b):
    """The 3 x 3 matrix product a b."""
    return [[math.fsum(a[i][k]*b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def segment_distance(p, a, b):
    d = [y-x for x, y in zip(a, b)]
    den = math.fsum(t*t for t in d)
    t = min(1, max(0, math.fsum((c-x)*v for c, x, v in zip(p, a, d))/den)) if den else 0
    return math.dist(p, [x+t*v for x, v in zip(a, d)])


def safe(a, b, world):
    if any(not float(lo)+1e-9 < p < float(hi)-1e-9
           for point in (a, b) for p, lo, hi in zip(point, world["lower"], world["upper"])):
        return False
    return all(segment_distance(vector(o["centre"]), a, b) > float(o["radius"])+1e-9
               for o in world["obstacles"])


def arm_gap(x, q):
    """The arm's smallest gap to the obstacle at q: links placed by accumulated headings, each a segment."""
    px, py, heading = float(x["base"][0]), float(x["base"][1]), 0.0
    points = [[px, py]]
    for length, angle in zip(vector(x["lengths"]), q):
        heading += angle
        px, py = px+length*math.cos(heading), py+length*math.sin(heading)
        points.append([px, py])
    centre = vector(x["centre"])
    return min(segment_distance(centre, a, b) for a, b in zip(points, points[1:]))-float(x["radius"])


def swept(x):
    """Dense samples with a margin: between two samples no point of the arm moves more than half a spacing times the
    sum over joints of the outward chain length times the joint's change (5.30, 5.34). 1 clear, 0 a collision."""
    qa, qb, lengths = vector(x["qa"]), vector(x["qb"]), vector(x["lengths"])
    moved = math.fsum(math.fsum(lengths[i:])*abs(b-a) for i, (a, b) in enumerate(zip(qa, qb)))
    samples = 2000
    lowest = min(arm_gap(x, [a+(b-a)*k/samples for a, b in zip(qa, qb)]) for k in range(samples+1))
    if lowest-moved/(2*samples) > 1e-6:
        return 1
    if lowest < -1e-6:
        return 0
    raise ValueError("undecided: the samples neither keep clear by the margin nor collide")


def planned(found, length=0):
    return {"found": int(found), "length": length, "violations": 0}


def grid(x):
    world, step = x["world"], float(x["cell"])
    lo, hi = vector(world["lower"]), vector(world["upper"])
    shape = [math.ceil((high-low)/step) for low, high in zip(lo, hi)]
    centres, allowed = {}, set()
    for i in range(shape[0]):
        for j in range(shape[1]):
            lower = [lo[0]+i*step, lo[1]+j*step]
            upper = [min(lower[k]+step, hi[k]) for k in range(2)]
            centres[(i, j)] = [(a+b)/2 for a, b in zip(lower, upper)]
            if any(a < low+1e-9 or b > high-1e-9 for a, b, low, high in zip(lower, upper, lo, hi)):
                continue
            blocked = False
            for obstacle in world["obstacles"]:
                p = vector(obstacle["centre"])
                nearest = [min(max(c, a), b) for c, a, b in zip(p, lower, upper)]
                blocked |= math.dist(p, nearest) < float(obstacle["radius"])+1e-9
            if not blocked:
                allowed.add((i, j))
    start, goal = vector(x["start"]), vector(x["goal"])
    first = tuple(math.floor((p-low)/step) for p, low in zip(start, lo))
    last = tuple(math.floor((p-low)/step) for p, low in zip(goal, lo))
    if first not in allowed or last not in allowed:
        return planned(False)
    # Exhaustive Dijkstra, no heuristic and no candidate queue implementation.
    distance, settled = {first: 0.0}, set()
    while set(distance)-settled:
        current = min(set(distance)-settled, key=lambda c: (distance[c], c))
        if current == last:
            return planned(True, distance[current]+math.dist(start, centres[first])+math.dist(goal, centres[last]))
        settled.add(current)
        for other in allowed-settled:
            di, dj = other[0]-current[0], other[1]-current[1]
            if max(abs(di), abs(dj)) != 1:
                continue
            if di and dj and ((current[0]+di, current[1]) not in allowed or
                              (current[0], current[1]+dj) not in allowed):
                continue
            proposal = distance[current]+math.dist(centres[current], centres[other])
            distance[other] = min(distance.get(other, math.inf), proposal)
    return planned(False)


def tree(x):
    world, origin, goal = x["world"], vector(x["start"]), vector(x["goal"])
    if not safe(origin, origin, world):
        return planned(False)
    if safe(origin, goal, world):
        return planned(True, math.dist(origin, goal))
    # Store complete paths rather than the candidate's parent-index tree.
    paths = [[origin]]
    rng = random.Random(x["seed"])
    for _ in range(x["iterations"]):
        sample = goal if rng.random() < float(x["bias"]) else [
            rng.uniform(float(a), float(b)) for a, b in zip(world["lower"], world["upper"])]
        path = min(paths, key=lambda p: math.dist(p[-1], sample))
        point, distance = path[-1], math.dist(path[-1], sample)
        if distance == 0:
            continue
        fraction = min(float(x["step"]), distance)/distance
        proposal = [a+(b-a)*fraction for a, b in zip(point, sample)]
        if not safe(point, proposal, world):
            continue
        extension = [*path, proposal]
        paths.append(extension)
        if math.dist(proposal, goal) <= float(x["step"]) and safe(proposal, goal, world):
            extension.append(goal)
            return planned(True, math.fsum(math.dist(a, b) for a, b in zip(extension, extension[1:])))
    return planned(False)


def evaluate(rule, inputs):
    x = inputs
    if rule == "friction":
        # OpenStax 6.2: the maximum static friction is the coefficient times the normal force (a printed mass weighs
        # 9.80 N a kilogram, section 5.4); the margin is that less the load along the surface.
        normal = Decimal(x["mass_kg"])*Decimal("9.80") if "mass_kg" in x else Decimal(x["normal"])
        holds = Decimal(x["friction_coefficient"])*normal
        return holds if x.get("view") == "holds" else {"holds": holds, "margin": holds-Decimal(x["tangential"])}
    if rule == "distance":
        return segment_distance(vector(x["p"]), vector(x["a"]), vector(x["b"]))
    if rule == "swept_point":
        with localcontext() as context:
            context.prec = 60
            a, b, centre = [[Decimal(str(v)) for v in x[key]] for key in ("a", "b", "centre")]
            radius = Decimal(str(x["reach"])) + Decimal("1e-9")
            d, offset = [v-u for u, v in zip(a, b)], [u-v for u, v in zip(a, centre)]
            c = sum(v*v for v in offset)-radius*radius
            if c < 0:
                return Decimal(0)
            length = sum(v*v for v in d)
            if length == 0:
                return Decimal(-1)
            projection = sum(v*w for v, w in zip(d, offset))/length
            radial = radius*radius-sum(v*v for v in offset)+projection*projection*length
            if radial <= 0:
                return Decimal(-1)
            entry = -projection-(radial/length).sqrt()
            return entry if 0 <= entry <= 1 else Decimal(-1)
    if rule == "swept_arm":
        return swept(x)
    if rule == "chain":
        # The product of homogeneous transforms (3.51-3.53), one link at a time.
        base = vector(x["base"])
        transform = [[1.0, 0.0, base[0]], [0.0, 1.0, base[1]], [0.0, 0.0, 1.0]]
        previous = [transform[0][2], transform[1][2]]
        for length, turn in zip(x["lengths"], x["turns"]):
            previous = [transform[0][2], transform[1][2]]
            a, length = math.pi*float(turn), float(length)
            c, s = math.cos(a), math.sin(a)
            transform = product(transform, [[c, -s, length*c], [s, c, length*s], [0.0, 0.0, 1.0]])
        end = [transform[0][2], transform[1][2]]
        if x.get("view") == "direction":
            return [(e - p)/float(x["lengths"][-1]) for e, p in zip(end, previous)]
        return end
    if rule in ("euler", "closed_form"):
        dt = Decimal(str(x["dt"]))
        p, v, a = [[Decimal(str(z)) for z in x[k]] for k in ("position", "velocity", "acceleration")]
        position = [p0+v0*dt+(a0*dt**2/2 if rule == "closed_form" else 0) for p0, v0, a0 in zip(p, v, a)]
        return [position, [v0+a0*dt for v0, a0 in zip(v, a)]]
    if rule == "average_speed":
        dt = float(x["dt"])
        return {"speed": math.dist(vector(x["a"]), vector(x["b"]))/dt if dt > 0 else 0,
                "timing": int(dt <= 0)}
    if rule == "a_star":
        return grid(x)
    if rule == "rrt":
        return tree(x)
    raise ValueError(rule)
