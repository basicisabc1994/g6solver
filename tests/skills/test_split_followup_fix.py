# SPDX-License-Identifier: Apache-2.0
"""Pin the single priority order for discovery fallback candidates."""
from __future__ import annotations

from g6.core import selector
from g6.mcp import corpus_build
from g6.skills import corpus


def test_live_review_fallback_evicts_verify_answer_and_keeps_ranked_order():
    data = corpus.load()
    tools = [e for e in data["entries"] if e["kind"] == "tool"]
    live = {**data, "entries": [e for e in data["entries"] if e["kind"] != "route"] + corpus_build._routes(tools)}
    reply = selector.select("review my Python patch against its requirements and acceptance tests", "verify",
                            corpus=live, limit=5)
    assert [c["id"] for c in reply["candidates"]] == [
        "tool:g6_review_patch", "tool:g6_review_until_clean", "tool:g6_mission_submit",
        "route:review", "route:discover",
    ]
    assert any("replaced op:coding_agent_gate.verify_answer" in m["why"] for m in reply["mechanisms"])
    assert reply["candidates"][-1]["ranked_by"] == ["lexical", "phase_prior", "fallback"]


def _ranked_call_and_routes(monkeypatch):
    data = corpus.load()
    ranked = ["tool:g6_confirm_behaviour", "route:review", "route:question", "route:compose", "route:workflow"]
    entries = [e for e in data["entries"] if e["id"] in {*ranked, "route:discover"}]
    monkeypatch.setattr(selector, "_lexical", lambda task, documents: {
        key: 10 - index for index, key in enumerate(ranked)
    })
    return {**data, "entries": entries}, ranked


def test_fallback_keeps_call_and_top_route_and_evicts_lowest_route(monkeypatch):
    data, ranked = _ranked_call_and_routes(monkeypatch)
    reply = selector.select("ranking fixture", corpus=data, limit=5)
    assert [c["id"] for c in reply["candidates"]] == [*ranked[:-1], "route:discover"]
    assert any("replaced route:workflow" in m["why"] for m in reply["mechanisms"])


def test_two_slots_keep_best_and_discovery_before_top_route(monkeypatch):
    data, ranked = _ranked_call_and_routes(monkeypatch)
    reply = selector.select("ranking fixture", corpus=data, limit=2)
    assert [c["id"] for c in reply["candidates"]] == [ranked[0], "route:discover"]
    assert reply["next"]["id"] == ranked[0]
    assert reply["candidates"][-1]["ranked_by"] == ["fallback"]
    assert any("replaced route:review with the discovery fallback" in m["why"] for m in reply["mechanisms"])


def test_discovery_fills_second_slot_when_only_best_route_ranked(monkeypatch):
    data = corpus.load()
    entries = [e for e in data["entries"] if e["id"] in {"route:review", "route:discover"}]
    monkeypatch.setattr(selector, "_lexical", lambda task, documents: {"route:review": 10})
    reply = selector.select("ranking fixture", corpus={**data, "entries": entries}, limit=2)
    assert [c["id"] for c in reply["candidates"]] == ["route:review", "route:discover"]
    assert reply["next"]["id"] == "route:review"
    assert reply["candidates"][-1]["ranked_by"] == ["fallback"]
