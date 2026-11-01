# SPDX-License-Identifier: Apache-2.0
"""Whether the loop learned from a failure is counted by plain code: a model never grades its own lesson."""
import pytest

from g6.metaharness.lessons import (
    ROOT_CAUSES,
    Calibration,
    Lesson,
    Prediction,
    Reading,
    calibrate,
    gap,
    hit_rate,
    repeats,
    score,
    stuck,
    trusted,
)
from g6.research.ahi.contracts import ROOT_CAUSES as AHI_ROOT_CAUSES


def _readings(signal: str, kind: str, cells: dict[str, tuple[int, int]]) -> list[Reading]:
    return [Reading(signal, kind, group, agreed) for group, (right, wrong) in cells.items()
            for agreed in [True] * right + [False] * wrong]


def _lesson(mistake="verification_gap", generation=1, cases=("c1",)) -> Lesson:
    return Lesson(lesson_id=f"l{generation}", generation=generation, root_cause=mistake, case_ids=cases, hypothesis="the reviewer read only the patch",
                  alternatives=("the test was wrong",), check="replay c1", scope="reviewer on determined bugs")


def test_the_root_causes_are_the_closed_list_ahi_declares():
    assert ROOT_CAUSES == AHI_ROOT_CAUSES


def test_each_signal_is_counted_by_kind_of_task():
    table = calibrate(_readings("clean", "training", {"a": (2, 0), "b": (2, 0)}) + _readings("clean", "unseen", {"c": (0, 2), "d": (1, 1)}))
    by = {(row.signal, row.kind): row for row in table}
    assert (by["clean", "training"].agreed, by["clean", "training"].readings) == (4, 4)
    assert (by["clean", "unseen"].agreed, by["clean", "unseen"].readings) == (1, 4)
    assert by["clean", "unseen"].rate == 0.25


def test_the_bound_is_by_bug_so_many_readings_of_one_bug_earn_little():
    """The first stage's lesson: repeats of one bug are not independent, so 30 agreements on a bug are one bug's worth of evidence."""
    one, many = calibrate(_readings("clean", "x", {"a": (30, 0)}))[0], calibrate(_readings("clean", "x", {g: (1, 0) for g in "abcdefghij"}))[0]
    assert one.groups == 1 and many.groups == 10
    assert one.lower < 0.1 < 0.7 < many.lower


def test_a_bug_counts_as_agreeing_only_when_most_of_its_readings_did():
    row = calibrate(_readings("clean", "x", {"a": (2, 1), "b": (1, 1), "c": (1, 2)}))[0]
    assert row.groups == 3 and row.groups_agreed == 1


def test_a_signal_nobody_measured_is_not_trusted():
    assert not trusted((), "clean", "unseen", 0.5)


def test_a_signal_is_trusted_only_where_it_was_measured_and_only_on_its_bound():
    table = calibrate(_readings("clean", "training", {g: (1, 0) for g in "abcdefghij"}) + _readings("clean", "unseen", {"k": (5, 0)}))
    assert trusted(table, "clean", "training", 0.7)
    assert not trusted(table, "clean", "training", 0.9)
    assert not trusted(table, "clean", "unseen", 0.7), "five agreements on one bug must not read as trust"
    assert not trusted(table, "clean", "held-out", 0.1)


def test_the_gap_is_how_much_of_a_signals_accuracy_did_not_carry():
    tuned = Calibration("clean", "training", agreed=13, readings=14, groups=7, groups_agreed=7, lower=0.65)
    unseen = Calibration("clean", "unseen", agreed=19, readings=37, groups=14, groups_agreed=7, lower=0.25)
    assert gap(tuned, unseen) == pytest.approx(13 / 14 - 19 / 37)


@pytest.mark.parametrize("field,value,why", [("root_cause", "bad_luck", "root_cause"), ("check", "", "check"), ("alternatives", (), "alternatives"),
                                             ("case_ids", (), "case_ids"), ("hypothesis", " ", "hypothesis")])
def test_a_lesson_with_nothing_to_check_it_by_is_refused(field, value, why):
    fields = {"lesson_id": "l1", "generation": 1, "root_cause": "verification_gap", "case_ids": ("c1",), "hypothesis": "h", "alternatives": ("a",),
              "check": "replay c1", "scope": "s"} | {field: value}
    with pytest.raises(ValueError, match=why):
        Lesson(**fields)


def test_a_mistake_counts_as_repeated_only_after_its_lesson():
    seen = [(1, "verification_gap", "c1"), (2, "verification_gap", "c2"), (2, "bad_remediation", "c3"), (3, "verification_gap", "c1")]
    got = repeats([_lesson(generation=1)], seen)
    assert got["verification_gap"] == {"after_lesson": 2, "same_cases": 1, "generations": 2}
    assert "bad_remediation" not in got


def test_a_mistake_with_no_lesson_is_not_a_repeat_it_is_a_mistake_still_to_learn():
    assert repeats([], [(1, "verification_gap", "c1"), (2, "verification_gap", "c1")]) == {}


def test_the_earliest_lesson_of_a_mistake_is_the_one_that_counts():
    lessons = [_lesson(generation=2), _lesson(generation=1)]
    assert repeats(lessons, [(2, "verification_gap", "c9")])["verification_gap"]["after_lesson"] == 1


def test_a_mistake_is_stuck_once_it_came_back_in_enough_generations():
    got = repeats([_lesson(generation=1)], [(2, "verification_gap", "c2"), (3, "verification_gap", "c4")])
    assert stuck(got, limit=2) == ["verification_gap"]
    assert stuck(got, limit=3) == []


@pytest.mark.parametrize("before,expected,observed,said", [(0.5, 0.7, 0.8, "held"), (0.5, 0.7, 0.6, "failed"), (0.7, 0.5, 0.4, "held"), (0.7, 0.5, 0.6, "failed")])
def test_a_prediction_is_scored_in_the_direction_it_named(before, expected, observed, said):
    assert score(Prediction("p1", "false alarms", before, expected), observed) == said


def test_a_prediction_with_no_direction_is_refused():
    with pytest.raises(ValueError, match="direction"):
        Prediction("p1", "false alarms", 0.5, 0.5)


def test_a_prediction_with_no_outcome_yet_is_open_and_is_not_a_miss():
    assert score(Prediction("p1", "false alarms", 0.5, 0.7), None) == "open"
    assert hit_rate(["held", "failed", "open"]) == 0.5
    assert hit_rate(["open"]) is None
