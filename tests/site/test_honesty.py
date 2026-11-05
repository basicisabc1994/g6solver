# SPDX-License-Identifier: Apache-2.0
"""Public copy says "checked" for what G6 ran and "challenged" for what an independent reviewer or a replay tried to
break; it never says that G6 verified work (the owner's ruling, 2026-11-05).

What it guards against: honest drift, a claim written in good faith by the owner, an agent or a contributor. It does
not guard against deliberate evasion: anyone can hide a word in an image, in CSS or behind look-alike characters, and
no reading of the text stops that.

The copy is read as a reader sees it: Markdown rendered by the site's own renderer (``pages.views.markdown_html``) or,
for the docs site, with the extensions site/mkdocs.yml names; a skill's front matter as G6 reads it
(``g6.core.skills.front_matter``); HTML read by an HTML parser, its visible text, the attributes and the structured-data
fields a reader or a search engine is shown, with only block-level elements ending a run of text. The reliability state
keeps its name: a code span is passed over when it holds a state, the three states listed together, a Python identifier
or dotted name, or code Python parses as code (`verified`, `verify_answer`, `verify(body, artifact)`); a code block is
code, but a diagram is read. The three states named together as one list pass, and so does the bare word in quotation
marks, which mentions it.

``LEFT`` holds the names the owner kept (2026-11-05): a failure mode's title and description, an interface's name, the
guide's Pattern 5, a field of study and an essay's title, a third party's task, an essay's heading, a registered
milestone. Not covered here: the research papers (``docs/theory``), the registered study records (``docs/validation``),
the design records (``docs/superpowers``), the release checklist (``docs/releases/PLAN_AUDIT.md``), the vendored skills,
the certificates and the generated repository graph. The essays may discuss verification as a field, but no sentence in
them may say G6 does it."""
from __future__ import annotations

import ast
import html
import json
import re
import unicodedata
from html.parser import HTMLParser
from pathlib import Path

import markdown
import pytest

from g6.core.skills import front_matter
from pages import views
from pages.routes import PAGES

OSS = Path(__file__).resolve().parents[2]

#: The claim forms.
CLAIM = re.compile(r"\bverif(?:y|ies|ied|ying|ications?)\b", re.IGNORECASE)
STATE_NAMES = ("verified", "qualified-draft", "blocked-escalated")
_STATE = r"(?:verified|qualified-draft|blocked-escalated)"
_SEP = r"(?:\s*[,;/]\s*|\s+)(?:(?:or|and)\s+)?"
#: Three states in one list (a list is kept only when it names all three): the home page's chips, llms.txt, a note.
STATE_LIST = re.compile(rf"\b{_STATE}{_SEP}{_STATE}{_SEP}{_STATE}\b", re.IGNORECASE)


def _all_states(text: str) -> bool:
    return {state.lower() for state in re.findall(_STATE, text, re.IGNORECASE)} == set(STATE_NAMES)
#: The bare word in quotation marks, double or single, straight or curly, mentions it; a quoted phrase is read.
MENTION = re.compile(r"(?:\"\s*verif\w*[.,;:!?]?\s*\"|(?<!\w)'\s*verif\w*[.,;:!?]?\s*'(?!\w)"
                     r"|\u201c\s*verif\w*[.,;:!?]?\s*\u201d|\u2018\s*verif\w*[.,;:!?]?\s*\u2019)", re.IGNORECASE)
#: Names left as they are, each with the reason; the report lists where each one stands.
LEFT = {
    "work called verified when the evidence does not support it":
        "the title of the failure mode overstated_verification in src/g6/core/data/failure_modes/core.json",
    "labelled checked or verified beyond what its evidence shows": "the same failure mode's description, there",
    "verification plan": "the action gate's field (verification_plan, G6-INV-05)",
    "verification contract": "action_logic's control, which checks an action's verification plan",
    "verification-contract": "the same control's name, hyphenated before a noun",
    "verified learning event": "the ECHA spec's milestone (VLE-1) and the V4 study's registered question",
    "Self-Verification Impossibility": "the guide's Pattern 5, Model 16 of the Unified Theory",
    "formal verification": "the field of study (the About page) and an essay's title (the using-g6 lifecycle)",
    "fact-verification": "SciFact's task, a third party's name",
    "Move deliberately and verify things": "an essay's heading, quoted",
    "--no-verify": "git's flag that skips its hooks, an interface name (the coding agent gate's skill)",
    "verification-before-completion": "the vendored superpowers skill's name, a third party's (the using-g6 lifecycle)",
}

#: The Markdown and text a reader sees on GitHub and on the docs site, and the skills G6 installs.
MARKDOWN = sorted({
    *(OSS / name for name in ("README.md", "AGENT_GUIDE.md", "examples/README.md", "docs/AI_FOR_AI_WORKFLOW.md",
                              "docs/AI_FOR_AI_CONTINUATION.md", "docs/skills/using-g6/TESTING.md",
                              "docs/releases/launch.md", "docs/releases/v0.1.1.md", "docs/releases/OWNER_PILOT.md",
                              "docs/releases/RELEASE_TRIALS.md", "site/docs/llms.txt")),
    *(OSS / "site" / "docs").rglob("*.md"),
    *(OSS / "web" / "content").glob("*.md"),
    *(OSS / "src" / "g6" / "skills" / "using-g6").rglob("*.md"),
    *(path for path in (OSS / "src" / "g6").rglob("skill/SKILL.md") if "vendor" not in path.parts),
})
#: Elements that end a run of text: the HTML block-level elements, as Python-Markdown, the site's renderer, lists them.
#: Every other element (a, span, em, br, tspan) sits inside a line, and a line break is whitespace.
_BLOCK = frozenset(markdown.util.BLOCK_LEVEL_ELEMENTS)
_SHOWN = ("title", "alt", "aria-label", "aria-description", "placeholder")
#: The structured-data fields a search engine shows a reader, read wherever they sit; an address field is not one.
_READ_FIELDS = ("headline", "description", "name", "alternativeHeadline", "abstract", "text")
_ADDRESS_FIELDS = ("url", "@id", "image", "sameAs", "logo", "contentUrl", "thumbnailUrl")
#: The meta tags a reader or a search engine is shown, by their name or property; an address tag is not one.
_META_READ = frozenset({"description", "og:title", "og:description", "og:site_name", "og:image:alt", "twitter:title",
                        "twitter:description", "twitter:image:alt"})
_BUTTONS = ("button", "submit", "reset")                  # an <input> of these types shows its value
#: Characters a reader never sees: the soft hyphen and the zero-width ones.
_INVISIBLE = re.compile("[\u00ad\u200b-\u200d\u2060\ufeff]")
#: What an exempt code span becomes: a neutral word, so the words and the quotation marks around it stay as they were.
PLACEHOLDER = "CODE"


def _normalise(text: str) -> str:
    """The text as a reader reads it: compatibility forms folded (NFKC: a ligature is its letters), the characters
    nobody sees taken out, the marks on a letter taken off (a combining mark cannot split a word), whitespace
    collapsed."""
    text = _INVISIBLE.sub("", unicodedata.normalize("NFKC", text))
    text = "".join(ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn")
    return " ".join(unicodedata.normalize("NFC", text).split())


def _diagram(source: str) -> str:
    """A diagram's words as prose, as the drawn diagram shows them: Mermaid's own escapes (#quot;, #9829;) decoded and
    its emphasis marks (*, _, `) taken out; then a node labelled with a state's name alone names the state, and every
    other label is read, quotation marks and all."""
    source = re.sub(r"#(\d+|[A-Za-z]+);", lambda m: html.unescape(f"&{'#' if m.group(1).isdigit() else ''}"
                                                                  f"{m.group(1)};"), source)
    source = re.sub(r"[*_`]", "", source)
    source = re.sub(r'\["\s*(?:verified|qualified-draft|blocked-escalated)\s*"\]', " ", source)
    return source.replace('"', " ")


#: Python that prose cannot be: a call, an import, an attribute, a subscript, an assignment, a keyword, an ordering.
_CODE = (ast.Call, ast.Import, ast.ImportFrom, ast.Attribute, ast.Subscript, ast.Assign, ast.keyword)
_ORDER = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)


def _is_name(code: str) -> bool:
    """A code span that is not prose: a state, the three states listed together, a Python identifier or dotted name
    (a field, a tool, a module), or code Python's own parser reads as code (``verify(body, artifact_bytes)``, an
    import line)."""
    code = _normalise(code)
    if code.lower() in STATE_NAMES or (STATE_LIST.fullmatch(code) and _all_states(code)):
        return True
    if code and all(part.isidentifier() for part in code.split(".")):
        return True
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    return any(isinstance(node, _CODE) or (isinstance(node, ast.Compare) and all(isinstance(op, _ORDER)
                                                                             for op in node.ops))
               for node in ast.walk(tree))


class _Span(str):
    """An inline code span's text, kept apart until the words around it are known."""


def _fields(value: object, key: str = ""):
    """The reader-facing fields of parsed structured data, wherever they sit; an address field is never read."""
    if isinstance(value, dict):
        for name, item in value.items():
            if name not in _ADDRESS_FIELDS:
                yield from _fields(item, name)
    elif isinstance(value, list):
        for item in value:
            yield from _fields(item, key)
    elif isinstance(value, str) and key in _READ_FIELDS:
        yield value


class _Reader(HTMLParser):
    """What a reader sees: each block's text, with inline markup joined into it, and each attribute and structured-data
    field a reader or a search engine is shown, as units of their own. Scripts and styles are not read (structured
    data is, through json), nor code blocks except diagrams."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.units: list[list[str]] = []
        self.run: list[str] = []
        self.hidden = 0                       # inside <script> or <style>
        self.pre: list[bool] = []             # the open <pre> blocks: True for a diagram
        self.code: list[str] | None = None    # an inline code span being read
        self.data: list[str] | None = None    # a structured-data script being read

    def end_run(self) -> None:
        if "".join(self.run).strip():
            self.units.append(self.run)
        self.run = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}     # html.parser gives attribute names in lower case
        name, prop = values.get("name", "").lower(), values.get("property", "").lower()
        self.units += [[values[shown]] for shown in _SHOWN if values.get(shown)]
        if tag == "input" and values.get("type", "").lower() in _BUTTONS and values.get("value"):
            self.units.append([values["value"]])
        if tag == "meta" and values.get("content") and ({name, prop} & _META_READ):
            self.units.append([values["content"]])                  # read by its key; an address key is not one
        classes = values.get("class", "").lower().split()
        if tag == "script" and values.get("type", "").lower() == "application/ld+json":
            self.data = []
        elif tag in ("script", "style"):
            self.hidden += 1
        elif tag == "pre":
            self.end_run()
            self.pre.append("mermaid" in classes)                   # the website's diagram
        elif tag == "code" and self.pre:
            self.pre[-1] = self.pre[-1] or "language-mermaid" in classes   # the docs site's mermaid fence
        elif tag == "code":
            self.code = []
        elif tag == "br":
            (self.code if self.code is not None else self.run).append("\n")    # a line break is whitespace
        elif tag in _BLOCK:
            self.end_run()

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.data is not None:
            try:
                data = json.loads("".join(self.data))
            except json.JSONDecodeError:
                data = None                                          # a search engine cannot read it either
            self.units += [[text] for text in _fields(data)]
            self.data = None
        elif tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
        elif tag == "pre" and self.pre:
            self.pre.pop()
            self.end_run()
        elif tag == "code" and self.code is not None:
            self.run.append(_Span("".join(self.code)))
            self.code = None
        elif tag in _BLOCK:
            self.end_run()

    def handle_data(self, data: str) -> None:
        if self.data is not None:
            self.data.append(data)
            return
        if self.hidden:
            return
        if self.code is not None:
            self.code.append(data)
        elif self.pre:
            if self.pre[-1]:
                self.run.append(_diagram(data))
        else:
            self.run.append(data)


def _units(page: str) -> list[list[str]]:
    reader = _Reader()
    reader.feed(page)
    reader.close()
    reader.end_run()
    return reader.units


def _resolve(unit: list[str], mark=lambda span: PLACEHOLDER) -> str:
    """A unit's text. A code span is exempt (``mark`` stands in for it) when it only names something and stands as a
    word of its own; inside a word, or as prose, it is read as the words it holds."""
    out: list[str] = []
    for index, part in enumerate(unit):
        if isinstance(part, _Span):
            before = _normalise("".join(out) + "|")[-2:-1]          # the visible character either side of it
            after = _normalise("|" + "".join(unit[index + 1:]))[1:2]
            in_word = bool(re.match(r"\w", before) or re.match(r"\w", after))
            part = mark(part) if _is_name(part) and not in_word else str(part)
        out.append(part)
    return "".join(out)


def _page_copy(page: str) -> str:
    """What a visitor reads on a page: its blocks and its shown attributes, one paragraph each."""
    return "\n\n".join(_resolve(unit) for unit in _units(page))


#: The docs site's Markdown extensions: MkDocs' own (toc, tables, fenced_code) and those site/mkdocs.yml adds, but
#: pymdownx, which is not a dependency here. Of those that change how indented text renders, admonition and
#: md_in_html are installed; pymdownx.details (a collapsible note, ???) and pymdownx.tabbed (a tab, === "Title") are
#: read as notes (!!!), with the same words; pymdownx.superfences' fences are read as Python-Markdown's, so a fence
#: inside an indented block is read as prose (more is read, nothing is hidden).
DOCS_SITE = ("toc", "tables", "fenced_code", "admonition", "attr_list", "md_in_html")
DOCS_SITE_PYMDOWNX = ("pymdownx.superfences", "pymdownx.tabbed", "pymdownx.details")


def _docs_site_html(text: str) -> str:
    text = re.sub(r"^([ \t]*)\?\?\?\+?(?=[ \t]+\S)", r"\1!!!", text, flags=re.MULTILINE)        # a collapsible note
    text = re.sub(r'^([ \t]*)===[+!]*(?=[ \t]+")', r"\1!!! tab", text, flags=re.MULTILINE)     # a tab
    return markdown.markdown(text, extensions=DOCS_SITE)


def _markdown_copy(text: str, *, docs_site: bool = False) -> str:
    """What a reader reads in Markdown: the front matter's values as G6 reads them, then the page as it is rendered,
    by MkDocs for the docs site and by the website's own renderer for everything else. Words the site holds back
    until extensions.SHOW_PLANNED is on are read too: GitHub shows them."""
    text = views._PLANNED.sub(lambda m: m.group(1), text)
    page = _docs_site_html(text) if docs_site else views.markdown_html(text)
    return "\n\n".join([*front_matter(text).values(), _page_copy(page)])


def claims(copy: str) -> list[str]:
    """Each claim form left in ``copy``, read as a reader reads it, once the whole phrases in ``LEFT``, the mentions
    and the three-state lists are taken out, in its context."""
    copy = _normalise(copy)
    for name in LEFT:
        copy = re.sub(rf"(?<![\w-]){re.escape(name)}(?![\w-])", " ", copy, flags=re.IGNORECASE)
    copy = MENTION.sub(" ", copy)
    copy = STATE_LIST.sub(lambda m: " " if _all_states(m.group(0)) else m.group(0), copy)
    return [copy[max(0, m.start() - 60):m.end() + 60] for m in CLAIM.finditer(copy)]


def g6_claims(page: str) -> list[str]:
    """Each sentence of a page that names G6, in any case and in a code span too, and makes a claim form."""
    found = []
    for unit in _units(page):
        spans: dict[str, str] = {}

        def mark(span: str, spans: dict[str, str] = spans) -> str:
            key = f"\x00{len(spans)}\x00"
            spans[key] = str(span)
            return key

        for sentence in re.split(r"(?<=[.!?])\s+", _normalise(_resolve(unit, mark))):
            read = sentence
            for key, value in spans.items():
                read, sentence = read.replace(key, value), sentence.replace(key, PLACEHOLDER)
            if re.search(r"\bG6\b", _normalise(read), re.IGNORECASE) and claims(sentence):
                found.append(_normalise(read))
    return found


def test_the_check_refuses_the_claim_and_allows_the_state_in_code_format():
    # the instrument itself: a claim is found in prose and in a page, and each allowed form passes
    assert claims("Each change is verified in its own worktree.")
    assert claims("G6 verifies the patch") and claims("Verification: does it work?") and claims("we verify it")
    assert claims(_page_copy('<p class="x">The result is <strong>verified</strong>.</p>'))
    assert claims(_page_copy('<meta name="description" content="Every answer verified.">'))
    assert claims(_page_copy('<meta property="og:description" content="Every answer verified.">'))
    assert claims(_page_copy('<img alt="A verified answer"><a title="Verified">x</a><svg aria-label="verified"/>'))
    assert claims(_markdown_copy("A [link](x.md) to a verified fix.\n"))
    assert claims(_markdown_copy("---\nname: x\ndescription: Use when a patch must be verified.\n---\n\n# X\n"))
    assert not claims(_markdown_copy("The state is `verified`, `qualified-draft` or `blocked-escalated`.\n"))
    assert not claims(_markdown_copy("Call `coding_agent_gate.verify_answer` with a `verifier`.\n"))
    # a code span Python reads as code is code; one it reads as words is prose
    assert not claims(_markdown_copy("`verify(body, artifact_bytes)` names it; `from g6.evidence import verify`.\n"))
    for prose in ("`patch is verified`", "`verified and complete`", "`not verified`", "`G6 verified it`"):
        assert claims(_markdown_copy(f"The patch: {prose}.\n")), prose
    assert not claims(_markdown_copy("```python\nassert verify(body) == []\n```\n"))
    # a diagram is read: its caption and its labels, quoted or not; a node labelled with the state alone names it
    assert claims(_markdown_copy("```mermaid A caption\nflowchart TD\n  a --> b[\"verify it\"]\n```\n"))
    assert claims(_markdown_copy("```mermaid Only a pass reaches verified.\nflowchart TD\n  a --> b\n```\n"))
    assert claims(_page_copy('<pre class="mermaid">a --&gt; b[&quot;verify it&quot;]</pre>'))
    assert not claims(_markdown_copy("```mermaid A caption\nflowchart TD\n  a --> ok[\"verified\"]\n```\n"))
    assert not claims(_page_copy('<pre class="mermaid">a --&gt; ok[&quot;verified&quot;]:::approve</pre>'))
    assert not claims(_page_copy("<p>Every result is <code>verified</code>.</p>"))
    assert not claims('Never summarise it as "done" or "verified".')
    assert not claims("Every result is verified, qualified-draft or blocked-escalated.")
    assert not claims(_page_copy('<span class="state">verified</span> <span class="state">qualified-draft</span> '
                                 '<span class="state">blocked-escalated</span>'))
    assert not claims("work called verified when the evidence does not support it")
    assert not claims(_page_copy('<meta property="og:url" content="https://g6solver.com/essays/verify-it/">'))
    assert not claims(_page_copy("<script>verify()</script><style>.verified{}</style><p>Checked.</p>"))
    # a LEFT phrase is left only whole; an exempt code span stands in as a word, keeping the words around it
    assert not claims("The field of formal verification.") and claims("A non-formal verification of it.")
    assert not claims(_page_copy('<p>Never call it "<code>verified</code>".</p>'))
    assert _page_copy("<p>The <code>verify_answer</code> op.</p>") == f"The {PLACEHOLDER} op."
    assert g6_claims("<p><code>G6</code> checks it.</p>") == [] and g6_claims("<p>G\u200b6 verifies it.</p>")
    # a word is read as it looks: a combining mark does not split it, and a name keeps its letters
    assert claims(_page_copy("<p>G6 produces v\u0301erified answers.</p>")) and not claims("G\u00f6del's theorem.")
    # structured data is read by its reader-facing fields, button values and placeholders are read, and so is a word
    # the site holds back for now
    assert claims(_page_copy('<script type="application/ld+json">{"a": {"description": "G6 verifies it."}}</script>'))
    assert not claims(_page_copy('<script type="application/ld+json">{"a": "G6 "verifies" it"}</script>'))  # unreadable
    assert not claims(_page_copy('<script type="application/ld+json">{"keywords": "verification"}</script>'))
    assert not claims(_page_copy('<meta property="og:url" content="G6 verifies it">'))     # an address tag, by its key
    assert claims(_page_copy('<input type="submit" value="Verify">')) and claims(_page_copy('<input placeholder="Verify">'))
    assert claims(_markdown_copy("Text. <!-- planned -->G6 verifies it.<!-- /planned -->\n"))
    # a diagram is read as drawn: Mermaid's escapes decoded, its emphasis marks gone
    assert claims(_markdown_copy('```mermaid A caption\nflowchart TD\n  a["G6 veri**fies** it"]\n```\n'))
    assert claims(_markdown_copy('```mermaid A caption\nflowchart TD\n  a["#118;erified it"]\n```\n'))


def test_the_essay_check_reads_sentences_not_the_page():
    assert g6_claims("<p>The field. G6 verifies every answer.</p>") == ["G6 verifies every answer."]
    assert g6_claims("<p>G6 checks it. Formal methods verify programs.</p>") == []
    # each attribute is a sentence of its own: the logo's label does not join a figure's
    assert g6_claims('<a aria-label="G6, home"></a><svg aria-label="A pipeline with a verification gap"></svg>') == []
    # each block is read on its own: a heading does not join the paragraph after it
    assert g6_claims("<h2>G6</h2><p>Formal methods verify programs.</p>") == []


# Review 1 of the check (2026-11-05): six ways past it, each read here as a reader reads it.
def test_a_word_split_by_markup_is_read_whole():
    assert claims(_markdown_copy("G6 veri**fies** every patch."))
    assert claims(_page_copy("<p>G6 veri<em>fies</em> every patch.</p>"))


def test_quotation_marks_exempt_a_mention_of_the_word_not_a_claim_in_them():
    assert not claims(_markdown_copy('Never summarise it as "done" or "verified".'))
    assert claims(_markdown_copy('G6 produces "verified answers" for every request.'))


def test_the_state_list_needs_all_three_states():
    assert not claims("Every result is verified, qualified-draft or blocked-escalated.")
    assert claims("G6 produces verified, qualified-draft answers.")


def test_an_attribute_is_read_whatever_its_quotes():
    assert claims(_page_copy("<meta name='description' content='G6 verifies every patch.'>"))


def test_code_format_exempts_a_state_or_an_identifier_not_prose():
    assert not claims(_markdown_copy("The state is `verified`."))
    assert claims(_markdown_copy("G6 `verifies every patch` before it ships."))


def test_the_essay_check_reads_g6_in_any_case():
    assert g6_claims("<p>g6 verifies every answer.</p>")


# Review 2 of the check (2026-11-05): five more, each closed once, at the text every source becomes.
def test_a_left_phrase_is_a_whole_phrase_not_part_of_another_word():
    assert claims(_markdown_copy("G6 performs informal verification of every answer."))


@pytest.mark.parametrize("source, essay", [('G6 produces "verified `answers`" for every request.', False),
                                           ("G6 veri`fies` every patch.", False),
                                           ("`G6` verifies every answer.", True)])
def test_an_exempt_code_span_keeps_the_words_around_it(source, essay):
    page = views.markdown_html(source)
    assert "<code>" in page
    assert g6_claims(page) if essay else claims(_page_copy(page))


def test_attribute_values_that_name_a_meta_are_read_in_any_case():
    assert claims(_page_copy('<meta name="DESCRIPTION" content="G6 verifies every answer.">'))
    assert claims(_page_copy('<meta property="OG:Description" content="G6 verifies every answer.">'))


def test_a_diagram_on_the_docs_site_is_read():
    assert claims(_markdown_copy('```mermaid\nflowchart TD\n  a["G6 verifies every patch"]\n```\n', docs_site=True))


def test_soft_hyphens_and_zero_width_characters_do_not_split_a_word():
    for hidden in ("&shy;", "\u200b", "\u200d", "\u2060", "\ufeff"):
        assert claims(_page_copy(f"<p>G6 produces veri{hidden}fied answers.</p>")), repr(hidden)
    assert claims(_page_copy("<p>G6 produces veri\ufb01ed answers.</p>"))       # a ligature, read as the letters


# Review 3 of the check (2026-11-05): the rest of the class, read as a reader reads honest copy.
def test_a_code_span_is_exempt_only_as_a_state_a_name_or_python_code():
    assert claims(_markdown_copy("G6 outputs are `verified!`"))
    assert not claims(_markdown_copy("`verified`, `coding_agent_gate.verify_answer` and `verify(body, artifact)`."))


def test_an_inline_element_keeps_a_sentence_and_a_word_whole():
    assert g6_claims("<p>G6<br>verifies every answer.</p>")
    assert claims(_page_copy("<svg><text>G6 veri<tspan>fies</tspan> every patch.</text></svg>"))
    assert g6_claims("<p><a href='/x'>G6</a> <span>verifies</span> every answer.</p>")


def test_a_name_in_another_alphabet_is_not_refused():
    assert claims(_markdown_copy("G6 is used in Hawai\u02bbi.")) == []


def test_ascii_single_quotes_mark_a_mention_as_curly_ones_do():
    assert claims("Avoid the word \u2018verified\u2019 in public copy.") == []
    assert claims(_markdown_copy("Avoid the word 'verified' in public copy.")) == []
    assert claims(_markdown_copy("G6 gives 'verified answers' every time."))
    assert claims("G6's verified answers' quality.")                     # an apostrophe is not a quotation mark


def test_a_state_only_diagram_label_is_exempt_after_its_emphasis_is_taken_out():
    assert claims(_markdown_copy('```mermaid\nflowchart TD\n  a["`**verified**`"]\n```\n')) == []


def test_structured_data_is_read_by_field_whatever_its_text_starts_with():
    data = '{"@type": "WebPage", "description": "/ G6 verifies every patch.", "url": "/verified/"}'
    assert claims(_page_copy(f'<script type="application/ld+json">{data}</script>'))
    nested = '{"author": {"name": "G6 verified"}, "image": {"name": "verified.png"}, "sameAs": ["/verify"]}'
    assert len(claims(_page_copy(f'<script type="application/ld+json">{nested}</script>'))) == 1


def test_a_docs_site_tab_is_read_as_prose():
    assert claims(_markdown_copy('=== "Result"\n\n    G6 verifies every patch.\n', docs_site=True))


def test_the_docs_site_is_read_with_the_extensions_mkdocs_yml_names():
    config = (OSS / "site" / "mkdocs.yml").read_text(encoding="utf-8")
    block = config.split("\nmarkdown_extensions:\n", 1)[1].split("\n\n", 1)[0]
    named = set(re.findall(r"^  - ([\w.]+)", block, re.MULTILINE))
    # every extension the docs site names is either rendered here or read as this test says (DOCS_SITE_PYMDOWNX)
    assert named == (set(DOCS_SITE) - {"tables", "fenced_code"}) | set(DOCS_SITE_PYMDOWNX), named
    # a collapsible note's and a tab's words are read, not taken for an indented code block
    assert claims(_markdown_copy('??? question "Why"\n    G6 verifies it.\n', docs_site=True))
    assert claims(_markdown_copy('===+ "One"\n    G6 verifies it.\n', docs_site=True))
    # a setext heading's underline is not a tab
    assert "<h1" in _docs_site_html("A title\n===\n")


def _file_copy(path: Path) -> str:
    return _markdown_copy(path.read_text(encoding="utf-8"), docs_site=(OSS / "site" / "docs") in path.parents)


@pytest.mark.parametrize("path", MARKDOWN, ids=lambda p: p.relative_to(OSS).as_posix())
def test_the_markdown_a_reader_sees_never_says_g6_verified_work(path):
    assert claims(_file_copy(path)) == [], path.relative_to(OSS).as_posix()


@pytest.mark.parametrize("path", ["/" + page.path for page in PAGES if page.namespace != "blog"])
def test_no_page_of_the_site_says_g6_verified_work(client, path):
    assert claims(_page_copy(client.get(path).content.decode())) == [], path


@pytest.mark.parametrize("path", ["/" + page.path for page in PAGES if page.namespace == "blog"])
def test_no_essay_says_that_g6_verifies(client, path):
    assert g6_claims(client.get(path).content.decode()) == [], path
