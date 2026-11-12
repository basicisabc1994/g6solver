# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""A row's card: every count with its denominator, and the frozen bars read against them (design 15.7, 11.2 item 4).

A result is one scored item: its ``label`` (``right``, ``wrong`` or ``unlabelled``, fixed before the run) and its
``decision`` (``accepted``, ``refused``, or one of the non-decisions ``unsupported``, ``unrun``, ``timed_out``,
``unavailable``). A non-decision is never a detection: it leaves the eligible denominators. A zero denominator makes
the card ``unavailable`` (``passed`` is None), never passed. The rates are historical rates on this scope, with exact
binomial bounds; they are not a chance of any one statement being right.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from g6.core.binomial import lower_bound, upper_bound

DECISIONS = ("accepted", "refused")
NON_DECISIONS = ("unsupported", "unrun", "timed_out", "unavailable")
ALPHA = 0.05


@dataclass(frozen=True)
class Card:
    row: str
    fragment: str
    manifest_sha256: str
    right_accepted: int
    right_refused: int
    wrong_accepted: int
    wrong_refused: int
    unsupported: int
    unrun: int
    timed_out: int
    unavailable: int
    unlabelled: int
    eligible_right: int
    eligible_wrong: int
    intervals: Mapping[str, tuple[float, float]]
    passed: bool | None
    #: Wrong items by stratum: {stratum: [accepted, refused]}; reported, and scored only as the manifest says.
    strata: Mapping[str, tuple[int, int]]
    total: int


def card(results: Iterable[Mapping[str, str]], manifest) -> Card:
    """The card of ``results`` under the manifest's frozen thresholds. ``max_wrong_acceptance`` is read on the wrong
    items of the stratum the manifest scores (``scored_stratum``; all wrong items when it names none)."""
    results = list(results)
    count = {name: 0 for name in ("right_accepted", "right_refused", "wrong_accepted", "wrong_refused",
                                  *NON_DECISIONS, "unlabelled")}
    strata: dict[str, list[int]] = {}
    scored = [0, 0]
    wanted = manifest.thresholds.get("scored_stratum_only")
    for one in results:
        label, decision = one["label"], one["decision"]
        if decision not in (*DECISIONS, *NON_DECISIONS) or label not in ("right", "wrong", "unlabelled"):
            raise ValueError(f"a result has a label and a decision the card knows: {dict(one)!r}")
        if decision in NON_DECISIONS:
            count[decision] += 1
        elif label == "unlabelled":
            count["unlabelled"] += 1
        else:
            count[f"{label}_{decision}"] += 1
            if label == "wrong":
                stratum = one.get("stratum", "")
                strata.setdefault(stratum, [0, 0])[decision == "refused"] += 1
                if not wanted or stratum == "differs_on_example":
                    scored[decision == "refused"] += 1
    eligible_right = count["right_accepted"] + count["right_refused"]
    eligible_wrong = count["wrong_accepted"] + count["wrong_refused"]
    intervals, passed = {}, None
    if eligible_right:
        intervals["right_acceptance"] = (lower_bound(count["right_accepted"], eligible_right, ALPHA),
                                         upper_bound(count["right_accepted"], eligible_right, ALPHA))
    if eligible_wrong:
        intervals["wrong_acceptance"] = (lower_bound(count["wrong_accepted"], eligible_wrong, ALPHA),
                                         upper_bound(count["wrong_accepted"], eligible_wrong, ALPHA))
    if eligible_right and sum(scored):
        passed = (count["right_accepted"] / eligible_right >= manifest.thresholds["min_right_acceptance"]
                  and scored[0] / sum(scored) <= manifest.thresholds["max_wrong_acceptance"])
    return Card(row=manifest.row, fragment=",".join(manifest.fragments), manifest_sha256=manifest.sha256,
                eligible_right=eligible_right, eligible_wrong=eligible_wrong, intervals=intervals, passed=passed,
                strata={name: (pair[0], pair[1]) for name, pair in sorted(strata.items())}, total=len(results),
                **count)


def render_markdown(card: Card) -> str:
    def rate(part: int, whole: int, name: str) -> str:
        if not whole:
            return f"{part} of 0 (no eligible case: unavailable)"
        low, high = card.intervals[name]
        return f"{part} of {whole} ({part / whole:.2f}; 95% bounds {low:.2f} to {high:.2f})"

    state = "unavailable (a denominator is zero)" if card.passed is None else "met" if card.passed else "not met"
    lines = [f"# Row: {card.row} ({card.fragment})", "", f"Manifest SHA-256: {card.manifest_sha256}", "",
             "Historical rates on this scope and these cases; not a chance that any one statement is right.", "",
             f"- Right accepted: {rate(card.right_accepted, card.eligible_right, 'right_acceptance')}",
             f"- Right refused: {card.right_refused} of {card.eligible_right}",
             f"- Wrong accepted: {rate(card.wrong_accepted, card.eligible_wrong, 'wrong_acceptance')}",
             f"- Wrong refused: {card.wrong_refused} of {card.eligible_wrong}"]
    lines += [f"- Wrong, stratum {name or 'none'}: accepted {a} of {a + r}" for name, (a, r) in card.strata.items()]
    lines += [f"- {name.replace('_', ' ').capitalize()}: {getattr(card, name)} of {card.total} (not a detection)"
              for name in (*NON_DECISIONS, "unlabelled")]
    lines += ["", f"Frozen bars: {state}."]
    return "\n".join(lines) + "\n"


__all__ = ["ALPHA", "DECISIONS", "NON_DECISIONS", "Card", "card", "render_markdown"]