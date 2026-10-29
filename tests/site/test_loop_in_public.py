# SPDX-License-Identifier: Apache-2.0
"""G6 maintaining G6, in public (owner, 2026-10-29): the home page, the FAQ and the README say that G6's own
repository is to be maintained by its self-improvement loop, as an open experiment. They say it as it is on launch
day: the loop is being built, a maintainer reviews and merges every change, and what it shows is reported whatever it
is. The design they link to is in the repository."""
from __future__ import annotations

import html
import re
from pathlib import Path

OSS = Path(__file__).resolve().parents[2]
DESIGN = "docs/superpowers/specs/2026-10-29-self-improvement-loop-design.md"
LINK = f"https://github.com/basicisabc1994/g6solver/blob/main/{DESIGN}"
SAID = (
    "building a loop in which G6 maintains its own GitHub repository",
    "A maintainer reviews and merges every change",
    "open experiment in self-improvement infrastructure for AI agents",
)


def _words(text: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())


def test_the_home_page_says_g6_is_to_maintain_its_own_repository_in_public(client):
    page = client.get("/").content.decode()
    section = page.split('id="in-public"', 1)[1].split("</section>", 1)[0]
    words = _words(section)
    for phrase in SAID:
        assert phrase in words
    assert "including if it doesn't help" in words
    assert LINK in section
    # the status rules hold here as everywhere: no "experimental" label, never "verified"
    assert not re.search(r"(?i)\bexperimental\b|\bverified\b", words)


def test_the_faq_and_the_readme_say_the_same_and_link_to_the_design():
    for name in ("web/content/faq.md", "site/docs/faq.md", "README.md"):
        text = " ".join((OSS / name).read_text(encoding="utf-8").split())
        for phrase in SAID:
            assert phrase in text, f"{name}: {phrase}"
        assert LINK in text, name


def test_the_design_the_pages_link_to_is_in_the_repository():
    assert (OSS / DESIGN).is_file()
