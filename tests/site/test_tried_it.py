# SPDX-License-Identifier: Apache-2.0
"""What happened when we tried it (owner, 2026-10-29: launch with the results up front): the home page and the README
say plainly what the ten worked examples showed: the agent and its model, that G6 reproduced no defect, that six of
the ten patches failed the maintainers' tests, and what G6 is today. They link to the full record in the
repository."""
from __future__ import annotations

import html
import re
from pathlib import Path

OSS = Path(__file__).resolve().parents[2]
RESULTS = "validation/worked_examples/RESULTS.md"
LINK = f"https://github.com/basicisabc1994/g6solver/blob/main/{RESULTS}"
SAID = (
    "Claude Code with Claude Sonnet 5.5",
    "ten recent, real bugs",
    "G6 reproduced no defect",
    "Six of the ten patches failed the maintainers' tests",
    "not yet a defect finder",
    # round 2 (2026-10-29): the same ten bugs with G6's own model proposing checks in every review
    "A second round turned on G6's own model",
    "it proposed 51 checks, and none found a defect",
    "Five of the ten new patches failed",
    # the coding study (2026-11-06): the review loops, measured on unseen bugs, and where the findings are
    "We then built review loops around Claude Code and tested them on real bugs they had never seen",
    "None beat Claude Code alone by the rule we fixed in advance.",
)
#: Where each says the findings are: the Research page's coding study, and the README's docs copy of it.
FINDINGS = {"home": 'href="/research/#coding-study"', "README": "(site/docs/research.md#the-coding-study)"}
#: Said while round 2 ran; it has run.
STALE = ("under way",)


def _words(text: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())


def test_the_home_page_says_what_happened_when_we_tried_it(client):
    page = client.get("/").content.decode()
    section = page.split('id="tried-it"', 1)[1].split("</section>", 1)[0]
    words = _words(section)
    for phrase in SAID:
        assert phrase in words
    assert not [phrase for phrase in STALE if phrase in words]
    assert LINK in section and FINDINGS["home"] in section
    assert not re.search(r"(?i)\bexperimental\b|\bverified\b|proof of concept", words)


def test_the_readme_says_the_same_and_links_to_the_record():
    text = " ".join((OSS / "README.md").read_text(encoding="utf-8").split())
    for phrase in SAID:
        assert phrase in text, phrase
    section = text.split("## What happened when we tried it", 1)[1].split("## ", 1)[0]
    assert not [phrase for phrase in STALE if phrase in section]
    assert LINK in text and FINDINGS["README"] in section
    docs = (OSS / "site" / "docs" / "research.md").read_text(encoding="utf-8")
    assert "## The coding study {#the-coding-study}" in docs and "`aspirational` — protocol published, not yet run" \
        not in docs


def test_the_record_the_pages_link_to_is_in_the_repository_and_labelled_a_worked_example():
    record = (OSS / RESULTS).read_text(encoding="utf-8")
    assert "Worked examples" in record
    assert "say nothing" in record and "how often G6 helps" in record
