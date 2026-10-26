# SPDX-License-Identifier: Apache-2.0
"""/metaharness/: how the meta-harness works, told without study codenames, with its Python coloured."""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

from pages import evidence, views

CSS = Path(__file__).resolve().parents[2] / "web" / "static" / "css" / "content.css"


def _page(client) -> str:
    return client.get("/metaharness/").content.decode()


def test_the_page_names_no_study_by_its_code_and_has_no_evidence_box(client):
    # owner, 2026-10-19: "remove all references to the 'v6' study"; "cut the 'evidence so far' section"
    page = _page(client)
    assert not re.search(r"\bV6b?\b", page) and "Evidence so far" not in page


def test_front_matter_is_never_shown_as_text(client, tmp_path, monkeypatch):
    # owner, 2026-10-19: "why does the page say 'title: ...' above the title?"
    assert "title:" not in _page(client)
    (tmp_path / "x.md").write_text("---\ntitle: Hidden\n---\n\n# Shown\n\nBody.\n", encoding="utf-8")
    monkeypatch.setattr(views, "CONTENT_DIR", tmp_path)
    body, title = views.render_markdown("x.md")
    assert "title:" not in body and "<hr" not in body and title == "Shown"


def _board(rnd: str) -> dict:
    return json.loads((evidence.VALIDATION_DIR / "rounds" / rnd / "scoreboard.json").read_text(encoding="utf-8"))


def test_the_round_is_shown_in_full_against_its_scoreboard(client):
    # owner, 2026-10-19: say these come from Claude Code working with G6, and how quickly and how far G6 lifted the
    # model; 2026-10-20: why were these higher than Research? Only the wins were listed. Every task set is shown now.
    page = html.unescape(_page(client))
    assert "Claude Code" in page
    board = _board("round-02")
    assert board["reflector"] in page and board["model"] in page
    for wf, arms in board["workflows"].items():
        row = page.split(f">{evidence.WORKFLOWS[wf]}<", 1)[1].split("</tr>", 1)[0]
        for arm in ("A", "D"):
            assert f"{arms[arm]['success']} of {arms[arm]['n']}" in row, (wf, arm)
        run = board["tuning"][f"{wf}-D"]
        assert (f"approved in {run['minutes']} minutes" if run["outcome"] == "activated"
                else f"refused after {run['minutes']} minutes") in row, wf
    pooled = page.split(">All five<", 1)[1].split("</tr>", 1)[0]
    assert f"{board['pooled']['A']['success']} of {board['pooled']['A']['n']}" in pooled
    assert f"{board['pooled']['D']['success']} of {board['pooled']['D']['n']}" in pooled
    # owner, 2026-10-20: a worked example; results vary with the use case and the data available
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))
    assert "worked example" in text and "results will vary with the task and the data you have" in text
    assert 'href="/research/#reading"' in page


def test_the_registered_study_is_left_to_research_and_the_first_run_is_quoted_from_its_own_files(client):
    # owner, 2026-10-21: study results live on the Research page, as a worked example; other pages point there
    page = _page(client)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(page)))
    facts = evidence.headline_facts()
    assert f"{facts['total']['A']} to {facts['total']['D']} of {facts['n']} overall" not in text
    assert 'href="/research/#the-meta-harness"' in page
    assert not re.search(r"\b[Rr]ound \d", text)                         # runs are described, never numbered
    first = _board("round-01")
    for wf in ("gsm8k", "banking77"):
        arms, run = first["workflows"][wf], first["tuning"][f"{wf}-D"]
        assert run["outcome"] == "activated"
        assert f"({arms['A']['success']} to {arms['D']['success']}, in {run['minutes']} minutes)" in text, wf


def test_round_three_is_told_from_its_own_records(client):
    # owner, 2026-10-20: the last round's result, on every page that reports the rounds
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(_page(client))))
    board = _board("round-03")
    flows, run = board["workflows"], board["tuning"]["gsm8k-D"]
    assert run["outcome"] == "activated"
    assert (f"GSM8K from {flows['gsm8k']['A']['success']} to {flows['gsm8k']['D']['success']} of "
            f"{flows['gsm8k']['D']['n']} in {run['minutes']} minutes") in text
    assert f"MASSIVE from {flows['massive']['A']['success']} to {flows['massive']['G']['success']}" in text
    both = evidence.both_modes()
    worst = both["worst"]
    assert f"no change approved on any of the {both['datasets']} task sets" in text
    assert f"({worst['components']} against {worst['alone']})" in text and "one layer can mask another" in text


def test_each_approved_change_is_described_as_the_change_its_round_made():
    """The 'What G6 changed' column, against each approved profile (no change is described that was not made)."""
    board = _board("round-02")
    for wf, run in board["tuning"].items():
        wf, _, arm = wf.rpartition("-")
        if arm != "D" or run["outcome"] != "activated":
            continue
        head = json.loads((evidence.VALIDATION_DIR / "rounds" / "round-02" / "tuning" / f"{wf}-D.json").read_text(
            encoding="utf-8"))["head_profile"]
        if wf == "gsm8k":
            assert "step by step" in head["prompt"]
        else:                                             # banking77 and MASSIVE: eight labelled examples
            assert head["exemplars"] == 8, wf


def test_python_on_the_markdown_pages_is_coloured(client):
    page = _page(client)
    assert '<div class="highlight">' in page and '<span class="kn">from</span>' in page
    css = CSS.read_text(encoding="utf-8")
    for token in (".highlight .k", ".highlight .s", ".highlight .c", ".highlight .nf"):
        assert token in css, token
