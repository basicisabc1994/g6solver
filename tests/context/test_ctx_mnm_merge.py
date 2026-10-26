# SPDX-License-Identifier: Apache-2.0
import pytest

from g6.context import ContextItem, estimate_tokens


def item(key, text, score=0):
    return ContextItem(key=key, value=text, source=key, section="repository", kind="retrieved_excerpt",
                       metadata={"score": score})


def test_fusion_ranks_before_deduplication_and_keeps_provenance():
    # Removing fuse.py's sort before deduplication makes this fail (CTX_MNM-01, D1).
    from g6.context import fuse

    earlier = item("earlier", "alpha beta gamma delta epsilon zeta eta theta", 999)
    better = item("better", "alpha beta gamma delta epsilon zeta eta theta iota", -1)
    unrelated = item("unrelated", "copper pipe flow")
    result = fuse([[unrelated, earlier], [better], [better]], 100)
    assert result[0].source == "better"
    assert result[0].metadata["dropped_sources"] == ["earlier"]
    assert "earlier" not in [x.source for x in result]
    assert result[0].trust == "untrusted" and not result[0].may_instruct


def test_fusion_never_merges_passages_whose_numbers_or_negation_differ():
    # Removing fuse's `said == facts` condition on near-duplicates makes this fail (the 2 and "not" passages vanish).
    from g6.context import fuse

    four = item("pump-manual", "The pump delivers at most 4 litres per minute at full speed")
    two = item("pump-leaflet", "The pump delivers at most 2 litres per minute at full speed")
    never = item("pump-notice", "The pump does not deliver at most 4 litres per minute at full speed")
    result = fuse([[four, two, never]], 1000)
    assert sorted(x.source for x in result) == ["pump-leaflet", "pump-manual", "pump-notice"]
    assert all(x.metadata["dropped_sources"] == [] for x in result)
    same = item("pump-copy", "The pump delivers at most 4 litres per minute at full speed.")
    assert [x.metadata["dropped_sources"] for x in fuse([[four], [same]], 1000)] == [["pump-copy"]]


def test_fusion_refuses_when_the_top_passage_alone_exceeds_the_budget():
    # Removing fuse's empty-selection refusal makes this fail (it returned [] with no code).
    from g6.context import fuse

    with pytest.raises(ValueError, match="G6_E_CONTEXT_FUSION_BUDGET"):
        fuse([[item("one", "A whole passage that is longer than the budget allows.")]], 2)
    assert fuse([], 10) == []   # nothing retrieved is not a budget failure


def test_fusion_holds_budget_without_cutting_or_trusting_an_estimate():
    # Removing budget.py's ContextItem cost calculation makes this fail.
    from g6.context import fuse

    first = item("one", "A whole passage.")
    second = item("two", "A different whole passage.")
    result = fuse([[first, second]], estimate_tokens(first.value))
    assert [x.value for x in result] == [first.value]
    assert sum(estimate_tokens(x.value) for x in result) <= estimate_tokens(first.value)

def test_fusion_budget_dependency_failure_returns_no_items(monkeypatch):
    # Removing fuse's call to select_within_budget makes this fail.
    import importlib
    import pytest
    module = importlib.import_module("g6.context.fuse")
    monkeypatch.setattr(module, "select_within_budget", lambda *a: (_ for _ in ()).throw(RuntimeError("budget failed")))
    with pytest.raises(RuntimeError, match="budget failed"):
        module.fuse([[ContextItem(key="x", value="Copper conducts.", section="repository")]], 100)
