# SPDX-License-Identifier: Apache-2.0
"""Every public page of the website renders, from the one page table."""
from __future__ import annotations

import html
import re

import pytest

from pages.guard import saas_hits
from pages.routes import PAGES, REDIRECTS, public_paths


def test_the_guard_finds_hosted_text_anywhere():
    assert saas_hits('<a data-x="Start free trial">') == ["free trial"]
    assert saas_hits("<code>claude mcp add g6 https://g6solver.com/mcp/sse</code>") == ["g6solver.com/mcp"]
    assert saas_hits("<p>frontier tiers</p>") == []             # whole words only: "tier" is not "tiers"/"frontier"
    # structured data a search engine reads but a visitor never sees: a price offer is SaaS text too
    assert saas_hits('{"@type": "Offer", "name": "Builder", "price": "50", "priceCurrency": "AUD"}') == ["priceCurrency"]


def test_only_the_named_legitimate_phrases_are_allowed():
    legit = ('<h3 id="tier-2-self-correcting">Tier 2: Self-Correcting</h3> Tiers 2 and 3 below; use your own API key;'
             " log failed login attempts; free and open, no signup; per-token pricing; Stripe all built;"
             " there is no hosted service; <strong>Hardcoded API keys</strong> that anyone can find;"
             " Are API keys and secrets in environment variables; No API keys, passwords, or tokens in the code;"
             " Whether the user needs to be logged in; model providers cannot extract premium rents")
    assert saas_hits(legit) == []
    for hosted in ("the Builder tier", "generate an API key", "Sign up free", "see our pricing", "pay with Stripe"):
        assert saas_hits(hosted), hosted


def test_the_guard_reads_inflections_entities_and_hosted_text():
    # the final review's probes: each slipped past the first guard
    for hosted in ("manage your API keys", "after signing up", "once you have signed up", "while logged in",
                   "free trials", "via our hosted MCP server", "Premium harnesses may degrade", "Sign&nbsp;up today",
                   "your API&#8209;key", "sign&#xAD;in"):
        assert saas_hits(hosted), hosted


@pytest.mark.parametrize("path", public_paths())
def test_no_page_reads_like_saas(client, path):
    assert saas_hits(client.get(path).content.decode()) == [], path


@pytest.mark.parametrize("path", public_paths())
def test_every_route_renders(client, path):
    assert client.get(path).status_code == 200, path


@pytest.mark.parametrize("path", public_paths())
def test_no_template_syntax_leaks(client, path):
    html = client.get(path).content.decode()
    assert "{#" not in html and "{%" not in html and "{{" not in html, path
    # MkDocs-only Markdown (collapsible "???" notes, "===" tabs) that Python-Markdown prints as text
    assert not re.search(r'<p>(?:\?\?\?|!!!)\+? |<p>=== "', html), path


@pytest.mark.parametrize("path", public_paths())
def test_no_page_says_to_pip_install_g6_from_pypi(client, path):
    # G6 is not on PyPI yet (pypi.org/pypi/g6/json is a 404): until the first release, install from a clone
    text = html.unescape(client.get(path).content.decode())
    assert not re.search(r"""pip install\s+(?:-U\s+|--upgrade\s+)?["']?g6\b""", text), path


@pytest.mark.parametrize("path", public_paths())
def test_no_page_abbreviates_the_command_line(client, path):
    # owner rule: no abbreviations in public copy; a product's own name (Gemini CLI, AWS CLI) is its name
    text = html.unescape(client.get(path).content.decode())
    assert not re.search(r"\b(?:a|the|its|Codex) CLI\b", text), path


@pytest.mark.parametrize("path", public_paths())
def test_no_page_tells_of_a_former_product(client, path):
    # owner, 2026-10-19: "get rid of any references to the 'former product' ... there is just one thing now, the open
    # source framework"
    text = html.unescape(client.get(path).content.decode())
    assert not re.search(r"(?i)\b(?:former|earlier|old|previous|hosted) (?:g6solver\.com )?product\b|\barchived? "
                         r"(?:result|benchmark)", text), path


def test_relative_markdown_links_become_site_urls(client):
    page = client.get("/bundles/").content.decode()
    assert 'href="/research/"' in page and 'href="/contribute/"' in page and "../" not in page
    assert 'href="/bundles/"' in client.get("/guide/view/").content.decode()


def test_the_blog_is_called_essays(client):
    # owner, 2026-10-19: "change 'blog' to 'essays'"; old /blog/ links keep working through a redirect
    assert "/essays/" in public_paths() and not any(p.startswith("/blog/") for p in public_paths())
    assert ("/blog/*", "/essays/:splat") in REDIRECTS
    nav = client.get("/").content.decode()
    assert ">Essays<" in nav and ">Blog<" not in nav


def test_dropped_pages_redirect_somewhere_that_exists():
    paths = set(public_paths())
    for old, new in REDIRECTS:
        target = new.split(":splat")[0]                  # "/essays/:splat" keeps the rest of the old path
        assert target in paths, (old, new)
    assert {"/pricing/", "/accounts/*", "/billing/*", "/benchmarks/", "/features/"} <= {o for o, _ in REDIRECTS}
    # "Build a workflow" described the hosted platform and its sign-up; the developers page covers running a workflow
    assert ("/docs/build-workflow/", "/developers/") in REDIRECTS and "/docs/build-workflow/" not in paths


def test_the_ported_site_is_all_there():
    names = {(p.namespace, p.name) for p in PAGES}
    kept = {"landing", "about", "principles", "faq", "security", "license", "privacy", "guide", "guide_view",
            "get_started", "metaharness", "research", "bundles", "developers", "contribute", "components", "graph"}
    assert {("pages", n) for n in kept} <= names
    # The docs-hub pages that described the hosted service (getting started, architecture, MCP, enhance your agent,
    # build a workflow) redirect to the open-source pages; the deep docs rewrite them.
    assert {("docs_hub", n) for n in ("landing", "full")} <= names
    assert len([n for ns, n in names if ns == "blog"]) == 22                 # the index and 21 essays


def test_the_page_table_has_unique_paths_and_names():
    paths = public_paths()
    assert len(paths) == len(set(paths))
    assert len({(p.namespace, p.name) for p in PAGES}) == len(PAGES)


def test_the_home_page_and_a_placeholder_render(client):
    assert client.get("/").status_code == 200
    reply = client.get("/docs/full/")
    assert reply.status_code == 200 and b"being rebuilt" in reply.content


def test_settings_have_no_database_auth_or_analytics():
    from django.conf import settings

    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.dummy"   # Django's "no database"
    assert "django.contrib.auth" not in settings.INSTALLED_APPS
    assert settings.SITE_URL == "https://g6solver.com" and not hasattr(settings, "GA_MEASUREMENT_ID")
