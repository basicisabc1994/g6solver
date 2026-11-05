# SPDX-License-Identifier: Apache-2.0
"""Public copy says "checked" for what G6 ran and "challenged" for what an independent reviewer or a replay tried to
break; it never says that G6 verified work (the owner's ruling, 2026-11-05). The reliability state keeps its name:
`verified` in code format names the state and is allowed, as is any identifier in code format (`verify_answer`,
`verifier`), the three states named together as a list, and the word in quotation marks, which mentions it.

``LEFT`` holds the names the owner decides on: a failure mode's title, a field's or a feature's name, a field of study,
a third party's words. Not covered here, and left for the owner: the research papers (``docs/theory``), the study
records (``docs/validation``), the design records (``docs/superpowers``), the release checklist
(``docs/releases/PLAN_AUDIT.md``), the vendored skills, the certificates and the generated repository graph. The essays
are about the field and never name G6: they may discuss verification, but no sentence in them may say G6 does it."""
from __future__ import annotations

import html
import re
from pathlib import Path

import pytest

from pages.routes import PAGES

OSS = Path(__file__).resolve().parents[2]

#: The claim forms.
CLAIM = re.compile(r"\bverif(?:y|ies|ied|ying|ications?)\b", re.IGNORECASE)
#: The three states named together, as a list: the home page's chips, llms.txt, a diagram's note.
STATES = re.compile(r"\bverified\b(?=[\s,;/]*(?:(?:or|and)\s+)?qualified-draft\b)", re.IGNORECASE)
#: A word or a short phrase in quotation marks mentions it.
QUOTED = re.compile(r'"[^"\n]{1,60}"|“[^”\n]{1,60}”')
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
_FENCE = re.compile(r"^```(\w*)([^\n]*)\n(.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)
_CODE_SPAN = re.compile(r"(`+)(?!`).+?(?<!`)\1(?!`)", re.DOTALL)
_MERMAID = re.compile(r'<pre class="mermaid">(.*?)</pre>', re.DOTALL)


def _diagram(source: str) -> str:
    """A diagram's words as prose: a node labelled with a state's name alone names the state; every other label is
    read, quotation marks and all."""
    source = re.sub(r'\["(?:verified|qualified-draft|blocked-escalated)"\]', " ", source)
    return source.replace('"', " ")


def _markdown_copy(text: str) -> str:
    """What a reader reads: no code (a diagram and its caption are read), no comment, no link target."""
    text = _FENCE.sub(lambda m: f"{m.group(2)}\n{_diagram(m.group(3))}" if m.group(1) == "mermaid" else "", text)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = _CODE_SPAN.sub("", text)
    return re.sub(r"\]\([^)\s]*\)", "]", text)


def _page_copy(page: str) -> str:
    """What a visitor reads: the text, the attributes a reader or a search engine is shown, and no code."""
    page = _MERMAID.sub(lambda m: html.escape(_diagram(html.unescape(m.group(1)))), page)
    page = re.sub(r"<(script|style|code)\b.*?</\1>", " ", page, flags=re.DOTALL | re.IGNORECASE)
    shown = [value for value in re.findall(r'\b(?:alt|title|aria-label|content|placeholder)="([^"]*)"', page)
             if not re.match(r"(?:https?:|/)", value)]                  # an address is not read
    text = html.unescape(" . ".join([re.sub(r"<[^>]+>", " ", page), *shown]))   # each attribute a sentence of its own
    return re.sub(r"https?://\S+", " ", text)


def claims(copy: str) -> list[str]:
    """Each claim form left in ``copy`` once the state's name, the quoted words and ``LEFT`` are taken out, in its
    context."""
    copy = " ".join(copy.split())
    for name in LEFT:
        copy = re.sub(re.escape(name), " ", copy, flags=re.IGNORECASE)
    copy = STATES.sub(" ", QUOTED.sub(" ", copy))
    return [copy[max(0, m.start() - 60):m.end() + 60] for m in CLAIM.finditer(copy)]


def test_the_check_refuses_the_claim_and_allows_the_state_in_code_format():
    # the instrument itself: a claim is found in prose and in a page, and each allowed form passes
    assert claims("Each change is verified in its own worktree.")
    assert claims("G6 verifies the patch") and claims("Verification: does it work?") and claims("we verify it")
    assert claims(_page_copy('<p class="x">The result is <strong>verified</strong>.</p>'))
    assert claims(_page_copy('<meta name="description" content="Every answer verified.">'))
    assert claims(_markdown_copy("A [link](x.md) to a verified fix.\n"))
    assert not claims(_markdown_copy("The state is `verified`, `qualified-draft` or `blocked-escalated`.\n"))
    assert not claims(_markdown_copy("Call `coding_agent_gate.verify_answer` with a `verifier`.\n"))
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
    assert not claims(_page_copy('<span class="state">verified</span> <span class="state">qualified-draft</span>'))
    assert not claims("work called verified when the evidence does not support it")
    assert not claims(_page_copy('<meta property="og:url" content="https://g6solver.com/essays/verify-it/">'))


def g6_claims(page: str) -> list[str]:
    """Each sentence of a page that names G6 and makes a claim form."""
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(_page_copy(page).split()))
    return [sentence for sentence in sentences if re.search(r"\bG6\b", sentence) and claims(sentence)]


def test_the_essay_check_reads_sentences_not_the_page():
    assert g6_claims("<p>The field. G6 verifies every answer.</p>") == ["G6 verifies every answer."]
    assert g6_claims("<p>G6 checks it. Formal methods verify programs.</p>") == []
    # each attribute is a sentence of its own: the logo's label does not join a figure's
    assert g6_claims('<a aria-label="G6, home"></a><svg aria-label="A pipeline with a verification gap"></svg>') == []


@pytest.mark.parametrize("path", MARKDOWN, ids=lambda p: p.relative_to(OSS).as_posix())
def test_the_markdown_a_reader_sees_never_says_g6_verified_work(path):
    assert claims(_markdown_copy(path.read_text(encoding="utf-8"))) == [], path.relative_to(OSS).as_posix()


@pytest.mark.parametrize("path", ["/" + page.path for page in PAGES if page.namespace != "blog"])
def test_no_page_of_the_site_says_g6_verified_work(client, path):
    assert claims(_page_copy(client.get(path).content.decode())) == [], path


@pytest.mark.parametrize("path", ["/" + page.path for page in PAGES if page.namespace == "blog"])
def test_no_essay_says_that_g6_verifies(client, path):
    assert g6_claims(client.get(path).content.decode()) == [], path
