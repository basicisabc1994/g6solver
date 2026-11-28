# SPDX-License-Identifier: Apache-2.0
"""Repair 3: discovery is a fallback, and no fixture sentence evades the census."""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from g6.core import selector
from g6.mcp import corpus_build
from g6.skills import corpus, routing


def _derived():
    spec = importlib.util.spec_from_file_location("_repair3_census", Path(__file__).with_name("test_split_repair.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("limit", [1, 2, 3, 5, 8])
def test_discovery_survives_every_nonempty_route_list(limit):
    reply = selector.select("summarise the error log and identify a failure mode", "diagnose", limit=limit)
    routes = [c for c in reply["candidates"] if c["kind"] == "route"]
    assert len(reply["candidates"]) <= limit
    if not routes:
        return  # A single call is not a route list.
    assert any(c["id"] == "route:discover" for c in routes)
    discovery = next(c for c in routes if c["id"] == "route:discover")
    if limit in (2, 3, 5):
        assert "fallback" in discovery["ranked_by"]
    if "fallback" in discovery["ranked_by"]:
        assert routes[-1] == discovery
    assert discovery["validated"]


def test_fallback_keeps_the_specific_review_route_when_it_took_the_last_slot():
    data = corpus.load()
    tools = [e for e in data["entries"] if e["kind"] == "tool"]
    live = {**data, "entries": [e for e in data["entries"] if e["kind"] != "route"] + corpus_build._routes(tools)}
    reply = selector.select("review my Python patch against its requirements and acceptance tests", "verify",
                            corpus=live)
    ids = [c["id"] for c in reply["candidates"]]
    assert {"route:review", "route:discover"} <= set(ids)
    assert ids[-1] == "route:discover"
    assert len(ids) == 5


def test_discovery_keeps_its_rank_when_already_offered():
    # A corpus with just these two routes gives discovery a real lexical first place.
    data = corpus.load()
    entries = [e for e in data["entries"] if e["id"] in {"route:discover", "route:workflow"}]
    reply = selector.select("g6_find g6_describe components discovery", corpus={**data, "entries": entries}, limit=2)
    assert reply["candidates"][0]["id"] == "route:discover"
    assert "fallback" not in reply["candidates"][0]["ranked_by"]


@pytest.mark.parametrize("old", [
    "When a G6 reply carries repair, follow its step; if by is person, tell the person and stop.",
    "Local settings assume a trusted host; unrestricted same-user shell access defeats that separation.",
    "G6's self-improvement loop on a folder.",
], ids=["person-repair-stop", "trusted-host", "ordinary-description"])
def test_census_rejects_loss_without_any_keyword_predicate(monkeypatch, old):
    derived = _derived()
    read_text = Path.read_text
    pattern = r"\s+".join(re.escape(w) for w in old.split())

    def changed(path, *args, **kwargs):
        text = read_text(path, *args, **kwargs)
        if path.is_relative_to(derived.GUIDE):
            return re.sub(pattern, "", text.replace("`", ""))
        return text

    monkeypatch.setattr(Path, "read_text", changed)
    caught = []
    for route_id in routing.load().routes:
        try:
            derived.test_each_route_reaches_its_original_safety_and_authority_sentences(route_id)
        except AssertionError:
            caught.append(route_id)
    assert caught, f"census accepted removal of {old}"
    if old.startswith("Local settings"):
        assert set(caught) == set(routing.load().routes)
    elif old.startswith("When a G6 reply"):
        assert "select" in caught
    else:
        assert "improve" in caught


@pytest.mark.parametrize("replacement", ["-", "A replacement nobody can reach."])
def test_reworded_exception_needs_a_named_reachable_replacement(monkeypatch, replacement):
    derived = _derived()
    table = Path(__file__).with_name("fixtures") / "using_g6_sentence_exceptions.tsv"
    read_text = Path.read_text

    def changed(path, *args, **kwargs):
        text = read_text(path, *args, **kwargs)
        if path == table:
            text += ("G6's self-improvement loop on a folder.\t"
                     "reworded-to: A test rewording must carry its replacement.\t" + replacement + "\n")
        return text

    monkeypatch.setattr(Path, "read_text", changed)
    with pytest.raises(AssertionError):
        derived.test_each_route_reaches_its_original_safety_and_authority_sentences("improve")
