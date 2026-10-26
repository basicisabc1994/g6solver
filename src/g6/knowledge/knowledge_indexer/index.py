# SPDX-License-Identifier: Apache-2.0
"""The family's default index: passages in a SQLite file with an FTS5 full-text index, under a storage budget.

:class:`SqliteIndex` keeps passages (each with its source, the hash of the text it was cut from and its offsets) in
``$G6_HOME/knowledge/index/<name>.db``, or a path a Python caller passes; over MCP only a plain name is taken, so a
caller cannot point the writes anywhere else. Adding is incremental: a passage already held (same id) is not added
again, and nothing is rebuilt.

The budget is ``max_bytes`` for the whole index on disk, and it belongs to the index: the first write keeps it in
the index's own file (the caller's ``max_bytes``, or :data:`DEFAULT_MAX_BYTES`), and a later call cannot raise it. A
later call's ``max_bytes`` smaller than the kept budget caps that call only; the index keeps its own. Before anything
is written, the write is projected (the bytes on disk now, plus an allowance per new passage: :data:`PER_CHARACTER` bytes for each byte of its
text, its source's record, and :data:`PER_ROW` bytes) and a projection over the budget is refused. The projection
is an estimate, so the size is measured again inside the write's transaction, before it commits (SQLite's page count
times its page size, which counts the pages the write added), and a write measured over the budget is rolled back.
Either refusal is ``G6_E_KNOWLEDGE_OVER_BUDGET`` with both numbers, and the index is left as it was. A file whose
size cannot be read fails closed.

Search is FTS5's BM25 over the passages' text (``unicode61`` words, diacritics folded): each word of the query is
looked up on its own (FTS5's own query syntax is never taken from the caller) and a passage matching more, rarer
words ranks higher. The score is ``fts5_bm25``: BM25 negated, so higher is better, comparable only within one search.
A search runs inside SQLite for at most :data:`QUERY_SECONDS`: a progress handler looks at the clock every
:data:`_STEPS` steps and ends a query past it, which fails as ``G6_E_KNOWLEDGE_INDEX_TIMEOUT``, never as no match.
Every passage read back is checked against what was indexed: its id (bound to its source, the hash of the text it
was cut from and its offsets) and the hash of its own text kept beside it. So an edit to a row's text alone, or to its
source, offsets or document hash, fails the search rather than being served. A stated limit: an edit that rewrites
the text and the hash beside it together, keeping the text's length, is not caught here, since the id binds the
source and the offsets, not the words; re-checking the passage against its source (``knowledge_manifest``) catches it.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from g6.core.storage import state_path
from g6.knowledge._index import (BAD_QUERY, FAILED, OVER_BUDGET, TIMED_OUT, UNAVAILABLE, Added, Hit, IndexBackend,
                                 IndexRefused, Removed, Retrieves, Searched, index_backends)
from g6.knowledge._types import Passage, Source, sha256_hex

#: The budget when a caller sets none: 256 MiB for one index on disk.
DEFAULT_MAX_BYTES = 256 * 1024 * 1024
#: The projection's allowance for each byte of a new passage's text: the row holds it once, the full-text index its
#: words and their positions again. Measured on generated English-like text (2026-10-21), the index took 1.1 times
#: the text for 20,000-character passages and 1.9 times for 1,000-character ones, the source records included, and
#: the projection was over the measured size in each case; the measurement before commit decides either way.
PER_CHARACTER = 3
#: The projection's allowance for each new row besides its text: the record of its source, the id and the offsets.
PER_ROW = 512
#: What an index holding nothing yet is projected to take (its tables and the full-text index's own records).
EMPTY_INDEX = 64 * 1024
SCALE = "fts5_bm25"
NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_WORD = re.compile(r"\w+")
#: The most query words looked up.
MAX_QUERY_WORDS = 64
#: The longest one search may run inside SQLite, in seconds; a query past it is ended.
QUERY_SECONDS = 10.0
#: How many SQLite steps run between two looks at the clock.
_STEPS = 1000
_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS passages (rid INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, source TEXT NOT NULL, "
    "document_sha256 TEXT NOT NULL, span_start INTEGER NOT NULL, span_end INTEGER NOT NULL, text TEXT NOT NULL, "
    "text_sha256 TEXT NOT NULL)",
    "CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(text, content='passages', content_rowid='rid', "
    "tokenize='unicode61 remove_diacritics 2')",
    "CREATE TABLE IF NOT EXISTS budget (id INTEGER PRIMARY KEY CHECK (id = 1), max_bytes INTEGER NOT NULL)",
)


def fts5_ready() -> str:
    """``""`` when this Python's SQLite has FTS5, otherwise why not."""
    try:
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return f"this Python's SQLite has no FTS5 full-text search ({exc}); use a Python whose SQLite has it"
    return ""


def projected_bytes(passages: Sequence[Passage]) -> int:
    """The allowance :class:`SqliteIndex` projects for writing ``passages``."""
    return sum(PER_CHARACTER * len(p.text.encode("utf-8")) + len(p.source.model_dump_json()) + PER_ROW
               for p in passages)


def match_expression(query: str) -> str:
    """The FTS5 query for ``query``: each of its words, quoted, any of them; :class:`IndexRefused` when it has none."""
    words = list(dict.fromkeys(w.lower() for w in _WORD.findall(query)))[:MAX_QUERY_WORDS]
    if not words:
        raise IndexRefused(BAD_QUERY, "the query has no word to search for")
    return " OR ".join('"' + w.replace('"', '""') + '"' for w in words)


class SqliteIndex(Retrieves):
    """Passages in one SQLite file with an FTS5 index, under the budget the index keeps; ``max_bytes`` is the budget
    a first write keeps, or a smaller cap on a later one (``None``: the kept budget, or :data:`DEFAULT_MAX_BYTES`
    for a first write). See the module docstring."""

    backend = "sqlite"
    score_scale = SCALE

    def __init__(self, name: str = "default", *, path: Path | str | None = None,
                 max_bytes: int | None = None) -> None:
        if not NAME.fullmatch(name):
            raise IndexRefused(UNAVAILABLE, f"{name!r} is not an index name (lower-case letters, digits, '-' and "
                                            "'_', at most 64): an index is named, never placed by a path")
        if max_bytes is not None and max_bytes <= 0:
            raise IndexRefused(OVER_BUDGET, "max_bytes must be a positive number of bytes")
        self.name = name
        self.path = Path(path) if path is not None else state_path("knowledge", "index", f"{name}.db")
        self.max_bytes = max_bytes

    # -- the file -----------------------------------------------------------------------------------------------------

    def on_disk(self) -> int:
        """The bytes the index takes on disk now (its file and any journal beside it); 0 when it does not exist.
        A size that cannot be read is :class:`IndexRefused`: the budget is never checked against an undercount."""
        total = 0
        for path in (self.path, Path(f"{self.path}-journal"), Path(f"{self.path}-wal")):
            try:
                total += path.stat().st_size
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise IndexRefused(FAILED, f"the size of {path} cannot be read ({type(exc).__name__}), so the budget "
                                           "cannot be checked") from None
        return total

    def _connect(self) -> sqlite3.Connection:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), isolation_level=None, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=DELETE")  # the file's size is the index's size once a write commits
        except (sqlite3.Error, OSError) as exc:
            raise IndexRefused(FAILED, f"the index at {self.path} cannot be opened: {type(exc).__name__}: {exc}") \
                from None
        return conn

    def _held(self, conn: sqlite3.Connection, ids: Sequence[str]) -> set[str]:
        held: set[str] = set()
        for at in range(0, len(ids), 500):
            chunk = list(ids[at:at + 500])
            marks = ",".join("?" * len(chunk))
            held |= {r["id"] for r in conn.execute(f"SELECT id FROM passages WHERE id IN ({marks})", chunk)}
        return held

    @staticmethod
    def _kept_budget(conn: sqlite3.Connection) -> int | None:
        """The budget the index keeps; ``None`` before its first write."""
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'budget'").fetchone() is None:
            return None
        row = conn.execute("SELECT max_bytes FROM budget WHERE id = 1").fetchone()
        return int(row[0]) if row is not None else None

    def _budget(self, kept: int | None) -> tuple[int, str]:
        """The limit this write is held to, and why: the kept budget, a smaller cap of this call's, or (for a first
        write) the budget it will keep."""
        if kept is None:
            return self.max_bytes or DEFAULT_MAX_BYTES, "set by this first write and kept with the index"
        if self.max_bytes is not None and self.max_bytes < kept:
            return self.max_bytes, f"this call's cap, smaller than the {kept} bytes the index keeps"
        return kept, "kept with the index since its first write; a call cannot raise it"

    @staticmethod
    def _measured(conn: sqlite3.Connection) -> int:
        pages = conn.execute("PRAGMA page_count").fetchone()[0]
        return pages * conn.execute("PRAGMA page_size").fetchone()[0]

    # -- the Index interface ------------------------------------------------------------------------------------------

    def add(self, passages: Sequence[Passage]) -> Added:
        """Add the passages it does not hold yet, within the budget; see the module docstring."""
        unique = list({p.id: p for p in reversed(passages)}.values())[::-1]  # the first of each id, in order
        existed = self.path.exists()
        before = self.on_disk()
        held: set[str] = set()
        kept: int | None = None
        if existed and unique:
            conn = self._connect()
            try:
                if self._has_schema(conn):
                    held, kept = self._held(conn, [p.id for p in unique]), self._kept_budget(conn)
            except sqlite3.Error as exc:
                raise IndexRefused(FAILED, f"the index at {self.path} cannot be read: {exc}") from None
            finally:
                conn.close()
        new = [p for p in unique if p.id not in held]
        limit, basis = self._budget(kept)
        budget = {"max_bytes": limit, "budget": basis, "asked_max_bytes": self.max_bytes, "bytes_before": before}
        if not new:
            return Added(added=0, known=len(unique), evidence={**budget, "bytes_after": before})
        projected = (before or EMPTY_INDEX) + projected_bytes(new)
        if projected > limit:
            raise IndexRefused(OVER_BUDGET, f"adding {len(new)} passage{'s' if len(new) != 1 else ''} is projected to "
                                            f"bring the index to {projected} bytes, over its budget of {limit} "
                                            f"({basis}); nothing was written",
                               {**budget, "checked": "projected before writing", "projected_bytes": projected})
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in _SCHEMA:
                    conn.execute(statement)
                kept = self._kept_budget(conn)  # another writer's first write may have set it meanwhile
                if kept is None:
                    conn.execute("INSERT INTO budget (id, max_bytes) VALUES (1, ?)",
                                 (self.max_bytes or DEFAULT_MAX_BYTES,))
                limit, basis = self._budget(kept)
                budget.update(max_bytes=limit, budget=basis)
                meanwhile = self._held(conn, [p.id for p in new])  # once: what another writer added since the look
                new = [p for p in new if p.id not in meanwhile]
                for p in new:
                    cursor = conn.execute(
                        "INSERT INTO passages (id, source, document_sha256, span_start, span_end, text, text_sha256) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (p.id, p.source.model_dump_json(), p.document_sha256, p.start, p.end, p.text,
                         sha256_hex(p.text)))
                    conn.execute("INSERT INTO passages_fts (rowid, text) VALUES (?, ?)", (cursor.lastrowid, p.text))
                measured = self._measured(conn)
                if measured > limit:
                    conn.execute("ROLLBACK")
                    raise IndexRefused(OVER_BUDGET, f"the write was measured at {measured} bytes before it committed, "
                                                    f"over the index's budget of {limit} (projected "
                                                    f"{projected}); it was rolled back and nothing was kept",
                                       {**budget, "checked": "measured before committing",
                                        "projected_bytes": projected, "measured_bytes": measured})
                conn.execute("COMMIT")
            except sqlite3.Error as exc:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise IndexRefused(FAILED, f"the index at {self.path} could not be written: {exc}") from None
        finally:
            conn.close()
            if not existed and self.path.exists() and not self._holds_anything():
                self.path.unlink(missing_ok=True)  # a first write that was refused leaves no file behind
        after = self.on_disk()
        return Added(added=len(new), known=len(unique) - len(new),
                     evidence={**budget, "checked": "projected before writing and measured before committing",
                               "projected_bytes": projected, "measured_bytes": measured, "bytes_after": after})

    def _has_schema(self, conn: sqlite3.Connection) -> bool:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'passages_fts'").fetchone() is not None

    def _holds_anything(self) -> bool:
        try:
            conn = sqlite3.connect(str(self.path))
            try:
                return conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'passages'").fetchone() is not None
            finally:
                conn.close()
        except sqlite3.Error:
            return True  # a file that cannot be read is never removed

    def _passage(self, row: sqlite3.Row) -> Passage:
        # catches a row whose text no longer has the hash kept beside it, or whose id no longer binds its source and
        # offsets; not a rewrite of the text and that hash together (see the module docstring)
        edited = (f"the indexed passage {row['id']} no longer matches what was indexed (its row was edited "
                  "afterwards), so the search is not served")
        if sha256_hex(row["text"]) != row["text_sha256"]:
            raise IndexRefused(FAILED, edited)
        try:
            source = Source.model_validate(json.loads(row["source"]))
            return Passage(id=row["id"], source=source, document_sha256=row["document_sha256"],
                           start=row["span_start"], end=row["span_end"], text=row["text"])
        except (ValidationError, ValueError, TypeError):
            raise IndexRefused(FAILED, edited) from None

    def find(self, query: str, top_k: int = 10) -> Searched:
        """FTS5's BM25 search; see the module docstring."""
        said = {"backend": self.backend, "query": query, "score_scale": SCALE}
        try:
            expression = match_expression(query)
        except IndexRefused as exc:
            return Searched(**said, outcome="failed", reason=exc.reason, evidence={"code": exc.code})
        why = fts5_ready()
        if why:
            return Searched(**said, outcome="unavailable", reason=why)
        if not self.path.exists():
            return Searched(**said, outcome="empty", searched=0, reason=f"nothing is indexed under {self.name!r}")
        try:
            conn = self._connect()
        except IndexRefused as exc:
            return Searched(**said, outcome="failed", reason=exc.reason)
        limit = QUERY_SECONDS
        deadline, ended = time.monotonic() + limit, []

        def past_limit() -> int:
            if time.monotonic() > deadline:
                ended.append(True)
                return 1  # SQLite ends the query
            return 0

        try:
            if not self._has_schema(conn):
                return Searched(**said, outcome="empty", searched=0, reason=f"nothing is indexed under {self.name!r}")
            conn.set_progress_handler(past_limit, _STEPS)
            count = conn.execute("SELECT count(*) FROM passages").fetchone()[0]
            rows = conn.execute(
                "SELECT p.*, -bm25(passages_fts) AS score FROM passages_fts JOIN passages p ON p.rid = "
                "passages_fts.rowid WHERE passages_fts MATCH ? ORDER BY bm25(passages_fts), p.rid LIMIT ?",
                (expression, top_k)).fetchall()
            conn.set_progress_handler(None, _STEPS)
            hits = [Hit(passage=self._passage(r), score=float(r["score"]), score_scale=SCALE, rank=n)
                    for n, r in enumerate(rows, start=1)]
        except IndexRefused as exc:
            return Searched(**said, outcome="failed", reason=exc.reason)
        except sqlite3.Error as exc:
            if ended:
                return Searched(**said, outcome="failed", reason=f"the search ran past its limit of {limit:g} seconds "
                                "and was ended: this is not no match; ask with fewer or rarer words",
                                evidence={"code": TIMED_OUT, "limit_seconds": limit})
            return Searched(**said, outcome="failed", reason=f"the index at {self.path} could not be searched: {exc}")
        finally:
            conn.close()
        if not hits:
            return Searched(**said, outcome="empty", searched=count,
                            reason=f"no passage of the {count} indexed holds any word of the query")
        return Searched(**said, outcome="ok", hits=hits, searched=count,
                        evidence={"expression": expression})

    def remove(self, ids: Sequence[str]) -> Removed:
        """Take the passages ``ids`` out of the index; the ones it did not hold are listed as missing."""
        wanted = list(dict.fromkeys(ids))
        if not wanted or not self.path.exists():
            return Removed(missing=wanted)
        conn = self._connect()
        try:
            if not self._has_schema(conn):
                return Removed(missing=wanted)
            conn.execute("BEGIN IMMEDIATE")
            removed = []
            for ident in wanted:
                row = conn.execute("SELECT rid, text FROM passages WHERE id = ?", (ident,)).fetchone()
                if row is None:
                    continue
                conn.execute("INSERT INTO passages_fts (passages_fts, rowid, text) VALUES ('delete', ?, ?)",
                             (row["rid"], row["text"]))
                conn.execute("DELETE FROM passages WHERE rid = ?", (row["rid"],))
                removed.append(ident)
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise IndexRefused(FAILED, f"the index at {self.path} could not be changed: {exc}") from None
        finally:
            conn.close()
        return Removed(removed=removed, missing=[i for i in wanted if i not in removed])

    def status(self) -> dict[str, Any]:
        """Where the index is, what it holds, its size on disk and the budget it keeps (before its first write, the
        budget a first write would keep)."""
        out: dict[str, Any] = {"backend": self.backend, "index": self.name, "path": str(self.path),
                               "exists": self.path.exists(), "passages": 0, "sources": 0, "bytes": self.on_disk(),
                               "max_bytes": self.max_bytes or DEFAULT_MAX_BYTES,
                               "budget": "not kept yet: the first write keeps it", "score_scale": SCALE,
                               "fts5": fts5_ready() or "ready"}
        if out["exists"]:
            conn = self._connect()
            try:
                if self._has_schema(conn):
                    out["passages"] = conn.execute("SELECT count(*) FROM passages").fetchone()[0]
                    out["sources"] = conn.execute(
                        "SELECT count(DISTINCT json_extract(source, '$.uri')) FROM passages").fetchone()[0]
                    kept = self._kept_budget(conn)
                    if kept is not None:
                        out["max_bytes"], out["budget"] = kept, "kept with the index since its first write"
            except sqlite3.Error as exc:
                raise IndexRefused(FAILED, f"the index at {self.path} cannot be read: {exc}") from None
            finally:
                conn.close()
        return out


def _open(index: str = "default", max_bytes: int | None = None, **_: Any) -> SqliteIndex:
    return SqliteIndex(index, max_bytes=max_bytes)


BACKEND = IndexBackend(name="sqlite", summary="passages in a SQLite file under G6's state folder with an FTS5 "
                       "full-text index (BM25), under a storage budget", score_scale=SCALE, open=_open,
                       ready=fts5_ready)
index_backends.register(BACKEND, replace=True)

__all__ = ["BACKEND", "DEFAULT_MAX_BYTES", "EMPTY_INDEX", "NAME", "PER_CHARACTER", "PER_ROW", "SCALE", "SqliteIndex",
           "fts5_ready", "match_expression", "projected_bytes"]
