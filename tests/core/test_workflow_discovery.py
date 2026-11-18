"""Development routing examples and closed-candidate model ordering, never real models."""
import asyncio
import json

import pytest

from g6.core import discovery, semantic
from g6.core.llm import FakeLLM


DEV = [
    ("I finished an AI coding task. Can you check if the patch truly works?", {"g6_review_patch", "g6_review_until_clean"}),
    ("Check the changes in my Python repo before I merge them", {"g6_review_patch", "g6_review_until_clean"}),
    ("I want my coding agent to learn from past mistakes", {"metaharness_start", "g6_improve_start"}),
    ("Improve my coding agent each time it makes an error", {"metaharness_start", "g6_improve_start"}),
    ("Can you analyse whether this invoice claim is backed by an official document?", {"g6_check_claim", "g6_knowledge_check", "g6_invoke"}),
    ("Should an agent send this email to customers?", {"g6_invoke"}),
    ("Can you search my local documents semantically?", {"g6_knowledge_search", "g6_knowledge_context", "g6_invoke"}),
]


@pytest.mark.parametrize("query,tools", DEV)
def test_development_workflows_lexical(query, tools):
    with semantic.request(llm=None):
        hits = discovery.find(query)
    assert hits and hits[0].get("tool") in tools, hits[:1]
    if "invoice" in query:
        assert hits[0]["component"] == "grounding"
    if "email" in query:
        assert hits[0]["component"] == "action_gating"
    if "semantically" in query:
        assert hits[0]["component"] != "australian_legal_corpus"
    assert hits[0]["ranked_by"] == "lexical"
    assert hits[0]["call"]


@pytest.mark.parametrize("query,tools", DEV)
def test_development_workflows_model(query, tools):
    with semantic.request(llm=None):
        floor = discovery.find(query)
    assert floor and floor[0].get("tool") in tools
    llm = FakeLLM(default=json.dumps({"value": [floor[0]["id"]]}))
    with semantic.request(llm=llm):
        hits = discovery.find(query)
    assert hits[0]["id"] == floor[0]["id"]
    assert hits[0]["ranked_by"] == "model"
    assert len(llm.calls) == 1


@pytest.mark.parametrize("reply", ['{"value":["tool:absent"]}', 'broken json', '{"value":["tool:g6_review_patch","tool:g6_review_patch"]}'])
def test_bad_rerank_keeps_lexical(reply):
    with semantic.request(llm=None):
        floor = discovery.find(DEV[0][0])
    assert floor[0]["ranked_by"] == "lexical"
    with semantic.request(llm=FakeLLM(default=reply)):
        hits = discovery.find(DEV[0][0])
    assert hits == floor


def test_raise_keeps_lexical():
    def broken(system, user):
        raise RuntimeError("offline")

    with semantic.request(llm=None):
        floor = discovery.find(DEV[0][0])
    assert floor[0]["ranked_by"] == "lexical"
    llm = FakeLLM(broken)
    with semantic.request(llm=llm):
        assert discovery.find(DEV[0][0]) == floor
    assert len(llm.calls) == 1


def test_model_can_only_reorder_offered_ids():
    with semantic.request(llm=None):
        floor = discovery.find(DEV[0][0])
    llm = FakeLLM(default=json.dumps({"value": [floor[1]["id"], floor[0]["id"]]}))
    with semantic.request(llm=llm):
        hits = discovery.find(DEV[0][0])
    assert [h["id"] for h in hits[:2]] == [floor[1]["id"], floor[0]["id"]]
    offered = json.loads(llm.calls[0][1])["candidates"]
    assert {h["id"] for h in hits} <= {c["id"] for c in offered}


def test_abstention_and_determinism():
    with semantic.request(llm=None):
        assert discovery.find("zzzz qqqq") == []
        assert discovery.find("Please recommend a delicious recipe for banana custard") == []
        assert discovery.find(DEV[0][0]) == discovery.find(DEV[0][0])


def test_names_stays_component_only_without_intent_override():
    hits = discovery.find("improve classification", names=["grounding"])
    assert all(h["component"] == "grounding" and "tool" not in h for h in hits)


@pytest.mark.parametrize("client_name", ["codex", "claude-code"])
def test_fresh_mcp_client_can_find_workflow_without_grant(client_name):
    from fastmcp import Client
    from mcp.types import Implementation
    from g6.mcp import build_server

    async def run():
        with semantic.request(llm=None):
            async with Client(build_server(), client_info=Implementation(name=client_name, version="test")) as client:
                result = await client.call_tool("g6_find", {"query": DEV[0][0]})
                assert not (result.structured_content or {}).get("error"), result.structured_content
                return result.data

    hits = asyncio.run(run())
    assert isinstance(hits, list) and hits[0].get("tool") in DEV[0][1]


def test_unknown_client_remains_withheld_without_a_source_grant():
    from fastmcp import Client
    from g6.mcp import build_server

    async def run():
        with semantic.request(llm=None):
            async with Client(build_server()) as client:
                result = await client.call_tool("g6_find", {"query": DEV[0][0]})
                return result.structured_content

    reply = asyncio.run(run())
    assert reply["ok"] is False and reply["warning_card"]["code"] == "G6_E_CLOUD_CONSENT_REQUIRED"


def test_route_hit_keeps_its_load_instruction_without_inventing_a_tool(monkeypatch):
    from g6.skills import corpus

    data = {"components": {}, "entries": [{"id": "route:context", "kind": "route",
            "text": "local document search", "establishes": "an instruction",
            "call": {"skill": "using-g6", "section": "context"}}]}
    monkeypatch.setattr(corpus, "load", lambda: data)
    with semantic.request(llm=None):
        hit = discovery.find("local document search")[0]
    assert "tool" in hit and hit["tool"] is None
    assert hit["call"] == data["entries"][0]["call"]
    assert hit["runnable"] is False and hit["via"] == "skill"
