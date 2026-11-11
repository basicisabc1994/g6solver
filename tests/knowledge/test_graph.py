# SPDX-License-Identifier: Apache-2.0
"""The passage-linked graph: every edge has a passage and a witness that still reads true, a path never passes through
a revoked or ungranted node, an import is reported as a possible dependency, and limits refuse."""
from __future__ import annotations

import pytest

from g6.knowledge import _engine as engine
from g6.knowledge import _graph
from g6.knowledge._registry import ProjectStore, Refused
from g6.knowledge._scope import access
from tests.knowledge.corpus import MODULES, SUBJECTS
from tests.knowledge.corpus.rig import NOW, make


def built(tmp_path, monkeypatch, **kw):
    rig = make(tmp_path, monkeypatch, **kw)
    source = rig.add()["source"]
    store = ProjectStore(rig.binding)
    report = _graph.build(store, store.published().number)
    return rig, source, store, report


def paths(rig, start, relation="imports", acc=None, **kw):
    response, refs = _graph.traverse(acc or rig.local("traverse"), start, relation, clock=lambda: NOW, **kw)
    return response, refs


def test_every_edge_has_a_passage_and_witness_offsets_that_hold(tmp_path, monkeypatch):
    rig, source, store, report = built(tmp_path, monkeypatch)
    assert report["edges"] > 40 and report["dynamic_imports_unknown"] == 0 and report["files_not_read"] == 0
    _, raw = store.graph(1)
    edges = [_graph.Edge.model_validate_json(e) for e in raw]
    members = {pid: m for m in store.members(1) for pid in m.passage_ids}
    assert {e.relation for e in edges} == {"contains", "imports"}
    for edge in edges:
        member = members[edge.passage_id]
        text = (rig.folder / member.relative).read_bytes().decode("utf-8")
        witness = text[edge.witness[0]:edge.witness[1]]
        assert witness and (witness.startswith("#") if edge.relation == "contains" else "import" in witness), witness
        start, end = member.spans[member.passage_ids.index(edge.passage_id)]
        assert start <= edge.witness[0] < end and edge.source_version == member.file_sha256
    response, refs = paths(rig, f"docs/{SUBJECTS[0]}.md", "contains")
    path, = response.payload["paths"]
    assert path["endpoints"] == [f"docs/{SUBJECTS[0]}.md", f"docs/{SUBJECTS[0]}.md#The {SUBJECTS[0]} decision"]
    assert path["edges"][0]["certainty"] == "witnessed" and len(refs) == 1 and refs[0].item_id == path["edges"][0]["passage_id"]


def test_imports_are_reported_as_possible_not_behavioural(tmp_path, monkeypatch):
    rig, _, _, _ = built(tmp_path, monkeypatch)
    response, _ = paths(rig, f"src/{MODULES[0]}.py", max_hops=1)
    path, = response.payload["paths"]
    edge, = path["edges"]
    assert (edge["subject"], edge["object"], edge["relation"]) == (f"src/{MODULES[0]}.py", f"src/{MODULES[1]}.py", "imports")
    assert edge["certainty"] == "possible" and edge["status"] == "asserted" and "possible dependency" in edge["means"]
    assert "nothing here says the code runs" in edge["means"]


def test_paths_keep_order_direction_and_endpoints(tmp_path, monkeypatch):
    rig, _, _, _ = built(tmp_path, monkeypatch)
    response, _ = paths(rig, f"src/{MODULES[0]}.py", max_hops=3)
    found = {p["hops"]: p for p in response.payload["paths"]}
    assert set(found) == {1, 2, 3}
    three = found[3]
    assert [e["subject"] for e in three["edges"]] == [f"src/{m}.py" for m in MODULES[:3]]
    assert [e["object"] for e in three["edges"]] == [f"src/{m}.py" for m in MODULES[1:4]]
    assert three["endpoints"] == [f"src/{MODULES[0]}.py", f"src/{MODULES[3]}.py"]
    assert all(e["direction"] == "subject_to_object" for e in three["edges"])
    assert response.payload["more_beyond_max_hops"] is True and response.backends[0].state == "answered"
    whole, _ = paths(rig, f"src/{MODULES[0]}.py", max_hops=6)
    assert max(p["hops"] for p in whole.payload["paths"]) == 5, "the import ring is walked once, never round again"
    assert paths(rig, "src/nothing.py")[0].payload == {"kind": "path", "paths": [], "start_found": False,
                                                        "max_hops": 3, "more_beyond_max_hops": False}


def test_dynamic_import_is_unknown(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    (rig.folder / "src" / "loader.py").write_text(
        '"""Loads a plugin by name."""\nimport importlib\n\n\ndef load(name):\n    return importlib.import_module(name)\n',
        encoding="utf-8")
    rig.add()
    store = ProjectStore(rig.binding)
    report = _graph.build(store, 1)
    assert report["dynamic_imports_unknown"] == 1
    assert paths(rig, "src/loader.py")[0].payload["paths"] == [], "an unknown import is not a path to anything"
    _, raw = store.graph(1)
    assert sum('"imports_unknown"' in e for e in raw) == 1


def test_path_through_a_revoked_or_ungranted_node_is_not_returned(tmp_path, monkeypatch, accepted_cloud_dialog):
    rig, source, store, _ = built(tmp_path, monkeypatch)
    start = f"src/{MODULES[0]}.py"
    cloud = access(rig.binding, "traverse", "anthropic:claude_code")
    hidden, refs = paths(rig, start, acc=cloud)
    assert hidden.payload["paths"] == [] and hidden.payload["start_found"] is False and refs == ()
    assert MODULES[1] not in hidden.model_dump_json()
    accepted_cloud_dialog(f"knowledge:{source}", "anthropic:claude_code", text="synthetic corpus")
    assert len(paths(rig, start, acc=cloud)[0].payload["paths"]) == 3
    assert paths(rig, start, acc=rig.local("traverse", sources=[]))[0].payload["paths"] == []
    # an intermediate node whose file changed: every path through it goes, the one before it stays
    middle = rig.folder / "src" / f"{MODULES[1]}.py"
    middle.write_text(middle.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    after, _ = paths(rig, start)
    assert [p["hops"] for p in after.payload["paths"]] == [1]
    engine.revoke(rig.local("revoke"), source)
    assert paths(rig, start)[0].payload["paths"] == []


def test_edges_of_a_changed_file_are_invalidated(tmp_path, monkeypatch):
    rig, source, store, _ = built(tmp_path, monkeypatch)
    start = f"src/{MODULES[0]}.py"
    path = rig.folder / "src" / f"{MODULES[0]}.py"
    path.write_text(path.read_text(encoding="utf-8").replace(f"from src import {MODULES[1]}", "import json as j"),
                    encoding="utf-8")
    assert paths(rig, start)[0].payload["paths"] == [], "the witness no longer reads true"
    assert [d.why for d in store.invalidated("graph_edge", "graph-g1", at=NOW)] == ["changed"]
    engine.refresh(rig.local("refresh"), clock=lambda: NOW)
    assert paths(rig, start)[0].backends[0].reason.startswith("no graph is built for generation 2")
    _graph.build(store, 2)
    assert paths(rig, start)[0].payload["paths"] == [], "the import is gone from the file, and from the graph"
    assert paths(rig, f"src/{MODULES[1]}.py")[0].payload["paths"]


def test_same_name_in_two_projects_is_not_merged(tmp_path, monkeypatch):
    first, _, store_one, _ = built(tmp_path, monkeypatch, name="first")
    second, _, store_two, _ = built(tmp_path, monkeypatch, name="second")
    one = {_graph.Node.model_validate_json(n).id for n in store_one.graph(1)[0]}
    two = {_graph.Node.model_validate_json(n).id for n in store_two.graph(1)[0]}
    assert one and two and not one & two
    more = second.add(str(second.folder / "src"))["source"]
    _graph.build(store_two, 2)
    labels = [_graph.Node.model_validate_json(n) for n in store_two.graph(2)[0]]
    same = [n for n in labels if n.label in (f"src/{MODULES[0]}.py", f"{MODULES[0]}.py")]
    assert len({n.id for n in same}) == 2 and {n.registration_id for n in same} >= {more}


def test_hop_and_path_limits_refuse_rather_than_truncate_silently(tmp_path, monkeypatch):
    rig, _, _, _ = built(tmp_path, monkeypatch)
    start = f"src/{MODULES[0]}.py"
    refused, refs = paths(rig, start, max_hops=5, max_paths=2)
    assert refused.payload["paths"] == [] and "more than 2 paths" in refused.payload["refused"] and refs == ()
    assert refused.backends[0].state == "refused"
    for bad in ({"max_hops": 0}, {"max_hops": 99}, {"max_paths": 0}, {"max_paths": 10_000}):
        with pytest.raises(Refused):
            paths(rig, start, **bad)
    with pytest.raises(Refused):
        paths(rig, start, "depends_on")
    empty = make(tmp_path, monkeypatch, name="empty", build=False)
    assert paths(empty, start)[0].backends[0].reason.startswith("not_published")