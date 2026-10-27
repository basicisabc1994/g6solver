# SPDX-License-Identifier: Apache-2.0
"""The home page and the site shell: G6's own three.js lattice drawn from the component registry, no inherited
background effects, and Gelly and her cat with a way to hide them."""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

from g6.core.registry import get_registry
from pages import catalogue, evidence
from pages.routes import public_paths


def _lattice(page: str) -> dict:
    found = re.search(r'<script id="lattice-data" type="application/json">(.*?)</script>', page, re.DOTALL)
    assert found, "the home page carries the lattice's data"
    return json.loads(found.group(1))


def test_the_catalogue_is_the_registry():
    # the core harness is every registered package except the extensions, which declare a family; an extension built in
    # a vetted area is not in the library until its certificate is current. The site pass (SITE_PASS item 2) restores
    # the exact library: an extension that joins or leaves it fails here until the pages that name it follow
    names = set(get_registry().names())
    members = {r["name"] for r in catalogue.family_members()}
    library = {r["name"] for r in catalogue.library_extensions()}
    waiting = {r["name"] for r in catalogue.awaiting_review()}
    assert waiting == {'adapt_automl', 'adapt_sklearn', 'duty_of_care', 'adapt_bayesian', 'adapt_physical_ai'}
    rows = catalogue.components()
    assert {r["name"] for r in rows} == names - members and len(rows) == 34
    assert library == members - waiting and not library & waiting and library == {
        "adapt_audio", "adapt_blender", "adapt_comfyui", "adapt_diagrams",
        "adapt_django", "adapt_ffmpeg", "adapt_generative_art", "adapt_healing", "adapt_image",
        "adapt_library_synthesizer", "adapt_pandas", "adapt_pytorch", "adapt_rq",
        "adapt_synth", "adapt_visualisation", "adapt_voice", "adapt_webpage", "agent_autogen", "agent_claude",
        "agent_codex", "agent_langgraph", "agent_nanoclaw", "agent_runtime", "agent_smolagents", "align_prompt_library",
        "australian_legal_corpus", "cog_arch_actr", "cog_arch_aixi", "cog_arch_gps", "component_creator", "ctx_ace",
        "ctx_cognee", "ctx_colbert", "ctx_contexthub", "ctx_elastic", "ctx_geo", "ctx_git", "ctx_langextract",
        "ctx_library_mapper", "ctx_search", "ctx_vision", "database", "decision_making", "design_governance",
        "hat_orchestrator", "knowledge_chunker", "knowledge_converter", "knowledge_fetcher",
        "knowledge_indexer", "knowledge_manifest", "llm_router", "meta_programming", "shared_workspace",
        "swe_diagnostics", "workspace_manager"}
    for row in rows:
        assert set(row["requires"]) <= names, row["name"]          # links only between components
        assert row["maturity"] and row["summary"]
    assert "action_gating" in next(r for r in rows if r["name"] == "metaharness")["requires"]


def test_the_home_page_lattice_is_drawn_from_the_catalogue(client):
    data = _lattice(client.get("/").content.decode())
    assert [n["name"] for n in data["nodes"]] == [r["name"] for r in catalogue.components()]
    assert data["centre"] == "metaharness"


# What a newcomer should never meet on a hover card: audit references, file or package names, solver jargon, or a
# sentence cut off at a docstring's line break ("..., breakpoints,").
_JARGON = re.compile(r"audit \d|§|\.md\b|\bg6\.|_|\bDörner\b|\bDPLL\b|\bLTL\b|\benvelope\b|[,;:—]\s*$")


def test_plain_words_cover_exactly_the_registered_core_components():
    members = {r["name"] for r in catalogue.family_members()}
    assert set(catalogue.PLAIN) == set(get_registry().names()) - members and not set(catalogue.PLAIN) & members


def test_every_lattice_point_says_in_plain_words_what_it_does_and_what_it_guards_against(client):
    # owner, 2026-10-19: the hover labels must make sense to someone new to G6
    for node in _lattice(client.get("/").content.decode())["nodes"]:
        for key in ("title", "plain", "guards"):
            assert node[key] and not _JARGON.search(node[key]), (node["name"], key, node[key])
        assert node["title"] != node["name"]


def test_the_hover_card_shows_the_plain_words(client):
    js = (Path(__file__).resolve().parents[2] / "web" / "static" / "js" / "lattice.js").read_text(encoding="utf-8")
    assert "nd.title" in js and "nd.plain" in js and "nd.guards" in js and "nd.summary" not in js
    assert "see what it does" in client.get("/").content.decode()


def test_the_results_put_the_model_alone_beside_g6_on_a_named_local_model(client):
    # owner, 2026-10-19: compare against the raw model; say the results are on a local model; the hand-written prompt
    # runs on G6's own architecture, so it is not a separate category; quote component-wise effects without cherry
    # picking (the largest gain and the largest loss alike)
    section = client.get("/").content.decode().split('id="tests"', 1)[1].split("</section>", 1)[0]
    text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", section)))
    assert evidence.MODEL in text and "Hand-written prompt" not in text and not re.search(r"\bV\d+b?\b", text)
    _, groups = evidence.headline()
    cards = section.split('class="ev__wf"')[1:]
    assert len(cards) == len(groups)
    for card, group in zip(cards, groups):
        card = html.unescape(card)
        assert f">{group.label}<" in card
        for bar in group.bars:
            assert bar.label in card and f"{bar.value} of {bar.n}" in card, (group.label, bar.arm)
    best, worst = evidence.sweep_extremes()
    assert best["shown"] in text and worst["shown"] in text


def test_the_results_are_framed_as_a_worked_example_with_how_fast_g6_engineered_them(client):
    # owner, 2026-10-19: "frame the empirical results on the main page as examples of self-engineering capability
    # rather than definitive benchmark results"; 2026-10-20: "a proof of concept exemplar application which is intended
    # to show the power of the system and results will vary depending on the individual use case/data availability",
    # then "replace the term 'proof of concept' with worked example on the website"
    section = client.get("/").content.decode().split('id="tests"', 1)[1].split("</section>", 1)[0]
    text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", section)))
    assert "worked example" in text.lower() and "not a benchmark" in text.lower()
    assert "results will vary with the task and the data you have" in text
    assert 'href="/research/#reading"' in section
    found = evidence.latest_round_speed()
    assert found["changes"]
    for change in found["changes"]:                       # every change the latest round approved, with its time
        assert f"{change['dataset']} in {change['minutes']} minutes" in text, change
        assert f"{change['alone']} to {change['tuned']} of {change['n']}" in text, change


def test_where_self_tuning_found_nothing_the_prebuilt_components_finding_is_stated(client):
    # owner, 2026-10-19: self-tuned below every-component-on on MASSIVE and MBPP "is a scientific finding ... the mode
    # in which to use this (pre-built component vs self-engineer from scratch) is actually a highlight of the system"
    section = client.get("/").content.decode().split('id="tests"', 1)[1].split("</section>", 1)[0]
    text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", section)))
    facts = evidence.headline_facts()
    assert facts["components_where_untuned"] and "A finding" in text and "prebuilt components" in text
    for name, alone, components in facts["components_where_untuned"]:
        assert f"{name} ({alone} to {components})" in text, name
    assert "round 2 is testing the fixes" not in text
    # 2026-10-20: round 3, the last, found the two modes are alternatives, not a stack
    both = evidence.both_modes()
    worst = both["worst"]
    assert "alternatives, not a stack" in text and "in the last development run" in text
    assert both["round"] == evidence.latest_round_speed()["round"]          # "the last" is true of the round it reads
    assert f"on {worst['dataset']} every component on did worse than the model alone ({worst['components']} against " \
           f"{worst['alone']})" in text


def test_the_home_page_says_the_thesis(client):
    page = client.get("/").content.decode()
    assert "AI builds.<br>Evidence<br>decides." in page
    # owner, 2026-10-23: the browser tab says what G6 does, not the thesis
    assert "<title>G6: build agents from parts you can check" in page
    for step in ("Propose", "Test", "Gate", "Approve"):
        assert f">{step}<" in page, step
    assert 'claude mcp add g6 -- g6-mcp' in page


def test_all_three_ways_to_use_g6_are_available_now(client):
    # DECISIONS #36: full source is the source itself, to use; nothing about the three ways is "planned"
    modes = client.get("/").content.decode().split('data-modes', 1)[1].split("</section>", 1)[0]
    for mode in ("As built", "Compose", "Full source"):
        assert f">{mode}<" in modes, mode
    assert "planned" not in modes.lower()


def test_no_page_loads_the_old_background_or_smooth_scroll(client):
    for path in public_paths():
        sources = re.findall(r'<script[^>]*\bsrc="([^"]+)"', client.get(path).content.decode())
        old = [s for s in sources if re.search(r"vanta|lenis|gsap|three\.js/r134|animations\.js|background\.js", s)]
        assert old == [], (path, old)


STATIC = Path(__file__).resolve().parents[2] / "web" / "static"


def test_each_limit_lights_its_own_components_on_the_lattice(client):
    # owner, 2026-10-20: "keep this punchy and engaging/fun in terms of the three js animation on scroll"
    from pages import narrative

    page = client.get("/").content.decode()
    assert 'id="lattice-labels"' in page
    stage = page.split('class="stage"', 1)[1].split("</section>", 1)[0]
    for limit in narrative.LIMITS:                       # on the stage, so the lattice is beside it
        assert f'id="limit-{limit["key"]}" data-parts="{" ".join(limit["parts"])}"' in stage, limit["key"]
    js = (STATIC / "js" / "lattice.js").read_text(encoding="utf-8")
    assert '".limit[data-parts]"' in js and "lattice-label" in js and "pointerenter" in js and "focusin" in js


def test_the_periodic_table_holds_every_core_component_once(client):
    # owner, 2026-10-20: the components are "a periodic table of intelligence"
    placed = [name for _, names in catalogue.TABLE for name in names]
    assert sorted(placed) == sorted(r["name"] for r in catalogue.components())          # each once, none unregistered
    section = client.get("/").content.decode().split('id="two-parts"', 1)[1].split("</section>", 1)[0]
    for column, elements in catalogue.table():
        assert f'<p class="ptable__head">{column}</p>' in section
        for el in elements:
            assert f'href="/components/#core-{el["name"]}"' in section and not el["short"].startswith("The ")
    assert section.count("is-centre") == 1 and 'href="/components/#core-metaharness" title=' in section


def test_the_learning_record_is_g6s_own_record_of_a_change_it_kept(client):
    import json

    change = evidence.learned_change()
    assert change and change["verdict"] == "recommend" and change["losses"] == 0
    board = sorted((evidence.VALIDATION_DIR / "rounds").glob("round-*/scoreboard.json"))[-1]
    record = json.loads((board.parent / "tuning" / "gsm8k-D.json").read_text(encoding="utf-8"))
    assert change["after"] == record["head_profile"]["prompt"] and change["minutes"] == record["minutes"]
    assert change["wins"] == record["run"]["confirmation"]["success"]["wins"]
    section = html.unescape(client.get("/").content.decode().split('id="learning"', 1)[1].split("</section>", 1)[0])
    assert change["after"] in section and f"won {change['wins']}, lost {change['losses']}, of {change['held_out']}" in section
    assert f"{change['minutes']} minutes" in section
    weights = section.split('<pre class="weights"', 1)[1].split("</figure>", 1)[0]       # made up, so labelled
    assert 'aria-hidden="true"' in section.split('<pre class="weights"', 1)[1][:30] and "Illustration." in weights


def test_the_results_count_up_once_and_hide_nothing_without_the_script(client):
    page = client.get("/").content.decode()
    section = page.split('id="tests"', 1)[1].split("</section>", 1)[0]
    assert 'class="ev" data-count' in section and 'src="/static/js/count.js"' in page
    _, groups = evidence.headline()
    for group in groups:                                 # the page holds the final number; the script only plays it in
        assert (f'<span class="ev__to" data-from="{group.by_arm["A"].value}">{group.best.value}</span>') in section
    js = (STATIC / "js" / "count.js").read_text(encoding="utf-8")
    assert js.index("prefers-reduced-motion: reduce") < js.index("classList.add")
    css = (STATIC / "css" / "g6.css").read_text(encoding="utf-8")
    hidden = re.findall(r"([^{}]+)\{[^{}]*scaleX\(0\)", css)
    assert hidden and all(".is-armed" in selector for selector in hidden)
    for banned in ("http", "localStorage", "sessionStorage", "document.cookie"):
        assert banned not in js, banned


def test_gelly_and_her_cat_are_on_every_page_with_a_way_to_hide_them(client):
    for path in ("/", "/research/", "/essays/", "/faq/"):
        page = client.get(path).content.decode()
        assert 'id="gelly"' in page and "click to tickle" in page, path
        assert 'id="gelly-cat"' in page and "click to pet" in page, path
        assert 'id="gelly-toggle"' in page and ">Hide Gelly<" in page, path
        assert "sixby" not in page.lower(), path
