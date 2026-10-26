# SPDX-License-Identifier: Apache-2.0
"""The one table of public pages. The URL routes, the sitemap and the static export all read it, so they cannot
disagree."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pages import failure_map


@dataclass(frozen=True)
class Page:
    name: str                                            # URL name within its namespace
    namespace: Literal["pages", "docs_hub", "blog"]
    path: str                                            # full site path, no leading slash: "about/", "docs/full/"
    kind: Literal["template", "markdown", "placeholder", "failure_mode"]
    source: str                                          # a template, a content/*.md name, a placeholder's title, or a
                                                         # failure-mode code


def _template(name: str, path: str, namespace: str = "pages") -> Page:
    folder = {"pages": "pages", "docs_hub": "docs_hub", "blog": "blog"}[namespace]
    return Page(name, namespace, path, "template", f"{folder}/{name}.html")  # type: ignore[arg-type]


# The 21 essays, as on g6solver.com: (URL name and template, path).
_ESSAYS = ("ai_cant_think", "hallucination_is_design", "why_benchmarks_lie", "context_window_is_not_memory",
           "scaling_laws_ceiling", "metacognition_missing_skill", "epistemology_of_ai", "two_types_of_thinking",
           "learning_without_forgetting", "self_improvement_safety", "alignment_is_engineering",
           "move_fast_break_things", "ai_needs_middleware", "algorithms_missing_intelligence",
           "formal_verification_safety_net", "economics_of_intelligence", "democratising_agi",
           "augmentation_not_replacement", "map_is_not_territory", "antifragile_ai", "periodic_table_of_intelligence")

PAGES: tuple[Page, ...] = (
    _template("landing", ""),
    *(_template(n, f"{n}/") for n in ("about", "principles", "security", "license", "privacy", "guide", "research",
                                      "components")),
    Page("guide_view", "pages", "guide/view/", "markdown", "guide.md"),
    _template("get_started", "get-started/"),            # its bundle section is content/install.md
    *(Page(n, "pages", f"{n}/", "markdown", f"{n}.md")
      for n in ("faq", "metaharness", "bundles", "developers", "contribute")),
    _template("graph", "graph/"),                        # the viewer itself is a static file: static/graph/app/
    _template("failure_modes", "failure-modes/"),        # the map; then one page per mode, from the generated map
    *(Page(f"failure_mode_{m['code']}", "pages", f"failure-modes/{m['code']}/", "failure_mode", m["code"])
      for m in failure_map.modes()),
    _template("landing", "docs/", "docs_hub"),
    Page("full", "docs_hub", "docs/full/", "placeholder", "The full documentation"),
    Page("index", "blog", "essays/", "template", "blog/index.html"),
    *(_template(n, f"essays/{n.replace('_', '-')}/", "blog") for n in _ESSAYS),
)

# Old URLs of dropped pages -> where they go now (Cloudflare `_redirects` syntax: "*" matches the rest of a path).
REDIRECTS: tuple[tuple[str, str], ...] = (
    ("/pricing/", "/"),
    ("/accounts/*", "/"),
    ("/billing/*", "/"),
    ("/marketplace/*", "/"),
    ("/onboarding/", "/"),
    ("/dashboard/", "/"),
    ("/partners/apply/", "/"),
    ("/journeys/*", "/"),
    ("/careers/", "/about/"),
    ("/terms/", "/license/"),
    ("/features/", "/components/"),
    ("/setup/", "/get-started/"),
    ("/download/", "/get-started/"),
    ("/install/", "/get-started/"),
    ("/docs/getting-started/", "/get-started/"),
    ("/docs/architecture/", "/components/"),
    ("/docs/mcp/", "/developers/"),
    ("/docs/enhance-agent/", "/developers/"),
    ("/docs/build-workflow/", "/developers/"),
    ("/benchmarks/", "/research/"),
    ("/benchmarks/*", "/research/"),
    ("/failures/", "/failure-modes/"),
    ("/context/", "/docs/"),
    ("/docs/api/", "/docs/"),
    ("/docs/tutorials/", "/docs/"),
    ("/guide/download/*", "/guide/"),
    ("/blog/*", "/essays/:splat"),                       # the blog is called Essays (owner, 2026-10-19)
)


def public_paths() -> list[str]:
    return ["/" + page.path for page in PAGES]
