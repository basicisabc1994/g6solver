# SPDX-License-Identifier: Apache-2.0
"""One view per kind of page in ``pages.routes.PAGES``."""
from __future__ import annotations

import html
import json
import random
import re
from datetime import datetime
from pathlib import Path

import markdown
from django.conf import settings
from django.shortcuts import render
from django.utils.safestring import mark_safe

from pages import catalogue, clients, evidence, extensions, failure_map, narrative
from pages.routes import Page

CONTENT_DIR = Path(__file__).resolve().parents[1] / "content"   # read at request time, so a test can point it elsewhere


def _weights(rows: int = 10, cols: int = 7) -> str:
    """An illustration of what a model's learning looks like: rows of small signed numbers, the same on every build."""
    rng = random.Random(20261020)
    return "\n".join("  ".join(f"{rng.gauss(0, 0.04):+.4f}" for _ in range(cols)) for _ in range(rows))


def _home() -> dict:
    _, groups = evidence.headline()
    best, worst = evidence.sweep_extremes()
    return {"lattice": catalogue.lattice(), "table": catalogue.table(), "weights": _weights(),
            "change": evidence.learned_change(), "headline_groups": groups, "facts": evidence.headline_facts(),
            "best": best, "worst": worst, "speed": evidence.latest_round_speed(), "both": evidence.both_modes(),
            "limits": [dict(limit, essay=limit["essay"] and narrative.essay_path(limit["essay"]),
                            named=[(part, catalogue.PLAIN[part][0]) for part in limit["parts"]])
                       for limit in narrative.LIMITS]}


def _components() -> dict:
    from g6.reliability_pipeline.premortem import PREMORTEM_FAILURE_MODES

    from g6.core import vetting

    capabilities, status = extensions.capabilities()
    core = catalogue.components()
    counts = extensions.counts()
    return {"core": core, "core_groups": catalogue.core_groups(), "core_names": {c["name"] for c in core},
            "families": extensions.families(), "into_core": extensions.into_core(),
            "extension_count": counts["extensions"], "extension_counts": counts,
            "premortem_modes": PREMORTEM_FAILURE_MODES, "capabilities": capabilities, "capability_status": status,
            "show_planned": extensions.SHOW_PLANNED, "no_qualified_review": vetting.NO_QUALIFIED_REVIEW}


def _get_started() -> dict:
    body, _ = render_markdown("install.md")
    return {"clients": clients.CLIENTS, "models": clients.MODELS, "bundle_guide": mark_safe(_H1.sub("", body, count=1))}


def _graph() -> dict:
    """The repository graph's facts, read from the graph the viewer loads (static/graph/), so the page cannot drift."""
    folder = settings.STATICFILES_DIRS[0] / "graph"
    graph = json.loads((folder / "knowledge-graph.json").read_text(encoding="utf-8"))
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    when = datetime.fromisoformat(meta["lastAnalyzedAt"].replace("Z", "+00:00"))
    return {"commit": meta["gitCommitHash"], "analysed": f"{when.day} {when:%B %Y}", "files": meta["analyzedFiles"],
            "nodes": f"{len(graph['nodes']):,}", "tour": len(graph["tour"]),
            "layers": [{"name": x["name"], "description": x["description"], "files": len(x["nodeIds"])}
                       for x in graph["layers"]]}


# Template pages whose numbers or text are read from files or the registry at render time: (namespace, name) -> context.
CONTEXTS = {("pages", "research"): evidence.context, ("pages", "landing"): _home,
            ("pages", "get_started"): _get_started, ("pages", "components"): _components, ("pages", "graph"): _graph,
            ("pages", "failure_modes"): failure_map.context}


def template_page(request, page: Page):
    build = CONTEXTS.get((page.namespace, page.name))
    return render(request, page.source, build() if build else {})


# A link to another page's Markdown file ("research.md#x", "../bundles/index.md") -> that page's URL ("/research/#x").
# The pages were one folder deep on the MkDocs site, so a leading "../" or "./" says nothing about the target here.
_MD_LINK = re.compile(r'href="(?!https?:|#|/)(?:\.\.?/)*([A-Za-z0-9_\-/]+?)(?:/index)?\.md(#[^"]*)?"')
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.DOTALL)
_FRONT_MATTER = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", re.DOTALL)    # MkDocs-style metadata, never page text
_FENCE = re.compile(r"(`{3,}|~{3,})\s*([\w+-]*)(.*)")
# Words about extensions still to come, kept in the Markdown and shown only while extensions.SHOW_PLANNED is on
_PLANNED = re.compile(r"<!-- planned -->(.*?)<!-- /planned -->", re.DOTALL)


def _planned(text: str) -> str:
    """The text with each ``<!-- planned -->...<!-- /planned -->`` span kept (its marks taken out) while the switch is
    on, or taken out whole while it is off."""
    return _PLANNED.sub(lambda m: m.group(1) if extensions.SHOW_PLANNED else "", text)


def _diagrams(text: str) -> str:
    """Each ```mermaid fence as a figure that static/js/mermaid-init.js draws: its source escaped, so it reads as text
    where the script does not run, and the rest of the fence's first line as the caption. Other fences are untouched
    (codehilite colours them), and so is anything inside them."""
    lines, out, i = text.split("\n"), [], 0
    while i < len(lines):
        found = _FENCE.fullmatch(lines[i].rstrip("\r"))
        if not found:
            out.append(lines[i])
            i += 1
            continue
        mark = found.group(1)
        close = re.compile(re.escape(mark[0]) + "{%d,}\\s*" % len(mark))
        end = next((j for j in range(i + 1, len(lines)) if close.fullmatch(lines[j])), len(lines))
        if found.group(2) != "mermaid":
            out += lines[i:end + 1]
        else:
            caption = found.group(3).strip()
            out += ["", '<figure class="diagram">',
                    '<pre class="mermaid">' + html.escape("\n".join(lines[i + 1:end]), quote=False) + "</pre>",
                    *([f"<figcaption>{html.escape(caption)}</figcaption>"] if caption else []), "</figure>", ""]
        i = end + 1
    return "\n".join(out)


def render_markdown(name: str) -> tuple[str, str]:
    """A content/*.md file as HTML with its links made site URLs, and its title (the first heading)."""
    text = _diagrams(_planned(_FRONT_MATTER.sub("", (CONTENT_DIR / name).read_text(encoding="utf-8"), count=1)))
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "codehilite", "toc", "admonition", "attr_list"],
                             extension_configs={"codehilite": {"guess_lang": False, "css_class": "highlight"}})
    # Pygments' token classes, coloured in content.css
    body = _MD_LINK.sub(lambda m: f'href="/{m.group(1).strip("/")}/{m.group(2) or ""}"', body)
    heading = _H1.search(body)
    return body, (re.sub(r"<[^>]+>", "", heading.group(1)).strip() if heading else "G6")


def markdown_page(request, page: Page):
    body, title = render_markdown(page.source)
    return render(request, "markdown_page.html", {"body": mark_safe(body), "page": page, "title": title})


def placeholder_page(request, page: Page):
    return render(request, "placeholder.html", {"title": page.source})


def failure_mode_page(request, page: Page):
    return render(request, "pages/failure_mode.html", failure_map.mode_context(page.source))


VIEWS = {"template": template_page, "markdown": markdown_page, "placeholder": placeholder_page,
         "failure_mode": failure_mode_page}


def view_for(page: Page):
    return lambda request: VIEWS[page.kind](request, page)
