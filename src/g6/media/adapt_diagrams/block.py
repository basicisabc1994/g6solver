# SPDX-License-Identifier: Apache-2.0
"""AdaptDiagramsBlock, the registry and MCP door (``g6_invoke``) to adapt_diagrams.

Text ops write a diagram's source from nodes and edges (``mermaid_flowchart``, ``mermaid_sequence``,
``graphviz_digraph``, ``graphviz_graph``, ``plantuml_sequence``, ``plantuml_component``) or an ASCII drawing
(``ascii_box``, ``ascii_tree``, ``ascii_table``); each checks what it writes and answers ``qualified-draft`` with the
``G6_E_MEDIA_TEXT_ONLY`` card, because no program parsed or drew the text. ``render_dot`` has Graphviz's dot draw DOT
source (yours, screened, or one G6 writes from nodes and edges) as SVG or PNG into G6's media folder, inspecting the
file from disk before it takes its name; a file that fails is removed, a file it would have replaced is left as it was,
and the op is refused with the failed check's code. ``render_dot`` runs a
program on your computer, so the package lists it in ``__g6_sensitive_ops__``: it runs from Python, and over MCP only
when the person who runs the server lists it in ``G6_MCP_SENSITIVE``. Nothing here is ever ``verified``.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.media._inspect import inspect_image, inspect_svg, pillow
from g6.media._types import (
    BACKEND_FAILED,
    EXTRA_MISSING,
    SIZE_MISMATCH,
    TEXT_ONLY,
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
from g6.media.adapt_diagrams import dot, generators
from g6.media.adapt_diagrams.generators import MAX_EDGES, MAX_NODES, Cell, Direction, Edge, Node, TreeNode

_OPS = {
    "mermaid_flowchart": "mermaid_flowchart(nodes, edges, direction, directed, title): Mermaid flowchart text, every "
                         "label escaped and every edge checked against the nodes",
    "mermaid_sequence": "mermaid_sequence(nodes as participants, edges as labelled messages in order, title): Mermaid "
                        "sequence diagram text",
    "graphviz_digraph": "graphviz_digraph(nodes, edges, direction, title): Graphviz DOT text for a directed graph, "
                        "passing the screen render_dot uses",
    "graphviz_graph": "graphviz_graph(nodes, edges, direction, title): Graphviz DOT text for an undirected graph",
    "plantuml_sequence": "plantuml_sequence(nodes as participants, edges as labelled messages, title): PlantUML text",
    "plantuml_component": "plantuml_component(nodes, edges, title): PlantUML component diagram text",
    "ascii_box": "ascii_box(title, lines): a box drawn in text, every line the same display width",
    "ascii_tree": "ascii_tree(tree={label, children}): a tree drawn in text",
    "ascii_table": "ascii_table(headers, rows): a table drawn in text, one cell per header in every row, each cell at "
                   "most 200 characters and the table at most 1,000,000 columns drawn in all",
    "render_dot": "render_dot(source, or nodes and edges with directed and direction; format=svg|png, name, overwrite, "
                  "timeout_s): Graphviz dot draws it into G6's media folder and the file is inspected",
}
#: It runs Graphviz's dot, a program on your computer, so over MCP it runs only when the server's owner allows it.
SENSITIVE_OPS: tuple[str, ...] = ("render_dot",)
_GRAPH_OPS = ("mermaid_flowchart", "mermaid_sequence", "graphviz_digraph", "graphviz_graph", "plantuml_sequence",
              "plantuml_component")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")


class DiagramInput(BaseModel):
    """One op's parameters. Fields an op does not use are ignored by it; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["mermaid_flowchart", "mermaid_sequence", "graphviz_digraph", "graphviz_graph", "plantuml_sequence",
                "plantuml_component", "ascii_box", "ascii_tree", "ascii_table", "render_dot", "ops", "help"] = "help"
    title: str = Field("", max_length=200)
    nodes: list[Node] = Field(default_factory=list, max_length=MAX_NODES)
    edges: list[Edge] = Field(default_factory=list, max_length=MAX_EDGES)
    directed: bool = True
    direction: Direction = "TB"
    source: str = Field("", max_length=dot.MAX_SOURCE)
    format: Literal["svg", "png"] = "svg"
    lines: list[str] = Field(default_factory=list, max_length=200)
    tree: TreeNode | None = None
    headers: list[str] = Field(default_factory=list, max_length=30)
    rows: list[list[Cell]] = Field(default_factory=list, max_length=1000)
    name: str = Field("", max_length=100)
    overwrite: bool = False
    timeout_s: float = Field(dot.DEFAULT_SECONDS, ge=1, le=dot.MAX_SECONDS)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)

    @field_validator("title")
    @classmethod
    def _one_line(cls, value: str) -> str:
        if _CONTROL.search(value):
            raise ValueError("a title is one line with no control characters")
        return value

    @field_validator("lines", "headers")
    @classmethod
    def _short_lines(cls, value: list[str]) -> list[str]:
        if any(len(v) > 200 for v in value):
            raise ValueError("a line or header is at most 200 characters")
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

    @model_validator(mode="after")
    def _what_the_op_needs(self) -> DiagramInput:
        if self.op == "render_dot" and bool(self.source.strip()) == bool(self.nodes):
            raise ValueError("render_dot draws either DOT source or nodes and edges: give one of them")
        if self.op == "ascii_tree" and self.tree is None:
            raise ValueError("ascii_tree needs a tree: {\"label\": ..., \"children\": [...]}")
        return self


def _ids(d: DiagramInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


def _graph_text(d: DiagramInput) -> tuple[str, str, list[str]]:
    title = {"title": d.title}
    checks = ["node ids are unique", "every edge joins declared nodes"]
    if d.op == "mermaid_flowchart":
        return "mermaid", generators.mermaid_flowchart(d.nodes, d.edges, direction=d.direction, directed=d.directed,
                                                       **title), checks + ["labels written as Mermaid entity codes"]
    if d.op == "mermaid_sequence":
        return "mermaid", generators.mermaid_sequence(d.nodes, d.edges, **title), checks + [
            "every message has a label", "labels written as Mermaid entity codes"]
    if d.op in ("graphviz_digraph", "graphviz_graph"):
        text = generators.dot(d.nodes, d.edges, directed=d.op == "graphviz_digraph", direction=d.direction, **title)
        dot.screen(text)
        return "dot", text, checks + ["names and labels quoted", "passes the screen render_dot runs before dot"]
    maker = generators.plantuml_sequence if d.op == "plantuml_sequence" else generators.plantuml_component
    return "plantuml", maker(d.nodes, d.edges, **title), checks + [
        "every label and the title hold only letters, digits, spaces and plain punctuation"]


def _ascii_text(d: DiagramInput) -> tuple[str, list[str]]:
    if d.op == "ascii_box":
        text = generators.ascii_box(d.title, d.lines)
    elif d.op == "ascii_tree":
        text = generators.ascii_tree(d.tree)
    else:
        text = generators.ascii_table(d.headers, d.rows)
    checks = []
    if d.op != "ascii_tree":
        widths = {generators.display_width(line) for line in text.splitlines()}
        if len(widths) != 1:  # a guard on the generator itself: a ragged box is never returned as drawn
            raise MediaError(BACKEND_FAILED, f"the drawing came out ragged (line widths {sorted(widths)})")
        checks.append(f"every line is {widths.pop()} columns wide")
    if d.op == "ascii_table":
        rows = text.splitlines()[3:-1]
        missing = [(r, c) for r, row in enumerate(d.rows) for c, cell in enumerate(row)
                   if generators.cell_text(cell) not in rows[r]]
        if missing:
            raise MediaError(BACKEND_FAILED, f"cells missing from their rows: {missing[:5]}")
        checks.append("every cell is in its own row")
    return text, checks


def _text(d: DiagramInput) -> MediaResult:
    try:
        if d.op in _GRAPH_OPS:
            dialect, text, checks = _graph_text(d)
        else:
            dialect = "ascii"
            text, checks = _ascii_text(d)
    except MediaError as exc:
        return refuse(d.op, exc.code, exc.reason, **_ids(d))
    said = {"mermaid": "Mermaid", "dot": "Graphviz DOT", "plantuml": "PlantUML", "ascii": "ASCII"}[dialect]
    hint = " (draw it with render_dot)" if dialect == "dot" else ""
    return answer(d.op, f"{said} text, written and checked by G6; no program parsed or drew it{hint}", card=TEXT_ONLY,
                  evidence=[{"kind": "diagram_text", "dialect": dialect, "checks": checks, "parsed_by": None,
                             "drawn": False}],
                  text=text, dialect=dialect, checks=checks, **_ids(d))


def _svg_size(svg: str) -> tuple[float, float] | None:
    match = re.search(r'<svg[^>]*\swidth="([\d.]+)pt"[^>]*\sheight="([\d.]+)pt"', svg)
    return (float(match.group(1)), float(match.group(2))) if match else None


def _same_shape(svg: str, measured: dict[str, Any]) -> Check:
    """Whether the PNG is the picture the SVG describes: the same proportions, within a pixel or two."""
    size, width, height = _svg_size(svg), measured.get("width"), measured.get("height")
    if size is None or not width or not height or not size[0] or not size[1]:
        return Check(name="png_matches_layout", passed=False, code=SIZE_MISMATCH,
                     detail="the layout's size or the picture's could not be read")
    expected = size[1] * width / size[0]
    ok = abs(height - expected) <= max(2.0, 0.02 * expected)
    return Check(name="png_matches_layout", passed=ok, code="" if ok else SIZE_MISMATCH,
                 detail=f"{width}x{height} pixels for a {size[0]:g}x{size[1]:g} pt layout"
                        + ("" if ok else f" (expected about {expected:.0f} pixels high)"))


def _render(d: DiagramInput) -> MediaResult:
    ids = _ids(d)
    try:
        if d.source.strip():
            source, expect = d.source, None
        else:
            source = generators.dot(d.nodes, d.edges, directed=d.directed, direction=d.direction, title=d.title)
            expect = ([n.id for n in d.nodes], [(e.source, e.target) for e in d.edges])
        screened = dot.screen(source)
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        path = media_file(d.name or f"diagram-{digest[:12]}", f".{d.format}")
        if d.format == "png" and pillow() is None:
            return refuse(d.op, EXTRA_MISSING, "Pillow is not installed, so a PNG cannot be inspected and dot was not "
                                               "run: pip install 'g6[image]' (or ask for svg, which needs nothing)",
                          **ids)
        run = dot.run_dot(source, ("svg",) if d.format == "svg" else ("svg", "png"), seconds=d.timeout_s)
        svg = dot.without_doctype(run.outputs["svg"])
        staged, written = stage_media(path, svg if d.format == "svg" else run.outputs["png"], overwrite=d.overwrite)
    except MediaError as exc:
        return refuse(d.op, exc.code, exc.reason, **ids)
    layout = svg.decode("utf-8", "replace")
    try:
        graph, counts = dot.graph_check(layout, nodes=expect[0] if expect else None,
                                        edges=expect[1] if expect else None, directed=d.directed)
        if d.format == "svg":
            base = inspect_svg(staged)
            extra = [graph, dot.loads_nothing(layout)]
        else:
            base = inspect_image(staged, formats=("PNG",))
            extra = [graph, _same_shape(layout, base.measured)] if base.passed else [graph]
    except Exception:
        discard(staged)
        raise
    if d.format == "png":  # the PNG's pixels are measured; what it draws is read from dot's SVG of the same layout
        extra = [c.model_copy(update={"detail": f"in dot's SVG of the same layout, not the PNG: {c.detail}"[:500]})
                 if c.name == "draws_the_graph" else c for c in extra]
    inspection = Inspection(inspector=f"{base.inspector}, with adapt_diagrams' graph check", kind=base.kind,
                            checks=(*base.checks, *extra), measured={**base.measured, **counts}, mime=base.mime)
    ran = {"kind": "graphviz_run", "program": "dot", "version": run.version, "argv": run.argv,
           "warnings": run.warnings, "screen": screened, "source_sha256": digest, "execution": run.execution}
    if not inspection.passed:
        failure = inspection.failure
        removed = discard(staged)
        return refuse(d.op, failure.code, f"render_dot: {failure.detail}",
                      evidence=[inspection.evidence(), {**ran, "file_removed": removed}], **ids)
    try:
        place_media(staged, path)
    except (OSError, MediaError) as exc:
        discard(staged)
        return refuse(d.op, BACKEND_FAILED, f"render_dot: the drawing passed but could not take its name: {exc}"[:500],
                      **ids)
    requested: dict[str, Any] = {"format": d.format, "from": "source" if expect is None else "nodes and edges"}
    if expect is not None:
        requested |= {"nodes": len(expect[0]), "edges": len(expect[1])}
    recipe = {"op": d.op, "format": d.format, "source_sha256": digest, "directed": d.directed,
              "direction": d.direction} if expect is not None else {"op": d.op, "format": d.format,
                                                                    "source_sha256": digest}
    artifact = media_artifact(path, inspection, Provenance(backend="graphviz", backend_version=run.version,
                                                           technique="dot", recipe=recipe, recipe_sha256=digest),
                              requested)
    fields: dict[str, Any] = {"dot_source": source} if expect is not None else {}
    drew = f"{counts['nodes_drawn']} nodes and {counts['edges_drawn']} edges" + (
        ", every one declared," if expect is not None else "")
    said = (f"an SVG drawn by Graphviz dot and inspected: it decodes, loads nothing from outside itself and draws {drew}"
            .rstrip(",") if d.format == "svg" else
            "a PNG drawn by Graphviz dot and inspected: it decodes, is not blank and has the proportions of dot's "
            f"layout; its {drew} were counted in dot's SVG of the same layout, not in the PNG's pixels")
    return answer(d.op, said,
                  artifact=artifact, evidence=[ran], written=written, nodes_drawn=counts["nodes_drawn"],
                  edges_drawn=counts["edges_drawn"], **fields, **ids)


@block_contract(
    purpose="write diagrams as Mermaid, Graphviz DOT, PlantUML or ASCII text, escaped and checked, and draw DOT with "
            "Graphviz into an inspected SVG or PNG",
    mitigates_failure_modes=("visual_check_failed",),
    verification_method="text: G6's own checks of what it wrote (escaping, unique names, every edge's ends declared, "
                        "line widths); render_dot: the media inspector and a check that dot's drawing holds every "
                        "declared node and every declared edge between its own two nodes",
)
@component_maturity(
    "beta",
    reason="dependency-free generators with per-dialect escaping; DOT drawn by the person's own Graphviz on screened "
           "source and inspected; qualified-draft at best: no layout or readability is judged, and Mermaid and "
           "PlantUML text is never parsed here",
)
class AdaptDiagramsBlock(AIBlock[DiagramInput, dict, dict]):
    """Write or draw a diagram and say what was checked about it. Never ``verified``."""

    def __init__(self, name: str = "adapt_diagrams", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: DiagramInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        try:
            result = _render(data) if data.op == "render_dot" else _text(data)
        except Exception as exc:  # noqa: BLE001 - an error is a refusal with its reason, never a crash or a diagram
            result = refuse(data.op, BACKEND_FAILED, f"{data.op} failed: {type(exc).__name__}: {exc}"[:500],
                            **_ids(data))
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


__all__ = ["SENSITIVE_OPS", "AdaptDiagramsBlock", "DiagramInput"]
