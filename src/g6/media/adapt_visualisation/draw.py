# SPDX-License-Identifier: Apache-2.0
"""G6's own chart drawing: the rows become a :class:`Scene` of marks (bars, points, a line, bins, boxes, cells, ticks
and labels) in pixels, which is written as SVG (standard library), as PNG (Pillow, the ``image`` extra) or as an HTML
page holding the SVG and a table of the rows.

Every value axis comes from round ticks (steps of 1, 2, 2.5 or 5 times a power of ten), and the ticks are drawn as text
at their exact positions, so :mod:`.readback` can read every mark's value back from the file alone. A bar chart's and a
histogram's value axis always includes zero, and every bar starts there. Nothing written loads anything: no script,
no link, no image, no style sheet; the page carries a content security policy that allows none.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from html import escape
from typing import Any

from g6.media.adapt_visualisation.data import ChartDataError, bins, box_stats, shown, sturges

INK, MUTED, GRID, BACKGROUND = "#111827", "#4b5563", "#e5e7eb", "#ffffff"
SERIES, BOX_FILL, EMPTY = "#2563eb", "#bfdbfe", "#f3f4f6"
LOW, HIGH = (239, 246, 255), (30, 58, 138)
#: The most bars, box groups, heat-map categories on a side (so 100 by 100 cells) and points on a line over categories.
MAX_BARS, MAX_GROUPS, MAX_SIDE, MAX_LINE_CATEGORIES = 200, 50, 100, 1000
MARKERS, POINT_LABELS = 200, 30
CSP = "default-src 'none'; style-src 'unsafe-inline'"


@dataclass
class Mark:
    """One thing drawn. ``probe`` is a pixel inside a filled mark that a PNG check reads its colour at (see
    :func:`probes`)."""

    shape: str  # rect, circle, polyline, line, text
    cls: str
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    r: float = 0.0
    x2: float = 0.0
    y2: float = 0.0
    points: list[tuple[float, float]] = field(default_factory=list)
    fill: str = "none"
    stroke: str = "none"
    stroke_width: float = 1.0
    text: str = ""
    anchor: str = "start"
    middle: bool = False
    size: int = 12
    rotate: float = 0.0
    probe: tuple[float, float] | None = None


@dataclass
class Scene:
    kind: str
    width: int
    height: int
    title: str
    marks: list[Mark] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    table: tuple[list[str], list[list[str]]] = ([], [])


@dataclass
class Linear:
    lo: float
    hi: float
    p0: float
    p1: float
    ticks: list[float]
    labels: list[str]

    def __call__(self, value: float) -> float:
        return self.p0 + (value - self.lo) * (self.p1 - self.p0) / (self.hi - self.lo)


def linear(values: list[float], p0: float, p1: float, *, zero: bool = False, count: int = 5) -> Linear:
    lo, hi = min(values), max(values)
    if zero:
        lo, hi = min(lo, 0.0), max(hi, 0.0)
    if lo == hi:
        pad = abs(lo) * 0.1 or 1.0
        lo, hi = (lo, hi + pad) if zero and lo == 0 else (lo - pad, hi + pad)
    raw = (hi - lo) / count
    magnitude = 10 ** math.floor(math.log10(raw))
    factor = next(m for m in (1, 2, 2.5, 5, 10) if raw <= m * magnitude * (1 + 1e-12))
    step = factor * magnitude
    start, end = math.floor(lo / step + 1e-9) * step, math.ceil(hi / step - 1e-9) * step
    decimals = max(0, -math.floor(math.log10(step) + 1e-12)) + (1 if factor == 2.5 else 0)
    ticks = [round(start + i * step, decimals + 3) for i in range(round((end - start) / step) + 1)]
    labels = ["0" if float(f"{t:.{decimals}f}") == 0 else f"{t:.{decimals}f}" for t in ticks]
    return Linear(ticks[0], ticks[-1], p0, p1, ticks, labels)


@dataclass
class Bands:
    names: list[str]
    p0: float
    p1: float
    padding: float = 0.2

    @property
    def step(self) -> float:
        return (self.p1 - self.p0) / max(1, len(self.names))

    def start(self, i: int) -> float:
        return self.p0 + i * self.step + self.step * self.padding / 2

    def width(self) -> float:
        return self.step * (1 - self.padding)

    def centre(self, i: int) -> float:
        return self.p0 + (i + 0.5) * self.step


def _text_width(text: str, size: int = 12) -> float:
    return 0.6 * size * len(text)


def _colour(t: float) -> str:
    return "#" + "".join(f"{round(a + t * (b - a)):02x}" for a, b in zip(LOW, HIGH))


class _Frame:
    """The plot area and its two axes, drawn into ``scene``."""

    def __init__(self, scene: Scene, y_labels: list[str], *, legend: int = 0, rotate_x: bool = False) -> None:
        self.scene = scene
        widest = max((_text_width(t) for t in y_labels), default=0)
        self.left = max(56.0, widest + 34.0)
        self.right = 20.0 + legend
        self.top = 40.0 if scene.title else 16.0
        self.bottom = 76.0 if rotate_x else 52.0
        self.x0, self.x1 = self.left, scene.width - self.right
        self.y0, self.y1 = self.top, scene.height - self.bottom
        if self.x1 - self.x0 < 60 or self.y1 - self.y0 < 40:
            raise ChartDataError("the picture is too small for its axes and labels; ask for a larger width or height")
        if scene.title:
            self.add(Mark("text", "g6-title", x=scene.width / 2, y=24, text=scene.title, anchor="middle", size=15,
                          fill=INK))

    def add(self, mark: Mark) -> None:
        self.scene.marks.append(mark)

    def value_axis(self, scale: Linear, title: str) -> None:
        for tick, label in zip(scale.ticks, scale.labels):
            y = scale(tick)
            self.add(Mark("line", "g6-grid", x=self.x0, y=y, x2=self.x1, y2=y, stroke=GRID))
            self.add(Mark("text", "g6-y-tick", x=self.x0 - 8, y=y, text=label, anchor="end", middle=True, fill=MUTED))
        self.add(Mark("line", "g6-axis", x=self.x0, y=self.y0, x2=self.x0, y2=self.y1, stroke=MUTED))
        if title:
            self.add(Mark("text", "g6-axis-title", x=14, y=(self.y0 + self.y1) / 2, text=title, anchor="middle",
                          middle=True, rotate=-90, fill=INK))

    def number_x_axis(self, scale: Linear, title: str) -> None:
        for tick, label in zip(scale.ticks, scale.labels):
            x = scale(tick)
            self.add(Mark("line", "g6-axis", x=x, y=self.y1, x2=x, y2=self.y1 + 5, stroke=MUTED))
            self.add(Mark("text", "g6-x-tick", x=x, y=self.y1 + 18, text=label, anchor="middle", fill=MUTED))
        self._x_line(title)

    def category_x_axis(self, bands: Bands, title: str, *, rotate: bool) -> None:
        for i, name in enumerate(bands.names):
            x = bands.centre(i)
            self.add(Mark("text", "g6-x-category", x=x, y=self.y1 + 18, text=name,
                          anchor="end" if rotate else "middle", rotate=-40 if rotate else 0, fill=MUTED))
        self._x_line(title)

    def _x_line(self, title: str) -> None:
        self.add(Mark("line", "g6-axis", x=self.x0, y=self.y1, x2=self.x1, y2=self.y1, stroke=MUTED))
        if title:
            self.add(Mark("text", "g6-axis-title", x=(self.x0 + self.x1) / 2, y=self.scene.height - 10, text=title,
                          anchor="middle", fill=INK))


def _crowded(names: list[str], span: float) -> bool:
    return bool(names) and max(_text_width(n) for n in names) + 6 > span / len(names)


def _unique(names: list[str], what: str) -> None:
    seen: set[str] = set()
    for name in names:
        if name in seen:
            raise ChartDataError(f"{what} {name!r} is given more than once; give each one row, or aggregate first")
        seen.add(name)


def _distinct_when_drawn(names: list[str], what: str) -> None:
    """Refuse two names that differ only in white space: a picture draws "a b", "a  b" and "a\\nb" alike."""
    drawn: dict[str, str] = {}
    for name in names:
        first = drawn.setdefault(" ".join(name.split()), name)
        if first != name:
            raise ChartDataError(f"the {what} {first!r} and {name!r} differ only in white space, so they look the same "
                                 "when drawn; make them differ in what is drawn")


def bar(scene: Scene, records: list[dict], x_title: str, y_title: str) -> None:
    names, values = [r["x"] for r in records], [r["y"] for r in records]
    _unique(names, "the category")
    _distinct_when_drawn(names, "categories")
    if len(names) > MAX_BARS:
        raise ChartDataError(f"a bar chart shows at most {MAX_BARS} bars, not {len(names)}")
    probe = linear(values, 1, 0, zero=True)
    rotate = _crowded(names, scene.width - 120)
    frame = _Frame(scene, probe.labels, rotate_x=rotate)
    y = linear(values, frame.y1, frame.y0, zero=True)
    bands = Bands(names, frame.x0, frame.x1)
    frame.value_axis(y, y_title)
    frame.category_x_axis(bands, x_title, rotate=rotate)
    zero = y(0.0)
    unseen = []
    for i, value in enumerate(values):
        top, bottom = min(y(value), zero), max(y(value), zero)
        x, w = bands.start(i), bands.width()
        frame.add(Mark("rect", "g6-bar", x=x, y=top, w=w, h=bottom - top, fill=SERIES,
                       probe=(x + w / 2, (top + bottom) / 2) if w >= 4 and bottom - top >= 4 else None))
        if value != 0 and bottom - top < 0.5:
            unseen.append(f"{names[i]!r} ({shown(value)})")
        if w >= 14:
            frame.add(Mark("text", "g6-value", x=x + w / 2, y=top - 4 if value >= 0 else bottom + 12,
                           text=shown(value), anchor="middle", size=10, fill=INK))
    frame.add(Mark("line", "g6-axis", x=frame.x0, y=zero, x2=frame.x1, y2=zero, stroke=MUTED))
    scene.facts = {"bars": len(values), "value_axis": [y.lo, y.hi]}
    if unseen:
        scene.facts["under_half_a_pixel"] = unseen
    scene.table = ([x_title, y_title], [[n, shown(v)] for n, v in zip(names, values)])


def line(scene: Scene, records: list[dict], x_title: str, y_title: str, *, numeric_x: bool) -> None:
    if numeric_x:
        ordered = sorted(records, key=lambda r: r["x"])
        if [r["row"] for r in ordered] != [r["row"] for r in records]:
            scene.facts["sorted_by_x"] = True
        records = ordered
        xs = [r["x"] for r in records]
        if len(set(xs)) != len(xs):
            raise ChartDataError("an x value is given more than once; a line has one y at each x")
    else:
        if len(records) > MAX_LINE_CATEGORIES:
            raise ChartDataError(f"a line over categories shows at most {MAX_LINE_CATEGORIES} points, not "
                                 f"{len(records)}; give x as numbers, or fewer rows")
        _unique([r["x"] for r in records], "the x category")
        _distinct_when_drawn([r["x"] for r in records], "x categories")
    ys = [r["y"] for r in records]
    names = [] if numeric_x else [r["x"] for r in records]
    rotate = not numeric_x and _crowded(names, scene.width - 120)
    frame = _Frame(scene, linear(ys, 1, 0).labels, rotate_x=rotate)
    y = linear(ys, frame.y1, frame.y0)
    frame.value_axis(y, y_title)
    if numeric_x:
        x_scale = linear([r["x"] for r in records], frame.x0, frame.x1)
        frame.number_x_axis(x_scale, x_title)
        points = [(x_scale(r["x"]), y(r["y"])) for r in records]
    else:
        bands = Bands(names, frame.x0, frame.x1)
        frame.category_x_axis(bands, x_title, rotate=rotate)
        points = [(bands.centre(i), y(r["y"])) for i, r in enumerate(records)]
    frame.add(Mark("polyline", "g6-line", points=points, stroke=SERIES, stroke_width=2))
    if len(points) <= MARKERS:
        for px, py in points:
            frame.add(Mark("circle", "g6-marker", x=px, y=py, r=3.5, fill=SERIES, probe=(px, py)))
    if len(points) <= POINT_LABELS:
        for (px, py), r in zip(points, records):
            frame.add(Mark("text", "g6-value", x=px, y=py - 8, text=shown(r["y"]), anchor="middle", size=10,
                           fill=INK))
    scene.facts |= {"points": len(points), "value_axis": [y.lo, y.hi]}
    scene.table = ([x_title, y_title], [[shown(r["x"]) if numeric_x else r["x"], shown(r["y"])] for r in records])


def scatter(scene: Scene, records: list[dict], x_title: str, y_title: str) -> None:
    ys = [r["y"] for r in records]
    frame = _Frame(scene, linear(ys, 1, 0).labels)
    y = linear(ys, frame.y1, frame.y0)
    x = linear([r["x"] for r in records], frame.x0, frame.x1)
    frame.value_axis(y, y_title)
    frame.number_x_axis(x, x_title)
    for r in records:
        px, py = x(r["x"]), y(r["y"])
        frame.add(Mark("circle", "g6-point", x=px, y=py, r=3.5, fill=SERIES, probe=(px, py)))
    scene.facts = {"points": len(records), "x_axis": [x.lo, x.hi], "value_axis": [y.lo, y.hi]}
    scene.table = ([x_title, y_title], [[shown(r["x"]), shown(r["y"])] for r in records])


def histogram(scene: Scene, records: list[dict], x_title: str, count: int | None) -> None:
    values = [r["x"] for r in records]
    count = count or sturges(len(values))
    edges, counts = bins(values, count)
    frame = _Frame(scene, linear([float(c) for c in counts], 1, 0, zero=True).labels)
    y = linear([float(c) for c in counts], frame.y1, frame.y0, zero=True)
    x = linear([edges[0], edges[-1]], frame.x0, frame.x1)
    frame.value_axis(y, "count")
    frame.number_x_axis(x, x_title)
    zero = y(0.0)
    unseen = []
    for i, c in enumerate(counts):
        left, right, top = x(edges[i]), x(edges[i + 1]), y(float(c))
        if c and zero - top < 0.5:
            unseen.append(f"from {shown(edges[i])} to {shown(edges[i + 1])} ({c} {'value' if c == 1 else 'values'})")
        frame.add(Mark("rect", "g6-bin", x=left, y=top, w=right - left, h=zero - top, fill=SERIES, stroke=BACKGROUND,
                       probe=((left + right) / 2, (top + zero) / 2) if right - left >= 6 and zero - top >= 4 else None))
        if right - left >= 14:
            frame.add(Mark("text", "g6-value", x=(left + right) / 2, y=top - 4, text=str(c), anchor="middle", size=10,
                           fill=INK))
    scene.facts = {"bins": count, "edges": edges, "counts": counts, "rule": "equal width; [a, b), the last [a, b]"}
    if unseen:
        scene.facts["under_half_a_pixel"] = unseen
    scene.table = (["from", "to", "count"], [[shown(edges[i]), shown(edges[i + 1]), str(c)]
                                             for i, c in enumerate(counts)])


def box(scene: Scene, records: list[dict], x_title: str, y_title: str, *, grouped: bool) -> None:
    groups: dict[str, list[float]] = {}
    for r in records:
        groups.setdefault(r["x"] if grouped else y_title, []).append(r["y"])
    if len(groups) > MAX_GROUPS:
        raise ChartDataError(f"a box plot shows at most {MAX_GROUPS} groups, not {len(groups)}")
    _distinct_when_drawn(list(groups), "groups")
    stats = {name: box_stats(values) for name, values in groups.items()}
    every = [v for values in groups.values() for v in values]
    names = list(groups)
    rotate = _crowded(names, scene.width - 120)
    frame = _Frame(scene, linear(every, 1, 0).labels, rotate_x=rotate)
    y = linear(every, frame.y1, frame.y0)
    bands = Bands(names, frame.x0, frame.x1, padding=0.4)
    frame.value_axis(y, y_title)
    frame.category_x_axis(bands, x_title if grouped else "", rotate=rotate)
    for i, name in enumerate(names):
        s, x, w, c = stats[name], bands.start(i), bands.width(), bands.centre(i)
        frame.add(Mark("line", "g6-whisker-high", x=c, y=y(s["q3"]), x2=c, y2=y(s["high"]), stroke=INK))
        frame.add(Mark("line", "g6-whisker-low", x=c, y=y(s["q1"]), x2=c, y2=y(s["low"]), stroke=INK))
        for end in (s["high"], s["low"]):
            frame.add(Mark("line", "g6-cap", x=c - w / 4, y=y(end), x2=c + w / 4, y2=y(end), stroke=INK))
        top, mid, bottom = y(s["q3"]), y(s["median"]), y(s["q1"])
        part = max((mid - top, (top + mid) / 2), (bottom - mid, (mid + bottom) / 2))  # the taller half, off the median
        frame.add(Mark("rect", "g6-box", x=x, y=top, w=w, h=bottom - top, fill=BOX_FILL, stroke=INK,
                       probe=(c, part[1]) if w >= 4 and part[0] >= 4 else None))
        frame.add(Mark("line", "g6-median", x=x, y=y(s["median"]), x2=x + w, y2=y(s["median"]), stroke=INK,
                       stroke_width=2))
        for v in s["outliers"]:
            frame.add(Mark("circle", "g6-outlier", x=c, y=y(v), r=3, fill=INK, probe=(c, y(v))))
    scene.facts = {"groups": {n: {k: v for k, v in s.items()} for n, s in stats.items()},
                   "method": "quartiles by linear interpolation (type 7); whiskers within 1.5 IQR (Tukey)"}
    scene.table = (["group", "n", "low", "q1", "median", "q3", "high", "outliers"],
                   [[n, str(s["n"]), shown(s["low"]), shown(s["q1"]), shown(s["median"]), shown(s["q3"]),
                     shown(s["high"]), " ".join(shown(v) for v in s["outliers"])] for n, s in stats.items()])


def heatmap(scene: Scene, records: list[dict], x_title: str, y_title: str, value_title: str) -> None:
    xs = list(dict.fromkeys(r["x"] for r in records))
    ys = list(dict.fromkeys(r["y"] for r in records))
    if len(xs) > MAX_SIDE or len(ys) > MAX_SIDE:
        raise ChartDataError(f"a heat map is at most {MAX_SIDE} by {MAX_SIDE} cells")
    _distinct_when_drawn(xs, "x categories")
    _distinct_when_drawn(ys, "y categories")
    cells: dict[tuple[str, str], float] = {}
    for r in records:
        key = (r["x"], r["y"])
        if key in cells:
            raise ChartDataError(f"the cell x={key[0]!r}, y={key[1]!r} is given more than once; give each cell one "
                                 "row, or aggregate first")
        cells[key] = r["value"]
    values = list(cells.values())
    lo, hi = min(values), max(values)
    legend = [lo] if lo == hi else [lo + (hi - lo) * k / 4 for k in range(5)]
    widest = max(_text_width(shown(v)) for v in legend)
    rotate = _crowded(xs, scene.width - 160)
    frame = _Frame(scene, ys, legend=int(widest) + 44, rotate_x=rotate)
    bx, by = Bands(xs, frame.x0, frame.x1, padding=0.0), Bands(ys, frame.y0, frame.y1, padding=0.0)
    frame.category_x_axis(bx, x_title, rotate=rotate)
    for j, name in enumerate(ys):
        frame.add(Mark("text", "g6-y-category", x=frame.x0 - 8, y=by.centre(j), text=name, anchor="end", middle=True,
                       fill=MUTED))
    if y_title:
        frame.add(Mark("text", "g6-axis-title", x=14, y=(frame.y0 + frame.y1) / 2, text=y_title, anchor="middle",
                       middle=True, rotate=-90, fill=INK))
    w, h = bx.step, by.step
    for i, xn in enumerate(xs):
        for j, yn in enumerate(ys):
            x, y = bx.start(i), by.start(j)
            if (xn, yn) not in cells:
                frame.add(Mark("rect", "g6-cell-empty", x=x, y=y, w=w, h=h, fill=EMPTY, stroke=BACKGROUND))
                continue
            v = cells[(xn, yn)]
            t = 0.5 if hi == lo else (v - lo) / (hi - lo)
            frame.add(Mark("rect", "g6-cell", x=x, y=y, w=w, h=h, fill=_colour(t), stroke=BACKGROUND,
                           probe=(x + 3, y + 3) if w >= 8 and h >= 8 else None))
            if w >= _text_width(shown(v), 10) + 6 and h >= 14:
                frame.add(Mark("text", "g6-value", x=x + w / 2, y=y + h / 2, text=shown(v), anchor="middle",
                               middle=True, size=10, fill=BACKGROUND if t > 0.55 else INK))
    lx = frame.x1 + 12
    for k, v in enumerate(legend):
        t = 0.5 if hi == lo else (v - lo) / (hi - lo)
        top = frame.y0 + k * 22
        frame.add(Mark("rect", "g6-legend-swatch", x=lx, y=top, w=14, h=14, fill=_colour(t), stroke=MUTED))
        frame.add(Mark("text", "g6-legend-label", x=lx + 20, y=top + 7, text=shown(v), middle=True, size=11,
                       fill=MUTED))
    if value_title:
        frame.add(Mark("text", "g6-legend-title", x=lx, y=frame.y0 - 6, text=value_title, size=11, fill=INK))
    scene.facts = {"cells": len(cells), "cells_empty": len(xs) * len(ys) - len(cells), "x": len(xs), "y": len(ys),
                   "value_range": [lo, hi]}
    scene.table = ([x_title, y_title, value_title], [[a, b, shown(v)] for (a, b), v in cells.items()])


# -- writers -----------------------------------------------------------------------------------------------------------

def _n(v: float) -> str:
    text = f"{v:.2f}"
    return "0.00" if text == "-0.00" else text


def _element(m: Mark) -> str:
    if m.shape == "rect":
        return (f'<rect class="{m.cls}" x="{_n(m.x)}" y="{_n(m.y)}" width="{_n(m.w)}" height="{_n(m.h)}" '
                f'fill="{m.fill}" stroke="{m.stroke}"/>')
    if m.shape == "circle":
        return f'<circle class="{m.cls}" cx="{_n(m.x)}" cy="{_n(m.y)}" r="{_n(m.r)}" fill="{m.fill}"/>'
    if m.shape == "line":
        return (f'<line class="{m.cls}" x1="{_n(m.x)}" y1="{_n(m.y)}" x2="{_n(m.x2)}" y2="{_n(m.y2)}" '
                f'stroke="{m.stroke}" stroke-width="{m.stroke_width:g}"/>')
    if m.shape == "polyline":
        points = " ".join(f"{_n(x)},{_n(y)}" for x, y in m.points)
        return (f'<polyline class="{m.cls}" points="{points}" fill="none" stroke="{m.stroke}" '
                f'stroke-width="{m.stroke_width:g}"/>')
    turn = f' transform="rotate({m.rotate:g} {_n(m.x)} {_n(m.y)})"' if m.rotate else ""
    middle = ' dominant-baseline="middle"' if m.middle else ""
    return (f'<text class="{m.cls}" x="{_n(m.x)}" y="{_n(m.y)}" text-anchor="{m.anchor}"{middle} '
            f'font-size="{m.size}" fill="{m.fill}"{turn}>{escape(m.text, quote=False)}</text>')


def to_svg(scene: Scene) -> str:
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{scene.width}" height="{scene.height}" '
            f'viewBox="0 0 {scene.width} {scene.height}" role="img" font-family="sans-serif">')
    out = [head]
    if scene.title:
        out.append(f"<title>{escape(scene.title, quote=False)}</title>")
    out.append(f'<rect class="g6-background" x="0" y="0" width="{scene.width}" height="{scene.height}" '
               f'fill="{BACKGROUND}"/>')
    out += [_element(m) for m in scene.marks]
    return "\n".join([*out, "</svg>"]) + "\n"


def rgb(colour: str) -> tuple[int, int, int]:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def probes(scene: Scene) -> list[tuple[tuple[float, float], tuple[int, int, int]]]:
    """Where a PNG check looks for the marks, and the colour it looks for: inside each filled mark large enough to hold
    a probe (a bar, a bin, a box, a heat-map cell, a point, a marker, an outlier), and at each point of a line drawn
    without markers (a line with markers is looked for at them)."""
    found = [(m.probe, rgb(m.fill)) for m in scene.marks if m.probe is not None]
    if not any(m.cls == "g6-marker" for m in scene.marks):
        found += [(p, rgb(m.stroke)) for m in scene.marks if m.cls == "g6-line" for p in m.points]
    return found


def to_png(scene: Scene) -> bytes:
    """The scene drawn by Pillow; raises ImportError without it."""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (scene.width, scene.height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    fonts: dict[int, Any] = {}

    def font(size: int) -> Any:
        if size not in fonts:
            try:
                fonts[size] = ImageFont.load_default(size=size)
            except TypeError:  # a Pillow without FreeType has one fixed size
                fonts[size] = ImageFont.load_default()
        return fonts[size]

    for m in scene.marks:
        if m.shape == "rect" and m.w >= 0.5 and m.h >= 0.5:
            draw.rectangle([m.x, m.y, m.x + m.w, m.y + m.h], fill=m.fill,
                           outline=None if m.stroke == "none" else m.stroke)
        elif m.shape == "circle":
            draw.ellipse([m.x - m.r, m.y - m.r, m.x + m.r, m.y + m.r], fill=m.fill)
        elif m.shape == "line":
            draw.line([(m.x, m.y), (m.x2, m.y2)], fill=m.stroke, width=max(1, round(m.stroke_width)))
        elif m.shape == "polyline" and len(m.points) > 1:
            draw.line(m.points, fill=m.stroke, width=max(1, round(m.stroke_width)), joint="curve")
        elif m.shape == "text" and m.text:
            f = font(m.size)
            left, top, right, bottom = draw.textbbox((0, 0), m.text, font=f)
            tw, th = right - left, bottom - top
            if m.rotate:
                layer = Image.new("RGBA", (tw + 4, th + 4), (255, 255, 255, 0))
                ImageDraw.Draw(layer).text((2 - left, 2 - top), m.text, font=f, fill=m.fill)
                turned = layer.rotate(-m.rotate, expand=True)
                at = (m.x - turned.width, m.y) if m.anchor == "end" else (m.x - turned.width / 2,
                                                                          m.y - turned.height / 2)
                image.paste(turned, (round(at[0]), round(at[1])), turned)
                continue
            dx = tw if m.anchor == "end" else tw / 2 if m.anchor == "middle" else 0
            dy = th / 2 if m.middle else th
            draw.text((m.x - dx - left, m.y - dy - top), m.text, font=f, fill=m.fill)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def to_html(scene: Scene, svg: str, said: str) -> str:
    headers, rows = scene.table
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "\n".join("<tr>" + "".join(f"<td>{escape(c)}</td>" for c in row) + "</tr>" for row in rows)
    title = escape(scene.title or "Chart")
    return (f'<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            f'<meta http-equiv="Content-Security-Policy" content="{CSP}">\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">\n<title>{title}</title>\n'
            "<style>body{font-family:sans-serif;margin:16px;color:#111827;background:#ffffff}"
            "table{border-collapse:collapse;margin-top:12px}th,td{border:1px solid #d1d5db;padding:2px 8px}"
            "th{background:#f3f4f6;text-align:left}caption{text-align:left;padding:4px 0}"
            "svg{max-width:100%;height:auto}</style>\n</head>\n<body>\n"
            f"<figure>\n{svg}<figcaption>{escape(said)}</figcaption>\n</figure>\n"
            f"<table>\n<caption>What the chart shows</caption>\n<thead><tr>{head}</tr></thead>\n<tbody>\n{body}\n"
            "</tbody>\n</table>\n</body>\n</html>\n")


__all__ = ["CSP", "HIGH", "LOW", "Bands", "Linear", "Mark", "Scene", "bar", "box", "heatmap", "histogram", "line",
           "linear", "probes", "rgb", "scatter", "to_html", "to_png", "to_svg"]
