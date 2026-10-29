# SPDX-License-Identifier: Apache-2.0
"""The minimal launch's copy (owner, 2026-10-29): the status line word for word on the pages and files that ship, the
whole first journey for Claude Code and Codex on the Get started page and in the README, and no claim that G6 finds
more defects or saves time. The owner keeps the "engineers itself" wording on the About page and site-wide, in the
title suffix and descriptions too, so nothing here checks for it."""
from __future__ import annotations

import html
import re
from pathlib import Path

import pytest

from g6.cli import setup

OSS = Path(__file__).resolve().parents[2]
STATUS = ("Early alpha. Individual capabilities have been tested; general application-level improvements have not "
          "yet been established.")
PAGES = ("/get-started/", "/faq/", "/security/", "/developers/")
#: The first journey, as setup itself says it: the command for each client, the existing entry, the review.
JOURNEY = ("g6 setup --client claude --allow patch_review.review", "--client codex", "claude mcp get g6",
           "codex mcp get g6", *setup.REMOVE.values(), "g6_review_patch", "metaharness_job", "assessment.report",
           "three rounds", "Merging stays your decision.")
CLAIMS = re.compile(r"catch(?:es)? more|more defects|fewer (?:bugs|defects)|saves? (?:you )?time|faster",
                    re.IGNORECASE)


def flat(page: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).split())


def shipped() -> dict[str, str]:
    return {name: " ".join((OSS / name).read_text(encoding="utf-8").split())
            for name in ("README.md", "docs/releases/launch.md")}


@pytest.mark.parametrize("path", PAGES)
def test_each_page_that_ships_carries_the_status_line_word_for_word(client, path):
    assert STATUS in flat(client.get(path).content.decode()), path


def test_the_readme_and_the_release_notes_carry_it_too():
    for name, text in shipped().items():
        assert STATUS in text, name


def test_get_started_shows_the_whole_first_journey_for_both_clients_in_order(client):
    page = flat(client.get("/get-started/").content.decode())
    for part in (*JOURNEY, "G6_MCP_SENSITIVE=patch_review.review"):
        assert part in page, part
    install, setup_step, review = (page.index(word) for word in ("git clone", "g6 setup --client claude",
                                                                  "g6_review_patch"))
    assert install < setup_step < review


def test_the_readme_shows_the_same_journey():
    readme = shipped()["README.md"]
    for part in JOURNEY:
        assert part in readme, part


#: What G6 runs a repository's tests with (``repo_tests.interpreter``), said where the first review is.
INTERPRETER = ("G6 runs the tests with the repository's own .venv or venv; without one, with the Python running G6, "
               "which then needs pytest and the project's dependencies.")
#: The places a founder follows as the first journey; none may skip g6 setup or install less than .[all].
FIRST_JOURNEY = ("README.md", "site/docs/index.md", "site/docs/install.md", "web/content/install.md",
                 "web/content/developers.md", "site/docs/developers.md", "web/content/guide.md",
                 "site/docs/guide/index.md", "web/templates/pages/landing.html")


def test_the_first_review_says_which_python_runs_the_tests(client):
    readme = shipped()["README.md"]
    review = readme.split("## The first review", 1)[1].split("\n## ", 1)[0]
    assert INTERPRETER in review.replace("`", "") and "G6_REPO_PYTHON" in review
    assert "(docs/AI_FOR_AI_WORKFLOW.md#reference-application-review-a-python-patch)" in review
    page = client.get("/get-started/").content.decode()
    step = flat(page.split(">The first review</h3>", 1)[1].split('<h3 class="h3">', 1)[0])     # to the next step
    step = re.sub(r" ([;,.])(?=\s|$)", r"\1", step)       # a tag taken out before punctuation leaves a space
    assert INTERPRETER in step and "G6_REPO_PYTHON" in step and "docs/AI_FOR_AI_WORKFLOW.md" in step


def test_every_first_journey_installs_all_and_runs_setup_never_a_bare_mcp_add(client):
    for name in FIRST_JOURNEY:
        text = (OSS / name).read_text(encoding="utf-8")
        for bare in (".[mcp]", "claude mcp add g6", "codex mcp add g6"):
            assert bare not in text, (name, bare)
    page = client.get("/get-started/").content.decode()
    journey = page.split('id="from-agent"', 1)[1].split(">Another agent or client</h3>", 1)[0]
    assert 'pip install ".[all]"' in journey and ".[mcp]" not in journey and "mcp add" not in journey


def test_the_launch_copy_claims_no_more_defects_found_and_no_time_saved(client):
    texts = {**shipped(), **{path: flat(client.get(path).content.decode()) for path in PAGES}}
    for where, text in texts.items():
        assert not CLAIMS.search(text), (where, CLAIMS.search(text).group(0))


def test_the_release_notes_say_what_ships_what_does_not_and_leave_the_version_to_the_owner():
    notes = shipped()["docs/releases/launch.md"]
    assert notes.startswith("# G6 VERSION-TO-BE-SET-BY-THE-OWNER")
    for part in ("## What ships", "## What does not ship", "## Limits", "G6's own model calls", "V9 and V10",
                 "plugins and hooks", "Venture", "continuous integration check", "macOS", "Python first"):
        assert part in notes, part
