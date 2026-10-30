# SPDX-License-Identifier: Apache-2.0
"""Worked examples (owner, 2026-10-30): the site calls a bundle a worked example, because it "doesn't package the whole
system": it is a restricted subset of G6 applied to one problem. The page links to each one's builds, and the FAQ says
how someone who does not program uses G6 through Claude Code or Codex in plain words."""
from __future__ import annotations

import html
import re

RELEASES = "https://github.com/basicisabc1994/g6solver/releases/latest/download/"
PLATFORMS = ("windows-amd64.exe", "macos-arm64.zip", "linux-x86_64.zip")


def _text(page: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).split())


def _main(client, path: str) -> str:
    return client.get(path).content.decode().split('<main id="main-content">', 1)[1]


def test_the_page_is_called_worked_examples_and_says_they_are_a_subset_of_g6_for_one_problem(client):
    main = _main(client, "/bundles/")
    assert re.search(r"<h1\b[^>]*>\s*Worked examples\s*</h1>", main)
    text = _text(main)
    assert "not G6 packaged whole" in text and "restricted subset of G6's components" in text
    assert "called a bundle" in text                                      # the name the code and the releases use


def test_the_page_links_to_every_worked_examples_builds(client):
    from g6.bundles import catalog

    main = _main(client, "/bundles/")
    for bundle_id in catalog():
        for platform in PLATFORMS:
            assert f'href="{RELEASES}g6-{bundle_id}-{platform}"' in main, (bundle_id, platform)
    assert "first release" not in _text(main)                             # v0.1.0 shipped the downloads


def test_the_site_names_them_worked_examples_not_bundles(client):
    footer = client.get("/").content.decode().split("<footer", 1)[1]
    assert ">Worked examples</a>" in footer and ">Bundles</a>" not in footer
    docs = _text(_main(client, "/docs/"))
    assert "Worked examples" in docs and "bundle" not in docs.lower()
    faq = _text(_main(client, "/faq/"))
    assert "What is a worked example?" in faq and "What is a bundle?" not in faq


def test_a_person_who_does_not_program_is_told_how_to_use_g6_from_claude_code_or_codex(client):
    main = _main(client, "/faq/")
    answer = main.split("Do I need to be a programmer?", 1)[1].split("<h3", 1)[0]
    text = _text(answer)
    assert "Claude Code" in text and "Codex" in text and "plain words" in text
    assert "Review the change with G6 before you tell me it's done" in text
    assert 'href="/get-started/"' in answer and 'href="/bundles/"' in answer
    assert "first release" not in text
