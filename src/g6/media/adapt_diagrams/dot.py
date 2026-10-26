# SPDX-License-Identifier: Apache-2.0
"""Graphviz's ``dot``, the one outside program adapt_diagrams starts, and what G6 checks before and after it runs.

**Before.** DOT source is data: :func:`screen` reads it with a lexer that follows Graphviz's own (quoted strings with
``\\"`` escapes and ``+`` joins, ``//`` and ``/* */`` comments, ``#`` lines) and refuses what would make dot read a file
or draw something it was not asked to: an HTML-like label (``<…>``, which can name an image), and every attribute that
is not on :data:`ATTRIBUTES` (so ``image``, ``shapefile``, ``fontpath``, ``imagepath``, ``stylesheet``, ``URL``,
``href``, ``size`` and ``dpi`` among others are refused), a ``shape`` outside :data:`SHAPES` (``epsf`` and ``custom``
read files), a ``fontname`` that looks like a path, a number outside its bounds, and a run of backslashes before a
quote (Graphviz versions read it differently, so the lexer could not know where the string ends).

**Running it.** dot is the person's own program, never installed or bundled by G6: ``G6_GRAPHVIZ_DOT`` names it, or
``dot`` is found on PATH (:data:`GRAPHVIZ`, detected at call time through :mod:`g6.core.outside`). Each run gets a
fresh temporary directory holding ``diagram.gv``; dot runs there as ``dot -T<format> -o diagram.<format> diagram.gv``,
an argument list G6 builds with no caller text in it, under :mod:`g6.core.component_process`: a deadline (the whole
process tree killed when it passes), a memory cap per process (the Windows Job Object; elsewhere the deadline is the
bound), an output cap, and an environment holding only what dot needs to start (no keys, no G6 settings, its
temporary and home folders inside the run directory). A syntax error dot reports is told apart from a missing program
and from any other failure.

**After.** :func:`graph_check` reads the SVG dot wrote: each node group (``<g class="node">``, named by its
``<title>``) and edge group that draws something (named ``tail->head``, or ``tail--head`` undirected, by its
``<title>``). A diagram G6 generated from nodes and edges must draw exactly those nodes and those edges: the drawn
edges' two ends, compared with the declared ones as a multiset (direction kept in a directed graph, each edge counted
as often as it is declared); an edge whose title does not name exactly one pair of declared nodes fails. One from
source must draw at least one node or edge. :func:`loads_nothing` checks the file
references nothing outside itself. dot's ``<!DOCTYPE>`` line (a reference to the SVG 1.1 DTD on the web) is removed
before the file is written; nothing else dot wrote is changed.
"""
from __future__ import annotations

import os
import re
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from g6.core import component_process, outside
from g6.core.outside import OutsideProgram
from g6.core.process_tree import ProcessTreeError
from g6.media._types import BACKEND_FAILED, BLANK, INPUT_INVALID, NO_OUTPUT, OUTSIDE_MISSING, Check, MediaError

DOT_ENV = "G6_GRAPHVIZ_DOT"
INSTALL = ("Install Graphviz from https://graphviz.org/download/ (Windows: winget install graphviz; macOS: brew install "
           "graphviz; Debian or Ubuntu: sudo apt install graphviz) and put dot on PATH, or set G6_GRAPHVIZ_DOT to the "
           "dot program")
GRAPHVIZ = OutsideProgram(name="Graphviz", purpose="render_dot draws DOT diagrams as SVG or PNG with Graphviz's dot",
                          install=INSTALL, licence="EPL-1.0", command="dot", env=DOT_ENV, version_args=("-V",))

#: Codes of this package's own refusals (a failed check's code starts G6_E_MEDIA_, as the family's checks require).
DOES_NOT_PARSE = "G6_E_MEDIA_DIAGRAM_DOES_NOT_PARSE"
MISMATCH = "G6_E_MEDIA_DIAGRAM_MISMATCH"
LOADS_SOMETHING = "G6_E_MEDIA_LOADS_EXTERNAL"

MAX_SOURCE = 200_000
DEFAULT_SECONDS, MAX_SECONDS = 30.0, 120.0
MEMORY_MB = 1024
MAX_OUTPUT_BYTES = 32 * 1024 * 1024
_ENV_KEEP = ("SYSTEMROOT", "WINDIR", "LANG", "LC_ALL", "FONTCONFIG_FILE", "FONTCONFIG_PATH", "GVBINDIR")
_TAIL = 600

#: Graph, node and edge attributes that change only how a diagram is laid out and drawn.
ATTRIBUTES = frozenset({
    "label", "xlabel", "headlabel", "taillabel", "labelloc", "labeljust", "rankdir", "splines", "nodesep", "ranksep",
    "bgcolor", "color", "fillcolor", "fontcolor", "fontsize", "fontname", "style", "penwidth", "pencolor", "shape",
    "width", "height", "fixedsize", "peripheries", "sides", "skew", "distortion", "orientation", "regular", "group",
    "rank", "concentrate", "compound", "newrank", "ordering", "center", "forcelabels", "clusterrank", "outputorder",
    "arrowhead", "arrowtail", "arrowsize", "dir", "constraint", "weight", "minlen", "lhead", "ltail", "headport",
    "tailport", "samehead", "sametail", "decorate", "labelfloat", "labelfontsize", "labelfontcolor", "labelfontname",
    "labelangle", "labeldistance", "gradientangle"})
#: Graphviz's built-in node shapes (``epsf`` and ``custom``, which read a file, are not among them).
SHAPES = frozenset({
    "box", "polygon", "ellipse", "oval", "circle", "point", "egg", "triangle", "plaintext", "plain", "diamond",
    "trapezium", "parallelogram", "house", "pentagon", "hexagon", "septagon", "octagon", "doublecircle",
    "doubleoctagon", "tripleoctagon", "invtriangle", "invtrapezium", "invhouse", "Mdiamond", "Msquare", "Mcircle",
    "rect", "rectangle", "square", "star", "none", "underline", "cylinder", "note", "tab", "folder", "box3d",
    "component", "promoter", "cds", "terminator", "utr", "primersite", "restrictionsite", "fivepoverhang",
    "threepoverhang", "noverhang", "assembly", "signature", "insulator", "ribosite", "rnastab", "proteasesite",
    "proteinstab", "rpromoter", "rarrow", "larrow", "lpromoter", "record", "Mrecord"})
#: Bounds of the numeric attributes, so no single value asks for a canvas or a layout far beyond a diagram's.
BOUNDS = {"width": (0, 20), "height": (0, 20), "fontsize": (1, 144), "labelfontsize": (1, 144), "penwidth": (0, 20),
          "arrowsize": (0, 10), "nodesep": (0.02, 10), "ranksep": (0.02, 20), "peripheries": (0, 10), "sides": (3, 100),
          "skew": (-100, 100), "distortion": (-100, 100), "orientation": (-360, 360), "minlen": (0, 100),
          "weight": (0, 1000), "labelangle": (-180, 180), "labeldistance": (0, 20), "gradientangle": (-360, 360)}
_FONT = re.compile(r"[A-Za-z][A-Za-z0-9 ,\-]{0,63}")
_ID = re.compile(r"[A-Za-z_\u0080-\U0010ffff][A-Za-z0-9_\u0080-\U0010ffff]*")
_NUMERAL = re.compile(r"-?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)")


@dataclass(frozen=True)
class Token:
    kind: str  # "id" (a name or a number), "string" (a quoted string, as dot reads it), "punct", "edgeop", "plus"
    text: str
    line: int


def _refused(line: int, reason: str) -> MediaError:
    return MediaError(INPUT_INVALID, f"the DOT source, line {line}: {reason}")


def _string(source: str, start: int, line: int) -> tuple[str, int, int]:
    """The quoted string opening at ``start``: its text as dot reads it, where it ends, and the line it ends on."""
    out, i, n = [], start + 1, len(source)
    while True:
        if i >= n:
            raise _refused(line, "a quoted string is never closed")
        ch = source[i]
        if ch == '"':
            return "".join(out), i + 1, line
        if ch == "\\":
            j = i
            while j < n and source[j] == "\\":
                j += 1
            run, after = j - i, source[j] if j < n else ""
            if after == '"' and run >= 2:
                raise _refused(line, "a run of backslashes before a quote, which Graphviz versions read differently")
            if after == '"':
                out.append('"')
                i += 2
                continue
            if after == "\n" and run == 1:
                i, line = i + 2, line + 1
                continue
            out.append("\\" * run)
            i += run
            continue
        if ch == "\n":
            line += 1
        out.append(ch)
        i += 1


def tokens(source: str) -> list[Token]:
    """The DOT source as dot's lexer reads it; anything it would not read, or that would read a file, is refused."""
    found: list[Token] = []
    i, n, line = 0, len(source), 1
    while i < n:
        ch = source[i]
        if ch == "\n":
            line, i = line + 1, i + 1
        elif ch in " \t\r\f\v":
            i += 1
        elif ch == "#" or source.startswith("//", i):
            if ch == "#" and i and source[i - 1] != "\n":
                raise _refused(line, "a '#' that does not start a line")
            end = source.find("\n", i)
            i = n if end < 0 else end
        elif source.startswith("/*", i):
            end = source.find("*/", i + 2)
            if end < 0:
                raise _refused(line, "a /* comment is never closed")
            line += source.count("\n", i, end)
            i = end + 2
        elif ch == '"':
            text, i, end_line = _string(source, i, line)
            found.append(Token("string", text, line))
            line = end_line
        elif ch in "{}[];,=:":
            found.append(Token("punct", ch, line))
            i += 1
        elif source.startswith("->", i) or source.startswith("--", i):
            found.append(Token("edgeop", source[i:i + 2], line))
            i += 2
        elif ch == "+":
            found.append(Token("plus", ch, line))
            i += 1
        elif (number := _NUMERAL.match(source, i)) is not None:
            found.append(Token("id", number.group(0), line))
            i = number.end()
        elif (name := _ID.match(source, i)) is not None:
            found.append(Token("id", name.group(0), line))
            i = name.end()
        elif ch == "<":
            raise _refused(line, "an HTML-like label (<...>) is not drawn here: it can name an image file; use a "
                                 "quoted label")
        else:
            raise _refused(line, f"{ch!r} is not part of the DOT language")
    joined: list[Token] = []
    for k, tok in enumerate(found):  # "a" + "b" is one string to dot
        if tok.kind == "plus":
            nxt = found[k + 1] if k + 1 < len(found) else None
            if not joined or joined[-1].kind != "string" or nxt is None or nxt.kind != "string":
                raise _refused(tok.line, "'+' joins two quoted strings and nothing else")
            continue
        if tok.kind == "string" and k and found[k - 1].kind == "plus":
            joined[-1] = Token("string", joined[-1].text + tok.text, joined[-1].line)
            continue
        joined.append(tok)
    return joined


def screen(source: str) -> dict[str, Any]:
    """Refuse DOT source that could make dot read a file or draw beyond a diagram (see the module docstring); return
    what was read: the attributes set and the number of tokens."""
    if not source.strip():
        raise MediaError(INPUT_INVALID, "the DOT source is empty")
    if len(source) > MAX_SOURCE:
        raise MediaError(INPUT_INVALID, f"the DOT source is longer than {MAX_SOURCE} characters")
    if "\x00" in source:
        raise MediaError(INPUT_INVALID, "the DOT source holds a NUL character")
    toks = tokens(source)
    used: set[str] = set()
    for k, tok in enumerate(toks):
        if tok.kind != "punct" or tok.text != "=":
            continue
        name = toks[k - 1] if k else None
        value = toks[k + 1] if k + 1 < len(toks) else None
        if name is None or value is None or name.kind not in ("id", "string") or value.kind not in ("id", "string"):
            raise _refused(tok.line, "an '=' needs an attribute name before it and a value after it")
        attribute = name.text
        if attribute not in ATTRIBUTES:
            raise _refused(tok.line, f"the attribute {attribute[:40]!r} is not one G6 lets dot read: only layout and "
                                     "drawing attributes are (no image, shapefile, fontpath, imagepath, stylesheet, "
                                     "URL, href, size or dpi)")
        if attribute == "shape" and value.text not in SHAPES:
            raise _refused(tok.line, f"the shape {value.text[:40]!r} is not one of Graphviz's built-in shapes")
        if attribute in ("fontname", "labelfontname") and not _FONT.fullmatch(value.text):
            raise _refused(tok.line, f"the font name {value.text[:40]!r} is not a plain font family name")
        if attribute in BOUNDS:
            low, high = BOUNDS[attribute]
            try:
                number = float(value.text)
            except ValueError:
                raise _refused(tok.line, f"{attribute} takes one number, not {value.text[:40]!r}") from None
            if not low <= number <= high:
                raise _refused(tok.line, f"{attribute} {number:g} is outside {low:g} to {high:g}")
        used.add(attribute)
    if not any(t.kind == "punct" and t.text == "{" for t in toks):
        raise MediaError(INPUT_INVALID, "the DOT source has no graph body ({ ... })")
    return {"attributes": sorted(used), "tokens": len(toks)}


@dataclass
class DotRun:
    """What one run of dot made: the bytes of each format asked for, its version, what it printed, the argument lists."""

    outputs: dict[str, bytes] = field(default_factory=dict)
    version: str = ""
    program: str = ""
    warnings: list[str] = field(default_factory=list)
    argv: list[list[str]] = field(default_factory=list)
    execution: dict[str, Any] = field(default_factory=dict)


def _tail(text: str) -> str:
    return " ".join(text.split())[-_TAIL:]


def run_dot(source: str, formats: tuple[str, ...], *, seconds: float = DEFAULT_SECONDS) -> DotRun:
    """Run dot on ``source`` (already screened) once per format, all within ``seconds``; see the module docstring.
    Raises :class:`MediaError`: ``G6_E_OUTSIDE_PROGRAM_MISSING`` with the install steps, ``…_DIAGRAM_DOES_NOT_PARSE``
    with dot's own message, ``…_NO_OUTPUT`` or ``…_BACKEND_FAILED``."""
    found = outside.require(GRAPHVIZ)
    if found.is_fail():
        raise MediaError(OUTSIDE_MISSING, found.error.removeprefix(f"[{OUTSIDE_MISSING}] "))
    detected = found.value
    run = DotRun(version=detected.version, program=detected.where)
    seconds = min(max(seconds, 1.0), MAX_SECONDS)
    with tempfile.TemporaryDirectory(prefix="g6_dot_", ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        (root / "tmp").mkdir()
        (root / "diagram.gv").write_bytes(source.encode("utf-8"))
        env = {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}
        env |= {"TEMP": str(root / "tmp"), "TMP": str(root / "tmp"), "TMPDIR": str(root / "tmp"),
                "HOME": str(root / "tmp")}
        window = None
        try:
            with component_process.window(seconds) as window:
                for fmt in formats:
                    argv = [detected.where, f"-T{fmt}", "-o", f"diagram.{fmt}", "diagram.gv"]
                    run.argv.append(argv)
                    done = component_process.run_contained(argv, env=env, cwd=root,
                                                           memory_bytes=MEMORY_MB * 1024 * 1024)
                    said = (done.stderr or b"").decode("utf-8", "replace") + (done.stdout or b"").decode(
                        "utf-8", "replace")
                    if done.returncode != 0:
                        code = DOES_NOT_PARSE if "syntax error" in said.lower() else BACKEND_FAILED
                        raise MediaError(code, f"dot exited {done.returncode}: {_tail(said) or 'it said nothing'}")
                    if said.strip():
                        run.warnings.append(_tail(said))
                    made = root / f"diagram.{fmt}"
                    if not made.is_file() or made.stat().st_size == 0:
                        raise MediaError(NO_OUTPUT, f"dot exited 0 but wrote no {fmt} file")
                    if made.stat().st_size > MAX_OUTPUT_BYTES:
                        raise MediaError(BACKEND_FAILED, f"dot wrote more than {MAX_OUTPUT_BYTES} bytes of {fmt}")
                    run.outputs[fmt] = made.read_bytes()
        except component_process.ExecutionInterrupted:
            raise MediaError(BACKEND_FAILED, f"dot did not finish within {seconds:g} s, so it was stopped and its "
                                             "process tree killed") from None
        except (OSError, ProcessTreeError, ConnectionResetError) as exc:
            raise MediaError(BACKEND_FAILED, f"dot could not be run to the end: {type(exc).__name__}: {exc}"[:500]) \
                from None
        finally:
            if window is not None:
                run.execution = {k: window.execution.get(k) for k in ("cleanup_confirmed", "child_exited",
                                                                       "elapsed_seconds")}
    return run


_DOCTYPE = re.compile(r'<!DOCTYPE svg PUBLIC "[^"<>]*"\s+"[^"<>]*">\s*')
_SVG = "{http://www.w3.org/2000/svg}"
_DRAWN = ("polygon", "ellipse", "path", "polyline", "text", "rect", "circle", "line")


def without_doctype(svg: bytes) -> bytes:
    """dot's SVG with its ``<!DOCTYPE svg PUBLIC …>`` line removed (only that line; any other is left, and refused)."""
    text = svg.decode("utf-8", "replace")
    return _DOCTYPE.sub("", text, count=1).encode("utf-8")


def drawn(svg: str) -> tuple[list[str], list[str]]:
    """The names of the node groups that draw something and the titles of the edge groups that do, in dot's SVG."""
    root = ET.fromstring(svg)
    nodes, edges = [], []
    for group in root.iter(f"{_SVG}g"):
        kind = group.get("class", "")
        if kind not in ("node", "edge"):
            continue
        if not any(child.tag.removeprefix(_SVG) in _DRAWN for child in group.iter() if child is not group):
            continue
        title = group.find(f"{_SVG}title")
        (nodes if kind == "node" else edges).append((title.text or "") if title is not None else "")
    return nodes, edges


def _ends(title: str, names: set[str], arrow: str) -> list[tuple[str, str]]:
    """Every pair of declared node names an edge's title (``tail`` ``arrow`` ``head``) can be read as."""
    found, at = [], title.find(arrow)
    while at >= 0:
        tail, head = title[:at], title[at + len(arrow):]
        if tail in names and head in names:
            found.append((tail, head))
        at = title.find(arrow, at + 1)
    return found


def graph_check(svg: str, *, nodes: list[str] | None = None, edges: list[tuple[str, str]] | None = None,
                directed: bool = True) -> tuple[Check, dict[str, Any]]:
    """Whether dot's SVG draws the graph: exactly ``nodes`` (by name) and ``edges`` (``(from, to)`` pairs, compared as
    a multiset, direction kept when ``directed``) when they are given, else at least one node or edge."""
    try:
        found, titles = drawn(svg)
    except ET.ParseError as exc:
        return Check(name="draws_the_graph", passed=False, code=NO_OUTPUT, detail=f"not XML: {exc}"[:500]), {}
    measured = {"nodes_drawn": len(found), "edges_drawn": len(titles)}
    if nodes is None:
        ok = bool(found) or bool(titles)
        return Check(name="draws_the_graph", passed=ok, code="" if ok else BLANK,
                     detail=f"{len(found)} nodes and {len(titles)} edges drawn"), measured
    declared = list(edges or [])
    arrow = "->" if directed else "--"

    def key(pair: tuple[str, str]) -> tuple[str, str]:
        return pair if directed else min(pair, pair[::-1])

    def said_edges(pairs: Counter) -> str:
        return ", ".join(f"{a!r} {arrow} {b!r}" for a, b in sorted(pairs.elements())[:5])

    want, got, unread = Counter(key(e) for e in declared), Counter(), []
    for title in titles:
        ends = _ends(title, set(nodes), arrow)
        if len(ends) == 1:
            got[key(ends[0])] += 1
        else:
            unread.append(title)
    missing = sorted(set(nodes) - set(found))
    extra = sorted(set(found) - set(nodes))
    if missing or extra or len(found) != len(nodes) or want != got or unread:
        said = [f"{len(found)} of {len(nodes)} nodes and {len(titles)} of {len(declared)} edges drawn"]
        if missing:
            said.append(f"not drawn: {', '.join(map(repr, missing[:5]))}")
        if extra:
            said.append(f"drawn but not declared: {', '.join(map(repr, extra[:5]))}")
        if want - got:
            said.append(f"edges declared but not drawn: {said_edges(want - got)}")
        if got - want:
            said.append(f"edges drawn but not declared: {said_edges(got - want)}")
        if unread:
            said.append(f"edges whose title names no single pair of declared nodes: {', '.join(map(repr, unread[:5]))}")
        return Check(name="draws_the_graph", passed=False, code=MISMATCH, detail="; ".join(said)[:500]), measured
    return Check(name="draws_the_graph", passed=True,
                 detail=f"every declared node ({len(nodes)}) and edge ({len(declared)}) is drawn, each edge between "
                        "its own two nodes"), measured


_ELSEWHERE = re.compile(r"(?i)@import|url\s*\(\s*['\"]?\s*(?!#)")
_LOADING = frozenset({"script", "foreignobject", "image", "iframe", "object", "embed", "audio", "video", "link", "use"})


def loads_nothing(svg: str) -> Check:
    """Whether the SVG references or runs nothing outside itself: no element that loads or runs something (script,
    image, foreign object, use, iframe, object, embed, audio, video, link), no ``href``, ``src`` or ``on…`` attribute,
    no ``url(…)`` but one to a fragment of the file itself (``url(#…)``), no ``@import`` and no style sheet
    instruction."""
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        return Check(name="loads_nothing", passed=False, code=NO_OUTPUT, detail=f"not XML: {exc}"[:500])
    hits: list[str] = ["<?xml-stylesheet?>"] if "<?xml-stylesheet" in svg.lower() else []
    for element in root.iter():
        tag = str(element.tag).rpartition("}")[2].lower()
        if tag in _LOADING:
            hits.append(f"<{tag}>")
        for key, value in element.attrib.items():
            local = key.rpartition("}")[2].lower()
            if local in ("href", "src") or local.startswith("on") or _ELSEWHERE.search(value):
                hits.append(f"{local}=")
        if tag == "style" and _ELSEWHERE.search(element.text or ""):
            hits.append("<style> with an import or url()")
    if hits:
        return Check(name="loads_nothing", passed=False, code=LOADS_SOMETHING,
                     detail=f"the file references or runs something outside itself: {', '.join(sorted(set(hits)))}")
    return Check(name="loads_nothing", passed=True,
                 detail="no link, script, image, event handler, import or outside url() in the file")


__all__ = ["ATTRIBUTES", "BOUNDS", "DEFAULT_SECONDS", "DOES_NOT_PARSE", "DOT_ENV", "GRAPHVIZ", "INSTALL",
           "LOADS_SOMETHING", "MAX_SECONDS", "MAX_SOURCE", "MISMATCH", "SHAPES", "DotRun", "Token", "drawn",
           "graph_check", "loads_nothing", "run_dot", "screen", "tokens", "without_doctype"]
