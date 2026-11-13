# SPDX-License-Identifier: Apache-2.0
"""Refresh: changed files get new passage ids and their old ones are tombstoned first; readers keep the generation
they started on; no crash point exposes a changed passage or half a generation; a failed refresh serves nothing
that changed."""
from __future__ import annotations

import shutil
import subprocess
import time

import pytest

from g6._context_engine import _engine as engine
from g6.knowledge._index import IndexRefused
from g6._context_engine._registry import ProjectStore
from tests.knowledge.corpus import SUBJECTS
from tests.knowledge.corpus.rig import NOW, make

DOCS, OTHER, NOTES = SUBJECTS[0], SUBJECTS[2], SUBJECTS[1]


def edit(rig, subject=DOCS, extra="\nA later ruling: the pangolin clause applies.\n"):
    path = rig.folder / "docs" / f"{subject}.md"
    path.write_text(path.read_text(encoding="utf-8") + extra, encoding="utf-8")
    return path


def refresh(rig, source=None):
    return engine.refresh(rig.local("refresh"), source, clock=lambda: NOW)


def test_refresh_publishes_new_ids_and_tombstones_old_ones(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    source = rig.add()["source"]
    old = {i["passage_id"] for i in rig.items(DOCS)}
    untouched = {i["passage_id"] for i in rig.items(OTHER)}
    edit(rig)
    started = time.monotonic()
    done = refresh(rig)
    assert time.monotonic() - started < 30, "the protocol's readiness limit for the 46-file corpus"
    assert done["state"] == "published" and done["generation"] == 2
    row, = done["sources"]
    assert (row["source"], row["changed"], row["removed"], row["added"]) == (source, 1, 0, 0)
    assert row["unchanged"] == 48 and row["basis"] == "file_hash"
    new = {i["passage_id"] for i in rig.items(DOCS)}
    assert new and not new & old
    assert rig.items("pangolin")[0]["path"] == f"docs/{DOCS}.md"
    assert {i["passage_id"] for i in rig.items(OTHER)} == untouched, "an unchanged file keeps its passage ids"
    store = ProjectStore(rig.binding)
    assert [(t.reason, t.uri.endswith(f"docs/{DOCS}.md"), t.generation) for t in store.tombstones()] == [("changed", True, 1)]
    for passage in old:
        found, _ = engine.fetch(rig.local("fetch"), passage, clock=lambda: NOW)
        assert found["passage"] is None
    receipts = store.receipts(2)
    assert {r.state for r in receipts} == {"read_back"}
    assert {r.item for r in receipts if r.action == "remove"} == old
    assert {r.item for r in receipts if r.action == "add"} == {
        pid for m in store.members(2) if m.relative == f"docs/{DOCS}.md" for pid in m.passage_ids}
    assert refresh(rig)["state"] == "unchanged" and store.published().number == 2


def test_readers_pin_the_generation_they_started_on(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    rig.add()
    edit(rig)

    def refresh_here(name):
        if name == "search:found":
            monkeypatch.setattr(engine, "checkpoint", lambda n: None)
            assert refresh(rig)["state"] == "published"

    monkeypatch.setattr(engine, "checkpoint", refresh_here)
    in_flight = rig.search(f"{DOCS} {OTHER}")   # found its hits in generation 1; generation 2 was published meanwhile
    assert in_flight.generation == 1
    paths = {i["path"] for i in in_flight.payload["items"]}
    assert f"docs/{OTHER}.md" in paths and f"docs/{DOCS}.md" not in paths
    assert all(c["generation"] == 1 for i in in_flight.payload["items"] for c in i["contributions"])
    assert "changed" in {d.why for d in in_flight.denied}
    after = rig.search(f"{DOCS} {OTHER}")
    assert after.generation == 2 and {f"docs/{OTHER}.md", f"docs/{DOCS}.md"} <= {i["path"] for i in after.payload["items"]}


@pytest.mark.parametrize("point", ["refresh:tombstoned", "refresh:pending", "refresh:acknowledged", "refresh:read_back"])
def test_crash_mid_refresh_exposes_neither_old_changed_nor_half_new(tmp_path, monkeypatch, point):
    rig = make(tmp_path, monkeypatch)
    rig.add()
    old = {i["passage_id"] for i in rig.items(DOCS)}
    edit(rig)

    def crash(name):
        if name == point:
            raise RuntimeError("the process died here")

    monkeypatch.setattr(engine, "checkpoint", crash)
    with pytest.raises(RuntimeError):
        refresh(rig)
    monkeypatch.setattr(engine, "checkpoint", lambda name: None)
    store = ProjectStore(rig.binding)   # what a new process sees
    assert store.published().number == 1
    during = rig.search(f"{DOCS} {OTHER} pangolin")
    assert during.generation == 1
    paths = {i["path"] for i in during.payload["items"]}
    assert f"docs/{OTHER}.md" in paths and f"docs/{DOCS}.md" not in paths, "neither the old text nor the new"
    assert not old & {i["passage_id"] for i in during.payload["items"]}
    done = refresh(rig)   # running it again completes the work
    assert done["state"] == "published"
    assert [g.state for g in store.generations()].count("published") == 2
    assert all(g.state != "staging" for g in store.generations())
    assert rig.items("pangolin") and not old & {i["passage_id"] for i in rig.items(DOCS)}
    assert refresh(rig)["state"] == "unchanged"


def test_failed_refresh_reports_unavailable_and_serves_no_changed_passage(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    rig.add()
    old = {i["passage_id"] for i in rig.items(DOCS)}
    edit(rig)
    real = engine._lexical

    class Broken:
        def __init__(self, inner):
            self.inner = inner

        def add(self, passages):
            raise IndexRefused("G6_E_KNOWLEDGE_INDEX_FAILED", "the disk is full")

        def __getattr__(self, name):
            return getattr(self.inner, name)

    monkeypatch.setattr(engine, "_lexical", lambda s, g: Broken(real(s, g)))
    failed = refresh(rig)
    assert failed["state"] == "unavailable" and failed["generation"] == 1 and "G6_E_KNOWLEDGE_INDEX_FAILED" in failed["reason"]
    assert rig.items(DOCS) == [] and rig.items("pangolin") == [] and rig.items(OTHER)
    assert "changed" in {d.why for d in rig.search(DOCS).denied}
    monkeypatch.setattr(engine, "_lexical", real)
    assert refresh(rig)["state"] == "published"
    assert rig.items("pangolin") and not old & {i["passage_id"] for i in rig.items(DOCS)}


def test_removed_file_is_tombstoned_at_once(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    rig.add()
    old = rig.items(DOCS)[0]["passage_id"]
    (rig.folder / "docs" / f"{DOCS}.md").unlink()

    def crash(name):
        if name == "refresh:pending":
            raise RuntimeError("stopped before anything was staged")

    monkeypatch.setattr(engine, "checkpoint", crash)
    with pytest.raises(RuntimeError):
        refresh(rig)
    monkeypatch.setattr(engine, "checkpoint", lambda name: None)
    store = ProjectStore(rig.binding)
    assert [t.reason for t in store.tombstones()] == ["changed"] and store.published().number == 1
    members, denied = store.use(rig.local(), [old], at=NOW)
    assert members == [] and denied[0].why == "changed"
    done = refresh(rig)
    assert done["sources"][0]["removed"] == 1 and done["state"] == "published"
    assert rig.items(DOCS) == [] and rig.items(OTHER)
    assert not [m for m in store.members(done["generation"]) if m.relative == f"docs/{DOCS}.md"]


def test_new_file_needs_a_new_preview(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    rig.add()
    (rig.folder / "docs" / "late.md").write_text("# The pangolin decision\n\nThe pangolin note.\n", encoding="utf-8")
    done = refresh(rig)
    assert done["state"] == "unchanged" and done["sources"][0]["added"] == 1
    assert done["sources"][0]["needs_a_new_preview"] == ["docs/late.md"]
    assert rig.items("pangolin") == []
    edit(rig)
    assert refresh(rig)["state"] == "published" and rig.items(f"{DOCS} decision")
    assert all(i["path"] != "docs/late.md" for i in rig.items("pangolin", top_k=20))


def test_refresh_by_commit_records_the_commit(tmp_path, monkeypatch):
    if shutil.which("git") is None:
        pytest.skip("not reached: no git program on this machine")
    rig = make(tmp_path, monkeypatch)

    def git(*args):
        done = subprocess.run(["git", "-C", str(rig.project), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                               "-c", "commit.gpgsign=false", *args], capture_output=True, text=True, check=False)
        assert done.returncode == 0, done.stderr
        return done.stdout.strip()

    git("init", "-q")
    git("add", "-A")
    git("commit", "-q", "-m", "the corpus")
    source = rig.add()["source"]
    store = ProjectStore(rig.binding)
    first = refresh(rig)
    if first["sources"][0]["basis"] != "commit":
        pytest.skip("not reached: git did not answer through ctx_git here")
    assert first["sources"][0]["commit"] == git("rev-parse", "HEAD")
    edit(rig)
    dirty = refresh(rig)
    assert dirty["state"] == "published" and dirty["sources"][0]["basis"] == "file_hash" and dirty["sources"][0]["commit"] == ""
    git("add", "-A")
    git("commit", "-q", "-m", "an edit")
    clean = refresh(rig)
    assert clean["sources"][0]["basis"] == "commit" and clean["sources"][0]["commit"] == git("rev-parse", "HEAD")
    kept = store.rereads(source)
    assert [r.basis for r in kept] == ["commit", "file_hash", "commit"] and kept[-1].commit == git("rev-parse", "HEAD")
    assert all(r.at for r in kept)


def test_a_file_put_back_as_it_was_is_served_again_after_refresh(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    rig.add()
    path = rig.folder / "docs" / f"{DOCS}.md"
    original = path.read_bytes()
    old = {i["passage_id"] for i in rig.items(DOCS)}
    edit(rig)
    assert rig.items(DOCS) == []   # the search observed the change and denied the file
    (rig.project / "more").mkdir()
    (rig.project / "more" / "extra.md").write_text("# The pangolin decision\n\nThe pangolin note.\n", encoding="utf-8")
    rig.add(str(rig.project / "more"))   # another generation is built while the file is still denied
    assert rig.items(DOCS) == []
    path.write_bytes(original)
    assert rig.items(DOCS) == [], "denied until a refresh has read it again"
    done = refresh(rig)
    assert done["state"] == "published"
    assert {i["passage_id"] for i in rig.items(DOCS)} == old
