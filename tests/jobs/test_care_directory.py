# SPDX-License-Identifier: Apache-2.0
"""The helpline directory (``g6.jobs._care.directory``): every entry carries its source, the date it was retrieved,
the SHA-256 of the retrieved page and the exact clause its number comes from; emergency numbers come first; the
directory reports its age and, past 180 days (G6's own choice), says plainly that the numbers may have changed; and a
region it does not cover is told so, never handed another country's line as its own (DUTY_OF_CARE-03,
JOB_PSYCHOLOGIST-02)."""
from __future__ import annotations

import datetime as dt
import json
import re

from g6.jobs._care import directory, sources

DATA = directory.load()
RETRIEVED = dt.date(2026, 10, 22)


def test_every_entry_carries_its_source_retrieval_date_hash_and_clause() -> None:
    register = sources.entries()
    assert DATA["entries"]
    for entry in DATA["entries"]:
        found = register[entry["source"]]
        assert entry["retrieved"] == found["retrieved"] and entry["sha256"] == found["sha256"], entry["service"]
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) and entry["clause"].strip()


def test_each_entrys_clause_is_one_its_register_entry_quotes() -> None:
    register = sources.entries()
    for entry in DATA["entries"]:
        assert entry["clause"] in register[entry["source"]]["clause"].split(" [...] "), entry["service"]


def test_each_number_is_printed_in_its_own_clause() -> None:
    # A number typed from memory, with no clause that prints it, fails here. A number the source prints another way
    # (9–1–1) carries that printing, which must be in the clause and have the number's own digits.
    for entry in DATA["entries"]:
        assert entry["contacts"], entry["service"]
        for contact in entry["contacts"]:
            if contact["how"] == "chat online":
                assert entry["kind"] == "online" and contact["address"].startswith("https://"), entry["service"]
                assert "chat" in entry["clause"].lower(), entry["service"]
                continue
            assert contact["how"] in ("call", "text"), entry["service"]
            printed = contact.get("printed_as", contact["number"])
            assert printed in entry["clause"], entry["service"]
            assert re.sub(r"\D", "", printed) == re.sub(r"\D", "", contact["number"]), entry["service"]


def test_open_at_all_hours_is_said_by_the_entrys_own_clause() -> None:
    # A service counted as open at all hours must have a clause that says so.
    for entry in DATA["entries"]:
        if entry.get("all_hours"):
            assert any(w in entry["clause"].lower() for w in ("24/7", "anytime", "any time", "24 hours")), \
                entry["service"]


def test_the_held_crisis_lines_are_there() -> None:
    numbers = {(e["region"], c.get("number")) for e in DATA["entries"] for c in e["contacts"]}
    assert {("AU", "000"), ("AU", "13 11 14"), ("AU", "0477 13 11 14"), ("US", "988"), ("UK", "116 123"),
            ("US", "911"), ("UK", "999")} <= numbers
    online = {(e["region"], c["address"]) for e in DATA["entries"] for c in e["contacts"] if c["how"] == "chat online"}
    assert online == {("AU", "https://www.lifeline.org.au/chat"), ("US", "https://chat.988lifeline.org/")}


def test_emergency_numbers_come_first() -> None:
    for region in (None, "AU", "US", "UK"):
        kinds = [e["kind"] for e in directory.lookup(region, today=RETRIEVED)["entries"]]
        assert kinds == sorted(kinds, key=lambda k: k != "emergency"), region
    assert directory.lookup("AU", today=RETRIEVED)["entries"][0]["contacts"][0]["number"] == "000"


def test_the_directory_reports_its_age_and_each_entrys() -> None:
    found = directory.lookup("AU", today=dt.date(2026, 11, 1))
    assert found["age_days"] == 10 and not found["stale"]
    assert all(e["age_days"] == 10 and e["retrieved"] == "2026-10-22" for e in found["entries"])
    assert "may have changed" not in found["note"]


def test_past_180_days_it_says_plainly_that_the_numbers_may_have_changed() -> None:
    # Removing the age comparison in directory.lookup makes the second half fail; making it ">=" fails the first.
    on_the_day = directory.lookup(None, today=RETRIEVED + dt.timedelta(days=180))
    assert not on_the_day["stale"]
    past = directory.lookup(None, today=RETRIEVED + dt.timedelta(days=181))
    assert past["stale"] and past["age_days"] == 181 and "may have changed" in past["note"]
    assert past["stale_after_days"] == 180 and past["stale_after_source"] == "g6"


def test_no_region_gives_every_region() -> None:
    found = directory.lookup(None, today=RETRIEVED)
    assert found["region"] is None and found["region_covered"] is None
    assert {e["region"] for e in found["entries"]} == {"AU", "US", "UK"}


def test_a_region_given_gets_only_its_own_entries() -> None:
    found = directory.lookup("us", today=RETRIEVED)
    assert found["region"] == "US" and found["region_covered"] is True
    assert {e["region"] for e in found["entries"]} == {"US"}
    assert directory.lookup("GB", today=RETRIEVED)["region"] == "UK"


def test_a_region_it_does_not_cover_is_told_so_and_never_given_another_countrys_line_as_its_own() -> None:
    # JOB_PSYCHOLOGIST-02: the archive handed unlisted locales another country's hotline.
    found = directory.lookup("NZ", today=RETRIEVED)
    assert found["region"] == "NZ" and found["region_covered"] is False
    assert "does not cover NZ" in found["note"] and "local emergency number" in found["note"]
    assert all(e["region"] != "NZ" for e in found["entries"])


def test_each_covered_region_has_its_own_emergency_number_first() -> None:
    # Removing the 911 or 999 entry from data/directory.json leaves that region with no emergency line.
    for region, number in (("AU", "000"), ("US", "911"), ("UK", "999")):
        first = directory.lookup(region, today=RETRIEVED)["entries"][0]
        assert first["kind"] == "emergency" and first["contacts"][0]["number"] == number, region
    # with no region stated, nobody's own emergency number is known
    assert "local emergency number" in directory.lookup(None, today=RETRIEVED)["note"]


def test_nothing_in_the_directory_calls_itself_verified() -> None:
    text = json.dumps(directory.lookup(None, today=RETRIEVED)).lower()
    assert "verified" not in text and "safe" not in text.replace("safety", "")
