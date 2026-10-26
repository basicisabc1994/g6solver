# SPDX-License-Identifier: Apache-2.0
"""The jobs family's one crisis trigger (``g6.jobs._care.crisis``): every field the caller gives is scanned, with
``g6.guards``' own text folding and a word-boundary match; every term of each recall set fires; each term's near miss
is either not fired or listed as a known false positive; each exception is its own rule and never hides a trigger
phrase; and the lock has no way to be lifted (DUTY_OF_CARE-01, JOB_PSYCHOLOGIST-02).

The phrases here name ideation and intent only, never a method."""
from __future__ import annotations

import time

import pytest

from g6.jobs._care import crisis

RULES = crisis.rules()
TERMS = [(category, entry["term"]) for category, body in RULES["categories"].items() for entry in body["terms"]]
NEAR = [(entry["term"], entry["near_miss"], entry["near_miss_fires"])
        for body in RULES["categories"].values() for entry in body["terms"]]
EXCEPTIONS = RULES["exceptions"]


def _pairs(finding: crisis.CrisisFinding) -> set[tuple[str, str]]:
    return {(m.category, m.term) for m in finding.matches}


def test_the_trigger_covers_the_kinds_of_crisis() -> None:
    assert set(RULES["categories"]) == {"suicidal_ideation", "self_harm", "harm_to_others", "child_danger",
                                        "harmed_by_someone"}
    assert all(body["terms"] for body in RULES["categories"].values())
    assert len({term for _, term in TERMS}) == len(TERMS)


#: Spellings the first trigger missed (item 2), each with the category it must fire. Full-width letters and a
#: zero-width space inside a word are folded by NFKC and by removing format characters.
SPELLINGS = [
    ("I dont want to live anymore", "suicidal_ideation"),
    ("Some days I want to kill my self", "suicidal_ideation"),
    ("thinking about kill-myself again", "suicidal_ideation"),
    ("I wish I was dead", "suicidal_ideation"),
    ("I might unalive myself", "suicidal_ideation"),
    ("honestly kms", "suicidal_ideation"),
    ("ｉ ｗａｎｔ ｔｏ ｄｉｅ", "suicidal_ideation"),
    ("I want to d​ie", "suicidal_ideation"),
    ("I want to die", "suicidal_ideation"),
    ("my dad hits me and I am scared", "harmed_by_someone"),
]


@pytest.mark.parametrize(("text", "category"), SPELLINGS, ids=range(len(SPELLINGS)))
def test_each_spelling_the_first_trigger_missed_now_fires(text: str, category: str) -> None:
    # Item 2. Removing the NFKC step or the format-character removal in crisis.normalise fails the full-width and
    # zero-width cases; removing an added term from data/crisis_rules.json fails its case.
    assert category in {m.category for m in crisis.scan({"message": text}).matches}


def test_terms_added_for_these_spellings_are_marked_as_added_and_not_reviewed_by_a_qualified_person() -> None:
    added = {e["term"] for body in RULES["categories"].values() for e in body["terms"] if e.get("added_for_review")}
    assert {"dont want to live", "kill my self", "wish i was dead", "unalive myself", "kms", "hits me"} <= added
    # DECISIONS #39: no qualified reviewer confirms them; "added for the qualified reviewer to confirm" fails here
    assert all(e.get("review") == "added after the first review; not reviewed by a qualified person"
               for body in RULES["categories"].values() for e in body["terms"] if e.get("added_for_review"))


def test_a_phrase_split_across_two_fields_fires() -> None:
    # Item 2: the leaves are also read joined by a space in order. Removing the joined pass fails here.
    finding = crisis.scan({"first": "some days I really want to", "second": "die, honestly"})
    assert finding.fired and [m.field for m in finding.matches] == ["first + second"]


def test_a_field_name_is_scanned_like_its_value() -> None:
    # Item 8: crisis words sent as a key, with an empty value, still fire.
    assert crisis.scan({"I want to die": ""}).fired


def test_the_scans_work_is_bounded() -> None:
    # Item 3: 100 fields of 100,000 characters, every occurrence inside an exception. Searching every cover for every
    # occurrence (the first trigger) took far longer; one Excepted per exception, term and field, and covers searched
    # in order, keep it under a second (after one warm-up scan, which builds the normaliser's tables).
    crisis.scan({"warm-up": "x"})
    text = ("kill them with kindness " * 4166).ljust(100_000)
    fields = {f"f{i}": text for i in range(100)}
    start = time.perf_counter()
    finding = crisis.scan(fields)
    took = time.perf_counter() - start
    assert not finding.fired and len(finding.excepted) <= len(EXCEPTIONS) * len(fields)
    assert took < 1.0, took


@pytest.mark.parametrize(("category", "term"), TERMS)
def test_every_term_of_every_recall_set_fires(category: str, term: str) -> None:
    # Removing the term from data/crisis_rules.json, or the pattern match in crisis.scan, makes this fail.
    sentence = f"Some days it is just this: {term}. That is all."
    assert (category, term) in _pairs(crisis.scan({"message": sentence}))
    # the same words, shouted and spaced out, are folded the way g6.guards folds text
    shouted = "  ".join(term.upper().split())
    assert (category, term) in _pairs(crisis.scan({"message": f"{shouted}!"}))


@pytest.mark.parametrize(("term", "near_miss", "listed"), NEAR)
def test_each_near_miss_is_not_fired_or_is_listed_as_a_known_false_positive(term: str, near_miss: str,
                                                                            listed: bool) -> None:
    # Removing the word-boundary lookarounds in crisis._pattern fires the near misses marked false.
    assert crisis.scan({"message": near_miss}).fired is listed, (term, near_miss)
    assert (near_miss in crisis.known_false_positives()) is listed


def test_a_crisis_phrase_in_any_field_fires_not_only_the_main_text() -> None:
    # D1 (JOB_PSYCHOLOGIST-02: the archive scanned only the task). Removing the loop over every field in crisis.scan
    # (scanning only the first) makes this fail: the phrase sits in the last of three fields, under a name nothing
    # was written around.
    fields = {"task": "Book a dentist appointment for Thursday", "title": "Weekly plan",
              "carer_remarks": "She said she has no reason to live any more"}
    finding = crisis.scan(fields)
    assert finding.fired and [(m.field, m.category) for m in finding.matches] == [("carer_remarks",
                                                                                   "suicidal_ideation")]
    assert finding.fields_scanned == ("task", "title", "carer_remarks")


def test_the_models_own_output_is_scanned_like_any_input() -> None:
    finding = crisis.scan({"model_output": "Draft reply: I hear that you want to die, and"})
    assert finding.fired and finding.matches[0].field == "model_output"


def test_curly_apostrophes_are_folded_as_g6_guards_folds_them() -> None:
    # crisis.scan uses g6.guards' own folding; without it the curly apostrophe would not match "don't".
    assert crisis.scan({"m": "I don’t want to live like this"}).fired


def test_text_with_no_listed_words_does_not_fire_and_says_what_it_checked() -> None:
    finding = crisis.scan({"note": "Feeling flat after a long week at work.", "empty": ""})
    assert not finding.fired and finding.matches == () and finding.fields_scanned == ("note", "empty")
    assert crisis.scan({}).fields_scanned == () and not crisis.scan({}).fired


def test_a_field_that_is_not_text_is_refused_not_skipped() -> None:
    with pytest.raises(TypeError):
        crisis.scan({"answer": 3})  # type: ignore[dict-item]


@pytest.mark.parametrize("exception", EXCEPTIONS, ids=lambda e: e["id"])
def test_each_exception_is_its_own_rule_and_holds_alone(exception: dict) -> None:
    # DECISIONS #39: nothing waits for a qualified person's review; "awaiting review by a qualified person" fails here
    assert exception["review"] == "not reviewed by a qualified person" and exception["why"]
    finding = crisis.scan({"m": f"Honestly, {exception['phrase']} today."})
    assert not finding.fired and [e.exception for e in finding.excepted] == [exception["id"]]


@pytest.mark.parametrize("exception", EXCEPTIONS, ids=lambda e: e["id"])
def test_no_exception_hides_any_trigger_phrase_beside_it(exception: dict) -> None:
    # Widening an exception's span beyond its own words in crisis.scan makes a trigger phrase beside it disappear.
    for category, term in TERMS:
        before = crisis.scan({"m": f"{exception['phrase']}, and {term}"})
        after = crisis.scan({"m": f"{term}; {exception['phrase']}"})
        assert (category, term) in _pairs(before) and (category, term) in _pairs(after), (exception["id"], term)


def test_the_lock_has_no_way_to_be_lifted() -> None:
    # DUTY_OF_CARE-01: the archive let any asserted clinician id lift its lock. This one has no such method at all.
    public = sorted(name for name in dir(crisis.CrisisLock) if not name.startswith("_"))
    assert public == ["engage", "held"]
    lock = crisis.CrisisLock()
    first, second = {"first": 1}, {"second": 2}
    assert lock.held("person-1") is None
    lock.engage("person-1", first)
    lock.engage("person-1", second)
    assert lock.held("person-1") == first and lock.held("person-2") is None
    with pytest.raises(AttributeError):
        lock.anything_else = True  # type: ignore[attr-defined]
