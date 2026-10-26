# SPDX-License-Identifier: Apache-2.0
"""One crisis trigger for the whole library: what a person says, in every field given, before anything else.

:func:`scan` reads every field of a mapping (the person's message, a form's free text, a carer's note, or a model's own
output before it is shown), its name as well as its value, and every value joined in order, so a phrase split across
two fields is read whole. Each text is normalised (:func:`normalise`: ``g6.guards``' folding, Unicode NFKC, format
characters removed, everything but letters and digits made a space, lower case) and each term of the recall sets in
``data/crisis_rules.json`` is matched as whole words: suicidal ideation, self-harm, harm to others, a child in immediate
danger, and someone saying they are being harmed. An exception is its own rule and covers only its own words. A finding
names the category, the term and where it was found, never a risk level.

:func:`crisis_result` is the one answer to a finding: ``blocked-escalated`` with ``G6_E_DUTY_OF_CARE_CRISIS``, a
response that follows safe-messaging practice as the WHO's 2023 resource for media professionals and Mindframe's
guidelines set it out (:data:`RESPONSE_RULES`, each cited in the evidence: help first, emergency number first, a few
services, no act described, nothing the person said repeated), and the dated directory for the stated region, or every
region when none is stated. Nothing else is produced. Before it is returned, every composed response is checked against
those rules (:func:`response_problems`); one that breaks any of them, or cannot be checked, is never returned
(``G6_E_DUTY_OF_CARE_RESPONSE_REFUSED``, with the numbers).

:class:`CrisisLock` is a plain in-memory object a run holds: once engaged for a subject, :func:`locked` answers every
later op naming that run and subject with the same crisis result. It has no method that releases a subject and is never
written to disk; a call naming another run or subject is not locked (DUTY_OF_CARE-01).

Each guard in the rule table (``g6.jobs._care.sources.RULES``) names the one top-level function that decides it.

Blind spot: the trigger catches stated words. A person in crisis who does not use them, misspells them in ways not
listed, uses slang, writes in another language, uses lookalike letters NFKC does not fold, or means it without the
listed words is missed, so finding no listed words is never evidence that a person is all right.
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

from g6.guards.patterns import _fold as fold  # g6.guards' own folding, the first step of normalise
from g6.jobs._care import directory, sources
from g6.jobs._care.envelope import CHECK_FAILED, CRISIS, RESPONSE_REFUSED, CareResult, refuse

CATEGORIES = ("suicidal_ideation", "self_harm", "harm_to_others", "child_danger", "harmed_by_someone")
_PUNCTUATION = re.compile(r"[^\w\s]")
_NOT_ALNUM = re.compile(r"[\W_]+")


def rules() -> dict[str, Any]:
    """The committed recall sets, exceptions and near misses (``data/crisis_rules.json``)."""
    return _rules()


@lru_cache(maxsize=1)
def _rules() -> dict[str, Any]:
    text = resources.files("g6.jobs._care").joinpath("data/crisis_rules.json").read_text(encoding="utf-8")
    return json.loads(text)


def _drop_format(found: re.Match[str]) -> str:
    return "" if unicodedata.category(found.group()) == "Cf" else " "


def normalise(text: str) -> str:
    """``g6.guards``' folding, then Unicode NFKC, format characters (zero-width spaces and the like) removed, every
    character that is not a letter or a digit made a space, runs of spaces made one, lower case."""
    text = unicodedata.normalize("NFKC", fold(text))
    text = _PUNCTUATION.sub(_drop_format, text)
    return _NOT_ALNUM.sub(" ", text).lower().strip()


def _spaced(words: str) -> str:
    """Normalised words with two spaces between them and one at each end, so whole-word needles never overlap and
    ``str.count`` counts every occurrence."""
    return " " + words.replace(" ", "  ") + " "


@dataclass(frozen=True)
class _Phrase:
    category: str
    name: str            # the term, or the exception's id
    needle: str          # _spaced(normalise(phrase))
    words: frozenset[str]
    size: int            # number of words


def _phrase(category: str, name: str, text: str) -> _Phrase:
    norm = normalise(text)
    return _Phrase(category, name, _spaced(norm), frozenset(norm.split()), len(norm.split()))


def _self_overlaps(words: list[str]) -> bool:
    return any(words[:k] == words[-k:] for k in range(1, len(words)))


@lru_cache(maxsize=4)
def _compiled(digest: str) -> tuple[list[_Phrase], list[_Phrase], int, frozenset[str]]:
    """The terms and exceptions as needles, the most words any has, and the exceptions whose occurrences can
    overlap one another (they take the exact path)."""
    data = json.loads(digest)
    terms = [_phrase(category, entry["term"], entry["term"])
             for category in CATEGORIES for entry in data["categories"][category]["terms"]]
    exceptions = [_phrase(e["category"], e["id"], e["phrase"]) for e in data["exceptions"]]
    width = max(p.size for p in [*terms, *exceptions])
    overlapping = frozenset(e.name for e in exceptions if _self_overlaps(normalise(e.needle).split()))
    return terms, exceptions, width, overlapping


@dataclass(frozen=True)
class Match:
    """One listed term found in one field."""

    category: str
    term: str
    field: str


@dataclass(frozen=True)
class Excepted:
    """A term found only inside an exception's own words, and so not counted."""

    exception: str
    category: str
    term: str
    field: str


@dataclass(frozen=True)
class CrisisFinding:
    """What :func:`scan` found. ``fired`` is True when any listed term matched outside every exception."""

    fired: bool
    matches: tuple[Match, ...]
    excepted: tuple[Excepted, ...]
    fields_scanned: tuple[str, ...]

    def evidence(self) -> list[dict[str, Any]]:
        """Each match as an evidence entry: the category and where it was found (the label the caller gave the
        field), never the person's words and never the matched term."""
        return [{"kind": "crisis_trigger", "category": m.category, "where": m.field, "rule": "crisis_trigger"}
                for m in self.matches]


def _positions(text: str, needle: str) -> list[int]:
    found, at = [], text.find(needle)
    while at >= 0:
        found.append(at)
        at = text.find(needle, at + 1)
    return found


class _Unit:
    """One text to scan: its double-spaced normalised form, its words, and where it came from."""

    __slots__ = ("text", "words", "where", "boundary")

    def __init__(self, norm: str, where: str, boundary: int | None = None) -> None:
        self.text, self.words, self.where, self.boundary = _spaced(norm), frozenset(norm.split()), where, boundary


def term_fires(count: int, inside: int) -> bool:
    """crisis_trigger: True when a listed term occurs in a text more often than inside its exceptions' own words, so
    one occurrence outside every exception is enough."""
    return count > inside


def _scan_unit(unit: _Unit, compiled: tuple, matched: dict[str, Match], matches: list[Match],
               excepted: list[Excepted], seen: set[tuple[str, str, str]]) -> None:
    """Every term in ``unit`` outside its category's exceptions. A unit with a ``boundary`` (two fields joined) counts
    only occurrences that cross it, and only terms no single field matched."""
    terms, exceptions, _, overlapping = compiled
    for term in terms:
        if not term.words <= unit.words or (unit.boundary is not None and term.name in matched):
            continue
        count = unit.text.count(term.needle)
        if not count:
            continue
        holders = [e for e in exceptions if e.category == term.category and term.needle in e.needle
                   and e.words <= unit.words]
        if unit.boundary is None and len(holders) <= 1 and not any(h.name in overlapping for h in holders):
            # exact by counting: each occurrence of an exception holds a fixed number of the term's occurrences
            inside = {h.name: unit.text.count(h.needle) * h.needle.count(term.needle) for h in holders}
            outside = term_fires(count, sum(inside.values()))
            used = [name for name, n in inside.items() if n]
        else:
            read, held, used = _exact(unit, term, holders)
            outside = term_fires(read, held)
        for name in used:
            if (name, term.name, unit.where) not in seen:  # one per exception, term and field: bounded
                seen.add((name, term.name, unit.where))
                excepted.append(Excepted(name, term.category, term.name, unit.where))
        if outside:
            found = Match(term.category, term.name, unit.where)
            matches.append(found)
            matched.setdefault(term.name, found)


def _exact(unit: _Unit, term: _Phrase, holders: list[_Phrase]) -> tuple[int, int, list[str]]:
    """Each occurrence checked against each holder's covers, found in order by bisection: how many occurrences were
    read and how many of those sat inside an exception, stopping at the first that did not (one is enough)."""
    covers = {h.name: _positions(unit.text, h.needle) for h in holders}
    sizes = {h.name: len(h.needle) for h in holders}
    read, held, used = 0, 0, []
    for at in _positions(unit.text, term.needle):
        end = at + len(term.needle)
        if unit.boundary is not None and not (at <= unit.boundary - 2 and end >= unit.boundary + 2):
            continue  # inside one field: the field's own scan decides it
        read += 1
        inside = None
        for name, starts in covers.items():
            k = bisect.bisect_right(starts, at) - 1
            if k >= 0 and starts[k] + sizes[name] >= end:
                inside = name
                break
        if inside is None:
            return read, held, used
        held += 1
        if inside not in used:
            used.append(inside)
    return read, held, used


def scan(fields: Mapping[str, str]) -> CrisisFinding:
    """Scan every field of ``fields`` (its name and its value), and every pair of neighbouring values joined, for
    every listed term after :func:`normalise`."""
    compiled = _compiled(json.dumps(rules(), sort_keys=True))
    width = compiled[2]
    matched: dict[str, Match] = {}
    matches: list[Match] = []
    excepted: list[Excepted] = []
    seen: set[tuple[str, str, str]] = set()
    values: list[tuple[str, list[str]]] = []
    for name, value in fields.items():
        if not isinstance(value, str):
            raise TypeError(f"field {name!r} is {type(value).__name__}, not text: the crisis trigger reads text only")
        _scan_unit(_Unit(normalise(str(name)), f"{name} (name)"), compiled, matched, matches, excepted, seen)
        norm = normalise(value)
        _scan_unit(_Unit(norm, str(name)), compiled, matched, matches, excepted, seen)
        if norm:
            values.append((str(name), norm.split()))
    for (left, tail), (right, head) in zip(values, values[1:]):  # a phrase split across two fields
        before, after = " ".join(tail[-width:]), " ".join(head[:width])
        unit = _Unit(f"{before} {after}", f"{left} + {right}", boundary=len(_spaced(before)) - 1)
        _scan_unit(unit, compiled, matched, matches, excepted, seen)
    return CrisisFinding(bool(matches), tuple(matches), tuple(excepted), tuple(str(n) for n in fields))


def known_false_positives() -> tuple[str, ...]:
    """The near misses the trigger fires on, kept because missing a person costs more than a false alarm."""
    return tuple(entry["near_miss"] for body in rules()["categories"].values() for entry in body["terms"]
                 if entry["near_miss_fires"])


class CrisisLock:
    """The crisis lock a run holds, in memory only. :meth:`engage` keeps the first crisis record for a subject;
    :meth:`held` returns it. There is no way to release a subject: the lock lasts as long as the run's object."""

    __slots__ = ("_held",)

    def __init__(self) -> None:
        self._held: dict[str, dict[str, Any]] = {}

    def engage(self, subject: str, record: dict[str, Any]) -> None:
        self._held.setdefault(subject, record)

    def held(self, subject: str) -> dict[str, Any] | None:
        return self._held.get(subject)


def locked(locks: Mapping[str, CrisisLock], run_id: Any, subject: Any) -> dict[str, Any] | None:
    """crisis_lock: the crisis record the run's lock holds for ``subject``, which every later op naming that run and
    subject gives again; ``None`` when none is held (ids that are not text hold none). Nothing releases one."""
    if not (isinstance(run_id, str) and isinstance(subject, str)):
        return None
    lock = locks.get(run_id)
    return None if lock is None else lock.held(subject)


def _view(region: str | None, today: Any = None) -> dict[str, Any]:
    """The directory for ``region``; if it cannot be read, an empty view that says so (the response then says to call
    the local emergency number)."""
    try:
        return directory.lookup(region, today=today)
    except Exception as exc:  # noqa: BLE001 - a crisis answer must still go out without its directory
        return {"region": region, "region_covered": None, "entries": [], "age_days": None, "stale": None,
                "note": f"The directory could not be read ({type(exc).__name__}), so no numbers are listed. If anyone "
                        "is in danger right now, call the local emergency number."}


#: The safe-messaging rules the response follows, each with the register entry and the clause it rests on (the
#: register's own words for a withheld page)
#: (``g6.jobs._care.sources``). The guidance is written for media; G6 applies it to a one-to-one reply.
RESPONSE_RULES: tuple[tuple[str, str, str], ...] = (
    ("help_first", "who-media-2023", "Information about support services should be provided at the end of the news "
                                     "story or, even better, at the beginning of all stories about suicide."),
    ("where_and_how", "who-media-2023", "Do provide accurate information about where and how to seek help for "
                                        "suicidal thoughts and suicidal crises."),
    ("few_services", "who-media-2023", "Only a limited number of services should be mentioned"),
    ("two_all_hours_services", "mindframe-help-seeking", "name two support services that are open around the "
                                                         "clock, every day of the week"),
    ("online_service", "mindframe-help-seeking", "list at least one support service or forum that works online"),
    ("category_fit", "mindframe-help-seeking", "match the services named to the content, for instance its demographic "
                                               "group, its state, or its cultural diversity"),
    ("numbers_checked", "who-media-2023", "The contact details of listed services should be checked regularly to "
                                          "ensure that they are accurate."),
    ("no_method", "who-media-2023", "Don’t describe the method used."),
    ("method_location", "mindframe-method-location", "Any communication that touches on suicide should keep detail of "
                                                     "the method and the place to a minimum."),
    ("language", "mindframe-language", "Some wording can do harm when suicide is discussed."),
    ("no_sensational_language", "who-media-2023", "Don’t use language/content which sensationalizes, romanticizes "
                                                  "or normalizes suicide, or that presents it as a viable solution to "
                                                  "problems."),
    ("seek_help_message", "who-media-2023", "to include the message that people who are suicidal should seek help, to "
                                            "indicate how they can access that help"),
    ("hope", "who-media-2023", "stories of overcoming a suicidal crisis can lead to fewer suicides"),
    ("others_too", "who-media-2023", "It is also helpful to report stories about how some people have reached out to "
                                     "support others in crisis"),
    ("minimise_harm", "mindframe-guidelines", "Where self-harm is discussed, the guidelines ask that the Mindframe "
                                              "guidance and the media's own codes of practice be applied, so "
                                              "that audiences are harmed as little as possible."),
)
_THE ={"AU": "Australia", "US": "the United States", "UK": "the United Kingdom"}


#: How many services open at all hours the response names a region, after its emergency number (Mindframe's "two").
ALL_HOURS_SERVICES = 2
_PHONE_KINDS = ("crisis_line", "support_line")  # crisis lines first, then support lines, each in directory order


def _by_region(entries: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Each region's services in directory order, entries of the same service merged (Lifeline's call and text)."""
    regions: dict[str, dict[str, dict[str, Any]]] = {}
    for e in entries:
        services = regions.setdefault(e["region"], {})
        if e["service"] in services:
            services[e["service"]]["contacts"] = [*services[e["service"]]["contacts"], *e["contacts"]]
        else:
            services[e["service"]] = {**e, "contacts": list(e["contacts"])}
    return {region: list(services.values()) for region, services in regions.items()}


def enough_lines(named: int) -> bool:
    """few_services: True once :data:`ALL_HOURS_SERVICES` lines open at all hours are named for a region, so no more
    are; the rest stay in the directory beside the response."""
    return named >= ALL_HOURS_SERVICES


def fewer_lines(named: int) -> bool:
    """two_all_hours_services: True when fewer lines open at all hours than :data:`ALL_HOURS_SERVICES` are named for a
    region, because the directory holds fewer; the evidence then says so."""
    return named < ALL_HOURS_SERVICES


def category_fit(lines: list[dict[str, Any]], categories: tuple[str, ...], first: list[dict[str, Any]]
                 ) -> dict[str, Any] | None:
    """category_fit: the line open at all hours, other than the ``first`` named, whose own held clause states a remit
    matching what fired; it takes the second place. ``None`` when none fits."""
    return next((s for s in lines if set((s.get("remit") or {}).get("categories", ())) & set(categories)
                 and s not in first), None)


def online_service(services: list[dict[str, Any]]) -> dict[str, Any] | None:
    """online_service: the region's one online service the directory holds with a source; ``None`` when it holds
    none, which the evidence then says (none is invented)."""
    return next((s for s in services if s["kind"] == "online"), None)


def _pick(services: list[dict[str, Any]], categories: tuple[str, ...] = ()
          ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    emergency = [s for s in services if directory.emergency_first(s)][:1]
    lines = sorted((s for s in services if s["kind"] in _PHONE_KINDS and s.get("all_hours")),
                   key=lambda s: _PHONE_KINDS.index(s["kind"]))
    phones: list[dict[str, Any]] = []
    for line in lines:
        if enough_lines(len(phones)):
            break
        phones.append(line)
    fit = category_fit(lines, categories, phones[:1])
    if fit is not None:
        phones = [*phones[:1], fit][:ALL_HOURS_SERVICES]
    online = online_service(services)
    return emergency, phones, [] if online is None else [online]


def _covered(view: dict[str, Any]) -> bool:
    return view.get("region") is not None and bool(view.get("region_covered"))


def named_services(view: dict[str, Any], categories: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """The services the response names for a covered region (few_services): the emergency number first (not
    counted), then two services open at all hours where the directory holds them (two_all_hours_services), the second
    one fitting what fired where a held clause supports it (category_fit), then one online service where it holds one
    (online_service). An unstated or uncovered region gets none: no other country's number is named as its own."""
    if not _covered(view):
        return []
    return [s for services in _by_region(view["entries"]).values() for part in _pick(services, categories)
            for s in part]


def response_gaps(view: dict[str, Any], categories: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """What the response could not name because the directory does not hold it, region by region, said plainly."""
    gaps = []
    for region, services in (_by_region(view["entries"]).items() if _covered(view) else ()):
        _, phones, online = _pick(services, categories)
        name = _THE.get(region, region)
        if fewer_lines(len(phones)):
            gaps.append({"kind": "safe_messaging_gap", "rule": "two_all_hours_services", "region": region,
                         "note": f"the directory holds {len(phones)} service{'s' if len(phones) != 1 else ''} open at "
                                 f"all hours for {name}, not two"})
        if not online:
            gaps.append({"kind": "safe_messaging_gap", "rule": "online_service", "region": region,
                         "note": f"the directory holds no online support service with a source for {name}; none is "
                                 "invented"})
    return gaps


def problematic_language(text: str) -> list[str]:
    """language: each phrase Mindframe's language guide marks problematic that ``text`` uses, as whole words after
    folding."""
    spaced = _spaced(normalise(text))
    return [p for p in sources.problematic_phrases() if _spaced(normalise(p)) in spaced]


def _reach(contact: dict[str, Any]) -> str:
    if contact["how"] == "chat online":
        return f"chat online at {contact['address']}"
    return f"{contact['how']} {contact['number']}"


_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December")


def stale_line(view: dict[str, Any], shown: list[dict[str, Any]], today: dt.date | None = None) -> str | None:
    """numbers_checked: one plain line when the numbers shown may have changed: the directory is past its 180 days,
    or a shown number's source is past its review window or its own recorded review date. ``None`` when nothing shown
    is stale. A register that cannot be read counts as stale."""
    if not shown:
        return None
    today = today or dt.datetime.now(dt.timezone.utc).date()
    stale = bool(view.get("stale"))
    try:
        held = sources.entries()
        late = {f["source"] for f in sources.stale_sources(today)}
        for service in shown:
            due = held[service["source"]].get("source_review_due")
            stale = stale or service["source"] in late or bool(due and today > dt.date.fromisoformat(due))
    except Exception:  # noqa: BLE001 - G4: a register that cannot be read cannot say the numbers are current
        stale = True
    if not stale:
        return None
    day = min(dt.date.fromisoformat(s["retrieved"]) for s in shown)
    return f"These numbers were last checked on {day.day} {_MONTHS[day.month - 1]} {day.year} and may have changed."


def response(view: dict[str, Any], categories: tuple[str, ...] = (), today: dt.date | None = None) -> str:
    """The words for the person (RESPONSE_RULES): where to get help first, the region's emergency number first; a
    few services, each with how and when to reach it, the second fitting what fired where a held clause supports it;
    no act described and nothing the person said repeated; that talking to someone helps and people come through; and
    that someone worried about another person can call too. For an unstated or uncovered region it names no number:
    it says to call the local emergency number and a local crisis line, and asks which country the person is in."""
    first = "If anyone's life is in danger right now, call your local emergency number now."
    if not _covered(view):
        held = [_THE.get(r, r) for r in dict.fromkeys(e["region"] for e in view["entries"])]
        ask = "Which country are you in?"
        if held:
            ask += f" Crisis lines are held here for {', '.join(held[:-1])}{' and ' if held[:-1] else ''}{held[-1]}."
        lines = [first, "You do not have to go through this alone. Talking to someone can help: please contact a "
                        "local crisis line now.", ask,
                 "People do come through times like this. If you are worried about someone else, you can contact a "
                 "local crisis line too."]
        return "\n".join(lines)
    services = named_services(view, categories)
    emergency = [e for e in services if directory.emergency_first(e)]
    if emergency:
        first = f"If anyone's life is in danger right now, call {emergency[0]['service']} now."
    lines = [first, "You do not have to go through this alone. Talking to someone can help"
             + (", and you can reach someone now:" if services else ".")]
    for e in services:
        lines.append(f"- {e['service']}: {' or '.join(_reach(c) for c in e['contacts'])} ({e['hours']})")
    stale = stale_line(view, services, today)
    if stale:
        lines.append(stale)
    lines.append("People do come through times like this. If you are worried about someone else, you can contact "
                 "these services too.")
    return "\n".join(lines)


#: Every word the response's own lines use, the regions it names and the months of its dates, kept in step with
#: :func:`response` and :func:`stale_line`. A word they add that is not here fails method_location, so a new word in
#: the response is a reviewed change.
_OWN_WORDS = frozenset(normalise(" ".join((
    "If anyone's life is in danger right now, call your local emergency number now.",
    "You do not have to go through this alone. Talking to someone can help, and you can reach someone now:",
    "please contact a local crisis line now. Which country are you in? Crisis lines are held here for",
    "call text or chat online at",
    "These numbers were last checked on and may have changed.",
    "People do come through times like this. If you are worried about someone else, you can contact these services",
    "a local crisis line too.", *_THE.values(), *_MONTHS))).split())
#: How a named service is reached, as :func:`response` writes it: call or text and a number, or chat online at an
#: address; then when, in brackets.
_WAY = r"(?:(?:call|text) \d(?:[\d ]*\d)?|chat online at https://\S+)"
_SERVICE_LINE = re.compile(rf"- [^\n]+?: {_WAY}(?: or {_WAY})* \([^()\n]+\)")


def _directory_words(view: dict[str, Any]) -> set[str]:
    """The directory's own words for each entry the view holds: its service, hours, contacts and retrieval date."""
    return {word for e in view.get("entries") or () for word in normalise(" ".join(
        [e["service"], e["hours"], e["retrieved"], *(str(v) for c in e["contacts"] for v in c.values())])).split()}


def _own_text(text: str, view: dict[str, Any]) -> str:
    """``text`` with the name of each service the view holds taken out: the response's own words."""
    for name in sorted({e["service"] for e in view.get("entries") or ()}, key=len, reverse=True):
        text = text.replace(name, " ")
    return text


def help_not_first(text: str, view: dict[str, Any]) -> bool:
    """help_first: True when the response does not open with where to get help now: a first line that says to call
    now, naming the region's emergency number when the response names one."""
    first = text.split("\n", 1)[0].lower()
    emergency = [s for s in named_services(view) if directory.emergency_first(s)]
    where = emergency[0]["service"] if emergency else "your local emergency number"
    return not first.startswith("if anyone's life is in danger right now, call") or where.lower() not in first


def unreachable(text: str) -> list[str]:
    """where_and_how: each line naming a service that does not say how to reach it (call or text and the number, or
    the address to chat online at) and when (its hours, in brackets)."""
    return [line for line in text.split("\n") if line.startswith("- ") and not _SERVICE_LINE.fullmatch(line)]


def outside_words(text: str, view: dict[str, Any]) -> list[str]:
    """method_location and minimise_harm: each word of the response that is neither one of G6's own (the words its
    lines use, reviewed with them) nor the directory's own for an entry the view holds. So the response gives no
    detail of any method, place or act, and the same words serve every category."""
    held = _OWN_WORDS | _directory_words(view)
    return [word for word in dict.fromkeys(normalise(text).split()) if word not in held]


def listed_words(text: str, view: dict[str, Any]) -> list[str]:
    """no_method: each listed term the crisis trigger finds in the response's own words (the services' names taken
    out), and each category it names: so it describes no act, repeats nothing the person said and does not say what
    the trigger called it."""
    own = _own_text(text, view)
    spaced = _spaced(normalise(own))
    return [*(m.term for m in scan({"response": own}).matches),
            *(c for c in CATEGORIES if _spaced(normalise(c.replace("_", " "))) in spaced)]


def sensational(text: str, view: dict[str, Any]) -> bool:
    """no_sensational_language: True when the response raises its voice (an exclamation mark) or names suicide in its
    own words (the services' names taken out): words that never name it cannot sensationalise it or present it as a
    solution."""
    own = _spaced(normalise(_own_text(text, view)))
    return "!" in text or any(_spaced(word) in own for word in ("suicide", "suicidal"))


def no_seek_help(text: str) -> bool:
    """seek_help_message: True when the response does not say that talking to someone can help, with how to reach
    someone now (the services it names, or a local crisis line)."""
    low = text.lower()
    return "talking to someone can help" not in low or not ("you can reach someone now" in low
                                                            or "contact a local crisis line now" in low)


def no_hope(text: str) -> bool:
    """hope: True when the response does not say that people do come through times like this."""
    return "people do come through" not in text.lower()


def no_others(text: str) -> bool:
    """others_too: True when the response does not tell someone worried about another person that they can contact
    the services too."""
    return "if you are worried about someone else, you can contact" not in text.lower()


def response_problems(text: str, view: dict[str, Any]) -> list[str]:
    """The rules a composed response breaks, by name, or ``[]``: every response is checked by each of these before it
    is returned (:func:`crisis_result`, :func:`check_failed`)."""
    found = {"help_first": help_not_first(text, view), "where_and_how": unreachable(text),
             "language": problematic_language(text), "method_location": outside_words(text, view),
             "minimise_harm": outside_words(text, view), "no_method": listed_words(text, view),
             "no_sensational_language": sensational(text, view), "seek_help_message": no_seek_help(text),
             "hope": no_hope(text), "others_too": no_others(text)}
    return [rule for rule, hit in found.items() if hit]


def _cited() -> list[dict[str, Any]]:
    return [{"kind": "safe_messaging", "rule": rule, "source": source, "clause": clause}
            for rule, source, clause in RESPONSE_RULES]


def _answer(operation: str, code: str, reason: str, *, view: dict[str, Any], evidence: list[dict[str, Any]],
            categories: tuple[str, ...] = (), today: Any = None, request_id: str = "", run_id: str = "",
            **fields: Any) -> CareResult:
    """The answer with its response, once every check of :func:`response_problems` has run and none has fired. A
    composed response that breaks a rule, or that cannot be checked, is never returned: the answer carries the numbers
    and no response, with ``RESPONSE_REFUSED`` (a check-failed answer keeps its own code: that the trigger could not
    run comes first)."""
    try:
        text = response(view, categories, today)
        broken = response_problems(text, view)
        why = f"they break the rules {', '.join(broken)}"
        checks = [{"kind": "response_check", "rule": rule, "fired": True} for rule in broken]
    except Exception as exc:  # noqa: BLE001 - G4: a response that cannot be checked is never shown
        text, why = "", f"their checks could not run ({type(exc).__name__})"
        checks = [{"kind": "response_check", "ran": False, "error": type(exc).__name__}]
    if not checks:
        return refuse(operation, code, reason, evidence=evidence, request_id=request_id, run_id=run_id,
                      response=text, directory=view, **fields)
    withheld = (f"The words composed for the person are not shown: {why}. If anyone is in danger right now, call the "
                "local emergency number, and pass the conversation to a person now.")
    return refuse(operation, code if code == CHECK_FAILED else RESPONSE_REFUSED, f"{reason} {withheld}",
                  evidence=[*evidence, *checks], request_id=request_id, run_id=run_id, directory=view, **fields)


def crisis_result(operation: str, evidence: list[dict[str, Any]], *, region: str | None, locked: bool, reason: str,
                  request_id: str = "", run_id: str = "", today: Any = None) -> CareResult:
    """The one answer to a crisis finding. Nothing else is produced. The categories that fired choose the second
    service where a held clause supports it; a stale directory says so on the card as well as in the response."""
    view = _view(region, today)
    categories = tuple(dict.fromkeys(e["category"] for e in evidence if e.get("kind") == "crisis_trigger"))
    stale = stale_line(view, named_services(view, categories), today)
    return _answer(operation, CRISIS, f"{reason} {stale}" if stale else reason, view=view, categories=categories,
                   today=today, evidence=[*evidence, *_cited(), *response_gaps(view, categories)],
                   request_id=request_id, run_id=run_id, locked=locked)


def check_failed(operation: str, error: BaseException, *, region: str | None, request_id: str = "",
                 run_id: str = "") -> CareResult:
    """The crisis check could not run: blocked, with the directory, as a possible crisis. The reason names the kind
    of failure only, never anything from the input."""
    view = _view(region)
    return _answer(operation, CHECK_FAILED, f"The crisis check could not run ({type(error).__name__}), so this is "
                                            "treated as a possible crisis and goes to a person.",
                   view=view, evidence=_cited(), request_id=request_id, run_id=run_id)


__all__ = ["ALL_HOURS_SERVICES", "CATEGORIES", "RESPONSE_RULES", "CrisisFinding", "CrisisLock", "Excepted", "Match",
           "category_fit", "check_failed", "crisis_result", "enough_lines", "fewer_lines", "help_not_first",
           "known_false_positives", "listed_words", "locked", "named_services", "no_hope", "no_others", "no_seek_help",
           "normalise", "online_service", "outside_words", "problematic_language", "response", "response_gaps",
           "response_problems", "rules", "scan", "sensational", "stale_line", "term_fires", "unreachable"]
