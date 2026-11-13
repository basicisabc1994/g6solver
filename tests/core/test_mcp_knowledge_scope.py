# SPDX-License-Identifier: Apache-2.0
"""A reply is a function of the rows in the caller's scope and of nothing else: an inventory over every Context Engine
tool the server registers, each driven with an adversarial family of arguments (a guessed id, the id of a real
object the caller holds no grant for, a mixed grant, a path probe), and the whole reply compared between two
corpora that differ only outside the caller's scope. The objects are real: each rig has an indexing job, a graph,
a saved context and passages of the source that is not shared."""
from __future__ import annotations

import asyncio
import json
import re

import pytest
from fastmcp import Client
from fastmcp.client.elicitation import ElicitResult
from mcp.types import Implementation

from g6.core import cloud_consent as cloud
from g6._context_engine import _engine as engine
from g6._context_engine import _graph, _jobs, _planner, _relations
from g6._context_engine._registry import ProjectStore
from g6._context_engine._scope import consent_scope
from g6.mcp import build_server
from tests.knowledge.corpus import SUBJECTS, fake_elastic
from tests.knowledge.corpus.rig import NOW, make, serve

CLAUDE = "anthropic:claude_code"
ALLOW = lambda action: {"kind": "action_gate", "decision": "allow"}  # noqa: E731
WORDS = f"{SUBJECTS[0]} convention service"
#: Each tool's adversarial family, in the order the calls are made (tools that change something come last). The
#: capital words stand for the rig's own objects: PASSAGE, SOURCE and JOB are a passage, the registration and the
#: finished indexing job of the source that is NOT shared with the caller; SHARED is the shared source (present in
#: the mixed-grant case only, otherwise an id nobody holds); GUESS is an id nobody holds; the path words are probes
#: under the permitted root. A tool the server registers that has no family here fails the test.
FAMILIES = {
    "g6_knowledge_sources": [{}],
    "g6_knowledge_search": [{"query": WORDS, "top_k": 20}, {"query": "arrangement private pangolin", "top_k": 1},
                            {"query": WORDS, "sources": ["SOURCE"]}, {"query": WORDS, "sources": ["SOURCE", "SHARED"]},
                            {"query": WORDS, "routes": ["structure"]}, {"query": WORDS, "routes": ["exact", "structure"]},
                            {"query": WORDS, "plan": True, "needs": ["arrangement", "convention"]},
                            {"query": "which files does src/ledger.py import", "plan": True}],
    "g6_knowledge_context": [{"task": WORDS}, {"task": WORDS, "sources": ["SOURCE"]}, {"task": WORDS, "budget_tokens": 50}],
    "g6_knowledge_passage": [{"passage_id": "PASSAGE"}, {"passage_id": "PASSAGE", "raw": True}, {"passage_id": "GUESS"}],
    "g6_knowledge_job": [{"job_id": "JOB"}, {"job_id": "job-0000000000000000"}],
    "g6_knowledge_count": [{"query": SUBJECTS[0]}, {"query": "arrangement"}, {"sources": ["SOURCE"]}, {}],
    "g6_knowledge_paths": [{"start": "src/ledger.py"}, {"start": "src/ledger.py", "max_hops": 6},
                           {"start": f"docs/{SUBJECTS[0]}.md", "relation": "contains"}, {"start": "notes/shared.md", "relation": "contains"}],
    "g6_knowledge_refresh": [{}, {"source": "SOURCE"}, {"source": "SHARED"}],
    "g6_knowledge_delete": [{"source": "SOURCE", "complete": True}, {"source": "src-0000000000000000"}],
    "g6_knowledge_add": [{"path": "MISSING"}, {"path": "FILE"}, {"path": "CREDENTIAL"}, {"path": "CREDENTIAL_ALIAS"},
                         {"path": "UNDER_CREDENTIAL"}, {"path": "FOLDER", "share": True, "backends": ["elastic_managed"]}],
    "g6_knowledge_revoke": [{"source": "src-0000000000000000"}, {"source": "SOURCE"}],
}
#: The path probes that name nothing G6 may index: the refusal is one and the same for all of them.
NOT_FOLDERS = ("MISSING", "FILE", "CREDENTIAL", "CREDENTIAL_ALIAS", "UNDER_CREDENTIAL")
_VARYING = ((re.compile(r"job-[0-9a-f]{16}"), "JOB"), (re.compile(r"src-[0-9a-f]{16}"), "SRC"),
            (re.compile(r"pack-[0-9a-f]{32}"), "PACK"), (re.compile(r"g6-knowledge-v1-[0-9a-f]{16}[-0-9a-z]*"), "INDEX"),
            (re.compile(r"[0-9a-f]{64}"), "HASH"), (re.compile(r"[0-9a-f]{32}"), "ID"),
            (re.compile(r"\b[0-9a-f]{12}\b"), "ID"), (re.compile(r"\d{4}-\d\d-\d\dT[\d:.]+(?:Z|\+00:00)"), "TIME"),
            (re.compile(r"seconds[\\\\\"]*: ?[0-9.e-]+"), "seconds: T"),   # time spent: measured, not a field of a source
            (re.compile(r"[A-Za-z]:\\\\[^\"]*?\\\\knowledge\\\\previews\\\\"), "PREVIEWS/"))


def _same(value) -> str:
    text = json.dumps(value, sort_keys=True, default=str)
    for pattern, name in _VARYING:
        text = pattern.sub(name, text)
    return text


def _told(reply) -> str:
    return _same({"data": reply.data, "content": [getattr(c, "text", "") for c in reply.content]})


def _world(tmp_path, monkeypatch, name, change, client_name, mixed):
    """One project: a source shared with nobody (the synthetic corpus, changed by ``change`` before it is indexed and
    again afterwards), and in the mixed case a second source, the same in every world, shared with Claude. Returns
    what the client is told for every member of every family, and controls read while this project is bound."""
    rig = make(tmp_path / name, monkeypatch)
    change(rig)
    private = rig.add()["source"]
    shared = "src-" + "1" * 16
    if mixed:
        folder = rig.project / "notes"
        folder.mkdir()
        (folder / "shared.md").write_text(f"# Shared\n\nThe {SUBJECTS[0]} convention service is described here.\n",
                                          encoding="utf-8")
        (folder / "open.py").write_text('"""Open module."""\nimport json\n', encoding="utf-8")
        shared = rig.add(str(folder), backends=())["source"]
        scope = consent_scope(shared)
        assert cloud._grant(scope, CLAUDE, cloud.preview(scope, CLAUDE)[1], "Person") == "granted"
    store = ProjectStore(rig.binding)
    job = _jobs.start(rig.binding, private, ["elastic_managed"], wait=True, gate=ALLOW)     # a real job
    assert job.state == "published", job
    assert _graph.build(store, store.published().number)["nodes"] > 40                         # a real graph
    saved, _ = engine.context(rig.local("context"), WORDS, clock=lambda: NOW)                   # a real saved context
    passage = rig.items("decision", top_k=1)[0]["passage_id"]                                  # a real passage
    edge = _relations.extract(rig.local("fetch"), [passage], limits=_relations.Limits(model_calls=1), clock=lambda: NOW,
                              caller=__import__("g6.core.llm", fromlist=["FakeLLM"]).FakeLLM(lambda s, u: json.dumps(
                                  [{"passage": 1, "subject": "decision", "object": "every caller", "relation": "defines"}])))
    words = {word: bool(rig.items(word, clock=lambda: NOW, top_k=20)) for word in ("convention", "arrangement", "decision")}
    change(rig, after=True)   # and the unshared files differ again after they were indexed
    (rig.project / ".ssh").mkdir(exist_ok=True)
    (rig.project / ".ssh" / "deploy").mkdir(exist_ok=True)
    stands = {"PASSAGE": passage, "GUESS": "f" * 64, "SOURCE": private, "SHARED": shared, "JOB": job.id,
              "FOLDER": str(rig.folder), "MISSING": "knowledge/docs/no-such-note.md",
              "FILE": f"knowledge/docs/{SUBJECTS[4]}.md", "CREDENTIAL": ".ssh", "CREDENTIAL_ALIAS": ".SSH",
              "UNDER_CREDENTIAL": ".ssh/deploy"}

    def real(value):
        if isinstance(value, list):
            return [real(v) for v in value]
        return stands.get(value, value) if isinstance(value, str) else value

    serve(rig, monkeypatch)
    server = build_server()

    async def declines(message, response_type, params, context):
        return ElicitResult(action="decline")

    async def run():
        tools = sorted(t for t in await server.get_tools() if t.startswith("g6_knowledge_"))
        assert set(tools) == set(FAMILIES), f"a tool with no adversarial family: {set(tools) ^ set(FAMILIES)}"
        out = {}
        async with Client(server, client_info=Implementation(name=client_name, version="test"),
                          elicitation_handler=declines) as client:
            for tool, family in FAMILIES.items():
                for n, arguments in enumerate(family):
                    reply = await client.call_tool(tool, {k: real(v) for k, v in arguments.items()}, raise_on_error=False)
                    out[f"{tool}#{n} {json.dumps(arguments, sort_keys=True)}"] = _told(reply)
        return out

    # the engine's functions no tool reaches, with the same real objects and a destination that holds no grant for
    # the unshared source (asked before the tools, whose last calls change the project)
    cloud_access = lambda operation: rig.to(CLAUDE, operation)  # noqa: E731
    apart = {"engine.pack": _same(engine.pack(cloud_access("context"), saved["pack"], clock=lambda: NOW)[0]),
             "_relations.leads": _same(_relations.leads(cloud_access("traverse"), "decision", clock=lambda: NOW)[0].model_dump()),
             "_planner.run": _same([part.model_dump() for part in _planner.run(
                 cloud_access("plan"), f"how is the {SUBJECTS[0]} arrangement followed", ["arrangement"], clock=lambda: NOW)[:2]])}
    assert edge and saved["pack"] and engine.sources(rig.local("list"))[0]["state"] == "active"
    told = asyncio.run(run())
    return {**told, **apart}, words


def _as_built(rig, after=False) -> None:
    pass


def _other_content(rig, after=False) -> None:
    """Before indexing: other words in every file, one file gone and two more. After: one file rewritten and one
    removed, so that a re-read of the unshared source would find changes."""
    if after:
        (rig.folder / "docs" / f"{SUBJECTS[6]}.md").write_text("# Rewritten\n\nAfter it was indexed.\n", encoding="utf-8")
        (rig.folder / "notes" / f"{SUBJECTS[1]}.txt").unlink()
        return
    for path in sorted(p for p in rig.folder.rglob("*") if p.is_file() and p.suffix in (".md", ".txt", ".py")):
        path.write_text(path.read_text(encoding="utf-8").replace("convention", "arrangement") + "\nAn added line.\n",
                        encoding="utf-8")
    (rig.folder / "docs" / f"{SUBJECTS[2]}.md").unlink()
    for n in range(2):
        (rig.folder / "docs" / f"extra-{n}.md").write_text(f"# Extra {n}\n\nAnother private note {n}.\n", encoding="utf-8")


@pytest.mark.parametrize("client_name, mixed", [("claude-code", False), ("some-unknown-client", False),
                                                ("claude-code", True)])
def test_every_tool_tells_a_client_with_no_grant_the_same_whatever_the_source_holds(tmp_path, monkeypatch, client_name,
                                                                                  mixed):
    es, stop = fake_elastic.serve(monkeypatch)
    try:
        first, words = _world(tmp_path, monkeypatch, "one", _as_built, client_name, mixed)
        assert words["convention"] and not words["arrangement"]
        second, other = _world(tmp_path, monkeypatch, "two", _other_content, client_name, mixed)
    finally:
        stop()
    assert other["arrangement"], "control: the two corpora do differ"
    assert first.keys() == second.keys()
    def where(a: str, b: str) -> str:
        at = next((n for n, (x, y) in enumerate(zip(a, b, strict=False)) if x != y), min(len(a), len(b)))
        return f"...{a[max(at - 60, 0):at + 80]}... against ...{b[max(at - 60, 0):at + 80]}..."

    differing = [f"{call}: {where(first[call], second[call])}" for call in first if first[call] != second[call]]
    assert differing == [], "a reply varies with a source outside the caller's scope: " + "; ".join(differing)
    assert not any("extra-" in told or "An added line" in told for told in second.values())
    # one refusal for every path that is not an indexable folder: it does not say what is at the path
    add = [told for call, told in first.items() if call.startswith("g6_knowledge_add#")]
    assert len(set(add[:len(NOT_FOLDERS)])) == 1, "the refusal differs with what is, or is not, at a guessed path"
    if mixed:   # control: the shared source is served, so the mixed case does compare replies that hold content
        assert "shared.md" in first['g6_knowledge_search#0 {"query": "%s", "top_k": 20}' % WORDS]
