# SPDX-License-Identifier: Apache-2.0
"""The duty-of-care rule table as the certificate issuer reads it (``docs/transition/CERTIFICATES.md``, "What the
author supplies", item 2): every ``implements`` is a literal naming a top-level function; each guard's function holds
its decision and, where it has one, its threshold; the product path calls it, so the issuer's seeded removal (the body
becomes ``return None``, read as not firing) stops a firing case; and a seeded boundary defect moves the threshold.

The phrases here name ideation and intent only, never a method."""
from __future__ import annotations

import ast
import datetime as dt
import importlib
import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from g6.core import component_certificate as records
from g6.jobs._care import crisis, directory, envelope, instruments, sources
from g6.mcp import invoke_reply
from g6.workforce import component_certificate as certificates

SRC = Path(sources.__file__).resolve().parents[3]
TODAY = dt.date(2026, 10, 23)
RETRIEVED = dt.date(2026, 10, 22)
GUARDS = sorted(name for name, rule in sources.RULES.items() if rule.tier == "guard")

#: Each guard with a threshold, and how many boundary defects the issuer seeds in the function it names.
BOUNDARIES = {"crisis_trigger": 1, "directory_age": 3, "few_services": 3, "two_all_hours_services": 3,
              "numbers_checked": 1, "phq9_item9": 3, "answers_required": 2}
#: Each guard whose decision holds no comparison at a boundary and no number for a seeded boundary defect to change,
#: and why. Its certificate names the gap no_seeded_defect.
NO_BOUNDARY = {
    "crisis_lock": "a record is held for the run and subject, or none is",
    "emergency_first": "an entry is an emergency number or it is not",
    "online_service": "the directory holds an online service for the region or it does not",
    "category_fit": "a held remit matches what fired or it does not",
    "licence_settled": "a licence entry is settled or it is not",
    "help_first": "the composed response opens with the help line or it does not",
    "where_and_how": "each service line says how and when or it does not",
    "language": "the composed response holds a problematic phrase or it does not",
    "method_location": "the composed response holds a word outside G6's own and the directory's or it does not",
    "minimise_harm": "the composed response holds a word outside G6's own and the directory's or it does not",
    "no_method": "the trigger finds a listed word in the response's own words or it does not",
    "no_sensational_language": "the composed response raises its voice or names the subject in its own words or not",
    "seek_help_message": "the composed response says talking helps, with how to reach someone now, or it does not",
    "hope": "the composed response says people come through or it does not",
    "others_too": "the composed response tells someone worried about another person to call too or it does not",
}


def _implementation(rule: str) -> tuple[Any, str, str]:
    """The module, the function name and the source text the rule table names for ``rule``."""
    file, _, function = sources.RULES[rule].implements.rpartition(":")
    module = importlib.import_module(file.removesuffix(".py").replace("/", "."))
    return module, function, (SRC / file).read_text(encoding="utf-8")


def test_the_issuers_reader_reads_every_rule() -> None:
    # Fix item 1: writing any implements in sources.RULES as an expression (it was _CARE + "crisis.py:scan") fails here.
    read = records.rules_from(Path(sources.__file__))
    assert {name: rule["implements"] for name, rule in read.items()} == {
        name: rule.implements for name, rule in sources.RULES.items()}


def test_every_tiered_rule_names_a_top_level_function() -> None:
    # Fix item 2: pointing crisis_lock back at a method (it was CrisisLock.held), or removing crisis.locked, fails here.
    for name, rule in sources.RULES.items():
        if rule.tier:
            _, function, source = _implementation(name)
            assert any(isinstance(node, ast.FunctionDef) and node.name == function
                       for node in ast.parse(source).body), name


def test_each_guard_with_a_threshold_holds_it_in_the_function_it_names() -> None:
    # Fix item 3: the thresholds sat in helpers the table did not name (_pick, _scan_unit), so the issuer seeded no
    # boundary defect for them. Moving a threshold back out of its named function fails here.
    assert set(GUARDS) == set(BOUNDARIES) | set(NO_BOUNDARY) and not set(BOUNDARIES) & set(NO_BOUNDARY)
    counts = {}
    for name in GUARDS:
        _, function, source = _implementation(name)
        counts[name] = len(certificates.boundary(source, function))
    assert {name: counts[name] for name in BOUNDARIES} == BOUNDARIES
    assert [name for name in NO_BOUNDARY if counts[name]] == []


def _phq9() -> dict:
    return instruments.get("phq9")


#: For each guard with a threshold: the input that fires at it and the input that holds just inside it, given to the
#: function the rule table names.
AT_THE_THRESHOLD: dict[str, tuple[Callable[[], dict], Callable[[], dict]]] = {
    "crisis_trigger": (lambda: {"count": 1, "inside": 0}, lambda: {"count": 1, "inside": 1}),
    "directory_age": (lambda: {"age_days": 181}, lambda: {"age_days": 180}),
    "few_services": (lambda: {"named": 2}, lambda: {"named": 1}),
    "two_all_hours_services": (lambda: {"named": 1}, lambda: {"named": 2}),
    # the NHS page's own review date, 3 February 2026: the day after it, and the day itself
    "numbers_checked": (lambda: {"view": {"stale": False}, "today": dt.date(2026, 2, 4),
                                 "shown": [{"source": "nhs-when-to-call-999", "retrieved": "2026-10-22"}]},
                        lambda: {"view": {"stale": False}, "today": dt.date(2026, 2, 3),
                                 "shown": [{"source": "nhs-when-to-call-999", "retrieved": "2026-10-22"}]}),
    "phq9_item9": (lambda: {"data": _phq9(), "answers": [0] * 8 + [1]},
                   lambda: {"data": _phq9(), "answers": [0] * 9}),
    "answers_required": (lambda: {"data": _phq9(), "answers": [0] * 8},
                         lambda: {"data": _phq9(), "answers": [0] * 9}),
}


def _fired(function: Callable[..., Any], given: dict) -> bool | None:
    """Whether the guard fired on ``given``; ``None`` when the function raised (the issuer reads that as unknown)."""
    try:
        return bool(function(**given))
    except Exception:  # noqa: BLE001 - an error is no observation
        return None


def _seeded(module: Any, function: str, source: str) -> Callable[..., Any]:
    """The function from the issuer's own seeded copy of G6's source (one change inside it)."""
    namespace: dict[str, Any] = {"__name__": module.__name__}
    exec(compile(source, module.__file__, "exec"), namespace)  # noqa: S102 - G6's own source, one seeded change
    return namespace[function]


@pytest.mark.parametrize("rule", sorted(AT_THE_THRESHOLD))
def test_a_seeded_boundary_defect_moves_the_guards_threshold(rule: str) -> None:
    # Fix item 3: a case at the threshold and one just inside it catch a defect seeded at the boundary. Removing the
    # comparison from the named function (count > inside in crisis.term_fires, for one) fails here.
    module, function, source = _implementation(rule)
    fires, holds = (make() for make in AT_THE_THRESHOLD[rule])
    real = getattr(module, function)
    assert (_fired(real, fires), _fired(real, holds)) == (True, False)
    seen = [(_fired(seeded, fires), _fired(seeded, holds))
            for seeded in (_seeded(module, function, m) for m in certificates.boundary(source, function))]
    # caught: the defect changed what was observed (a defect that only raised is unknown, as the issuer reads it)
    assert [s for s in seen if None not in s and s != (True, False)], rule


def _ids() -> dict[str, str]:
    return {"run_id": f"run-{uuid.uuid4().hex[:10]}", "subject": f"person-{uuid.uuid4().hex[:8]}"}


def _invoke(op: str, **params: object) -> dict:
    return invoke_reply("duty_of_care", op, json.dumps(params))


def _locked_later() -> bool:
    ids = _ids()
    first = _invoke("screen", fields={"chat": "I want to die"}, region="AU", **ids)["result"]["code"]
    return first == envelope.CRISIS and _invoke("help", **ids)["result"].get("code") == envelope.CRISIS


def _au(categories: tuple[str, ...] = (), today: dt.date = TODAY) -> str:
    return crisis.response(directory.lookup("AU", today=today), categories, today)


def _phones(region: str, categories: tuple[str, ...] = ()) -> list[str]:
    named = crisis.named_services(directory.lookup(region, today=TODAY), categories)
    return [e["service"] for e in named if e["kind"] not in ("emergency", "online")]


def _scored(name: str, answers: list) -> str:
    try:
        return instruments.score(name, answers).kind
    except Exception as exc:  # noqa: BLE001 - what the product path does once a guard is removed
        return type(exc).__name__


#: Each response rule's change to the words composed for Australia, which only that rule's check must catch.
DOCTORED: dict[str, Callable[[str], str]] = {
    "help_first": lambda text: "\n".join([*text.split("\n")[1:], text.split("\n")[0]]),
    "where_and_how": lambda text: text.replace(" (for emergencies)", "", 1),
    "language": lambda text: text + "\nIt was political suicide.",
    "method_location": lambda text: text + "\nTuesday.",
    "minimise_harm": lambda text: text + "\nTuesday.",
    "no_method": lambda text: text + "\nI want to die.",
    "no_sensational_language": lambda text: text.replace("times like this.", "times like this!"),
    "seek_help_message": lambda text: text.replace("Talking to someone can help", "Someone can help"),
    "hope": lambda text: text.replace("People do come through times like this. ", ""),
    "others_too": lambda text: text.replace(" If you are worried about someone else, you can contact these services "
                                            "too.", ""),
}


def _refused_for(rule: str) -> Callable[[], bool]:
    def run() -> bool:
        real = crisis.response
        crisis.response = lambda *a, **k: DOCTORED[rule](real(*a, **k))
        try:
            result = crisis.crisis_result("screen", [], region="AU", locked=False, reason="r", today=TODAY)
        finally:
            crisis.response = real
        fired = [e["rule"] for e in result.evidence if e.get("kind") == "response_check"]
        return result.code == envelope.RESPONSE_REFUSED and "response" not in result.model_dump() and rule in fired
    return run


#: For each guard, its firing case on the product path (the block, the scan, the directory, the response or the
#: scorer), which must stop firing once the function the rule table names is removed.
ON_THE_PRODUCT_PATH: dict[str, Callable[[], bool]] = {
    "crisis_trigger": lambda: crisis.scan({"m": "I want to die"}).fired,
    "crisis_lock": _locked_later,
    "emergency_first": lambda: ([e["kind"] for e in directory.lookup(None, today=TODAY)["entries"]][:3]
                                == ["emergency"] * 3 and "000" in _au().split("\n")[0]),
    "directory_age": lambda: directory.lookup("AU", today=RETRIEVED + dt.timedelta(days=181))["stale"] is True,
    "few_services": lambda: len(_phones("AU")) == 2,
    "two_all_hours_services": lambda: any(g["rule"] == "two_all_hours_services"
                                          for g in crisis.response_gaps(directory.lookup("US", today=TODAY))),
    "online_service": lambda: any(e["kind"] == "online" for e in crisis.named_services(
        directory.lookup("AU", today=TODAY))),
    "category_fit": lambda: _phones("AU", ("harmed_by_someone",)) == ["Lifeline", "1800RESPECT"],
    "numbers_checked": lambda: "may have changed" in _au(today=RETRIEVED + dt.timedelta(days=181)),
    "phq9_item9": lambda: _scored("phq9", [0] * 8 + [1]) == "crisis",
    "answers_required": lambda: _scored("gad7", [0] * 6) == "refused",
    "licence_settled": lambda: _scored("k10", [1] * 10) == "refused",
    **{rule: _refused_for(rule) for rule in DOCTORED},
}


@pytest.mark.parametrize("rule", GUARDS)
def test_removing_a_guards_function_stops_its_firing_case_on_the_product_path(rule: str, monkeypatch) -> None:
    # Fix item 3: the issuer's removal makes the named function return None. Removing its call from the product path
    # (crisis.term_fires in crisis._scan_unit, crisis.locked in DutyOfCareBlock, for two) fails here.
    assert set(ON_THE_PRODUCT_PATH) == set(GUARDS)
    module, function, _ = _implementation(rule)
    assert ON_THE_PRODUCT_PATH[rule](), rule
    monkeypatch.setattr(module, function, lambda *args, **kwargs: None)
    assert not ON_THE_PRODUCT_PATH[rule](), rule


def test_every_composed_response_passes_every_check() -> None:
    # The checks the response path runs refuse nothing G6 composes: each region, none, one not covered, a directory
    # that could not be read, a stale one, and every category that can fire. A word added to crisis.response that is
    # not in crisis._OWN_WORDS fails here.
    broken = {"region": "AU", "region_covered": None, "entries": [], "note": "could not be read"}
    for today in (TODAY, RETRIEVED + dt.timedelta(days=181)):
        for view in [*(directory.lookup(r, today=today) for r in ("AU", "US", "UK", "NZ", None)), broken]:
            for categories in [(), *((c,) for c in crisis.CATEGORIES), crisis.CATEGORIES]:
                text = crisis.response(view, categories, today)
                assert crisis.response_problems(text, view) == [], (view.get("region"), categories, today)
