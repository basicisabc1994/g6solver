# SPDX-License-Identifier: Apache-2.0
"""Persistent ColBERT with a deterministic stand-in encoder (no weights are loaded): vectors survive a restart and
are not encoded again, incremental changes match a rebuild, the candidate stage is bounded, a stop mid-add leaves
whole passages only, another pin refuses the store, and a lexical fallback is never called meaning."""
from __future__ import annotations

import hashlib

import pytest

from g6.knowledge._index import OVER_BUDGET, IndexRefused
from g6.knowledge._types import Document, Source
from g6.knowledge.ctx_colbert import persistent, readiness
from g6.knowledge.ctx_colbert.persistent import MAX_CANDIDATES, PersistentColbertIndex

PIN = readiness.ModelPin(name="stand-in", revision="r1", tokenizer_sha256="1" * 64, config_sha256="2" * 64,
                         weights_sha256="3" * 64, dimension=8, max_tokens=64, precision="float32",
                         chunker="words:200:20", schema_version="v1")


class StandIn:
    """One unit vector per word, the same for the same word: a query's words score highest on passages holding them."""

    def __init__(self, why="", fail_after=None):
        self.why, self.fail_after, self.calls = why, fail_after, 0

    def ready(self):
        return self.why

    def encode(self, texts, *, query=False):
        if not query:
            self.calls += 1
            if self.fail_after is not None and self.calls > self.fail_after:
                raise RuntimeError("the process was stopped while encoding")
        out = []
        for text in texts:
            vectors = []
            for word in text.lower().split()[:PIN.max_tokens]:
                seed = hashlib.sha256(word.encode()).digest()
                raw = [b - 127.5 for b in seed[:PIN.dimension]]
                norm = sum(v * v for v in raw) ** 0.5
                vectors.append([v / norm for v in raw])
            out.append(vectors)
        return out


def passages(count, prefix="p"):
    out = []
    for n in range(count):
        text = f"note {prefix}{n} about subject{prefix}{n} and the shared convention"
        source = Source.of(f"g6-knowledge://src-{'4' * 16}/{prefix}{n}.md", text.encode(), retrieved_at="2026-11-11T12:00:00Z")
        out.append(Document(text=text, source=source).passage(0, len(text)))
    return out


def store(tmp_path, encoder=None, pin=PIN, **kw):
    return PersistentColbertIndex(tmp_path / "vectors.db", pin, max_ram_bytes=kw.pop("max_ram_bytes", 10**9),
                                  encoder=encoder if encoder is not None else StandIn(), **kw)


def test_vectors_survive_restart_and_are_not_re_encoded(tmp_path):
    items = passages(20)
    first = store(tmp_path, batch=4)
    assert first.add(items).added == 20 and first.encoded == 20
    again = store(tmp_path)   # a new process
    assert again.add(items).model_dump()["known"] == 20 and again.encoded == 0
    found = again.find("subjectp7", top_k=3)
    assert found.outcome == "ok" and found.hits[0].passage == items[7] and found.evidence == {"rung": "colbert", "candidates": 20}
    assert again.status()["passages"] == 20


def test_incremental_add_and_remove_match_a_rebuild(tmp_path):
    items = passages(12)
    grown = store(tmp_path / "a")
    grown.add(items[:8])
    grown.add(items[6:])
    assert grown.remove([items[0].id, items[1].id, "f" * 64]).removed == [items[0].id, items[1].id]
    rebuilt = store(tmp_path / "b")
    rebuilt.add(items[2:])
    for query in ("subjectp5", "shared convention", "subjectp11 note"):
        one, other = grown.find(query, top_k=5), rebuilt.find(query, top_k=5)
        assert [(h.passage.id, round(h.score, 6)) for h in one.hits] == [(h.passage.id, round(h.score, 6)) for h in other.hits]
    assert items[0].id not in {h.passage.id for h in grown.find("subjectp0", top_k=12).hits}


def test_candidate_stage_is_bounded_at_2000(tmp_path):
    index = store(tmp_path)
    index.add(passages(6))
    too_many = index.find("subjectp1", candidates=[f"{n:064x}" for n in range(MAX_CANDIDATES + 1)])
    assert too_many.outcome == "failed" and too_many.evidence["code"] == persistent.TOO_MANY and too_many.hits == []
    assert MAX_CANDIDATES == 2_000
    some = index.find("subjectp1", candidates=[p.id for p in passages(6)[:3]])
    assert some.evidence["candidates"] == 3 and {h.passage.id for h in some.hits} <= {p.id for p in passages(6)[:3]}
    import sqlite3

    conn = sqlite3.connect(index.path)
    row = conn.execute("SELECT * FROM vectors LIMIT 1").fetchone()
    conn.executemany("INSERT INTO vectors VALUES (?, ?, ?, ?, ?, ?)",
                     [(f"{n:064x}", row[1], row[2], row[3], row[4], row[5]) for n in range(MAX_CANDIDATES)])
    conn.commit()
    conn.close()
    unbounded = index.find("subjectp1")
    assert unbounded.outcome == "failed" and "candidate stage is required" in unbounded.reason, "never a full scan"


def test_crash_mid_add_leaves_whole_passages_only(tmp_path):
    items = passages(10)
    with pytest.raises(RuntimeError):
        store(tmp_path, StandIn(fail_after=2), batch=3).add(items)
    after = store(tmp_path)
    assert after.status()["passages"] == 6, "two whole batches, nothing of the third"
    for hit in after.find("shared convention", top_k=10).hits:
        assert hit.passage in items[:6]
    assert after.add(items).added == 4 and after.status()["passages"] == 10


def test_changed_pin_refuses_the_old_store(tmp_path):
    store(tmp_path).add(passages(3))
    other = PIN.model_copy(update={"revision": "r2"})
    assert PIN.digest != other.digest
    changed = store(tmp_path, pin=other)
    assert changed.find("subjectp1").outcome == "failed" and changed.find("subjectp1").evidence["code"] == persistent.PIN_CHANGED
    with pytest.raises(IndexRefused) as refused:
        changed.add(passages(1, "q"))
    assert refused.value.code == persistent.PIN_CHANGED
    assert store(tmp_path).status()["passages"] == 3


def test_failed_load_is_unavailable_with_reason(tmp_path):
    down = store(tmp_path, StandIn(why="the weights file is missing from the local cache"))
    found = down.find("subjectp1")
    assert found.outcome == "unavailable" and "weights file is missing" in found.reason and found.evidence == {"rung": ""}
    with pytest.raises(IndexRefused):
        down.add(passages(1))
    assert PersistentColbertIndex(tmp_path / "v.db", PIN, max_ram_bytes=10**9).find("x").outcome == "unavailable"
    files = {}
    for role in ("tokenizer", "config", "weights"):
        files[role] = tmp_path / role
        files[role].write_bytes(role.encode())
    pin = PIN.model_copy(update={f"{r}_sha256": hashlib.sha256(r.encode()).hexdigest() for r in files})
    assert "not located" in readiness.ready(pin)
    assert "missing" in readiness.ready(pin, files={**files, "weights": tmp_path / "absent"})
    assert "pinned hash" in readiness.ready(PIN, files=files)
    assert "not one the scale row has measured" in readiness.ready(pin, files=files, platform="win32")
    assert readiness.ready(pin, files=files, platform="linux", measured=frozenset({"linux"})) == ""
    assert "under the declared limit" in readiness.ready(pin, files=files, platform="linux", measured=frozenset({"linux"}),
                                                         free_bytes=10, max_ram_bytes=100)
    with pytest.raises(IndexRefused):
        persistent.open_for(None, 1)


def test_bm25_fallback_is_named_and_not_called_semantic(tmp_path):
    items = passages(5)
    fallback = persistent.lexical_fallback("subjectp3 convention", items, top_k=3)
    assert fallback.backend == "colbert_persistent" and fallback.score_scale == "bm25"
    assert fallback.evidence["rung"] == "bm25" and fallback.evidence["fallback"] is True and fallback.evidence["semantic"] is False
    assert fallback.hits[0].passage == items[3]
    from g6._context_engine._envelope import BackendReport
    from g6._context_engine._fuse import fuse

    report = BackendReport(backend=fallback.backend, route="meaning", state="answered", rung=fallback.evidence["rung"])
    fused = fuse([(report, fallback.hits, 1)], top_k=3)
    assert all("not semantic" in c.reason for c in fused.contributions)


def test_memory_limit_is_enforced_before_encoding(tmp_path):
    encoder = StandIn()
    small = store(tmp_path, encoder, max_ram_bytes=100)
    with pytest.raises(IndexRefused) as refused:
        small.add(passages(4))
    assert refused.value.code == OVER_BUDGET and encoder.calls == 0 and small.status()["passages"] == 0
    with pytest.raises(IndexRefused):
        PersistentColbertIndex(tmp_path / "x.db", PIN, max_ram_bytes=10**9, device="cuda")


def test_scope_and_generation_filters_apply_to_candidates(tmp_path):
    mine, theirs, old = passages(3, "m"), passages(3, "t"), passages(3, "o")
    writer = store(tmp_path)
    writer.add(mine, registration_id="src-" + "1" * 16, generation=2)
    writer.add(theirs, registration_id="src-" + "2" * 16, generation=2)
    writer.add(old, registration_id="src-" + "1" * 16, generation=1)
    scoped = store(tmp_path, registrations=["src-" + "1" * 16], generation=2)
    everything = [p.id for p in (*mine, *theirs, *old)]
    assert {h.passage.id for h in scoped.find("shared convention", top_k=20).hits} == {p.id for p in mine}
    assert {h.passage.id for h in scoped.find("shared convention", top_k=20, candidates=everything).hits} == {p.id for p in mine}
    assert store(tmp_path, registrations=[], generation=2).find("shared").outcome == "empty"