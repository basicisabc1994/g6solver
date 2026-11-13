# SPDX-License-Identifier: Apache-2.0
"""The Context Engine's scope is bound by the host: no binding, no access; a caller narrows and never widens; one
project's store is invisible to another; and no generic component op reaches a project store."""
from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from g6.core.registry import get_registry
from g6.core.storage import state_path
from g6._context_engine import _engine as engine
from g6._context_engine._envelope import SearchRequest
from g6._context_engine._registry import ProjectStore
from g6._context_engine._scope import (ROOTS_ENV, SCOPE_DENIED, START_IS_A_ROOT, Access, ScopeDenied, access, host_binding,
                                 inside)
from tests.knowledge.corpus import SUBJECTS
from tests.knowledge.corpus.rig import NOW, link_out, make


def test_no_binding_denies_every_operation(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    with pytest.raises(ScopeDenied) as denied:
        host_binding(environ={}, start=tmp_path / "missing")
    assert denied.value.code == SCOPE_DENIED
    nothing = Access(binding=None, operation="search", destination="local")
    calls = (lambda: engine.preview(None, str(tmp_path)),
             lambda: engine.commit(None, None, "Person"),
             lambda: engine.search(nothing, SearchRequest(query="quokka")),
             lambda: engine.fetch(nothing, "0" * 64),
             lambda: engine.revoke(nothing, "src-0000000000000000"),
             lambda: engine.sources(nothing))
    for call in calls:
        with pytest.raises(ScopeDenied) as denied:
            call()
        assert denied.value.code == "G6_E_KNOWLEDGE_SCOPE"
    with pytest.raises(ScopeDenied):
        access(None, "search", "local")
    assert not (tmp_path / "home" / "knowledge").exists()


def test_binding_refuses_home_and_filesystem_root(tmp_path):
    for start in (Path.home(), Path(Path.home().anchor)):
        with pytest.raises(ScopeDenied):
            host_binding(environ={}, start=start)
    project = tmp_path / "project"
    project.mkdir()
    import os
    named = os.pathsep.join([str(Path.home()), Path.home().anchor, str(project), str(tmp_path / "missing")])
    assert host_binding(environ={ROOTS_ENV: named}, start=project).roots == (project.resolve(),)


def test_only_the_roots_the_person_names_are_permitted(tmp_path, monkeypatch):
    """No default root (the stricter option): the start folder names the project and is not a root unless listed."""
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    (project / "docs").mkdir(parents=True)
    unlisted = host_binding(environ={}, start=project)
    assert START_IS_A_ROOT is False and unlisted.roots == ()
    with pytest.raises(ScopeDenied, match=ROOTS_ENV):
        engine.preview(unlisted, str(project / "docs"))
    import os
    listed = host_binding(environ={ROOTS_ENV: str(project / "docs") + os.pathsep + "relative/ignored"}, start=project)
    assert listed.roots == ((project / "docs").resolve(),)
    assert listed.project == unlisted.project and len(listed.project) == 64


def test_root_outside_binding_is_refused(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    (sibling / "note.md").write_text("# outside\n", encoding="utf-8")
    refused = [str(sibling), str(rig.project / ".."), "..", str(rig.project / "missing" / ".." / ".." / "sibling")]
    if link_out(rig.project / "linked", sibling):
        refused += [str(rig.project / "linked"), "linked"]
    for path in refused:
        with pytest.raises(ScopeDenied):
            engine.preview(rig.binding, path)
        with pytest.raises(ScopeDenied):
            inside(rig.binding, path)
    assert not ProjectStore(rig.binding).path.exists()
    assert not state_path("knowledge", "previews").exists()


def test_caller_can_only_narrow(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    added = rig.add()
    word = SUBJECTS[0]
    assert rig.items(word)
    for narrowed in (["src-0000000000000000"], [], ["not-an-id", "../" + added["source"]]):
        assert rig.items(word, acc=rig.local(sources=narrowed)) == []
    assert rig.items(word, acc=rig.local(sources=[added["source"], "src-0000000000000000"]))
    # a request's own narrowing intersects the access's; it cannot add a source the access left out
    response, _ = engine.search(rig.local(sources=["src-0000000000000000"]),
                                SearchRequest(query=word, sources=(added["source"],)), clock=lambda: NOW)
    assert response.payload["items"] == [] and response.withheld == ()
    assert engine.sources(rig.local("list", sources=[])) == []


def test_other_project_store_is_invisible(tmp_path, monkeypatch):
    first = make(tmp_path, monkeypatch, name="first")
    second = make(tmp_path, monkeypatch, name="second", build=False)
    assert first.binding.project != second.binding.project
    first.add()
    word = SUBJECTS[3]
    passage = first.items(word)[0]["passage_id"]
    response = second.search(word)
    assert response.payload["items"] == [] and response.generation is None
    assert "not_published" in response.backends[0].reason
    found, refs = engine.fetch(second.local("fetch"), passage, clock=lambda: NOW)
    assert found["passage"] is None and refs == ()
    assert {d["why"] for d in found["denied"]} <= {"unknown", "not_published"}
    assert engine.sources(second.local("list")) == []
    with pytest.raises(ScopeDenied):   # an access bound to one project cannot be used on another's store
        ProjectStore(first.binding).use(second.local(), [passage], at=NOW)


#: Every string parameter of every knowledge-family op, and what the op takes it as. A new op or parameter fails this
#: test until it is reviewed here: none of these may resolve to a file under ``knowledge/projects/``.
REVIEWED = {
    "ctx_colbert": {"query": "text", "texts": "text", "rung": "identifier"},
    "ctx_contexthub": {"op": "identifier", "registry": "identifier", "name": "identifier", "query": "text",
                       "tags": "text", "language": "identifier", "version": "identifier", "id": "identifier",
                       "text": "text"},
    "ctx_elastic": {"index": "cluster index name", "query": "text", "fields": "identifier", "filters": "text",
                    "id": "identifier"},
    "ctx_search": {"query": "text", "backend": "identifier"},
    "knowledge_chunker": {"text": "text", "uri": "label"},
    "knowledge_converter": {"text": "text", "data_base64": "text", "fetched": "identifier", "media_type": "identifier",
                            "uri": "label"},
    "knowledge_fetcher": {"source": "identifier", "url": "url", "query": "text", "categories": "text",
                          "set_spec": "identifier", "since": "identifier", "mailto": "text"},
    "knowledge_indexer": {"backend": "identifier", "index": "plain index name", "query": "text", "ids": "identifier"},
    "knowledge_manifest": {"passage_id": "identifier"},
}


def _string_parameters() -> dict[str, dict[str, str]]:
    found = {}
    for name, ref in sorted(get_registry().refs.items()):
        if ref.family != "knowledge":
            continue
        module = importlib.import_module(ref.module.split(":")[0])
        models = [v for k, v in vars(module).items()
                  if inspect.isclass(v) and issubclass(v, BaseModel) and k.endswith("Input")]
        assert models, f"{name} declares no input model to inventory"
        found[name] = {field: str(info.annotation) for model in models for field, info in model.model_fields.items()
                       if "str" in str(info.annotation) and field not in ("request_id", "run_id")}
    return found


def test_generic_door_cannot_open_a_project_store(tmp_path, monkeypatch):
    from g6.knowledge.knowledge_indexer.index import NAME, SqliteIndex
    from g6.mcp import invoke_reply

    actual = _string_parameters()
    assert {c: set(f) for c, f in actual.items()} == {c: set(f) for c, f in REVIEWED.items()}, (
        "a knowledge op gained or lost a string parameter: review whether it can name a file under knowledge/projects/")
    locations = {(c, f) for c, fields in REVIEWED.items() for f, kind in fields.items() if kind == "plain index name"}
    assert locations == {("knowledge_indexer", "index")}
    rig = make(tmp_path, monkeypatch)
    rig.add()
    store = ProjectStore(rig.binding)
    generation = store.published().number
    lexical = store.index_path("sqlite", generation)
    word = SUBJECTS[5]
    assert rig.items(word)
    before = {p.name: p.read_bytes() for p in store.folder.iterdir() if p.is_file()}
    monkeypatch.setenv("G6_MCP_SENSITIVE", "knowledge_indexer")
    evil = [str(lexical), str(lexical.with_suffix("")), f"../projects/{rig.binding.project}/lexical.g{generation}",
            f"projects/{rig.binding.project}/lexical.g{generation}", f"..\\projects\\{rig.binding.project}\\registry",
            "projects", f"lexical.g{generation}", "lexical"]
    for name in evil:
        if NAME.fullmatch(name):   # a plain name is placed in the generic folder, never in a project's
            assert SqliteIndex(name).path.parent == state_path("knowledge", "index")
        for op in ("search", "status", "remove", "add"):
            params = {"index": name, "query": word, "ids": ["0" * 64]}
            reply = json.dumps(invoke_reply("knowledge_indexer", op, json.dumps(params)))
            assert f"The {word} decision" not in reply and f"{word} convention" not in reply, (op, name)
    assert {p.name: p.read_bytes() for p in store.folder.iterdir() if p.is_file()} == before
    passage = rig.items(word)[0]["passage_id"]
    reply = json.dumps(invoke_reply("knowledge_manifest", "get", json.dumps({"passage_id": passage})))
    assert f"{word} convention" not in reply
