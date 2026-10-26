# SPDX-License-Identifier: Apache-2.0
"""The crisis response follows safe-messaging practice, each rule citing a clause its register entry quotes (WHO and
IASP, Preventing suicide: a resource for media professionals, update 2023; Everymind's Mindframe guidelines): where
and how to get help comes first, emergency number first; only a few services are named; nothing the person said and
nothing about any act is repeated; and someone worried about another person is told they can call too.

The phrases here name ideation and intent only, never a method."""
from __future__ import annotations

import datetime as dt
import json

import pytest

from g6.jobs._care import crisis, directory, envelope, sources

TODAY = dt.date(2026, 10, 23)


def _response(region: str | None) -> str:
    return crisis.response(directory.lookup(region, today=TODAY))


def test_each_response_rule_cites_a_clause_its_register_entry_quotes() -> None:
    held = sources.entries()
    assert {source for _, source, _ in crisis.RESPONSE_RULES} == {
        "who-media-2023", "mindframe-guidelines", "mindframe-language", "mindframe-method-location",
        "mindframe-help-seeking"}
    # Fix pass 5: two rules' thresholds rest elsewhere than the practice the response cites: few_services' two lines
    # on Mindframe's clause, numbers_checked's windows on G6's own choice. Every other rule cites the same entry.
    threshold = {"few_services": "mindframe-help-seeking", "numbers_checked": "g6"}
    for rule, source, clause in crisis.RESPONSE_RULES:
        assert sources.RULES[rule].source == threshold.get(rule, source), rule
        assert clause in held[source]["clause"].split(" [...] "), rule


def _views() -> list[dict]:
    broken = {"region": "AU", "region_covered": None, "entries": [], "note": "could not be read"}
    return [directory.lookup(r, today=TODAY) for r in ("AU", "US", "UK", "NZ", None)] + [broken]


def test_the_problematic_phrases_are_read_from_the_language_guide_clauses() -> None:
    assert set(sources.problematic_phrases()) == {
        "successful suicide", "unsuccessful suicide", "committed suicide", "commit suicide", "suicide epidemic",
        "failed suicide", "suicide bid", "political suicide", "suicide mission"}


def test_the_response_never_uses_a_phrase_the_language_guide_marks_problematic() -> None:
    # Mindframe's language table. The control shows the check reads the text: a doctored response is caught.
    for view in _views():
        assert crisis.problematic_language(crisis.response(view)) == [], view["region"]
    assert crisis.problematic_language("Someone Committed  Suicide here") == ["committed suicide"]


@pytest.mark.parametrize("answer", ["crisis", "check_failed"])
def test_a_composed_response_holding_a_problematic_phrase_is_never_returned(answer: str, monkeypatch) -> None:
    # Fix item 4: removing the problematic_language call in crisis.response_problems leaves "language" unnamed here,
    # and removing the response_problems call in crisis._answer returns these words to the person.
    real = crisis.response
    monkeypatch.setattr(crisis, "response", lambda *a, **k: real(*a, **k) + "\nIt was political suicide.")
    if answer == "crisis":
        result = crisis.crisis_result("screen", [], region="AU", locked=True, reason="r", today=TODAY)
        assert result.code == envelope.RESPONSE_REFUSED and result.locked is True
    else:
        result = crisis.check_failed("screen", OSError("x"), region="AU")
        assert result.code == envelope.CHECK_FAILED  # the trigger could not run: that comes first
    dumped = result.model_dump()
    assert result.completion_state == "blocked-escalated" and "response" not in dumped
    assert "political suicide" not in json.dumps(dumped).lower() and result.directory["entries"]
    assert "language" in [e["rule"] for e in result.evidence if e.get("kind") == "response_check"]
    assert "not shown" in result.reason and "emergency number" in result.reason


@pytest.mark.parametrize(("region", "expected"), [("AU", 2), ("US", 1), ("UK", 1)])
def test_two_services_open_at_all_hours_a_region_or_fewer_said_so(region: str, expected: int) -> None:
    # Mindframe asks for two support services open at all hours (the register's own words); the emergency number
    # comes first and is not counted. Naming only the first all-hours line (or every one) fails the Australian case.
    view = directory.lookup(region, today=TODAY)
    named = crisis.named_services(view)
    phones = [e for e in named if e["kind"] not in ("emergency", "online")]
    assert named[0]["kind"] == "emergency" and len(phones) == expected
    assert all(e["all_hours"] for e in phones)
    gaps = [g for g in crisis.response_gaps(view) if g["rule"] == "two_all_hours_services"]
    assert (gaps != []) is (expected < 2)
    if gaps:
        assert f"{expected} service" in gaps[0]["note"] and "not two" in gaps[0]["note"]


@pytest.mark.parametrize(("region", "online"), [("AU", True), ("US", True), ("UK", False)])
def test_one_online_service_where_one_is_held_otherwise_a_recorded_gap(region: str, online: bool) -> None:
    # Mindframe asks for at least one support service or forum that works online. None is invented.
    view = directory.lookup(region, today=TODAY)
    named = [e for e in crisis.named_services(view) if e["kind"] == "online"]
    assert len(named) == (1 if online else 0)
    gaps = [g for g in crisis.response_gaps(view) if g["rule"] == "online_service"]
    assert (gaps == []) is online
    if online:
        assert "chat online at https://" in crisis.response(view)


@pytest.mark.parametrize(("region", "number"), [("AU", "000"), ("US", "911"), ("UK", "999")])
def test_help_comes_first_with_the_regions_emergency_number(region: str, number: str) -> None:
    # WHO 2023: help information "at the beginning". Dropping the region's emergency entry, or moving the help lines
    # after the rest, fails here.
    lines = _response(region).split("\n")
    assert "life is in danger" in lines[0] and number in lines[0]
    assert lines[2].startswith("- ") and number in lines[2]


@pytest.mark.parametrize(("region", "count"), [("AU", 4), ("US", 3), ("UK", 2), (None, 0), ("NZ", 0)])
def test_only_a_few_services_are_named(region: str | None, count: int) -> None:
    # WHO 2023: "Only a limited number of services should be mentioned": per region the emergency number, two lines
    # open at all hours and one online service, where held. Listing every directory entry fails here; the full
    # directory still travels beside the response. An unstated or uncovered region gets none (item 4).
    services = [line for line in _response(region).split("\n") if line.startswith("- ")]
    assert len(services) == count


@pytest.mark.parametrize("region", [None, "NZ"])
def test_an_unstated_or_uncovered_region_names_no_other_countrys_number(region: str | None) -> None:
    # Item 4: listing every region's lines (the earlier response) fails here; the directory op still has them.
    text = _response(region)
    numbers = {c["number"] for e in directory.load()["entries"] for c in e["contacts"] if "number" in c}
    assert [n for n in numbers if n in text] == []
    first = text.split("\n")[0]
    assert first.endswith("call your local emergency number now.")
    assert "local crisis line" in text and "Which country are you in?" in text
    assert "Australia, the United States and the United Kingdom" in text


@pytest.mark.parametrize("region", ["AU", "US", "UK"])
def test_a_covered_region_still_gives_its_emergency_number_first_and_at_most_two_lines(region: str) -> None:
    named = crisis.named_services(directory.lookup(region, today=TODAY))
    assert named[0]["kind"] == "emergency"
    assert len([e for e in named if e["kind"] not in ("emergency", "online")]) <= 2


def test_a_stale_directory_says_so_in_the_words_a_person_reads() -> None:
    # Item 6: past 180 days the response and the crisis card carry one plain line; removing the stale check in
    # crisis.response fails the first half.
    later = dt.date(2026, 10, 22) + dt.timedelta(days=181)
    text = crisis.response(directory.lookup("AU", today=later), today=later)
    assert "last checked on 22 October 2026 and may have changed" in text
    card = crisis.crisis_result("screen", [], region="AU", locked=False, reason="r", today=later).warning_card
    assert "may have changed" in card["message"]
    assert "may have changed" not in crisis.response(directory.lookup("AU", today=TODAY), today=TODAY)


def test_a_source_past_its_own_review_date_says_so_now() -> None:
    # Item 6: the NHS page gave 3 February 2026 as its next review; that date has passed, so the United Kingdom's
    # response says its numbers may have changed even though the directory is days old.
    assert sources.entries()["nhs-when-to-call-999"]["source_review_due"] == "2026-02-03"
    text = crisis.response(directory.lookup("UK", today=TODAY), today=TODAY)
    assert text.count("may have changed") == 1 and "last checked on 22 October 2026" in text


def test_the_second_service_fits_what_was_said_where_a_held_clause_supports_it() -> None:
    # Item 7: someone harmed at home gets 1800RESPECT (its clause: "for those facing domestic or family violence")
    # in place of the second crisis line; 000 stays first and there are still at most two lines.
    fired = crisis.scan({"m": "my dad hits me and I am scared"}).evidence()
    named = crisis.named_services(directory.lookup("AU", today=TODAY), categories=("harmed_by_someone",))
    assert named[0]["contacts"][0]["number"] == "000"
    lines = [e["service"] for e in named if e["kind"] not in ("emergency", "online")]
    assert lines == ["Lifeline", "1800RESPECT"] and fired
    child = crisis.named_services(directory.lookup("AU", today=TODAY), categories=("child_danger",))
    assert [e["service"] for e in child if e["kind"] not in ("emergency", "online")] == ["Lifeline", "Kids Helpline"]
    # nothing held fits ideation in the United Kingdom: its line stays as it was
    uk = crisis.named_services(directory.lookup("UK", today=TODAY), categories=("harmed_by_someone",))
    assert [e["service"] for e in uk if e["kind"] not in ("emergency", "online")] == ["Samaritans"]


def test_each_remit_rests_on_its_own_clause_and_says_no_qualified_person_reviewed_it() -> None:
    for entry in directory.load()["entries"]:
        remit = entry.get("remit")
        if remit:
            assert remit["clause_says"] in entry["clause"] and remit["categories"], entry["service"]
            # DECISIONS #39: nothing waits for a qualified reviewer; "for the qualified reviewer to confirm" fails here
            assert remit["review"] == "not reviewed by a qualified person"


def test_someone_worried_about_another_person_is_told_they_can_call_too() -> None:
    assert "worried about someone else" in _response("AU")


def test_the_response_repeats_nothing_the_person_said_and_names_no_category() -> None:
    # WHO 2023: "Don't describe the method used"; Mindframe asks that audiences be harmed as little as possible. The response is the same
    # whatever fired, and carries neither the person's words nor what the trigger called them.
    first = crisis.crisis_result("screen", crisis.scan({"m": "I want to die"}).evidence(), region="AU", locked=False,
                                 reason="r", today=TODAY)
    second = crisis.crisis_result("screen", crisis.scan({"m": "I will hurt myself"}).evidence(), region="AU",
                                  locked=False, reason="r", today=TODAY)
    assert first.response == second.response
    text = first.response.lower()
    assert "want to die" not in text and "hurt myself" not in text
    assert not any(category.replace("_", " ") in text for category in crisis.CATEGORIES)


def test_the_crisis_result_cites_every_safe_messaging_rule_it_applied() -> None:
    result = crisis.crisis_result("screen", [], region="UK", locked=False, reason="r", today=TODAY)
    cited = [(e["rule"], e["source"], e["clause"]) for e in result.evidence if e.get("kind") == "safe_messaging"]
    assert cited == [tuple(r) for r in crisis.RESPONSE_RULES]
