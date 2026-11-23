"""Family disclosure preserves the existing discovery and selector contracts."""
import asyncio
import json

import pytest
from fastmcp import Client
from mcp.types import Implementation

from g6.core import discovery, semantic, selector
from g6.mcp import build_server, registrations
from g6.skills import corpus, routing


def test_family_projection_retains_every_candidate_field_and_the_complete_selection():
    from g6.mcp.families import compact_candidates, compact_selection

    with semantic.request(llm=None):
        reply = selector.select("check patch", considering=["g6_review_patch"], limit=8)
    projected = compact_selection(reply)
    assert projected.keys() == reply.keys()
    for key in reply.keys() - {"candidates"}:
        assert projected[key] == reply[key]
    assert len(projected["candidates"]) == len(reply["candidates"])
    for actual, expected in zip(projected["candidates"], reply["candidates"], strict=True):
        assert {k: actual[k] for k in expected} == expected
        assert actual["families"]
    extension = {"component": "third_party", "score": 1.0, "runnable": False, "via": "python"}
    assert compact_candidates([extension]) == [extension]


def test_bundled_langextract_instruction_comes_from_its_own_skill():
    from g6.core.skills import front_matter, skill_text

    text = skill_text("ctx_langextract")
    assert text is not None
    front = front_matter(text)
    entry = next(e for e in corpus.build()["entries"] if e["id"] == f"skill:{front['name']}")
    assert entry["kind"] == "skill"
    assert front["description"] in entry["text"]
    assert entry["call"]["skill"] == "sources/ctx_langextract/skill/SKILL.md"
    assert entry["establishes"] == "a way of working"
    assert "no evidence" in entry["not_established"]
    assert selector._validate(entry, entry["call"]) == (True, [])


def test_every_tool_has_a_written_form_in_its_family_reference():
    from g6.mcp.families import family_for

    for server, tools in registrations.recorded().items():
        for tool in tools:
            family = family_for({"id": f"mcp:{server}/{tool}", "kind": "mcp"})
            path = registrations.package_root() / routing.REFERENCES / f"family-{family}.md"
            assert routing.forms(path.read_text(encoding="utf-8")).names(server, tool), (family, server, tool)



@pytest.mark.parametrize("limit", [1, 3, 5, 8, 100])
def test_explicit_limits_and_fields_survive_family_disclosure(limit):
    async def run():
        with semantic.request(llm=None):
            async with Client(build_server(), client_info=Implementation(name="codex", version="test")) as client:
                task = "review my patch before I say done"
                selected = await client.call_tool("g6_select", {"task": task, "limit": limit})
                found = await client.call_tool("g6_find", {"query": task, "limit": limit})
                direct = selector.select(task, limit=limit)
                reply = selected.structured_content
                assert len(reply["candidates"]) == len(direct["candidates"])
                for actual, expected in zip(reply["candidates"], direct["candidates"], strict=True):
                    assert {k: actual[k] for k in expected} == expected
                    assert actual["families"]
                for key in direct.keys() - {"candidates", "request_id", "run_id"}:
                    assert reply[key] == direct[key]
                expected = discovery.find(task, limit=limit)
                assert len(found.data) == len(expected)
                for actual, row in zip(found.data, expected, strict=True):
                    assert isinstance(actual, dict)
                    assert {k: actual[k] for k in row} == row
                    assert actual["families"]
    asyncio.run(run())


def test_family_references_keep_real_tool_written_forms():
    given = registrations.inputs()
    assert routing.check(routing.load(), **given) == []
    for surface, text in given["texts"].items():
        if "/family-" in surface:
            assert routing.forms(text).prefixed or routing.forms(text).bare or "g6_describe" in text


def test_corpus_refresh_keeps_every_previous_identity():
    import subprocess

    baseline = json.loads(subprocess.check_output(
        ["git", "show", "315c9311:src/g6/skills/corpus.json"], text=True, encoding="utf-8"))
    before = {row["id"] for row in baseline["entries"]}
    after = [row["id"] for row in corpus.entries()]
    assert before <= set(after)
    assert len(after) == len(set(after))
    assert {f"skill:{name}" for name in __import__("g6.core.skills", fromlist=["vendored"]).vendored()} <= set(after)
