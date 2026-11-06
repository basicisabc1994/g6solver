# SPDX-License-Identifier: Apache-2.0
"""/research/: the evidence page, rendered from pages.evidence. One thesis (owner, 2026-10-19): G6 engineers its own
gains, so every study run is shown as the same model without G6 and with it, labelled core harness or extensions.
Since 2026-11-03 it also carries the coding study, which found no significant uplift on unseen bugs, so the page says
where G6 gained and where it did not."""
from __future__ import annotations

import html
import json
import re
import shutil
from pathlib import Path

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


def _coding_file() -> dict:
    return json.loads(evidence.VALIDATION_DIR.joinpath("results", "coding", "coding_study.json")
                      .read_text(encoding="utf-8"))


def _table(section: str, table_id: str) -> list[list[str]]:
    """The rows of one table in the section, each as its cells' text (header cells included)."""
    table = section.split(f'id="{table_id}"', 1)[1].split("</table>", 1)[0]
    return [[_text(cell).strip() for cell in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S)]
            for row in re.findall(r"<tr>(.*?)</tr>", table, re.S)]


#: The story, in the order the brief of 2026-11-06 set: why, how it was measured, what it found, why, the limits of
#: self-improvement, G6's own code, cost.
STORY = ("How it was measured", "What it found", "Why, as the measurements show it",
         "The limits of self-improvement, and where checking helps", "Reviewing G6's own code", "Cost")


def test_the_coding_study_tells_one_story_in_plain_words(client):
    # owner, 2026-11-03: "update the case study on the website with your findings"; plain words, no arm's short name,
    # results a worked example, never more than the numbers say; why first, and no plans (planned studies not shown)
    page = _page(client)
    assert page.index('id="coding-study"') < page.index('id="the-meta-harness"') < page.index('id="reading"')
    section = _section(page, "coding-study")
    text = _text(section)
    assert "A worked example" in text and "Claude Code on real bugs, with a review loop and without" in text
    why = re.search(r'<p class="[^"]*\bwhy\b[^"]*">(.*?)</p>', section, re.S)
    assert why and section.index(why[0]) < section.index("<h3>")
    assert _text(why[1]).split() == ("We set out to measure whether G6 makes a coding agent right more often than "
                                     "Claude Code alone, on real bugs neither had seen. We registered every test and "
                                     "its bar before running it.").split()
    headings = [_text(h).strip() for h in re.findall(r"<h3[^>]*>(.*?)</h3>", section, re.S)]
    assert [h for h in headings if h in STORY] == list(STORY), headings
    for arm in evidence.CODING_ARMS.values():
        assert arm in text, arm
    assert not re.search(r"\b(?:[KLMV]\d+|arm [A-Z])\b", text)
    assert "No registered test on unseen bugs met the fixed rule." in text
    assert not re.search(r"(?i)\bplans?\b(?! written)|\bnext\b|\bwill\b|\bnot yet run\b", text.replace(
        "The study's plan, written before each run", "").replace("the plan's section", "").replace(
        "where the plan counted them", "").replace("where the plan gave one", ""))
    assert not re.search(r"(?i)\b(?:verified|proven?|proof of concept|experimental|breakthrough|superhuman)\b", text)
    assert not re.search(r"(?i)\bthe first\b|\bfirst (?:to|ever)\b", text)
    # withdrawn or overstated figures stay off the page: the 0.80 power forecast, replay 4's double count, branch names
    assert "0.80" not in text and "477" not in text and "claude/" not in section


def test_every_registered_comparison_on_unseen_bugs_is_in_the_table_by_bug(client):
    data = _coding_file()
    rows = _table(html.unescape(_section(_page(client), "coding-study")), "coding-tests")[1:]
    expected = [(key, arm) for key in ("stage_1", "stage_2", "stage_4") for arm in data[key]["judged"]]
    assert len(rows) == len(expected)
    for row, (key, arm) in zip(rows, expected):
        stage, passed = data[key], data[key]["passed"]["maintainers"]
        assert row[0].startswith(f"Stage {key[-1]}") and evidence.CODING_ARMS[arm].lower() in row[0], row
        assert row[2] == f"{passed['A']} → {passed[arm]} of {stage['runs']}", row
        by_bug = evidence._by_bug(stage, arm)
        if by_bug:
            p = evidence.sign_p(by_bug["better"], by_bug["worse"])       # the sign test over bugs, never over runs
            assert row[3] == f"{by_bug['better']} better, {by_bug['worse']} worse, {by_bug['same']} the same; p = {p:.2f}"
        else:
            assert row[3] == "not counted"
        assert row[5] == ("Met" if stage["rule_met"] else "Not met")


def test_tuned_scores_stand_beside_the_unseen_ones(client):
    data = _coding_file()
    rows = _table(html.unescape(_section(_page(client), "coding-study")), "coding-tuned")
    for row in rows[1:-1]:
        arm = next(a for a, label in evidence.CODING_ARMS.items() if row[0].startswith(label + ","))
        score = "maintainers" if "maintainers'" in row[0] else "amended"
        assert row[1:] == [f"{data[k]['passed'][score][arm]} of {data[k]['runs']}"
                           for k in ("training", "stage_1", "stage_2")], row
    assert rows[-1][0].startswith("The review loop's lead")


def test_the_coding_studys_numbers_on_the_page_are_its_files(client):
    data = _coding_file()
    text = _text(_section(_page(client), "coding-study"))
    pilot = data["pilot"]
    assert (f"It made right {pilot['made_right']} of {pilot['wrong_first']} "
            f"({100 * pilot['made_right'] / pilot['wrong_first']:.0f}%)") in text
    assert f"make right at least {pilot['bar_pct']}% of the wrong first patches" in text
    assert "The last pilot did not meet its bar." in text
    assert (f"In {pilot['named_and_kept']} of the last pilot's {pilot['wrong_first'] - pilot['made_right']} wrong "
            f"final patches") in text
    rows = [r for r in data["within_run"]["rows"] if r["determined"]]
    runs, right = sum(r["runs"] for r in rows), sum(r["first_right"] for r in rows)
    made, broke = sum(r["wrong_to_right"] for r in rows), sum(r["right_to_wrong"] for r in rows)
    assert f"passed {right} of {runs} ({100 * right / runs:.0f}%)" in text
    assert f"made right {made} of {runs - right} wrong first patches and broke {broke} of {right} right ones" in text
    rev = data["frozen_reviewer"]
    assert f"flagged {rev['false_alarms']} of {rev['right']} right patches and missed {rev['misses']} of" in text
    assert "a balanced accuracy of 53.6%" in text                          # (141/279 + 80/141) / 2, worked out
    wrong = data["wrong_runs"]
    assert f"Of {wrong['runs']} wrong runs in stages 1 and 2, {wrong['new_behaviour_only']} failed only" in text
    held = data["stage_3"]["held_out"]
    assert f"{held['wins']} to {held['losses']}, and G6's gate refused it" in text
    assert "with probability 0.25, and one of 200 with probability 0.52" in text
    spend = data["spend"]
    assert f"Paid model calls came to ${spend['usd']:.2f} for the whole study, within a limit of ${spend['limit_usd']}" in text
    assert f"${spend['design_review_usd']:.2f} of that paid for a review of the study's design" in text


def test_the_reviews_of_g6s_own_code_are_counted_by_the_page_not_typed_in(client):
    own = _coding_file()["own_code"]
    section = html.unescape(_section(_page(client), "coding-study"))
    rows = _table(section, "own-code")
    later = own["later"]
    assert [r[1] for r in rows[1:]] == [str(later[k]) for k in ("first_review_read", "earlier_repair", "last_repair",
                                                                "resolved")]
    text = _text(section)
    found, repair = sum(own["by_round"]), later["earlier_repair"] + later["last_repair"]
    assert f"{own['clean']} of the {own['loops']} loops ended with a review that found nothing" in text
    assert f"Together the reviews found {found} defects" in text
    assert f"So {repair} of {later['resolved']} ({100 * repair / later['resolved']:.0f}%) sat in lines a repair" in text
    assert "An AI review of AI code is a sample, not a census." in text
    template = (Path(evidence.__file__).parents[1] / "templates" / "pages" / "research.html").read_text(encoding="utf-8")
    for typed in (str(found), "168.06", "53.6", "0.25", str(later["resolved"])):
        assert typed not in template, typed                                  # worked out or read, never typed


def test_a_changed_count_in_a_copy_of_the_coding_studys_file_changes_the_page(client, tmp_path, monkeypatch):
    copy = tmp_path / "validation"
    shutil.copytree(evidence.VALIDATION_DIR, copy)
    path = copy / "results" / "coding" / "coding_study.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["stage_2"]["passed"]["maintainers"]["K1"] = 29
    data["stage_2"]["differ_from_A_maintainers"]["K1"]["pluggy-809"] = [0, 2]
    data["stage_2"]["differ_from_A_maintainers"]["K1"]["anyio-1424"] = [1, 2]
    data["own_code"]["by_round"][0] += 7
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(evidence, "VALIDATION_DIR", copy)
    section = html.unescape(_section(_page(client), "coding-study"))
    row = next(r for r in _table(section, "coding-tests") if r[0].startswith("Stage 2") and "scout" not in r[0])
    assert row[2] == "23 → 29 of 40" and row[3] == "4 better, 0 worse, 16 the same; p = 0.06"
    assert f"Together the reviews found {sum(data['own_code']['by_round'])} defects" in _text(section)


def test_the_page_claims_no_more_than_the_coding_study_shows(client):
    # the hero, the description, the reading's lede and its status: true of the local-model gains and of the coding
    # study's no significant uplift on unseen bugs alike
    page = _page(client)
    text = _text(page)
    for gone in ("G6 engineers its own gains", "the power of G6's self-engineering", "The experiments are complete",
                 "the numbers here are final"):
        assert gone not in text and gone not in page, gone
    hero = _text(page.split('class="rs-hero"', 1)[1].split("</section>", 1)[0])
    assert "Where G6 gained, and where it did not." in hero
    description = html.unescape(re.search(r'<meta name="description" content="([^"]*)"', page)[1])
    assert "no significant uplift over Claude Code alone on unseen bugs" in description
    reading = _text(_section(page, "reading"))
    assert "where a harness around the same model gained" in reading and "and where it did not" in reading
    status = reading.split("Status", 1)[1]
    assert "its numbers stand as measured" in status and "coding study's numbers stand as measured" in status
    assert "plan" not in status
    assert "The local-model study" in _text(_section(page, "the-meta-harness"))
    entry = html.unescape(_section(page, "studies").split(">Review loop on unseen coding bugs<", 1)[1]
                          .split("</li>", 1)[0])
    assert "Not shown" in entry and "Review loop: <b>" in entry and "G6: <b>" not in entry
    assert "p = 0.25 by bug" in entry and "p = 0.125" not in entry               # by bug, never by run


def test_the_page_reads_without_javascript(client):
    page = _page(client)
    executable = [m for m in re.finditer(r"<script\b([^>]*)>", page) if "src=" not in m[1] and "ld+json" not in m[1]]
    assert executable == []
    assert '<script defer src="/static/js/research.js"></script>' in page
    # bars carry their width in the HTML, so they show without the script
    assert re.search(r'class="rs-fill" style="--v: \d+(\.\d)?%"', page)
