# SPDX-License-Identifier: Apache-2.0
"""The scoring instruments (``g6.jobs._care.instruments``): the PHQ-9 and GAD-7 as their own forms print them, scored
as the forms score them and nothing more ("score computed": no band, no risk level, no diagnosis); any non-zero PHQ-9
item 9 goes to a person whatever the total; a missing or out-of-range answer is refused, never defaulted; and every
instrument whose licence is not settled is refused, with no item text shipped (DUTY_OF_CARE-06, JOB_PSYCHOLOGIST-01,
handoff section 4)."""
from __future__ import annotations

import json

import pytest

from g6.jobs._care import instruments

TERMS = "No permission required to reproduce, translate, display or distribute."
REFUSED = ("pcl5", "audit", "dass21", "k10", "must", "barthel")
#: Words a band, a risk level or a diagnosis would bring into a result.
STRATIFIED = ("band", "severity", "mild", "moderate", "severe", "minimal", "risk", "diagnos", "depression level",
              "disorder")


def test_the_phq9_ships_its_items_options_and_scoring_as_its_form_prints_them() -> None:
    phq9 = instruments.get("phq9")
    assert phq9["licence"]["state"] == "settled" and phq9["licence"]["terms"] == TERMS
    assert phq9["stem"] == ("Over the last 2 weeks, how often have you been bothered by any of the following "
                            "problems?")
    assert [o["label"] for o in phq9["options"]] == ["Not at all", "Several days", "More than half the days",
                                                     "Nearly every day"]
    assert [o["value"] for o in phq9["options"]] == [0, 1, 2, 3]
    assert len(phq9["items"]) == 9 and [i["number"] for i in phq9["items"]] == list(range(1, 10))
    assert phq9["items"][0]["text"] == "Little interest or pleasure in doing things"
    assert phq9["items"][5]["text"] == ("Feeling bad about yourself — or that you are a failure or have let yourself "
                                        "or your family down")
    assert phq9["scoring"]["rule"] == "sum" and (phq9["scoring"]["minimum"], phq9["scoring"]["maximum"]) == (0, 27)
    assert "bands" not in phq9 and "the form prints no severity bands" in phq9["scoring"]["note"]


def test_the_gad7_ships_as_its_form_prints_it_with_the_same_licence_line() -> None:
    gad7 = instruments.get("gad7")
    assert gad7["licence"]["state"] == "settled" and gad7["licence"]["terms"] == TERMS
    assert [i["text"] for i in gad7["items"]] == [
        "Feeling nervous, anxious or on edge", "Not being able to stop or control worrying",
        "Worrying too much about different things", "Trouble relaxing",
        "Being so restless that it is hard to sit still", "Becoming easily annoyed or irritable",
        "Feeling afraid as if something awful might happen"]
    assert (gad7["scoring"]["minimum"], gad7["scoring"]["maximum"]) == (0, 21)


def test_a_score_is_the_sum_of_the_item_scores_and_nothing_else() -> None:
    # Known value worked by hand from the form's own coding line (the form prints no worked example): 1+2+0+3+1+0+2+1+0.
    outcome = instruments.score("phq9", [1, 2, 0, 3, 1, 0, 2, 1, 0])
    assert outcome.kind == "score_computed" and outcome.total == 10
    assert outcome.item_scores == (1, 2, 0, 3, 1, 0, 2, 1, 0)
    assert instruments.score("gad7", [3] * 7).total == 21 and instruments.score("gad7", [0] * 7).total == 0
    text = json.dumps(outcome.as_dict()).lower()
    assert [w for w in STRATIFIED if w in text] == []


@pytest.mark.parametrize("answers", [[0] * 8 + [1], [3] * 8 + [2], [0] * 8 + [3]])
def test_a_non_zero_phq9_item_9_goes_to_a_person_whatever_the_total(answers: list[int]) -> None:
    # Removing the item-9 check in instruments.score makes this fail: total 1 would read as a computed score.
    outcome = instruments.score("phq9", answers)
    assert outcome.kind == "crisis" and outcome.total is None and outcome.item_scores == ()
    assert outcome.item == 9 and outcome.code == instruments.CRISIS


def test_item_9_goes_to_a_person_even_when_other_answers_are_missing_or_out_of_range() -> None:
    assert instruments.score("phq9", [None] * 8 + [1]).kind == "crisis"
    assert instruments.score("phq9", [7] * 8 + [1]).kind == "crisis"
    assert instruments.score("phq9", [0] * 8 + [9]).kind == "crisis"  # any non-zero answer, even one off the form


@pytest.mark.parametrize("answers", [[], [0] * 8, [0, 0, None, 0, 0, 0, 0, 0, 0], None])
def test_a_missing_answer_is_refused_never_defaulted(answers: list | None) -> None:
    # Treating a missing answer as 0 (the archive's default) would give a total here.
    outcome = instruments.score("phq9", answers)
    assert outcome.kind == "refused" and outcome.code == instruments.MISSING and outcome.total is None


@pytest.mark.parametrize("answers", [[0, 0, 4, 0, 0, 0, 0], [0, -1, 0, 0, 0, 0, 0], [0] * 8])
def test_an_out_of_range_answer_or_one_too_many_is_refused(answers: list[int]) -> None:
    outcome = instruments.score("gad7", answers)
    assert outcome.kind == "refused" and outcome.code == instruments.OUT_OF_RANGE and outcome.total is None


@pytest.mark.parametrize("name", REFUSED)
def test_an_instrument_whose_licence_is_not_settled_is_refused_and_ships_no_items(name: str) -> None:
    data = instruments.get(name)
    assert data["licence"]["state"] == "unsettled" and data["licence"]["reason"]
    assert "items" not in data and "options" not in data and "stem" not in data
    outcome = instruments.score(name, [0] * 10)
    assert outcome.kind == "refused" and outcome.code == instruments.LICENCE_UNSETTLED
    assert data["licence"]["reason"] in outcome.reason


@pytest.mark.parametrize("name", ["bdi_ii", "bdi2", "c_ssrs", "cssrs", "made_up"])
def test_bdi_ii_and_c_ssrs_are_not_shipped_at_all(name: str) -> None:
    assert name not in {i["id"] for i in instruments.catalogue()}
    outcome = instruments.score(name, [0] * 9)
    assert outcome.kind == "refused" and outcome.code == instruments.UNKNOWN


def test_the_catalogue_lists_each_instrument_with_its_licence_state() -> None:
    listed = {i["id"]: i for i in instruments.catalogue()}
    assert set(listed) == {"phq9", "gad7", *REFUSED}
    assert listed["phq9"]["licence_state"] == "settled" and listed["phq9"]["items"] == 9
    assert listed["gad7"]["score_range"] == [0, 21]
    for name in REFUSED:
        assert listed[name]["licence_state"] == "unsettled" and listed[name]["refused"]
        assert listed[name]["reason"] and "items" not in listed[name]
