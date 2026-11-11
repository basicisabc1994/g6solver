# SPDX-License-Identifier: Apache-2.0
"""Snapshots, retained bytes, deletion and reverse dependencies: an answer can be reconstructed only while its bytes
are retained; retained bytes are never searchable; a complete deletion leaves only tombstone ids; and every stored
consumer is denied once its source is revoked, changed or deleted."""
from __future__ import annotations

import json

import pytest

from g6.knowledge import _engine as engine
from g6.knowledge import _snapshots
from g6.knowledge._registry import Dependent, ProjectStore, Refused
from g6.knowledge._scope import ScopeDenied
from tests.knowledge.corpus import SECRET, SECRET_SUBJECT, SUBJECTS
from tests.knowledge.corpus.rig import NOW, make

DOCS, OTHER = SUBJECTS[0], SUBJECTS[2]


def test_snapshot_reconstructs_the_exact_rendered_excerpts(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    rig.add(retention="audit")
    response = rig.search(f"{DOCS} {SECRET_SUBJECT}")
    assert response.snapshot.startswith("snap-") and response.payload["items"]
    path = rig.folder / "docs" / f"{DOCS}.md"
    rebuilt = _snapshots.reconstruct(rig.local("fetch"), response.snapshot)
    assert rebuilt["state"] == "reconstructed" and rebuilt["exact"] is True
    assert [i["view_text"] for i in rebuilt["items"]] == [i["view"]["text"] for i in response.payload["items"]]
    assert rebuilt["generation"] == response.generation and rebuilt["versions"]["chunker"] == engine.CHUNKER
    assert set(rebuilt["versions"]) == {"converter", "chunker", "index_schema", "g6"}
    assert SECRET not in json.dumps(rebuilt) and SECRET not in ProjectStore(rig.binding).snapshot(response.snapshot)
    # the snapshot is of what was returned: it does not follow the file
    kept = ProjectStore(rig.binding).snapshot(response.snapshot)
    path.write_text("entirely different words now\n", encoding="utf-8")
    assert ProjectStore(rig.binding).snapshot(response.snapshot) == kept
    with pytest.raises(ScopeDenied):
        other = make(tmp_path, monkeypatch, name="other", build=False)
        ProjectStore(rig.binding).use(other.local(), [], at=NOW)
    assert _snapshots.reconstruct(make(tmp_path, monkeypatch, name="other", build=False).local("fetch"),
                                  response.snapshot)["state"] == "unavailable"


def test_no_retention_means_reconstruction_unavailable_and_says_so(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    rig.add()   # retention "none"
    response = rig.search(DOCS)
    assert response.payload["items"] and response.snapshot == ""
    store = ProjectStore(rig.binding)
    assert not (store.folder / "retained").exists()
    for ident in ("", "snap-" + "0" * 32):
        rebuilt = _snapshots.reconstruct(rig.local("fetch"), ident)
        assert rebuilt["state"] == "unavailable" and "retention" in rebuilt["reason"] and rebuilt["items"] == []


def test_retained_bytes_are_not_searchable(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    source = rig.add(retention="audit")["source"]
    store = ProjectStore(rig.binding)
    kept = sorted((store.folder / "retained").iterdir())
    assert len(kept) == 49
    engine.revoke(rig.local("revoke"), source)
    assert rig.items(DOCS) == [] and len(sorted((store.folder / "retained").iterdir())) == 49
    # another source's search never reads the retained folder: the same words there return nothing
    (rig.project / "more").mkdir()
    (rig.project / "more" / "extra.md").write_text("# The pangolin decision\n\nThe pangolin note.\n", encoding="utf-8")
    rig.add(str(rig.project / "more"))
    assert rig.items("pangolin") and rig.items(f"{DOCS} convention") == []
    assert store.retained("0" * 64) is None and store.retained("../registry.db") is None


def test_complete_delete_removes_bytes_and_keeps_only_tombstone_ids(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    source = rig.add(retention="audit")["source"]
    response = rig.search(DOCS)
    store = ProjectStore(rig.binding)
    ids = {pid for m in store.members(1) for pid in m.passage_ids}
    partial = engine.delete(rig.local("delete"), source, complete=False, attribution="Person")
    assert partial["state"] == "deleted" and partial["audit_copies"] == "kept" and rig.items(DOCS) == []
    assert (store.folder / "retained").exists() and store.snapshot(response.snapshot) is not None
    assert _snapshots.reconstruct(rig.local("fetch"), response.snapshot)["state"] == "unavailable"
    done = engine.delete(rig.local("delete"), source, complete=True, attribution="Person")
    assert done["audit_copies"] == "removed" and "cannot be recalled" in done["note"]
    assert not list((store.folder / "retained").glob("*")) and store.snapshot(response.snapshot) is None
    assert _snapshots.reconstruct(rig.local("fetch"), response.snapshot)["state"] == "unavailable"
    registration, = store.registrations()
    assert registration.state == "deleted" and registration.root == "" and registration.label == ""
    assert {t.reason for t in store.tombstones()} == {"deleted"}
    for generation in store.generations():
        assert store.members(generation.number) == []
        index = store.index_path("sqlite", generation.number)
        if index.exists():
            assert engine._lexical(store, generation.number).status()["passages"] == 0
    everything = b"".join(p.read_bytes() for p in store.folder.rglob("*") if p.is_file())
    assert f"{DOCS} convention".encode() not in everything and b"docs/" not in everything
    assert not any(pid.encode() in everything for pid in ids)
    with pytest.raises(Refused):   # a deleted registration cannot be revoked back to life or deleted into something else
        engine.delete(rig.local("delete"), "src-0000000000000000", complete=True, attribution="Person")
    assert rig.add()["source"] != source and rig.items(DOCS)


def test_dependents_are_invalidated_by_revoke_change_and_delete(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    first = rig.add(str(rig.folder / "docs"), retention="audit")["source"]
    second = rig.add(str(rig.folder / "notes"))["source"]
    store = ProjectStore(rig.binding)
    one, other = rig.items(DOCS)[0], rig.items(OTHER)[0]
    note = rig.items(SUBJECTS[1])[0]
    generation = store.published().number
    for ident, passage, source in (("pack-a", one, first), ("pack-b", other, first), ("pack-c", note, second)):
        store.depend(Dependent(kind="pack", ident=ident, registration_id=source,
                               passage_ids=(passage["passage_id"],), generation=generation))
    snapshot = rig.search(DOCS).snapshot
    assert snapshot and all(store.invalidated("pack", i, at=NOW) == [] for i in ("pack-a", "pack-b", "pack-c"))
    assert store.invalidated("snapshot", snapshot, at=NOW) == []
    assert [d.why for d in store.invalidated("pack", "never-registered", at=NOW)] == ["unknown"]
    # change: only the consumers of the changed file
    path = rig.folder / "docs" / f"{DOCS}.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nedited\n", encoding="utf-8")
    assert rig.items(DOCS) == []
    assert [d.why for d in store.invalidated("pack", "pack-a", at=NOW)] == ["changed"]
    assert [d.why for d in store.invalidated("snapshot", snapshot, at=NOW)] == ["changed"]
    assert store.invalidated("pack", "pack-b", at=NOW) == [] and store.invalidated("pack", "pack-c", at=NOW) == []
    assert _snapshots.reconstruct(rig.local("fetch"), snapshot)["state"] == "unavailable"
    engine.refresh(rig.local("refresh"), clock=lambda: NOW)
    assert [d.why for d in store.invalidated("pack", "pack-a", at=NOW)] == ["changed"], "a refresh publishes new ids; it does not revive a consumer of the old ones"
    # revoke: every consumer of that registration
    engine.revoke(rig.local("revoke"), first)
    assert [d.why for d in store.invalidated("pack", "pack-b", at=NOW)] == ["revoked"]
    assert store.invalidated("pack", "pack-c", at=NOW) == []
    # delete
    engine.delete(rig.local("delete"), second, complete=True, attribution="Person")
    assert [d.why for d in store.invalidated("pack", "pack-c", at=NOW)] == ["revoked"]
