---
name: g6-adapt-diagrams
description: Use when you need a diagram (a flowchart, a sequence of messages, a graph, a component map, a box, tree or table in plain text) written as Mermaid, Graphviz DOT, PlantUML or ASCII that will not break on a quote or a keyword, or drawn by Graphviz into an SVG or PNG you can trust holds every node you declared.
---

# G6 adapt_diagrams

`g6.media.adapt_diagrams` writes diagrams from nodes and edges as Mermaid, Graphviz DOT, PlantUML or ASCII text, and has
Graphviz's `dot` draw DOT into an SVG or PNG in G6's media folder (`$G6_HOME/media`). Text is escaped for its dialect
and checked before it is returned; a drawing is inspected from disk before it is returned. It is an extension in the
media family. Its best state is `qualified-draft`.

## When to use it

- You (a coding agent) want Mermaid for a README or a pull request, and labels hold quotes, `#`, `<`, `|` or words
  such as `end`: the text comes back escaped, with node names chosen so no keyword can break it.
- You have a graph as nodes and edges and want it as DOT, PlantUML or a text box, tree or table.
- You need to see a diagram, not just write it: `render_dot` draws DOT (yours or one written from your nodes) as SVG or
  PNG, then checks the file decodes and draws every node and edge you declared.
- You want a typo in an edge (a node that does not exist) refused, not drawn as a stray box.

## When not to use it

- Charts of data (bars, lines, scatter): use the media family's charts block.
- Layout quality: nothing here judges whether a diagram is readable or well arranged; look at it.
- Checking Mermaid or PlantUML you wrote yourself: text ops write from nodes and edges; nothing here parses Mermaid or
  PlantUML.
- Diagrams that need HTML-like labels, images inside nodes or links: `render_dot` refuses them.

## Ops

- `mermaid_flowchart`: `nodes` (`id`, `label`, `shape`: box, round, stadium, circle, diamond, hexagon, cylinder,
  parallelogram), `edges` (`from`, `to`, `label`), `direction` (TB, TD, LR, BT, RL), `directed`, `title`.
- `mermaid_sequence`: nodes are participants in order, edges are messages in order; every message needs a label.
- `graphviz_digraph`: DOT text for a directed graph; it passes the same screen `render_dot` runs.
- `graphviz_graph`: DOT text for an undirected graph.
- `plantuml_sequence`: PlantUML participants and labelled messages.
- `plantuml_component`: PlantUML components and their links.
- `ascii_box`: a `title` and `lines` in a box; every line the same display width (wide characters count two).
- `ascii_tree`: a `tree` of `{"label", "children"}` drawn with `+--` and `` `-- ``.
- `ascii_table`: `headers` and `rows`, exactly one cell per header in every row; a cell is at most 200 characters,
  and a table at most 1,000,000 columns drawn in all (its lines times its width).
- `render_dot`: `source` (your DOT) or `nodes` and `edges` (with `directed`, `direction`, `title`), drawn as `format`
  `svg` or `png` under `name`; `overwrite` to replace a different file of that name; `timeout_s` (1 to 120, default
  30) bounds dot.

## Alone and composed

Alone over MCP: `g6_invoke("adapt_diagrams", "mermaid_flowchart", '{"nodes": [...], "edges": [...]}')` and paste
`result.text`. `render_dot` is a sensitive op, because it runs Graphviz's `dot` on your computer: over MCP it runs
only when the person who runs the server adds `adapt_diagrams.render_dot` to `G6_MCP_SENSITIVE`; then open
`result.artifact.path`. The text ops start nothing and are never refused. From Python:
`get_registry().invoke("adapt_diagrams", op, params)` runs every op, or use the generators in
`g6.media.adapt_diagrams.generators`.

Composed:

- Write with `graphviz_digraph`, adjust the DOT, then draw it with `render_dot` `source`.
- A drawn file's `artifact` (path, SHA-256, checks, provenance with dot's version and the source's hash) is what to
  keep beside a document that embeds it.

## Failure modes it catches

- `visual_check_failed` (an image, video or 3D output that is blank, corrupt or wrong, reported as done): a drawing
  with nothing in it is refused with `G6_E_MEDIA_BLANK`; one that leaves out a node or an edge you declared, draws one
  you did not, or joins an edge between other nodes than you declared (direction and repeats counted), with
  `G6_E_MEDIA_DIAGRAM_MISMATCH`; a file that does not parse with `G6_E_MEDIA_UNDECODABLE`; an SVG that
  would load or run something outside itself with `G6_E_MEDIA_LOADS_EXTERNAL`. The failed file is removed, and a
  file of that name it would have replaced with `overwrite` is left as it was.
- Text that would not parse: an edge to a node you did not declare, a node id used twice, a sequence message with no
  label, a PlantUML label or title holding anything but letters, digits, spaces and `. , : ; ! ? ' ( ) + - * / = @ #
  & _ |` (or holding a doubled `*`, `/`, `_`, `-` or `=`, or `&#`), which PlantUML could read as markup, a
  preprocessor call (`%`, `$`) or a link (`[[ ]]`): refused with `G6_E_MEDIA_INPUT_INVALID`, naming what is wrong.
- A DOT syntax error is dot's own message under `G6_E_MEDIA_DIAGRAM_DOES_NOT_PARSE`, never mistaken for a missing
  program (`G6_E_OUTSIDE_PROGRAM_MISSING`, with the install steps).

## Reading the result

- Text: `qualified-draft` with `warning_card.code` `G6_E_MEDIA_TEXT_ONLY`; `text`, `dialect` and `checks` (what G6
  checked). No program parsed or drew it.
- A drawing: `qualified-draft` with `G6_E_MEDIA_INSPECTED`; `artifact` holds the path, MIME type read from the bytes,
  size, SHA-256 and each check; `nodes_drawn` and `edges_drawn`; `dot_source` when G6 wrote the DOT. A PNG is checked
  for decoding, not being blank and having the proportions of dot's layout; its nodes and edges are counted in dot's
  SVG of the same layout, not in the PNG's pixels, and the message says so.
- `render_dot` source is screened first. Refused with `G6_E_MEDIA_INPUT_INVALID`: an HTML-like label (`<...>`), any
  attribute that is not a layout or drawing one (`image`, `shapefile`, `fontpath`, `imagepath`, `stylesheet`, `URL`,
  `href`, `size` and `dpi` among them), the `epsf` and `custom` shapes, a font name that looks like a path, a number
  outside its bounds, and a run of backslashes before a quote.

## Blind spots

- Mermaid and PlantUML text is never parsed here: the escaping follows each dialect's documented rules, and only the
  program that renders it can confirm it.
- The drawing check counts what dot drew; it does not judge layout, overlap or readability.
- Source you give is checked for at least one node or edge drawn, not for the nodes you meant.
- dot runs as you, bounded by a deadline and a memory cap on Windows (elsewhere the deadline is the bound); the screen
  is an allow-list of attributes, not a sandbox.
- Files stay in the media folder until you delete them.

## What it needs

- Nothing for the text ops.
- `render_dot` needs Graphviz (EPL-1.0), a program you install: `winget install graphviz`, `brew install graphviz`
  or `sudo apt install graphviz`, then `dot` on PATH or `G6_GRAPHVIZ_DOT` naming it. G6 never installs it; without it
  `render_dot` refuses with `G6_E_OUTSIDE_PROGRAM_MISSING` and the install steps.
- A PNG needs `pip install "g6[image]"` (Pillow, to inspect it). Without it a PNG is refused with
  `G6_E_MEDIA_EXTRA_MISSING` before dot runs; SVG needs nothing.

## Examples

A flowchart whose labels would break unescaped Mermaid.

```g6-example op=mermaid_flowchart
{"nodes": [{"id": "start", "label": "Read \"config\""}, {"id": "end", "label": "x < 5?", "shape": "diamond"}], "edges": [{"from": "start", "to": "end", "label": "a | b"}], "direction": "LR"}
```

```g6-example op=mermaid_sequence
{"nodes": [{"id": "agent", "label": "Agent"}, {"id": "g6", "label": "G6"}], "edges": [{"from": "agent", "to": "g6", "label": "check this; then reply"}, {"from": "g6", "to": "agent", "label": "qualified-draft"}]}
```

```g6-example op=graphviz_digraph
{"nodes": [{"id": "a", "label": "Plan"}, {"id": "b", "label": "Build"}], "edges": [{"from": "a", "to": "b"}], "title": "Release"}
```

```g6-example op=graphviz_graph
{"nodes": [{"id": "x"}, {"id": "y"}], "edges": [{"from": "x", "to": "y", "label": "linked"}]}
```

```g6-example op=plantuml_sequence
{"nodes": [{"id": "c", "label": "Client"}, {"id": "s", "label": "Server"}], "edges": [{"from": "c", "to": "s", "label": "GET /status"}]}
```

```g6-example op=plantuml_component
{"nodes": [{"id": "web", "label": "Web app"}, {"id": "db", "label": "Database"}], "edges": [{"from": "web", "to": "db", "label": "reads"}]}
```

```g6-example op=ascii_box
{"title": "Next steps", "lines": ["1. run the tests", "2. read the diff"]}
```

```g6-example op=ascii_tree
{"tree": {"label": "src", "children": [{"label": "g6", "children": [{"label": "media"}]}, {"label": "tests"}]}}
```

```g6-example op=ascii_table
{"headers": ["check", "passed"], "rows": [["parses", true], ["draws every node", true]]}
```

Draw a graph with Graphviz (a sensitive op: refused over MCP unless the server allows it, and refused with the install
steps where dot is not installed).

```g6-example op=render_dot
{"nodes": [{"id": "a", "label": "Start"}, {"id": "b", "label": "Done"}], "edges": [{"from": "a", "to": "b"}], "format": "svg", "name": "start-done"}
```

From Python: an edge to a node that does not exist is refused, and a source that names an image is refused before dot
starts.

```python
from g6.core.registry import get_registry

reg = get_registry()
text = reg.invoke("adapt_diagrams", "mermaid_flowchart", {
    "nodes": [{"id": "end", "label": 'He said "go"'}], "edges": []}).unwrap()
assert text["text"].splitlines()[1] == '    n1["He said #34;go#34;"]'
typo = reg.invoke("adapt_diagrams", "graphviz_digraph", {
    "nodes": [{"id": "a"}], "edges": [{"from": "a", "to": "b"}]}).unwrap()
assert typo["completion_state"] == "blocked-escalated" and "'b'" in typo["reason"]
image = reg.invoke("adapt_diagrams", "render_dot", {"source": 'digraph { a [image="secret.png"] }'}).unwrap()
assert image["code"] == "G6_E_MEDIA_INPUT_INVALID" and "'image'" in image["reason"]
```

## The four open problems

- **Learning signals**: each refusal names what failed (the undeclared node, the refused attribute, the node dot left
  out, dot's own message). Not yet: nothing records refusals per dialect or learns from them.
- **Evaluators the model cannot influence**: the drawing is read from disk by the media inspector and the graph check
  (node and edge groups in dot's SVG); a caller cannot pass or skip a check, and no model is consulted.
- **Transfer by failure mode**: the same inspector and codes guard every media block, so "blank" and "does not decode"
  mean the same here as for pictures.
- **Enough held-out cases**: Not yet: there is no fixed set of diagrams with stored drawings; the tests check exact
  escaped text, refused sources and a stand-in dot's drawings, and the real dot only where it is installed.
