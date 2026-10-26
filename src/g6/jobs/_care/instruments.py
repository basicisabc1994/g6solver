# SPDX-License-Identifier: Apache-2.0
"""Scoring instruments as data (``data/instruments/*.json``) under one scorer.

The PHQ-9 and the GAD-7 ship as their own forms print them (item text, response options, the office-coding line),
under the forms' own terms: "No permission required to reproduce, translate, display or distribute." :func:`score`
adds the item scores as the form's coding line does and says "score computed": the forms print no severity bands, and
G6 gives no band, risk level or diagnosis. Any PHQ-9 item 9 answer other than 0 goes to a person, with the crisis
response and the directory, whatever the total. A missing or out-of-range answer is refused, never defaulted.

Every instrument whose licence entry is not settled (PCL-5, AUDIT, DASS-21, K10, MUST and the Barthel Index) holds the
state "unsettled" and no item text, and :func:`score` refuses it with its reason. The BDI-II and the C-SSRS are not
shipped at all.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from importlib import resources
from typing import Any

from g6.jobs._care.envelope import CRISIS, LICENCE_UNSETTLED, MISSING, OUT_OF_RANGE, UNKNOWN

#: The instruments shipped, by id; each has ``data/instruments/<id>.json``.
SHIPPED = ("phq9", "gad7", "pcl5", "audit", "dass21", "k10", "must", "barthel")


def get(name: str) -> dict[str, Any]:
    """One instrument's data file. Raises ``KeyError`` for one that is not shipped."""
    if name not in SHIPPED:
        raise KeyError(name)
    text = resources.files("g6.jobs._care").joinpath(f"data/instruments/{name}.json").read_text(encoding="utf-8")
    return json.loads(text)


def catalogue() -> list[dict[str, Any]]:
    """Each shipped instrument with its licence state: the item count and score range of a settled one; the reason
    an unsettled one is refused."""
    out = []
    for name in SHIPPED:
        data = get(name)
        row: dict[str, Any] = {"id": name, "name": data["name"], "licence_state": data["licence"]["state"]}
        if data["licence"]["state"] == "settled":
            row.update(refused=False, items=len(data["items"]), source=data["source"],
                       options=[o["value"] for o in data["options"]],
                       score_range=[data["scoring"]["minimum"], data["scoring"]["maximum"]],
                       licence_terms=data["licence"]["terms"])
        else:
            row.update(refused=True, what=data.get("what", ""), reason=data["licence"]["reason"])
        out.append(row)
    return out


@dataclass(frozen=True)
class ScoreOutcome:
    """What :func:`score` found: ``score_computed`` with the total and the item scores; ``crisis`` with the item that
    goes to a person (and no total); or ``refused`` with its code and reason."""

    kind: str
    instrument: str
    code: str = ""
    reason: str = ""
    total: int | None = None
    item_scores: tuple[int, ...] = ()
    item: int | None = None
    source: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def licence_unsettled(data: dict[str, Any]) -> bool:
    """licence_settled: True for an instrument whose licence entry is not settled, which is refused."""
    return data["licence"]["state"] != "settled"


def to_a_person(data: dict[str, Any], answers: Sequence[int | None]) -> dict[str, Any] | None:
    """phq9_item9: the form's item whose answer goes to a person (any answer other than 0, whatever the total), or
    ``None`` when no such item has one."""
    for rule in data.get("to_a_person", ()):
        index = rule["item"] - 1
        if index < len(answers) and answers[index] is not None and answers[index] != 0:
            return rule
    return None


def unanswered(data: dict[str, Any], answers: Sequence[int | None]) -> tuple[str, str] | None:
    """answers_required: the code and reason that refuse the score when an item has no answer, more answers are given
    than the form has items, or an answer is not one of the form's values; ``None`` when every item has one."""
    items, allowed = len(data["items"]), {o["value"] for o in data["options"]}
    if len(answers) > items:
        return OUT_OF_RANGE, f"{data['name']} has {items} items; {len(answers)} answers were given"
    missing = [i + 1 for i in range(items) if i >= len(answers) or answers[i] is None]
    if missing:
        return MISSING, f"{data['name']} needs an answer to every item; items {missing} have none, and none is assumed"
    wrong = [i + 1 for i, a in enumerate(answers) if type(a) is not int or a not in allowed]
    if wrong:
        return OUT_OF_RANGE, f"{data['name']} items {wrong} are not one of the form's values {sorted(allowed)}"
    return None


def score(name: str, responses: Sequence[int | None] | None) -> ScoreOutcome:
    """Score ``responses`` (one per item, in the form's order) on instrument ``name``."""
    if name not in SHIPPED:
        return ScoreOutcome("refused", name, UNKNOWN, f"{name!r} is not an instrument G6 ships; the instruments op "
                                                      "lists them")
    data = get(name)
    if licence_unsettled(data):
        return ScoreOutcome("refused", name, LICENCE_UNSETTLED, f"{data['name']} is refused: {data['licence']['reason']}")
    answers = list(responses or [])
    rule = to_a_person(data, answers)
    if rule:
        return ScoreOutcome("crisis", name, CRISIS, f"{data['name']} item {rule['item']} was answered with something "
                                                    f"other than 0: {rule['why']}", item=rule["item"],
                            source=data["source"])
    refused = unanswered(data, answers)
    if refused:
        return ScoreOutcome("refused", name, refused[0], refused[1])
    total = sum(answers)
    return ScoreOutcome("score_computed", name, total=total, item_scores=tuple(answers), source=data["source"],
                        reason=f"score computed: {data['name']} total {total} of {data['scoring']['maximum']}, the "
                               "sum of the item scores as the form's coding line adds them; the score is all G6 "
                               "gives, and a qualified person interprets it")


__all__ = ["CRISIS", "LICENCE_UNSETTLED", "MISSING", "OUT_OF_RANGE", "SHIPPED", "UNKNOWN", "ScoreOutcome",
           "catalogue", "get", "licence_unsettled", "score", "to_a_person", "unanswered"]
