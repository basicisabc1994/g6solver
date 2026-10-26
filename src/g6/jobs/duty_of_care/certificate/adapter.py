# SPDX-License-Identifier: Apache-2.0
"""Map each case and generated input to the implementing function and read what it did.

A guard's observation is 1 when it fires and 0 when it holds; G6's removal of a guard (its function's body becomes
``return None``) reads as holding. A score's observation is its total, -1 for a refusal and -2 for the crisis result,
and, when the input names an item to raise, the same for the answers with that item raised by one.

Where a guard's function is a decision the product path asks (the trigger's ``term_fires``, the directory's
``emergency_first`` and ``too_old``, the named lines' ``enough_lines``, ``fewer_lines``, ``online_service`` and
``category_fit``), the case's input goes through that path, which calls the function by name, so the observation is
what the person, the directory or the response would get. The response checks are read on the response the code
composes for the case's region, changed only as the case's ``edit`` says (lines swapped, moved or dropped; a sentence
holding the rule's own words dropped; a service line's hours or ways to reach it taken off; words added): no case
holds the response's text.
"""
import copy
import datetime as dt
import re

from g6.jobs._care import crisis, directory, instruments

REFUSED = -1
CRISIS = -2
_PHONES = ("crisis_line", "support_line")
_FORMS = {"phq9_total": "phq9", "gad7_total": "gad7"}
#: The response checks, and whether each reads the directory's view beside the text.
_CHECKS = {"help_first": True, "where_and_how": False, "language": False, "method_location": True,
           "minimise_harm": True, "no_method": True, "no_sensational_language": True, "seek_help_message": False,
           "hope": False, "others_too": False}
_NAMED = ("few_services", "online_service", "category_fit")


def _day(x):
    return dt.date.fromisoformat(x["today"])


def _region_entries(view):
    return [e for e in view["entries"] if view.get("region") is not None and e["region"] == view["region"]]


def _edited(text, edits):
    """The composed text changed as the case says, one change at a time."""
    lines = text.split("\n")
    for edit in edits:
        how, args = edit[0], edit[1:]
        services = [i for i, line in enumerate(lines) if line.startswith("- ")]
        if how == "swap":
            i, j = args
            lines[i], lines[j] = lines[j], lines[i]
        elif how == "move_last":
            lines.append(lines.pop(args[0]))
        elif how == "drop_line":
            lines.pop(args[0])
        elif how == "drop_sentence":
            words = args[0].lower()
            kept = []
            for line in lines:
                left = " ".join(s for s in re.split(r"(?<=[.?!])\s+", line) if words not in s.lower())
                if left:
                    kept.append(left)
            lines = kept
        elif how == "drop_hours":
            at = services[args[0]]
            lines[at] = re.sub(r" \([^()]*\)$", "", lines[at])
        elif how == "drop_contact":
            at = services[args[0]]
            name, _, rest = lines[at].partition(": ")
            hours = re.search(r" \([^()]*\)$", rest)
            lines[at] = name + ":" + (hours.group(0) if hours else "")
        elif how == "append":
            lines.append(args[0])
        elif how == "end":
            lines[-1] = lines[-1] + args[0]
        else:
            raise ValueError(how)
    return "\n".join(lines)


def _check(function, rule, x):
    today = _day(x)
    view = directory.lookup(x.get("region"), today=today)
    composed_for = directory.lookup(x["compose_for"], today=today) if "compose_for" in x else view
    text = _edited(crisis.response(composed_for, tuple(x.get("categories", ())), today), x["edit"])
    found = function(text, view) if _CHECKS[rule] else function(text)
    return int(bool(found))


def _named(rule, x):
    view = directory.lookup(x["region"], today=_day(x))
    categories = tuple(x.get("categories", ()))
    if rule == "two_all_hours_services":
        # The gap the response's evidence says when fewer lines than two are named for the region.
        return int(any(g["rule"] == rule and g["region"] == view["region"]
                       for g in crisis.response_gaps(view, categories)))
    named = crisis.named_services(view, categories)
    kinds = [e["kind"] for e in named]
    phones = [e for e in named if e["kind"] in _PHONES]
    if rule == "few_services":
        held = {e["service"] for e in _region_entries(view)}
        few = (kinds.count("emergency") == 1 and len(phones) <= 2 and kinds.count("online") <= 1
               and all(k in ("emergency", *_PHONES, "online") for k in kinds))
        return int(few and len({e["service"] for e in named}) < len(held))
    if rule == "online_service":
        online = [e for e in named if e["kind"] == "online"]
        return int(len(online) == 1 and online[0]["region"] == view["region"])
    if rule == "category_fit":
        if len(phones) < 2:
            return 0
        remit = set((phones[1].get("remit") or {}).get("categories", ()))
        return int(bool(remit & set(categories)))
    raise ValueError(rule)


def _held(entries, region, how):
    """The directory's own entries, the stated region's arranged as the case says (the rest left as held)."""
    code = region.upper() if region else None
    mine = [e for e in entries if code is None or e["region"] == code]
    others = [e for e in entries if not (code is None or e["region"] == code)]
    if how == "reversed":
        mine = mine[::-1]
    elif how == "emergency second":
        first = [e for e in mine if e["kind"] == "emergency"]
        rest = [e for e in mine if e["kind"] != "emergency"]
        mine = rest[:1] + first + rest[1:]
    elif how == "without the emergency number":
        mine = [e for e in mine if e["kind"] != "emergency"]
    elif how != "as held":
        raise ValueError(how)
    return others + mine


def _emergency_first(x):
    data = copy.deepcopy(directory.load())
    data["entries"] = _held(data["entries"], x.get("region"), x["held"])
    original = directory.load
    directory.load = lambda: data  # lookup reads the directory through load(); it reads this arrangement
    try:
        view = directory.lookup(x.get("region"), today=_day(x))
    finally:
        directory.load = original
    return int(bool(view["entries"]) and view["entries"][0]["kind"] == "emergency")


def _total(function, name, answers):
    found = function(name, answers)
    if found.kind == "score_computed":
        return found.total
    return CRISIS if found.kind == "crisis" else REFUSED


def evaluate(function, rule, inputs):
    x = inputs
    if rule == "crisis_trigger":
        # scan asks term_fires for each listed term it finds.
        return int(crisis.scan(x["fields"]).fired)
    if rule == "crisis_lock":
        # The runs' locks, each subject engaged in its run in turn; then the asked run and subject, as many times as
        # the case says.
        locks = {}
        for run, subject in x["engage"]:
            locks.setdefault(run, crisis.CrisisLock()).engage(subject, {"operation": "screen"})
        run, subject = x["ask"]
        seen = [function(locks, run, subject) for _ in range(x["times"])]
        return int(all(record is not None for record in seen))
    if rule == "emergency_first":
        return _emergency_first(x)
    if rule == "directory_age":
        return int(directory.lookup(x.get("region"), today=_day(x))["stale"] is True)
    if rule in _CHECKS:
        return _check(function, rule, x)
    if rule in _NAMED or rule == "two_all_hours_services":
        return _named(rule, x)
    if rule == "numbers_checked":
        today = _day(x)
        view = directory.lookup(x["region"], today=today)
        return int(function(view, crisis.named_services(view), today) is not None)
    if rule in _FORMS:
        total = _total(function, _FORMS[rule], x["answers"])
        if "raise" not in x:
            return total
        raised = list(x["answers"])
        raised[x["raise"]] = raised[x["raise"]] + 1
        return {"total": total, "raised": _total(function, _FORMS[rule], raised)}
    if rule == "phq9_item9":
        return int(function(instruments.get(x["instrument"]), x["answers"]) is not None)
    if rule == "answers_required":
        return int(function(instruments.get(x["instrument"]), x["answers"]) is not None)
    if rule == "licence_settled":
        return int(bool(function(instruments.get(x["instrument"]))))
    raise ValueError(rule)
