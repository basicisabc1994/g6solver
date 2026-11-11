# SPDX-License-Identifier: Apache-2.0
"""Scope before retrieval, as an inventory over every Context Engine tool the server registers: what a client whose
provider holds no grant is told is the same for two corpora that differ only in the ungranted source's content."""
from __future__ import annotations

import asyncio
import json
import re

import pytest
from fastmcp import Client
from fastmcp.client.elicitation import ElicitResult
from mcp.types import Implementation

from g6.knowledge import _engine as engine
from g6.mcp import build_server
from tests.knowledge.corpus import SUBJECTS
from tests.knowledge.corpus.rig import NOW, make, serve

#: The arguments each tool is called with ("PASSAGE", "SOURCE" and "FOLDER" stand for the rig's own). A tool the
#: server registers that is not here fails the test: a new tool is called for an ungranted client before it ships.
ARGUMENTS = {
    "g6_knowledge_add": {"path": "FOLDER", "share": True, "backends": ["elastic_managed"]},
    "g6_knowledge_context": {"task": f"{SUBJECTS[0]} convention decision"},
    "g6_knowledge_count": {"query": SUBJECTS[0]},
    "g6_knowledge_delete": {"source": "SOURCE", "complete": True},
    "g6_knowledge_job": {"job_id": "job-0000000000000000"},
    "g6_knowledge_passage": {"passage_id": "PASSAGE", "raw": True},
    "g6_knowledge_paths": {"start": "src/ledger.py", "relation": "imports", "max_hops": 3},
    "g6_knowledge_refresh": {},
    "g6_knowledge_search": {"query": f"{SUBJECTS[0]} convention service", "top_k": 20},
    "g6_knowledge_sources": {},
    "g6_knowledge_revoke": {"source": "src-0000000000000000"},
}
_VARYING = ((re.compile(r"src-[0-9a-f]{16}"), "SRC"), (re.compile(r"[0-9a-f]{64}"), "HASH"),
            (re.compile(r"[0-9a-f]{32}"), "ID"), (re.compile(r"\b[0-9a-f]{12}\b"), "ID"),
            (re.compile(r"\d{4}-\d\d-\d\dT[\d:.]+(?:Z|\+00:00)"), "TIME"))


def _told(reply) -> str:
    text = json.dumps({"data": reply.data, "content": [getattr(c, "text", "") for c in reply.content]},
                      sort_keys=True, default=str)
    for pattern, name in _VARYING:
        text = pattern.sub(name, text)
    return text


def _replies(tmp_path, monkeypatch, name, change, client_name):
    rig = make(tmp_path / name, monkeypatch)
    change(rig)
    source = rig.add()["source"]                    # indexed locally; shared with no provider
    passage = rig.items("decision", top_k=1)[0]["passage_id"]
    serve(rig, monkeypatch)
    server = build_server()

    async def declines(message, response_type, params, context):
        return ElicitResult(action="decline")

    async def run():
        tools = sorted(t for t in await server.get_tools() if t.startswith("g6_knowledge_"))
        assert set(tools) == set(ARGUMENTS), set(tools) ^ set(ARGUMENTS)
        out = {}
        async with Client(server, client_info=Implementation(name=client_name, version="test"),
                          elicitation_handler=declines) as client:
            for tool in tools:
                arguments = {k: {"PASSAGE": passage, "SOURCE": source, "FOLDER": str(rig.folder)}.get(v, v)
                             if isinstance(v, str) else v for k, v in ARGUMENTS[tool].items()}
                out[tool] = _told(await client.call_tool(tool, arguments, raise_on_error=False))
        return out

    told = asyncio.run(run())
    # controls, while this project is the bound one: the person's own local search reaches the source, with the
    # words this corpus holds, and the declined deletion deleted nothing
    words = {word: bool(rig.items(word, clock=lambda: NOW)) for word in ("convention", "arrangement", "decision")}
    assert words["decision"] and engine.sources(rig.local("list"))[0]["state"] == "active"
    return told, words

def _as_built(rig) -> None:
    pass


def _other_content(rig) -> None:
    """The same folder with other words in every file, one file gone and two more."""
    for path in sorted(p for p in rig.folder.rglob("*") if p.is_file() and p.suffix in (".md", ".txt", ".py")):
        path.write_text(path.read_text(encoding="utf-8").replace("convention", "arrangement") + "\nAn added line.\n",
                        encoding="utf-8")
    (rig.folder / "docs" / f"{SUBJECTS[2]}.md").unlink()
    for n in range(2):
        (rig.folder / "docs" / f"extra-{n}.md").write_text(f"# Extra {n}\n\nAnother private note {n}.\n", encoding="utf-8")


@pytest.mark.parametrize("client_name", ["claude-code", "some-unknown-client"])
def test_every_tool_tells_a_client_with_no_grant_the_same_whatever_the_source_holds(tmp_path, monkeypatch, client_name):
    first, words = _replies(tmp_path, monkeypatch, "one", _as_built, client_name)
    assert words["convention"] and not words["arrangement"]
    second, other = _replies(tmp_path, monkeypatch, "two", _other_content, client_name)
    assert other["arrangement"] and not other["convention"], "control: the two corpora do differ"
    for tool in sorted(ARGUMENTS):
        assert first[tool] == second[tool], f"{tool} tells a client with no grant something about the source's content"
    for told in (*first.values(), *second.values()):
        assert "convention" not in told.replace("convention decision", "").replace("convention service", "") \
            and "arrangement" not in told and "docs/" not in told and "extra-" not in told