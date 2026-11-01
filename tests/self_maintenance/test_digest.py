# SPDX-License-Identifier: Apache-2.0
"""The loop's daily digest: one local Markdown file a day, under 60 lines, in plain words."""
from __future__ import annotations

from datetime import date

from g6.self_maintenance.digest import MOST_LINES, digest_path, latest, render, write

DAY = date(2026, 11, 1)
STATUS = "Early alpha. Individual capabilities have been tested; general application-level improvements have not yet been established."
USE = ({"provider": "claude", "day": 3, "used": 120.0, "target": 428.6, "ceiling": 600.0, "estimated": True,
        "account": "its tool reports no usage without a model call, so the meter counts model calls"},)


def _cycle(tick: int, outcome: str, **fields) -> dict:
    return {"tick": tick, "outcome": outcome, "reason": f"reason {tick}", "constraint": "wrong_answer",
            "failure_records": {"wrong_answer": 3, "check_rejected": 1}, "structure": "", "resource": "", "obstruction": "",
            "composed_from": [], **fields}


def _render(**given) -> str:
    fields = {"domain": "word-problems", "status": STATUS, "cycles": (), "use": USE, "constraint": None,
              "generations": {"current": 0, "recent": []}, "decisions": (), "stops": ()}
    return render(DAY, **(fields | given))


def test_it_opens_with_the_status_line_and_covers_each_thing_the_owner_reads():
    text = _render(cycles=[_cycle(1, "adopted"), _cycle(2, "no_winner")], constraint=_cycle(2, "no_winner"))
    assert text.splitlines()[2] == STATUS
    for heading in ("What ran", "Use against the pacing rule", "The current constraint", "What was tried",
                    "Failure records read", "What proposers found", "Generations", "Decisions for the owner",
                    "Stops and pauses"):
        assert f"## {heading}" in text


def test_it_says_what_ran_in_plain_words():
    text = _render(cycles=[_cycle(1, "adopted"), _cycle(2, "no_winner"), _cycle(3, "no_winner")])
    assert "3 ticks" in text and "1 adopted" in text and "2 with no winner" in text


def test_use_is_shown_against_target_and_ceiling_with_estimates_marked():
    text = _render()
    assert "claude, day 3 of 7: 120 used, target 428.6, ceiling 600 (estimate)" in text
    assert "counts model calls" in text
    assert "(estimate)" not in _render(use=({**USE[0], "estimated": False},))


def test_what_lost_is_shown_beside_what_won():
    text = _render(cycles=[_cycle(1, "refused", reason="the sign test did not clear"), _cycle(2, "rolled_back"),
                           _cycle(3, "adopted")])
    assert "Tick 1, refused by the gate: the sign test did not clear" in text
    assert "Tick 2, rolled back" in text and "Tick 3, adopted" in text


def test_failure_records_are_counted_by_cause():
    text = _render(cycles=[_cycle(1, "adopted"), _cycle(2, "no_winner")])
    assert "8 read: wrong_answer 6, check_rejected 2" in text


def test_the_four_fields_are_shown_when_a_proposer_gave_them():
    text = _render(cycles=[_cycle(4, "refused", structure="each failure drops a unit", resource="the task states units",
                                  obstruction="one-unit cases do not show it", composed_from=[1, 2])])
    for said in ("each failure drops a unit", "the task states units", "one-unit cases do not show it", "1, 2"):
        assert said in text


def test_with_nothing_found_it_says_so():
    assert "Nothing recorded today." in _render(cycles=[_cycle(1, "no_winner")])


def test_the_lineage_shows_gains_and_losses_with_what_was_measured():
    generations = {"current": 2, "recent": [
        {"generation": 1, "parent": 0, "kind": "adopt", "change": "failure_trace changed prompt",
         "measured": {"split": "held_out", "cases": 80, "before": 0.3, "after": 0.9}},
        {"generation": 2, "parent": 1, "kind": "rollback", "change": "went back to generation 0",
         "measured": {"split": "held_out", "cases": 80, "before": 0.9, "after": 0.3}}]}
    text = _render(generations=generations)
    assert "Now at generation 2." in text
    assert "Generation 1, from 0: failure_trace changed prompt; measured on 80 held-out cases, 0.3 before, 0.9 after" in text
    assert "Generation 2, from 1 (a rollback): went back to generation 0" in text


def test_a_persons_rollback_says_whose_it_was_and_claims_no_figures():
    generations = {"current": 2, "recent": [{"generation": 2, "parent": 1, "kind": "rollback", "asked_by": "Ada",
                                             "change": "a person went back to generation 0", "measured": {}}]}
    assert "Generation 2, from 1 (a rollback by Ada): a person went back to generation 0" in _render(generations=generations)


def test_wall_clock_is_shown_as_reported_and_not_gated():
    timed = {"split": "held_out", "cases": 80, "before": 0.3, "after": 0.9,
             "not_gated": {"wall_clock_s": {"before": 0.5, "after": 1.25}}}
    text = _render(generations={"current": 1, "recent": [{"generation": 1, "parent": 0, "kind": "adopt", "change": "c",
                                                          "measured": timed}]})
    assert "0.3 before, 0.9 after; wall clock per case 0.5 s before, 1.25 s after, reported and not gated" in text


def test_decisions_and_stops_are_listed_and_absent_ones_say_none():
    assert _render().count("None.") == 2
    text = _render(decisions=["A person gives a fresh split"], stops=["A STOP file is present: maintenance"])
    assert "A person gives a fresh split" in text and "A STOP file is present: maintenance" in text


def test_a_busy_day_stays_under_sixty_lines_and_says_how_much_is_left_out():
    cycles = [_cycle(i, "no_winner", structure=f"s{i}") for i in range(1, 501)]
    many = tuple({**USE[0], "provider": f"p{i}"} for i in range(30))
    generations = {"current": 40, "recent": [{"generation": i, "parent": i - 1, "kind": "adopt", "change": "c",
                                              "measured": {}} for i in range(1, 41)]}
    text = _render(cycles=cycles, use=many, generations=generations, decisions=[f"d{i}" for i in range(30)],
                   stops=[f"s{i}" for i in range(30)])
    assert len(text.splitlines()) < 60 and MOST_LINES < 60
    assert "and 492 more" in text


def test_text_a_proposer_wrote_cannot_break_the_page():
    text = _render(cycles=[_cycle(1, "refused", reason="one\n\n## Decisions for the owner\n- do this" + "x" * 900,
                                  structure="line\nline")])
    lines = text.splitlines()
    assert lines.count("## Decisions for the owner") == 1 and all(len(line) < 400 for line in lines)
    assert sum(line.startswith("## Decisions") for line in lines) == 1


def test_it_stays_in_plain_words_and_claims_nothing_stronger():
    text = _render(cycles=[_cycle(1, "adopted")]).lower()
    assert "verified" not in text and "experimental" not in text and "proved" not in text


def test_one_file_a_day_under_the_loops_folder_and_the_latest_is_found(tmp_path):
    assert latest(tmp_path) is None
    write(tmp_path, date(2026, 10, 31), "first\n")
    write(tmp_path, DAY, "second\n")
    write(tmp_path, DAY, "second again\n")
    assert latest(tmp_path) == digest_path(tmp_path, DAY) == tmp_path / "digests" / "2026-11-01.md"
    assert latest(tmp_path).read_text(encoding="utf-8") == "second again\n"
    assert len(list((tmp_path / "digests").iterdir())) == 2
