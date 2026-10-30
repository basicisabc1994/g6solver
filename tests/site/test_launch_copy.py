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


#: The launch review's I1 (the owner's ruling): patch review runs a patch only inside the boundary.
PATCH_REVIEW_BOUNDARY = ("Patch review needs an operating-system boundary (Linux with bubblewrap, Windows after the "
                         "one-time access grant): allowing unconfined runs does not make it run, and on macOS it "
                         "refuses.")
#: The launch review's I5: where the patch's own code runs, and what G6 does not catch.
OWN_PROCESS = ("The patch's own code runs in pytest's own process, in the repository's own environment (its .venv, "
               "which the agent can change): a patch written to defeat the checks can make them report a pass, and G6 "
               "does not catch that.")


def _plain(text: str) -> str:
    return " ".join(text.replace("`", "").split())


def test_patch_review_says_it_needs_the_boundary_whatever_the_person_allowed(client):
    for name in ("README.md", "docs/releases/launch.md", "docs/AI_FOR_AI_WORKFLOW.md"):
        assert PATCH_REVIEW_BOUNDARY in _plain((OSS / name).read_text(encoding="utf-8")), name
    started = client.get("/get-started/").content.decode()
    install = flat(started.split('id="from-agent"', 1)[1].split("</li>", 1)[0])
    assert PATCH_REVIEW_BOUNDARY in install
    notes = _plain((OSS / "docs/releases/launch.md").read_text(encoding="utf-8"))
    assert "G6 refuses to run a patch or a repository's tests there unless the person allows it once" not in notes


def test_patch_review_says_the_patch_runs_in_pytests_own_process():
    readme = _plain((OSS / "README.md").read_text(encoding="utf-8"))
    review = readme.split("## The first review", 1)[1].split(" ## ", 1)[0]
    assert OWN_PROCESS in review
    for name in ("docs/releases/launch.md", "docs/AI_FOR_AI_WORKFLOW.md"):
        assert OWN_PROCESS in _plain((OSS / name).read_text(encoding="utf-8")), name
    limits = _plain((OSS / "docs/releases/launch.md").read_text(encoding="utf-8")).split("## Limits", 1)[1]
    assert OWN_PROCESS in limits
    guide = _plain((OSS / "docs/AI_FOR_AI_WORKFLOW.md").read_text(encoding="utf-8"))
    section = guide.split("## Reference application: review a Python patch", 1)[1].split(" ## ", 1)[0]
    assert OWN_PROCESS in section and PATCH_REVIEW_BOUNDARY in section


#: The owner's pilot (2026-10-30): a workflow's files live in the project's .g6 folder, untracked unless ignored.
IGNORE_G6 = ("A workflow's files live in the project's .g6/ folder: add .g6/ to the repository's .gitignore, or a later "
             "patch review there counts them as part of the change.")


def test_the_workflow_docs_say_to_ignore_g6s_project_folder():
    for name in ("README.md", "docs/AI_FOR_AI_WORKFLOW.md"):
        assert IGNORE_G6 in _plain((OSS / name).read_text(encoding="utf-8")), name


#: L1 (the owner's decision): G6's own environment, made with --copies, and why.
OWN_ENVIRONMENT = "python -m venv --copies"
WHY_COPIES = ("on Linux, G6 cannot yet tell its own Python from a project environment linked to the same one, and "
              "refuses to review then")


def test_every_install_makes_g6s_own_environment_with_copies_first_and_says_why(client):
    for name in ("README.md", "site/docs/index.md", "web/content/guide.md", "site/docs/guide/index.md",
                 "docs/AI_FOR_AI_WORKFLOW.md", "docs/releases/launch.md"):
        text = _plain((OSS / name).read_text(encoding="utf-8"))
        assert OWN_ENVIRONMENT in text and WHY_COPIES in text, name
        assert text.index(OWN_ENVIRONMENT) < text.index('pip install ".[all]"'), name   # made, then installed into
    page = flat(client.get("/get-started/").content.decode())
    assert OWN_ENVIRONMENT in page and WHY_COPIES in page, page
    assert page.index(OWN_ENVIRONMENT) < page.index('pip install ".[all]"')
    assert "source .venv/bin/activate" in page and ".venv\\Scripts\\Activate.ps1" in page   # POSIX and PowerShell


#: L1's line on the home page's quick start, directly before its two commands.
FIRST_OWN_ENVIRONMENT = ("First make G6's own environment with python -m venv --copies .venv and activate it. Get "
                         "started shows the commands for each system.")


def test_the_home_pages_quick_start_first_makes_g6s_own_environment_and_links_get_started(client):
    page = client.get("/").content.decode()
    band = page.split('aria-labelledby="start"', 1)[1].split("</section>", 1)[0]
    text = flat(band)
    assert FIRST_OWN_ENVIRONMENT in text, text
    assert text.index(FIRST_OWN_ENVIRONMENT) < text.index("git clone") < text.index('pip install ".[all]" && g6 setup')
    before = band.split('<div class="cmd">', 1)[0]
    assert 'href="/get-started/"' in before   # the line links to Get started


def test_the_release_notes_say_what_ships_what_does_not_and_name_the_version_the_owner_set():
    import g6
    notes = shipped()["docs/releases/launch.md"]
    assert notes.startswith(f"# G6 {g6.__version__}: ")   # the owner set it, 2026-10-29
    assert "VERSION-TO-BE-SET" not in notes
    for part in ("## What ships", "## What does not ship", "## Limits", "G6's own model calls", "V9 and V10",
                 "plugins and hooks", "Venture", "continuous integration check", "macOS", "Python first"):
        assert part in notes, part
