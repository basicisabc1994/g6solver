# SPDX-License-Identifier: Apache-2.0
"""The duty-of-care source register (handoff section 4, in the v2a format of ``docs/transition/CERTIFICATES.md``):
every entry has every field, a clause hash that matches its clause and a SHA-256 of the bytes retrieved; every rule
the shared code applies names an entry, the caller's input, or G6's own choice with its effect; each instrument and
directory entry cites an entry the register has; and what is not yet held is listed as missing, never filled in."""
from __future__ import annotations

import datetime as dt
import hashlib

from g6.core import component_certificate as records
from g6.jobs._care import directory, instruments, sources

TODAY = dt.date(2026, 10, 23)


def test_every_register_entry_has_every_field_and_every_rule_resolves() -> None:
    assert sources.register_problems() == []
    for entry in sources.entries().values():
        assert all(str(entry[f]).strip() for f in sources.FIELDS), entry["id"]
        assert entry["url"].startswith("https://") and entry["review_months"] == 12
        assert hashlib.sha256(entry["clause"].encode("utf-8")).hexdigest() == entry["clause_sha256"]


def test_a_changed_clause_is_caught_by_its_hash(monkeypatch) -> None:
    # Removing the clause-hash comparison in sources.register_problems makes this pass silently.
    real = sources.register()
    tampered = {**real, "entries": [{**real["entries"][0], "clause": real["entries"][0]["clause"] + " (edited)"},
                                    *real["entries"][1:]]}
    monkeypatch.setattr(sources, "register", lambda: tampered)
    assert any("clause hash differs" in p for p in sources.register_problems())


def test_each_instrument_and_directory_entry_cites_the_register() -> None:
    held = sources.entries()
    for name in ("phq9", "gad7"):
        data = instruments.get(name)
        assert data["source"] in held and held[data["source"]]["sha256"] == data["licence"]["sha256"]
    assert {e["source"] for e in directory.load()["entries"]} <= set(held)


def test_g6s_own_rules_say_what_they_can_do_and_never_let_a_result_through() -> None:
    own = {name: rule for name, rule in sources.RULES.items() if rule.kind == "own"}
    assert own and all(rule.effect in ("refuse", "escalate", "lower_label", "note") for rule in own.values())
    assert all(rule.implements.startswith("g6/jobs/_care/") for rule in sources.RULES.values() if rule.implements)


def test_each_guard_cites_the_source_that_sets_its_threshold() -> None:
    # Fix pass 5: the PHQ-9 form sets no threshold for item 9, and WHO asks only that numbers be "checked regularly",
    # so both are G6's own; the "two" lines come from Mindframe's help-seeking page. Citing phq9-form or who-media-2023
    # again in sources.RULES fails here.
    assert sources.RULES["phq9_item9"].source == records.OWN_THRESHOLD
    assert sources.RULES["numbers_checked"].source == records.OWN_THRESHOLD
    assert sources.RULES["few_services"].source == "mindframe-help-seeking"
    assert "two support services" in sources.entries()["mindframe-help-seeking"]["clause"]


def test_what_is_not_yet_held_is_listed_as_missing_not_filled_in() -> None:
    missing = {m["topic"]: m for m in sources.register()["missing"]}
    assert set(missing) == {"an online support service for the United Kingdom"}
    assert all(m["pages_needed"] and m["status"].startswith("not held") for m in missing.values())


def test_the_safe_messaging_and_emergency_sources_are_held() -> None:
    held = sources.entries()
    assert {"who-media-2023", "mindframe-guidelines", "mindframe-language", "mindframe-method-location",
            "mindframe-help-seeking", "nhs-when-to-call-999", "us-code-47-251"} <= set(held)
    assert "CC BY-NC-SA 3.0 IGO" in held["who-media-2023"]["licence"]


def test_each_entry_says_how_its_clause_was_checked() -> None:
    # A clause from a PDF is matched to the text extracted from it, never claimed as matched to the bytes.
    for entry in sources.entries().values():
        check = entry["clause_check"]
        if entry["url"].endswith(".pdf") or entry["id"] == "who-media-2023":
            assert "extracted" in check and "not matched to the bytes" in check, entry["id"]
        else:
            assert "retrieved bytes" in check, entry["id"]


def test_no_register_entry_is_an_encyclopaedia_and_911_rests_on_the_statute() -> None:
    # docs/transition/CERTIFICATES.md: "a blog, an encyclopaedia ... is never a register entry". The United States
    # emergency number rests on 47 U.S.C. 251(e)(3), never on an encyclopaedia article.
    for entry in sources.entries().values():
        text = " ".join(str(entry.get(k, "")) for k in ("url", "issuing_body", "title", "url_role")).lower()
        assert "wikipedia" not in text and "encyclopaedia" not in text, entry["id"]
    us_emergency = [e for e in directory.load()["entries"] if e["region"] == "US" and e["kind"] == "emergency"]
    assert [e["source"] for e in us_emergency] == ["us-code-47-251"]
    assert "universal emergency telephone number within the United States" in us_emergency[0]["clause"]


def test_no_entry_is_past_its_review_window_today() -> None:
    assert sources.stale_sources(TODAY) == []
    later = sources.stale_sources(TODAY + dt.timedelta(days=400))
    assert {f["source"] for f in later} == set(sources.entries())
