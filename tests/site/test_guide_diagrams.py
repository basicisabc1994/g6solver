# SPDX-License-Identifier: Apache-2.0
"""Diagrams in the guide (owner, 2026-10-19: "diagrams and flowcharts are your friend ... integrate this into the
guide"). A ```mermaid fence becomes a figure Mermaid draws in the browser; every other fence is still coloured by
Pygments; every diagram has a caption; every name a diagram uses exists in the open-source code."""
from __future__ import annotations

import html
import importlib
import re
from pathlib import Path

import pytest

from pages import views
from pages.guard import saas_hits

OSS = Path(__file__).resolve().parents[2]
STATIC = OSS / "web" / "static"
_FIGURE = re.compile(r'<figure class="diagram">\s*<pre class="mermaid">(.*?)</pre>\s*(?:<figcaption>(.*?)</figcaption>)?'
                     r"\s*</figure>", re.DOTALL)


@pytest.fixture
def content(tmp_path, monkeypatch):
    monkeypatch.setattr(views, "CONTENT_DIR", tmp_path)
    return tmp_path


def test_a_mermaid_fence_becomes_a_figure_with_its_source_escaped(content):
    (content / "d.md").write_text(
        "# T\n\n```mermaid How a & b meet\nflowchart LR\n  A[\"a < b & c\"] --> B\n\n  B -.-> C\n```\n\n"
        "```python\ndef f():\n    return 1\n```\n\n```mermaid\nflowchart TD\n  X --> Y\n```\n", encoding="utf-8")
    body, _ = views.render_markdown("d.md")
    figures = _FIGURE.findall(body)
    assert figures[0] == ('flowchart LR\n  A["a &lt; b &amp; c"] --&gt; B\n\n  B -.-&gt; C', "How a &amp; b meet")
    assert figures[1] == ("flowchart TD\n  X --&gt; Y", "")                 # no caption, no empty figcaption
    assert '<figcaption></figcaption>' not in body
    # the python fence is still Pygments' tokens, and the diagram source is not
    assert '<div class="highlight"><pre><span></span><code><span class="k">def</span>' in body
    assert body.count('class="highlight"') == 1


def test_a_fence_that_only_mentions_mermaid_is_left_alone(content):
    (content / "d.md").write_text("```text\n```mermaid is how a diagram starts\n```\n", encoding="utf-8")
    body, _ = views.render_markdown("d.md")
    assert "<figure" not in body and "mermaid is how a diagram starts" in body


def _diagrams(client) -> list[tuple[str, str]]:
    page = client.get("/guide/view/").content.decode()
    return [(html.unescape(source), html.unescape(caption)) for source, caption in _FIGURE.findall(page)]


def test_the_guide_explains_with_diagrams(client):
    diagrams = _diagrams(client)
    assert 6 <= len(diagrams) <= 10
    for source, caption in diagrams:
        assert caption.strip() and len(caption) < 160, source[:60]          # one line saying what it shows
        assert re.match(r"(flowchart|sequenceDiagram|stateDiagram-v2)\b", source.strip()), source[:60]
    kinds = {source.split()[0] for source, _ in diagrams}
    assert {"flowchart", "sequenceDiagram"} <= kinds


def test_no_diagram_names_the_former_product(client):
    for source, caption in _diagrams(client):
        text = f"{source}\n{caption}"
        assert "mvp." not in text and saas_hits(text) == [], text[:80]
        for term in ("account", "subscription", "billing", "price", "g6solver", "cloud", "API key"):
            assert term.lower() not in text.lower(), (term, text[:80])


def test_every_name_in_a_diagram_exists_in_the_code(client):
    mcp = (OSS / "src" / "g6" / "mcp" / "__init__.py").read_text(encoding="utf-8")
    seen = set()
    for source, caption in _diagrams(client):
        text = f"{source}\n{caption}"
        for tool in re.findall(r"\b(?:g6_[a-z_]+|metaharness_[a-z_]+)\b", text):
            assert f"def {tool}(" in mcp, tool
            seen.add(tool)
        for dotted in re.findall(r"\bg6(?:\.[A-Za-z_]\w*)+", text):
            parts = dotted.split(".")
            for cut in range(len(parts), 0, -1):            # the longest importable module, then attributes
                try:
                    found = importlib.import_module(".".join(parts[:cut]))
                except ImportError:
                    continue
                for attr in parts[cut:]:
                    found = getattr(found, attr)               # AttributeError names what does not exist
                break
            else:
                pytest.fail(dotted)
            seen.add(dotted)
    assert {"g6_invoke", "metaharness_approve"} <= seen and any(n.startswith("g6.harness") for n in seen)


def test_mermaid_is_themed_from_the_site_and_loaded_only_where_a_diagram_is():
    js = (STATIC / "js" / "mermaid-init.js").read_text(encoding="utf-8")
    assert "theme: 'dark'" not in js and "theme: 'base'" in js and "securityLevel: 'strict'" in js
    assert "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.esm.min.mjs" in js
    assert re.search(r"import\(\s*['\"]https://cdn\.jsdelivr", js)              # fetched only when a page has one
    for token in ("--paper", "--ink", "--rule", "--teal", "--font", "prefers-color-scheme"):
        assert token in js, token
    css = (STATIC / "css" / "content.css").read_text(encoding="utf-8")
    assert ".diagram" in css and "overflow-x: auto" in css[css.index(".diagram"):]
