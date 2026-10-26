# SPDX-License-Identifier: Apache-2.0
"""Where every rule the duty-of-care code applies comes from: the committed source register, the caller's input, or
G6's own choice, which can only refuse, escalate or add a note (handoff section 4; the v2a register format of
``docs/transition/CERTIFICATES.md``).

``data/source_register.json`` holds one entry per primary source: issuing body, title, edition, publication date,
jurisdiction, URL, licence, the date retrieved, the SHA-256 of the bytes retrieved, the exact clause used and its own
SHA-256, and how its clauses were matched (``clause_check``). What is not yet held (an online support service for the
United Kingdom) is listed under ``missing`` and is never filled in. :func:`stale_sources` raises a finding for an entry
past its review window. :func:`problematic_phrases` reads the language guide's problematic phrases from its clause.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from dataclasses import dataclass
from importlib import resources
from typing import Any, Literal

#: The fields every register entry fills in.
FIELDS = ("id", "issuing_body", "title", "edition", "published", "jurisdiction", "url", "licence", "retrieved",
          "sha256", "clause", "review_months")


@dataclass(frozen=True)
class Rule:
    """One rule the code applies. ``source`` is a register entry's id, the caller's fields, or ``"g6"`` for a rule of
    G6's own; ``effect`` says what an own rule can do to an answer (refuse, escalate, lower_label or note)."""

    rule: str
    kind: Literal["register", "caller", "own"]
    source: str | tuple[str, ...]
    effect: str = ""
    tier: str = ""
    implements: str = ""


#: G6's own response rules are checked on every composed response before it is returned; one that breaks any of them
#: is never returned (``crisis.response_problems``).
RULES: dict[str, Rule] = {
    "crisis_trigger": Rule("any listed term, matched as whole words in any field after g6.guards folds the text and "
                           "outside every exception's own words, gives the crisis result", "own", "g6", "escalate",
                           tier="guard", implements="g6/jobs/_care/crisis.py:term_fires"),
    "crisis_lock": Rule("once the crisis result is given for a subject in a run, every later op for that subject in "
                        "that run gives it again; nothing releases it", "own", "g6", "refuse", tier="guard",
                        implements="g6/jobs/_care/crisis.py:locked"),
    "emergency_first": Rule("the response and the directory put the emergency number first, as the government "
                            "directory does", "own", "healthdirect-mental-health-helplines", "note", tier="guard",
                            implements="g6/jobs/_care/directory.py:emergency_first"),
    "directory_age": Rule("past 180 days since retrieval the directory says the numbers may have changed (the WHO "
                          "resource asks that contact details be checked regularly; the 180 days are G6's own)", "own",
                          "g6", "note", tier="guard", implements="g6/jobs/_care/directory.py:too_old"),
    "help_first": Rule("the response opens with where to get help now, the region's emergency number first; a "
                       "composed response that does not is never returned", "own", "who-media-2023", "refuse",
                       tier="guard", implements="g6/jobs/_care/crisis.py:help_not_first"),
    "where_and_how": Rule("each service named says how to reach it (call or text, the number, or the address to chat "
                          "at) and when; a composed response with a line that does not is never returned", "own",
                          "who-media-2023", "refuse", tier="guard", implements="g6/jobs/_care/crisis.py:unreachable"),
    "few_services": Rule("the response names only a few services a region: the emergency number, two lines open at "
                         "all hours and one online service; the full directory travels beside it (WHO asks for a "
                         "limited number; the two are Mindframe's)", "own", "mindframe-help-seeking", "note",
                         tier="guard", implements="g6/jobs/_care/crisis.py:enough_lines"),
    "two_all_hours_services": Rule("after the emergency number, two services open at all hours a region where the "
                                   "directory holds them, and fewer only when it holds fewer, saying so", "own",
                                   "mindframe-help-seeking", "note", tier="guard",
                                   implements="g6/jobs/_care/crisis.py:fewer_lines"),
    "online_service": Rule("one online service a region where the directory holds one with a source; otherwise the "
                           "gap is said, and none is invented", "own", "mindframe-help-seeking", "note", tier="guard",
                           implements="g6/jobs/_care/crisis.py:online_service"),
    "category_fit": Rule("where a held clause states a remit matching what fired, that line takes the second place "
                         "(no qualified person has reviewed a remit)", "own", "mindframe-help-seeking", "note",
                         tier="guard", implements="g6/jobs/_care/crisis.py:category_fit"),
    "numbers_checked": Rule("when the numbers shown may have changed (the directory past 180 days, or a source past "
                            "its review), the response and the card say so in one plain line (WHO asks only that "
                            "contact details be checked regularly; the 180 days and the 12-month review window are "
                            "G6's own, and a page's own review date is the directory's data)", "own", "g6", "note",
                            tier="guard", implements="g6/jobs/_care/crisis.py:stale_line"),
    "language": Rule("the response uses no phrase the language guide marks problematic; a composed response holding "
                     "one is never returned", "own", "mindframe-language", "refuse", tier="guard",
                     implements="g6/jobs/_care/crisis.py:problematic_language"),
    "method_location": Rule("the response gives no detail of any method or place: it uses only G6's own reviewed "
                            "words and the directory's own; a composed response holding any other word is never "
                            "returned", "own", "mindframe-method-location", "refuse", tier="guard",
                            implements="g6/jobs/_care/crisis.py:outside_words"),
    "no_method": Rule("the response describes no act and repeats nothing the person said, nor what the trigger "
                      "called it: the trigger finds no listed term in its own words and it names no category; a "
                      "composed response that does is never returned", "own", "who-media-2023", "refuse",
                      tier="guard", implements="g6/jobs/_care/crisis.py:listed_words"),
    "no_sensational_language": Rule("the response uses plain, calm words and presents nothing as a solution: no "
                                    "exclamation mark, and its own words never name suicide; a composed response "
                                    "that does is never returned", "own", "who-media-2023", "refuse", tier="guard",
                                    implements="g6/jobs/_care/crisis.py:sensational"),
    "seek_help_message": Rule("the response says that talking to someone can help, and how to reach someone now; a "
                              "composed response that does not is never returned", "own", "who-media-2023", "refuse",
                              tier="guard", implements="g6/jobs/_care/crisis.py:no_seek_help"),
    "hope": Rule("the response says that people do come through times like this; a composed response that does not "
                 "is never returned", "own", "who-media-2023", "refuse", tier="guard",
                 implements="g6/jobs/_care/crisis.py:no_hope"),
    "others_too": Rule("the response tells someone worried about another person that they can contact the services "
                       "too; a composed response that does not is never returned", "own", "who-media-2023", "refuse",
                       tier="guard", implements="g6/jobs/_care/crisis.py:no_others"),
    "minimise_harm": Rule("one response serves every category the trigger fires on (self-harm included), with no "
                          "detail of any act: the same reviewed words for every category and nothing else; a "
                          "composed response holding any other word is never returned", "own", "mindframe-guidelines",
                          "refuse", tier="guard", implements="g6/jobs/_care/crisis.py:outside_words"),
    "phq9_total": Rule("the PHQ-9 total is the sum of its nine item scores, as the form's office-coding line adds "
                       "them; the form prints no worked value", "register", "phq9-form", tier="property",
                       implements="g6/jobs/_care/instruments.py:score"),
    "gad7_total": Rule("the GAD-7 total is the sum of its seven item scores, as the form's office-coding line adds "
                       "them; the form prints no worked value", "register", "gad7-form", tier="property",
                       implements="g6/jobs/_care/instruments.py:score"),
    "phq9_item9": Rule("any PHQ-9 item 9 answer other than 0 gives the crisis result, whatever the total (the form "
                       "sets no threshold: this one is G6's own)", "own", "g6", "escalate", tier="guard",
                       implements="g6/jobs/_care/instruments.py:to_a_person"),
    "answers_required": Rule("an item with no answer, or an answer that is not one of the form's values, refuses the "
                             "score; nothing is assumed", "own", "g6", "refuse", tier="guard",
                             implements="g6/jobs/_care/instruments.py:unanswered"),
    "licence_settled": Rule("an instrument whose licence entry is not settled is refused and ships no item text",
                            "own", "g6", "refuse", tier="guard",
                            implements="g6/jobs/_care/instruments.py:licence_unsettled"),
    "answers": Rule("the answers are the person's, as given, one per item in the form's order", "caller",
                    ("responses",)),
    "fields": Rule("the text scanned is what the caller gives, every field of it", "caller", ("fields",)),
}


def register() -> dict[str, Any]:
    """The committed register, read as package data."""
    text = resources.files("g6.jobs._care").joinpath("data/source_register.json").read_text(encoding="utf-8")
    return json.loads(text)


def entries() -> dict[str, dict[str, Any]]:
    """Each register entry by its id."""
    return {e["id"]: e for e in register()["entries"]}


def problematic_phrases() -> tuple[str, ...]:
    """The phrases Mindframe's language guide marks problematic, read from the register's quoted clause (its
    Problematic column, one issue per fragment after the first). The bare word quoted in an issue's own name ("the term
    'suicide'") is the issue, not a problematic phrase."""
    fragments = entries()["mindframe-language"]["clause"].split(" [...] ")[1:]
    found = [p for fragment in fragments for p in re.findall(r"'([^']+)'", fragment)]
    return tuple(dict.fromkeys(p for p in found if p != "suicide"))


def register_problems() -> list[str]:
    """What is wrong with the register or the rule table, or ``[]``: a field missing or empty, a clause hash that
    differs from its clause, a hash that is not SHA-256, a date that is not one, or a rule naming an entry the
    register does not have."""
    out: list[str] = []
    found = {e["id"]: e for e in register()["entries"]}
    for entry_id, entry in found.items():
        missing = [f for f in FIELDS if entry.get(f) in (None, "")]
        if missing:
            out.append(f"{entry_id}: {', '.join(missing)} not filled in")
        if hashlib.sha256(str(entry.get("clause", "")).encode("utf-8")).hexdigest() != entry.get("clause_sha256"):
            out.append(f"{entry_id}: recorded clause hash differs")
        sha = str(entry.get("sha256", ""))
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            out.append(f"{entry_id}: sha256 is not a SHA-256 hex digest")
        try:
            _dt.date.fromisoformat(str(entry.get("retrieved", "")))
        except ValueError:
            out.append(f"{entry_id}: retrieved is not a date")
    for name, rule in RULES.items():
        if rule.kind != "caller" and rule.source != "g6" and rule.source not in found:
            out.append(f"rule {name}: {rule.source} is not in the register")
        if rule.kind == "own" and rule.effect not in ("refuse", "escalate", "lower_label", "note"):
            out.append(f"rule {name}: a rule of G6's own must say what it can do to an answer")
    return out


def _add_months(day: _dt.date, months: int) -> _dt.date:
    count = day.year * 12 + day.month - 1 + months
    year, month = count // 12, count % 12 + 1
    for last in (31, 30, 29, 28):
        try:
            return _dt.date(year, month, min(day.day, last))
        except ValueError:
            continue
    raise ValueError(f"no date {months} months after {day}")  # pragma: no cover - every month has a 28th


def stale_sources(today: _dt.date | None = None) -> list[dict[str, str]]:
    """A finding for each register entry retrieved longer ago than its review window."""
    today = today or _dt.date.today()
    out = []
    for entry_id, entry in sorted(entries().items()):
        due = _add_months(_dt.date.fromisoformat(entry["retrieved"]), int(entry["review_months"]))
        if today > due:
            out.append({"finding": "stale_source", "source": entry_id, "retrieved": entry["retrieved"],
                        "review_due": due.isoformat()})
    return out


__all__ = ["FIELDS", "RULES", "Rule", "entries", "problematic_phrases", "register", "register_problems",
           "stale_sources"]
