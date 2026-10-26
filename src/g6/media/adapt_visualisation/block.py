# SPDX-License-Identifier: Apache-2.0
"""AdaptVisualisationBlock, the registry and MCP door (``g6_invoke``) to adapt_visualisation: charts of rows you give.

Ops: ``line``, ``bar``, ``scatter``, ``histogram``, ``box`` and ``heatmap``, plus ``ops`` and ``help``. Each reads the
columns you name (it never guesses one), counts every row it cannot plot and refuses to drop any unless you pass
``drop_unplottable`` (``G6_E_MEDIA_CHART_DATA_DROPPED``), draws the chart with G6's own code and writes it into G6's
media folder as SVG, PNG (the ``image`` extra) or a self-contained HTML page, inspecting the file from disk before it
takes its name: it decodes, is the size asked for, loads nothing from outside itself, and every value it shows is the
value given, read back from the file (for a PNG, from the layout it is drawn from, written as SVG, with its marks
looked for in the picture by colour; with none large enough to look for, the result says the picture was checked only
for decoding, its size and not being blank). A value too small to see is reported. A file that fails is removed, a
file it would have replaced is left as it was, and the op is refused with the failed check's code. Nothing here is
ever ``verified``, and nothing G6 writes runs code.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.media._inspect import inspect_image, inspect_svg, pillow
from g6.media._types import (
    BACKEND_FAILED,
    EXTRA_MISSING,
    INPUT_INVALID,
    NO_OUTPUT,
    SIZE_MISMATCH,
    UNDECODABLE,
    Check,
    Inspection,
    MediaError,
    MediaResult,
    Provenance,
    answer,
    check_name,
    discard,
    media_artifact,
    media_file,
    place_media,
    refuse,
    stage_media,
)
from g6.media.adapt_visualisation import data, draw, readback
from g6.media.adapt_visualisation.data import MAX_ROWS, ChartDataError, Value

DATA_DROPPED = "G6_E_MEDIA_CHART_DATA_DROPPED"
_OPS = {
    "line": "line(rows, x, y): a line through each row's point, numbers on x sorted, categories in the order given "
            "(at most 1000 of them)",
    "bar": "bar(rows, x as the category, y as the number): one bar per category, from a value axis holding zero",
    "scatter": "scatter(rows, x, y): one point per row, both numbers",
    "histogram": "histogram(rows, x, bins): how many rows fall in each of equal-width bins of x",
    "box": "box(rows, y, x to group by): quartiles, whiskers within 1.5 IQR and outliers of y, per group",
    "heatmap": "heatmap(rows, x, y, value): one cell per x and y category, coloured by value, empty where no row",
}
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ChartInput(BaseModel):
    """One chart's rows and how to draw them. Fields an op does not use are ignored by it; fields no op has are
    refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["line", "bar", "scatter", "histogram", "box", "heatmap", "ops", "help"] = "help"
    rows: list[dict[str, Value]] = Field(default_factory=list, max_length=MAX_ROWS)
    x: str = Field("", max_length=100)
    y: str = Field("", max_length=100)
    value: str = Field("", max_length=100)
    bins: int | None = Field(None, ge=1, le=50)
    title: str = Field("", max_length=200)
    x_label: str = Field("", max_length=100)
    y_label: str = Field("", max_length=100)
    value_label: str = Field("", max_length=100)
    width: int = Field(640, ge=240, le=2000)
    height: int = Field(400, ge=180, le=2000)
    format: Literal["svg", "png", "html"] = "svg"
    drop_unplottable: bool = False
    name: str = Field("", max_length=100)
    overwrite: bool = False
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)

    @field_validator("rows")
    @classmethod
    def _plain_rows(cls, rows: list[dict[str, Value]]) -> list[dict[str, Value]]:
        for i, row in enumerate(rows):
            if len(row) > 50:
                raise ValueError(f"row {i} has more than 50 columns")
            for key, value in row.items():
                text = [key] + ([value] if isinstance(value, str) else [])
                if any(len(t) > 200 or _CONTROL.search(t) for t in text):
                    raise ValueError(f"row {i}: a column name or text value is longer than 200 characters or holds "
                                     "control characters")
        return rows

    @field_validator("title", "x_label", "y_label", "value_label", "x", "y", "value")
    @classmethod
    def _one_line(cls, value: str) -> str:
        if _CONTROL.search(value) or "\n" in value or "\r" in value:
            raise ValueError("a title, label or column name is one line with no control characters")
        return value

    @field_validator("name")
    @classmethod
    def _plain_name(cls, value: str) -> str:
        if value:
            try:
                check_name(value)
            except MediaError as exc:
                raise ValueError(exc.reason) from None
        return value


def _ids(d: ChartInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


def _roles(d: ChartInput) -> dict[str, tuple[str, str]]:
    if d.op == "bar":
        return {"x": (d.x, "category"), "y": (d.y, "number")}
    if d.op == "line":
        return {"x": (d.x, "auto"), "y": (d.y, "number")}
    if d.op == "scatter":
        return {"x": (d.x, "number"), "y": (d.y, "number")}
    if d.op == "histogram":
        return {"x": (d.x, "number")}
    if d.op == "box":
        return {"y": (d.y, "number"), **({"x": (d.x, "category")} if d.x else {})}
    return {"x": (d.x, "category"), "y": (d.y, "category"), "value": (d.value, "number")}


def _expected(d: ChartInput, records: list[dict], numeric_x: bool, y_title: str) -> Any:
    """What the chart must show, worked out from the rows here, not by the drawing."""
    if d.op in ("bar", "scatter"):
        return [(r["x"], r["y"]) for r in records]
    if d.op == "line":
        ordered = sorted(records, key=lambda r: r["x"]) if numeric_x else records
        return [(r["x"], r["y"]) for r in ordered]
    if d.op == "histogram":
        return [r["x"] for r in records]
    if d.op == "box":
        groups: dict[str, list[float]] = {}
        for r in records:
            groups.setdefault(r["x"] if d.x else y_title, []).append(r["y"])
        return groups
    return {(r["x"], r["y"]): r["value"] for r in records}


def _scene(d: ChartInput, records: list[dict], numeric_x: bool) -> draw.Scene:
    scene = draw.Scene(d.op, d.width, d.height, d.title)
    x_title, y_title = d.x_label or d.x, d.y_label or d.y
    if d.op == "bar":
        draw.bar(scene, records, x_title, y_title)
    elif d.op == "line":
        draw.line(scene, records, x_title, y_title, numeric_x=numeric_x)
    elif d.op == "scatter":
        draw.scatter(scene, records, x_title, y_title)
    elif d.op == "histogram":
        draw.histogram(scene, records, x_title, d.bins)
    elif d.op == "box":
        draw.box(scene, records, x_title, y_title, grouped=bool(d.x))
    else:
        draw.heatmap(scene, records, x_title, y_title, d.value_label or d.value)
    return scene


def _page(path: Path, d: ChartInput) -> tuple[Inspection, str]:
    """The HTML page's own checks, and the SVG it holds."""
    inspector = "g6.media page inspector (html.parser, xml.etree)"
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    if not text:
        return Inspection(inspector=inspector, kind="page", checks=(
            Check(name="exists", passed=False, code=NO_OUTPUT, detail="no page was written"),)), ""
    found = re.search(r"<svg\b.*?</svg>", text, re.DOTALL)
    checks = [Check(name="exists", passed=True, detail=f"{path.stat().st_size} bytes")]
    if found is None:
        checks.append(Check(name="parses", passed=False, code=UNDECODABLE, detail="the page holds no chart"))
        return Inspection(inspector=inspector, kind="page", checks=tuple(checks)), ""
    svg = found.group(0)
    size = re.match(r'<svg[^>]*\swidth="(\d+)"[^>]*\sheight="(\d+)"', svg)
    ok = bool(size) and (int(size.group(1)), int(size.group(2))) == (d.width, d.height)
    checks += [Check(name="parses", passed=True, detail="an HTML page holding one SVG chart"),
               Check(name="size_matches_request", passed=ok, code="" if ok else SIZE_MISMATCH,
                     detail=f"the chart is {size.group(1)}x{size.group(2)}" if size else "the chart has no size")]
    return Inspection(inspector=inspector, kind="page", checks=tuple(checks), mime="text/html",
                      measured={"width": d.width, "height": d.height} if ok else {}), svg


def _version() -> str:
    import g6

    return f"g6 {g6.__version__} charts (SVG and HTML: standard library; PNG: Pillow)"


def _make(d: ChartInput) -> MediaResult:
    ids = _ids(d)
    if not d.rows:
        return refuse(d.op, INPUT_INVALID, "no rows were given to chart", **ids)
    if d.format == "png" and pillow() is None:
        return refuse(d.op, EXTRA_MISSING, "Pillow is not installed, so a PNG can be neither drawn nor inspected: "
                                           "pip install 'g6[image]' (or ask for svg or html, which need nothing)",
                      **ids)
    try:
        records, coverage = data.take(d.rows, _roles(d))
    except ChartDataError as exc:
        return refuse(d.op, INPUT_INVALID, str(exc), **ids)
    report = coverage.report()
    if coverage.dropped_count and not d.drop_unplottable:
        return refuse(d.op, DATA_DROPPED, f"{coverage.said()}: nothing was drawn, because a chart that leaves rows out "
                                          "says less than the data; correct them, or pass drop_unplottable=true to "
                                          "chart the rest with the drop reported",
                      evidence=[{"kind": "chart_coverage", **report}], coverage=report, **ids)
    if not records:
        return refuse(d.op, DATA_DROPPED, f"{coverage.said()}: no row can be plotted",
                      evidence=[{"kind": "chart_coverage", **report}], coverage=report, **ids)
    numeric_x = coverage.columns.get("x_kind") == "number"
    try:
        scene = _scene(d, records, numeric_x)
    except ChartDataError as exc:
        return refuse(d.op, INPUT_INVALID, str(exc), evidence=[{"kind": "chart_coverage", **report}], **ids)
    if scene.facts.get("sorted_by_x"):
        coverage.notes.append("the rows were drawn in order of x, not in the order given")
        report = coverage.report()
    unseen = scene.facts.get("under_half_a_pixel", [])
    if unseen:
        what = f"{len(unseen)} {'bar' if d.op == 'bar' else 'bin'}{'s' if len(unseen) > 1 else ''}"
        coverage.notes.append(f"{what} under half a pixel tall, too small to see: {', '.join(unseen[:5])}"
                              + (f" and {len(unseen) - 5} more" if len(unseen) > 5 else ""))
        report = coverage.report()
    svg = draw.to_svg(scene)
    payload = (draw.to_png(scene) if d.format == "png" else draw.to_html(scene, svg, coverage.said())
               if d.format == "html" else svg.encode("utf-8"))
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    recipe = {"op": d.op, "format": d.format, "width": d.width, "height": d.height, "columns": coverage.columns,
              "bins": d.bins, "drop_unplottable": d.drop_unplottable,
              "rows_sha256": hashlib.sha256(json.dumps(d.rows, sort_keys=True).encode()).hexdigest()}
    digest = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()
    try:
        path = media_file(d.name or f"{d.op}-{digest[:12]}", f".{d.format}")
        staged, written = stage_media(path, payload, overwrite=d.overwrite)
    except MediaError as exc:
        return refuse(d.op, exc.code, exc.reason, **ids)
    try:
        inspection, measured = _inspect_chart(d, staged, svg, scene, _expected(d, records, numeric_x,
                                                                               d.y_label or d.y),
                                              {"numeric_x": numeric_x} if d.op == "line" else {})
    except Exception as exc:  # noqa: BLE001 - a file that could not be inspected is never left to look like a result
        discard(staged)
        return refuse(d.op, BACKEND_FAILED, f"the chart could not be inspected: {type(exc).__name__}: {exc}"[:500],
                      **ids)
    if not inspection.passed:
        failure = inspection.failure
        removed = discard(staged)
        return refuse(d.op, failure.code, f"{d.op}: {failure.detail}", evidence=[
            inspection.evidence(), {"kind": "chart_coverage", **report, "file_removed": removed}], coverage=report,
            **ids)
    try:
        place_media(staged, path)
    except (OSError, MediaError) as exc:
        discard(staged)
        return refuse(d.op, BACKEND_FAILED, f"the chart passed but could not take its name: {exc}"[:500], **ids)
    artifact = media_artifact(path, inspection, Provenance(backend="g6_charts", backend_version=_version(),
                                                           technique=d.op, recipe=recipe, recipe_sha256=digest),
                              {"chart": d.op, "format": d.format, "width": d.width, "height": d.height,
                               "rows": len(d.rows)})
    if d.format != "png":
        said = f"a {d.op} chart, inspected: read back from the file, every value it shows is the value given"
    else:
        looked = measured.get("picture_marks_looked_for", 0)
        said = (f"a {d.op} chart as PNG, inspected: read back from the layout it is drawn from (written as SVG, not "
                "from the PNG's pixels), every value it shows is the value given; "
                + (f"{looked} marks were found in the picture where the layout puts them" if looked else
                   "the picture itself was checked only for decoding, its size and not being blank, because no mark "
                   "is large enough to look for in it"))
    notes = [n for n in coverage.notes if "under half a pixel" in n]
    return answer(d.op, f"{said}; {coverage.said()}" + "".join(f"; {n}" for n in notes), artifact=artifact,
                  evidence=[{"kind": "chart_coverage", **report}, {"kind": "chart_readback", **measured}],
                  coverage=report, chart=scene.facts, written=written, **ids)


def _inspect_chart(d: ChartInput, path: Path, svg: str, scene: draw.Scene, expected: Any,
                   options: dict[str, Any]) -> tuple[Inspection, dict[str, Any]]:
    """The written file inspected from disk: the family's checks for its format, then every value read back."""
    if d.format == "svg":
        text = path.read_text(encoding="utf-8")
        base = inspect_svg(path, width=d.width, height=d.height)
        shows, measured = readback.chart_check(d.op, text, expected, **options)
        shows = shows.model_copy(update={"detail": f"read back from the file: {shows.detail}"[:500]})
        extra = [shows, readback.loads_nothing(text)]
    elif d.format == "png":
        base = inspect_image(path, width=d.width, height=d.height, formats=("PNG",))
        shows, measured = readback.chart_check(d.op, svg, expected, **options)
        shows = shows.model_copy(update={"detail": "read back from the layout the PNG is drawn from, written as SVG, "
                                                   f"not from its pixels: {shows.detail}"[:500]})
        probes = draw.probes(scene) if base.passed else []
        extra = [shows, readback.png_probes(path, probes)] if probes else [shows]
        measured = {**measured, "picture_marks_looked_for": len(probes)}
    else:
        base, inner = _page(path, d)
        page = path.read_text(encoding="utf-8", errors="replace")
        shows, measured = readback.chart_check(d.op, inner, expected, **options) if inner else (
            Check(name="shows_the_data", passed=False, code=readback.CHART_MISMATCH, detail="no chart to read"), {})
        shows = shows.model_copy(update={"detail": f"read back from the page's chart: {shows.detail}"[:500]})
        extra = [shows, readback.page_loads_nothing(page)]
    inspection = Inspection(inspector=f"{base.inspector}, with adapt_visualisation's read-back of every value",
                            kind=base.kind, checks=(*base.checks, *extra), measured={**base.measured, **measured},
                            mime=base.mime)
    return inspection, measured


@block_contract(
    purpose="chart rows the caller gives (line, bar, scatter, histogram, box, heat map) with G6's own drawing, and "
            "show that the file holds every row given and only the values given",
    mitigates_failure_modes=("misleading_chart", "visual_check_failed"),
    verification_method="coverage of every row; the media inspector; each value read back from the written file "
                        "through its drawn axes and compared with values worked out from the rows",
)
@component_maturity(
    "beta",
    reason="charts drawn by G6's own code from named columns; dropped rows refused unless asked for and always "
           "reported; every value read back from the file; qualified-draft at best: whether the chart answers the "
           "question asked is not judged",
)
class AdaptVisualisationBlock(AIBlock[ChartInput, dict, dict]):
    """Chart rows and say what was checked about the chart. Never ``verified``."""

    def __init__(self, name: str = "adapt_visualisation", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: ChartInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": []})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        try:
            result = _make(data)
        except Exception as exc:  # noqa: BLE001 - an error is a refusal with its reason, never a crash or a chart
            result = refuse(data.op, BACKEND_FAILED, f"{data.op} failed: {type(exc).__name__}: {exc}"[:500],
                            **_ids(data))
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


__all__ = ["DATA_DROPPED", "AdaptVisualisationBlock", "ChartInput"]
