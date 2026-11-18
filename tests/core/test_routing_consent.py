"""Routing consent boundaries, with synthetic arguments and no durable grants."""
import asyncio
import json

import pytest
from fastmcp import Client
from mcp.types import Implementation

from g6.core import cloud_consent as cloud, discovery, semantic, selector
from g6.core.llm import FakeLLM, LLMError
from g6.mcp import build_server


@pytest.mark.parametrize("client_name", ["codex", "claude-code"])
@pytest.mark.parametrize("tool", ["g6_find", "g6_select"])
def test_no_model_fresh_client_gets_ranked_reply(monkeypatch, client_name, tool):
    decisions = []
    refuse = cloud._refuse_unshared
    take = cloud._take

    def observe_take():
        try:
            return take()
        except Exception as exc:
            decisions.append({"state_error": str(exc), "cause": repr(exc.__cause__),
                              "path": getattr(exc.__cause__, "filename", None)})
            raise

    def observe(state, provider, refs, supplied):
        decisions.append({"provider": provider, "sources": [
            (s.source_id, s.content_sha256, s.recorded, s.source_id in supplied) for s in refs]})
        return refuse(state, provider, refs, supplied)

    monkeypatch.setattr(cloud, "_refuse_unshared", observe)
    monkeypatch.setattr(cloud, "_take", observe_take)

    async def run():
        with semantic.request(llm=None):
            async with Client(build_server(), client_info=Implementation(name=client_name, version="test")) as client:
                args = {"query": "check patch"} if tool == "g6_find" else {"task": "check patch"}
                result = await client.call_tool(tool, args)
                reply = result.structured_content or {}
                assert "cloud_consent_code" not in reply, json.dumps({"reply": reply, "trace": decisions})
                hits = result.data if tool == "g6_find" else reply["candidates"]
                assert hits, reply
                assert all("lexical" in hit["ranked_by"] for hit in hits)

    asyncio.run(run())


def test_refused_optional_send_returns_lexical_order_and_code():
    # A foreign subclass cannot establish a local or supported destination.
    # The real semantic consent preview refuses it before complete() is reached.
    class Unresolved(FakeLLM):
        pass

    llm = Unresolved(default='{"value":[]}')
    with semantic.request(llm=None):
        floor = discovery.find("check patch")
    with semantic.request(llm=llm):
        hits = discovery.find("check patch")
    assert [h["id"] for h in hits] == [h["id"] for h in floor]
    assert hits and all(h["ranked_by"] == "lexical" for h in hits)
    assert all(h["rerank_code"] == cloud.REQUIRED for h in hits)
    assert llm.calls == []


def test_allowed_local_fake_reranks_through_reader():
    with semantic.request(llm=None):
        floor = discovery.find("check patch")
    llm = FakeLLM(default=json.dumps({"value": [floor[1]["id"], floor[0]["id"]]}))
    with semantic.request(llm=llm):
        hits = discovery.find("check patch")
    assert [h["id"] for h in hits[:2]] == [floor[1]["id"], floor[0]["id"]]
    assert hits[0]["ranked_by"] == "model" and len(llm.calls) == 1


@pytest.mark.parametrize("code", [cloud.REQUIRED, cloud.STATE_UNAVAILABLE])
def test_fake_send_refusal_code_is_retained_without_exception_text(code):
    def refuse(system, user):
        raise LLMError(f"[{code}] synthetic private detail")

    with semantic.request(llm=FakeLLM(refuse)) as scope:
        hits = discovery.find("check patch")
    assert hits and all(h["ranked_by"] == "lexical" and h["rerank_code"] == code for h in hits)
    assert scope.mechanisms[-1]["code"] == code
    assert "synthetic private detail" not in json.dumps([hits, scope.mechanisms])


def test_selector_uses_shared_optional_rerank_and_reports_refusal():
    with semantic.request(llm=None):
        floor = selector.select("check patch")

    def refuse(system, user):
        raise LLMError(cloud.REQUIRED)

    with semantic.request(llm=FakeLLM(refuse)):
        reply = selector.select("check patch")
    assert reply["ok"]
    assert [c["id"] for c in reply["candidates"]] == [c["id"] for c in floor["candidates"]]
    stage = next(m for m in reply["mechanisms"] if m["stage"] == "candidates")
    assert stage["ranked_by"] == "lexical" and stage["rerank_code"] == cloud.REQUIRED


def test_selector_successful_rerank_uses_only_offered_ids():
    offered = []

    def choose(system, user):
        body = json.loads(user)
        if body["kind"] == "workflow_ranking":
            offered.extend(c["id"] for c in body["candidates"])
            return json.dumps({"value": offered[1:2] + offered[:1]})
        return json.dumps({"value": "R0" if body["kind"] == "risk" else []})

    with semantic.request(llm=FakeLLM(choose)):
        reply = selector.select("check patch")
    assert offered, reply["mechanisms"]
    assert reply["candidates"][0]["id"] == offered[1]
    assert "model" in reply["candidates"][0]["ranked_by"]
    assert "model reorder" in reply["warning_card"]["message"]


@pytest.mark.parametrize("granted", [False, True])
def test_existing_consent_decision_controls_fake_send(monkeypatch, granted):
    # A fake gate uses the real pure admission decision with in-memory recorded
    # state. No ledger, durable grant or real cloud backend is involved.
    checked = []

    class Gate:
        def __init__(self, caller):
            self.caller = caller

        def complete(self, system, user, **kwargs):
            refs = cloud.current_sources()
            checked.extend(refs)
            state = {(s.source_id, "openai:codex"): {"kind": "grant", "entry_sha256": "synthetic"}
                     for s in refs} if granted else {}
            cloud._refuse_unshared(state, "openai:codex", refs, ())
            return self.caller.complete(system, user, **kwargs)

    monkeypatch.setattr(semantic, "guard", Gate)
    with semantic.request(llm=None):
        floor = discovery.find("check patch")
    llm = FakeLLM(default=json.dumps({"value": [floor[1]["id"]]}))
    with semantic.request(llm=llm):
        hits = discovery.find("check patch")
    assert checked
    assert len(llm.calls) == int(granted)
    assert hits[0]["ranked_by"] == ("model" if granted else "lexical")
    if granted:
        assert hits[0]["id"] == floor[1]["id"]
    else:
        assert [h["id"] for h in hits] == [h["id"] for h in floor]
        assert hits[0]["rerank_code"] == cloud.REQUIRED


def test_client_request_declaration_is_admitted_without_grant():
    from g6.core.consent_declarations import TOOLS

    for tool in ("g6_find", "g6_select"):
        args = {"query": "check patch"} if tool == "g6_find" else {"task": "check patch"}
        with cloud.collect_sources() as capture:
            capture.client_arguments = args
            refs = TOOLS[tool].sources(args)
            # Exercise admission's actual pure decision, with an empty decision store.
            cloud._refuse_unshared({}, "openai:codex", refs, capture.supplied)
            cloud._refuse_unshared({}, "anthropic:claude_code", refs, capture.supplied)


def test_refused_optional_send_does_not_withhold_lexical_tool_reply():
    from g6.mcp.cloud_consent import _source_function

    def g6_find(query: str) -> list[dict]:
        return discovery.find(query)

    g6_find.__module__ = "g6.mcp"
    wrapped = _source_function(g6_find)

    def refuse(system, user):
        raise LLMError(cloud.REQUIRED)

    with cloud.collect_sources() as capture, semantic.request(llm=FakeLLM(refuse)):
        capture.client_arguments = {"query": "check patch"}
        capture.client = "openai:codex", object()
        capture.expected_tool = wrapped._g6_tool_identity
        hits = asyncio.run(wrapped(query="check patch"))
        assert hits and hits[0]["ranked_by"] == "lexical"
        assert hits[0]["rerank_code"] == cloud.REQUIRED
        # Use the real policy decision on the real wrapper's frozen result sources.
        # No ledger stub, grant, filesystem access or admission bypass is involved.
        cloud._refuse_unshared({}, "openai:codex", capture.returned[2], capture.supplied)


def test_refused_optional_read_keeps_preexisting_unknown_source():
    def refuse(system, user):
        raise LLMError(cloud.REQUIRED)

    with cloud.collect_sources() as capture, semantic.request(llm=FakeLLM(refuse)):
        cloud.record_sources((cloud.UNKNOWN,))
        assert discovery.find("check patch")[0]["ranked_by"] == "lexical"
        assert cloud.UNKNOWN in capture.sources
        with pytest.raises(LLMError, match=cloud.REQUIRED):
            cloud._refuse_unshared({}, "openai:codex", capture.sources, capture.supplied)
