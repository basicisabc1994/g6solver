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
)


def _words(text: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())


def test_the_home_page_says_what_happened_when_we_tried_it(client):
    page = client.get("/").content.decode()
    section = page.split('id="tried-it"', 1)[1].split("</section>", 1)[0]
    words = _words(section)
    for phrase in SAID:
        assert phrase in words
    assert LINK in section
    assert not re.search(r"(?i)\bexperimental\b|\bverified\b|proof of concept", words)


def test_the_readme_says_the_same_and_links_to_the_record():
    text = " ".join((OSS / "README.md").read_text(encoding="utf-8").split())
    for phrase in SAID:
        assert phrase in text, phrase
    assert LINK in text


def test_the_record_the_pages_link_to_is_in_the_repository_and_labelled_a_worked_example():
    record = (OSS / RESULTS).read_text(encoding="utf-8")
    assert "Worked examples" in record
    assert "say nothing" in record and "how often G6 helps" in record
