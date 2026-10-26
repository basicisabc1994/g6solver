# SPDX-License-Identifier: Apache-2.0
"""Diagram text from nodes and edges, escaped for its dialect: Mermaid, PlantUML, Graphviz DOT and ASCII.

Nothing here runs a program. Each generator checks what it is given before it writes a line (node names unique, every
edge's two ends declared, labels a dialect can hold) and raises :class:`~g6.media._types.MediaError` with
``G6_E_MEDIA_INPUT_INVALID`` otherwise, so a diagram that would not parse is refused rather than returned.

* Mermaid and PlantUML name nodes ``n1``, ``n2`` … (participants ``p1`` …, components ``c1`` …) and carry the caller's
  names as labels, so no name can collide with a keyword (``end``, ``graph``) or break the syntax. Mermaid labels are
  written with its numeric entity codes for ``" # & < > `` ` (``|`` and ``;`` where they end a label), so they are drawn
  as written. PlantUML has no escape this package could check without PlantUML itself, so its labels and title are held
  to an allow-list: letters, digits, spaces and plain punctuation (:data:`PLANTUML_PUNCTUATION`), without a doubled
  Creole mark or an entity; anything else (markup, ``%`` and ``$`` of its preprocessor, ``[[`` of a link) is refused,
  naming it.
* DOT quotes every name and label (``\\`` and ``"`` escaped, a newline as ``\\n``); a name may not hold a backslash, and
  a label may not end with one, because Graphviz versions read a run of backslashes before a closing quote differently.
* ASCII drawings measure each character's display width (wide characters count two, combining marks none), so every
  line of a box or table is the same width. A table cell is at most :data:`MAX_CELL` characters, and a table is at most
  :data:`MAX_TABLE_AREA` columns drawn in all (its lines times its width), checked before any line is padded.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from g6.media._types import INPUT_INVALID, MediaError

Shape = Literal["box", "round", "stadium", "circle", "diamond", "hexagon", "cylinder", "parallelogram"]
Direction = Literal["TB", "TD", "LR", "BT", "RL"]
Cell = str | bool | int | float | None

MAX_NODES = 500
MAX_EDGES = 2000
#: A table cell holds at most this many characters (as a header does), and a table draws at most this many columns in
#: all, its lines times its width, so no request can make G6 build a drawing far larger than itself.
MAX_CELL = 200
MAX_TABLE_AREA = 1_000_000
_CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f\u2028\u2029]")
_NEWLINE_TOO = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def _plain(value: str, what: str, *, newline: bool) -> str:
    if (_CONTROL if newline else _NEWLINE_TOO).search(value):
        raise ValueError(f"{what} may not hold control characters" + ("" if newline else " or line breaks"))
    return value


class Node(BaseModel):
    """A node: its ``id`` (how edges name it), its ``label`` (what is drawn; the id when empty) and its ``shape``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=100)
    label: str = Field("", max_length=200)
    shape: Shape = "box"

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if "\\" in value:
            raise ValueError("a node id may not hold a backslash")
        return _plain(value, "a node id", newline=False)

    @field_validator("label")
    @classmethod
    def _label(cls, value: str) -> str:
        return _plain(value, "a label", newline=True)

    @property
    def shown(self) -> str:
        return self.label or self.id


class Edge(BaseModel):
    """An edge (or a message, in a sequence diagram) ``from`` one node id ``to`` another, with an optional label."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    source: str = Field(alias="from", min_length=1, max_length=100)
    target: str = Field(alias="to", min_length=1, max_length=100)
    label: str = Field("", max_length=200)

    @field_validator("label")
    @classmethod
    def _label(cls, value: str) -> str:
        return _plain(value, "a label", newline=True)


class TreeNode(BaseModel):
    """One item of an ASCII tree and the items under it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1, max_length=200)
    children: list[TreeNode] = Field(default_factory=list, max_length=200)

    @field_validator("label")
    @classmethod
    def _label(cls, value: str) -> str:
        return _plain(value, "a tree label", newline=False)

    def count(self) -> int:
        return 1 + sum(c.count() for c in self.children)

    def depth(self) -> int:
        return 1 + max((c.depth() for c in self.children), default=0)

    @model_validator(mode="after")
    def _bounded(self) -> TreeNode:
        if self.depth() > 32 or self.count() > 1000:
            raise ValueError("a tree holds at most 1000 items, at most 32 deep")
        return self


def _refuse(reason: str) -> MediaError:
    return MediaError(INPUT_INVALID, reason)


def _checked(value: str, what: str) -> str:
    try:
        return _plain(value, what, newline=False)
    except ValueError as exc:
        raise _refuse(str(exc)) from None


def check_graph(nodes: list[Node], edges: list[Edge]) -> None:
    """Refuse a graph with no nodes, too many, a name used twice, or an edge to or from an undeclared node."""
    if not nodes:
        raise _refuse("a diagram needs at least one node")
    if len(nodes) > MAX_NODES or len(edges) > MAX_EDGES:
        raise _refuse(f"a diagram holds at most {MAX_NODES} nodes and {MAX_EDGES} edges")
    seen: set[str] = set()
    repeated: set[str] = set()
    for n in nodes:
        (repeated if n.id in seen else seen).add(n.id)
    twice = sorted(repeated)
    if twice:
        raise _refuse(f"node ids must be unique; used more than once: {', '.join(map(repr, twice[:5]))}")
    missing = sorted({end for e in edges for end in (e.source, e.target) if end not in seen})
    if missing:
        raise _refuse(f"every edge joins declared nodes; not declared: {', '.join(map(repr, missing[:5]))}"
                      + (f" and {len(missing) - 5} more" if len(missing) > 5 else ""))


# -- Mermaid -----------------------------------------------------------------------------------------------------------

_MERMAID_SHAPES = {"box": ("[", "]"), "round": ("(", ")"), "stadium": ("([", "])"), "circle": ("((", "))"),
                   "diamond": ("{", "}"), "hexagon": ("{{", "}}"), "cylinder": ("[(", ")]"),
                   "parallelogram": ("[/", "/]")}
_MERMAID_ALWAYS = '"#&<>`'


def mermaid_text(text: str, also: str = "") -> str:
    """``text`` as Mermaid draws it literally: ``" # & < > `` ` and ``also`` as numeric entity codes (``#34;`` …),
    a line break as ``<br>``."""
    special = _MERMAID_ALWAYS + also
    return "".join("<br>" if ch == "\n" else f"#{ord(ch)};" if ch in special else ch for ch in text)


def _front_matter(title: str) -> list[str]:
    return ["---", f"title: {json.dumps(title, ensure_ascii=False)}", "---"] if title else []


def mermaid_flowchart(nodes: list[Node], edges: list[Edge], *, direction: Direction = "TB", directed: bool = True,
                      title: str = "") -> str:
    check_graph(nodes, edges)
    ids = {n.id: f"n{i}" for i, n in enumerate(nodes, 1)}
    lines = [*_front_matter(title), f"flowchart {direction}"]
    for n in nodes:
        start, end = _MERMAID_SHAPES[n.shape]
        lines.append(f'    {ids[n.id]}{start}"{mermaid_text(n.shown)}"{end}')
    link = "-->" if directed else "---"
    for e in edges:
        label = f'|"{mermaid_text(e.label, "|")}"|' if e.label else ""
        lines.append(f"    {ids[e.source]} {link}{label} {ids[e.target]}")
    return "\n".join(lines) + "\n"


def mermaid_sequence(nodes: list[Node], edges: list[Edge], *, title: str = "") -> str:
    """Participants in the order given, then each message in order; every message needs a label."""
    check_graph(nodes, edges)
    unlabelled = [i for i, e in enumerate(edges, 1) if not e.label.strip()]
    if unlabelled:
        raise _refuse(f"every message in a sequence diagram needs a label (message {unlabelled[0]} has none)")
    ids = {n.id: f"p{i}" for i, n in enumerate(nodes, 1)}
    lines = [*_front_matter(title), "sequenceDiagram"]
    for n in nodes:
        lines.append(f"    participant {ids[n.id]} as {mermaid_text(n.shown, ';%')}")
    for e in edges:
        lines.append(f"    {ids[e.source]}->>{ids[e.target]}: {mermaid_text(e.label, ';%')}")
    return "\n".join(lines) + "\n"


# -- PlantUML ----------------------------------------------------------------------------------------------------------

#: The characters a PlantUML label or title may hold beside letters, digits and line breaks. PlantUML reads much else as
#: markup (Creole), the end of a quoted label, a preprocessor call or variable (``%``, ``$``), a link (``[[ ]]``), an
#: entity, an icon or a colour (``<``), and has no escape G6 could check without PlantUML itself.
PLANTUML_PUNCTUATION = frozenset(" .,:;!?'()+-*/=@#&_|")
#: What PlantUML reads as markup though each character is allowed: Creole's doubled marks, and an entity.
_PLANTUML_REFUSED = ("**", "//", "__", "--", "==", "&#")


def _plantuml_text(text: str, what: str) -> str:
    found = next((ch for ch in text if ch != "\n" and not ch.isalnum() and ch not in PLANTUML_PUNCTUATION), None)
    if found is None:
        found = next((m for m in _PLANTUML_REFUSED if m in text), None)
    if found is not None:
        raise _refuse(f"{what} {text[:60]!r} holds {found!r}: PlantUML text from G6 holds only letters, digits, spaces "
                      "and . , : ; ! ? ' ( ) + - * / = @ # & _ |, without a doubled * / _ - = or '&#', because PlantUML "
                      "reads the rest as markup, a preprocessor call or a link and G6 cannot escape it for PlantUML; "
                      "leave it out or use Mermaid or DOT")
    return text.replace("\n", "\\n")


def _plantuml_head(title: str) -> list[str]:
    return ["@startuml", *([f"title {_plantuml_text(title, 'the title')}"] if title else [])]


def plantuml_sequence(nodes: list[Node], edges: list[Edge], *, title: str = "") -> str:
    check_graph(nodes, edges)
    unlabelled = [i for i, e in enumerate(edges, 1) if not e.label.strip()]
    if unlabelled:
        raise _refuse(f"every message in a sequence diagram needs a label (message {unlabelled[0]} has none)")
    ids = {n.id: f"p{i}" for i, n in enumerate(nodes, 1)}
    lines = _plantuml_head(title)
    for n in nodes:
        lines.append(f'participant "{_plantuml_text(n.shown, "the label")}" as {ids[n.id]}')
    for e in edges:
        lines.append(f"{ids[e.source]} -> {ids[e.target]} : {_plantuml_text(e.label, 'the message')}")
    return "\n".join([*lines, "@enduml"]) + "\n"


def plantuml_component(nodes: list[Node], edges: list[Edge], *, title: str = "") -> str:
    check_graph(nodes, edges)
    ids = {n.id: f"c{i}" for i, n in enumerate(nodes, 1)}
    lines = _plantuml_head(title)
    for n in nodes:
        lines.append(f'component "{_plantuml_text(n.shown, "the label")}" as {ids[n.id]}')
    for e in edges:
        label = f" : {_plantuml_text(e.label, 'the label')}" if e.label else ""
        lines.append(f"{ids[e.source]} --> {ids[e.target]}{label}")
    return "\n".join([*lines, "@enduml"]) + "\n"


# -- Graphviz DOT ------------------------------------------------------------------------------------------------------

_DOT_SHAPES = {"box": "shape=box", "round": "shape=ellipse", "stadium": 'shape=box, style="rounded"',
               "circle": "shape=circle", "diamond": "shape=diamond", "hexagon": "shape=hexagon",
               "cylinder": "shape=cylinder", "parallelogram": "shape=parallelogram"}
_RANKDIR = {"TB": "TB", "TD": "TB", "LR": "LR", "BT": "BT", "RL": "RL"}


def dot_quote(text: str, what: str = "a label") -> str:
    """``text`` as a quoted DOT string: ``\\`` and ``"`` escaped, a line break as ``\\n``."""
    if text.endswith("\\"):
        raise _refuse(f"{what} may not end with a backslash: Graphviz versions read it differently")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def dot(nodes: list[Node], edges: list[Edge], *, directed: bool = True, direction: Direction = "TB",
        title: str = "") -> str:
    check_graph(nodes, edges)
    lines = [f"{'digraph' if directed else 'graph'} G {{",
             f"    graph [rankdir={_RANKDIR[direction]}"
             + (f", label={dot_quote(title, 'the title')}, labelloc=t" if title else "") + "];"]
    for n in nodes:
        lines.append(f"    {dot_quote(n.id, 'a node id')} [label={dot_quote(n.shown)}, {_DOT_SHAPES[n.shape]}];")
    link = "->" if directed else "--"
    for e in edges:
        label = f" [label={dot_quote(e.label)}]" if e.label else ""
        lines.append(f"    {dot_quote(e.source, 'a node id')} {link} {dot_quote(e.target, 'a node id')}{label};")
    return "\n".join([*lines, "}"]) + "\n"


# -- ASCII -------------------------------------------------------------------------------------------------------------

def display_width(text: str) -> int:
    """How many monospaced columns ``text`` takes: wide and full-width characters two, combining marks none."""
    return sum(0 if unicodedata.combining(ch) else 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * (width - display_width(text))


def ascii_box(title: str, lines: list[str]) -> str:
    if not title and not lines:
        raise _refuse("a box needs a title or some lines")
    for value in [title, *lines]:
        _checked(value, "a box line")
    width = max(display_width(t) for t in [title, *lines])
    rule = "+" + "-" * (width + 2) + "+"
    out = [rule]
    if title:
        out += [f"| {_pad(title, width)} |", rule]
    if lines:
        out += [f"| {_pad(line, width)} |" for line in lines] + [rule]
    return "\n".join(out) + "\n"


def ascii_tree(tree: TreeNode) -> str:
    out = [tree.label]

    def walk(node: TreeNode, prefix: str) -> None:
        for i, child in enumerate(node.children):
            last = i == len(node.children) - 1
            out.append(f"{prefix}{'`-- ' if last else '+-- '}{child.label}")
            walk(child, prefix + ("    " if last else "|   "))

    walk(tree, "")
    return "\n".join(out) + "\n"


def cell_text(value: Cell) -> str:
    """A table cell as written: ``None`` empty, booleans ``true`` and ``false``, a float in Python's shortest form."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return repr(value) if isinstance(value, float) else str(value)


def ascii_table(headers: list[str], rows: list[list[Cell]]) -> str:
    if not headers:
        raise _refuse("a table needs at least one header")
    short = next((i for i, row in enumerate(rows, 1) if len(row) != len(headers)), None)
    if short is not None:
        raise _refuse(f"row {short} has {len(rows[short - 1])} cells and the table has {len(headers)} headers; every "
                      "row needs exactly one cell per header (give \"\" or null for an empty one)")
    text = [[cell_text(c) for c in row] for row in rows]
    for r, row in enumerate([headers, *text]):
        for c, value in enumerate(row, 1):
            if len(value) > MAX_CELL:
                where = f"header {c}" if r == 0 else f"row {r}, column {c}"
                raise _refuse(f"a table cell is at most {MAX_CELL} characters; {where} has {len(value)}")
            _checked(value, "a table cell")
    widths = [max(display_width(r[i]) for r in [headers, *text]) for i in range(len(headers))]
    width, lines = sum(widths) + 3 * len(widths) + 1, len(text) + 4
    if width * lines > MAX_TABLE_AREA:
        raise _refuse(f"the table would be {lines} lines of {width} columns, {width * lines:,} in all; a table draws at "
                      f"most {MAX_TABLE_AREA:,}: shorten the cells or split the rows")
    rule = "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    def line(cells: list[str]) -> str:
        return "|" + "|".join(f" {_pad(c, w)} " for c, w in zip(cells, widths)) + "|"

    return "\n".join([rule, line(headers), rule, *(line(r) for r in text), rule]) + "\n"


__all__ = ["MAX_CELL", "MAX_EDGES", "MAX_NODES", "MAX_TABLE_AREA", "PLANTUML_PUNCTUATION", "Cell", "Direction", "Edge",
           "Node", "Shape", "TreeNode", "ascii_box", "ascii_table", "ascii_tree", "cell_text", "check_graph",
           "display_width", "dot", "dot_quote", "mermaid_flowchart", "mermaid_sequence", "mermaid_text",
           "plantuml_component", "plantuml_sequence"]
