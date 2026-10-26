# SPDX-License-Identifier: Apache-2.0
"""The repository graph page: Understand-Anything's own viewer, built for this site, over a graph of the public code
whose machine-written text says so and reads like the rest of the site."""
from __future__ import annotations

import importlib.util
import json
import re

import pytest
from django.conf import settings

from pages.guard import saas_hits
from pages.management.commands.build_static import _headers
from pages.routes import PAGES

GRAPH = settings.STATICFILES_DIRS[0] / "graph"


@pytest.fixture(scope="module")
def graph() -> dict:
    return json.loads((GRAPH / "knowledge-graph.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def meta() -> dict:
    return json.loads((GRAPH / "meta.json").read_text(encoding="utf-8"))


def _prose(graph: dict) -> list[str]:
    """Every piece of text the model wrote: node summaries and language notes, layer and tour text, the project's
    description. Names, ids and paths are the code's own, which the export scan covers."""
    return [graph["project"]["description"],
            *(n.get(k, "") for n in graph["nodes"] for k in ("summary", "languageNotes")),
            *(f"{x['name']} {x['description']}" for x in graph["layers"]),
            *(f"{x['title']} {x['description']}" for x in graph["tour"])]


def test_the_graph_page_is_a_page_not_a_placeholder():
    assert next(p for p in PAGES if p.name == "graph").kind == "template"


def test_the_footer_and_the_components_page_link_to_the_graph(client):
    assert 'href="/graph/"' in client.get("/").content.decode()
    assert 'href="/graph/"' in client.get("/components/").content.decode()


def test_the_graph_page_embeds_the_viewer_and_says_its_text_is_machine_written(client, meta):
    html = client.get("/graph/").content.decode()
    assert '<iframe' in html and 'src="/static/graph/app/index.html"' in html
    assert 'href="/static/graph/app/index.html"' in html                          # open it full screen
    assert "Machine-generated, not reviewed." in html
    assert meta["gitCommitHash"] in html
    assert "Understand-Anything" in html and 'href="/static/graph/app/LICENSE.txt"' in html and "MIT" in html


def test_the_graph_page_lists_the_layers_the_graph_holds(client, graph):
    html = client.get("/graph/").content.decode()
    for layer in graph["layers"]:
        assert layer["name"] in html
    assert f"{len(graph['nodes']):,}" in html and f"{len(graph['layers'])} layers" in html
    # The layers' text is on the page itself, so it follows the site's rule: no abbreviations beyond these names.
    shown = {w for x in graph["layers"] for w in re.findall(r"\b[A-Z]{2,}\b", x["description"])}
    assert shown <= {"MCP", "README", "HTML", "URL"}, shown


def test_the_graph_is_of_the_public_code_with_relative_paths_only(graph):
    paths = [n["filePath"] for n in graph["nodes"]]
    assert paths and all(p and not p.startswith(("/", "\\")) and ":" not in p for p in paths)
    text = json.dumps(graph)
    for private in ("mvp_v1", ".claude", "AppData", "/Users/", "mvp."):
        assert private not in text, private
    # personal names and paths: the release scan's own rules, so this file need not spell them out
    spec = importlib.util.spec_from_file_location("export_public", settings.BASE_DIR.parent / "tools" / "export_public.py")
    export_public = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(export_public)
    assert export_public.scan(GRAPH) == []


def test_every_file_the_graph_names_is_in_the_tree(graph):
    # a renamed module must not live on in the graph (the "experimental" paths were renamed in the copy pass)
    root = settings.BASE_DIR.parent
    missing = sorted({n["filePath"] for n in graph["nodes"] if not (root / n["filePath"]).exists()})
    assert missing == []
    ids = {n["id"] for n in graph["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in graph["edges"])
    assert all(i in ids for layer in graph["layers"] for i in layer.get("nodeIds", ()))
    assert not re.search(r"experimental", json.dumps(graph), re.IGNORECASE)


def test_the_graphs_machine_written_text_passes_the_site_guard(graph):
    prose = "\n".join(_prose(graph))
    assert saas_hits(prose) == []
    assert not re.search(r"\bexperimental\b", prose, re.IGNORECASE)


def test_the_viewer_is_built_for_this_site_with_its_licence():
    app = GRAPH / "app"
    index = (app / "index.html").read_text(encoding="utf-8")
    assert '/static/graph/app/assets/' in index
    bundle = "".join(p.read_text(encoding="utf-8") for p in (app / "assets").glob("index-*.js"))
    assert "/static/graph/knowledge-graph.json" in bundle and "/static/graph/domain-graph.json" in bundle
    assert not (app / "knowledge-graph.json").exists()                  # the plugin's own sample graph is left out
    notice = (app / "LICENSE.txt").read_text(encoding="utf-8")
    assert "MIT License" in notice and "Lum1104/Understand-Anything" in notice


def test_the_security_policy_lets_the_site_frame_only_itself():
    policy = _headers([])
    assert "frame-ancestors 'self'" in policy and "frame-ancestors 'none'" not in policy
