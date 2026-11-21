# SPDX-License-Identifier: Apache-2.0
"""Regressions for route-local authority and selection after progressive disclosure."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from bisect import bisect_right
from html.parser import HTMLParser
from pathlib import Path

import pytest
from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML

from g6.skills import corpus, routing

GUIDE = Path(__file__).resolve().parents[2] / "src/g6/skills/using-g6"
BASELINE = Path(__file__).with_name("fixtures") / "using_g6_wiring_int.md"
EXCEPTIONS = BASELINE.with_name("using_g6_sentence_exceptions.tsv")
LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
# This closed table has two lead-reviewed navigation-only rows. Any row change
# needs the lead's review and an update to this pin in the same change.
EXCEPTION_ROWS_SHA256 = "570281c5f6754e065736f17e5acc6b4601daa6fcaa31767cd883d635031a415c"
TASKS = [
    ("write numbered requirements, acceptance tests and template instances for a hidden constraint", "verify",
     {"route:review"}),
    ("the reply carries repair and by is person", "stuck", {"route:select", "route:review"}),
    ("evidence kinds must match between data-only stages field mapping", "plan", {"route:compose"}),
    ("held-out cases exploration safety injection evaluator approval prompt", "plan", {"route:fit"}),
]


def _normal(text: str) -> str:
    return " ".join(text.split())


def _instructions(text: str) -> str:
    # Relative navigation changes location, not the authority of the sentence.
    # The separate pointer census resolves every such file from its own folder.
    text = re.sub(r"\s*\((?:references/)?[^()\s]+\.md\)", "", text)
    return re.sub(r"\breferences/([^\s()]+\.md)\b", r"\1", text)


def _rendered_paragraphs(text: str) -> list[str]:
    """Read only HTML rendered from the whole, unaltered CommonMark document.

    Renderer offsets carry provenance, never replacement text: they distinguish
    passed-through HTML from generated elements and select source ranges only
    after rendering. All visible words come from the resulting HTML tree.
    """
    class SourceRenderer(RendererHTML):
        def __init__(self, parser):
            super().__init__(parser)
            self.spans = []
            self.raw = []
            self.captions = []

        def render(self, tokens, options, env):
            chunks, offset, ancestors = [], 0, []
            for index, token in enumerate(tokens):
                if token.type == "inline":
                    children, parts = token.children or [], []
                    # The guides declare numbered-step captions in one form:
                    # a leading strong plain-text label ending '.', followed by
                    # prose in the same first direct paragraph of an ordered
                    # item. Standalone bold text and later/nested blocks count.
                    visible = [(i, child) for i, child in enumerate(children)
                               if child.type != "text" or child.content]
                    caption_end = None
                    if (ancestors[-3:] == ["ordered_list_open", "list_item_open", "paragraph_open"]
                            and index >= 2 and tokens[index - 2].type == "list_item_open"
                            and len(visible) >= 4
                            and [child.type for _, child in visible[:4]] == [
                                "strong_open", "text", "strong_close", "text"]
                            and visible[1][1].content.endswith(".")
                            and visible[3][1].content[:1].isspace()
                            and visible[3][1].content.strip()):
                        caption_end = visible[2][0]
                    position = offset
                    for child_index, child in enumerate(children):
                        rule = self.rules.get(child.type, self.renderToken)
                        part = rule(children, child_index, options, env)
                        if child.type == "html_inline":
                            self.raw.append((position, position + len(part)))
                        if caption_end is not None and child_index <= caption_end:
                            self.captions.append((position, position + len(part)))
                        parts.append(part)
                        position += len(part)
                    chunk = "".join(parts)
                else:
                    rule = self.rules.get(token.type, self.renderToken)
                    chunk = rule(tokens, index, options, env)
                    if token.type == "html_block":
                        self.raw.append((offset, offset + len(chunk)))
                if chunk:
                    self.spans.append((offset, offset + len(chunk), token.map,
                                       token.markup if token.type == "heading_open" else ""))
                chunks.append(chunk)
                offset += len(chunk)
                if token.nesting == 1:
                    ancestors.append(token.type)
                elif token.nesting == -1:
                    ancestors.pop()
            return "".join(chunks)

    parser = MarkdownIt("commonmark", renderer_cls=SourceRenderer)
    html = parser.render(text)
    spans, raw = parser.renderer.spans, parser.renderer.raw
    starts = [span[0] for span in spans]
    raw_starts = [span[0] for span in raw]
    captions = parser.renderer.captions
    caption_starts = [span[0] for span in captions]
    line_starts = [0, *(match.end() for match in re.finditer("\n", html))]
    # Preserve the baseline's front-matter/ATX-label exclusions after rendering.
    front_matter_end = text.find("---", 3) if text.startswith("---") else -1
    front_matter_lines = text.count("\n", 0, front_matter_end) + 1 if front_matter_end >= 0 else 0

    class Paragraph(str):
        """Rendered text with its original block's source range attached."""

    class VisibleHTML(HTMLParser):
        void = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
                "param", "source", "track", "wbr"}
        blocks = {"p", "li", "pre", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"}

        def __init__(self):
            super().__init__()
            self.elements = []
            self.parts, self.paragraphs = [], []
            self.caption = []
            self.heading_label = False
            self.source = None

        def position(self):
            line, column = self.getpos()
            return line_starts[line - 1] + column

        def is_raw(self):
            position = self.position()
            index = bisect_right(raw_starts, position) - 1
            return index >= 0 and position < raw[index][1]

        def flush(self):
            paragraph = "".join(self.parts)
            caption = "".join(self.caption)
            if paragraph.strip() or caption.strip():
                rendered = Paragraph(paragraph)
                rendered.caption = caption
                rendered.source_lines = self.source
                rendered.label = self.heading_label or bool(
                    self.source and self.source[1] <= front_matter_lines)
                self.paragraphs.append(rendered)
            self.parts, self.caption = [], []
            self.source = None

        def handle_starttag(self, tag, attrs):
            if self.is_raw():
                if tag not in self.void:
                    self.elements.append(tag)
                return
            if tag in self.blocks:
                self.flush()
            if tag.startswith("h") and tag[1:] in "123456":
                index = bisect_right(starts, self.position()) - 1
                self.heading_label = index >= 0 and spans[index][3].startswith("#")
            if tag == "br" and not self.elements:
                self.parts.append(" ")

        def handle_startendtag(self, tag, attrs):
            # HTML ignores a self-closing slash on non-void elements.
            self.handle_starttag(tag, attrs)

        def handle_endtag(self, tag):
            if self.is_raw():
                if tag in self.elements:
                    last = len(self.elements) - 1 - self.elements[::-1].index(tag)
                    del self.elements[last:]
                return
            if tag in self.blocks:
                self.flush()
            if tag.startswith("h") and tag[1:] in "123456":
                self.heading_label = False

        def handle_data(self, data):
            if self.elements or self.is_raw():
                return
            caption = bisect_right(caption_starts, self.position()) - 1
            in_caption = caption >= 0 and self.position() < captions[caption][1]
            index = bisect_right(starts, self.position()) - 1
            source = spans[index][2] if index >= 0 else None
            if source:
                self.source = source
            (self.caption if in_caption else self.parts).append(data)

    tree = VisibleHTML()
    tree.feed(html)
    tree.close()
    tree.flush()
    return tree.paragraphs


def _paragraph_sentences(paragraphs, *, verbatim=False):
    """Normalize rendered text only; never reinterpret it as Markdown."""
    def split(text):
        if not verbatim:
            text = _instructions(text)
        return [s for part in re.split(r"\n\s*\n", text)
                for s in re.split(r"(?<=[.!?])\s+", _normal(part)) if s]

    sentences = []
    for paragraph in paragraphs:
        if getattr(paragraph, "label", False):
            continue
        # Lead ruling (2026-11-21): a numbered-step caption's words are part of
        # the rule. The caption is kept, and every sentence it captions carries
        # it, so a changed or qualifying caption changes the census.
        caption = " ".join(split(getattr(paragraph, "caption", "")))
        sentences.extend(_Sentence(s, bare=s, caption=True) for s in split(caption))
        sentences.extend(_Sentence(f"{s} {caption}" if caption else s, bare=s) for s in split(paragraph))
    return sentences


class _Sentence(str):
    """A census sentence; `bare` is its own words without the caption that binds it."""

    def __new__(cls, text, *, bare, caption=False):
        sentence = super().__new__(cls, text)
        sentence.bare, sentence.caption = bare, caption
        return sentence


def _sentences(text: str, *, verbatim: bool = False) -> list[str]:
    return _paragraph_sentences(_rendered_paragraphs(text), verbatim=verbatim)


def _paragraphs_in_lines(paragraphs, lines):
    return [paragraph for paragraph in paragraphs if paragraph.source_lines
            and paragraph.source_lines[0] < lines[1] and paragraph.source_lines[1] > lines[0]]


def _source_lines(document, fragment):
    start = document.index(fragment)
    first = document.count("\n", 0, start)
    return first, first + len(fragment.splitlines())


def _held_sentences(original, route_id):
    """All sentences, without a keyword filter; shared text binds every route."""
    rendered = _rendered_paragraphs(original)
    route = routing.load().routes[route_id]
    headings = {"review": "## Deliver a coding task with G6", "discover": "## Find",
                "compose": "## Compose", "fit": "## Fit"}
    section = routing.section(original, headings.get(route_id, route.anchor))
    assert section, route_id
    tools = {key.split("/", 1)[1] for key, entry in routing.load().entries.items()
             if entry.route == route_id and key.startswith("mcp:g6/")}
    paragraphs = []
    for paragraph in re.split(r"\n\s*\n|\n(?=- \*\*|\d+\. )", original):
        first_tool = re.search(r"\b(?:g6_|metaharness_)[a-z_]+", paragraph)
        if first_tool and first_tool.group() in tools:
            paragraphs.append(paragraph)
    shared = [original.split("- **select.**", 1)[0], routing.section(original, "## Read and ask")]
    assert all(shared)
    return {s for text in [*shared, section, *paragraphs]
            for s in _paragraph_sentences(_paragraphs_in_lines(rendered, _source_lines(original, text)))}


def _exceptions(original):
    rows = list(csv.DictReader(EXCEPTIONS.read_text(encoding="utf-8").splitlines(), delimiter="\t"))
    exceptions = {}
    for row in rows:
        assert set(row) == {"sentence", "reason", "replacement"}, row
        sentence, reason = row["sentence"], row["reason"]
        replacement = "" if row["replacement"] == "-" else row["replacement"]
        # Rows name a sentence by its own words, captioned or not.
        assert sentence in {s.bare for s in _sentences(original)}, f"stale sentence exception: {sentence}"
        assert sentence not in exceptions, f"duplicate sentence exception: {sentence}"
        assert reason.startswith(("reworded-to: ", "dropped-because: ")) and reason.split(": ", 1)[1], row
        if reason.startswith("reworded-to:"):
            assert replacement and _sentences(replacement) == [replacement], row
        else:
            assert not replacement, row
        exceptions[sentence] = replacement
    return exceptions


def _reviewed_exceptions(original, expected_digest=EXCEPTION_ROWS_SHA256):
    rows = list(csv.DictReader(EXCEPTIONS.read_text(encoding="utf-8").splitlines(), delimiter="\t"))
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode("utf-8")).hexdigest()
    # Bind the reviewed table at definition time: replacing a mutable module
    # attribute during a run cannot authorize new drops or rewordings.
    assert digest == expected_digest, (
        "cannot reach original sentences with unreviewed exception rows: lead review and pin update required")
    exceptions = _exceptions(original)
    # Both reviewed rows drop obsolete navigation. There is no rewording grant
    # for any sentence, independent of its words; new rows need lead review.
    assert not any(exceptions.values()), "sentence rewording requires lead review"
    return exceptions


def _navigation_links(text, *, document=None):
    """Follow only links rendered by CommonMark, including reference-style links."""
    def links(tokens):
        for token in tokens:
            if token.type == "link_open":
                yield token.attrGet("href")
            if token.children:
                yield from links(token.children)

    parser = MarkdownIt("commonmark")
    if document is None:
        return list(links(parser.parse(text)))
    # Parse the entire document so reference definitions and container state
    # (including HTML comments spanning a route) are both preserved. Source maps
    # restrict navigation to this route's rendered inline tokens.
    start = document.index(text)
    first = document.count("\n", 0, start)
    last = first + len(text.splitlines())
    tokens = [token for token in parser.parse(document)
              if token.type == "inline" and token.map and first <= token.map[0] and token.map[1] <= last]
    return list(links(tokens))


def _reachable(root, route):
    """Follow local reference links from this route only, including onward links."""
    line = routing.section(root, route.anchor)
    assert line, route.id
    texts, seen, pending = [root], set(), [GUIDE / target for target in _navigation_links(line, document=root)]
    while pending:
        path = pending.pop().resolve()
        assert path.is_relative_to(GUIDE.resolve()), path.name
        if path in seen:
            continue
        seen.add(path)
        text = path.read_text(encoding="utf-8")
        texts.append(text)
        pending.extend(path.parent / target for target in _navigation_links(text))
    return texts


def test_every_fixture_sentence_has_an_original_route_owner():
    original = BASELINE.read_text(encoding="utf-8")
    held = set().union(*(_held_sentences(original, route_id) for route_id in routing.load().routes))
    assert set(_sentences(original)) <= held
    _exceptions(original)


@pytest.mark.parametrize("route_id", sorted(routing.load().routes))
def test_each_route_reaches_its_original_safety_and_authority_sentences(route_id):
    """Every fixture sentence survives on every route that held it, or has a reviewed exception.

    Whole sentences are compared after whitespace and markup normalization.
    Only the closed, reviewed navigation exceptions may drop a sentence.
    """
    original = BASELINE.read_text(encoding="utf-8")
    exceptions = _reviewed_exceptions(original)
    required = {exceptions.get(getattr(s, "bare", s), s) for s in _held_sentences(original, route_id)} - {""}
    root = (GUIDE / "SKILL.md").read_text(encoding="utf-8")
    reachable = _reachable(root, routing.load().routes[route_id])
    available = {s for text in reachable for s in _sentences(text)}
    missing = sorted(required - available)
    assert not missing, f"{route_id} cannot reach original sentences: {missing}"
    # The whole-sentence equality above protects every visible sentence equally;
    # only approved navigation-location normalization is allowed by _instructions.
    # A caption's own sentence is a label (e.g. "Review.") that other text may
    # begin with; its words are guarded by every sentence it captions above.
    qualified = sorted((s, candidate) for s in required if not getattr(s, "caption", False)
                       for candidate in available
                       if candidate != s and re.match(re.escape(s.rstrip('.!?')) + r"\b", candidate))
    # An intact duplicate cannot hide a newly appended conditional exception,
    # even across a paragraph break or with a different verb. Original following
    # sentences are allowed exactly as written; no exception wording is hand-kept.
    original_sentences = {getattr(s, "bare", s) for s in _sentences(original)}
    for text in reachable:
        sentences = _sentences(text)
        qualified.extend((rule, following) for rule, following in zip(sentences, sentences[1:])
                         if rule in required
                         and re.match(r"(?:If|Unless|Except|But|However|Otherwise|When|Although)\b", following)
                         and getattr(following, "bare", following) not in original_sentences)
    assert not qualified, f"{route_id} qualifies original authority rules: {qualified}"


@pytest.mark.parametrize("route_id", sorted(routing.load().routes))
def test_route_corpus_includes_its_root_entry_and_every_declared_detail(route_id):
    route = routing.load().routes[route_id]
    entry = next(e for e in corpus._routes([]) if e["id"] == f"route:{route_id}")
    root = (GUIDE / "SKILL.md").read_text(encoding="utf-8")
    assert _normal(routing.section(root, route.anchor)) in entry["text"]
    for detail in route.detail:
        assert _normal((GUIDE.parents[1] / detail).read_text(encoding="utf-8")) in entry["text"], detail


@pytest.mark.parametrize("detail", [path for path in sorted((GUIDE / "references").glob("*.md"))
                                    if "**Repair.**" in path.read_text(encoding="utf-8")], ids=lambda p: p.name)
def test_every_explicit_repair_step_keeps_the_roots_repair_owner_rule(detail):
    """A repair step must retain the root's complete rule, including the person-owned stop."""
    root = (GUIDE / "SKILL.md").read_text(encoding="utf-8")
    rules = [sentence for sentence in _sentences(root) if "reply carries repair" in sentence]
    assert rules
    document = detail.read_text(encoding="utf-8")
    paragraphs = _rendered_paragraphs(document)
    step = re.search(r"\d+\. \*\*Repair\.\*\*\s*(.*?)(?=\n\d+\. |\Z)", document, re.S)
    assert step
    section = _normal(" ".join(_paragraphs_in_lines(paragraphs, _source_lines(document, step.group(1)))))
    assert all(rule in section for rule in rules), detail.name


def test_validating_any_skill_route_reads_guidance_without_importing_components():
    """Every shipped route validates in a fresh process; a missing section still refuses."""
    probe = r'''
import copy, json, sys
from g6.core import selector
from g6.core.registry import get_registry
from g6.skills import corpus

data = corpus.load()
registry = get_registry()
packages = {registry.refs[c].module.split(":", 1)[0]: c
            for c in data["components"] if c in registry.refs}
before = set(sys.modules)
results = []
for entry in data["entries"]:
    if entry["kind"] != "route":
        continue
    isolated = {**data, "entries": [entry]}
    candidate = selector.select("using g6", corpus=isolated)["candidates"][0]
    results.append((candidate["id"], candidate["validated"], candidate["issues"]))
missing = copy.deepcopy(isolated)
missing["entries"][0]["call"]["section"] = "## A section that does not exist"
refused = selector.select("using g6", corpus=missing)["candidates"][0]
print(json.dumps({"results": results, "refused": refused,
                  "imports": sorted(c for p, c in packages.items() if p in sys.modules and p not in before)}))
'''
    env = {**os.environ, "PYTHONPATH": str(GUIDE.parents[2])}
    done = subprocess.run([sys.executable, "-X", "utf8", "-c", probe], capture_output=True,
                          text=True, env=env, timeout=120, check=True)
    result = json.loads(done.stdout.strip().splitlines()[-1])
    assert result["imports"] == []
    assert {row[0] for row in result["results"]} == {f"route:{r}" for r in routing.load().routes}
    assert all(valid and not issues for _, valid, issues in result["results"])
    assert result["refused"]["validated"] is False
    assert result["refused"]["issues"] == ["the skill has no section ## A section that does not exist"]


@pytest.mark.parametrize("task,phase,baseline_routes", TASKS)
def test_selection_loses_no_route_offered_at_the_integration_baseline(task, phase, baseline_routes):
    """These literal route sets were read from c5782df1's corpus with the unchanged selector."""
    from g6.core import selector

    offered = {c["id"] for c in selector.select(task, phase)["candidates"]}
    assert baseline_routes <= offered, sorted(offered)


@pytest.mark.parametrize("task,phase,baseline_routes", TASKS)
def test_live_route_details_preserve_the_baseline_choices(task, phase, baseline_routes):
    """Exercise the route builder and public selector together, without building or writing a corpus."""
    from g6.core import selector

    data = corpus.load()
    tools = [e for e in data["entries"] if e["kind"] == "tool"]
    live = {**data, "entries": [e for e in data["entries"] if e["kind"] != "route"] + corpus._routes(tools)}
    offered = {c["id"] for c in selector.select(task, phase, corpus=live)["candidates"]}
    assert baseline_routes <= offered, sorted(offered)


def test_root_keeps_repair_owner_and_workflow_registration_at_the_moment_of_need():
    root = _normal((GUIDE / "SKILL.md").read_text(encoding="utf-8"))
    assert "When a G6 reply carries `repair`, follow its step; if `by` is person, tell the person and stop." in root
    line = routing.section((GUIDE / "SKILL.md").read_text(encoding="utf-8"), "**workflow.**")
    assert "where the server is registered" in _normal(line)


def test_reference_guidance_has_no_empty_signpost_or_self_pointer():
    for path in (GUIDE / "references").glob("*.md"):
        text = path.read_text(encoding="utf-8")
        assert "Route signpost" not in text, path.name
        assert "sections below are routes too" not in _normal(text), path.name
        targets = LINK.findall(text) + re.findall(r"`([^`\n]+\.md)`", text)
        assert all((path.parent / target).resolve() != path.resolve() for target in targets), path.name
