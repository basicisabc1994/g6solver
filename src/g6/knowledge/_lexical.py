# SPDX-License-Identifier: Apache-2.0
"""The Context Engine's lexical reader: the family's SQLite FTS5 index, searched only inside a scope decided before
the search.

``_``-prefixed, so it never registers as a component. Scope comes first (design 12.6): the engine works out, from the
registry and the grant ledger alone, which source registrations an access may read, and :class:`ScopedLexical`
restricts the full-text query itself to their passages. Nothing of a registration outside the scope is matched,
ranked or counted, so no reply can vary with what such a source holds, and passages the caller may not use never
take a place in the candidate window. With no registration in scope nothing is searched at all.

``held_ids`` lists the passage ids an index file holds, so that a generation staged from a copy can take out every
row its membership does not list, whatever an interrupted earlier step left behind.
"""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Sequence
from pathlib import Path

from g6.knowledge._index import TIMED_OUT, Hit, IndexRefused, Searched
from g6.knowledge.knowledge_indexer.index import _STEPS, QUERY_SECONDS, SCALE, SqliteIndex, fts5_ready, match_expression

#: The address prefix of one registration's files (``g6.knowledge._registry.source_uri``).
_PREFIX = "g6-knowledge://{}/"


class ScopedLexical(SqliteIndex):
    """One generation's lexical index, read inside ``registrations`` (``None``: unscoped, for writing and for
    maintenance; a search always gives a scope)."""

    def __init__(self, path: Path, registrations: Sequence[str] | None = None) -> None:
        super().__init__("lexical", path=path)
        self.registrations = None if registrations is None else tuple(sorted(set(registrations)))

    def held_ids(self) -> set[str]:
        if not self.path.exists():
            return set()
        conn = self._connect()
        try:
            if not self._has_schema(conn):
                return set()
            return {row["id"] for row in conn.execute("SELECT id FROM passages")}
        except sqlite3.Error as exc:
            raise IndexRefused("G6_E_KNOWLEDGE_INDEX_FAILED", f"the index cannot be read ({type(exc).__name__})") from None
        finally:
            conn.close()

    def find(self, query: str, top_k: int = 10) -> Searched:
        if self.registrations is None:
            return super().find(query, top_k)
        said = {"backend": self.backend, "query": query, "score_scale": SCALE}
        if not self.registrations:
            return Searched(**said, outcome="empty", searched=0, reason="no source is in scope")
        try:
            expression = match_expression(query)
        except IndexRefused as exc:
            return Searched(**said, outcome="failed", reason=exc.reason, evidence={"code": exc.code})
        why = fts5_ready()
        if why:
            return Searched(**said, outcome="unavailable", reason=why)
        if not self.path.exists():
            return Searched(**said, outcome="empty", searched=0, reason="nothing is indexed")
        try:
            conn = self._connect()
        except IndexRefused as exc:
            return Searched(**said, outcome="failed", reason=exc.reason)
        deadline, ended = time.monotonic() + QUERY_SECONDS, []

        def past_limit() -> int:
            if time.monotonic() > deadline:
                ended.append(True)
                return 1
            return 0

        scope = " OR ".join("json_extract(p.source, '$.uri') LIKE ? ESCAPE '\\'" for _ in self.registrations)
        like = [_PREFIX.format(r).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                for r in self.registrations]
        try:
            if not self._has_schema(conn):
                return Searched(**said, outcome="empty", searched=0, reason="nothing is indexed")
            conn.set_progress_handler(past_limit, _STEPS)
            count = conn.execute(f"SELECT count(*) FROM passages p WHERE {scope}", like).fetchone()[0]
            rows = conn.execute(
                "SELECT p.*, -bm25(passages_fts) AS score FROM passages_fts JOIN passages p ON p.rid = "
                f"passages_fts.rowid WHERE passages_fts MATCH ? AND ({scope}) ORDER BY bm25(passages_fts), p.rid "
                "LIMIT ?", (expression, *like, top_k)).fetchall()
            conn.set_progress_handler(None, _STEPS)
            hits = [Hit(passage=self._passage(r), score=float(r["score"]), score_scale=SCALE, rank=n)
                    for n, r in enumerate(rows, start=1)]
        except IndexRefused as exc:
            return Searched(**said, outcome="failed", reason=exc.reason)
        except sqlite3.Error:
            code = {"code": TIMED_OUT} if ended else {}
            return Searched(**said, outcome="failed", reason="the search did not finish" if ended else
                            "the index could not be searched", evidence=code)
        finally:
            conn.close()
        if not hits:
            return Searched(**said, outcome="empty", searched=count, reason="no passage in scope holds a word of the query")
        return Searched(**said, outcome="ok", hits=hits, searched=count, evidence={"expression": expression})


__all__ = ["ScopedLexical"]
