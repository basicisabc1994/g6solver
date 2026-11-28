# SPDX-License-Identifier: Apache-2.0
"""Full-pool priority must retain reservations before spending remaining slots."""
from __future__ import annotations

import pytest

from g6.core import selector
from g6.skills import corpus


def _ranked(monkeypatch, *, routes=True):
    data = corpus.load()
    ranked = ["tool:g6_review_patch", "tool:g6_review_until_clean", "tool:g6_premortem",
              "tool:g6_confirm_behaviour", "tool:g6_find"]
    if routes:
        ranked += ["route:review"]
    entries = [e for e in data["entries"] if e["id"] in {*ranked, "route:discover"}]
    kinds = {e["id"]: e["kind"] for e in entries}
    monkeypatch.setattr(selector, "_lexical", lambda task, documents: {
        key: (1000 - 100 * i) / (selector.LEXICAL_WEIGHT * (selector.ROUTE_WEIGHT if kinds[key] == "route" else 1))
        for i, key in enumerate(ranked)
    })
    return {**data, "entries": entries}


@pytest.mark.parametrize("limit,expected", [
    (1, ["tool:g6_find"]),
    (2, ["tool:g6_confirm_behaviour", "tool:g6_find"]),
    (3, ["tool:g6_review_patch", "tool:g6_confirm_behaviour", "tool:g6_find"]),
    (4, ["tool:g6_review_patch", "tool:g6_confirm_behaviour", "tool:g6_find", "route:discover"]),
    (5, ["tool:g6_review_patch", "tool:g6_confirm_behaviour", "tool:g6_find", "route:review", "route:discover"]),
])
def test_considered_calls_reserve_slots_before_rank_and_discovery(monkeypatch, limit, expected):
    reply = selector.select("ranking fixture", corpus=_ranked(monkeypatch), limit=limit,
                            considering=["g6_find", "g6_confirm_behaviour", "g6_find"])
    assert [c["id"] for c in reply["candidates"]] == expected


def test_scheduler_reserves_before_considered_calls(monkeypatch):
    reply = selector.select("ranking fixture", corpus=_ranked(monkeypatch), limit=1, considering=[
        "g6_find", {"hypothesis": "behaviour is ambiguous", "id": "intent", "severity": "high",
                    "predicts": "a question resolves it", "check": "g6_confirm_behaviour"},
    ])
    assert [c["id"] for c in reply["candidates"]] == ["tool:g6_confirm_behaviour"]
    assert reply["next"]["id"] == "tool:g6_confirm_behaviour"
    assert "scheduler" in reply["candidates"][0]["ranked_by"]


def test_no_scored_route_means_no_discovery_fallback(monkeypatch):
    reply = selector.select("ranking fixture", corpus=_ranked(monkeypatch, routes=False), limit=2)
    assert [c["id"] for c in reply["candidates"]] == ["tool:g6_review_patch", "tool:g6_review_until_clean"]


def test_remedy_reserves_before_scheduler_and_considered_calls(monkeypatch):
    reply = selector.select("ranking fixture", corpus=_ranked(monkeypatch), limit=1,
                            last_outcome="G6_E_UNKNOWN_COMPONENT", considering=["g6_review_patch", {
                                "hypothesis": "behaviour is ambiguous", "id": "intent", "severity": "high",
                                "predicts": "a question resolves it", "check": "g6_confirm_behaviour"}])
    assert [c["id"] for c in reply["candidates"]] == ["tool:g6_find"]
    assert reply["candidates"][0]["ranked_by"] == ["remedy"]
