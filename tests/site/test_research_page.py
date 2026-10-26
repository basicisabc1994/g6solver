# SPDX-License-Identifier: Apache-2.0
"""/research/: the evidence page, rendered from pages.evidence. One thesis (owner, 2026-10-19): G6 engineers its own
gains, so every study run is shown as the same model without G6 and with it, labelled core harness or extensions."""
from __future__ import annotations

import html
import json
import re

from pages import evidence


def _page(client) -> str:
    return client.get("/research/").content.decode()


def _section(page: str, section_id: str) -> str:
    return page.split(f'id="{section_id}"', 1)[1].split("</section>", 1)[0]


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)))


def test_the_counts_include_every_study_run_split_by_what_it_covers(client):
    counts = _text(_section(_page(client), "counts"))
    rows = evidence.ledger()
    assert f"Studies run {len(rows)}" in counts
    for scope, label in (("core", "On the core harness"), ("core + extensions", "Core with extensions"),
                         ("extensions", "On extensions")):
        assert f"{label} {sum(r.scope == scope for r in rows)}" in counts, scope
    assert f"Showed a gain {sum(r.outcome == 'gain' for r in rows)}" in counts


def test_the_headline_shows_the_model_alone_beside_each_g6_variant(client):
    headline = _section(_page(client), "the-meta-harness")      # the old anchor other pages link to
    _, groups = evidence.headline(research=True)
    assert "llama3.1:8b" in headline
    for group in groups:
        figure = headline.split(f">{group.label}<", 1)[1].split("</figure>", 1)[0]
        for bar in group.bars:
            assert f"{bar.label}" in html.unescape(figure) and f"{bar.value} of {bar.n}" in figure, (group.label, bar.arm)


def test_the_winning_approach_follows_the_tasks_structure(client):
    # owner, 2026-10-20: highlight the winning approach; read prebuilt components beating self-tuning on MASSIVE and
    # MBPP against the Swiss cheese failure model and the datasets' structure; bootstrapping a small model and
    # distilling a larger model's judgement through the harness is G6's core idea
    page = _page(client)
    headline = html.unescape(_text(_section(page, "the-meta-harness")))
    facts = evidence.headline_facts()
    for w in evidence.winners():
        row = html.unescape(_text(page.split(f'<th scope="row">{w["dataset"]}</th>', 1)[1].split("</tr>", 1)[0]))
        assert w["approach"] in row and w["change"] in row and f"{w['alone']} → {w['value']} of {w['n']}" in row
        assert f"{w['wins']} gained, {w['losses']} lost" in row
    for name, wins, losses in facts["components_vs_tuning"]:
        assert f"{name} ({wins} gained, {losses} lost)" in headline, name
    assert "The winning approach follows the task's structure" in headline and "Swiss cheese" in headline
    assert "Bootstrapping a small model through its harness" in headline and "no weights changed" in headline
    assert "distilled into a smaller model's harness" in headline and "chosen after the fact" in headline
    assert f"G6 solved {facts['best_total']} of {facts['n']}" in headline
    for group in evidence.headline()[1]:                               # the big number is the winning option
        assert f"Winning option: {group.best.label}" in headline


def test_round_three_reads_the_two_modes_as_alternatives_not_a_stack(client):
    # owner, 2026-10-20: the last round's result goes into the finding, with its interpretation
    page = _page(client)
    found, = [html.unescape(_text(page.split('id="winning-approach"', 1)[1].split("</div>", 1)[0]))]
    both = evidence.both_modes()
    worst = both["worst"]
    assert "they did not stack" in found and f"no change approved on any of its {both['datasets']} task sets" in found
    assert (f"on {worst['dataset']} every component on did worse than the model alone ({worst['components']} against "
            f"{worst['alone']} of {worst['n']}; {worst['lost']} tasks lost, {worst['gained']} gained)") in found
    assert "one layer can mask another" in found
    dev = _text(_section(page, "development"))
    assert "The programme closed with round 3" in dev and "closes with" not in dev


def test_the_component_matrix_shows_every_measured_change_gains_and_losses_alike(client):
    table = _section(_page(client), "components")
    matrix = evidence.component_sweep()
    for row in matrix["rows"]:
        line = html.unescape(table.split(f">{html.escape(row['label'])}<", 1)[1].split("</tr>", 1)[0])
        for cell in row["cells"]:
            if cell is not None:
                assert (f"+{cell}" if cell > 0 else f"{cell}".replace("-", "−")) in line, (row["label"], cell)


def test_every_study_shows_without_and_with_g6_and_what_it_covers(client):
    studies = html.unescape(_section(_page(client), "studies"))
    for row in evidence.ledger():
        item = studies.split(f">{row.name}<", 1)[1].split("</li>", 1)[0]
        assert row.without in item and row.with_g6 in item and row.note in item, row.name
    for heading in ("The core harness", "Core with extensions", "Extensions"):
        assert heading in studies


def test_no_study_is_named_by_its_code(client):
    # owner, 2026-10-19: "no one cares about what version this is"
    assert not re.search(r"\bV\d+b?\b", _text(_page(client)))


def test_each_round_shows_its_scoreboard_in_a_panel_labelled_a_worked_example(client):
    # owner, 2026-10-20: the "development, not a validation" section is "the G6 metaharness engineering itself as a
    # proof of concept", a term the site now calls a worked example
    panel = _section(_page(client), "development")
    assert "A worked example" in panel and "The G6 meta-harness engineering itself" in panel
    assert "not a validation" not in panel
    paths = sorted((evidence.VALIDATION_DIR / "rounds").glob("round-*/scoreboard.json"))
    assert paths and len(evidence.rounds()) == len(paths)
    for path in paths:
        board = json.loads(path.read_text(encoding="utf-8"))
        tile = panel.split(f'id="{path.parent.name}"', 1)[1].split("</article>", 1)[0]
        for key, arms in board["workflows"].items():
            figure = tile.split(f">{evidence.WORKFLOWS[key]}<", 1)[1].split("</figure>", 1)[0]
            for arm in arms:                              # each round's own arms (round 3: G and DG, no H or F)
                assert f"{arms[arm]['success']} of {arms[arm]['n']}" in figure, (path.parent.name, key, arm)
        note = next(r.note for r in evidence.rounds() if r.id == path.parent.name)
        assert html.unescape(note) in html.unescape(tile)


def test_the_four_problems_are_answered_not_left_open(client):
    theory = _text(_section(_page(client), "theory"))
    assert "Open problems" not in theory
    for problem in ("learning signals", "cannot influence", "unseen task families", "held-out examples"):
        assert problem in theory, problem


def test_the_theory_is_a_table_of_documents_with_their_key_points_and_no_abbreviations(client):
    # owner, 2026-10-19: no abbreviations; a table with a helpful summary of key points; add the two theory pages
    # derived from the benchmark runs
    from pathlib import Path

    from pages import theory

    section = _section(_page(client), "theory")
    text = _text(section)
    assert not re.search(r"\b(?:CSIR|ECHA|AHI|VLE)\b", text)
    table = section.split('<table class="rs-theory">', 1)[1].split("</table>", 1)[0]   # the section's other table: the problems
    assert len(theory.DOCUMENTS) == 6 and table.count("<tr") == len(theory.DOCUMENTS) + 1     # + the header row
    # docs/theory beside oss in the source repository; the export copies it to its own docs/theory (export/ALLOWLIST)
    here = Path(__file__).resolve().parents[2] / "docs" / "theory"
    folder = here if here.is_dir() else Path(__file__).resolve().parents[3] / "docs" / "theory"
    for doc in theory.DOCUMENTS:
        assert (folder / doc["file"]).is_file(), doc["file"]
        row = html.unescape(section.split(doc["file"], 1)[1].split("</tr>", 1)[0])
        for part in (doc["title"], doc["what"], doc["basis"], *doc["points"]):
            assert part in row, (doc["file"], part)


def test_the_results_are_read_as_a_worked_example_with_a_scientific_interpretation(client):
    # owner, 2026-10-20: freeze after round 3; "a correct scientific interpretation"; "a proof of concept exemplar
    # application which is intended to show the power of the system and results will vary depending on the individual
    # use case/data availability"
    page = _page(client)
    reading = _text(_section(page, "reading"))
    for row in ("What they are", "What they show", "What they do not show", "Why results vary", "Status"):
        assert row in reading, row
    facts = evidence.headline_facts()
    name, alone, tuned = facts["largest"]
    assert f"{name} went from {alone} to {tuned} of {facts['per_dataset']}" in reading
    assert f"{facts['total']['D']} against {facts['total']['F']} of {facts['n']}" in reading   # random search, plainly
    assert "worked example" in reading and "30 or more held-out examples" in reading
    assert "programme closed with development round 3" in reading and "alternatives, not a stack" in reading
    hero = page.split('class="rs-hero"', 1)[1].split("</section>", 1)[0]
    assert "worked example" in _text(hero) and "results will vary" in _text(hero)
    assert "proof of concept" not in _text(page).lower() and "proof-of-concept" not in _text(page).lower()


def test_the_development_section_is_closed_and_lists_no_study_that_never_ran(client):
    # owner, 2026-10-20: remove the "Registered, not run" section; the page shows only what ran
    page = _page(client)
    dev = _text(_section(page, "development"))
    assert "The G6 meta-harness engineering itself" in dev and "in progress" not in dev.split("Development round")[0]
    assert "Registered, not run" not in page and "running now" not in dev and "Next" not in dev
    for study in (s for s in evidence.studies() if s.kind == "planned"):
        assert study.title not in _text(page), study.id
    assert not re.search(r"\b(?:ECHA|VLE)\b", dev)


def test_the_page_reads_without_javascript(client):
    page = _page(client)
    executable = [m for m in re.finditer(r"<script\b([^>]*)>", page) if "src=" not in m[1] and "ld+json" not in m[1]]
    assert executable == []
    assert '<script defer src="/static/js/research.js"></script>' in page
    # bars carry their width in the HTML, so they show without the script
    assert re.search(r'class="rs-fill" style="--v: \d+(\.\d)?%"', page)
