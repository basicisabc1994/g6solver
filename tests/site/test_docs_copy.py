# SPDX-License-Identifier: Apache-2.0
"""One story across the site, the docs and the README (owner, 2026-10-19): the docs FAQ is the site's FAQ, the counts
are the registry's, no study codename or abbreviation appears outside the Research page, and nothing refers to the
earlier G6."""
from __future__ import annotations

import html
import re
from pathlib import Path

from pages import catalogue

OSS = Path(__file__).resolve().parents[2]
DOCS = OSS / "site" / "docs"
# The site's FAQ is written for the site; the docs build links to the docs' own pages instead.
LINKS = {"(/#limits)": "(index.md)", "(research.md#theory)": "(research.md)", "(get-started.md)": "(install.md)"}
CODENAMES = re.compile(r"\bV\d+b?\b|\bV2-F1\b|\bECHA\b|\bCSIR\b|\bAHI\b")
EARLIER = re.compile(r"earlier,? (private )?G6|the earlier G6|old system")


def _docs_pages() -> list[Path]:
    return sorted(p for p in DOCS.rglob("*.md") if p.name != "research.md") + [DOCS / "llms.txt"]


def test_the_docs_faq_is_the_sites_faq():
    # the docs build has no switch: it carries the site's FAQ as the site shows it, so with pages.extensions.SHOW_PLANNED
    # off its words about extensions to come are left out (fails while the docs copy still says "one at a time")
    from pages import views

    site = views._planned((OSS / "web" / "content" / "faq.md").read_text(encoding="utf-8"))
    for old, new in LINKS.items():
        site = site.replace(old, new)
    docs = (DOCS / "faq.md").read_text(encoding="utf-8")
    docs = "\n".join(line for line in docs.splitlines() if not line.startswith("<!--")) + "\n"
    assert docs == site, "copy web/content/faq.md to site/docs/faq.md as the site renders it, with the links in LINKS"


def test_the_counts_are_the_registrys():
    core = len(catalogue.components())
    for path in (OSS / "README.md", DOCS / "index.md", DOCS / "llms.txt"):
        counts = [int(n) for n in re.findall(r"\b(\d+) components\b", path.read_text(encoding="utf-8"))]
        assert counts and set(counts) == {core}, (path.name, counts)


WORDS = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
         "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty")


def _bundles() -> tuple[int, int, int, list[str]]:
    """How many bundles there are, how many work today (none awaiting its certificate), how many of those need no
    model, and the titles of the ones awaiting their certificate (``g6.bundles``)."""
    from g6.bundles import awaiting_certificate, catalog

    bundles = list(catalog().values())
    working = [b for b in bundles if not awaiting_certificate(b)]
    return (len(bundles), len(working), sum(1 for b in working if not b.needs_model),
            [b.title for b in bundles if awaiting_certificate(b)])


def test_the_bundle_counts_are_the_registrys(client):
    # every hand-written bundle count read from g6.bundles: fails while faq.md and install.md say "Seven of the eleven"
    # and the docs bundles page says "Eleven bundles work today; seven of them fully offline", counting the compliance
    # gap check, which is built and awaiting its certificate
    total, working, offline, awaiting = _bundles()
    assert awaiting and working + len(awaiting) == total
    faq = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", client.get("/faq/").content.decode())).split())
    assert f"{offline} of the {working} worked examples that work today, need no model at all" in faq
    assert f"There are {total}: {working} work today" in faq
    assert all(title.lower() in faq.lower() for title in awaiting)
    for path in (OSS / "web" / "content" / "install.md", DOCS / "install.md"):
        text = " ".join(path.read_text(encoding="utf-8").split())
        assert f"Of the {working} worked examples that work today, {offline} need no AI model at all" in text, path
    for path in (OSS / "web" / "content" / "bundles.md", DOCS / "bundles" / "index.md"):
        text = path.read_text(encoding="utf-8")
        assert f"{WORDS[working].capitalize()} worked examples work today; {WORDS[offline]} of them fully offline." in text, path
    for path in (OSS / "README.md", DOCS / "llms.txt"):
        assert re.findall(r"\b(\d+) bundles\b", path.read_text(encoding="utf-8")) == [str(total)], path.name


def test_the_docs_bundles_page_is_the_sites():
    # one story: the docs build's bundles page is the site's, so a status fixed on one is fixed on both (fails while the
    # docs copy still lists the compliance gap check as working)
    site = (OSS / "web" / "content" / "bundles.md").read_text(encoding="utf-8")
    assert (DOCS / "bundles" / "index.md").read_text(encoding="utf-8") == site


def test_the_readme_names_every_core_component_once():
    readme = (OSS / "README.md").read_text(encoding="utf-8")
    listing = readme.split("## What is in it", 1)[1].split("- **The harness**", 1)[0]
    named = re.findall(r"`(\w+)`", listing)
    assert sorted(named) == sorted(r["name"] for r in catalogue.components())


def test_no_codename_abbreviation_or_earlier_g6_outside_research():
    for path in [OSS / "README.md", *_docs_pages()]:
        text = path.read_text(encoding="utf-8")
        assert not CODENAMES.findall(text), (path.name, CODENAMES.findall(text))
        assert not EARLIER.search(text), (path.name, EARLIER.search(text).group(0))
