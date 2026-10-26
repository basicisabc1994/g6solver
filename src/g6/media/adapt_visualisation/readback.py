# SPDX-License-Identifier: Apache-2.0
"""Read a chart back from the file G6 wrote and compare what it shows with the rows the caller gave.

The reader knows the chart's kind and nothing of how it was drawn. It reads the axes from their tick labels (text at
each tick's position: the ticks must be evenly spaced), turns every mark's geometry back into values through them, and
names categories by the label under each mark. Then it compares with values worked out from the rows here, not by the
drawing code: each bar's category and height (and that it starts at a value axis holding zero), each point of a line
in order, each scatter point, each bin's edges and count (recounted from the rows; a value within a hair of an edge may
fall either side), each box's quartiles, median, whiskers and outliers (from :func:`statistics.quantiles`, a second
implementation of the drawing's rule), and each heat-map cell's value (from its colour, through the legend) with the
cells left empty. Every value label must show its value. A mismatch fails the check with the code
``G6_E_MEDIA_CHART_MISMATCH``.

:func:`loads_nothing` and :func:`page_loads_nothing` check that the SVG or the page references and runs nothing outside
itself. :func:`png_probes` looks for each mark in a PNG by its colour where the layout puts it; with nothing to look
for it is no check at all, so the caller says the picture was not looked into.
"""
from __future__ import annotations

import bisect
import re
import statistics
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from g6.media._types import Check
from g6.media.adapt_visualisation.data import same_number

CHART_MISMATCH = "G6_E_MEDIA_CHART_MISMATCH"
LOADS_EXTERNAL = "G6_E_MEDIA_LOADS_EXTERNAL"
#: Coordinates are written to a hundredth of a pixel; a value read back may be off by this much in pixels.
PIXEL = 0.02
_SVG = "{http://www.w3.org/2000/svg}"
_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


class Mismatch(Exception):
    """What the chart shows is not what the rows say."""


@dataclass
class Element:
    tag: str
    cls: str
    attrs: dict[str, str]
    text: str

    def num(self, key: str) -> float:
        try:
            return float(self.attrs[key])
        except (KeyError, ValueError):
            raise Mismatch(f"a {self.tag} ({self.cls}) has no number for {key}") from None


def elements(svg: str) -> list[Element]:
    root = ET.fromstring(svg)
    return [Element(str(e.tag).removeprefix(_SVG), e.get("class", ""), dict(e.attrib), "".join(e.itertext()))
            for e in root.iter()]


def _of(els: list[Element], cls: str) -> list[Element]:
    return [e for e in els if e.cls == cls]


class Axis:
    """A value axis read from its tick labels: value = v0 + (pixel - p0) * b."""

    def __init__(self, pairs: list[tuple[float, float]], what: str) -> None:
        if len(pairs) < 2:
            raise Mismatch(f"the {what} axis has fewer than two ticks to read it by")
        (self.v0, self.p0), (v1, p1) = pairs[0], pairs[-1]
        if p1 == self.p0:
            raise Mismatch(f"the {what} axis's ticks are all at one place")
        self.b = (v1 - self.v0) / (p1 - self.p0)
        # Each position is written to a hundredth of a pixel, so it is off by up to half of that, and so are the two
        # ticks the axis is read by: a tick may read up to PIXEL pixels' worth of value away from its own, and no more.
        slack = abs(self.b) * PIXEL + 1e-9 * abs(v1 - self.v0)
        for v, p in pairs:
            if abs(self.value(p) - v) > slack:
                raise Mismatch(f"the {what} axis's ticks are not evenly spaced (the tick {v:g} is at {p:g})")
        self.lo, self.hi = min(v for v, _ in pairs), max(v for v, _ in pairs)

    def value(self, pixel: float) -> float:
        return self.v0 + (pixel - self.p0) * self.b

    def pixel(self, value: float) -> float:
        return self.p0 + (value - self.v0) / self.b

    def tol(self) -> float:
        return abs(self.b) * PIXEL * 2


def _axis(els: list[Element], cls: str, coord: str, what: str) -> Axis:
    pairs = []
    for e in _of(els, cls):
        if not _NUMBER.fullmatch(e.text.strip()):
            raise Mismatch(f"the {what} axis has a tick label that is not a number: {e.text[:40]!r}")
        pairs.append((float(e.text), e.num(coord)))
    return Axis(pairs, what)


def _categories(els: list[Element], cls: str, coord: str) -> list[tuple[str, float]]:
    return [(e.text, e.num(coord)) for e in _of(els, cls)]


class _Names:
    """Category labels by their place along an axis, so the one under a mark is found without reading every label."""

    def __init__(self, names: list[tuple[str, float]]) -> None:
        self.names = sorted(names, key=lambda n: n[1])
        self.at = [p for _, p in self.names]

    def nearest(self, pixel: float, within: float, what: str) -> str:
        k = bisect.bisect_left(self.at, pixel)
        best = min((self.names[i] for i in (k - 1, k) if 0 <= i < len(self.names)), key=lambda n: abs(n[1] - pixel),
                   default=None)
        if best is None or abs(best[1] - pixel) > within:
            raise Mismatch(f"{what} at {pixel:.2f} has no category label under it")
        return best[0]


def _close(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol + 1e-9 * max(abs(a), abs(b))


def _differ(found: dict, want: dict, tol: float, what: str) -> None:
    missing = [k for k in want if k not in found]
    extra = [k for k in found if k not in want]
    if missing or extra:
        raise Mismatch(f"{what}: " + "; ".join(
            ([f"given but not drawn: {', '.join(map(repr, missing[:5]))}"] if missing else [])
            + ([f"drawn but not given: {', '.join(map(repr, extra[:5]))}"] if extra else [])))
    wrong = [k for k in want if not _close(found[k], want[k], tol)]
    if wrong:
        k = wrong[0]
        raise Mismatch(f"{what}: {len(wrong)} drawn at the wrong value, for example {k!r}: drawn {found[k]:g}, "
                       f"given {want[k]:g}")


def _value_labels(els: list[Element], places: list[tuple[float, float]], want: list[float], within: float) -> int:
    """Each value label (``g6-value``) shows the value of the mark it sits over."""
    labels = _of(els, "g6-value")
    for e in labels:
        x = e.num("x")
        k = min(range(len(places)), key=lambda i: abs(places[i][0] - x), default=None)
        if k is None or abs(places[k][0] - x) > within:
            raise Mismatch(f"the value label {e.text!r} is over no mark")
        if not same_number(e.text, want[k]):
            raise Mismatch(f"a value label says {e.text!r} where the value given is {want[k]:g}")
    return len(labels)


def read_bar(els: list[Element], want: list[tuple[str, float]]) -> dict[str, Any]:
    y = _axis(els, "g6-y-tick", "y", "value")
    if not y.lo <= 0 <= y.hi:
        raise Mismatch(f"the value axis runs from {y.lo:g} to {y.hi:g}, without zero, so the bars' lengths mislead")
    cats, zero, found = _Names(_categories(els, "g6-x-category", "x")), y.pixel(0.0), {}
    centres = []
    for e in _of(els, "g6-bar"):
        x, w, top = e.num("x"), e.num("width"), e.num("y")
        bottom = top + e.num("height")
        name = cats.nearest(x + w / 2, w / 2 + 1, "a bar")
        if abs(bottom - zero) <= PIXEL * 2:
            value = y.value(top)
        elif abs(top - zero) <= PIXEL * 2:
            value = y.value(bottom)
        else:
            raise Mismatch(f"the bar for {name!r} does not start at zero")
        if name in found:
            raise Mismatch(f"two bars are drawn for {name!r}")
        found[name] = value
        centres.append((x + w / 2, name))
    given = dict(want)
    _differ(found, given, y.tol(), "the bars")
    labels = _value_labels(els, [(c, 0.0) for c, _ in centres], [given[n] for _, n in centres], 1.0)
    return {"bars_read": len(found), "value_axis": [y.lo, y.hi], "value_labels_read": labels}


def _polyline(els: list[Element]) -> list[tuple[float, float]]:
    lines = _of(els, "g6-line")
    if len(lines) != 1:
        raise Mismatch(f"{len(lines)} lines are drawn, not one")
    try:
        return [(float(a), float(b)) for a, b in (p.split(",") for p in lines[0].attrs.get("points", "").split())]
    except ValueError:
        raise Mismatch("the line's points do not read as numbers") from None


def read_line(els: list[Element], want: list[tuple[Any, float]], *, numeric_x: bool) -> dict[str, Any]:
    y = _axis(els, "g6-y-tick", "y", "value")
    points = _polyline(els)
    if len(points) != len(want):
        raise Mismatch(f"the line has {len(points)} points; {len(want)} rows were plotted")
    if numeric_x:
        x = _axis(els, "g6-x-tick", "x", "x")
        drawn = [(x.value(px), y.value(py)) for px, py in points]
        bad = [k for k, ((dx, dy), (wx, wy)) in enumerate(zip(drawn, want))
               if not (_close(dx, wx, x.tol()) and _close(dy, wy, y.tol()))]
    else:
        cats = _categories(els, "g6-x-category", "x")
        step = abs(cats[1][1] - cats[0][1]) if len(cats) > 1 else 1e9
        names = _Names(cats)
        drawn = [(names.nearest(px, step / 2, "a point"), y.value(py)) for px, py in points]
        bad = [k for k, ((dx, dy), (wx, wy)) in enumerate(zip(drawn, want)) if dx != wx or not _close(dy, wy, y.tol())]
    if bad:
        k = bad[0]
        raise Mismatch(f"{len(bad)} points of the line are not where the rows put them, for example point {k + 1}: "
                       f"drawn {drawn[k]}, given {want[k]}")
    markers = [(e.num("cx"), e.num("cy")) for e in _of(els, "g6-marker")]
    if markers and any(not (_close(a, c, PIXEL) and _close(b, d, PIXEL)) for (a, b), (c, d) in zip(markers, points)):
        raise Mismatch("the point markers are not on the line's points")
    labels = _value_labels(els, points, [w for _, w in want], 1.0)
    return {"points_read": len(points), "value_axis": [y.lo, y.hi], "value_labels_read": labels}


def _match(expected: list[tuple[float, float]], drawn: list[tuple[float, float]], tol: float) -> int:
    """How many expected points have no drawn point within ``tol`` pixels (each drawn point used once)."""
    buckets: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
    for p in drawn:
        buckets[(int(p[0] // 1), int(p[1] // 1))].append(p)
    unmatched = 0
    for ex, ey in expected:
        bx, by = int(ex // 1), int(ey // 1)
        hit = None
        for key in ((bx + i, by + j) for i in (-1, 0, 1) for j in (-1, 0, 1)):
            for k, (dx, dy) in enumerate(buckets.get(key, ())):
                if abs(dx - ex) <= tol and abs(dy - ey) <= tol:
                    hit = (key, k)
                    break
            if hit:
                break
        if hit is None:
            unmatched += 1
        else:
            buckets[hit[0]].pop(hit[1])
    return unmatched


def read_scatter(els: list[Element], want: list[tuple[float, float]]) -> dict[str, Any]:
    y, x = _axis(els, "g6-y-tick", "y", "value"), _axis(els, "g6-x-tick", "x", "x")
    drawn = [(e.num("cx"), e.num("cy")) for e in _of(els, "g6-point")]
    if len(drawn) != len(want):
        raise Mismatch(f"{len(drawn)} points are drawn; {len(want)} rows were plotted")
    unmatched = _match([(x.pixel(a), y.pixel(b)) for a, b in want], drawn, PIXEL * 2)
    if unmatched:
        raise Mismatch(f"{unmatched} of the rows have no point drawn where their values put them")
    return {"points_read": len(drawn), "x_axis": [x.lo, x.hi], "value_axis": [y.lo, y.hi]}


def read_histogram(els: list[Element], values: list[float]) -> dict[str, Any]:
    y, x = _axis(els, "g6-y-tick", "y", "count"), _axis(els, "g6-x-tick", "x", "x")
    zero = y.pixel(0.0)
    bars = sorted(_of(els, "g6-bin"), key=lambda e: e.num("x"))
    if not bars:
        raise Mismatch("no bins are drawn")
    edges, counts = [], []
    for e in bars:
        top, bottom = e.num("y"), e.num("y") + e.num("height")
        if abs(bottom - zero) > PIXEL * 2:
            raise Mismatch("a bin does not start at zero")
        count = y.value(top)
        if abs(count - round(count)) > y.tol():
            raise Mismatch(f"a bin's height reads as {count:g}, not a whole count")
        edges.append((x.value(e.num("x")), x.value(e.num("x") + e.num("width"))))
        counts.append(round(count))
    tol = x.tol()
    if any(abs(edges[i][1] - edges[i + 1][0]) > tol for i in range(len(edges) - 1)):
        raise Mismatch("the bins are not side by side")
    if sum(counts) != len(values):
        raise Mismatch(f"the bins hold {sum(counts)} values; {len(values)} rows were plotted")
    outside = [v for v in values if not edges[0][0] - tol <= v <= edges[-1][1] + tol]
    if outside:
        raise Mismatch(f"{len(outside)} values fall outside every bin, for example {outside[0]:g}")
    for i, ((a, b), c) in enumerate(zip(edges, counts)):
        last = i == len(edges) - 1
        sure = sum(1 for v in values if a + tol < v < b - tol)
        maybe = sum(1 for v in values if a - tol <= v <= b + tol and (last or v < b + tol))
        if not sure <= c <= maybe:
            raise Mismatch(f"the bin from {a:g} to {b:g} shows {c}; the rows put {sure} to {maybe} values in it")
    labels = _value_labels(els, [((x.pixel(a) + x.pixel(b)) / 2, 0.0) for a, b in edges],
                           [float(c) for c in counts], 2.0)
    return {"bins_read": len(counts), "counts": counts, "value_labels_read": labels}


def _expected_box(values: list[float]) -> dict[str, Any]:
    s = sorted(values)
    q1, median, q3 = statistics.quantiles(s, n=4, method="inclusive") if len(s) > 1 else (s[0], s[0], s[0])
    iqr = q3 - q1
    inside = [v for v in s if q1 - 1.5 * iqr <= v <= q3 + 1.5 * iqr]
    return {"q1": q1, "median": median, "q3": q3, "low": inside[0], "high": inside[-1],
            "outliers": [v for v in s if v < inside[0] or v > inside[-1]]}


def read_box(els: list[Element], groups: dict[str, list[float]]) -> dict[str, Any]:
    y = _axis(els, "g6-y-tick", "y", "value")
    cats = _categories(els, "g6-x-category", "x")
    step = abs(cats[1][1] - cats[0][1]) if len(cats) > 1 else 1e9
    names = _Names(cats)
    found: dict[str, dict[str, Any]] = defaultdict(lambda: {"outliers": []})
    for e in _of(els, "g6-box"):
        name = names.nearest(e.num("x") + e.num("width") / 2, step / 2, "a box")
        found[name] |= {"q3": y.value(e.num("y")), "q1": y.value(e.num("y") + e.num("height"))}
    for e in _of(els, "g6-median"):
        found[names.nearest((e.num("x1") + e.num("x2")) / 2, step / 2, "a median")]["median"] = y.value(e.num("y1"))
    for end, edge in (("high", "q3"), ("low", "q1")):  # each whisker is marked by its end, so one of no length is read
        for e in _of(els, f"g6-whisker-{end}"):
            name = names.nearest(e.num("x1"), step / 2, "a whisker")
            if end in found[name]:
                raise Mismatch(f"two {end} whiskers are drawn for {name!r}")
            found[name] |= {end: y.value(e.num("y2")), f"{end}_from": y.value(e.num("y1")), f"{end}_edge": edge}
    for e in _of(els, "g6-outlier"):
        found[names.nearest(e.num("cx"), step / 2, "an outlier")]["outliers"].append(y.value(e.num("cy")))
    if set(found) != set(groups):
        raise Mismatch(f"boxes are drawn for {sorted(found)}; the rows hold {sorted(groups)}")
    for name, values in groups.items():
        want, got = _expected_box(values), found[name]
        for key in ("q1", "median", "q3", "low", "high"):
            if key not in got or not _close(got[key], want[key], y.tol()):
                raise Mismatch(f"the {key} of {name!r} is drawn at {got.get(key, float('nan')):g}, not "
                               f"{want[key]:g}")
        for end in ("high", "low"):
            if not _close(got[f"{end}_from"], want[got[f"{end}_edge"]], y.tol()):
                raise Mismatch(f"the {end} whisker of {name!r} does not start at the box")
        drawn_out, want_out = sorted(got["outliers"]), want["outliers"]
        if len(drawn_out) != len(want_out) or any(not _close(a, b, y.tol()) for a, b in zip(drawn_out, want_out)):
            raise Mismatch(f"{name!r} shows {len(drawn_out)} outliers; the rows have {len(want_out)}")
    return {"boxes_read": len(found), "value_axis": [y.lo, y.hi]}


def _rgb(colour: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", colour):
        raise Mismatch(f"the colour {colour!r} is not one the reader knows")
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def read_heatmap(els: list[Element], cells: dict[tuple[str, str], float]) -> dict[str, Any]:
    xs, ys = _categories(els, "g6-x-category", "x"), _categories(els, "g6-y-category", "y")
    swatches = _of(els, "g6-legend-swatch")
    labels = _of(els, "g6-legend-label")
    legend = []
    for s in swatches:
        mid = s.num("y") + s.num("height") / 2
        label = min(labels, key=lambda e: abs(e.num("y") - mid), default=None)
        if label is None or abs(label.num("y") - mid) > 2 or not _NUMBER.fullmatch(label.text.strip()):
            raise Mismatch("a legend colour has no number beside it")
        legend.append((float(label.text), _rgb(s.attrs.get("fill", ""))))
    if not legend:
        raise Mismatch("the heat map has no legend to read its colours by")
    legend.sort()
    (v_lo, c_lo), (v_hi, c_hi) = legend[0], legend[-1]
    channel = max(range(3), key=lambda k: abs(c_hi[k] - c_lo[k]))
    span = c_hi[channel] - c_lo[channel]

    def value_of(colour: tuple[int, int, int]) -> float:
        if v_hi == v_lo or span == 0:
            if colour != c_lo:
                raise Mismatch("a cell's colour is not the legend's only colour")
            return v_lo
        return v_lo + (colour[channel] - c_lo[channel]) / span * (v_hi - v_lo)

    sx = abs(xs[1][1] - xs[0][1]) if len(xs) > 1 else 1e9
    sy = abs(ys[1][1] - ys[0][1]) if len(ys) > 1 else 1e9
    x_names, y_names = _Names(xs), _Names(ys)
    found, empty, centres = {}, set(), {}
    for e in [*_of(els, "g6-cell"), *_of(els, "g6-cell-empty")]:
        cx, cy = e.num("x") + e.num("width") / 2, e.num("y") + e.num("height") / 2
        key = (x_names.nearest(cx, sx / 2 + 1, "a cell"), y_names.nearest(cy, sy / 2 + 1, "a cell"))
        if key in found or key in empty:
            raise Mismatch(f"two cells are drawn for x={key[0]!r}, y={key[1]!r}")
        if e.cls == "g6-cell-empty":
            empty.add(key)
            continue
        found[key] = value_of(_rgb(e.attrs.get("fill", "")))
        centres[key] = (cx, cy)
    _differ(found, cells, abs(v_hi - v_lo) * 0.01, "the cells")
    for e in _of(els, "g6-value"):  # the cell under a label is found by its two categories, not by reading every cell
        tx, ty = e.num("x"), e.num("y")
        try:
            key = (x_names.nearest(tx, sx / 2 + 1, "a value label"), y_names.nearest(ty, sy / 2 + 1, "a value label"))
        except Mismatch:
            key = None
        centre = centres.get(key)
        if centre is None or abs(centre[0] - tx) > 1 or abs(centre[1] - ty) > 1:
            raise Mismatch(f"the value label {e.text!r} is over no cell")
        if not same_number(e.text, cells[key]):
            raise Mismatch(f"the cell x={key[0]!r}, y={key[1]!r} says {e.text!r}; its value is {cells[key]:g}")
    every = {(a, b) for a, _ in xs for b, _ in ys}
    if empty != every - set(cells):
        raise Mismatch("the cells drawn empty are not the ones with no row")
    return {"cells_read": len(found), "cells_empty": len(empty), "legend": [v for v, _ in legend]}


READERS = {"bar": read_bar, "scatter": read_scatter, "histogram": read_histogram, "box": read_box,
           "heatmap": read_heatmap}


def chart_check(kind: str, svg: str, expected: Any, **options: Any) -> tuple[Check, dict[str, Any]]:
    """Whether the chart in ``svg`` shows ``expected`` (see the module docstring)."""
    try:
        els = elements(svg)
        measured = read_line(els, expected, **options) if kind == "line" else READERS[kind](els, expected)
    except ET.ParseError as exc:
        return Check(name="shows_the_data", passed=False, code=CHART_MISMATCH, detail=f"not XML: {exc}"[:500]), {}
    except Mismatch as exc:
        return Check(name="shows_the_data", passed=False, code=CHART_MISMATCH, detail=str(exc)[:500]), {}
    return Check(name="shows_the_data", passed=True,
                 detail=f"every value drawn is the value given ({kind})"), measured


_ELSEWHERE = re.compile(r"(?i)@import|url\s*\(\s*['\"]?\s*(?!#)")
_SVG_LOADING = frozenset({"script", "foreignobject", "image", "iframe", "object", "embed", "audio", "video", "link",
                          "use"})


def loads_nothing(svg: str) -> Check:
    """Whether the SVG references or runs nothing outside itself (no loading element, no ``href``, ``src`` or ``on…``
    attribute, no ``url()`` but to a fragment of itself, no ``@import``, no style sheet instruction)."""
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        return Check(name="loads_nothing", passed=False, code=CHART_MISMATCH, detail=f"not XML: {exc}"[:500])
    hits = ["<?xml-stylesheet?>"] if "<?xml-stylesheet" in svg.lower() else []
    for element in root.iter():
        tag = str(element.tag).rpartition("}")[2].lower()
        if tag in _SVG_LOADING:
            hits.append(f"<{tag}>")
        for key, value in element.attrib.items():
            local = key.rpartition("}")[2].lower()
            if local in ("href", "src") or local.startswith("on") or _ELSEWHERE.search(value):
                hits.append(f"{local}=")
        if tag == "style" and _ELSEWHERE.search(element.text or ""):
            hits.append("<style> with an import or url()")
    if hits:
        return Check(name="loads_nothing", passed=False, code=LOADS_EXTERNAL,
                     detail=f"the file references or runs something outside itself: {', '.join(sorted(set(hits)))}")
    return Check(name="loads_nothing", passed=True,
                 detail="no script, link, image, event handler, import or outside url() in the file")


_PAGE_LOADING = frozenset({"script", "link", "iframe", "frame", "frameset", "object", "embed", "img", "image",
                           "picture", "source", "track", "video", "audio", "base", "form", "use", "foreignobject",
                           "portal", "applet", "noscript", "template", "svg:image"})
_PAGE_ATTRS = frozenset({"src", "href", "srcset", "action", "formaction", "data", "poster", "background",
                         "xlink:href", "ping", "manifest", "codebase"})


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hits: list[str] = []
        self.policy = ""
        self._style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _PAGE_LOADING:
            self.hits.append(f"<{tag}>")
        found = {k.lower(): v or "" for k, v in attrs}
        for key, value in found.items():
            if key in _PAGE_ATTRS or key.startswith("on") or _ELSEWHERE.search(value):
                self.hits.append(f"{key}=")
        if tag == "meta" and found.get("http-equiv", "").lower() == "refresh":
            self.hits.append("<meta refresh>")
        if tag == "meta" and found.get("http-equiv", "").lower() == "content-security-policy":
            self.policy = found.get("content", "")
        self._style = tag == "style"

    handle_startendtag = handle_starttag

    def handle_endtag(self, tag: str) -> None:
        self._style = False

    def handle_data(self, data: str) -> None:
        if self._style and _ELSEWHERE.search(data):
            self.hits.append("<style> with an import or url()")

    def handle_pi(self, data: str) -> None:
        self.hits.append(f"<?{data[:20]}>")


def page_loads_nothing(page: str) -> Check:
    """Whether the page references, loads or runs nothing outside itself, and says so to the browser (a content
    security policy of ``default-src 'none'``)."""
    parser = _Page()
    parser.feed(page)
    parser.close()
    if parser.hits:
        return Check(name="loads_nothing", passed=False, code=LOADS_EXTERNAL,
                     detail=f"the page references, loads or runs something: {', '.join(sorted(set(parser.hits)))}")
    if "default-src 'none'" not in parser.policy:
        return Check(name="loads_nothing", passed=False, code=LOADS_EXTERNAL,
                     detail="the page carries no content security policy that forbids loading anything")
    return Check(name="loads_nothing", passed=True, detail="no script, link, image, frame, form, event handler, import "
                                                           "or url(); a content security policy forbids loading any")


def png_probes(path: Path, probes: list[tuple[tuple[float, float], tuple[int, int, int]]]) -> Check:
    """Whether the PNG has each mark's colour (within 48 per channel) at the pixel the layout puts it or one beside it
    (a line's pixels fall either side of its exact points). A check of no probes would pass on any picture, so there is
    none: :class:`ValueError`."""
    from PIL import Image

    if not probes:
        raise ValueError("no mark to look for: a check of nothing is not a pass")
    with Image.open(path, formats=("PNG",)) as image:
        pixels = image.convert("RGB")
        read, w, h = pixels.load(), pixels.width, pixels.height
        misses = []
        for (x, y), want in probes:
            px, py = min(max(int(x), 0), w - 1), min(max(int(y), 0), h - 1)
            near = [read[i, j] for i in range(max(px - 1, 0), min(px + 2, w)) for j in range(max(py - 1, 0),
                                                                                             min(py + 2, h))]
            if not any(all(abs(a - b) <= 48 for a, b in zip(got, want)) for got in near):
                misses.append((px, py, read[px, py], want))
    if misses:
        x, y, got, want = misses[0]
        return Check(name="marks_where_drawn", passed=False, code=CHART_MISMATCH,
                     detail=f"{len(misses)} of {len(probes)} marks are not in the picture where the layout puts them, "
                            f"for example at {x},{y}: {got}, not {want}")
    return Check(name="marks_where_drawn", passed=True,
                 detail=f"each of {len(probes)} marks has its colour in the picture where the layout puts it")


__all__ = ["CHART_MISMATCH", "LOADS_EXTERNAL", "PIXEL", "Axis", "Mismatch", "chart_check", "elements",
           "loads_nothing", "page_loads_nothing", "png_probes", "read_bar", "read_box", "read_heatmap",
           "read_histogram", "read_line", "read_scatter"]
