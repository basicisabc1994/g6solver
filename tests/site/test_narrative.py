# SPDX-License-Identifier: Apache-2.0
"""The site's narrative (owner, 2026-10-20): the landing page "should really emphasise our why first and the problem we
are solving - in fact every page should have this narrative structure except for the docs and essays page"; G6 has two
parts, the meta-harness and the components; and each limit of a language model is answered by named components."""
from __future__ import annotations

import html
import re

from pages import catalogue, narrative
from pages.routes import public_paths

DOCS = {"/docs/", "/docs/full/", "/guide/", "/guide/view/", "/developers/", "/contribute/"}
WHY_FIRST = ["/", "/about/", "/principles/", "/security/", "/license/", "/privacy/", "/research/", "/components/",
             "/get-started/", "/faq/", "/metaharness/", "/bundles/", "/graph/", "/failure-modes/"]
_WHY = re.compile(r'<p class="[^"]*\bwhy\b')


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)))


def test_every_page_but_the_docs_and_the_essays_is_held_to_the_narrative():
    essays = {p for p in public_paths() if p.startswith("/essays/")}
    # one generated reference page per failure mode, like the docs; the map itself is held to the narrative
    modes = {p for p in public_paths() if p.startswith("/failure-modes/") and p != "/failure-modes/"}
    assert set(public_paths()) - DOCS - essays - modes == set(WHY_FIRST)


def test_each_of_those_pages_says_why_before_any_section_starts(client):
    for path in WHY_FIRST:
        main = client.get(path).content.decode().split('<main id="main-content">', 1)[1]
        why, section = _WHY.search(main), re.search(r"<h2\b", main)
        assert why, path
        assert section is None or why.start() < section.start(), path
        assert len(_text(main[why.start():].split("</p>", 1)[0])) > 60, path        # a sentence, not a label


def test_the_home_page_starts_from_the_problem_then_says_what_g6_is(client):
    page = client.get("/").content.decode()
    hero = page.split('<header class="hero">', 1)[1].split("</header>", 1)[0]
    # owner, 2026-10-23 (feedback, the Gaddie pitch): the hero says what you can now do, the subheading names who it is
    # for and what G6 is, then the problem it answers
    assert "Now you can<br>make AI agents<br>check their<br>own work." in hero and "G6 engineers itself" not in _text(hero)
    # owner, 2026-10-23: "way too long and way too wordy - pare this back aggressively": one subheading, nothing more
    ledes = re.findall(r'<p class="(lede[^"]*)">(.*?)</p>', hero, re.S)
    assert len(ledes) == 1 and "why" in ledes[0][0].split()
    sub = _text(ledes[0][1])
    # owner, 2026-10-29 (minimal launch): the subheading names the two clients and what ships, and keeps a limit
    assert sub.startswith("For developers using Claude Code and Codex")
    assert "does not prove a change correct" in sub and len(sub.split()) <= 65
    order = [page.index(mark) for mark in ('class="hero"', "AI builds.<br>Evidence<br>decides.", 'id="loop"', 'id="limits"',
                                            'id="two-parts"', 'id="learning"', 'id="gives"', 'id="tests"')]
    assert order == sorted(order)


def test_each_limit_is_answered_by_components_that_exist(client):
    names = {row["name"] for row in catalogue.components()}
    section = client.get("/").content.decode().split('id="limits"', 1)[1].split("</section>", 1)[0]
    # the owner's list: memory, learning, hallucination, reasoning, benchmarks against the real world, context, cost,
    # access to frontier models, metacognition, forgetting, safe self-improvement, algorithm synthesis, formal logic
    assert {limit["key"] for limit in narrative.LIMITS} >= {"hallucination", "memory", "learning", "reasoning",
                                                           "algorithms", "benchmarks", "metacognition", "safety", "cost"}
    for limit in narrative.LIMITS:
        assert limit["parts"] and set(limit["parts"]) <= names, limit["key"]
        item = html.unescape(section.split(f'id="limit-{limit["key"]}"', 1)[1].split("</li>", 1)[0])
        for key in ("title", "cause", "answer"):
            assert limit[key] in item, (limit["key"], key)
        for part in limit["parts"]:
            assert f'href="/components/#core-{part}"' in item, (limit["key"], part)
        if limit["essay"]:
            path = narrative.essay_path(limit["essay"])
            assert path in public_paths() and f'href="{path}"' in item, limit["key"]


def test_the_two_parts_and_where_the_components_came_from_are_on_the_home_page_and_the_components_page(client):
    home = _text(client.get("/").content.decode().split('id="two-parts"', 1)[1].split("</section>", 1)[0])
    assert "The meta-harness learns" in home and "The components know how AI fails" in home
    assert "batteries included" in home and "250 million tokens" in home and "a periodic table of intelligence" in home
    components = _text(client.get("/components/").content.decode().split('class="wrap page-hero"', 1)[1]
                       .split("</header>", 1)[0])
    assert "one of G6's two parts" in components and "250 million tokens" in components


def test_the_home_page_says_how_g6_learns_differently_and_points_to_the_theory(client):
    page = client.get("/").content.decode()
    section = page.split('id="learning"', 1)[1].split("</section>", 1)[0]
    text = _text(section)
    for part in ("learns explicitly, through the harness", "From its own experiments", "From plain words",
                 "With a model of itself", "nothing in its design caps how far it can go"):
        assert part in text, part
    assert 'href="/research/#theory"' in section and 'id="theory"' in client.get("/research/").content.decode()


def test_no_page_calls_a_result_a_proof_of_concept(client):
    # owner, 2026-10-20: "replace the term 'proof of concept' with worked example on the website"
    for path in public_paths():
        text = _text(client.get(path).content.decode()).lower()
        assert "proof of concept" not in text and "proof-of-concept" not in text, path
