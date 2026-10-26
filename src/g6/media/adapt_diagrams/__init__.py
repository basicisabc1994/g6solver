# SPDX-License-Identifier: Apache-2.0
"""g6.media.adapt_diagrams — write checked Mermaid, DOT, PlantUML or ASCII diagrams, and draw DOT with Graphviz.

An extension in the media family. The text ops need nothing installed; ``render_dot`` needs Graphviz's ``dot``, a
program you install (``G6_GRAPHVIZ_DOT`` or ``dot`` on PATH), and the ``image`` extra only for PNG.

    from g6.core.registry import get_registry
    out = get_registry().invoke("adapt_diagrams", "mermaid_flowchart", {
        "nodes": [{"id": "a", "label": "Start"}, {"id": "b", "label": "Say \\"hi\\""}],
        "edges": [{"from": "a", "to": "b"}]}).unwrap()
    out["text"], out["completion_state"]   # Mermaid with its labels escaped, "qualified-draft"

What it does:

* six generators from nodes and edges (Mermaid flowchart and sequence, Graphviz DOT directed and undirected, PlantUML
  sequence and component) and three ASCII drawings (box, tree, table). Each refuses what would not parse (a name used
  twice, an edge to an undeclared node, a label the dialect cannot hold) instead of writing it, and escapes the rest;
  Mermaid and PlantUML name nodes themselves, so a caller's name never collides with a keyword;
* ``render_dot``: your DOT source (screened first: no attribute that reads a file, no HTML-like label, numbers within
  bounds) or DOT G6 writes, drawn by dot as a bounded child process into G6's media folder, then inspected from disk:
  the file decodes, references nothing outside itself, and draws every declared node and edge (from source: at least
  one). A syntax error is told apart from a missing program. It runs a program on your computer, so it is a sensitive
  op: over MCP it runs only when the person who runs the server lists it in ``G6_MCP_SENSITIVE``. Results are
  ``qualified-draft``; text no program parsed carries the ``G6_E_MEDIA_TEXT_ONLY`` card.

Not ported from the archive's adapt_diagrams, and why:

* the ``verified`` label on an echo of the caller's own source, and the ops that only echoed it (the Mermaid, PlantUML,
  Plotly and D3 "render source" ops);
* the D3, Plotly and dashboard pages, which loaded scripts from CDNs when opened: charts belong to the charts block;
* Mermaid class, state, ER and Gantt diagrams and PlantUML activity and class diagrams: their free-text members went in
  unescaped, and the activity diagram mixed two PlantUML syntaxes; the SVG compose op;
* the op-recommending model planner, the templates, the store in the home folder, the FastMCP server and the scaffold;
* base64 images in the reply: a drawing is a file in the media folder.
"""
from g6.media.adapt_diagrams.block import SENSITIVE_OPS, AdaptDiagramsBlock, DiagramInput
from g6.media.adapt_diagrams.dot import GRAPHVIZ
from g6.media.adapt_diagrams.generators import Edge, Node, TreeNode

__g6_component__ = True
__g6_block__ = "AdaptDiagramsBlock"
__g6_family__ = "media"
# render_dot runs Graphviz's dot, a program on your computer (screened source, bounded, writing only into G6's folders);
# the text ops start nothing. No op reaches the network or spends money.
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("visual_check_failed",)
__g6_introduces__: tuple[str, ...] = ()
__g6_outside__ = (GRAPHVIZ,)
__g6_extras__: dict[str, tuple[str, ...]] = {"image": ("PIL",)}  # only to inspect a PNG; SVG and text need nothing

__all__ = ["GRAPHVIZ", "SENSITIVE_OPS", "AdaptDiagramsBlock", "DiagramInput", "Edge", "Node", "TreeNode"]
