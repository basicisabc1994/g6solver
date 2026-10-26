# SPDX-License-Identifier: Apache-2.0
"""From a technique's drawing to file bytes (NumPy and Pillow, the ``procedural`` extra): rasterise, tone-map, blend
layers, filter, colour with a palette, and write PNG, SVG or OBJ.

A grid in its own units is stretched to 0 to 1; one whose range is effectively nothing (less than a billionth of its
size) becomes flat, so a collapsed technique is shown as the flat picture it is and refused by the inspector, never
stretched into amplified rounding noise.
"""
from __future__ import annotations

import io
from typing import Any

import numpy as np

from g6.media.adapt_generative_art.spec import PALETTES
from g6.media.adapt_generative_art.techniques import Drawing, _fit

MARGIN = 0.05


def normalise(grid: np.ndarray) -> np.ndarray:
    """``grid`` stretched to 0 to 1; flat (zeros) when its range is effectively nothing or it has no finite value."""
    grid = np.where(np.isfinite(grid), grid, np.nan)
    if np.isnan(grid).all():
        return np.zeros(grid.shape)
    lo, hi = float(np.nanmin(grid)), float(np.nanmax(grid))
    if hi - lo <= 1e-9 * max(1.0, abs(lo), abs(hi)):
        return np.zeros(grid.shape)
    return np.nan_to_num((grid - lo) / (hi - lo), nan=0.0)


def raster(drawing: Drawing, width: int, height: int) -> np.ndarray:
    """A drawing as a grid of values from 0 to 1, ``height`` by ``width``."""
    if drawing.grid is not None:
        grid = np.asarray(drawing.grid, dtype=float)
        return np.clip(np.nan_to_num(grid, nan=0.0), 0, 1) if drawing.ranged else normalise(grid)
    from PIL import Image, ImageDraw

    canvas = Image.new("L", (width, height), 0)
    pen = ImageDraw.Draw(canvas)
    strokes = drawing.strokes or []
    if strokes:
        every = np.vstack(strokes)
        lo, hi = every.min(axis=0), every.max(axis=0)
        for line in strokes:
            px, py = _fit(np.vstack([line, lo, hi]), width, height, MARGIN)
            pen.line(list(zip(px[:-2].tolist(), py[:-2].tolist(), strict=True)), fill=255,
                     width=int(drawing.info.get("line_width", 1)))
    return np.asarray(canvas, dtype=float) / 255.0


def tone_map(grid: np.ndarray, mode: str) -> np.ndarray:
    """``equalise``: each value replaced by its rank (an even spread); ``reinhard``: ``v / (1 + v)`` on values scaled
    to their mean, then stretched; ``none``: unchanged."""
    if mode == "equalise":
        flat = grid.ravel()
        if np.ptp(flat) == 0:
            return grid
        ranks = np.empty(flat.size)
        ranks[np.argsort(flat, kind="stable")] = np.arange(flat.size)
        return (ranks / max(1, flat.size - 1)).reshape(grid.shape)
    if mode == "reinhard":
        mean = float(grid.mean())
        scaled = grid / mean if mean > 0 else grid
        return normalise(scaled / (1 + scaled))
    return grid


def blend(base: np.ndarray, layer: np.ndarray, mode: str, weight: float) -> np.ndarray:
    """``layer`` blended onto ``base`` by ``mode``, mixed in at ``weight``."""
    mixed = {"add": lambda a, b: np.clip(a + b, 0, 1), "multiply": lambda a, b: a * b,
             "screen": lambda a, b: 1 - (1 - a) * (1 - b),
             "overlay": lambda a, b: np.where(a < 0.5, 2 * a * b, 1 - 2 * (1 - a) * (1 - b)),
             "max": np.maximum, "min": np.minimum, "difference": lambda a, b: np.abs(a - b)}[mode](base, layer)
    return np.clip(base * (1 - weight) + mixed * weight, 0, 1)


def _shift_max(grid: np.ndarray, radius: int, pick: Any) -> np.ndarray:
    out = grid.copy()
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx * dx + dy * dy <= radius * radius:
                out = pick(out, np.roll(np.roll(grid, dy, 0), dx, 1))
    return out


def _box(grid: np.ndarray, radius: int) -> np.ndarray:
    padded = np.pad(grid, radius, mode="edge")
    total = padded.cumsum(0).cumsum(1)
    total = np.pad(total, ((1, 0), (1, 0)))
    k = 2 * radius + 1
    h, w = grid.shape
    return (total[k:k + h, k:k + w] - total[:h, k:k + w] - total[k:k + h, :w] + total[:h, :w]) / (k * k)


def apply_filters(grid: np.ndarray, filters: list[Any]) -> np.ndarray:
    """Each filter in order, on values from 0 to 1 (unknown filters cannot reach here: the input model refuses
    them)."""
    for f in filters:
        kind = f.filter
        if kind == "blur":
            grid = _box(_box(grid, f.radius), f.radius)
        elif kind == "sharpen":
            grid = np.clip(grid + f.amount * (grid - _box(grid, 1)), 0, 1)
        elif kind == "edges":
            gx = np.roll(grid, -1, 1) - np.roll(grid, 1, 1)
            gy = np.roll(grid, -1, 0) - np.roll(grid, 1, 0)
            grid = np.clip(np.hypot(gx, gy), 0, 1)
        elif kind == "threshold":
            grid = (grid > f.level).astype(float)
        elif kind == "invert":
            grid = 1 - grid
        elif kind == "contrast":
            grid = 1 / (1 + np.exp(-(grid - 0.5) * f.factor * 4))
        elif kind == "posterise":
            grid = np.round(grid * (f.levels - 1)) / (f.levels - 1)
        elif kind == "dilate":
            grid = _shift_max(grid, f.radius, np.maximum)
        elif kind == "erode":
            grid = _shift_max(grid, f.radius, np.minimum)
    return grid


def palette(name: str, colours: list[str] | None = None) -> np.ndarray:
    """A 256 by 3 table of colours from the named palette's stops, or from ``colours`` when given."""
    stops = colours or list(PALETTES[name])
    rgb = np.array([[int(s[i:i + 2], 16) for i in (1, 3, 5)] for s in stops], dtype=float)
    at = np.linspace(0, 1, len(stops))
    t = np.linspace(0, 1, 256)
    return np.stack([np.interp(t, at, rgb[:, c]) for c in range(3)], axis=1).round().astype(np.uint8)


def png(grid: np.ndarray, table: np.ndarray) -> bytes:
    """``grid`` (0 to 1) coloured by ``table`` as PNG bytes, with no metadata (the same grid gives the same bytes)."""
    from PIL import Image

    index = np.clip((grid * 255).round(), 0, 255).astype(np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(table[index])).save(buffer, format="PNG")
    return buffer.getvalue()


def _hex(rgb: np.ndarray) -> str:
    return "#" + "".join(f"{int(v):02x}" for v in rgb)


def svg(drawing: Drawing, width: int, height: int, table: np.ndarray) -> bytes:
    """A stroke drawing as an SVG: one path per stroke, fitted to the picture, on the palette's lowest colour."""
    strokes = drawing.strokes or []
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
             f'viewBox="0 0 {width} {height}">', f'<rect width="{width}" height="{height}" fill="{_hex(table[0])}"/>']
    if strokes:
        every = np.vstack(strokes)
        lo, hi = every.min(axis=0), every.max(axis=0)
        line_width = int(drawing.info.get("line_width", 1))
        for line in strokes:
            px, py = _fit(np.vstack([line, lo, hi]), width, height, MARGIN)
            points = " L ".join(f"{x:.2f} {y:.2f}" for x, y in zip(px[:-2], py[:-2], strict=True))
            parts.append(f'<path d="M {points}" fill="none" stroke="{_hex(table[-1])}" stroke-width="{line_width}" '
                         'stroke-linecap="round" stroke-linejoin="round"/>')
    parts.append("</svg>")
    return ("\n".join(parts) + "\n").encode("utf-8")


def obj(grid: np.ndarray, height_scale: float, header: str) -> bytes:
    """A heightmap as a triangle mesh in OBJ (y up): square cells two units across the longer side, centred on the
    origin, heights from 0 to ``height_scale``."""
    rows, cols = grid.shape
    spacing = 2.0 / (max(rows, cols) - 1)
    xs = (np.arange(cols) - (cols - 1) / 2) * spacing
    zs = (np.arange(rows) - (rows - 1) / 2) * spacing
    lines = [f"# {header}"]
    lines += [f"v {x:.6f} {y:.6f} {z:.6f}" for z, row in zip(zs, grid * height_scale, strict=True)
              for x, y in zip(xs, row, strict=True)]
    index = np.arange(rows * cols).reshape(rows, cols) + 1
    a, b, c, d = index[:-1, :-1].ravel(), index[:-1, 1:].ravel(), index[1:, :-1].ravel(), index[1:, 1:].ravel()
    lines += [f"f {i} {j} {k}" for i, j, k in zip(a, c, b, strict=True)]
    lines += [f"f {i} {j} {k}" for i, j, k in zip(b, c, d, strict=True)]
    return ("\n".join(lines) + "\n").encode("utf-8")


__all__ = ["MARGIN", "apply_filters", "blend", "normalise", "obj", "palette", "png", "raster", "svg", "tone_map"]
