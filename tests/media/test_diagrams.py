# SPDX-License-Identifier: Apache-2.0
"""adapt_diagrams: diagram text is escaped for its dialect and refused when it would not parse (D1,
ADAPT_DIAGRAMS-02); nothing is ``verified``, and text no program parsed says so (ADAPT_DIAGRAMS-01); DOT source is data,
screened before dot starts, and dot runs as a bounded child process with a fixed argument list and a clean
environment; its drawing is inspected from disk and refused when it is blank or misses a declared node (D1); a syntax
error is told apart from a missing program (ADAPT_DIAGRAMS-03); errors tighten; it is reachable over MCP; without
Pillow a PNG says it cannot be inspected.

dot is never assumed: the tests start a stand-in (``stand_in_dot.py``, a G6-owned script) through the same
child-process path, and the one test that needs Graphviz itself skips with its reason when it is absent."""
from __future__ import annotations

import ast
import importlib.metadata
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

import g6.media.adapt_diagrams as package
from g6.core import discovery
from g6.core.failure_modes import FailureModeRegistry
from g6.core.registry import get_registry
from g6.invariants import check_envelope
from g6.mcp import describe_reply, invoke_reply
from g6.media import _inspect
from g6.media._types import (
    BACKEND_FAILED,
    BLANK,
    EXTRA_MISSING,
    INPUT_INVALID,
    NO_OUTPUT,
    OUTSIDE_MISSING,
    TEXT_ONLY,
    media_root,
)
from g6.media.adapt_diagrams import AdaptDiagramsBlock, block, dot, generators

SRC = Path(package.__file__).parent
STAND_IN = Path(__file__).with_name("stand_in_dot.py")
NODES = [{"id": "a", "label": "Start"}, {"id": "b", "label": "Check"}, {"id": "c", "label": "Done"}]
EDGES = [{"from": "a", "to": "b"}, {"from": "b", "to": "c", "label": "ok"}]


def _invoke(op: str, **params):
    result = get_registry().invoke("adapt_diagrams", op, params)
    assert result.__class__.__name__ == "Success", result
    return result.unwrap()


def _refused(op: str, **params) -> str:
    result = get_registry().invoke("adapt_diagrams", op, params)
    assert result.__class__.__name__ == "Failure", result
    return str(result.failure())


def _media_files() -> set[str]:
    return {p.name for p in media_root().iterdir()} if media_root().exists() else set()


class StandIn:
    """The stand-in dot: how it is told to behave, and what it saw each time it started."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def behave(self, mode: str = "normal") -> None:
        (self.folder / "behaviour.json").write_text(json.dumps({"mode": mode}), encoding="utf-8")

    def calls(self) -> list[dict]:
        log = self.folder / "calls.jsonl"
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []

    def runs(self) -> list[dict]:
        return [c for c in self.calls() if c["argv"] != ["-V"]]


@pytest.fixture
def stand_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StandIn:
    folder = tmp_path / "bin"
    folder.mkdir()
    script = folder / "dot_stand_in.py"
    shutil.copyfile(STAND_IN, script)
    if os.name == "nt":
        launcher = folder / "dot.cmd"
        launcher.write_text(f'@echo off\r\n"{sys.executable}" -I "{script}" %*\r\n', encoding="utf-8")
    else:
        launcher = folder / "dot"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{script}" "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)
    monkeypatch.setenv(dot.DOT_ENV, str(launcher))
    found = StandIn(folder)
    found.behave()
    return found


# -- D1 and ADAPT_DIAGRAMS-02: a label the dialect would misread is escaped; what would not parse is refused ----------

TRICKY = [{"id": "end", "label": 'Say "hi" & <b>bye</b>'}, {"id": "my node", "label": "a # b `c` #quot;"},
          {"id": "x"}]
TRICKY_EDGES = [{"from": "end", "to": "my node", "label": "x | y"}, {"from": "my node", "to": "x"}]


def test_d1_mermaid_labels_are_written_as_entity_codes_and_names_never_reach_the_text() -> None:
    out = _invoke("mermaid_flowchart", nodes=TRICKY, edges=TRICKY_EDGES, title="Plan: v2 # draft")
    assert out["completion_state"] == "qualified-draft" and out["warning_card"]["code"] == TEXT_ONLY
    assert out["text"] == (
        '---\ntitle: "Plan: v2 # draft"\n---\n'
        "flowchart TB\n"
        '    n1["Say #34;hi#34; #38; #60;b#62;bye#60;/b#62;"]\n'
        '    n2["a #35; b #96;c#96; #35;quot;"]\n'           # "#quot;" stays text, never a decoded quote
        '    n3["x"]\n'
        '    n1 -->|"x #124; y"| n2\n'
        "    n2 --> n3\n")


def test_d1_a_mermaid_sequence_escapes_its_statement_separators() -> None:
    out = _invoke("mermaid_sequence", nodes=[{"id": "alice", "label": "Alice; the first"}, {"id": "bob"}],
                  edges=[{"from": "alice", "to": "bob", "label": "50% of #1\nthen more"}])
    assert out["text"] == ("sequenceDiagram\n    participant p1 as Alice#59; the first\n    participant p2 as bob\n"
                           "    p1->>p2: 50#37; of #35;1<br>then more\n")


@pytest.mark.parametrize("op", block._GRAPH_OPS)
def test_d1_an_edge_to_an_undeclared_node_is_refused_by_every_graph_dialect(op: str) -> None:
    out = _invoke(op, nodes=NODES, edges=[*EDGES, {"from": "c", "to": "ghost", "label": "lost"}])
    assert out["completion_state"] == "blocked-escalated" and out["code"] == INPUT_INVALID
    assert "'ghost'" in out["reason"] and "text" not in out
    twice = _invoke(op, nodes=[*NODES, {"id": "a"}], edges=EDGES)
    assert twice["code"] == INPUT_INVALID and "'a'" in twice["reason"]


def test_plantuml_refuses_a_label_it_would_read_as_markup_and_names_its_own_nodes() -> None:
    bad = _invoke("plantuml_sequence", nodes=[{"id": "a", "label": 'He said "no"'}, {"id": "b"}],
                  edges=[{"from": "a", "to": "b", "label": "hello"}])
    assert bad["code"] == INPUT_INVALID and "'\"'" in bad["reason"]
    assert _invoke("plantuml_component", nodes=[{"id": "x", "label": "a **bold** claim"}])["code"] == INPUT_INVALID
    good = _invoke("plantuml_component", nodes=[{"id": "end", "label": "Web app"}, {"id": "db", "label": "Database"}],
                   edges=[{"from": "end", "to": "db", "label": "reads"}])
    assert good["text"] == ('@startuml\ncomponent "Web app" as c1\ncomponent "Database" as c2\nc1 --> c2 : reads\n'
                            "@enduml\n")
    seq = _invoke("plantuml_sequence", nodes=[{"id": "a", "label": "Client"}, {"id": "b", "label": "Server"}],
                  edges=[{"from": "a", "to": "b", "label": "GET /\nthen wait"}], title="Requests")
    assert seq["text"] == ('@startuml\ntitle Requests\nparticipant "Client" as p1\nparticipant "Server" as p2\n'
                           "p1 -> p2 : GET /\\nthen wait\n@enduml\n")


@pytest.mark.parametrize("text", ['%getenv("HOME")', "%date()", "%getenv(HOME)", "[[x]]", "[[https://x.test go]]",
                                  "$a", "cost: $5", "&#64;"])
def test_plantuml_refuses_its_preprocessor_and_links_in_every_label_and_the_title(text: str) -> None:
    two = [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]
    cases = [("plantuml_sequence", {"nodes": [{"id": "a", "label": text}, {"id": "b"}],
                                    "edges": [{"from": "a", "to": "b", "label": "go"}]}),
             ("plantuml_sequence", {"nodes": [{"id": text}, {"id": "b"}], "edges": [{"from": text, "to": "b",
                                                                                      "label": "go"}]}),
             ("plantuml_sequence", {"nodes": two, "edges": [{"from": "a", "to": "b", "label": text}]}),
             ("plantuml_sequence", {"nodes": two, "edges": [{"from": "a", "to": "b", "label": "go"}], "title": text}),
             ("plantuml_component", {"nodes": [{"id": "a", "label": text}]}),
             ("plantuml_component", {"nodes": two, "edges": [{"from": "a", "to": "b", "label": text}]}),
             ("plantuml_component", {"nodes": two, "title": text})]
    for op, params in cases:
        out = _invoke(op, **params)
        assert out["code"] == INPUT_INVALID and "text" not in out, (op, params, out.get("text"))
        assert "PlantUML" in out["reason"]


def test_plantuml_keeps_ordinary_punctuation() -> None:
    out = _invoke("plantuml_sequence", nodes=[{"id": "a", "label": "R&D (team 2)"}, {"id": "b", "label": "Résumé"}],
                  edges=[{"from": "a", "to": "b", "label": "GET /status?id=7; don't wait, ok! #1 @ops a+b_c|d"}],
                  title="Q3: plan.")
    assert out["completion_state"] == "qualified-draft", out.get("reason")
    assert "p1 -> p2 : GET /status?id=7; don't wait, ok! #1 @ops a+b_c|d\n" in out["text"]


def test_a_sequence_message_needs_a_label() -> None:
    for op in ("mermaid_sequence", "plantuml_sequence"):
        out = _invoke(op, nodes=NODES, edges=[{"from": "a", "to": "b"}])
        assert out["code"] == INPUT_INVALID and "label" in out["reason"]


def test_dot_quotes_every_name_and_label_and_its_text_passes_the_screen() -> None:
    out = _invoke("graphviz_digraph", nodes=[{"id": "a b", "label": 'say "hi"\nnow', "shape": "stadium"},
                                             {"id": "c"}],
                  edges=[{"from": "a b", "to": "c", "label": "x\\y"}], direction="LR", title="T")
    assert out["text"] == ('digraph G {\n    graph [rankdir=LR, label="T", labelloc=t];\n'
                           '    "a b" [label="say \\"hi\\"\\nnow", shape=box, style="rounded"];\n'
                           '    "c" [label="c", shape=box];\n    "a b" -> "c" [label="x\\\\y"];\n}\n')
    assert "passes the screen render_dot runs before dot" in out["checks"]
    undirected = _invoke("graphviz_graph", nodes=NODES, edges=EDGES)["text"]
    assert undirected.startswith("graph G {") and '"a" -- "b";' in undirected
    ends = _invoke("graphviz_digraph", nodes=[{"id": "a", "label": "ends in \\"}])
    assert ends["code"] == INPUT_INVALID and "backslash" in ends["reason"]
    assert "G6_E_INPUT_VALIDATION" in _refused("graphviz_digraph", nodes=[{"id": "a\\b"}])


# -- ASCII: every line the same width, every cell in its own row -------------------------------------------------------

def test_ascii_drawings_line_up_with_wide_characters_and_refuse_a_ragged_table() -> None:
    box = _invoke("ascii_box", title="Résumé", lines=["日本 ok", "plain"])
    assert box["text"] == ("+---------+\n| Résumé  |\n+---------+\n| 日本 ok |\n| plain   |\n+---------+\n")
    table = _invoke("ascii_table", headers=["name", "n", "ok"], rows=[["x", 1.5, True], ["long name", None, False]])
    assert table["text"] == ("+-----------+-----+-------+\n| name      | n   | ok    |\n+-----------+-----+-------+\n"
                             "| x         | 1.5 | true  |\n| long name |     | false |\n+-----------+-----+-------+\n")
    ragged = _invoke("ascii_table", headers=["a", "b"], rows=[["1", "2", "3"]])
    assert ragged["code"] == INPUT_INVALID and "row 1 has 3 cells" in ragged["reason"]
    tree = _invoke("ascii_tree", tree={"label": "root", "children": [{"label": "a", "children": [{"label": "a1"}]},
                                                                     {"label": "b"}]})
    assert tree["text"] == "root\n+-- a\n|   `-- a1\n`-- b\n"


def test_an_ascii_table_cell_is_at_most_200_characters_and_the_drawing_is_capped() -> None:
    long_cell = _invoke("ascii_table", headers=["a", "b"], rows=[["ok", "z" * 201]])
    assert long_cell["code"] == INPUT_INVALID and "200 characters" in long_cell["reason"], long_cell.get("reason")
    assert "row 1, column 2" in long_cell["reason"]
    started = time.monotonic()  # 1,004 lines of 1,291 columns: refused before a line is padded
    wide = _invoke("ascii_table", headers=[f"h{i}" for i in range(30)], rows=[["c" * 40] * 30] * 1000)
    assert wide["code"] == INPUT_INVALID and "1,000,000" in wide["reason"], wide.get("reason")
    assert "text" not in wide and time.monotonic() - started < 2
    ordinary = _invoke("ascii_table", headers=[f"h{i}" for i in range(10)], rows=[["c" * 20] * 10] * 100)
    assert ordinary["completion_state"] == "qualified-draft" and len(ordinary["text"].splitlines()) == 104


def test_a_table_that_would_draw_300_mb_is_refused_at_once() -> None:
    rows = [["x"] * 30 for _ in range(1000)]  # one 10,000-character cell in each of the 30 columns
    for i in range(30):
        rows[i][i] = "w" * 10_000
    started = time.monotonic()
    out = _invoke("ascii_table", headers=[f"h{i}" for i in range(30)], rows=rows)
    assert out["code"] == INPUT_INVALID and "200 characters" in out["reason"], out.get("reason")
    assert "text" not in out and time.monotonic() - started < 2


# -- ADAPT_DIAGRAMS-01: nothing is verified; text no program parsed says so ---------------------------------------------

def test_g1_no_op_is_verified_and_text_says_no_program_parsed_it() -> None:
    for op, params in (("mermaid_flowchart", {"nodes": NODES, "edges": EDGES}), ("ascii_box", {"title": "x"}),
                       ("graphviz_digraph", {"nodes": NODES})):
        out = _invoke(op, **params)
        assert out["completion_state"] == "qualified-draft" and out["warning_card"]["code"] == TEXT_ONLY
        assert out["evidence"][0]["parsed_by"] is None and out["evidence"][0]["drawn"] is False
        assert "no program parsed or drew it" in out["warning_card"]["message"]
    for path in SRC.rglob("*.py"):
        assert not re.search(r"""["']verified["']""", path.read_text(encoding="utf-8")), path.name


def test_g1_a_drawn_source_is_a_draft_resting_on_the_inspection(stand_in: StandIn) -> None:
    out = _invoke("render_dot", source="digraph { a -> b }", name="g1-source")
    assert out["completion_state"] == "qualified-draft", out.get("reason")
    assert out["evidence"][0]["kind"] == "media_inspection" and out["evidence"][0]["passed"]
    checks = {c["name"] for c in out["artifact"]["checks"]}
    assert {"parses", "draws_the_graph", "loads_nothing"} <= checks
    assert out["artifact"]["completion_state"] == "qualified-draft" and out["nodes_drawn"] == 2


# -- render_dot: inspected from disk; blank or incomplete drawings refused (D1) ----------------------------------------

def test_render_dot_draws_declared_nodes_into_the_media_folder(stand_in: StandIn) -> None:
    out = _invoke("render_dot", nodes=NODES, edges=EDGES, name="flow", request_id="r1", run_id="u1")
    assert out["completion_state"] == "qualified-draft", out.get("reason")
    path = Path(out["artifact"]["path"])
    assert path == media_root() / "flow.svg" and path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "<!DOCTYPE" not in text and "<title>a</title>" in text
    assert (out["nodes_drawn"], out["edges_drawn"]) == (3, 2) and out["dot_source"].startswith("digraph G {")
    assert out["artifact"]["mime"] == "image/svg+xml" and check_envelope(out) == []
    png = _invoke("render_dot", nodes=NODES, edges=EDGES, format="png", name="flow")
    assert png["completion_state"] == "qualified-draft", png.get("reason")
    assert png["artifact"]["mime"] == "image/png" and "png_matches_layout" in {c["name"] for c in
                                                                                png["artifact"]["checks"]}


def test_a_png_drawing_says_its_nodes_were_counted_in_the_svg_layout(stand_in: StandIn) -> None:
    out = _invoke("render_dot", nodes=NODES, edges=EDGES, format="png", name="said-png", overwrite=True)
    assert out["completion_state"] == "qualified-draft", out.get("reason")
    said = out["warning_card"]["message"]
    assert "has the proportions of dot's layout" in said and "counted in dot's SVG of the same layout" in said, said
    svg = _invoke("render_dot", nodes=NODES, edges=EDGES, name="said-svg", overwrite=True)
    assert "counted in dot's SVG" not in svg["warning_card"]["message"]


def test_d1_a_drawing_that_leaves_out_a_declared_node_is_refused_and_removed(stand_in: StandIn) -> None:
    stand_in.behave("drop_node")
    before = _media_files()
    for fmt in ("svg", "png"):
        out = _invoke("render_dot", nodes=NODES, edges=EDGES, format=fmt, name=f"d1-drop-{fmt}")
        assert out["completion_state"] == "blocked-escalated" and out["code"] == dot.MISMATCH, out.get("reason")
        assert "not drawn: 'c'" in out["reason"] and out["artifact"] is None
    assert _media_files() == before


def test_d1_a_drawing_whose_edges_join_other_nodes_is_refused_and_removed(stand_in: StandIn) -> None:
    stand_in.behave("rewire")  # a->b, b->c drawn as a->c, c->b: the same nodes and as many edges
    before = _media_files()
    for fmt in ("svg", "png"):
        out = _invoke("render_dot", nodes=NODES, edges=EDGES, format=fmt, name=f"d1-rewire-{fmt}")
        assert out["completion_state"] == "blocked-escalated" and out["code"] == dot.MISMATCH, out.get("reason")
        assert "declared but not drawn: 'a' -> 'b', 'b' -> 'c'" in out["reason"], out["reason"]
        assert "drawn but not declared: 'a' -> 'c', 'c' -> 'b'" in out["reason"] and out["artifact"] is None
    assert _media_files() == before
    stand_in.behave("normal")
    for directed in (True, False):
        out = _invoke("render_dot", nodes=NODES, edges=EDGES, directed=directed, name=f"wired-{directed}")
        assert out["completion_state"] == "qualified-draft" and out["edges_drawn"] == 2, out.get("reason")


def _edges_svg(*titles: str) -> str:
    groups = "".join(f'<g class="node"><title>{n}</title><ellipse rx="2" ry="2"/></g>' for n in "ab")
    groups += "".join(f'<g class="edge"><title>{t}</title><path d="M0,0 L9,9"/></g>' for t in titles)
    return f'<svg xmlns="http://www.w3.org/2000/svg">{groups}</svg>'


def test_d1_the_drawn_edges_are_the_declared_ones_with_direction_and_multiplicity() -> None:
    twice = [("a", "b"), ("a", "b")]
    assert dot.graph_check(_edges_svg("a&#45;&gt;b", "a&#45;&gt;b"), nodes=["a", "b"], edges=twice)[0].passed
    turned, _ = dot.graph_check(_edges_svg("a&#45;&gt;b", "b&#45;&gt;a"), nodes=["a", "b"], edges=twice)
    assert not turned.passed and turned.code == dot.MISMATCH and "'b' -> 'a'" in turned.detail
    once, _ = dot.graph_check(_edges_svg("a&#45;&gt;b"), nodes=["a", "b"], edges=twice)
    assert not once.passed and "declared but not drawn: 'a' -> 'b'" in once.detail
    either_way = dot.graph_check(_edges_svg("b&#45;&#45;a"), nodes=["a", "b"], edges=[("a", "b")], directed=False)
    assert either_way[0].passed  # an undirected edge has no direction to keep


@pytest.mark.parametrize("fmt", ["svg", "png"])
def test_a_refused_overwrite_leaves_the_file_it_would_have_replaced(stand_in: StandIn, fmt: str) -> None:
    first = _invoke("render_dot", nodes=NODES, edges=EDGES, format=fmt, name=f"kept-{fmt}", overwrite=True)
    assert first["completion_state"] == "qualified-draft", first.get("reason")
    path = Path(first["artifact"]["path"])
    before, files = path.read_bytes(), _media_files()
    stand_in.behave("drop_node")
    out = _invoke("render_dot", nodes=NODES, edges=EDGES, format=fmt, name=f"kept-{fmt}", overwrite=True)
    assert out["code"] == dot.MISMATCH and out["artifact"] is None, out.get("reason")
    assert path.read_bytes() == before and _media_files() == files
    stand_in.behave("normal")
    again = _invoke("render_dot", nodes=NODES[:2], edges=EDGES[:1], format=fmt, name=f"kept-{fmt}", overwrite=True)
    assert again["written"] == "replaced" and path.read_bytes() != before and _media_files() == files


def test_d1_a_blank_drawing_from_source_is_refused(stand_in: StandIn) -> None:
    stand_in.behave("blank")
    for fmt in ("svg", "png"):
        out = _invoke("render_dot", source="digraph { x -> y }", format=fmt, name=f"d1-blank-{fmt}")
        assert out["completion_state"] == "blocked-escalated" and out["code"] == BLANK, out.get("reason")
    assert not {"d1-blank-svg.svg", "d1-blank-png.png"} & _media_files()


# -- G2: DOT source is data; dot runs bounded, with G6's argument list and a clean environment -------------------------

REFUSED_SOURCES = [
    ('digraph { a [image="C:/Windows/win.ini"] }', "'image'"),
    ('digraph { a [shape=epsf, shapefile="x.ps"] }', "epsf"),
    ('digraph { a [label=<<IMG SRC="x.png"/>>] }', "HTML-like label"),
    ('digraph { a ["image"="x.png"] }', "'image'"),
    ('digraph { a ["ima" + "ge"="x.png"] }', "'image'"),
    ('digraph { graph [fontpath="C:/"]; a }', "'fontpath'"),
    ('digraph { a [fontname="C:/fonts/evil.ttf"] }', "font name"),
    ('digraph { a [label="x\\\\" image="y.png"] }', "run of backslashes"),
    ('digraph { stylesheet="https://example.com/x.css"; a }', "'stylesheet'"),
    ('digraph { a [URL="javascript:alert(1)"] }', "'URL'"),
    ('digraph { a [Image="x.png"] }', "'Image'"),
    ('digraph { a [width=100000] }', "outside"),
    ('digraph { graph [dpi=10000]; a }', "'dpi'"),
    ("digraph { a # b }", "'#'"),
    ('digraph { a [label="never closed] }', "never closed"),
    ("a -> b", "no graph body"),
]


@pytest.mark.parametrize(("source", "said"), REFUSED_SOURCES)
def test_g2_a_source_that_could_make_dot_read_a_file_is_refused_before_dot_starts(stand_in: StandIn, source: str,
                                                                                  said: str) -> None:
    out = _invoke("render_dot", source=source)
    assert out["completion_state"] == "blocked-escalated" and out["code"] == INPUT_INVALID, out
    assert said in out["reason"], out["reason"]
    assert stand_in.calls() == []


def test_g2_an_ordinary_source_with_comments_ports_and_joins_passes_the_screen(stand_in: StandIn) -> None:
    source = ('strict digraph "my graph" {\n  rankdir=LR; node [shape=box, style="rounded,filled", fillcolor="#eef",'
              ' fontname="Helvetica"];\n  a -> b -> c [label="x" + "y"];  /* note */ // done\n# a line\n'
              '  a:n -> c [label="say \\"hi\\""];\n}\n')
    found = dot.screen(source)
    assert found["attributes"] == ["fillcolor", "fontname", "label", "rankdir", "shape", "style"]
    out = _invoke("render_dot", source=source, name="ordinary")
    assert out["completion_state"] == "qualified-draft", out.get("reason")


def test_g2_dot_gets_g6s_argument_list_a_clean_environment_and_a_fresh_folder(stand_in: StandIn,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("G6_CANARY_SETTING", "canary-value")
    monkeypatch.setenv("SOME_SERVICE_TOKEN", "canary-token")
    out = _invoke("render_dot", source='digraph { a -> b [label="-Tpng -o C:/x.png"] }', format="png", name="argv")
    assert out["completion_state"] == "qualified-draft", out.get("reason")
    runs = stand_in.runs()
    assert [r["argv"] for r in runs] == [["-Tsvg", "-o", "diagram.svg", "diagram.gv"],
                                         ["-Tpng", "-o", "diagram.png", "diagram.gv"]]
    allowed = {"SYSTEMROOT", "WINDIR", "LANG", "LC_ALL", "FONTCONFIG_FILE", "FONTCONFIG_PATH", "GVBINDIR", "TEMP",
               "TMP", "TMPDIR", "HOME", "PROMPT", "COMSPEC", "PATHEXT", "PWD", "LC_CTYPE", "SHLVL",
               "__CF_USER_TEXT_ENCODING"}
    # cmd.exe, which runs the launcher on Windows, sets PROMPT, COMSPEC and PATHEXT itself; /bin/sh, which runs it
    # elsewhere, sets PWD (and SHLVL on macOS, where it is bash); the stand-in's Python sets LC_CTYPE itself where no
    # locale is set (its locale coercion; python:3.13 images set none); and macOS's CoreFoundation, which that Python
    # loads there, sets __CF_USER_TEXT_ENCODING itself
    for run in runs:  # the venv launcher on Windows adds __PYVENV_LAUNCHER__ for the interpreter it starts
        assert {k for k in run["env"] if k.upper() not in allowed and not k.startswith("__PYVENV")} == set(), \
            sorted(run["env"])
        assert "canary" not in json.dumps(run["env"])
        assert Path(run["cwd"]).name.startswith("g6_dot_") and not Path(run["cwd"]).exists()
    assert out["evidence"][1]["execution"]["cleanup_confirmed"] is True


def test_g2_a_dot_that_never_finishes_is_stopped_at_its_deadline(stand_in: StandIn) -> None:
    stand_in.behave("hang")
    started = time.monotonic()
    out = _invoke("render_dot", source="digraph { a }", timeout_s=2, name="hang")
    assert out["completion_state"] == "blocked-escalated" and out["code"] == BACKEND_FAILED
    assert "did not finish within 2 s" in out["reason"] and time.monotonic() - started < 30
    assert "hang.svg" not in _media_files()


# -- ADAPT_DIAGRAMS-03 and G4: a syntax error, a missing program and other failures are told apart, and tighten -------

def test_p2_a_syntax_error_is_dots_own_message_not_a_missing_program(stand_in: StandIn) -> None:
    out = _invoke("render_dot", source="digraph { a -> }}", name="syntax")
    assert out["code"] == dot.DOES_NOT_PARSE and "syntax error" in out["reason"], out


def test_g4_a_missing_dot_is_blocked_with_the_install_steps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(dot.DOT_ENV, str(tmp_path / "no-dot-here.exe"))
    out = _invoke("render_dot", nodes=NODES, edges=EDGES)
    assert out["completion_state"] == "blocked-escalated" and out["code"] == OUTSIDE_MISSING
    assert "graphviz.org" in out["reason"] and "G6 never installs it" in out["reason"]
    monkeypatch.delenv(dot.DOT_ENV)
    monkeypatch.setenv("PATH", str(tmp_path / "nothing-here"))
    assert _invoke("render_dot", source="digraph { a }")["code"] == OUTSIDE_MISSING


@pytest.mark.parametrize(("mode", "code"), [("crash", BACKEND_FAILED), ("no_output", NO_OUTPUT),
                                            ("garbage", "G6_E_MEDIA_UNDECODABLE")])
def test_g4_a_dot_that_fails_or_writes_nothing_usable_is_refused(stand_in: StandIn, mode: str, code: str) -> None:
    stand_in.behave(mode)
    out = _invoke("render_dot", source="digraph { a -> b }", name=f"g4-{mode}")
    assert out["completion_state"] == "blocked-escalated" and out["code"] == code, out.get("reason")
    assert f"g4-{mode}.svg" not in _media_files()


def test_g4_a_png_without_pillow_is_refused_before_dot_runs(stand_in: StandIn, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(block, "pillow", lambda: None)
    out = _invoke("render_dot", source="digraph { a }", format="png")
    assert out["code"] == EXTRA_MISSING and "pip install 'g6[image]'" in out["reason"] and stand_in.calls() == []


def test_warnings_dot_prints_are_kept_in_the_evidence(stand_in: StandIn) -> None:
    stand_in.behave("noisy")
    out = _invoke("render_dot", source="digraph { a -> b }", name="noisy")
    assert out["completion_state"] == "qualified-draft" and "something to say" in out["evidence"][1]["warnings"][0]


def test_an_svg_that_would_load_something_is_refused() -> None:
    for svg in ('<svg xmlns="http://www.w3.org/2000/svg"><image href="https://x/y.png"/></svg>',
                '<svg xmlns="http://www.w3.org/2000/svg"><rect onclick="alert(1)" width="2" height="2"/></svg>',
                '<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:url(https://x/y)" width="2"/></svg>'):
        assert not dot.loads_nothing(svg).passed, svg
    gradient = '<svg xmlns="http://www.w3.org/2000/svg"><rect fill="url(#l_0)" width="2" height="2"/></svg>'
    assert dot.loads_nothing(gradient).passed


# -- G3: a caller's claims are refused, not honoured -------------------------------------------------------------------

@pytest.mark.parametrize("field", [{"completion_state": "verified"}, {"approved": True}, {"reviewer": "Dr Who"},
                                   {"output_path": "C:/x.svg"}, {"output_format": "html"}, {"parsed": True}])
def test_g3_claims_and_paths_are_refused(field: dict) -> None:
    assert "G6_E_INPUT_VALIDATION" in _refused("mermaid_flowchart", nodes=NODES, **field)


@pytest.mark.parametrize("name", ["../escape", "C:/x", "a/b", "con"])
def test_a_name_with_a_folder_is_refused(name: str) -> None:
    assert "G6_E_INPUT_VALIDATION" in _refused("render_dot", source="digraph { a }", name=name)


# -- G5 to G8: no socket, no model, no stand-in in the package, permissive extra ---------------------------------------

def test_g5_render_dot_runs_a_program_so_it_is_refused_over_mcp_unless_the_server_allows_it(
        stand_in: StandIn, monkeypatch: pytest.MonkeyPatch) -> None:
    assert package.__g6_sensitive_ops__ == ("render_dot",) and [p.name for p in package.__g6_outside__] == ["Graphviz"]
    params = json.dumps({"nodes": NODES, "edges": EDGES, "name": "g5"})
    denied = invoke_reply("adapt_diagrams", "render_dot", params)
    assert not denied["ok"] and denied["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED"
    assert stand_in.calls() == [] and "g5.svg" not in _media_files()
    allowed = invoke_reply("adapt_diagrams", "render_dot", params,
                           allow_sensitive=frozenset({"adapt_diagrams.render_dot"}))
    assert allowed["ok"] and allowed["completion_state"] == "qualified-draft", allowed
    assert {Path(r["argv"][-1]).name for r in stand_in.runs()} == {"diagram.gv"}

    def refused(*args, **kwargs):
        raise AssertionError("a text op reached the network or started a program")

    monkeypatch.setattr(socket.socket, "connect", refused)
    monkeypatch.setattr(subprocess, "Popen", refused)
    for op in block._GRAPH_OPS:
        reply = invoke_reply("adapt_diagrams", op, json.dumps({"nodes": NODES, "edges": [
            {"from": "a", "to": "b", "label": "go"}]}))
        assert reply["ok"] and reply["completion_state"] == "qualified-draft", (op, reply)
    tree = ast.parse((SRC / "dot.py").read_text(encoding="utf-8"))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert "subprocess" not in imported  # dot starts only through g6.core.component_process


def test_g6_g7_the_package_reads_no_key_calls_no_model_and_holds_no_stand_in() -> None:
    for path in SRC.rglob("*"):
        if path.suffix not in (".py", ".md"):
            continue
        text = path.read_text(encoding="utf-8").lower()
        for word in ("api_key", "getenv", "environ.get", "litellm", "openrouter", "anthropic", "openai", "mock",
                     "fake", "simulat", "experimental", "all rights reserved", "https://cdn", "<script"):
            assert word not in text, (path.name, word)
        if path.suffix == ".py":
            assert path.read_text(encoding="utf-8").startswith("# SPDX-License-Identifier: Apache-2.0\n"), path.name


def test_g8_the_extra_is_permissively_licensed() -> None:
    assert package.__g6_extras__ == {"image": ("PIL",)}
    meta = importlib.metadata.metadata("pillow")
    said = " ".join(filter(None, [meta.get("License-Expression"), meta.get("License"),
                                  *meta.get_all("Classifier", [])])).upper()
    assert said and not re.search(r"\b(A?GPL|SSPL|NON-?COMMERCIAL)\b", said), said


# -- D2, D4, D6: declared failure mode, typed block, reachable over MCP -------------------------------------------------

def test_d2_it_declares_the_failure_mode_it_catches_and_moved_out_of_planned() -> None:
    assert package.__g6_mitigates__ == ("visual_check_failed",) and package.__g6_introduces__ == ()
    assert "g6.media.adapt_diagrams" not in FailureModeRegistry.planned()


def test_d4_the_block_is_typed_and_lists_its_ops() -> None:
    assert get_registry().get_class("adapt_diagrams") is AdaptDiagramsBlock
    assert block.DiagramInput.model_config["extra"] == "forbid"
    assert generators.Node.model_config["extra"] == "forbid" and generators.Edge.model_config["extra"] == "forbid"
    assert _invoke("ops")["ops"] == list(block._OPS) and len(_invoke("help")["help"]) == len(block._OPS)
    assert "G6_E_INPUT_VALIDATION" in _refused("render_dot", source="digraph { a }", nodes=NODES)


def test_d6_an_agent_finds_it_describes_it_and_invokes_it_over_mcp() -> None:
    assert "adapt_diagrams" in [r["component"] for r in discovery.find("draw a diagram as mermaid or graphviz")]
    info = describe_reply("adapt_diagrams")
    assert info["family"] == "media" and info["skill"] and sorted(info["ops"]) == sorted(block._OPS)
    assert info["sensitive_ops"] == ["render_dot"] and [p["name"] for p in info["outside"]] == ["Graphviz"]
    reply = invoke_reply("adapt_diagrams", "mermaid_flowchart",
                         json.dumps({"nodes": NODES, "edges": EDGES, "request_id": "q1", "run_id": "u1"}))
    assert reply["ok"] and reply["completion_state"] == "qualified-draft" and check_envelope(reply) == []
    assert (reply["result"]["request_id"], reply["result"]["run_id"]) == ("q1", "u1")


# -- D9: without Pillow, an honest "not installed" for PNG; the rest answers --------------------------------------------

_WITHOUT = """
import json, sys
sys.modules["PIL"] = None
from g6.core.registry import get_registry
reg = get_registry()
nodes = [{"id": "a"}, {"id": "b"}]
out = {op: reg.invoke("adapt_diagrams", op, params).unwrap() for op, params in (
    ("mermaid_flowchart", {"nodes": nodes}), ("ascii_table", {"headers": ["a"], "rows": [["1"]]}),
    ("render_dot", {"nodes": nodes, "format": "png"}), ("ops", {}))}
print(json.dumps({op: [v.get("completion_state"), v.get("code", "")] for op, v in out.items()}))
"""


def test_d9_without_pillow_a_png_says_it_is_not_installed_and_the_text_ops_answer(tmp_path: Path) -> None:
    done = subprocess.run([sys.executable, "-c", _WITHOUT], capture_output=True, text=True, timeout=120,
                          env={"G6_HOME": str(tmp_path), "PATH": "", "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")})
    assert done.returncode == 0, done.stderr
    found = json.loads(done.stdout.strip().splitlines()[-1])
    assert found["mermaid_flowchart"] == ["qualified-draft", ""] == found["ascii_table"]
    assert found["render_dot"] == ["blocked-escalated", EXTRA_MISSING] and found["ops"][0] is None


# -- With Graphviz itself, when it is installed -------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("dot") is None, reason="Graphviz dot is not installed on this machine (PATH has no dot)")
def test_the_real_dot_draws_a_declared_graph_as_svg_and_png(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(dot.DOT_ENV, raising=False)
    for fmt in ("svg", "png"):
        out = _invoke("render_dot", nodes=[*NODES, {"id": "end", "label": 'say "x"'}],
                      edges=[*EDGES, {"from": "c", "to": "end"}], format=fmt, name=f"real-{fmt}", overwrite=True)
        assert out["completion_state"] == "qualified-draft", out.get("reason")
        assert (out["nodes_drawn"], out["edges_drawn"]) == (4, 3)
    syntax = _invoke("render_dot", source="digraph { a -> }", name="real-syntax")
    assert syntax["code"] == dot.DOES_NOT_PARSE


def test_the_inspector_module_is_the_familys(stand_in: StandIn, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    real = _inspect.inspect_svg
    monkeypatch.setattr(block, "inspect_svg", lambda path, **kw: calls.append(path) or real(path, **kw))
    assert _invoke("render_dot", source="digraph { a }", name="family")["completion_state"] == "qualified-draft"
    assert calls and Path(calls[0]).parent == media_root()
