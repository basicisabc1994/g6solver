# SPDX-License-Identifier: Apache-2.0
"""/components/: G6's core harness, read from the registry, and every extension, read from the extension catalogue
(made once from the extension tracker) or, once it is in the library, from its own package (FW-04, D8)."""
from __future__ import annotations

import csv
import html
import importlib
import json
import re
import sys
from pathlib import Path

import pytest

import g6
from g6.core import registry, vetting
from g6.core.failure_modes import FailureModeRegistry
from g6.core.registry import get_registry
from pages import catalogue, extensions

TRACKER = Path(__file__).resolve().parents[3] / "docs" / "transition" / "extension_tracker"
VERDICT = "verdict_after_owner_2026_10_20"


def _page(client) -> str:
    return client.get("/components/").content.decode()


def _section(page: str, section_id: str) -> str:
    return page.split(f'id="{section_id}"', 1)[1].split("</section>", 1)[0]


def _item(page: str, name: str) -> str:
    """A listed row's own entry: from its anchor to the end of its status line, so a row nested under it is not read."""
    return "</p>".join(page.split(f'id="ext-{name}"', 1)[1].split("</p>", 2)[:2])


def _data() -> dict:
    return json.loads(extensions.CATALOGUE.read_text(encoding="utf-8"))


def _not_listed() -> set[str]:
    data = _data()
    return set(data["dropped"]) | {r["name"] for r in data["components"] if r["hidden"]}


def test_every_core_component_is_listed(client):
    core = _section(_page(client), "core")
    for row in catalogue.components():
        assert f">g6.{row['name']}<" in core, row["name"]


def test_each_core_component_says_in_plain_words_what_it_does_and_what_it_guards_against(client):
    core = _section(_page(client), "core")
    for row in catalogue.components():
        item = core.split(f'id="core-{row["name"]}"', 1)[1].split("</li>", 1)[0]
        for key in ("title", "plain", "guards"):
            assert html.escape(row[key]) in item, (row["name"], key)


def test_the_formal_methods_engines_are_shown_together(client):
    # the formal family together (owner, 2026-10-20); prolog joins the group once its package is registered
    core = _section(_page(client), "core")
    registered = {r["name"] for r in catalogue.components()}
    assert set(catalogue.FORMAL) - {"prolog"} <= registered
    formal, rest = core.split('id="formal-methods"', 1)[1].split('id="checks-and-gates"', 1)
    assert ">Formal methods<" in formal
    assert re.findall(r'id="core-(\w+)"', formal) == [n for n in catalogue.FORMAL if n in registered]
    assert re.findall(r'id="core-(\w+)"', rest) == sorted(registered - set(catalogue.FORMAL))


def test_every_listed_row_is_shown_with_the_failure_it_guards_or_would_guard_against(client, with_planned):
    # a planned row guards nothing yet (the failure map says so), so it never says "Guards against" in the present. With
    # SHOW_PLANNED on, every row of the catalogue is listed, as before 2026-10-23
    page = _section(_page(client), "extensions")
    rows = extensions.rows()
    assert len(rows) == len([r for r in _data()["components"] if not r["hidden"]]) > 100
    for row in rows:
        item = _item(page, row["name"])
        if row["merged"]:                                          # part of a package in the library: no planned words
            assert f"Merged into <code>{row['package']}</code>" in item, row["name"]
            assert html.escape(row["summary"]) not in item and "lanned" not in item, row["name"]
            continue
        assert html.escape(row["summary"]) in item and html.escape(row["failure_mode"]) in item, row["name"]
        said = ("Guards against" if row["in_library"] else "Built to guard against" if row["awaiting_review"]
                else "Planned to guard against")
        assert f"{said}: {html.escape(row['failure_mode'])}." in item, row["name"]
        assert row["in_library"] or "Guards against" not in item.replace("guard against", ""), row["name"]
    count = extensions.counts()
    assert f"<dt>Extensions</dt><dd>{count['extensions']}</dd>" in _page(client)
    assert count["extensions"] + count["joining"] == len(rows) and count["extensions"] > 80


def test_no_dropped_or_held_row_is_listed(client):
    page, hidden = _page(client), _not_listed()
    assert {"mastery_advisor", "realtime_bridge", "extension_classifier", "self_debug"} <= hidden
    for name in sorted(hidden):
        assert f'id="ext-{name}"' not in page and f'<code class="cmp__name">{name}</code>' not in page, name
        with pytest.raises(LookupError):
            extensions.named(name)
    assert not {r["name"] for r in extensions.rows()} & hidden
    assert not {n for c in extensions.CAPABILITIES for n in c["extensions"]} & hidden


@pytest.mark.skipif(not TRACKER.is_dir(), reason="the extension tracker is in the source repository only")
def test_the_catalogue_is_the_tracker():
    tracker = {}
    for path in sorted(TRACKER.glob("*.csv")):
        with path.open(encoding="utf-8", newline="") as fh:
            tracker.update({row["component"]: row for row in csv.DictReader(fh)})
    data = _data()
    assert data["dropped"] == sorted(n for n, r in tracker.items() if r[VERDICT] == "DROP") and len(data["dropped"]) == 20
    held = {n for n, r in tracker.items() if r[VERDICT] == "HOLD"}
    assert held == {"mastery_advisor"} and held <= {r["name"] for r in data["components"] if r["hidden"]}
    listed = {r["name"]: r for r in data["components"] if not r["hidden"]}
    assert set(listed) == set(tracker) - set(data["dropped"]) - held
    for name, row in listed.items():
        assert (row["family"], row["package"]) == (tracker[name]["family"], tracker[name]["package"]), name
        kinds = sorted(k.strip() for k in tracker[name]["vetting_required"].split(";") if k.strip())
        assert row["vetting"] == kinds, name                         # the areas whose review it waits for
    assert sum(1 for r in listed.values() if r["vetting"]) >= 40
    # nothing in the data says whether a row is in the library: the page asks the registry
    assert all(set(r) == {"name", "family", "package", "summary", "failure_mode", "needs", "vetting", "hidden"}
               for r in data["components"])


def test_families_are_the_fifteen_and_a_merge_is_listed_under_what_it_joins(client, with_planned):
    assert set(extensions.FAMILIES) == registry.FAMILIES
    page = _section(_page(client), "extensions")
    own = {r["name"]: r for r in extensions.extensions()}
    by_package = {r["package"]: r["name"] for r in own.values()}
    after = dict(zip(own, [*list(own)[1:], None]))               # the next extension of its own on the page
    for row in extensions.rows():
        assert row["family"] in extensions.FAMILIES or row["family"] == extensions.CORE, row["name"]
        if row["name"] in own:
            assert row["package"].rsplit(".", 1)[-1] == row["name"], row["name"]
            continue
        if row["family"] == extensions.CORE:                       # with the core package it joins
            holder = page.split('id="into-core"', 1)[1]
            holder = holder.split(f'id="into-{row["package"].split(".")[1]}"', 1)[1].split('id="into-', 1)[0]
        elif row["package"] in by_package:                         # under the extension it joins
            target = by_package[row["package"]]
            holder = page.split(f'id="ext-{target}"', 1)[1]
            holder = holder.split('<div class="cmp-joined">', 1)[1]
            holder = holder.split(f'id="ext-{after[target]}"', 1)[0] if after[target] else holder
        else:                                                      # in its family's shared code
            assert row["package"] == f"g6.{row['family']}", row["name"]
            holder = page.split(f'cmp-family__name">{extensions.FAMILIES[row["family"]]}<', 1)[1]
            holder = holder.split("Planned into the family's shared code", 1)[1].split("</details>", 1)[0]
        assert f'id="ext-{row["name"]}"' in holder, row["name"]
        said = "Merged into" if row["merged"] else "Planned to join"
        assert f"{said} <code>{row['package']}</code>" in _item(page, row["name"]), row["name"]
    names = {r["name"] for r in extensions.rows()}
    for joined, target in (("adapt_keras", "adapt_pytorch"), ("job_ai", "job_framework"), ("agent_router", "llm_router")):
        holding = [r["name"] for r in own[target]["joined"] + own[target]["merged_in"]]
        assert joined in names and joined not in own and joined in holding


def test_the_capability_harness_says_what_each_kind_of_extension_adds_and_achieves(client, with_planned):
    # owner, 2026-10-20: not "images from your coding agent" but a capability extension harness: what extending a
    # language model to other modalities, persistent tasks and real-world execution adds, and what it lets you achieve.
    # With SHOW_PLANNED on, every row the cards name is listed, as before 2026-10-23
    import importlib.util

    page = _page(client)
    assert "Images from the coding agent" not in page
    section = html.unescape(_section(page, "capabilities"))
    assert "A capability extension harness" in section and 'href="#ext-llm_router"' in section
    keys = {c["key"] for c in extensions.CAPABILITIES}
    assert {"modalities", "memory", "action"} <= keys and len(keys) == len(extensions.CAPABILITIES)
    for cap in extensions.CAPABILITIES:
        card = section.split(f'id="cap-{cap["key"]}"', 1)[1].split("</article>", 1)[0]
        for part in (cap["title"], cap["gap"], cap["achieve"]):
            assert part in card, (cap["key"], part)
        for name in cap["library"]:                                  # in the open-source library now
            ref = get_registry().refs.get(name)                      # a component, or a package such as g6.operator
            package = ref.module if ref else f"g6.{name}"
            assert importlib.util.find_spec(package) and package in card, name
        for row in extensions.named(*cap["extensions"]):             # listed; raises on a name the page does not list
            assert row["name"] in card and f'href="#ext-{row["name"]}"' in card, row["name"]
            said = "merged into" if row["merged"] else "joins"
            joins = f"<code>{row['name']}</code></a> ({said} <code>{row['package']}</code>)"
            assert (joins in card) is (not extensions._own(row)), row["name"]   # a merge is never shown as its own
    # what is in the library now, said from the registry, never assumed; only extensions of their own are counted
    shown = {n for c in extensions.CAPABILITIES for n in c["extensions"]}
    own = [row for row in extensions.named(*sorted(shown)) if extensions._own(row)]
    assert 0 < len(own) < len(shown)
    ready = sum(1 for row in own if row["in_library"])
    assert (f"{ready} of the {len(own)}" if ready else f"None of the {len(own)}") in section
    assert "starting with the formal methods engines" not in section  # they are in the core harness already
    for anchor in re.findall(r'href="#(core-[\w]+|ext-[\w]+)"', section):        # every in-page link has its target
        assert f'id="{anchor}"' in page, anchor


def test_ctx_git_01_the_action_card_says_which_git_acts_the_action_gate_checks(client):
    # Fails if extensions.CAPABILITIES' "action" card again says every act is gated: ctx_git asks the gate only for
    # fetch, pull, push and clone (ops.py, _go); its local changes are sensitive ops the person allows.
    action = next(c for c in extensions.CAPABILITIES if c["key"] == "action")["achieve"]
    assert "with each action checked by G6's action gate first" not in action
    assert "Outward git actions (fetch, pull, push, clone) are checked by G6's action gate first" in action
    assert "local ones (commit, merge, branch, tag, stash, add, reset) are sensitive ops you allow" in action
    assert action in html.unescape(_section(_page(client), "capabilities"))


def test_csf_strategy_claims_only_what_g6_csf_does():
    # the release integrator's follow-up (2026-10-23): g6.csf no longer declares improvised_risky_behaviour, since
    # nothing in it watches what an agent does (tests/csf/test_declared_failure_modes.py); its approved cap
    # (policy.tighten) only lowers a bound, and a caller's word is no approval. Fails while the catalogue row merged into
    # it says it guards against "agents improvising behaviour in known risky situations"
    row = next(r for r in _data()["components"] if r["name"] == "csf_strategy")
    words = (row["summary"] + " " + row["failure_mode"]).lower()
    assert row["package"] == "g6.csf" and "improvis" not in words
    assert "approval" in row["failure_mode"] and "caller" in row["failure_mode"]


def test_distillation_02_the_hover_says_a_bounded_child_process_not_a_sandbox():
    # Fails if catalogue.PLAIN["distillation"] again says it "runs it in a sandbox": it is a bounded child process.
    title, plain, guards = catalogue.PLAIN["distillation"]
    assert "a bounded child process" in plain and "sandbox" not in plain


def test_an_extension_in_the_library_is_listed_under_its_family_not_in_the_core(client):
    # owner, 2026-10-20: the model router goes "in the extensions for integrations", not in the core harness
    page = _page(client)
    core, listed = _section(page, "core"), _section(page, "extensions")
    library = catalogue.library_extensions()
    # the site pass (SITE_PASS item 2) restores the exact library, by name: one joining or leaving fails here
    assert [ext["name"] for ext in library] == [
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
        "swe_diagnostics", "workspace_manager"]
    rows = {row["name"]: row for row in extensions.rows()}
    for ext in library:
        assert f'id="core-{ext["name"]}"' not in core and f">g6.{ext['name']}<" not in core
        row = rows[ext["name"]]
        assert (row["family"], row["package"], row["in_library"]) == (ext["family"], ext["package"], True)
        label = extensions.FAMILIES[ext["family"]]
        family = listed.split(f'cmp-family__name">{label}<', 1)[1].split("</details>", 1)[0]
        item = _item(family, ext["name"])
        assert "In the library" in item and f"<code>{ext['package']}</code>" in item
        assert ("callable over MCP" in item) is ext["mcp"]
    # no other row is in the library, and the router's lines carry no hardware fit
    assert {name for name, row in rows.items() if row["in_library"]} == {ext["name"] for ext in library}
    router = rows["llm_router"]
    assert "hardware" not in (router["summary"] + router["failure_mode"]).lower()


def test_an_entry_in_the_library_reads_its_lines_from_its_package(client):
    # D8: the summary, "Guards against" and "Needs" come from the package's own declarations, never the catalogue
    import g6.llm_router as router

    first = router.__doc__.strip().splitlines()[0].split("—", 1)[1].strip()
    summary = first[:1].upper() + first[1:]
    guards = "; ".join(FailureModeRegistry.title(c) for c in router.__g6_mitigates__)
    needs = ", ".join([p.name for p in router.__g6_outside__] +
                      [f"the optional {e} extra" for e in getattr(router, "__g6_extras__", {})])
    item = _item(_section(_page(client), "extensions"), "llm_router")
    assert f'<p class="cmp__what">{html.escape(summary)}</p>' in item
    assert f"Guards against: {html.escape(guards)}. Needs {html.escape(needs)}. In the library" in item
    planned = next(r for r in _data()["components"] if r["name"] == "llm_router")
    assert html.escape(planned["summary"]) not in item and html.escape(planned["failure_mode"]) not in item


PROBE = '''"""g6.pagetest.page_probe — count the probes on a page, for the components page tests.

Nothing past the first line is the summary.
"""
from g6.core.outside import OutsideProgram

__g6_mitigates__ = ("unchosen_model",)
__g6_introduces__ = ()
__g6_outside__ = (OutsideProgram(name="Probe tool", purpose="probes pages", install="install it yourself",
                                 licence="MIT", command="g6-no-such-probe"),)
__g6_extras__ = {"probe": ("json",)}
'''


@pytest.fixture
def probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registry_reset) -> Path:
    """A temporary family ``g6.pagetest`` with no members yet, and a planned catalogue row for its member
    ``page_probe``, in the catalogue's own words."""
    fam = tmp_path / "g6" / "pagetest"
    fam.mkdir(parents=True)
    (fam / "__init__.py").write_text('"""A family for tests."""\n__g6_family__ = "pagetest"\n', encoding="utf-8")
    monkeypatch.setattr(g6, "__path__", [*g6.__path__, str(tmp_path / "g6")])
    monkeypatch.setattr(registry, "FAMILIES", registry.FAMILIES | {"pagetest"})
    monkeypatch.setitem(extensions.FAMILIES, "pagetest", "Page test")
    data = extensions._data()
    row = {"name": "page_probe", "family": "pagetest", "package": "g6.pagetest.page_probe",
           "summary": "The catalogue's planned words.", "failure_mode": "the catalogue's planned failure",
           "needs": ["the catalogue's planned need"], "vetting": [], "hidden": None}
    vetted = dict(row, name="page_vetted", package="g6.pagetest.page_vetted", vetting=["clinical"])
    monkeypatch.setattr(extensions, "_data", lambda: dict(data, components=[*data["components"], row, vetted]))
    importlib.invalidate_caches()
    yield fam
    for name in [m for m in sys.modules if m == "g6.pagetest" or m.startswith("g6.pagetest.")]:
        del sys.modules[name]


def test_in_the_library_follows_the_registry(client, probe: Path, with_planned):
    before = _item(_section(_page(client), "extensions"), "page_probe")
    assert "Planned as <code>g6.pagetest.page_probe</code>" in before and "In the library" not in before
    assert html.escape("The catalogue's planned words.") in before

    (probe / "page_probe").mkdir()
    (probe / "page_probe" / "__init__.py").write_text(PROBE, encoding="utf-8")
    importlib.invalidate_caches()
    registry._REGISTRY = None                                        # the registry looks again
    assert get_registry().refs["page_probe"].module == "g6.pagetest.page_probe"
    after = _item(_section(_page(client), "extensions"), "page_probe")
    assert "In the library: <code>g6.pagetest.page_probe</code>" in after and "callable over MCP" not in after
    assert '<p class="cmp__what">Count the probes on a page, for the components page tests.</p>' in after
    assert f"Guards against: {html.escape(FailureModeRegistry.title('unchosen_model'))}." in after
    assert "Needs Probe tool, the optional probe extra." in after
    assert "planned" not in after.replace("Planned", "")             # none of the catalogue's words are left


VETTED = PROBE.replace("page_probe", "page_vetted") + '''
__g6_vetting__ = {"kinds": ("clinical",), "register": "g6/pagetest/page_vetted/register.json",
                  "independent_pass": "g6/pagetest/page_vetted/review.json", "signed_review": SIGNED}
'''


def _install_vetted(probe: Path, signed: str | None, declare: bool = True) -> None:
    pkg = probe / "page_vetted"
    pkg.mkdir(exist_ok=True)
    for rel in ("register.json", "review.json", "signed.md"):
        (pkg / rel).write_text("{}", encoding="utf-8")
    text = VETTED.replace("SIGNED", repr(signed)) if declare else VETTED.split("\n__g6_vetting__", 1)[0] + "\n"
    (pkg / "__init__.py").write_text(text, encoding="utf-8")
    for name in [m for m in sys.modules if m.startswith("g6.pagetest.page_vetted")]:
        del sys.modules[name]
    importlib.invalidate_caches()
    registry._REGISTRY = None
    catalogue._extension_rows.cache_clear()


@pytest.mark.parametrize("show_planned", [False, True], ids=["live only", "with planned"])
@pytest.mark.parametrize("declare", [True, False], ids=["declared", "no declaration"])
def test_a_vetted_extension_is_built_but_not_in_the_library_until_its_certificate_is_current(client, probe, declare,
                                                                                               monkeypatch,
                                                                                               show_planned):
    # handoff §4: a clinical, mental-health, physical-safety, finance or political profile is built and tested, but not
    # listed as in the library until its G6 certificate is current; a missing declaration fails closed. Owner,
    # 2026-10-23 (DECISIONS #39): no qualified person's signed review is awaited, and the page says plainly that none
    # was given (fails while extensions._awaiting says "review by a qualified person" for these kinds). A built row is
    # not a planned one: it is shown whatever the switch says.
    monkeypatch.setattr(extensions, "SHOW_PLANNED", show_planned)
    _install_vetted(probe, signed=None, declare=declare)
    assert get_registry().refs["page_vetted"].module == "g6.pagetest.page_vetted"
    page = _page(client)
    item = _item(_section(page, "extensions"), "page_vetted")
    assert "Built as <code>g6.pagetest.page_vetted</code>; awaiting its certificate." in item
    assert "No qualified person has reviewed this component." in item and "qualified person signs" not in page
    assert "In the library" not in item
    assert f"Built to guard against: {html.escape(FailureModeRegistry.title('unchosen_model'))}." in item
    assert "page_vetted" not in {r["name"] for r in catalogue.library_extensions()}
    assert extensions.counts()["in_library"] == sum(1 for r in extensions.extensions() if r["in_library"])
    assert not next(r for r in extensions.rows() if r["name"] == "page_vetted")["in_library"]

    if declare:                                                      # a signed record, with no certificate
        _install_vetted(probe, signed="g6/pagetest/page_vetted/signed.md")
        item = _item(_section(_page(client), "extensions"), "page_vetted")
        assert "awaiting its certificate" in item                   # a signature alone never lists it
        # A current certificate is tested in tests/conformance/test_component_certificate.py; here the page only has
        # to show what g6.core.vetting decides once it is there: in the library, with no signature, saying so.
        decide = vetting.status
        monkeypatch.setattr(vetting, "status", lambda package, *args, **kwargs: vetting.REVIEWED
                            if package == "g6.pagetest.page_vetted" else decide(package, *args, **kwargs))
        catalogue._extension_rows.cache_clear()
        item = html.unescape(_item(_section(_page(client), "extensions"), "page_vetted"))
        assert "In the library: <code>g6.pagetest.page_vetted</code>" in item and "awaiting" not in item
        assert vetting.NO_QUALIFIED_REVIEW in " ".join(item.split())
        assert "page_vetted" in {r["name"] for r in catalogue.library_extensions()}


def test_an_extension_in_the_library_says_what_its_package_declares() -> None:
    # D8: the page's "Guards against" and "Needs" for an extension in the library agree with its own declarations
    from g6.core.failure_modes import FailureModeRegistry

    rows = {row["name"]: row for row in extensions.extensions()}
    for ext in catalogue.library_extensions():
        mod, row = get_registry().get_module(ext["name"]), rows[ext["name"]]
        titles = [FailureModeRegistry.title(code) for code in mod.__g6_mitigates__]
        assert titles and all(title in row["failure_mode"] for title in titles), ext["name"]
        needs = " ".join(row["needs"])
        assert all(program.name in needs for program in getattr(mod, "__g6_outside__", ())), ext["name"]


def test_the_page_labels_nothing_experimental(client):
    page = _page(client).lower()
    assert "experimental" not in page and "maturity" not in page


def test_the_three_ways_to_use_g6_are_all_available_now(client):
    ways = _section(_page(client), "ways")
    for way in ("As built", "Compose", "Full source"):
        assert f">{way}<" in ways, way
    assert "planned" not in ways.lower()


def test_mission_venture_and_premortem_modes_are_shown(client):
    modes = _section(_page(client), "modes")
    for mode in ("Mission", "Venture", "Premortem"):
        assert f">{mode}<" in modes, mode
