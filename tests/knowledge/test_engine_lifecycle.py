# SPDX-License-Identifier: Apache-2.0
"""Generations, tombstones, expiry and change: nothing half-published is readable, a revoke or an expiry is honoured
by the reply being built, a changed file is never served, and two processes on one project see one order."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import timedelta

import pytest

from g6._context_engine import _engine as engine
from g6._context_engine._registry import NOT_PUBLISHED, PREVIEW_USED, Member, ProjectStore, Refused
from g6._context_engine._scope import ROOTS_ENV
from g6.knowledge._types import instant
from tests.knowledge.corpus import SUBJECTS
from tests.knowledge.corpus.rig import NOW, make

DOCS, NOTES = SUBJECTS[0], SUBJECTS[1]   # a subject filed under docs/ and one under notes/


def test_publish_requires_read_back_of_every_passage(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    rig.add(str(rig.folder / "docs"))
    store = ProjectStore(rig.binding)
    assert store.published().number == 1
    real = engine._lexical

    class Short:
        """An index that silently keeps one passage fewer than it was given."""

        def __init__(self, inner):
            self.inner, self.short = inner, False

        def add(self, passages):
            if not self.short and len(passages) > 1:
                self.short, passages = True, passages[:-1]
            return self.inner.add(passages)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    monkeypatch.setattr(engine, "_lexical", lambda s, g: Short(real(s, g)))
    with pytest.raises(Refused) as refused:
        rig.add(str(rig.folder / "notes"))
    assert refused.value.code == NOT_PUBLISHED
    monkeypatch.setattr(engine, "_lexical", real)
    assert store.published().number == 1
    assert rig.items(DOCS) and rig.items(NOTES) == []
    assert [r.state for r in store.registrations()] == ["active", "revoked"]
    # the registry itself refuses, whatever the engine computed
    staged = store.stage({"sqlite": True})
    store.record(staged.number, [Member(registration_id=store.registrations()[0].id, generation=staged.number,
                                        uri="g6-knowledge://x/a.md", relative="a.md", file_sha256="0" * 64, size=1,
                                        document_sha256="0" * 64, passage_ids=("1" * 64, "2" * 64),
                                        spans=((0, 1), (1, 2)))])
    for receipt in ({}, {"sqlite": {"written": 2, "read_back": 1}}, {"sqlite": {"written": 1, "read_back": 1}}):
        with pytest.raises(Refused):
            store.publish(staged.number, receipt)
    assert store.published().number == 1


def test_search_before_first_publish_says_not_published_not_empty(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    response = rig.search(DOCS)
    assert response.generation is None and response.payload["items"] == []
    assert [b.state for b in response.backends] == ["unavailable"]
    assert "not_published" in response.backends[0].reason
    found, _ = engine.fetch(rig.local("fetch"), "0" * 64, clock=lambda: NOW)
    assert [d["why"] for d in found["denied"]] == ["not_published"]


@pytest.mark.parametrize("point", ["search:found", "search:checked"])
def test_revoke_denies_before_deletion(tmp_path, monkeypatch, point):
    from g6.knowledge.knowledge_indexer.index import SqliteIndex

    rig = make(tmp_path, monkeypatch)
    source = rig.add()["source"]
    store = ProjectStore(rig.binding)
    first = rig.items(DOCS)
    assert first

    def revoke_here(name):
        if name == point:
            engine.revoke(rig.local("revoke"), source)

    monkeypatch.setattr(engine, "checkpoint", revoke_here)
    in_flight = rig.search(DOCS)   # started before the revoke, finished after it
    monkeypatch.setattr(engine, "checkpoint", lambda name: None)
    assert in_flight.payload["items"] == []
    assert {d.why for d in in_flight.denied} == {"revoked"}
    # the rows are still in the index file: denial does not wait for deletion
    lexical = SqliteIndex("lexical", path=store.index_path("sqlite", store.published().number))
    assert lexical.find(DOCS).hits
    ids = [pid for m in store.members(store.published().number) for pid in m.passage_ids]
    members, denied = store.use(rig.local(), ids, at=NOW)
    assert members == [] and {d.why for d in denied} == {"revoked"} and len(denied) == len(ids)
    assert rig.items(DOCS) == []
    found, refs = engine.fetch(rig.local("fetch"), first[0]["passage_id"], clock=lambda: NOW)
    assert found["passage"] is None and refs == () and found["denied"][0]["why"] == "revoked"
    assert store.tombstones()[0].reason == "revoked" and store.registrations()[0].deny_epoch == 1


def test_revoked_registration_cannot_come_back(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    shown = engine.preview(rig.binding, str(rig.folder))
    first = engine.commit(rig.binding, shown, "Person")
    old = rig.items(DOCS)[0]["passage_id"]
    engine.revoke(rig.local("revoke"), first["source"])
    with pytest.raises(Refused) as refused:
        engine.commit(rig.binding, shown, "Person")
    assert refused.value.code == PREVIEW_USED
    assert rig.items(DOCS) == []
    second = rig.add()
    assert second["source"] != first["source"]
    items = rig.items(DOCS)
    assert items and {i["source"] for i in items} == {second["source"]}
    assert old not in {i["passage_id"] for i in items}
    found, _ = engine.fetch(rig.local("fetch"), old, clock=lambda: NOW)
    assert found["passage"] is None
    states = {r.id: r.state for r in ProjectStore(rig.binding).registrations()}
    assert states == {first["source"]: "revoked", second["source"]: "active"}


def test_expiry_is_checked_against_the_clock_at_use(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    added = rig.add(expires_days=1)
    expires = instant(added["expires_at"])
    before, after = expires - timedelta(seconds=1), expires + timedelta(seconds=1)
    store = ProjectStore(rig.binding)
    state = (store.registrations(), store.tombstones(), store.published())
    live = rig.items(DOCS, clock=lambda: before)
    assert live
    late = rig.search(DOCS, clock=lambda: after)
    assert late.payload["items"] == [] and {d.why for d in late.denied} == {"expired"}
    assert rig.search(DOCS, clock=lambda: expires).payload["items"] == []
    found, refs = engine.fetch(rig.local("fetch"), live[0]["passage_id"], clock=lambda: after)
    assert found["passage"] is None and refs == () and found["denied"][0]["why"] == "expired"
    assert (store.registrations(), store.tombstones(), store.published()) == state
    # an expiry that arrives between the search and the return is honoured by that reply
    ticks = iter([before, after, after])
    crossing = rig.search(DOCS, clock=lambda: next(ticks))
    assert crossing.payload["items"] == [] and {d.why for d in crossing.denied} == {"expired"}


def test_changed_file_is_denied_until_refresh(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    rig.add()
    old = rig.items(DOCS)[0]
    path = rig.folder / "docs" / f"{DOCS}.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nA later edit.\n", encoding="utf-8")
    response = rig.search(f"{DOCS} {NOTES}")
    paths = {i["path"] for i in response.payload["items"]}
    assert f"docs/{DOCS}.md" not in paths and f"notes/{NOTES}.txt" in paths
    assert "changed" in {d.why for d in response.denied}
    store = ProjectStore(rig.binding)
    assert [t.reason for t in store.tombstones()] == ["changed"]
    again = rig.search(DOCS)
    assert again.payload["items"] == [] and {d.why for d in again.denied} == {"changed"}
    assert len(store.tombstones()) == 1, "a known change is denied at use, not observed again"
    found, _ = engine.fetch(rig.local("fetch"), old["passage_id"], clock=lambda: NOW)
    assert found["passage"] is None and found["denied"][0]["why"] == "changed"
    # another folder added later does not bring the changed file's old passages back
    (rig.project / "more").mkdir()
    (rig.project / "more" / "extra.md").write_text("# The pangolin decision\n\nThe pangolin note.\n", encoding="utf-8")
    rig.add(str(rig.project / "more"))
    assert rig.items("pangolin") and rig.items(DOCS) == []


def test_same_bytes_rewritten_is_still_intact(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    rig.add()
    path = rig.folder / "docs" / f"{DOCS}.md"
    data = path.read_bytes()
    path.unlink()
    path.write_bytes(data)
    os.utime(path, (time.time() + 60, time.time() + 60))
    items = rig.items(DOCS)
    assert items and all(i["checked"]["outcome"] == "intact" for i in items)
    assert ProjectStore(rig.binding).tombstones() == []


def test_crash_between_stage_and_publish_leaves_previous_generation(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    rig.add(str(rig.folder / "docs"))

    class Died(BaseException):
        """The process dying: nothing after this point runs, no clean-up included."""

    def crash(name):
        if name == "commit:recorded":
            raise Died()

    monkeypatch.setattr(engine, "checkpoint", crash)
    with pytest.raises(Died):
        rig.add(str(rig.folder / "notes"))
    monkeypatch.setattr(engine, "checkpoint", lambda name: None)
    store = ProjectStore(rig.binding)   # what a new process sees
    assert store.published().number == 1
    assert [(g.number, g.state) for g in store.generations()] == [(1, "published"), (2, "staging")]
    assert rig.items(DOCS) and rig.items(NOTES) == []
    assert rig.search(NOTES).generation == 1
    rig.add(str(rig.folder / "notes"))
    assert [(g.number, g.state) for g in store.generations()] == [(1, "published"), (2, "abandoned"), (3, "published")]
    assert not store.index_path("sqlite", 2).exists()
    assert rig.items(DOCS) and rig.items(NOTES)


_SEARCHER = """
import json, sys, time
from pathlib import Path
from g6._context_engine import _engine as engine
from g6._context_engine._envelope import SearchRequest
from g6._context_engine._scope import ROOTS_ENV, access, host_binding
project, word, stop = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
binding = host_binding(environ={ROOTS_ENV: str(project)}, start=project)
while not stop.exists():
    started = time.time()
    response, _ = engine.search(access(binding, "search", "local"), SearchRequest(query=word))
    print(json.dumps({"started": started, "finished": time.time(), "items": len(response.payload["items"])}), flush=True)
"""


def test_two_processes_revoke_and_search(tmp_path, monkeypatch):
    rig = make(tmp_path, monkeypatch)
    source = rig.add()["source"]
    stop = tmp_path / "stop"
    env = {**os.environ, "G6_HOME": str(rig.home), ROOTS_ENV: str(rig.project)}
    child = subprocess.Popen([sys.executable, "-c", _SEARCHER, str(rig.project), DOCS, str(stop)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    rows = []
    try:
        while len([r for r in rows if r["items"]]) < 3:
            line = child.stdout.readline()
            assert line, child.stderr.read()
            rows.append(json.loads(line))
        engine.revoke(rig.local("revoke"), source)
        revoked_at = time.time()   # the revoke's transaction is committed
        after = 0
        while after < 5:
            line = child.stdout.readline()
            assert line, child.stderr.read()
            rows.append(json.loads(line))
            after += rows[-1]["started"] > revoked_at
    finally:
        stop.write_text("stop", encoding="utf-8")
        try:
            child.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate()
    assert child.returncode == 0
    later = [r for r in rows if r["started"] > revoked_at]
    assert len(later) >= 5 and all(r["items"] == 0 for r in later), later
    assert any(r["items"] for r in rows if r["finished"] < revoked_at)
