# SPDX-License-Identifier: Apache-2.0
"""The Context Engine over MCP, with a real in-process client: only the person's answer in the client's dialog indexes
or shares anything, and nothing of a source reaches a client whose provider holds no grant for it."""
from __future__ import annotations

import asyncio
import json

import pytest
from fastmcp import Client
from fastmcp.client.elicitation import ElicitResult
from mcp.types import Implementation

from g6.core import cloud_consent as cloud
from g6.core.consent_declarations import TOOL_ARGUMENTS, TOOLS
from g6.knowledge import _engine as engine
from g6.knowledge._registry import ProjectStore
from g6.mcp import build_server
from tests.knowledge.corpus import SUBJECTS
from tests.knowledge.corpus.rig import make, serve

CLAUDE = "anthropic:claude_code"
TOOLS_ADDED = ("g6_knowledge_add", "g6_knowledge_passage", "g6_knowledge_revoke", "g6_knowledge_search",
               "g6_knowledge_sources")
DOCS, NOTES = SUBJECTS[0], SUBJECTS[1]


class Dialog:
    """A scripted person: answers each dialog the client shows, and keeps what it was asked."""

    def __init__(self, index="accept", share="accept", before=None):
        self.index, self.share, self.before, self.questions = index, share, before, []

    async def __call__(self, message, response_type, params, context):
        self.questions.append(message)
        if self.before is not None:
            self.before(message)
        answer = self.index if message.startswith("Index this folder") else self.share
        if answer == "accept":
            return {"value": "Person"}
        if answer == "nameless":
            return {"value": "   "}
        return ElicitResult(action=answer)


def session(server, dialog=None, name="claude-code"):
    return Client(server, client_info=Implementation(name=name, version="test"),
                  **({"elicitation_handler": dialog} if dialog is not None else {}))


def call(server, tool, arguments, dialog=None, name="claude-code"):
    async def run():
        async with session(server, dialog, name) as client:
            return (await client.call_tool(tool, arguments, raise_on_error=False))
    return asyncio.run(run())


def calls(server, steps, dialog=None, name="claude-code"):
    async def run():
        async with session(server, dialog, name) as client:
            return [(await client.call_tool(tool, arguments, raise_on_error=False)).data for tool, arguments in steps]
    return asyncio.run(run())


@pytest.fixture
def rig(tmp_path, monkeypatch):
    made = make(tmp_path, monkeypatch)
    serve(made, monkeypatch)
    return made


def local_items(rig, query, top_k=20):
    return rig.items(query, top_k=top_k)


def test_the_five_tools_are_registered_and_declared():
    tools = asyncio.run(build_server().get_tools())
    assert set(TOOLS_ADDED) <= set(tools)
    for name in TOOLS_ADDED:
        assert TOOLS[name].classes == ("client_request",)
        assert set(tools[name].parameters.get("properties", {})) == set(TOOL_ARGUMENTS[name]), name
        assert tools[name].fn._g6_declaration is not None
    assert set(tools["g6_knowledge_add"].parameters["properties"]) == {"path", "share", "expires_days", "retention"}


@pytest.mark.parametrize("answer", ["decline", "cancel", "nameless", None])
def test_add_commits_nothing_unless_the_person_accepts(rig, answer):
    dialog = Dialog(index=answer) if answer else None
    reply = call(build_server(), "g6_knowledge_add", {"path": str(rig.folder)}, dialog).data
    assert reply.get("ok") is not True and reply["completion_state"] == "blocked-escalated"
    assert not ProjectStore(rig.binding).path.exists()
    assert engine.sources(rig.local("list")) == []
    if dialog is not None:
        assert len(dialog.questions) == 1


def test_add_shows_the_preview_digest_and_commits_only_on_accept(rig):
    dialog = Dialog()
    reply = call(build_server(), "g6_knowledge_add", {"path": str(rig.folder), "expires_days": 30}, dialog).data
    assert reply["ok"] is True, reply
    question, = dialog.questions
    listings = sorted((rig.home / "knowledge" / "previews").glob("*.md"))
    assert len(listings) == 1 and listings[0].stem in question
    assert "nothing is sent to a model" in question and "Files to index: 49" in question
    assert "credential" in question and "too_large" in question and "Expires: 20" in question
    registration, = ProjectStore(rig.binding).registrations()
    assert registration.preview_sha256 == listings[0].stem and registration.state == "active"
    assert registration.attribution_sha256 == cloud.sha256("Person")
    result = reply["result"]
    assert result["source"] == registration.id and result["sharing"] == "not_requested"
    assert "files" not in result and "label" not in result, "counts and labels are for a provider the source is shared with"
    listed = set(listings[0].read_text(encoding="utf-8").splitlines())
    assert any(line.startswith(f"- docs/{DOCS}.md") for line in listed) and "- .env: credential" in listed
    assert local_items(rig, DOCS)


def test_add_rechecks_the_preview_at_commit(rig):
    def slip_a_file_in(message):
        (rig.folder / "docs" / "late.md").write_text("# The pangolin decision\n\nAdded while the dialog was open.\n",
                                                     encoding="utf-8")

    reply = call(build_server(), "g6_knowledge_add", {"path": str(rig.folder)}, Dialog(before=slip_a_file_in)).data
    assert reply["ok"] is False and "G6_E_KNOWLEDGE_PREVIEW_CHANGED" in reply["error"]
    assert engine.sources(rig.local("list")) == [] and local_items(rig, "pangolin") == []


def test_agent_cannot_answer_the_dialog(rig):
    server = build_server()
    for extra in ("answer", "name", "approved", "value", "project", "principal", "destination", "provider"):
        dialog = Dialog()
        reply = call(server, "g6_knowledge_add", {"path": str(rig.folder), extra: "Person"}, dialog)
        assert reply.is_error and dialog.questions == [], extra
        assert call(server, "g6_knowledge_search", {"query": DOCS, extra: "x"}).is_error, extra
    assert engine.sources(rig.local("list")) == []


def test_search_returns_nothing_from_an_ungranted_source_to_a_cloud_client(rig):
    server = build_server()
    added = call(server, "g6_knowledge_add", {"path": str(rig.folder)}, Dialog()).data["result"]
    known = local_items(rig, DOCS)
    assert known
    replies = calls(server, [("g6_knowledge_search", {"query": DOCS, "top_k": 20}),
                             ("g6_knowledge_passage", {"passage_id": known[0]["passage_id"]}),
                             ("g6_knowledge_passage", {"passage_id": known[0]["passage_id"], "raw": True}),
                             ("g6_knowledge_sources", {})])
    text = json.dumps(replies)
    for planted in (f"{DOCS} convention", f"docs/{DOCS}", f"{DOCS}.md", *(i["passage_id"] for i in known)):
        assert planted not in text, planted
    search = replies[0]["result"]
    assert search["payload"]["items"] == [] and search["denied"] == []
    assert search["withheld"] == [{"source": added["source"], "provider": CLAUDE}]
    assert replies[1]["result"] == {"passage": None, "denied": [],
                                    "withheld": [{"source": added["source"], "provider": CLAUDE}]}
    assert replies[2]["result"]["passage"] is None
    row, = replies[3]["result"]["sources"]
    assert row["shared_with_this_client"] is False and "label" not in row and "files" not in row
    assert cloud.disclosures() == []


def test_search_after_grant_returns_cited_checked_passages_and_logs_the_disclosure(rig):
    server = build_server()
    dialog = Dialog()
    added, found = calls(server, [("g6_knowledge_add", {"path": str(rig.folder), "share": True}),
                                  ("g6_knowledge_search", {"query": DOCS})], dialog)
    assert added["result"]["sharing"] == "granted" and added["result"]["files"] == 49
    assert [q.split()[0] for q in dialog.questions] == ["Index", "Share"]
    assert f"knowledge:{added['result']['source']}" in dialog.questions[1] and "cannot be recalled" in dialog.questions[1]
    items = found["result"]["payload"]["items"]
    assert items and found["completion_state"] == "qualified-draft"
    for item in items:
        assert item["source"] == added["result"]["source"] and item["checked"]["outcome"] == "intact"
        assert item["trust"] == "untrusted" and item["end"] > item["start"] and len(item["passage_id"]) == 64
    assert items[0]["path"] == f"docs/{DOCS}.md" and DOCS in items[0]["view"]["text"]
    assert found["result"]["authorisation_revisions"] == {added["result"]["source"]: 1}
    logged = cloud.disclosures()
    assert logged and all(d["provider"] == CLAUDE for d in logged)
    rows = [s for d in logged for s in d["sources"] if s["source"] == f"knowledge:{added['result']['source']}"]
    assert {i["passage_id"] for i in items} <= {s["item_id"] for s in rows}
    local = {i["passage_id"]: i for i in local_items(rig, DOCS)}
    document = (rig.folder / "docs" / f"{DOCS}.md").read_bytes().decode("utf-8")
    for row in rows:
        if row["item_id"] in local:
            cut = document[local[row["item_id"]]["start"]:local[row["item_id"]]["end"]]
            assert row["content_sha256"] == cloud.sha256(cut)
    assert f"{DOCS} convention" not in json.dumps(logged) and DOCS not in json.dumps(logged)


def test_unknown_client_gets_nothing(rig):
    server = build_server()
    dialog = Dialog()
    added = call(server, "g6_knowledge_add", {"path": str(rig.folder), "share": True}, dialog, name="some-other-agent").data
    assert added["ok"] is True and added["result"]["sharing"] != "granted"
    source = added["result"]["source"]
    known = local_items(rig, DOCS)
    replies = calls(server, [("g6_knowledge_search", {"query": DOCS}),
                             ("g6_knowledge_passage", {"passage_id": known[0]["passage_id"], "raw": True}),
                             ("g6_cloud_grant", {"source": f"knowledge:{source}", "provider": "cloud:unknown"}),
                             ("g6_knowledge_search", {"query": DOCS})], Dialog(), name="some-other-agent")
    text = json.dumps(replies)
    assert f"{DOCS} convention" not in text and f"docs/{DOCS}" not in text
    assert replies[0]["result"]["payload"]["items"] == [] == replies[3]["result"]["payload"]["items"]
    assert replies[0]["result"]["withheld"] == [{"source": source, "provider": "cloud:unknown"}]
    assert replies[2]["ok"] is False
    assert cloud.disclosures() == []


def test_revoked_cloud_grant_stops_the_next_search(rig):
    server = build_server()
    added, before = calls(server, [("g6_knowledge_add", {"path": str(rig.folder), "share": True}),
                                   ("g6_knowledge_search", {"query": DOCS})], Dialog())
    assert before["result"]["payload"]["items"]
    source = added["result"]["source"]
    again = calls(server, [("g6_cloud_revoke", {"source": f"knowledge:{source}", "provider": CLAUDE}),
                           ("g6_knowledge_search", {"query": DOCS}),
                           ("g6_knowledge_passage", {"passage_id": before["result"]["payload"]["items"][0]["passage_id"]})])
    assert again[0]["ok"] is True
    assert again[1]["result"]["payload"]["items"] == []
    assert again[1]["result"]["withheld"] == [{"source": source, "provider": CLAUDE}]
    assert again[2]["result"]["passage"] is None
    assert f"{DOCS} convention" not in json.dumps(again)


def test_registration_revoke_stops_the_next_search_and_the_grant(rig):
    server = build_server()
    added, before = calls(server, [("g6_knowledge_add", {"path": str(rig.folder), "share": True}),
                                   ("g6_knowledge_search", {"query": DOCS})], Dialog())
    source = added["result"]["source"]
    passage = before["result"]["payload"]["items"][0]["passage_id"]
    revoked, after, fetched, listed = calls(server, [("g6_knowledge_revoke", {"source": source}),
                                                     ("g6_knowledge_search", {"query": DOCS}),
                                                     ("g6_knowledge_passage", {"passage_id": passage}),
                                                     ("g6_knowledge_sources", {})])
    assert revoked["result"]["state"] == "revoked" and revoked["result"]["sharing_revoked_for"] == [CLAUDE]
    assert after["result"]["payload"]["items"] == [] and fetched["result"]["passage"] is None
    assert not cloud.is_granted(f"knowledge:{source}", CLAUDE)
    assert listed["result"]["sources"][0]["state"] == "revoked"
    assert f"{DOCS} convention" not in json.dumps([after, fetched, listed])
    unknown = call(server, "g6_knowledge_revoke", {"source": "src-0000000000000000"}).data
    assert unknown["ok"] is False and "G6_E_KNOWLEDGE_SOURCE" in unknown["error"]


def test_raw_fetch_needs_the_grant_and_returns_exact_bytes(rig):
    server = build_server()
    shared, private = calls(server, [("g6_knowledge_add", {"path": str(rig.folder / "docs"), "share": True}),
                                     ("g6_knowledge_add", {"path": str(rig.folder / "notes")})], Dialog())
    granted = local_items(rig, DOCS)[0]
    ungranted = local_items(rig, NOTES)[0]
    raw, view, refused = calls(server, [
        ("g6_knowledge_passage", {"passage_id": granted["passage_id"], "raw": True}),
        ("g6_knowledge_passage", {"passage_id": granted["passage_id"]}),
        ("g6_knowledge_passage", {"passage_id": ungranted["passage_id"], "raw": True})])
    document = (rig.folder / "docs" / f"{DOCS}.md").read_bytes().decode("utf-8")
    assert raw["result"]["passage"]["text"] == document[granted["start"]:granted["end"]]
    assert "text" not in view["result"]["passage"] and view["result"]["passage"]["view"]["text"]
    assert refused["result"]["passage"] is None and f"{NOTES} convention" not in json.dumps(refused)
    assert refused["result"]["withheld"] == [{"source": private["result"]["source"], "provider": CLAUDE}]
    bad = call(server, "g6_knowledge_passage", {"passage_id": "../" + granted["passage_id"]}).data
    assert bad["ok"] is False and "G6_E_KNOWLEDGE_INPUT" in bad["error"]


def test_mixed_sources_return_only_the_granted_one(rig):
    server = build_server()
    shared, private, found = calls(server, [
        ("g6_knowledge_add", {"path": str(rig.folder / "docs"), "share": True}),
        ("g6_knowledge_add", {"path": str(rig.folder / "notes")}),
        ("g6_knowledge_search", {"query": f"{DOCS} {NOTES} convention decision", "top_k": 20})], Dialog())
    result = found["result"]
    assert result["payload"]["items"] and {i["source"] for i in result["payload"]["items"]} == {shared["result"]["source"]}
    assert result["withheld"] == [{"source": private["result"]["source"], "provider": CLAUDE}]
    text = json.dumps(found)
    assert f"{NOTES}.txt" not in text and "notes/" not in text
    assert f"The {NOTES} convention" not in text
    hidden = {i["passage_id"] for i in local_items(rig, f"{NOTES} convention decision")
              if i["source"] == private["result"]["source"]}
    assert hidden and not any(pid in text for pid in hidden)
    rows = {r["source"]: r for r in call(server, "g6_knowledge_sources", {}).data["result"]["sources"]}
    assert rows[shared["result"]["source"]]["label"] == "knowledge/docs" and "label" not in rows[private["result"]["source"]]


def test_reply_has_envelope_fields_and_stable_codes(rig, tmp_path):
    server = build_server()
    outside = tmp_path / "outside"
    outside.mkdir()
    dialog = Dialog()
    replies = calls(server, [("g6_knowledge_add", {"path": str(outside)}),
                             ("g6_knowledge_add", {"path": str(rig.folder), "retention": "forever"}),
                             ("g6_knowledge_add", {"path": str(rig.folder)}),
                             ("g6_knowledge_sources", {}),
                             ("g6_knowledge_search", {"query": DOCS}),
                             ("g6_knowledge_search", {"query": "?!"}),
                             ("g6_knowledge_passage", {"passage_id": "f" * 64}),
                             ("g6_knowledge_revoke", {"source": "not-a-source"})], dialog)
    for reply in replies:
        assert {"completion_state", "warning_card", "evidence", "request_id", "run_id"} <= set(reply), reply
        assert reply["completion_state"] in ("qualified-draft", "blocked-escalated")
    assert "[G6_E_KNOWLEDGE_SCOPE]" in replies[0]["error"] and str(outside) not in json.dumps(replies[0])
    assert "[G6_E_KNOWLEDGE_INPUT]" in replies[1]["error"]
    assert len(dialog.questions) == 1, "a refused path or setting opens no dialog"
    assert replies[2]["ok"] and replies[3]["ok"] and replies[4]["ok"]
    assert replies[5]["result"]["backends"][0]["state"] == "failed"
    assert replies[6]["result"]["denied"][0]["why"] == "unknown"
    assert "[G6_E_KNOWLEDGE_S" in replies[7]["error"]


def test_no_roots_named_means_nothing_can_be_added(tmp_path, monkeypatch):
    made = make(tmp_path, monkeypatch)
    monkeypatch.delenv("G6_KNOWLEDGE_ROOTS", raising=False)
    monkeypatch.chdir(made.project)
    dialog = Dialog()
    reply = call(build_server(), "g6_knowledge_add", {"path": str(made.folder)}, dialog).data
    assert reply["ok"] is False and "G6_KNOWLEDGE_ROOTS" in reply["error"] and dialog.questions == []
