# SPDX-License-Identifier: Apache-2.0
"""The manifest: sources, documents and passages kept in SQLite, and the check of a passage against its source as
read again.

:class:`Store` keeps what was read (each source by its address and the hash of its bytes), the text read from it
(each document by the hash of its text) and the passages cut from it, under ``$G6_HOME/knowledge/manifest.db`` or a
path a Python caller passes. A record is content-addressed, so recording it again changes nothing, and the same text
from a second source is recorded again with its own source. Only passages that are exact spans of their document are
recorded. Reading a passage back checks it against its stored document and that document against its stored hash, so
a row edited by hand is reported, not served.

:func:`verify_passage` compares a cited passage with its source as read again (a :class:`Document`, or a function
that re-reads a :class:`Source`). A re-read is a document from the passage's address read after its source was
(``retrieved_at`` later, to the microsecond); the very snapshot the passage was cut from, or an older one, is no
fresh read and is ``unverifiable``. When and how the re-read was made is the caller's claim, recorded as such in the
verdict. ``matches``: the text re-read is the text the passage was cut from (its hash is recomputed here) and the
passage is its span. ``changed``: the text is different, with ``span_intact`` saying whether the cited words are still
at the same offsets and ``found_at`` where they are now, if anywhere. ``source_unavailable``: the re-read failed.
``unverifiable``: there was nothing to compare, no fresh read, or the passage is not a span of the text it names.
Only ``matches`` passes; every other outcome fails closed.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from g6.core.storage import open_db, state_path
from g6.knowledge._types import Document, Passage, Source, instant, passage_problems, sha256_hex

STORE = "G6_E_KNOWLEDGE_STORE"
SPAN_MISMATCH = "G6_E_KNOWLEDGE_SPAN_MISMATCH"
Outcome = Literal["matches", "changed", "source_unavailable", "unverifiable"]
Reread = Document | Callable[[Source], Document] | None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    uri TEXT NOT NULL, sha256 TEXT NOT NULL, retrieved_at TEXT NOT NULL, provenance TEXT NOT NULL,
    licence TEXT NOT NULL, published_at TEXT NOT NULL, PRIMARY KEY (uri, sha256));
CREATE TABLE IF NOT EXISTS documents (
    sha256 TEXT NOT NULL, source_uri TEXT NOT NULL, source_sha256 TEXT NOT NULL, media_type TEXT NOT NULL,
    converted_by TEXT NOT NULL, unread TEXT NOT NULL, title TEXT NOT NULL, locator TEXT NOT NULL, text TEXT NOT NULL,
    PRIMARY KEY (sha256, source_uri, source_sha256));
CREATE TABLE IF NOT EXISTS passages (
    id TEXT PRIMARY KEY, source_uri TEXT NOT NULL, source_sha256 TEXT NOT NULL, document_sha256 TEXT NOT NULL,
    span_start INTEGER NOT NULL, span_end INTEGER NOT NULL, text TEXT NOT NULL);
"""


class StoreError(Exception):
    """A store that cannot be used, or a record it will not keep, with its stable code."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code, self.reason = code, reason


class Store:
    """Sources, documents and passages in one SQLite file: ``$G6_HOME/knowledge/manifest.db`` unless a Python caller
    names another path."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else state_path("knowledge", "manifest.db")
        self._conn: sqlite3.Connection | None = None

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            try:
                conn = open_db(str(self.path.resolve()))
                conn.executescript(_SCHEMA)
            except (sqlite3.Error, OSError) as exc:
                raise StoreError(STORE, f"the manifest at {self.path} cannot be opened: {type(exc).__name__}: "
                                        f"{exc}") from None
            self._conn = conn
        return self._conn

    def record(self, document: Document, passages: list[Passage]) -> tuple[int, int]:
        """Keep ``document`` and ``passages``; ``(newly recorded, already recorded)`` passages. Raises
        :class:`StoreError` (``G6_E_KNOWLEDGE_SPAN_MISMATCH``) without keeping anything when a passage is not an exact
        span of the document."""
        wrong = [f"{p.start}:{p.end}: {problem}" for p in passages for problem in passage_problems(p, document)]
        if wrong:
            raise StoreError(SPAN_MISMATCH, "these passages are not exact spans of the document, so nothing was "
                                            "recorded: " + "; ".join(wrong[:5]))
        src = document.source
        try:
            with self._db() as conn:
                conn.execute("INSERT OR IGNORE INTO sources VALUES (?, ?, ?, ?, ?, ?)",
                             (src.uri, src.sha256, src.retrieved_at, src.provenance, src.licence, src.published_at))
                conn.execute("INSERT OR IGNORE INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (document.sha256, src.uri, src.sha256, document.media_type, document.converted_by,
                              json.dumps(list(document.unread)), document.title, document.locator, document.text))
                before = conn.total_changes
                conn.executemany("INSERT OR IGNORE INTO passages VALUES (?, ?, ?, ?, ?, ?, ?)",
                                 [(p.id, src.uri, src.sha256, p.document_sha256, p.start, p.end, p.text)
                                  for p in passages])
                added = conn.total_changes - before
        except sqlite3.Error as exc:
            raise StoreError(STORE, f"the manifest at {self.path} could not be written: {exc}") from None
        return added, len(passages) - added

    def passage(self, passage_id: str) -> tuple[Passage, Document] | None:
        """The recorded passage ``passage_id`` and its document, each checked against what was recorded with it;
        ``None`` when nothing is recorded under that id. Raises :class:`StoreError` (``G6_E_KNOWLEDGE_STORE``) for a
        record that was edited after it was kept."""
        try:
            conn = self._db()
            row = conn.execute("SELECT * FROM passages WHERE id = ?", (passage_id,)).fetchone()
            if row is None:
                return None
            src = conn.execute("SELECT * FROM sources WHERE uri = ? AND sha256 = ?",
                               (row["source_uri"], row["source_sha256"])).fetchone()
            doc = conn.execute("SELECT * FROM documents WHERE sha256 = ? AND source_uri = ? AND source_sha256 = ?",
                               (row["document_sha256"], row["source_uri"], row["source_sha256"])).fetchone()
        except sqlite3.Error as exc:
            raise StoreError(STORE, f"the manifest at {self.path} could not be read: {exc}") from None
        edited = f"the record of passage {passage_id} was edited after it was kept, so it is not served"
        if src is None or doc is None or sha256_hex(doc["text"]) != doc["sha256"]:
            raise StoreError(STORE, edited)
        try:
            source = Source(uri=src["uri"], sha256=src["sha256"], retrieved_at=src["retrieved_at"],
                            provenance=src["provenance"], licence=src["licence"], published_at=src["published_at"])
            document = Document(text=doc["text"], source=source, media_type=doc["media_type"],
                                converted_by=doc["converted_by"], unread=tuple(json.loads(doc["unread"])),
                                title=doc["title"], locator=doc["locator"])
            passage = Passage(id=row["id"], source=source, document_sha256=row["document_sha256"],
                              start=row["span_start"], end=row["span_end"], text=row["text"])
        except (ValidationError, ValueError, TypeError):
            raise StoreError(STORE, edited) from None
        if passage_problems(passage, document):
            raise StoreError(STORE, edited)
        return passage, document


@dataclass(frozen=True)
class Verdict:
    """What :func:`verify_passage` found."""

    outcome: Outcome
    reason: str
    span_intact: bool | None = None
    found_at: list[int] | None = None
    reread_sha256: str = ""
    checks: tuple[str, ...] = field(default_factory=tuple)
    reread: dict | None = None  # the re-read's source, as the caller gave it


def _claimed(document: Document) -> dict:
    src = document.source
    return {"uri": src.uri, "sha256": src.sha256, "retrieved_at": src.retrieved_at, "provenance": src.provenance,
            "as": "the caller's claim: when and how this document was read is as the caller gave it, not observed "
                  "here"}


def verify_passage(passage: Passage, reread: Reread) -> Verdict:
    """Compare ``passage`` with its source as read again; see the module docstring."""
    if reread is None:
        return Verdict("unverifiable", "nothing was re-read from the source, so the passage was not compared with it")
    document = reread
    if not isinstance(reread, Document):
        try:
            document = reread(passage.source)
        except Exception as exc:  # noqa: BLE001 - a re-read that fails is the source being unavailable, never a pass
            return Verdict("source_unavailable", f"re-reading {passage.source.uri} failed: {type(exc).__name__}: "
                                                 f"{exc}"[:500])
    if not isinstance(document, Document):
        return Verdict("unverifiable", f"the re-read gave {type(document).__name__}, not a document")
    if document.source.uri != passage.source.uri:
        return Verdict("unverifiable", f"the document re-read is from {document.source.uri}, not the cited "
                                       f"{passage.source.uri}")
    claimed = _claimed(document)
    read_at, cut_from = instant(document.source.retrieved_at), instant(passage.source.retrieved_at)
    if read_at is None or cut_from is None or read_at <= cut_from:
        return Verdict("unverifiable", f"no fresh read was observed: the document given was read at "
                                       f"{document.source.retrieved_at}, not after the source the passage was cut "
                                       f"from ({passage.source.retrieved_at}), so it is that snapshot or an older "
                                       "one and nothing was read again", reread_sha256=document.sha256,
                       reread=claimed)
    held = document.text[passage.start:passage.end]
    checks = ("the re-read text's SHA-256, recomputed here, against the hash of the text the passage was cut from",
              "the re-read text at the passage's offsets against the passage's text")
    if document.sha256 == passage.document_sha256:
        if held != passage.text:
            return Verdict("unverifiable", "the passage is not the span it names of the very text it was cut from",
                           reread_sha256=document.sha256, checks=checks, reread=claimed)
        same_bytes = document.source.sha256 == passage.source.sha256
        return Verdict("matches", f"the document given as the source read again at {document.source.retrieved_at} "
                       "(the caller's claim) holds exactly the text the passage was cut from"
                       + ("" if same_bytes else "; its bytes changed, its text did not"), span_intact=True,
                       found_at=[passage.start, passage.end], reread_sha256=document.sha256, checks=checks,
                       reread=claimed)
    at = document.text.find(passage.text)
    found = [at, at + len(passage.text)] if at >= 0 else None
    if held == passage.text:
        reason = ("the source changed since the passage was cut; the cited words are still at the same offsets, so "
                  "cite them again from the version read now")
    elif found:
        reason = f"the source changed: the cited words moved from {passage.start}:{passage.end} to {found[0]}:{found[1]}"
    else:
        reason = "the source changed: the cited words are no longer in it"
    return Verdict("changed", reason, span_intact=held == passage.text, found_at=found,
                   reread_sha256=document.sha256, checks=checks, reread=claimed)


__all__ = ["SPAN_MISMATCH", "STORE", "Outcome", "Reread", "Store", "StoreError", "Verdict", "verify_passage"]