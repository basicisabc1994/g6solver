# SPDX-License-Identifier: Apache-2.0
"""adapt_visualisation: a chart never hides a row (D1, ADAPT_VISUALISATION-01, -02): one it cannot plot is refused
unless the caller asks to drop it, and then the drop is reported; every value the written file shows is read back
through its drawn axes and compared with the rows, so a misdrawn chart is refused and removed (D1); nothing is
``verified`` (ADAPT_VISUALISATION-03); no file it writes loads or runs anything; a caller's claims are refused; errors
tighten; it is reachable over MCP; without Pillow a PNG says it is not installed."""
from __future__ import annotations

import ast
import importlib.metadata
import json
import os
import re
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

import pytest

import g6.media.adapt_visualisation as package
from g6.core import discovery
from g6.core.failure_modes import FailureModeRegistry
from g6.core.registry import get_registry
from g6.invariants import check_envelope
from g6.mcp import describe_reply, invoke_reply
from g6.media._inspect import pillow
from g6.media._types import BACKEND_FAILED, EXTRA_MISSING, INPUT_INVALID, media_root
from g6.media.adapt_visualisation import AdaptVisualisationBlock, block, data, draw, readback

#: Only the PNG tests need Pillow (the image extra); SVG, HTML and the no-Pillow refusal run without it.
needs_pillow = pytest.mark.skipif(pillow() is None, reason="Pillow (the image extra) is not installed: PNG is not drawn")
PNG = pytest.param("png", marks=needs_pillow)
FORMATS = ["svg", PNG, "html"]
SRC = Path(package.__file__).parent
SVG = "{http://www.w3.org/2000/svg}"
SALES = [{"month": "Jan", "sales": 120, "cost": 80, "region": "north"},
         {"month": "Feb", "sales": 95.5, "cost": 70, "region": "north"},
         {"month": "Mar", "sales": 143, "cost": 90, "region": "south"},
         {"month": "Apr", "sales": 160, "cost": 101, "region": "south"},
         {"month": "May", "sales": 152, "cost": 99, "region": "east"},
         {"month": "Jun", "sales": -12.25, "cost": 120, "region": "east"}]
CHARTS = [("bar", {"x": "month", "y": "sales"}), ("line", {"x": "month", "y": "sales"}),
          ("line", {"x": "cost", "y": "sales"}), ("scatter", {"x": "cost", "y": "sales"}),
          ("histogram", {"x": "sales", "bins": 4}), ("box", {"y": "sales", "x": "region"}), ("box", {"y": "sales"}),
          ("heatmap", {"x": "month", "y": "region", "value": "sales"})]


def _invoke(op: str, **params):
    result = get_registry().invoke("adapt_visualisation", op, params)
    assert result.__class__.__name__ == "Success", result
    return result.unwrap()


def _refused(op: str, **params) -> str:
    result = get_registry().invoke("adapt_visualisation", op, params)
    assert result.__class__.__name__ == "Failure", result
    return str(result.failure())


def _media_files() -> set[str]:
    return {p.name for p in media_root().iterdir()} if media_root().exists() else set()


def _texts(path: Path, cls: str) -> list[str]:
    root = ET.fromstring(path.read_text(encoding="utf-8"))
    return [e.text or "" for e in root.iter(f"{SVG}text") if e.get("class") == cls]


# -- D1 and ADAPT_VISUALISATION-01/-02: a row the chart cannot plot is never dropped silently ---------------------------

BROKEN = [*SALES, {"month": "Jul", "sales": None}, {"month": "Aug", "sales": "n/a"},
          {"month": "Sep", "sales": True}, {"month": "Oct"}, {"month": "Nov", "sales": "1e400"}]


@pytest.mark.parametrize("op", ["bar", "line"])
def test_d1_rows_that_cannot_be_plotted_are_refused_with_each_one_named(op: str) -> None:
    before = _media_files()
    out = _invoke(op, rows=BROKEN, x="month", y="sales", name=f"d1-drop-{op}")
    assert out["completion_state"] == "blocked-escalated" and out["code"] == block.DATA_DROPPED
    cov = out["coverage"]
    assert (cov["rows_given"], cov["rows_plotted"], cov["dropped_count"]) == (11, 6, 5)
    assert [(d["row"], d["reason"]) for d in cov["dropped"]] == [
        (6, "missing"), (7, "not a number"), (8, "not a number"), (9, "missing"), (10, "not finite")]
    assert "6 of 11 rows plotted" in out["reason"] and _media_files() == before


def test_d1_asked_to_drop_them_it_draws_the_rest_and_says_what_it_dropped() -> None:
    out = _invoke("bar", rows=BROKEN, x="month", y="sales", drop_unplottable=True, name="d1-dropped")
    assert out["completion_state"] == "qualified-draft", out.get("reason")
    assert "6 of 11 rows plotted; 5 dropped (missing, not a number, not finite)" in out["warning_card"]["message"]
    assert out["evidence"][1] == {"kind": "chart_coverage", **out["coverage"]}
    assert _texts(Path(out["artifact"]["path"]), "g6-x-category") == ["Jan", "Feb", "Mar", "Apr", "May", "Jun"]


def _misdraw(monkeypatch: pytest.MonkeyPatch, chart: str, change) -> None:
    real = getattr(draw, chart)

    def wrong(scene, *args, **kwargs):
        real(scene, *args, **kwargs)
        change(scene)

    monkeypatch.setattr(draw, chart, wrong)


def _first(scene, cls):
    return next(m for m in scene.marks if m.cls == cls)


def _taller(bar) -> None:  # a fifth taller, still standing on zero
    bar.y, bar.h = bar.y - bar.h * 0.2, bar.h * 1.2


MISDRAWN = [
    ("bar", {"x": "month", "y": "sales"}, "bar", lambda s: _taller(_first(s, "g6-bar")), "the bars"),
    ("bar", {"x": "month", "y": "sales"}, "bar", lambda s: s.marks.remove(_first(s, "g6-bar")), "given but not drawn"),
    ("bar", {"x": "month", "y": "sales"}, "bar", lambda s: setattr(_first(s, "g6-x-category"), "text", "Dec"),
     "drawn but not given"),
    ("bar", {"x": "month", "y": "sales"}, "bar", lambda s: setattr(_first(s, "g6-value"), "text", "12"),
     "value label"),
    ("line", {"x": "cost", "y": "sales"}, "line", lambda s: _first(s, "g6-line").points.reverse(), "points"),
    ("scatter", {"x": "cost", "y": "sales"}, "scatter", lambda s: setattr(_first(s, "g6-point"), "y",
                                                                           _first(s, "g6-point").y + 9), "no point"),
    ("histogram", {"x": "sales", "bins": 4}, "histogram",
     lambda s: setattr(_first(s, "g6-bin"), "y", _first(s, "g6-bin").y - 30), "bin"),
    ("box", {"y": "sales", "x": "region"}, "box", lambda s: setattr(_first(s, "g6-median"), "y",
                                                                     _first(s, "g6-median").y + 6), "median"),
    ("heatmap", {"x": "month", "y": "region", "value": "sales"}, "heatmap",
     lambda s: setattr(_first(s, "g6-cell"), "fill", draw._colour(0.0)), "the cells"),
    ("heatmap", {"x": "month", "y": "region", "value": "sales"}, "heatmap",
     lambda s: setattr(_first(s, "g6-cell"), "cls", "g6-cell-empty"), "the cells"),
]


@pytest.mark.parametrize(("op", "params", "chart", "change", "said"), MISDRAWN)
@pytest.mark.parametrize("fmt", FORMATS)
def test_d1_a_chart_that_does_not_show_the_rows_is_refused_and_removed(monkeypatch: pytest.MonkeyPatch, op, params,
                                                                       chart, change, said, fmt) -> None:
    _misdraw(monkeypatch, chart, change)
    before = _media_files()
    out = _invoke(op, rows=SALES, format=fmt, name=f"d1-misdrawn-{op}-{fmt}", **params)
    assert out["completion_state"] == "blocked-escalated" and out["code"] == readback.CHART_MISMATCH, out.get("reason")
    assert said in out["reason"], out["reason"]
    assert out["artifact"] is None and _media_files() == before


@pytest.mark.parametrize("fmt", FORMATS)
def test_a_refused_overwrite_leaves_the_file_it_would_have_replaced(monkeypatch: pytest.MonkeyPatch, fmt: str) -> None:
    first = _invoke("bar", rows=SALES, x="month", y="sales", format=fmt, name=f"kept-{fmt}", overwrite=True)
    assert first["completion_state"] == "qualified-draft", first.get("reason")
    path = Path(first["artifact"]["path"])
    before, files = path.read_bytes(), _media_files()
    _misdraw(monkeypatch, "bar", lambda s: _taller(_first(s, "g6-bar")))
    out = _invoke("bar", rows=SALES, x="month", y="sales", format=fmt, name=f"kept-{fmt}", overwrite=True)
    assert out["code"] == readback.CHART_MISMATCH and out["artifact"] is None, out.get("reason")
    assert path.read_bytes() == before and _media_files() == files
    monkeypatch.undo()
    again = _invoke("bar", rows=SALES, x="month", y="sales", format=fmt, name=f"kept-{fmt}", overwrite=True, width=700)
    assert again["written"] == "replaced" and path.read_bytes() != before and _media_files() == files


@needs_pillow
def test_d1_a_png_whose_marks_are_not_where_the_layout_puts_them_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    import copy

    real = draw.to_png

    def vanished(scene):  # the bars are laid out, but the picture does not show them
        drawn = copy.deepcopy(scene)
        for m in drawn.marks:
            if m.cls == "g6-bar":
                m.fill = draw.BACKGROUND
        return real(drawn)

    monkeypatch.setattr(draw, "to_png", vanished)
    before = _media_files()
    out = _invoke("bar", rows=SALES, x="month", y="sales", format="png", name="d1-shifted")
    assert out["code"] == readback.CHART_MISMATCH and "not in the picture" in out["reason"], out.get("reason")
    assert _media_files() == before


def _picture_checked(out: dict) -> bool:
    """Whether a PNG chart is reported as checked in its pixels, beyond decoding, its size and not being blank."""
    if out["completion_state"] != "qualified-draft":
        return False
    return any(c["name"] == "marks_where_drawn" and c["passed"] for c in out["artifact"]["checks"])


LONG_LINE = [{"t": i, "v": (i * 7) % 11} for i in range(201)]  # more points than get a marker


@needs_pillow
@pytest.mark.parametrize(("op", "rows", "params", "left_out"), [
    ("line", LONG_LINE, {"x": "t", "y": "v"}, "g6-line"),
    ("box", SALES, {"y": "sales", "x": "region"}, "g6-box"),
    ("box", SALES, {"y": "sales"}, "g6-box"),
])
def test_a_png_drawn_without_its_line_or_boxes_is_never_reported_as_checked(monkeypatch: pytest.MonkeyPatch, op: str,
                                                                            rows: list, params: dict,
                                                                            left_out: str) -> None:
    import copy

    real = draw.to_png

    def without(scene):  # laid out in full, but the picture leaves the line or the boxes out
        drawn = copy.deepcopy(scene)
        drawn.marks = [m for m in drawn.marks if m.cls != left_out]
        return real(drawn)

    monkeypatch.setattr(draw, "to_png", without)
    before = _media_files()
    out = _invoke(op, rows=rows, format="png", name=f"png-without-{left_out}-{len(params)}", **params)
    assert not _picture_checked(out), out["artifact"]["checks"]
    assert out["code"] == readback.CHART_MISMATCH and "not in the picture" in out["reason"], out.get("reason")
    assert _media_files() == before


@needs_pillow
def test_each_format_says_what_was_checked_and_a_png_with_nothing_to_look_for_says_so() -> None:
    svg = _invoke("bar", rows=SALES, x="month", y="sales", name="said-svg", overwrite=True)
    assert "read back from the file" in svg["warning_card"]["message"]
    png = _invoke("line", rows=LONG_LINE, x="t", y="v", format="png", name="said-png", overwrite=True)
    assert _picture_checked(png) and "read back from the layout" in png["warning_card"]["message"], png.get("reason")
    assert "read back from the file" not in png["warning_card"]["message"]
    cells = [{"x": f"x{i}", "y": f"y{j}", "v": i * j} for i in range(20) for j in range(50)]  # cells 6 pixels high
    heat = _invoke("heatmap", rows=cells, x="x", y="y", value="v", format="png", name="said-heat", overwrite=True)
    assert heat["completion_state"] == "qualified-draft" and not _picture_checked(heat), heat.get("reason")
    assert "checked only for decoding, its size and not being blank" in heat["warning_card"]["message"]


@pytest.mark.parametrize(("op", "rows", "params"), [
    ("bar", [{"k": "a", "v": 1000}, {"k": "b", "v": 0.01}, {"k": "c", "v": 0}], {"x": "k", "y": "v"}),
    ("histogram", [{"v": 0}] + [{"v": 1}] * 5000, {"x": "v", "bins": 2}),
])
@pytest.mark.parametrize("fmt", ["svg", PNG])
def test_a_value_too_small_to_see_is_reported(op: str, rows: list, params: dict, fmt: str) -> None:
    out = _invoke(op, rows=rows, format=fmt, name=f"tiny-{op}-{fmt}", overwrite=True, **params)
    assert out["completion_state"] == "qualified-draft", out.get("reason")
    said = out["warning_card"]["message"]
    assert "under half a pixel" in said and any("under half a pixel" in n for n in out["coverage"]["notes"]), said
    assert ("'b'" in said) if op == "bar" else ("1 bin" in said)


def test_d1_a_value_axis_without_zero_is_refused_for_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    real = draw.linear

    def truncated(values, p0, p1, *, zero=False, count=5):
        return real([v for v in values if v > 50] or values, p0, p1, zero=False, count=count)

    monkeypatch.setattr(draw, "linear", truncated)
    out = _invoke("bar", rows=[{"k": "a", "v": 100}, {"k": "b", "v": 104}], x="k", y="v", name="d1-truncated")
    assert out["code"] == readback.CHART_MISMATCH and "without zero" in out["reason"]


def test_the_heat_map_keeps_every_cell_and_draws_the_missing_ones_empty() -> None:
    rows = [{"x": x, "y": y, "v": i} for i, (x, y) in enumerate((x, y) for x in "abc" for y in "pq") if i != 3]
    out = _invoke("heatmap", rows=rows, x="x", y="y", value="v", name="heat")
    assert out["completion_state"] == "qualified-draft", out.get("reason")
    assert (out["chart"]["cells"], out["chart"]["cells_empty"]) == (5, 1)
    assert out["evidence"][2]["cells_read"] == 5 and out["evidence"][2]["cells_empty"] == 1
    assert _texts(Path(out["artifact"]["path"]), "g6-value") == ["0", "1", "2", "4", "5"]


def test_a_heat_map_needs_x_y_and_value_and_no_column_is_guessed() -> None:
    out = _invoke("heatmap", rows=SALES, x="month", y="region")
    assert out["code"] == INPUT_INVALID and "name the value column" in out["reason"]
    out = _invoke("bar", rows=SALES, x="month", y="profit")
    assert out["code"] == INPUT_INVALID and "'profit'" in out["reason"] and "sales" in out["reason"]
    twice = _invoke("heatmap", rows=[*SALES, SALES[0]], x="month", y="region", value="sales")
    assert twice["code"] == INPUT_INVALID and "more than once" in twice["reason"]


@pytest.mark.parametrize(("op", "rows", "params", "column"), [
    ("bar", [{"k": "a b", "v": 1}, {"k": "a  b", "v": 2}], {"x": "k", "y": "v"}, "k"),
    ("bar", [{"k": "a\nb", "v": 1}, {"k": "c", "v": 3}, {"k": "a b", "v": 2}], {"x": "k", "y": "v"}, "k"),
    ("line", [{"k": " a", "v": 1}, {"k": "a", "v": 2}], {"x": "k", "y": "v"}, "k"),
    ("box", [{"g": "a b", "v": 1}, {"g": "a\tb", "v": 2}], {"x": "g", "y": "v"}, "g"),
    ("heatmap", [{"x": "p", "y": "a b", "v": 1}, {"x": "p", "y": "a  b", "v": 2}], {"x": "x", "y": "y", "value": "v"},
     "y"),
])
def test_categories_that_differ_only_in_white_space_are_refused_naming_them(op: str, rows: list, params: dict,
                                                                            column: str) -> None:
    before = _media_files()
    out = _invoke(op, rows=rows, **params)
    assert out["code"] == INPUT_INVALID and "differ only in white space" in out["reason"], out.get("reason")
    assert all(repr(r[column]) in out["reason"] for r in rows if r[column] != "c"), out["reason"]
    assert _media_files() == before


# -- values shown are the values given: read independently of the package -----------------------------------------------

def test_the_drawn_labels_and_categories_are_the_values_given() -> None:
    out = _invoke("bar", rows=SALES, x="month", y="sales", name="labels")
    path = Path(out["artifact"]["path"])
    assert _texts(path, "g6-x-category") == [r["month"] for r in SALES]
    assert [float(t) for t in _texts(path, "g6-value")] == [r["sales"] for r in SALES]
    ticks = [float(t) for t in _texts(path, "g6-y-tick")]
    assert ticks[0] <= -12.25 and ticks[-1] >= 160 and 0.0 in ticks       # the axis holds zero and every value
    root = ET.fromstring(path.read_text(encoding="utf-8"))
    bars = [e for e in root.iter(f"{SVG}rect") if e.get("class") == "g6-bar"]
    tick_y = [float(e.get("y")) for e in root.iter(f"{SVG}text") if e.get("class") == "g6-y-tick"]
    per_unit = (tick_y[0] - tick_y[-1]) / (ticks[-1] - ticks[0])
    heights = [float(b.get("height")) / per_unit for b in bars]
    assert heights == pytest.approx([abs(r["sales"]) for r in SALES], abs=0.05)


def test_known_values_box_and_histogram() -> None:
    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 100]
    box = _invoke("box", rows=[{"v": v} for v in values], y="v", name="box-known")
    stats = box["chart"]["groups"]["v"]
    assert (stats["q1"], stats["median"], stats["q3"]) == (3.25, 5.5, 7.75)    # type 7, as numpy.percentile
    assert (stats["low"], stats["high"], stats["outliers"]) == (1, 9, [100])
    hist = _invoke("histogram", rows=[{"v": v} for v in (1, 2, 2, 3, 3, 3, 4, 4, 4, 4)], x="v", bins=4)
    assert hist["chart"]["edges"] == [1, 1.75, 2.5, 3.25, 4] and hist["chart"]["counts"] == [1, 2, 3, 4]
    assert data.sturges(10) == 5 and data.shown(95.5) == "95.5" and data.shown(1e20) == "1e+20"


@pytest.mark.parametrize("values", [[5], [2, 2, 2], [0, 0, 0, 1, 2, 3, 4, 5]],
                         ids=["one row", "all equal", "low whisker of no length"])
@pytest.mark.parametrize("grouped", [True, False])
@pytest.mark.parametrize("fmt", FORMATS)
def test_a_box_whose_whisker_has_no_length_reads_back(values: list[int], grouped: bool, fmt: str) -> None:
    rows = [{"g": "a", "v": v} for v in values] + ([{"g": "b", "v": v} for v in (1, 4, 6, 9)] if grouped else [])
    out = _invoke("box", rows=rows, y="v", **({"x": "g"} if grouped else {}), format=fmt,
                  name=f"whisker-{len(values)}-{values[0]}-{grouped}-{fmt}", overwrite=True)
    assert out["completion_state"] == "qualified-draft", out.get("reason")
    stats = out["chart"]["groups"]["a" if grouped else "v"]
    assert stats["low"] == stats["q1"] or stats["high"] == stats["q3"]


def test_a_misplaced_whisker_is_still_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def longer(scene) -> None:  # the upper whisker, drawn upward from the box, made 12 pixels longer
        min((m for m in scene.marks if m.cls.startswith("g6-whisker")), key=lambda m: m.y2).y2 -= 12

    _misdraw(monkeypatch, "box", longer)
    out = _invoke("box", rows=[{"v": v} for v in (1, 2, 3, 4, 5, 6, 7, 8, 9, 100)], y="v", name="whisker-moved")
    assert out["code"] == readback.CHART_MISMATCH and "the high of 'v'" in out["reason"], out.get("reason")


@pytest.mark.parametrize(("op", "params"), CHARTS)
@pytest.mark.parametrize("fmt", FORMATS)
def test_every_chart_in_every_format_is_inspected_and_a_draft(op: str, params: dict, fmt: str) -> None:
    out = _invoke(op, rows=SALES, format=fmt, name=f"every-{op}-{fmt}-{len(params)}", overwrite=True, **params)
    assert out["completion_state"] == "qualified-draft", out.get("reason")
    checks = {c["name"]: c["passed"] for c in out["artifact"]["checks"]}
    assert checks["shows_the_data"] and all(checks.values())
    assert ("marks_where_drawn" in checks) == (fmt == "png") and ("loads_nothing" in checks) == (fmt != "png")
    assert out["artifact"]["mime"] == {"svg": "image/svg+xml", "png": "image/png", "html": "text/html"}[fmt]
    assert check_envelope(out) == []


def test_a_line_over_numbers_is_drawn_in_order_of_x_and_says_so() -> None:
    out = _invoke("line", rows=[{"t": 3, "v": 1}, {"t": 1, "v": 5}, {"t": 2, "v": 2}], x="t", y="v")
    assert out["chart"]["sorted_by_x"] and "in order of x" in out["coverage"]["notes"][0]
    assert _invoke("line", rows=[{"t": 1, "v": 1}, {"t": 1, "v": 2}], x="t", y="v")["code"] == INPUT_INVALID


# -- nothing G6 writes loads or runs anything ----------------------------------------------------------------------------

class _Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.found: list[tuple[str, dict]] = []

    def handle_starttag(self, tag, attrs) -> None:
        self.found.append((tag, dict(attrs)))


def test_no_written_file_loads_a_script_or_style_from_anywhere() -> None:
    rows = [{"k": "<script>alert(1)</script>", "v": 1}, {"k": "javascript:go()", "v": 2}]
    for fmt in ("svg", "html"):
        out = _invoke("bar", rows=rows, x="k", y="v", format=fmt, title="</title><script>x</script>",
                      name=f"offline-{fmt}")
        assert out["completion_state"] == "qualified-draft", out.get("reason")
        text = Path(out["artifact"]["path"]).read_text(encoding="utf-8")
        parser = _Tags()
        parser.feed(text)
        tags = {t for t, _ in parser.found}
        assert not tags & {"script", "link", "iframe", "object", "embed", "img", "image", "use", "foreignobject",
                           "base", "form", "style" if fmt == "svg" else "-"}, tags
        attrs = {k for _, a in parser.found for k in a}
        assert not {a for a in attrs if a in ("src", "href", "xlink:href", "srcset", "action") or a.startswith("on")}
        urls = set(re.findall(r"https?://[^\s\"'<>)]+", text))
        assert urls <= {"http://www.w3.org/2000/svg"}, urls        # the SVG namespace names, it is never fetched
        assert "@import" not in text and not re.search(r"url\(\s*['\"]?(?!#)", text)
        if fmt == "html":
            assert "Content-Security-Policy" in text and "default-src 'none'" in text
    assert not readback.page_loads_nothing('<html><body><img src="https://x/y.png"></body></html>').passed
    assert not readback.page_loads_nothing("<html><head><style>@import 'x.css'</style></head></html>").passed
    assert not readback.loads_nothing('<svg xmlns="http://www.w3.org/2000/svg"><script>x()</script></svg>').passed


# -- ADAPT_VISUALISATION-03 and G1: nothing is verified ------------------------------------------------------------------

def test_g1_over_mcp_a_chart_is_a_draft_resting_on_its_coverage_and_read_back() -> None:
    reply = invoke_reply("adapt_visualisation", "bar", json.dumps(
        {"rows": SALES, "x": "month", "y": "sales", "request_id": "q1", "run_id": "u1"}))
    assert reply["ok"] and reply["completion_state"] == "qualified-draft" and check_envelope(reply) == []
    result = reply["result"]
    assert [e["kind"] for e in result["evidence"]] == ["media_inspection", "chart_coverage", "chart_readback"]
    assert (result["request_id"], result["run_id"]) == ("q1", "u1")
    for path in SRC.rglob("*.py"):
        assert not re.search(r"""["']verified["']""", path.read_text(encoding="utf-8")), path.name


# -- G3: a caller's claims and code are refused ---------------------------------------------------------------------------

@pytest.mark.parametrize("field", [{"completion_state": "verified"}, {"approved": True}, {"reviewer": "Dr Who"},
                                   {"output_path": "C:/x.png"}, {"style": "ggplot"}, {"figsize": [2000, 2000]},
                                   {"script": "alert(1)"}, {"spec": {"javascript": "x"}}])
def test_g3_claims_paths_and_code_are_refused(field: dict) -> None:
    assert "G6_E_INPUT_VALIDATION" in _refused("bar", rows=SALES, x="month", y="sales", **field)


def test_category_lines_and_heat_maps_over_their_caps_are_refused_at_once() -> None:
    started = time.monotonic()
    line = _invoke("line", rows=[{"k": f"c{i}", "v": i % 7} for i in range(1001)], x="k", y="v")
    assert line["code"] == INPUT_INVALID and "at most 1000 points" in line["reason"], line.get("reason")
    wide = _invoke("heatmap", rows=[{"x": f"x{i}", "y": "y", "v": 1} for i in range(101)], x="x", y="y", value="v")
    assert wide["code"] == INPUT_INVALID and "at most 100 by 100" in wide["reason"], wide.get("reason")
    assert time.monotonic() - started < 2


def _at_the_cap(kind: str) -> tuple[str, object, dict]:
    if kind == "line":  # 1,000 categories
        records = [{"row": i, "x": f"c{i}", "y": float(i % 7)} for i in range(1000)]
        scene = draw.Scene("line", 2000, 600, "")
        draw.line(scene, records, "k", "v", numeric_x=False)
        return draw.to_svg(scene), [(r["x"], r["y"]) for r in records], {"numeric_x": False}
    records = [{"row": 100 * i + j, "x": f"x{i}", "y": f"y{j}", "value": float((i + j) % 10)}
               for i in range(100) for j in range(100)]  # 100 by 100 cells, each with its value written on it
    scene = draw.Scene("heatmap", 2000, 2000, "")
    draw.heatmap(scene, records, "x", "y", "v")
    assert sum(m.cls == "g6-value" for m in scene.marks) == 10_000
    return draw.to_svg(scene), {(r["x"], r["y"]): r["value"] for r in records}, {}


@pytest.mark.parametrize("kind", ["line", "heatmap"])
def test_a_chart_at_its_cap_reads_back_well_within_a_second(kind: str) -> None:
    svg, expected, options = _at_the_cap(kind)
    started = time.perf_counter()
    check, _ = readback.chart_check(kind, svg, expected, **options)
    elapsed = time.perf_counter() - started
    assert check.passed, check.detail
    assert elapsed < 1.0, f"{kind} at its cap read back in {elapsed:.2f} s"


def test_limits_are_one_set_for_every_surface() -> None:
    assert "G6_E_INPUT_VALIDATION" in _refused("bar", rows=SALES, x="month", y="sales", width=20000)
    many = [{"k": str(i), "v": i} for i in range(201)]
    assert _invoke("bar", rows=many, x="k", y="v")["code"] == INPUT_INVALID
    assert "G6_E_INPUT_VALIDATION" in _refused("bar", rows=[{"k": "a\x00b", "v": 1}], x="k", y="v")


# -- G4: errors tighten ---------------------------------------------------------------------------------------------------

def test_g4_a_drawing_that_raises_is_refused_and_leaves_no_file(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        raise ZeroDivisionError("in the middle of the axis")

    monkeypatch.setattr(draw, "bar", boom)
    before = _media_files()
    out = _invoke("bar", rows=SALES, x="month", y="sales", name="g4-boom")
    assert out["code"] == BACKEND_FAILED and "in the middle of the axis" in out["reason"] and _media_files() == before


def test_g4_an_inspector_that_raises_removes_the_file(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("reader broke")

    monkeypatch.setattr(readback, "chart_check", boom)
    before = _media_files()
    out = _invoke("bar", rows=SALES, x="month", y="sales", name="g4-reader")
    assert out["code"] == BACKEND_FAILED and _media_files() == before


def test_g4_a_png_without_pillow_is_refused_before_anything_is_drawn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(block, "pillow", lambda: None)
    out = _invoke("bar", rows=SALES, x="month", y="sales", format="png", name="g4-nopil")
    assert out["code"] == EXTRA_MISSING and "pip install 'g6[image] @ git+https://github.com/basicisabc1994/g6solver'" in out["reason"]
    assert "g4-nopil.png" not in _media_files()


@pytest.mark.parametrize(("value", "width", "height"), [(6, 640, 400), (50, 640, 400), (7, 333, 211),
                                                       (0.3, 1999, 1777), (123456, 241, 181), (-2.5, 917, 603)])
def test_ticks_written_to_a_hundredth_of_a_pixel_read_back(value: float, width: int, height: int) -> None:
    out = _invoke("bar", rows=[{"k": "a", "v": value}, {"k": "b", "v": value / 3}], x="k", y="v", width=width,
                  height=height, name=f"ticks-{width}x{height}", overwrite=True)
    assert out["completion_state"] == "qualified-draft", out.get("reason")
    single = _invoke("bar", rows=[{"k": "a", "v": value}], x="k", y="v", width=width, height=height,
                     name=f"tick-{width}x{height}", overwrite=True)
    assert single["completion_state"] == "qualified-draft", single.get("reason")
    if (value, width, height) == (6, 640, 400):  # the default single bar: ticks at 348, 237.33, 126.67 and 16
        root = ET.fromstring(Path(single["artifact"]["path"]).read_text(encoding="utf-8"))
        assert [e.get("y") for e in root.iter(f"{SVG}text") if e.get("class") == "g6-y-tick"] == [
            "348.00", "237.33", "126.67", "16.00"]


@pytest.mark.parametrize("pixels", [1.0, -1.0, 0.25])
def test_a_tick_moved_off_its_place_is_still_refused(monkeypatch: pytest.MonkeyPatch, pixels: float) -> None:
    def moved(scene) -> None:
        [m for m in scene.marks if m.cls == "g6-y-tick"][1].y += pixels

    _misdraw(monkeypatch, "bar", moved)
    out = _invoke("bar", rows=[{"k": "a", "v": 6}], x="k", y="v", name="tick-moved")
    assert out["code"] == readback.CHART_MISMATCH and "not evenly spaced" in out["reason"], out.get("reason")


def test_the_reader_refuses_an_axis_whose_ticks_lie() -> None:
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
           '<text class="g6-y-tick" x="0" y="90">0</text><text class="g6-y-tick" x="0" y="50">10</text>'
           '<text class="g6-y-tick" x="0" y="40">20</text></svg>')
    check, _ = readback.chart_check("bar", svg, [("a", 1.0)])
    assert not check.passed and "not evenly spaced" in check.detail


# -- G5 to G8 ---------------------------------------------------------------------------------------------------------------

@needs_pillow
def test_g5_it_has_no_sensitive_ops_because_it_opens_no_socket_and_starts_no_program(
        monkeypatch: pytest.MonkeyPatch) -> None:
    assert package.__g6_sensitive_ops__ == () and package.__g6_outside__ == ()

    def refused(*args, **kwargs):
        raise AssertionError("adapt_visualisation reached outside the machine or started a program")

    monkeypatch.setattr(socket.socket, "connect", refused)
    monkeypatch.setattr(subprocess, "Popen", refused)
    for op, params in CHARTS:
        assert _invoke(op, rows=SALES, format="png", **params)["completion_state"] == "qualified-draft"


def test_g6_g7_the_package_reads_no_key_calls_no_model_and_holds_no_stand_in() -> None:
    for path in SRC.rglob("*"):
        if path.suffix not in (".py", ".md"):
            continue
        text = path.read_text(encoding="utf-8").lower()
        for word in ("api_key", "getenv", "environ", "litellm", "openrouter", "anthropic", "openai", "mock", "fake",
                     "simulat", "experimental", "all rights reserved", "matplotlib", "https://cdn", "plotly.min",
                     "d3@"):
            assert word not in text or (word == "matplotlib" and path.name == "__init__.py"), (path.name, word)
        if path.suffix == ".py":
            assert path.read_text(encoding="utf-8").startswith("# SPDX-License-Identifier: Apache-2.0\n"), path.name
            tree = ast.parse(path.read_text(encoding="utf-8"))
            assert not [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") in
                        ("eval", "exec", "compile")], path.name


@needs_pillow
def test_g8_the_extra_is_permissively_licensed() -> None:
    assert package.__g6_extras__ == {"image": ("PIL",)}
    meta = importlib.metadata.metadata("pillow")
    said = " ".join(filter(None, [meta.get("License-Expression"), meta.get("License"),
                                  *meta.get_all("Classifier", [])])).upper()
    assert said and not re.search(r"\b(A?GPL|SSPL|NON-?COMMERCIAL)\b", said), said


# -- D2, D4, D6 -------------------------------------------------------------------------------------------------------------

def test_d2_it_declares_what_it_catches_and_moved_out_of_planned() -> None:
    assert package.__g6_mitigates__ == ("misleading_chart", "visual_check_failed") and package.__g6_introduces__ == ()
    assert "g6.media.adapt_visualisation" not in FailureModeRegistry.planned()


def test_d4_the_block_is_typed_and_lists_its_ops() -> None:
    assert get_registry().get_class("adapt_visualisation") is AdaptVisualisationBlock
    assert block.ChartInput.model_config["extra"] == "forbid"
    assert _invoke("ops")["ops"] == ["line", "bar", "scatter", "histogram", "box", "heatmap"]
    assert len(_invoke("help")["help"]) == 6
    assert _invoke("bar", rows=[], x="a", y="b")["code"] == INPUT_INVALID


def test_d6_an_agent_finds_it_by_its_failure_mode_describes_it_and_invokes_it_over_mcp() -> None:
    title = FailureModeRegistry.title("misleading_chart")
    assert "adapt_visualisation" in [r["component"] for r in discovery.find(title, limit=5)]
    info = describe_reply("adapt_visualisation")
    assert info["family"] == "media" and info["skill"] and info["sensitive_ops"] == []
    assert sorted(info["ops"]) == sorted(["line", "bar", "scatter", "histogram", "box", "heatmap"])
    for op, params in CHARTS:
        reply = invoke_reply("adapt_visualisation", op, json.dumps({"rows": SALES, **params}))
        assert reply["ok"] and reply["completion_state"] == "qualified-draft", reply


# -- D9: without Pillow, PNG says it is not installed; SVG and HTML answer ---------------------------------------------------

_WITHOUT = """
import json, sys
sys.modules["PIL"] = None
from g6.core.registry import get_registry
reg = get_registry()
rows = [{"k": "a", "v": 1}, {"k": "b", "v": 2}]
out = {fmt: reg.invoke("adapt_visualisation", "bar", {"rows": rows, "x": "k", "y": "v", "format": fmt}).unwrap()
       for fmt in ("svg", "html", "png")}
print(json.dumps({fmt: [v.get("completion_state"), v.get("code", "")] for fmt, v in out.items()}))
"""


def test_d9_without_pillow_a_png_says_it_is_not_installed_and_svg_and_html_answer(tmp_path: Path) -> None:
    done = subprocess.run([sys.executable, "-c", _WITHOUT], capture_output=True, text=True, timeout=120,
                          env={"G6_HOME": str(tmp_path), "PATH": "", "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")})
    assert done.returncode == 0, done.stderr
    found = json.loads(done.stdout.strip().splitlines()[-1])
    assert found["svg"] == ["qualified-draft", ""] == found["html"]
    assert found["png"] == ["blocked-escalated", EXTRA_MISSING]
