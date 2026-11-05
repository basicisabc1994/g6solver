# SPDX-License-Identifier: Apache-2.0
"""Public copy says "checked" for what G6 ran and "challenged" for what an independent reviewer or a replay tried to
break; it never says that G6 verified work (the owner's ruling, 2026-11-05). The copy is read as a reader sees it:
Markdown rendered by the site's own renderer (``pages.views.markdown_html``), a skill's front matter as G6 reads it
(``g6.core.skills.front_matter``), and HTML read by an HTML parser, its visible text and the attributes a reader or a
search engine is shown. The reliability state keeps its name: a code span is passed over when it holds only a state or
an identifier (`verified`, `verify_answer`); a code block is code, but a diagram is read. The three states named
together as one list pass, and so does the bare word in quotation marks, which mentions it.

``LEFT`` holds the names the owner kept (2026-11-05): a failure mode's title and description, an interface's name, the
guide's Pattern 5, a field of study and an essay's title, a third party's task, an essay's heading, a registered
milestone. Not covered here: the research papers (``docs/theory``), the registered study records (``docs/validation``),
the design records (``docs/superpowers``), the release checklist (``docs/releases/PLAN_AUDIT.md``), the vendored skills,
the certificates and the generated repository graph. The essays may discuss verification as a field, but no sentence in
them may say G6 does it."""
from __future__ import annotations

import ast
import re
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
#: The bare word in quotation marks mentions it; a quoted phrase is read.
MENTION = re.compile(r"[\"“‘]\s*verif\w*[.,;:!?]?\s*[\"”’]", re.IGNORECASE)
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
#: Elements that sit inside a line of text; any other element starts or ends a block.
_INLINE = frozenset("a abbr b bdi bdo cite code data dfn em i kbd mark q s samp small span strong sub sup time u var "
                    "wbr".split())
_SHOWN = ("title", "alt", "aria-label")


def _diagram(source: str) -> str:
    """A diagram's words as prose: a node labelled with a state's name alone names the state; every other label is
    read, quotation marks and all."""
    source = re.sub(r'\["(?:verified|qualified-draft|blocked-escalated)"\]', " ", source)
    return source.replace('"', " ")


#: Python that prose cannot be: a call, an import, an attribute, a subscript, an assignment, a keyword, an ordering.
_CODE = (ast.Call, ast.Import, ast.ImportFrom, ast.Attribute, ast.Subscript, ast.Assign, ast.keyword)
_ORDER = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)


def _is_name(code: str) -> bool:
    """A code span that is not prose: a state, an identifier with no space in it (a field, a tool, a path), or code
    Python's own parser reads as code (``verify(body, artifact_bytes)``, an import line)."""
    code = code.strip()
    if code.lower() in STATE_NAMES or (code and not any(ch.isspace() for ch in code)):
        return True
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    return any(isinstance(node, _CODE) or (isinstance(node, ast.Compare) and all(isinstance(op, _ORDER)
                                                                             for op in node.ops))
               for node in ast.walk(tree))


class _Reader(HTMLParser):
    """What a reader sees: each block's text, with inline markup joined into it, and each attribute a reader or a
    search engine is shown, as units of their own. Scripts and styles are not read, nor code blocks except diagrams;
    a code span is read unless it only names something."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.units: list[str] = []
        self.run: list[str] = []
        self.hidden = 0                       # inside <script> or <style>
        self.pre: list[bool] = []             # the open <pre> blocks: True for a diagram
        self.code: list[str] | None = None    # an inline code span being read

    def end_run(self) -> None:
        text = "".join(self.run)
        if text.strip():
            self.units.append(text)
        self.run = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        self.units += [values[name] for name in _SHOWN if values.get(name)]
        meta = values.get("name", "") == "description" or values.get("name", "").startswith("twitter:") \
            or values.get("property", "").startswith("og:")
        if tag == "meta" and meta and values.get("content") and not re.match(r"(?:https?:|/)", values["content"]):
            self.units.append(values["content"])                    # an address is not read
        if tag in ("script", "style"):
            self.hidden += 1
        elif tag == "pre":
            self.end_run()
            self.pre.append("mermaid" in values.get("class", "").split())
        elif tag == "code" and not self.pre:
            self.code = []
        elif tag not in _INLINE:
            self.end_run()

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
        elif tag == "pre" and self.pre:
            self.pre.pop()
            self.end_run()
        elif tag == "code" and self.code is not None:
            text, self.code = "".join(self.code), None
            if not _is_name(text):
                self.run.append(text)                                # prose in code format is read
        elif tag not in _INLINE:
            self.end_run()

    def handle_data(self, data: str) -> None:
        if self.hidden:
            return
        if self.code is not None:
            self.code.append(data)
        elif self.pre:
            if self.pre[-1]:
                self.run.append(_diagram(data))
        else:
            self.run.append(data)


def _units(page: str) -> list[str]:
    reader = _Reader()
    reader.feed(page)
    reader.close()
    reader.end_run()
    return reader.units


def _page_copy(page: str) -> str:
    """What a visitor reads on a page: its blocks and its shown attributes, one paragraph each."""
    return "\n\n".join(_units(page))


#: The docs site's Markdown extensions: MkDocs' own (toc, tables, fenced_code) and those site/mkdocs.yml adds, but
#: pymdownx, which is not a dependency here: its fences are read as Python-Markdown's, and a collapsible note (???) as a
#: note (!!!), with the same words.
DOCS_SITE = ("toc", "tables", "fenced_code", "admonition", "attr_list", "md_in_html")


def _docs_site_html(text: str) -> str:
    return markdown.markdown(re.sub(r"^(\s*)\?\?\?\+?", r"\1!!!", text, flags=re.MULTILINE), extensions=DOCS_SITE)


def _markdown_copy(text: str, *, docs_site: bool = False) -> str:
    """What a reader reads in Markdown: the front matter's values as G6 reads them, then the page as it is rendered,
    by MkDocs for the docs site and by the website's own renderer for everything else."""
    page = _docs_site_html(text) if docs_site else views.markdown_html(text)
    return "\n\n".join([*front_matter(text).values(), _page_copy(page)])


def claims(copy: str) -> list[str]:
    """Each claim form left in ``copy`` once ``LEFT``, the mentions and the three-state lists are taken out, in its
    context."""
    copy = " ".join(copy.split())
    for name in LEFT:
        copy = re.sub(re.escape(name), " ", copy, flags=re.IGNORECASE)
    copy = MENTION.sub(" ", copy)
    copy = STATE_LIST.sub(lambda m: " " if {s.lower() for s in re.findall(_STATE, m.group(0), re.IGNORECASE)}
                          == set(STATE_NAMES) else m.group(0), copy)
    return [copy[max(0, m.start() - 60):m.end() + 60] for m in CLAIM.finditer(copy)]


def g6_claims(page: str) -> list[str]:
    """Each sentence of a page that names G6, in any case, and makes a claim form."""
    sentences = (sentence for unit in _units(page) for sentence in re.split(r"(?<=[.!?])\s+", " ".join(unit.split())))
    return [sentence for sentence in sentences if re.search(r"\bG6\b", sentence, re.IGNORECASE) and claims(sentence)]


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


def test_the_docs_site_is_read_with_the_extensions_mkdocs_yml_names():
    config = (OSS / "site" / "mkdocs.yml").read_text(encoding="utf-8")
    block = config.split("\nmarkdown_extensions:\n", 1)[1].split("\n\n", 1)[0]
    named = {name for name in re.findall(r"^  - ([\w.]+)", block, re.MULTILINE) if not name.startswith("pymdownx.")}
    assert named == set(DOCS_SITE) - {"tables", "fenced_code"}, named
    # the collapsible note's words are read, not taken for an indented code block
    assert claims(_markdown_copy('??? question "Why"\n    G6 verifies it.\n', docs_site=True))


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
