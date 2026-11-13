# SPDX-License-Identifier: Apache-2.0
"""The Context Engine's lexical reader: the family's SQLite FTS5 index, searched only inside a scope decided before
the search.

``_``-prefixed, so it never registers as a component. Scope comes first (design 12.6): the engine works out, from the
registry and the grant ledger alone, which source registrations an access may read, and :class:`ScopedLexical`
restricts the full-text query itself to their passages. Nothing of a registration outside the scope is matched,
ranked or counted, so no reply can vary with what such a source holds, and passages the caller may not use never
take a place in the candidate window. With no registration in scope nothing is searched at all.

The order is G6's own, from the rows in scope only. FTS5's ``bm25()`` weighs a word by how many rows of the whole
table hold it, so its order of one source's passages would change with the words of another source, a revoked one
or an expired one. A scoped search therefore uses FTS5 to find the matching rows in scope and scores each itself:
BM25's term weight from the number of passages in scope that hold the word, and a saturating count of the word in
the passage (``K1``), with no length normalisation (the average length in scope would need a scan). Ties keep the
order the passages were indexed in. Cost: every matching row in scope is read and scored, so a query for a word
most passages hold takes time in proportion to the scope, inside the search's time limit; when the limit is
reached the search is ``failed``, never a partial order. Files observed changed (``excluded``) are left out before
the best ``top_k`` are chosen, so denied passages never use up the window.

``held_ids`` lists the passage ids an index file holds, so that a generation staged from a copy can take out every
row its membership does not list, whatever an interrupted earlier step left behind.
"""
from __future__ import annotations

import heapq
import json
import math
import re
import sqlite3
import time
import unicodedata
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from g6.knowledge._index import TIMED_OUT, Hit, IndexRefused, Searched, bad_k
from g6.knowledge.knowledge_indexer.index import (_STEPS, _WORD, MAX_QUERY_WORDS, QUERY_SECONDS, SqliteIndex,
                                                  fts5_ready, match_expression)

#: The scale of a scoped search's scores (``g6.knowledge._index.SCALES``).
SCOPED_SCALE = "bm25_in_scope"
K1 = 1.2
#: How many matching passages of files observed changed one search names as denied.
LEFT_OUT = 200
_TOKEN = re.compile(r"[^\W_]+")


def _tokens(text: str) -> tuple[str, ...]:
    """The words of ``text`` as the index's tokenizer sees them, closely enough to count them: letters and digits,
    case folded, diacritics removed."""
    plain = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    return tuple(_TOKEN.findall(plain.lower()))


def _quoted(phrase: str) -> str:
    return '"' + phrase.replace('"', '""') + '"'

#: The address prefix of one registration's files (``g6._context_engine._registry.source_uri``).
_PREFIX = "g6-knowledge://{}/"


class ScopedLexical(SqliteIndex):
    """One generation's lexical index, read inside ``registrations`` (``None``: unscoped, for writing and for
    maintenance; a search always gives a scope)."""

    def __init__(self, path: Path, registrations: Sequence[str] | None = None, *,
                 excluded: Sequence[str] = ()) -> None:
        super().__init__("lexical", path=path)
        self.registrations = None if registrations is None else tuple(sorted(set(registrations)))
        self.excluded = frozenset(excluded)   # the addresses of files observed changed: denied, so never a hit

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
        refused = bad_k(self.backend, query, SCOPED_SCALE, top_k)
        if refused is not None:
            return refused
        if self.registrations is None:
            return super().find(query, top_k)
        said = {"backend": self.backend, "query": query, "score_scale": SCOPED_SCALE}
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
        matching = ("FROM passages_fts JOIN passages p ON p.rid = passages_fts.rowid "
                    f"WHERE passages_fts MATCH ? AND ({scope})")
        phrases = [_tokens(w) for w in dict.fromkeys(w.lower() for w in _WORD.findall(query))][:MAX_QUERY_WORDS]
        try:
            if not self._has_schema(conn):
                return Searched(**said, outcome="empty", searched=0, reason="nothing is indexed")
            conn.set_progress_handler(past_limit, _STEPS)
            count = conn.execute(f"SELECT count(*) FROM passages p WHERE {scope}", like).fetchone()[0]
            # the statistics of the rows in scope, and of no other row: how many of them hold each word
            weight = {}
            for phrase in (p for p in phrases if p):
                held = conn.execute(f"SELECT count(*) {matching}", (_quoted(" ".join(phrase)), *like)).fetchone()[0]
                weight[phrase] = math.log(1.0 + (count - held + 0.5) / (held + 0.5))
            best: list[tuple[float, int, sqlite3.Row]] = []
            left_out: list[str] = []
            for row in conn.execute(f"SELECT p.* {matching} ORDER BY p.rid", (expression, *like)):
                if time.monotonic() > deadline:
                    ended.append(True)
                    raise sqlite3.OperationalError("interrupted")
                if self.excluded and json.loads(row["source"]).get("uri") in self.excluded:
                    if len(left_out) < LEFT_OUT:
                        left_out.append(row["id"])
                    continue   # a file observed changed is denied: it takes no place among the hits
                words = _tokens(row["text"])
                counts = Counter(words)
                score = 0.0
                for phrase, idf in weight.items():
                    tf = counts[phrase[0]] if len(phrase) == 1 else sum(
                        1 for n in range(len(words) - len(phrase) + 1) if tuple(words[n:n + len(phrase)]) == phrase)
                    score += idf * tf * (K1 + 1.0) / (tf + K1) if tf else 0.0
                entry = (score, -row["rid"], row)
                if len(best) < top_k:
                    heapq.heappush(best, entry)
                elif best and entry[:2] > best[0][:2]:
                    heapq.heapreplace(best, entry)
            conn.set_progress_handler(None, _STEPS)
            ordered = sorted(best, key=lambda e: (-e[0], -e[1]))
            hits = [Hit(passage=self._passage(row), score=score, score_scale=SCOPED_SCALE, rank=n)
                    for n, (score, _, row) in enumerate(ordered, start=1)]
        except IndexRefused as exc:
            return Searched(**said, outcome="failed", reason=exc.reason)
        except sqlite3.Error:
            code = {"code": TIMED_OUT} if ended else {}
            return Searched(**said, outcome="failed", reason="the search did not finish" if ended else
                            "the index could not be searched", evidence=code)
        finally:
            conn.close()
        said_of = {"expression": expression, "left_out_changed": left_out}   # matched, and denied as changed
        if not hits:
            return Searched(**said, outcome="empty", searched=count, evidence=said_of,
                            reason="no usable passage in scope holds a word of the query")
        return Searched(**said, outcome="ok", hits=hits, searched=count, evidence=said_of)


__all__ = ["K1", "SCOPED_SCALE", "ScopedLexical"]