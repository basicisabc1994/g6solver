# SPDX-License-Identifier: Apache-2.0
"""Owner, 2026-10-23: "comment out any planned extensions leaving only the live completed extensions on the website".

One switch, ``pages.extensions.SHOW_PLANNED`` (off), hides every planned row and every word about extensions to come.
The data keeps them (``data/component_catalogue.json``, ``catalog/failure_modes.json``), and turning the switch on
lists them again. A row already merged into a package in the library is live: it is shown as part of that package.
A built component still waiting for its certificate is not planned, so it is shown whatever the switch says."""
from __future__ import annotations

import html
import importlib.util
import re

import pytest

from pages import extensions, failure_map
from pages.routes import public_paths

# What the pages said of a planned row, a planned section or extensions to come (each fails while it is still said).
PLANNED_WORDS = ("Planned to guard against", "Planned as", "Planned to join", "Planned into", "planned pieces",
                 "the rest are planned", "Planned in", "planned to guard", "or is planned to", "one at a time")
# Studies, not extensions: the Research page lists the registered studies still to run (V3 to V5) as planned, and the
# bundles page points to the study planned for them. Neither names an extension. The essays use the word of software
# in general ("carefully planned"), never of G6's extensions; the checks above still read them.
STUDY_PAGES = {"/research/", "/bundles/"}
ESSAYS = "/essays/"


def _text(page: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page)))


def _planned() -> list[dict]:
    return [r for r in extensions.rows(planned=True) if r["planned"]]


def _unbuilt(package: str) -> bool:
    try:
        return importlib.util.find_spec(package) is None
    except (ImportError, ValueError):
        return True


@pytest.fixture(scope="module")
def rendered() -> dict[str, str]:
    """Every public page, rendered once with the switch as shipped."""
    from django.test import Client

    assert extensions.SHOW_PLANNED is False
    client = Client()
    return {path: client.get(path).content.decode() for path in public_paths()}


def test_the_switch_is_off():
    # one switch, documented beside the listing it governs
    assert extensions.SHOW_PLANNED is False
    assert "SHOW_PLANNED" in (extensions.__doc__ or "")


def test_no_public_page_names_a_planned_row_or_calls_an_extension_planned(rendered):
    planned = _planned()
    assert len(planned) > 50                                         # the catalogue still holds them
    names = {r["name"] for r in planned}
    packages = {r["package"] for r in planned if extensions._own(r)}
    packages |= {p for m in failure_map.modes() for p in m["planned"] if _unbuilt(p)}
    for path, page in rendered.items():
        text = _text(page)
        assert [n for n in sorted(names) if f'id="ext-{n}"' in page or f"<code>{n}</code>" in page
                or f'<code class="cmp__name">{n}</code>' in page] == [], path
        assert [p for p in sorted(packages) if f"<code>{p}</code>" in page] == [], path
        assert [w for w in PLANNED_WORDS if w.lower() in text.lower()] == [], path
        assert path in STUDY_PAGES or path.startswith(ESSAYS) or not re.search(r"(?i)\bplanned\b", text), path


def test_with_the_switch_on_the_planned_listing_returns(client, with_planned):
    page = client.get("/components/").content.decode()
    planned = _planned()
    assert [r["name"] for r in planned if f'id="ext-{r["name"]}"' not in page] == []
    assert "Planned as <code>" in page and "the rest are planned" in page
    listing = client.get("/failure-modes/").content.decode()
    assert 'id="planned"' in listing and "<dt>Planned</dt>" in listing
    mode = next(m for m in failure_map.modes() if m["planned"] and not m["guarded_by"])
    assert 'id="planned"' in client.get(f"/failure-modes/{mode['code']}/").content.decode()
    assert "one at a time" in _text(client.get("/faq/").content.decode())


def test_a_row_merged_into_a_package_in_the_library_is_shown_as_part_of_it(client):
    # the known defect: rows already merged into a package in the library were shown as planned
    rows = {r["name"]: r for r in extensions.rows(planned=True)}
    assert rows["agent_router"]["merged"] and rows["agent_router"]["package"] == "g6.llm_router"
    assert not rows["agent_router"]["planned"]
    # a family's shared code exists as soon as any member does, so it never makes a row merged
    assert rows["multimodal"]["package"] == "g6.media" and not rows["multimodal"]["merged"]
    # a row in a vetted area waits for its package's vetting like any other (g6.core.vetting)
    assert rows["domain_standards"]["vetting"] and not rows["domain_standards"]["merged"]
    merged = [r for r in rows.values() if r["merged"]]
    assert len(merged) > 40 and all(not extensions._own(r) for r in merged)
    page = client.get("/components/").content.decode()
    router = page.split('id="ext-llm_router"', 1)[1].split('id="ext-', 2)
    assert "Merged into it:" in router[0] and router[1].startswith("agent_router")
    item = page.split('id="ext-agent_router"', 1)[1].split("</li>", 1)[0]
    assert "Merged into <code>g6.llm_router</code>" in item and "lanned" not in item
    core = page.split('id="into-core"', 1)[1].split("</details>", 1)[0]
    into = [r for r in merged if r["family"] == extensions.CORE]
    assert into and all(f'id="ext-{r["name"]}"' in core for r in into)
    assert ">Merged into the core harness<" in page


def test_the_counts_are_of_what_is_shown(client):
    page = client.get("/components/").content.decode()
    counts, own = extensions.counts(), extensions.extensions()
    assert counts["extensions"] == len(own) == sum(1 for r in own if r["in_library"] or r["awaiting_review"])
    assert f"<dt>Extensions</dt><dd>{counts['extensions']}</dd>" in page
    every, own_every = extensions.rows(planned=True), extensions.extensions(planned=True)
    assert len(own_every) > len(own) and len(every) > len(extensions.rows())
    for total in (len(every), len(own_every)):                     # 194 rows, 96 extensions: never on the page
        assert f">{total}<" not in page and f" {total} " not in _text(page), total
    assert counts["joining"] == counts["merged"] == sum(1 for r in extensions.rows() if r["merged"])


def test_the_failure_map_says_not_guarded_yet_without_naming_a_planned_package(client):
    listing = client.get("/failure-modes/").content.decode()
    assert 'id="planned"' not in listing and "<dt>Planned</dt>" not in listing
    context = failure_map.context()
    assert [g["key"] for g in context["groups"]] == ["guarded", "unguarded"]
    assert context["counts"]["guarded"] + context["counts"]["unguarded"] == context["counts"]["total"]
    mode = next(m for m in failure_map.modes() if m["planned"] and not m["guarded_by"] and not m["awaiting_review"])
    group = listing.split('id="unguarded"', 1)[1].split("</section>", 1)[0]
    line = group.split(f'id="mode-{mode["code"]}"', 1)[1].split("</li>", 1)[0]
    assert [p for p in mode["planned"] if p in line] == []
    page = client.get(f"/failure-modes/{mode['code']}/").content.decode()
    assert 'id="planned"' not in page and "Nothing in the library guards against this yet" in _text(page)
    assert [p for p in mode["planned"] if p in page] == []


def test_a_capability_card_lists_only_what_is_shown(client):
    page = client.get("/components/").content.decode()
    groups, status = extensions.capabilities()
    shown = {r["name"] for r in extensions.rows()}
    # the cards name planned rows too; with the switch off they are left out (fails while every named row is listed)
    assert sum(len(g["rows"]) for g in groups) < sum(len(c["extensions"]) for c in extensions.CAPABILITIES)
    for cap in extensions.CAPABILITIES:
        group = next(g for g in groups if g["key"] == cap["key"])
        assert {r["name"] for r in group["rows"]} == {n for n in cap["extensions"] if n in shown}, cap["key"]
        drawn = f'id="cap-{cap["key"]}"' in page
        assert drawn is bool(cap["library"] or group["rows"]), cap["key"]   # nothing to show: no card
    assert status["total"] == sum(1 for g in groups for r in g["rows"] if extensions._own(r))


def test_the_markdown_pages_hide_what_is_still_to_come(client, monkeypatch):
    from pages import views

    off = views.render_markdown("faq.md")[0]
    monkeypatch.setattr(extensions, "SHOW_PLANNED", True)
    on = views.render_markdown("faq.md")[0]
    assert "one at a time" not in off and "one at a time" in on
    for body in (off, on):                                           # the marks themselves never reach a page
        assert "<!-- planned -->" not in body and "<!-- /planned -->" not in body
