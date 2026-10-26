# SPDX-License-Identifier: Apache-2.0
"""The procedural techniques, in NumPy (the ``procedural`` extra). Each one is deterministic given its seed: it draws
only from the ``numpy.random.Generator`` (or a local ``random.Random``) it is handed, never from a global generator.

:func:`draw` returns a :class:`Drawing`: a grid of values, or strokes (polylines) for the techniques a pen draws
(L-systems, the Koch snowflake, random walks). A grid is either already in the range 0 to 1 (``ranged``: cellular
automata, reaction-diffusion, Voronoi, domain colouring, shape grammars, pattern networks) or in its own units, which
the renderer stretches to 0 to 1. Nothing here hides a failure: a technique that dies out or collapses returns the
flat grid it really produced, and the inspector refuses it.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from g6.media._types import INPUT_INVALID, MediaError
from g6.media.adapt_generative_art import expression

#: The most symbols an L-system may expand to.
MAX_SYMBOLS = 400_000


@dataclass
class Drawing:
    grid: np.ndarray | None = None
    strokes: list[np.ndarray] | None = None
    ranged: bool = False
    info: dict[str, Any] = field(default_factory=dict)


# -- L-systems --------------------------------------------------------------------------------------------------------

def expand(axiom: str, rules: dict[str, str], iterations: int, limit: int = MAX_SYMBOLS) -> str:
    """``axiom`` rewritten ``iterations`` times by ``rules``, refused once it passes ``limit`` symbols."""
    current = axiom
    for _ in range(iterations):
        current = "".join(rules.get(ch, ch) for ch in current)
        if len(current) > limit:
            raise MediaError(INPUT_INVALID, f"the L-system grows past {limit} symbols; use fewer iterations")
    return current


def turtle(commands: str, angle_deg: float) -> list[np.ndarray]:
    """The polylines a turtle draws for ``commands``: F and G draw a unit step, f moves, + turns left, - turns right,
    [ and ] save and restore position and heading."""
    turn = math.radians(angle_deg)
    x = y = 0.0
    heading = math.pi / 2
    strokes: list[list[tuple[float, float]]] = []
    current = [(x, y)]
    stack: list[tuple[float, float, float]] = []
    for ch in commands:
        if ch in "FG":
            x, y = x + math.cos(heading), y + math.sin(heading)
            current.append((x, y))
        elif ch == "f":
            x, y = x + math.cos(heading), y + math.sin(heading)
            strokes.append(current)
            current = [(x, y)]
        elif ch == "+":
            heading += turn
        elif ch == "-":
            heading -= turn
        elif ch == "[":
            stack.append((x, y, heading))
        elif ch == "]" and stack:
            strokes.append(current)
            x, y, heading = stack.pop()
            current = [(x, y)]
    strokes.append(current)
    return [np.asarray(s, dtype=float) for s in strokes if len(s) > 1]


def _draw_lsystem(p: Any, width: int, height: int, rng: np.random.Generator, seed: int) -> Drawing:
    commands = expand(p.axiom, p.rules, p.iterations)
    lines = turtle(commands, p.angle)
    return Drawing(strokes=lines, info={"symbols": len(commands), "strokes": len(lines), "line_width": p.line_width})


# -- cellular automata ------------------------------------------------------------------------------------------------

def wolfram(rule: int, width: int, rows: int, start: np.ndarray | None) -> np.ndarray:
    """An elementary cellular automaton: ``rows`` steps of ``width`` wrapped cells from ``start`` (one live cell in the
    middle when ``None``). Bit ``k`` of ``rule`` is the next state of a cell whose (left, self, right) read ``k``."""
    table = np.array([(rule >> k) & 1 for k in range(8)], dtype=bool)
    row = np.zeros(width, dtype=bool) if start is None else start.astype(bool).copy()
    if start is None:
        row[width // 2] = True
    out = np.zeros((rows, width), dtype=bool)
    out[0] = row
    for r in range(1, rows):
        index = (np.roll(row, 1).astype(np.int8) << 2) | (row.astype(np.int8) << 1) | np.roll(row, -1).astype(np.int8)
        row = table[index]
        out[r] = row
    return out


def life_step(cells: np.ndarray) -> np.ndarray:
    """One generation of Conway's Game of Life on a wrapped grid."""
    n = sum(np.roll(np.roll(cells, dy, 0), dx, 1).astype(np.int8)
            for dy in (-1, 0, 1) for dx in (-1, 0, 1) if (dy, dx) != (0, 0))
    return (n == 3) | (cells & (n == 2))


def _draw_cellular_automaton(p: Any, width: int, height: int, rng: np.random.Generator, seed: int) -> Drawing:
    if p.kind == "wolfram":
        start = rng.random(width) < 0.5 if p.start == "random" else None
        cells = wolfram(p.rule, width, height, start)
        return Drawing(grid=cells.astype(float), ranged=True, info={"rule": p.rule, "live": int(cells.sum())})
    cells = rng.random((height, width)) < p.density
    trail = np.zeros((height, width))
    for _ in range(p.steps):
        cells = life_step(cells)
        trail = trail * 0.85 + cells
    return Drawing(grid=np.clip(trail / 3.0, 0, 1), ranged=True, info={"live": int(cells.sum()), "steps": p.steps})


# -- fractals ---------------------------------------------------------------------------------------------------------

def mandelbrot_escape(c: np.ndarray, max_iter: int, z0: np.ndarray | None = None) -> np.ndarray:
    """For each point, the iteration at which ``z -> z**2 + c`` first leaves the disc of radius 2 (``max_iter`` if it
    never does), starting from ``z0`` (0 for the Mandelbrot set)."""
    return _escape(c, max_iter, z0)[0]


def _escape(c: np.ndarray, max_iter: int, z0: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    z = np.zeros_like(c) if z0 is None else z0.astype(complex).copy()
    c = np.broadcast_to(c, z.shape)
    count = np.full(z.shape, max_iter, dtype=int)
    smooth = np.zeros(z.shape)
    alive = np.ones(z.shape, dtype=bool)
    with np.errstate(all="ignore"):
        for i in range(max_iter):
            z[alive] = z[alive] ** 2 + c[alive]
            out = alive & (np.abs(z) > 2.0)
            count[out] = i + 1
            smooth[out] = i + 1 - np.log2(np.log(np.abs(z[out])))
            alive &= ~out
            if not alive.any():
                break
    return count, smooth


def koch(depth: int) -> np.ndarray:
    """The closed Koch snowflake after ``depth`` subdivisions: ``3 * 4**depth + 1`` points."""
    pts = np.array([(0.0, 0.0), (1.0, 0.0), (0.5, math.sqrt(3) / 2), (0.0, 0.0)])
    cos, sin = math.cos(-math.pi / 3), math.sin(-math.pi / 3)
    for _ in range(depth):
        a, b = pts[:-1], pts[1:]
        third = (b - a) / 3
        p1, p3 = a + third, a + 2 * third
        p2 = p1 + np.stack([third[:, 0] * cos - third[:, 1] * sin, third[:, 0] * sin + third[:, 1] * cos], axis=1)
        pts = np.vstack([np.stack([a, p1, p2, p3], axis=1).reshape(-1, 2), pts[-1:]])
    return pts


def _plane(width: int, height: int, centre: tuple[float, float], half_width: float) -> np.ndarray:
    half_height = half_width * height / width
    re = np.linspace(centre[0] - half_width, centre[0] + half_width, width)
    im = np.linspace(centre[1] + half_height, centre[1] - half_height, height)
    return re[None, :] + 1j * im[:, None]


def density(xs: np.ndarray, ys: np.ndarray, width: int, height: int, margin: float = 0.05) -> np.ndarray:
    """Points counted per pixel, fitted to the picture with their aspect kept (y up), on a log scale."""
    xs, ys = np.ravel(xs), np.ravel(ys)
    keep = np.isfinite(xs) & np.isfinite(ys)
    xs, ys = xs[keep], ys[keep]
    grid = np.zeros((height, width))
    if xs.size == 0:
        return grid
    px, py = _fit(np.stack([xs, ys], axis=1), width, height, margin)
    ix = np.clip(px.astype(int), 0, width - 1)
    iy = np.clip(py.astype(int), 0, height - 1)
    np.add.at(grid, (iy, ix), 1.0)
    return np.log1p(grid)


def _fit(points: np.ndarray, width: int, height: int, margin: float) -> tuple[np.ndarray, np.ndarray]:
    """Pixel coordinates for ``points`` (y up), scaled to fit inside the margin with their aspect kept."""
    lo, hi = points.min(axis=0), points.max(axis=0)
    span = np.where(hi - lo > 0, hi - lo, 1.0)
    usable = np.array([width, height]) * (1 - 2 * margin)
    scale = float(np.min(usable / span))
    centre = (lo + hi) / 2
    px = (points[:, 0] - centre[0]) * scale + width / 2
    py = height / 2 - (points[:, 1] - centre[1]) * scale
    return px, py


def _draw_fractal(p: Any, width: int, height: int, rng: np.random.Generator, seed: int) -> Drawing:
    if p.kind == "koch":
        return Drawing(strokes=[koch(p.depth)], info={"depth": p.depth, "line_width": p.line_width})
    if p.kind == "sierpinski":
        chains = 1000
        steps = max(1, p.points // chains)
        corners = np.array([(0.0, 0.0), (1.0, 0.0), (0.5, math.sqrt(3) / 2)])
        at = rng.random((chains, 2))
        xs, ys = [], []
        for step in range(steps + 8):
            at = (at + corners[rng.integers(0, 3, chains)]) / 2
            if step >= 8:
                xs.append(at[:, 0].copy())
                ys.append(at[:, 1].copy())
        return Drawing(grid=density(np.array(xs), np.array(ys), width, height), info={"points": chains * steps})
    plane = _plane(width, height, p.centre or ((-0.5, 0.0) if p.kind == "mandelbrot" else (0.0, 0.0)), 1.6 / p.zoom)
    if p.kind == "mandelbrot":
        count, smooth = _escape(plane, p.max_iter)
    else:
        count, smooth = _escape(np.full(plane.shape, complex(*p.c)), p.max_iter, z0=plane)
    inside = count >= p.max_iter
    return Drawing(grid=np.where(inside, 0.0, np.sqrt(np.maximum(smooth, 0))),
                   info={"inside_fraction": round(float(inside.mean()), 6), "max_iter": p.max_iter})


# -- noise, flow fields and terrain -----------------------------------------------------------------------------------

def _fade(t: np.ndarray) -> np.ndarray:
    return t * t * t * (t * (t * 6 - 15) + 10)


def perlin(width: int, height: int, cell: float, rng: np.random.Generator) -> np.ndarray:
    """Gradient (Perlin) noise with lattice cells ``cell`` pixels across: 0 on every lattice point."""
    gw, gh = int(width / cell) + 2, int(height / cell) + 2
    angle = rng.uniform(0, 2 * math.pi, (gh + 1, gw + 1))
    gx, gy = np.cos(angle), np.sin(angle)
    xs, ys = np.arange(width) / cell, np.arange(height) / cell
    x0, y0 = np.floor(xs).astype(int), np.floor(ys).astype(int)
    fx, fy = xs - x0, ys - y0

    def corner(dy: int, dx: int) -> np.ndarray:
        rows, cols = (y0 + dy)[:, None], (x0 + dx)[None, :]
        return gx[rows, cols] * (fx - dx)[None, :] + gy[rows, cols] * (fy - dy)[:, None]

    u, v = _fade(fx)[None, :], _fade(fy)[:, None]
    top = corner(0, 0) * (1 - u) + corner(0, 1) * u
    bottom = corner(1, 0) * (1 - u) + corner(1, 1) * u
    return top * (1 - v) + bottom * v


def noise(width: int, height: int, scale: float, octaves: int, persistence: float,
          rng: np.random.Generator) -> np.ndarray:
    """Perlin noise summed over ``octaves``, each half the size of the last and ``persistence`` times as strong."""
    total = np.zeros((height, width))
    for octave in range(octaves):
        cell = scale / 2**octave
        if cell < 1.5:
            break
        total += persistence**octave * perlin(width, height, cell, rng)
    return total


def diamond_square(width: int, height: int, roughness: float, rng: np.random.Generator) -> np.ndarray:
    """A diamond-square heightmap of exactly ``height`` by ``width``: built on the smallest ``2**n + 1`` square that
    covers it and cropped, with displacements shrinking by ``roughness`` at each level."""
    n = 1
    while n + 1 < max(width, height, 2):
        n *= 2
    size = n + 1
    g = np.zeros((size, size))
    g[0, 0], g[0, -1], g[-1, 0], g[-1, -1] = rng.random(4)
    step, scale = n, 1.0
    while step > 1:
        half = step // 2
        corners = g[0:-1:step, 0:-1:step] + g[0:-1:step, step::step] + g[step::step, 0:-1:step] + g[step::step, step::step]
        g[half::step, half::step] = corners / 4 + (rng.random(corners.shape) - 0.5) * scale
        for first_row, first_col in ((0, half), (half, 0)):
            rows, cols = np.meshgrid(np.arange(first_row, size, step), np.arange(first_col, size, step), indexing="ij")
            total, count = np.zeros(rows.shape), np.zeros(rows.shape)
            for dy, dx in ((-half, 0), (half, 0), (0, -half), (0, half)):
                r, c = rows + dy, cols + dx
                ok = (r >= 0) & (r < size) & (c >= 0) & (c < size)
                total[ok] += g[r[ok], c[ok]]
                count[ok] += 1
            g[rows, cols] = total / count + (rng.random(rows.shape) - 0.5) * scale
        step, scale = half, scale * roughness
    return g[:height, :width].copy()


def _draw_noise(p: Any, width: int, height: int, rng: np.random.Generator, seed: int) -> Drawing:
    return Drawing(grid=noise(width, height, p.scale, p.octaves, p.persistence, rng),
                   info={"octaves": p.octaves})


def _draw_terrain(p: Any, width: int, height: int, rng: np.random.Generator, seed: int) -> Drawing:
    if p.kind == "perlin":
        return Drawing(grid=noise(width, height, p.scale, p.octaves, 0.5, rng), info={"kind": p.kind})
    return Drawing(grid=diamond_square(width, height, p.roughness, rng), info={"kind": p.kind})


def _draw_flow_field(p: Any, width: int, height: int, rng: np.random.Generator, seed: int) -> Drawing:
    potential = noise(width, height, p.scale, 3, 0.5, rng)
    dy, dx = np.gradient(potential)
    vx, vy = dy, -dx                                   # the curl of the potential: a flow with no sources or sinks
    speed = np.hypot(vx, vy)
    speed[speed == 0] = 1.0
    vx, vy = vx / speed * p.step_size, vy / speed * p.step_size
    px, py = rng.random(p.particles) * width, rng.random(p.particles) * height
    counts = np.zeros((height, width))
    for _ in range(p.steps):
        ix, iy = px.astype(int) % width, py.astype(int) % height
        np.add.at(counts, (iy, ix), 1.0)
        px, py = (px + vx[iy, ix]) % width, (py + vy[iy, ix]) % height
    return Drawing(grid=np.log1p(counts), info={"particles": p.particles, "steps": p.steps})


# -- reaction-diffusion -----------------------------------------------------------------------------------------------

def _laplacian(a: np.ndarray) -> np.ndarray:
    return np.roll(a, 1, 0) + np.roll(a, -1, 0) + np.roll(a, 1, 1) + np.roll(a, -1, 1) - 4 * a


def _draw_reaction_diffusion(p: Any, width: int, height: int, rng: np.random.Generator, seed: int) -> Drawing:
    u, v = np.ones((height, width)), np.zeros((height, width))
    r = max(2, min(width, height) // 10)
    centres = [(height // 2, width // 2)] + [(int(rng.integers(r, max(r + 1, height - r))),
                                             int(rng.integers(r, max(r + 1, width - r)))) for _ in range(4)]
    for cy, cx in centres:
        sl = (slice(max(0, cy - r), cy + r), slice(max(0, cx - r), cx + r))
        u[sl] = 0.5 + rng.random(u[sl].shape) * 0.1
        v[sl] = 0.25 + rng.random(v[sl].shape) * 0.1
    for _ in range(p.steps):
        uvv = u * v * v
        u = np.clip(u + p.diffusion_u * _laplacian(u) - uvv + p.feed * (1 - u), 0, 1)
        v = np.clip(v + p.diffusion_v * _laplacian(v) + uvv - (p.feed + p.kill) * v, 0, 1)
    return Drawing(grid=v, ranged=True, info={"v_max": float(v.max()), "steps": p.steps})


# -- attractors -------------------------------------------------------------------------------------------------------

_MAPS = {"clifford": (-1.4, 1.6, 1.0, 0.7), "dejong": (-2.0, -2.0, -1.2, 2.0)}


def _draw_attractor(p: Any, width: int, height: int, rng: np.random.Generator, seed: int) -> Drawing:
    # a map fills its attractor from many short orbits; a flow needs fewer, longer ones to trace its whole shape
    orbits = int(min(1000 if p.kind in _MAPS else 40, max(1, p.points // 200)))
    steps = max(1, p.points // orbits)
    xs, ys = np.empty((steps, orbits)), np.empty((steps, orbits))
    if p.kind in _MAPS:
        a, b, c, d = (given if given is not None else default
                      for given, default in zip((p.a, p.b, p.c, p.d), _MAPS[p.kind], strict=True))
        x, y = rng.normal(0, 0.1, orbits), rng.normal(0, 0.1, orbits)
        for i in range(steps + 20):
            if p.kind == "clifford":
                x, y = np.sin(a * y) + c * np.cos(a * x), np.sin(b * x) + d * np.cos(b * y)
            else:
                x, y = np.sin(a * y) - np.cos(b * x), np.sin(c * x) - np.cos(d * y)
            if i >= 20:
                xs[i - 20], ys[i - 20] = x, y
    else:
        state = rng.normal(0, 1, (orbits, 3)) + (1.0, 1.0, 1.0)
        dt = 0.005 if p.kind == "lorenz" else 0.02

        def flow(s: np.ndarray) -> np.ndarray:
            x, y, z = s[:, 0], s[:, 1], s[:, 2]
            if p.kind == "lorenz":
                return np.stack([10 * (y - x), x * (28 - z) - y, x * y - 8 / 3 * z], axis=1)
            return np.stack([-y - z, x + 0.2 * y, 0.2 + z * (x - 5.7)], axis=1)

        for i in range(steps + 400):
            state = state + dt * flow(state + dt / 2 * flow(state))
            if i >= 400:
                xs[i - 400] = state[:, 0]
                ys[i - 400] = state[:, 2] if p.kind == "lorenz" else state[:, 1]
    return Drawing(grid=density(xs, ys, width, height, margin=0.04), info={"orbits": orbits, "points": orbits * steps})


# -- Voronoi ----------------------------------------------------------------------------------------------------------

def _nearest(sites: np.ndarray, width: int, height: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    xs, ys = np.arange(width) + 0.5, np.arange(height) + 0.5
    best = np.full((height, width), np.inf)
    second = np.full((height, width), np.inf)
    label = np.zeros((height, width), dtype=int)
    for k, (sx, sy) in enumerate(sites):
        d = (xs[None, :] - sx) ** 2 + (ys[:, None] - sy) ** 2
        closer = d < best
        second = np.where(closer, best, np.minimum(second, d))
        best = np.where(closer, d, best)
        label = np.where(closer, k, label)
    return best, second, label


def _draw_voronoi(p: Any, width: int, height: int, rng: np.random.Generator, seed: int) -> Drawing:
    sites = rng.random((p.cells, 2)) * (width, height)
    for _ in range(p.relax):
        _, _, label = _nearest(sites, width, height)
        weight = np.bincount(label.ravel(), minlength=p.cells)
        ys, xs = np.indices((height, width)) + 0.5
        cx = np.bincount(label.ravel(), weights=xs.ravel(), minlength=p.cells)
        cy = np.bincount(label.ravel(), weights=ys.ravel(), minlength=p.cells)
        moved = weight > 0
        sites[moved] = np.stack([cx[moved], cy[moved]], axis=1) / weight[moved, None]
    best, second, label = _nearest(sites, width, height)
    spacing = math.sqrt(width * height / p.cells)
    if p.colouring == "cells":
        grid = rng.permutation(p.cells)[label] / max(1, p.cells - 1)
    elif p.colouring == "distance":
        grid = np.clip(np.sqrt(best) / spacing, 0, 1)
    else:
        grid = np.clip((np.sqrt(second) - np.sqrt(best)) / (0.25 * spacing), 0, 1)
    return Drawing(grid=grid, ranged=True, info={"cells": p.cells, "relax": p.relax})


# -- pattern networks (CPPN) and the genetic algorithm that evolves them ----------------------------------------------

_ACTIVATIONS = (np.sin, np.tanh, lambda x: np.exp(-x * x), np.abs, np.cos)


def _network(rng: np.random.Generator, layers: int, neurons: int) -> tuple[list[tuple[int, int]], np.ndarray]:
    """The weight shapes of a network over the inputs (x, y, r, 1), and the activation each neuron uses."""
    shapes, width_in = [], 4
    for _ in range(layers):
        shapes.append((width_in, neurons))
        width_in = neurons
    shapes.append((width_in, 1))
    return shapes, rng.integers(0, len(_ACTIVATIONS), (layers, neurons))


def _unflatten(genome: np.ndarray, shapes: list[tuple[int, int]]) -> list[np.ndarray]:
    found, at = [], 0
    for rows, cols in shapes:
        found.append(genome[at:at + rows * cols].reshape(rows, cols))
        at += rows * cols
    return found


def _run_network(weights: list[np.ndarray], acts: np.ndarray, width: int, height: int, zoom: float) -> np.ndarray:
    aspect = height / width
    xs = np.linspace(-zoom, zoom, width)
    ys = np.linspace(-zoom * aspect, zoom * aspect, height)
    gx, gy = np.meshgrid(xs, ys)
    inputs = np.stack([gx.ravel(), gy.ravel(), np.hypot(gx, gy).ravel(), np.ones(gx.size)], axis=1)
    out = np.empty(gx.size)
    for start in range(0, gx.size, 65536):
        h = inputs[start:start + 65536]
        for layer, w in enumerate(weights[:-1]):
            z = h @ w
            h = np.empty_like(z)
            for k, act in enumerate(acts[layer]):
                h[:, k] = _ACTIVATIONS[act](z[:, k])
        out[start:start + 65536] = np.tanh(h @ weights[-1])[:, 0]
    return ((out + 1) / 2).reshape(height, width)


def _draw_cppn(p: Any, width: int, height: int, rng: np.random.Generator, seed: int) -> Drawing:
    shapes, acts = _network(rng, p.layers, p.neurons)
    genome = rng.normal(0, 1.2, sum(r * c for r, c in shapes))
    return Drawing(grid=_run_network(_unflatten(genome, shapes), acts, width, height, p.zoom), ranged=True,
                   info={"layers": p.layers, "neurons": p.neurons})


def _fitness(kind: str, img: np.ndarray) -> float:
    contrast = min(1.0, float(img.std()) * 4)
    edges = min(1.0, float(np.abs(np.diff(img, axis=0)).mean() + np.abs(np.diff(img, axis=1)).mean()) * 8)
    if kind == "symmetry":
        return contrast * (1 - float(np.abs(img - img[:, ::-1]).mean()))
    if kind == "complexity":
        return contrast * edges
    return contrast * (1 - edges)


def _draw_evolved(p: Any, width: int, height: int, rng: np.random.Generator, seed: int) -> Drawing:
    shapes, acts = _network(rng, 2, 8)
    length = sum(r * c for r, c in shapes)
    population = rng.normal(0, 1.2, (p.population, length))
    history: list[float] = []
    best, best_score = population[0], -1.0
    for _ in range(p.generations):
        scores = np.array([_fitness(p.fitness, _run_network(_unflatten(g, shapes), acts, 48, 48, 1.0))
                           for g in population])
        top = int(np.argmax(scores))
        if scores[top] > best_score:
            best, best_score = population[top].copy(), float(scores[top])
        history.append(round(best_score, 6))
        children = [best.copy()]
        while len(children) < p.population:
            pick = [max(rng.integers(0, p.population, 3), key=lambda i: scores[i]) for _ in range(2)]
            mask = rng.random(length) < 0.5
            child = np.where(mask, population[pick[0]], population[pick[1]])
            mutate = rng.random(length) < 0.2
            children.append(child + mutate * rng.normal(0, p.mutation, length))
        population = np.array(children)
    return Drawing(grid=_run_network(_unflatten(best, shapes), acts, width, height, 1.0), ranged=True,
                   info={"fitness": p.fitness, "best_fitness_by_generation": history})


# -- domain colouring -------------------------------------------------------------------------------------------------

def _draw_domain_colouring(p: Any, width: int, height: int, rng: np.random.Generator, seed: int) -> Drawing:
    plane = _plane(width, height, p.centre, p.extent)
    with np.errstate(all="ignore"):
        values = np.broadcast_to(expression.evaluate(expression.parse(p.expression), plane), plane.shape)
        finite = np.isfinite(values)
        if p.colour_by == "phase":
            grid = np.where(finite, np.mod(np.angle(values) / (2 * math.pi), 1.0), 0.0)
        else:
            modulus = np.abs(values)
            grid = np.where(finite, modulus / (1 + modulus), 1.0)
    return Drawing(grid=grid, ranged=True, info={"not_finite_fraction": round(float(1 - finite.mean()), 6)})


# -- random walks -----------------------------------------------------------------------------------------------------

_LATTICE = np.array([(1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0)])


def _draw_random_walk(p: Any, width: int, height: int, rng: np.random.Generator, seed: int) -> Drawing:
    walks = []
    for _ in range(p.walkers):
        steps = _LATTICE[rng.integers(0, 4, p.steps)] if p.kind == "lattice" else rng.normal(0, 1, (p.steps, 2))
        walks.append(np.vstack([np.zeros((1, 2)), np.cumsum(steps, axis=0)]))
    return Drawing(strokes=walks, info={"walkers": p.walkers, "steps": p.steps, "line_width": p.line_width})


# -- shape grammars ---------------------------------------------------------------------------------------------------

def _rect(grid: np.ndarray, x: float, y: float, w: float, h: float, value: float, border: bool) -> None:
    gh, gw = grid.shape
    x0, y0, x1, y1 = max(0, int(x * gw)), max(0, int(y * gh)), min(gw, int((x + w) * gw)), min(gh, int((y + h) * gh))
    if x1 <= x0 or y1 <= y0:
        return
    grid[y0:y1, x0:x1] = value
    if border:
        grid[y0, x0:x1] = grid[y1 - 1, x0:x1] = grid[y0:y1, x0] = grid[y0:y1, x1 - 1] = 0.0


def _circle(grid: np.ndarray, cx: float, cy: float, r: float, value: float) -> None:
    gh, gw = grid.shape
    yy, xx = np.ogrid[:gh, :gw]
    grid[(xx - cx * gw) ** 2 + (yy - cy * gh) ** 2 <= (r * min(gw, gh)) ** 2] = value


def _subdivide(rule: str, rng: random.Random, grid: np.ndarray, x: float, y: float, w: float, h: float, depth: int,
               max_depth: int, min_cell: float) -> int:
    """Split a rectangle by ``rule`` until it is deep or small enough, then fill it; the number of pieces filled."""
    leaf = depth >= max_depth or w < min_cell or h < min_cell
    if rule == "architectural" and not leaf and rng.random() >= 0.7:
        leaf = True
    if leaf:
        if rule == "organic":
            _circle(grid, x + w / 2, y + h / 2, min(w, h) / 2, 0.3 + rng.random() * 0.7)
        else:
            value = {"mondrian": rng.choice((0.2, 0.4, 0.6, 0.8, 1.0)), "circuit": rng.choice((0.5, 0.7, 0.9))}.get(
                rule, min(1.0, 0.3 + depth * 0.1))
            _rect(grid, x, y, w, h, value, border=True)
        return 1
    go = (rule, rng, grid)
    if rule == "circuit" and rng.random() < 0.5:
        return sum(_subdivide(*go, x + i * w / 2, y + j * h / 2, w / 2, h / 2, depth + 1, max_depth, min_cell)
                   for i in (0, 1) for j in (0, 1))
    if rule == "circuit":
        parts = [(x + i * w / 3, y, w / 3, h) if w > h else (x, y + i * h / 3, w, h / 3) for i in range(3)]
        return sum(_subdivide(*go, *part, depth + 1, max_depth, min_cell) for part in parts)
    vertical = w > h if rule in ("mondrian", "architectural") else rng.random() < 0.5
    cut = rng.uniform(0.35, 0.65) if rule == "organic" else rng.uniform(0.3, 0.7)
    if vertical:
        first, second = (x, y, cut * w, h), (x + cut * w, y, w - cut * w, h)
    else:
        first, second = (x, y, w, cut * h), (x, y + cut * h, w, h - cut * h)
    if rule == "architectural":
        _rect(grid, *(second[:2] + ((0.01, h) if vertical else (w, 0.01))), 0.1, border=False)
    return (_subdivide(*go, *first, depth + 1, max_depth, min_cell)
            + _subdivide(*go, *second, depth + 1, max_depth, min_cell))


def _draw_shape_grammar(p: Any, width: int, height: int, rng: np.random.Generator, seed: int) -> Drawing:
    grid = np.zeros((height, width))
    pieces = _subdivide(p.rule_set, random.Random(seed), grid, 0.0, 0.0, 1.0, 1.0, 0, p.depth, p.min_cell)
    return Drawing(grid=grid, ranged=True, info={"rule_set": p.rule_set, "pieces": pieces})


def draw(technique: str, params: Any, width: int, height: int, seed: int) -> Drawing:
    """``technique`` with its checked ``params`` at ``width`` by ``height``, from ``seed`` alone."""
    painter = globals()[f"_draw_{technique}"]
    return painter(params, width, height, np.random.default_rng(seed), seed)


__all__ = ["MAX_SYMBOLS", "Drawing", "density", "diamond_square", "draw", "expand", "koch", "life_step",
           "mandelbrot_escape", "noise", "perlin", "turtle", "wolfram"]
