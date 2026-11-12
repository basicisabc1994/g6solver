# SPDX-License-Identifier: Apache-2.0
"""Persistent ColBERT (design 12.3): token vectors kept by passage id, added and removed incrementally, kept across
restarts, and scored over a bounded candidate set.

This module is the part that does not depend on which library route the scale spike chooses (protocol 05): the store
of vectors by passage id, its pin, the candidate bound, the memory limit and the scoring by late interaction. The
encoder is passed in. Which encoder ships (PyLate's own, a PLAID build, or ColBERT over lexical candidates), on which
platforms and inside which envelope, waits for the spike; until then no generation records this backend, so the
``meaning`` route is not ready and says so.

* A store belongs to one :class:`~g6.knowledge.ctx_colbert.readiness.ModelPin`. Opening it under another pin is
  refused: vectors of another model are never read.
* ``add`` encodes only passages it does not hold, in batches, each batch written in one transaction: a stop in the
  middle leaves whole passages only. The memory a batch needs is estimated and checked against ``max_ram_bytes``
  before anything is encoded.
* ``find`` scores at most :data:`MAX_CANDIDATES` passages: the candidates it is given (from the lexical stage), or
  every passage it holds when that is within the bound. More is refused, never scanned. Its evidence names the rung
  that ranked and the number of candidates.
* When the encoder cannot run, ``find`` is ``unavailable`` with the reason. :func:`lexical_fallback` is the BM25
  rung, named as a lexical fallback and never as meaning.
"""
from __future__ import annotations

import json
import sqlite3
import struct
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

from g6.knowledge._index import (FAILED, OVER_BUDGET, UNAVAILABLE, Added, Hit, IndexRefused, Removed, Retrieves, Searched,
                                 bad_k)
from g6.knowledge._types import Passage
from g6.knowledge.ctx_colbert.readiness import ModelPin

MAX_CANDIDATES = 2_000
BACKEND = "colbert_persistent"
SCALE = "colbert_maxsim"
PIN_CHANGED = "G6_E_KNOWLEDGE_COLBERT_PIN"
TOO_MANY = "G6_E_KNOWLEDGE_COLBERT_CANDIDATES"
_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS pin (id INTEGER PRIMARY KEY CHECK (id = 1), digest TEXT NOT NULL, body TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS vectors (passage_id TEXT PRIMARY KEY, registration_id TEXT NOT NULL, "
    "generation INTEGER NOT NULL, tokens INTEGER NOT NULL, data BLOB NOT NULL, record TEXT NOT NULL)",
)


class Encoder(Protocol):
    """What turns text into one vector per token. ``ready`` is ``""`` or why it cannot run; it loads nothing."""

    def ready(self) -> str: ...

    def encode(self, texts: Sequence[str], *, query: bool = False) -> list[list[list[float]]]: ...


def _pack(vectors: Sequence[Sequence[float]], pin: ModelPin) -> bytes:
    code = "e" if pin.precision == "float16" else "f"
    flat = [float(x) for vector in vectors for x in vector]
    return struct.pack(f"<{len(flat)}{code}", *flat)


def _unpack(data: bytes, tokens: int, pin: ModelPin) -> list[list[float]]:
    code = "e" if pin.precision == "float16" else "f"
    flat = struct.unpack(f"<{tokens * pin.dimension}{code}", data)
    return [list(flat[i * pin.dimension:(i + 1) * pin.dimension]) for i in range(tokens)]


def maxsim(query: Sequence[Sequence[float]], passage: Sequence[Sequence[float]]) -> float:
    """Late interaction: for each query token, its best dot product with any passage token, summed."""
    return sum(max((sum(a * b for a, b in zip(q, p, strict=True)) for p in passage), default=0.0) for q in query)


class PersistentColbertIndex(Retrieves):
    backend = BACKEND
    score_scale = SCALE

    def __init__(self, path: Path, pin: ModelPin, *, max_ram_bytes: int, device: str = "cpu", batch: int = 16,
                 encoder: Encoder | None = None, registrations: Sequence[str] | None = None,
                 generation: int | None = None) -> None:
        if device != "cpu":
            raise IndexRefused(UNAVAILABLE, "only the cpu device is inside the measured envelope")
        if max_ram_bytes <= 0 or batch < 1:
            raise IndexRefused(OVER_BUDGET, "max_ram_bytes and batch are positive")
        self.path, self.pin, self.max_ram_bytes, self.batch = Path(path), pin, int(max_ram_bytes), int(batch)
        self.encoder, self.generation = encoder, generation
        self.registrations = None if registrations is None else frozenset(registrations)
        self.encoded = 0   # passages this object encoded: a restart that re-encodes nothing leaves it at 0

    def _connect(self, *, create: bool) -> sqlite3.Connection | None:
        if not create and not self.path.exists():
            return None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            if create:
                for statement in _SCHEMA:
                    conn.execute(statement)
                conn.execute("INSERT OR IGNORE INTO pin VALUES (1, ?, ?)", (self.pin.digest, self.pin.model_dump_json()))
            held = conn.execute("SELECT digest FROM pin WHERE id = 1").fetchone()
        except sqlite3.Error as exc:
            conn.close()
            raise IndexRefused(FAILED, f"the vector store cannot be read ({type(exc).__name__})") from None
        if held is None or held["digest"] != self.pin.digest:
            conn.close()
            raise IndexRefused(PIN_CHANGED, "the store was built under another model pin; its vectors are not read. "
                                            "Build a new store for this pin.")
        return conn

    def _unready(self) -> str:
        if self.encoder is None:
            return "no encoder is configured for this pin"
        return self.encoder.ready()

    def add(self, passages: Sequence[Passage], *, registration_id: str = "", generation: int = 0,
            still: Callable[[], str] | None = None) -> Added:
        """``still`` says, before every batch, why the passages' source may no longer be used (``""``: it may); the
        first reason ends the write: whole batches written before it stay, nothing after it is encoded or kept, and
        the result says ``stopped``."""
        why = self._unready()
        if why:
            raise IndexRefused(UNAVAILABLE, why)
        stopped = ""
        conn = self._connect(create=True)
        try:
            known = {r["passage_id"] for r in conn.execute("SELECT passage_id FROM vectors")}
            fresh = [p for p in dict((p.id, p) for p in passages).values() if p.id not in known]
            bytes_per = 2 if self.pin.precision == "float16" else 4
            written = 0
            for start in range(0, len(fresh), self.batch):
                stopped = still() if still is not None else ""
                if stopped:
                    break
                chunk = fresh[start:start + self.batch]
                need = sum(min(len(p.text.split()) + 2, self.pin.max_tokens) for p in chunk) * self.pin.dimension * bytes_per * 3
                if need > self.max_ram_bytes:
                    raise IndexRefused(OVER_BUDGET, f"encoding this batch needs about {need} bytes, over the declared "
                                                    f"limit of {self.max_ram_bytes}; nothing of it was encoded",
                                       {"needed": need, "max_ram_bytes": self.max_ram_bytes})
                vectors = self.encoder.encode([p.text for p in chunk])
                if len(vectors) != len(chunk) or any(len(v) > self.pin.max_tokens or any(
                        len(t) != self.pin.dimension for t in v) for v in vectors):
                    raise IndexRefused(FAILED, "the encoder did not give one pinned-size vector per token")
                conn.execute("BEGIN IMMEDIATE")
                for passage, tokens in zip(chunk, vectors, strict=True):
                    conn.execute("INSERT INTO vectors VALUES (?, ?, ?, ?, ?, ?)",
                                 (passage.id, registration_id, int(generation), len(tokens), _pack(tokens, self.pin),
                                  passage.model_dump_json()))
                conn.execute("COMMIT")
                self.encoded += len(chunk)
                written += len(chunk)
        except sqlite3.Error as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise IndexRefused(FAILED, f"the vector store could not be written ({type(exc).__name__})") from None
        finally:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            conn.close()
        return Added(added=written, known=len(passages) - len(fresh),
                     evidence={"stopped": stopped, "not_written": len(fresh) - written} if stopped else {})

    def remove(self, ids: Sequence[str]) -> Removed:
        wanted = list(dict.fromkeys(ids))
        conn = self._connect(create=False)
        if conn is None:
            return Removed(missing=wanted)
        try:
            removed = []
            conn.execute("BEGIN IMMEDIATE")
            for ident in wanted:
                if conn.execute("DELETE FROM vectors WHERE passage_id = ?", (ident,)).rowcount:
                    removed.append(ident)
            conn.execute("COMMIT")
        finally:
            conn.close()
        return Removed(removed=removed, missing=[i for i in wanted if i not in removed])

    def _rows(self, conn: sqlite3.Connection, candidates: Sequence[str] | None) -> list[sqlite3.Row]:
        query, args = "SELECT * FROM vectors WHERE 1 = 1", []
        if self.generation is not None:
            query, args = query + " AND generation = ?", [*args, self.generation]
        if self.registrations is not None:
            marks = ",".join("?" * len(self.registrations)) or "''"
            query, args = query + f" AND registration_id IN ({marks})", [*args, *sorted(self.registrations)]
        if candidates is None:
            return conn.execute(query + " ORDER BY passage_id LIMIT ?", [*args, MAX_CANDIDATES + 1]).fetchall()
        wanted = set(candidates)
        return [r for r in conn.execute(query + " ORDER BY passage_id", args) if r["passage_id"] in wanted]

    def find(self, query: str, top_k: int = 10, *, candidates: Sequence[str] | None = None) -> Searched:
        said = {"backend": self.backend, "query": query, "score_scale": self.score_scale}
        refused = bad_k(self.backend, query, self.score_scale, top_k)
        if refused is not None:
            return refused
        why = self._unready()
        if why:
            return Searched(**said, outcome="unavailable", reason=why, evidence={"rung": ""})
        if candidates is not None and len(set(candidates)) > MAX_CANDIDATES:
            return Searched(**said, outcome="failed", reason=f"more than {MAX_CANDIDATES} candidates were given; the "
                            "candidate stage is bounded and nothing was scored", evidence={"code": TOO_MANY})
        try:
            conn = self._connect(create=False)
        except IndexRefused as exc:
            return Searched(**said, outcome="failed", reason=exc.reason, evidence={"code": exc.code})
        if conn is None:
            return Searched(**said, outcome="empty", searched=0, reason="no vectors are stored", evidence={"rung": "colbert"})
        try:
            rows = self._rows(conn, candidates)
        finally:
            conn.close()
        if len(rows) > MAX_CANDIDATES:
            return Searched(**said, outcome="failed", reason=f"the store holds more than {MAX_CANDIDATES} passages in "
                            "scope and no candidates were given; a candidate stage is required",
                            evidence={"code": TOO_MANY})
        if not rows:
            return Searched(**said, outcome="empty", searched=0, reason="no stored passage is in scope",
                            evidence={"rung": "colbert", "candidates": 0})
        tokens = self.encoder.encode([query], query=True)[0]
        scored = []
        for row in rows:
            passage = Passage(**json.loads(row["record"]))
            scored.append((passage, maxsim(tokens, _unpack(row["data"], row["tokens"], self.pin))))
        order = sorted(range(len(scored)), key=lambda i: (-scored[i][1], scored[i][0].id))[:top_k]
        hits = [Hit(passage=scored[i][0], score=scored[i][1], score_scale=self.score_scale, rank=n)
                for n, i in enumerate(order, start=1)]
        return Searched(**said, outcome="ok", hits=hits, searched=len(rows),
                        evidence={"rung": "colbert", "candidates": len(rows)})

    def status(self) -> dict[str, Any]:
        out = {"backend": self.backend, "path": str(self.path), "exists": self.path.exists(), "passages": 0,
               "pin": self.pin.digest, "max_ram_bytes": self.max_ram_bytes, "ready": self._unready() or "ready"}
        conn = self._connect(create=False)
        if conn is not None:
            try:
                out["passages"] = conn.execute("SELECT count(*) FROM vectors").fetchone()[0]
            finally:
                conn.close()
        return out


def lexical_fallback(query: str, passages: Sequence[Passage], top_k: int = 10) -> Searched:
    """The BM25 rung over ``passages``, named as what it is: a lexical fallback for when the dense rung cannot run.
    Its evidence says ``rung: bm25`` and ``fallback: true``, so fusion counts it once with the lexical backend and
    nothing presents it as meaning."""
    from g6.knowledge.ctx_colbert.retrieve import rank

    found = rank(query, passages, rung="bm25", top_k=top_k).searched
    return found.model_copy(update={"backend": BACKEND, "evidence": {**found.evidence, "rung": "bm25",
                                                                      "fallback": True, "semantic": False}})


def open_for(store: Any, generation: int, *, registrations: Sequence[str] | None = None) -> PersistentColbertIndex:
    """The project's store for the dispatcher's ``meaning`` route. No encoder or pin is configured until the scale
    spike has chosen a route, so this refuses with that reason."""
    raise IndexRefused(UNAVAILABLE, "persistent ColBERT has no encoder configured: the route waits for the scale "
                                    "spike's result (protocol 05)")


__all__ = ["BACKEND", "MAX_CANDIDATES", "PIN_CHANGED", "SCALE", "TOO_MANY", "Encoder", "PersistentColbertIndex",
           "lexical_fallback", "maxsim", "open_for"]
