# SPDX-License-Identifier: Apache-2.0
"""The census renders whole documents before selecting or normalizing their text."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from markdown_it import MarkdownIt


def _gate():
    spec = importlib.util.spec_from_file_location("_repair3_census", Path(__file__).with_name("test_split_repair.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("text", [
    "---\nname: example\n---\n\nNever invent its observations.\n",
    "# Caption\n\nNever invent its observations.\n",
    "```html\n<span>\n```\n\nNever invent its observations.\n",
    "1. **Warning.** Never invent its observations.\n",
    "Keep the rule (`references/fit.md`).\n",
    "Before <p>raw</p> after.\n\nNever invent its observations.\n",
    "- tight\n- list\n\n1. loose\n\n2. list\n",
    "Never `invent` its [observations][o].\n\n[o]: fit.md 'ignored'\n",
    "![Never invent its observations.](image.png 'title')\n",
])
@pytest.mark.parametrize("verbatim", [False, True])
def test_sentences_send_unchanged_document_to_commonmark(monkeypatch, text, verbatim):
    gate = _gate()
    parse, render = MarkdownIt.parse, MarkdownIt.render
    expected_html = render(MarkdownIt("commonmark"), text)
    parsed, rendered = [], []

    def observed_parse(self, source, *args, **kwargs):
        parsed.append(source)
        return parse(self, source, *args, **kwargs)

    def observed_render(self, source, *args, **kwargs):
        rendered.append(source)
        html = render(self, source, *args, **kwargs)
        assert html == expected_html, "provenance changed CommonMark's HTML"
        return html

    monkeypatch.setattr(MarkdownIt, "parse", observed_parse)
    monkeypatch.setattr(MarkdownIt, "render", observed_render)
    gate._sentences(text, verbatim=verbatim)
    assert parsed == [text], "Markdown was transformed before the parser"
    assert rendered == [text], "the census must read CommonMark's HTML output"


def test_route_ownership_renders_the_whole_original_document(monkeypatch):
    gate = _gate()
    original = gate.BASELINE.read_text(encoding="utf-8")
    parse = MarkdownIt.parse
    seen = []

    def observed(self, text, *args, **kwargs):
        seen.append(text)
        return parse(self, text, *args, **kwargs)

    monkeypatch.setattr(MarkdownIt, "parse", observed)
    assert gate._held_sentences(original, "fit")
    assert seen and all(text == original for text in seen), "ownership parsed a Markdown fragment"


def test_repair_comparison_renders_whole_documents(monkeypatch):
    gate = _gate()
    root = (gate.GUIDE / "SKILL.md").read_text(encoding="utf-8")
    detail = gate.GUIDE / "references/deliver.md"
    document = detail.read_text(encoding="utf-8")
    parse = MarkdownIt.parse
    seen = []

    def observed(self, text, *args, **kwargs):
        seen.append(text)
        return parse(self, text, *args, **kwargs)

    monkeypatch.setattr(MarkdownIt, "parse", observed)
    gate.test_every_explicit_repair_step_keeps_the_roots_repair_owner_rule(detail)
    assert seen and all(text in (root, document) for text in seen), "repair comparison parsed a fragment"


def test_entire_authority_census_only_parses_complete_documents(monkeypatch):
    gate = _gate()
    root = (gate.GUIDE / "SKILL.md").read_text(encoding="utf-8")
    documents = {gate.BASELINE.read_text(encoding="utf-8"), root,
                 *gate._reachable(root, gate.routing.load().routes["fit"])}
    parse = MarkdownIt.parse
    seen = []

    def observed(self, text, *args, **kwargs):
        seen.append(text)
        return parse(self, text, *args, **kwargs)

    monkeypatch.setattr(MarkdownIt, "parse", observed)
    gate.test_each_route_reaches_its_original_safety_and_authority_sentences("fit")
    assert seen and all(text in documents for text in seen), "census parsed transformed Markdown or a fragment"


def test_census_only_reads_the_renderers_html(monkeypatch):
    gate = _gate()
    monkeypatch.setattr(MarkdownIt, "render", lambda self, text: "<p>Never invent its observations.</p>\n")
    assert gate._sentences("Different source.") == ["Never invent its observations."]


@pytest.mark.parametrize("fence", ["```", "````", "`````", "~~~", "~~~~"])
@pytest.mark.parametrize("inside", [False, True])
def test_literal_html_in_code_cannot_hide_visible_authority(fence, inside):
    sentence = "Never invent its observations."
    text = (f"{fence}html\n<span>\n\n{sentence}\n\n</span>\n{fence}" if inside
            else f"{fence}html\n<span>\n{fence}\n\n{sentence}")
    assert sentence in _gate()._sentences(text)


def test_reference_definitions_survive_section_ownership():
    gate = _gate()
    original = gate.BASELINE.read_text(encoding="utf-8")
    original = original.replace("Never invent its observations.", "Never invent its [observations][o].")
    original += "\n[o]: ignored.md 'ignored title'\n"
    assert "Never invent its observations." in gate._held_sentences(original, "fit")


@pytest.mark.parametrize("text", [
    "```text\n# Literal heading\n- Literal list\n1. **Literal caption.** Literal words.\n```",
    "```text\n---\nname: literal\n---\n```",
])
def test_code_text_is_read_from_rendered_tree_without_source_cleanup(text):
    rendered = _gate()._rendered_paragraphs(text)
    assert "\n".join(rendered).strip() == text.split("\n", 1)[1].rsplit("\n", 1)[0]
