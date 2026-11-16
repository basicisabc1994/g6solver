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
             "/get-started/", "/faq/", "/metaharness/", "/bundles/", "/failure-modes/"]
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
    # owner, 2026-10-30: it names the formal path, as what ships does it: a check against a model, with any
    # counterexample replayed on the code, never a proof that the code is correct
    assert "against a formal mathematical model" in sub and "replaying counterexamples on your code" in sub
    assert "prove" not in sub and "verif" not in sub and len(sub.split()) <= 65
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
    # plan revision 8, E: the "250 million tokens" figure has no record behind it, so it stays out of the copy
    assert "batteries included" in home and "a periodic table of intelligence" in home
    assert "reviewing how agents fail" in home and "250 million" not in home
    components = _text(client.get("/components/").content.decode().split('class="wrap page-hero"', 1)[1]
                       .split("</header>", 1)[0])
    assert "one of G6's two parts" in components and "failure traces were reviewed" in components
    assert "250 million" not in components


def test_the_home_page_says_how_g6_learns_differently_and_points_to_the_theory(client):
    page = client.get("/").content.decode()
    section = page.split('id="learning"', 1)[1].split("</section>", 1)[0]
    text = _text(section)
    for part in ("learns explicitly, through the harness", "From its own experiments", "From plain words",
                 "With a model of itself", "how far it gets has not been measured in general"):
        assert part in text, part
    assert "nothing in its design caps" not in text          # plan revision 8, E: no unmeasured reach claim
    assert 'href="/research/#theory"' in section and 'id="theory"' in client.get("/research/").content.decode()


def test_no_page_calls_a_result_a_proof_of_concept(client):
    # owner, 2026-10-20: "replace the term 'proof of concept' with worked example on the website"
    for path in public_paths():
        text = _text(client.get(path).content.decode()).lower()
        assert "proof of concept" not in text and "proof-of-concept" not in text, path


def test_the_home_and_research_pages_claim_no_more_than_the_code_does(client):
    # plan revision 8, E (review 2 of the entry branch): the home page's answers say what the FAQ says
    answers = {limit["key"]: limit["answer"] for limit in narrative.LIMITS}
    assert "checks each claim against your sources" not in answers["hallucination"]
    assert "word by word" in answers["hallucination"] and "not a reading of meaning" in answers["hallucination"]
    assert "forgets nothing" not in answers["learning"] and "rolled back" in answers["learning"]
    assert "gates every action" not in answers["safety"] and "actions that go through it" in answers["safety"]
    assert "answers each limit" not in narrative.__doc__
    home = _text(client.get("/").content.decode())
    assert "Every action gated" not in home and "Actions that go through G6 are gated" in home
    research = _text(client.get("/research/").content.decode())
    assert "when G6 engineers the harness" not in research and "Larger models engineer the harness" not in research


def test_the_home_page_scopes_approval_labels_and_judging_to_what_the_code_does(client):
    # review 3 of the entry branch: a new meta-harness version goes live only on a person's yes (its drift monitor may
    # move back to an approved one by itself); a decision is blocked when the judging code differs from what was
    # confirmed (it is not out of reach); the three labels come with results through G6's doors, not every result
    home = _text(client.get("/").content.decode())
    for overclaim in ("Nothing changes until you say yes", "Every change is recorded, and can be rolled back",
                      "can never touch the checks that judge it", "Every result carries one of three labels",
                      "Every change still needs your approval"):
        assert overclaim not in home, overclaim
    assert "A new version goes live only when you say yes" in home
    assert "differs from the code you confirmed" in home
    # Review 4 F5: only returned results carry labels; a runtime exception is reported as a failure (MCP masks it).
    assert "A result a component returns through G6's MCP server or CLI carries one of three labels" in home
    assert "An operation that fails while running is reported as a failure" in home
    assert "the meta-harness puts no new version live without your approval" in home
