"""Ranker contracts over a small explicit corpus, independent of generated-artifact freshness."""
import json

import pytest

from g6.core import corpus_ranking as ranking, semantic
from g6.core.llm import FakeLLM


def corpus():
    return {"components": {}, "entries": [
        {"id": "op:example.check", "kind": "op", "component": "example", "text": "check a patch",
         "establishes": "a check", "call": {"tool": "g6_invoke"}},
        {"id": "skill:review", "kind": "skill", "text": "check a patch", "establishes": "a check"},
        {"id": "tool:review", "kind": "tool", "text": "check a patch", "establishes": "a check"},
        {"id": "route:review", "kind": "route", "text": "check a patch", "establishes": "a check"},
    ]}


def test_equal_evidence_keeps_g6_tool_above_working_method_and_ops():
    data = corpus()
    # Full op schema expansion is a corpus responsibility, so use non-op entries for lexical unit tests.
    data["entries"] = [e for e in data["entries"] if e["kind"] != "op"]
    assert ranking.rank("check patch", data).order[0] == "tool:review"


@pytest.mark.parametrize("reply", ['{"value":["absent"]}', 'broken', '{"value":["tool:review","tool:review"]}'])
def test_invalid_model_order_preserves_floor(reply):
    data = corpus()
    data["entries"] = [e for e in data["entries"] if e["kind"] != "op"]
    floor = ranking.rank("check patch", data)
    with semantic.request(llm=FakeLLM(default=reply)):
        result = ranking.rank("check patch", data, model=True)
    assert result == floor and result.ranked_by == "lexical"


def test_valid_model_order_is_single_closed_candidate_send():
    data = corpus()
    data["entries"] = [e for e in data["entries"] if e["kind"] != "op"]
    llm = FakeLLM(default=json.dumps({"value": ["skill:review", "tool:review"]}))
    with semantic.request(llm=llm):
        result = ranking.rank("check patch", data, model=True)
    assert result.order[:2] == ["skill:review", "tool:review"] and result.ranked_by == "model"
    assert len(llm.calls) == 1


def test_english_inflections_share_evidence():
    assert ranking.lexical("documents errors", {"x": "document error"}) == {"x": 1.0}


def test_query_coverage_floor_includes_unknown_words():
    data = corpus()
    data["entries"] = [e for e in data["entries"] if e["kind"] != "op"]
    assert not ranking.rank("patch quux blorf zyzzyva abracadabra", data, floor=ranking.FIND_FLOOR).order


def test_route_precedes_component_op_on_equal_evidence():
    data = corpus()
    data["entries"][0]["sensitive"] = False
    data["components"] = {"example": {"text": "", "guarantees": ["a check"], "bad_at": [],
                                       "needs": {}, "mitigates": [], "fields": []}}
    result = ranking.rank("check patch", data)
    assert result.order.index("route:review") < result.order.index("op:example.check")
