# SPDX-License-Identifier: Apache-2.0
"""Follow-up acceptance: every reply is filtered and safe fields survive."""
from __future__ import annotations

import asyncio
import json

import pytest

from g6.core import agent_guidance


@pytest.mark.parametrize("command", ["icacls folder /grant identity:F", "chmod 700 folder",
                                      "setfacl -m u:person:rwx folder", "/grant identity:F"])
def test_acl_forms_are_authority(command):
    assert agent_guidance._AUTHORITY.search(command)


@pytest.mark.parametrize("field", ["error", "degradation_reason", "warning_card", "next_actions", "repair", "result"])
def test_invoke_filters_every_refusal_and_result_surface(monkeypatch, field):
    from g6.core import door
    from g6.mcp import invoke_reply

    text = ("[G6_E_ISOLATION_UNAVAILABLE] Run icacls folder /grant identity:F. "
            "The person decides. Keep the evidence.")
    child = {"action": text, "reason": text, "step": text, "message": text}
    payload = {"ok": False, field: text if field in ("error", "degradation_reason") else [child]}
    monkeypatch.setattr(door, "invoke_checked", lambda *a, **k: payload)
    reply = invoke_reply("synthetic", "check", registry=object(), allow_sensitive=frozenset())
    encoded = json.dumps(reply)
    assert "icacls" not in encoded and "/grant" not in encoded
    assert "G6_E_ISOLATION_UNAVAILABLE" in encoded and "The person decides." in encoded
    assert "Keep the evidence." in encoded


def test_all_server_tools_use_the_filter_before_source_ticketing(tmp_path, monkeypatch):
    from fastmcp import Client
    from mcp.types import Implementation
    from g6.mcp import build_server
    from g6.mcp.cloud_consent import public_tool

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    server = build_server()

    @server.tool()
    @public_tool
    def synthetic_reply() -> dict:
        return {"ok": False, "error": "[G6_E_INPUT_VALIDATION] refused",
                "result": {"message": "Run chmod 700 folder. The person decides."},
                "next_actions": ["Set G6_REQUIRE_ISOLATION=0. Tell the person."],
                "repair": {"step": "Run g6 setup. The person decides."}}

    async def run():
        async with Client(server, client_info=Implementation(name="codex", version="test")) as client:
            result = await client.call_tool("synthetic_reply", {})
            for value in (result.structured_content, [c.model_dump() for c in result.content]):
                text = json.dumps(value)
                assert not agent_guidance._AUTHORITY.search(text)
                assert "The person decides." in text

    asyncio.run(run())


@pytest.mark.parametrize("transport", ["invoke", "mcp"])
def test_successful_chunker_result_passes_whole_with_offsets_and_digest(tmp_path, monkeypatch, transport):
    import hashlib
    import re

    from fastmcp import Client
    from mcp.types import Implementation
    from g6.core import door
    from g6.knowledge._types import Document, Passage, Source
    from g6.mcp import build_server, invoke_reply

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    text = "Make the script executable. Run chmod +x build.sh. Then run it."
    document = Document(text=text, source=Source(
        uri="file:///notes.md", sha256=hashlib.sha256(text.encode()).hexdigest(),
        retrieved_at="2026-11-17T00:00:00Z", provenance="caller_supplied"))
    params = {"document": document.model_dump(), "size": 200}
    expected = door.invoke_checked("knowledge_chunker", "chunk", params, allow=frozenset(), how_to_allow="")["result"]

    async def run():
        async def accepting(message, response_type, params, context):
            return {"value": "Fixture person"}

        async with Client(build_server(), client_info=Implementation(name="codex", version="test"),
                          elicitation_handler=accepting) as client:
            arguments = {"component": "knowledge_chunker", "op": "chunk", "params_json": json.dumps(params)}
            found = await client.call_tool("g6_invoke", arguments)
            reply = found.structured_content
            if "G6_E_CLOUD_CONSENT_REQUIRED" in json.dumps(reply):
                sources = re.search(r"Withheld source identifiers: ([^.]*)\.", json.dumps(reply))[1].split(", ")
                for source in sources:
                    await client.call_tool("g6_cloud_grant", {"source": source, "provider": "openai:codex"})
                found = await client.call_tool("g6_invoke", arguments)
                reply = found.structured_content
            # Both MCP representations must carry exactly the same result.
            assert json.loads(found.content[0].text)["result"] == reply["result"]
            return reply

    reply = (invoke_reply("knowledge_chunker", "chunk", json.dumps(params)) if transport == "invoke"
             else asyncio.run(run()))
    assert reply["ok"] is True
    assert json.dumps(reply["result"]).encode() == json.dumps(expected).encode()
    for passage in reply["result"]["passages"]:
        assert passage["text"] == text[passage["start"]:passage["end"]]
        assert passage["document_sha256"] == hashlib.sha256(text.encode()).hexdigest()
        Passage.model_validate(passage)  # recomputes the source/document/offset-bound id


def test_skill_keeps_safe_list_items_and_lines():
    text = ("- Read the fields.\n- Run g6 setup.\n- Keep the evidence.\n"
            "  Check the result.\n  Set G6_MCP_SENSITIVE=lean.\n  Tell the person.\n")
    clean = agent_guidance.skill_for_agent(text)
    for safe in ("Read the fields.", "Keep the evidence.", "Check the result.", "Tell the person."):
        assert safe in clean
    assert not agent_guidance._AUTHORITY.search(clean)


def test_a_split_command_still_removes_the_whole_fenced_block():
    text = "Keep evidence.\n\n```text\nread_fields()\ng6\nsetup\nrun()\n```\n\nTell the person.\n"
    clean = agent_guidance.skill_for_agent(text)
    assert "Keep evidence." in clean and "Tell the person." in clean
    assert "read_fields" not in clean and "run()" not in clean and "```" not in clean


def test_help_keeps_the_signature_around_an_authority_clause():
    text = agent_guidance.help_for_agent("synthetic", [
        "check(source, theorem, expected_statement) (over MCP when G6_MCP_SENSITIVE lists it). Keep evidence.",
        "query(goal, max_solutions); set G6_MCP_SENSITIVE first; returns answers.",
    ])
    for safe in ("check(source, theorem, expected_statement)", "Keep evidence.",
                 "query(goal, max_solutions)", "returns answers."):
        assert safe in text
    assert not agent_guidance._AUTHORITY.search(text)
