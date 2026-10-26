# SPDX-License-Identifier: Apache-2.0
"""The store: memory records in one SQLite file, found again by relevance, stale ones kept apart, lessons held back
until an approval is recorded.

:class:`Store` keeps :class:`~g6.memory._types.MemoryRecord` rows under ``$G6_HOME/memory/ctx_ace.db`` (or a path a
Python caller passes), each with an unkeyed SHA-256 of what was written: a row edited behind the store's back without
that hash recomputed is reported and never served. An edit that recomputes it (and the record id, for the id's
fields) is not caught, since anyone who can write the file can compute both; the approval, ``stale_after``,
``created_at`` and tags are outside the record id, so for them the row hash is the only check. Keeping the same record
twice keeps one. A record cites a passage of at most
:data:`~g6.memory._types.MAX_PASSAGE_TEXT` characters and takes at most :data:`~g6.memory._types.MAX_RECORD_BYTES`
as kept; the store holds at most :data:`MAX_RECORDS` records and :data:`MAX_STORE_BYTES` of them in all. Each limit is
checked before anything is written.

* :meth:`Store.search` ranks every record by BM25 relevance to the query alone (``g6.grounding.TextRetriever``): not
  by how recent a record is, and no weight from its writer is taken (the block refuses one), so a relevant old note is
  not buried under new ones. BM25's term saturation bounds what repeating a word does (a note of one query word
  repeated fifty times is tested not to outrank the relevant one), but a note worded to match a likely query can still
  rank high. A record past its ``stale_after`` is returned apart, as stale,
  never among the current ones; a lesson nobody approved is held back and counted.
* :meth:`Store.recall` lists records by id, tag or kind, newest first, split the same way.
* :meth:`Store.forget` removes the records a filter names, for a reason, and reads the store again to check each one
  is gone: its receipt lists what was removed, and a filter that names nothing removes nothing. With no filter at
  all, or no reason, it refuses and removes nothing: it never removes everything.
* :meth:`Store.approve` records an approval of a lesson, the reviewer's name kept as the approving caller's claim (G6
  cannot tell a person from a model that types a name); the store never applies a lesson to anything: an approved one
  is still a note, read back as data and marked ``approval_is: "claim"``.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from g6.core.storage import open_db, state_path
from g6.grounding.retrieval import TextRetriever
from g6.knowledge._types import Passage, now
from g6.memory._types import MAX_PASSAGE_TEXT, MAX_RECORD_BYTES, MAX_TEXT, Approval, MemoryRecord, is_stale, record_id

STORE = "G6_E_MEMORY_STORE"
INPUT = "G6_E_MEMORY_INPUT"
FULL = "G6_E_MEMORY_FULL"
NOT_FOUND = "G6_E_MEMORY_NOT_FOUND"
NOT_A_PROPOSAL = "G6_E_MEMORY_NOT_A_PROPOSAL"
REVIEWER = "G6_E_MEMORY_REVIEWER"
FORGET_INCOMPLETE = "G6_E_MEMORY_FORGET_INCOMPLETE"
NO_FILTER = "G6_E_MEMORY_NO_FILTER"
NO_REASON = "G6_E_MEMORY_NO_REASON"
#: The most records one store keeps, and the most bytes they take in all (their JSON); every search reads them all.
MAX_RECORDS = 10_000
MAX_STORE_BYTES = 64 * 1024 * 1024
_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, body TEXT NOT NULL, sha256 TEXT NOT NULL);
"""
_NOT_A_PERSON = ("agent:", "model:", "tool:")


class StoreError(Exception):
    """A store that cannot be used, or a request it will not carry out, with its stable code."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code, self.reason = code, reason


@dataclass(frozen=True)
class Found:
    """What a search or a recall found: ``current`` records (each with its BM25 score for a search), ``stale`` ones
    apart, how many proposed lessons were held back, how many more matched than were listed, and the rows that could
    not be read (by id, with why)."""

    current: tuple[tuple[MemoryRecord, float | None], ...] = ()
    stale: tuple[tuple[MemoryRecord, float | None], ...] = ()
    held_back: int = 0
    more: int = 0
    unreadable: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Receipt:
    """What :meth:`Store.forget` removed and how that was checked."""

    removed: tuple[MemoryRecord | str, ...]
    reason: str
    forgotten_at: str
    checked: str


def _digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _body(record: MemoryRecord) -> str:
    return json.dumps(record.model_dump(mode="json", exclude={"trust"}), sort_keys=True, separators=(",", ":"))


def _plain(name: str) -> str:
    """A name as it is compared: without white space, in one case (``Agent : Run-9`` is ``agent:run-9``)."""
    return re.sub(r"\s+", "", name).casefold()


class Store:
    """Memory records in one SQLite file: ``$G6_HOME/memory/ctx_ace.db`` unless a Python caller names another path.
    ``clock`` gives the moment staleness is judged at (now, in UTC, when not given)."""

    def __init__(self, path: Path | str | None = None, *, clock: Callable[[], datetime] | None = None) -> None:
        self.path = Path(path) if path is not None else state_path("memory", "ctx_ace.db")
        self.clock = clock or (lambda: datetime.now(UTC))
        self._conn: sqlite3.Connection | None = None

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                conn = open_db(str(self.path.resolve()))
                conn.executescript(_SCHEMA)
            except (sqlite3.Error, OSError) as exc:
                raise StoreError(STORE, f"the memory store at {self.path} cannot be opened: {type(exc).__name__}: "
                                        f"{exc}") from None
            self._conn = conn
        return self._conn

    def _rows(self) -> list[sqlite3.Row]:
        try:
            return list(self._db().execute("SELECT seq, id, body, sha256 FROM records ORDER BY seq"))
        except sqlite3.Error as exc:
            raise StoreError(STORE, f"the memory store at {self.path} could not be read: {exc}") from None

    def load(self) -> tuple[list[MemoryRecord], list[str]]:
        """Every record that reads back as it was kept, oldest first, and the ids of the rows that do not (each with
        why): a row whose text no longer has its hash, or whose fields no longer make its id, is never served."""
        records, unreadable = [], []
        for row in self._rows():
            if _digest(row["body"]) != row["sha256"]:
                unreadable.append(f"{row['id']}: edited after it was kept")
                continue
            try:
                record = MemoryRecord.model_validate(json.loads(row["body"]))
            except (ValueError, ValidationError):
                unreadable.append(f"{row['id']}: no longer a record")
                continue
            if record.id != row["id"]:
                unreadable.append(f"{row['id']}: kept under another record's id")
                continue
            records.append(record)
        return records, unreadable

    def put(self, *, text: str, written_by: str, source: str = "", passage: Passage | None = None,
            kind: str = "note", tags: tuple[str, ...] = (), stale_after: str = "") -> tuple[MemoryRecord, bool]:
        """Keep one record; ``(the record, whether it is new)``. The same kind, text, source, writer and passage kept
        before gives back the record kept then, unchanged. :class:`StoreError` ``G6_E_MEMORY_INPUT`` for fields that do
        not make a record (a cited passage over :data:`MAX_PASSAGE_TEXT` characters, a record over
        :data:`MAX_RECORD_BYTES` as kept), ``G6_E_MEMORY_FULL`` when the store holds :data:`MAX_RECORDS` or this record
        would take it over :data:`MAX_STORE_BYTES`; nothing is written then."""
        if passage is not None and not source:
            source = passage.source.uri
        try:
            record = MemoryRecord(id=record_id(kind, text, source, written_by, passage.id if passage else ""),
                                  kind=kind, text=text, source=source, passage=passage, written_by=written_by,
                                  created_at=now(), stale_after=stale_after, tags=tuple(tags))
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(x) for x in first.get("loc") or ()) or "record"
            raise StoreError(INPUT, f"{where}: {first.get('msg', '')}"[:400]) from None
        body = _body(record)
        if len(body) > MAX_RECORD_BYTES:
            raise StoreError(INPUT, f"the record takes {len(body)} bytes as kept; a record takes at most "
                                    f"{MAX_RECORD_BYTES}")
        conn = self._db()
        try:
            with conn:
                row = conn.execute("SELECT body, sha256 FROM records WHERE id = ?", (record.id,)).fetchone()
                if row is not None:
                    if _digest(row["body"]) != row["sha256"]:
                        raise StoreError(STORE, f"record {record.id} was edited after it was kept, so it is not "
                                                "served; forget it by its id and keep it again")
                    return MemoryRecord.model_validate(json.loads(row["body"])), False
                count, total = conn.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(body)), 0) FROM records").fetchone()
                if count >= MAX_RECORDS:
                    raise StoreError(FULL, f"the store holds {count} records, its most; forget some first")
                if total + len(body) > MAX_STORE_BYTES:
                    raise StoreError(FULL, f"the store's records take {total} bytes, and this one {len(body)} more "
                                           f"would pass its most, {MAX_STORE_BYTES}; forget some first")
                conn.execute("INSERT INTO records (id, body, sha256) VALUES (?, ?, ?)", (record.id, body, _digest(body)))
        except sqlite3.Error as exc:
            raise StoreError(STORE, f"the memory store at {self.path} could not be written: {exc}") from None
        return record, True

    def search(self, query: str, top_k: int = 5) -> Found:
        """The records most relevant to ``query`` by BM25 alone, best first; see the module docstring."""
        records, unreadable = self.load()
        at = self.clock()
        by_id = {r.id: r for r in records}
        ranked = TextRetriever([(r.id, r.text) for r in records]).search(query, top_k=max(1, len(records)))
        current, stale, held = [], [], 0
        for hit in ranked:
            record = by_id[hit.source]
            if record.status == "proposed":
                held += 1
            elif is_stale(record, at):
                stale.append((record, hit.score))
            else:
                current.append((record, hit.score))
        more = max(0, len(current) - top_k) + max(0, len(stale) - top_k)
        return Found(tuple(current[:top_k]), tuple(stale[:top_k]), held, more, tuple(unreadable))

    def recall(self, *, record_ids: tuple[str, ...] = (), tags: tuple[str, ...] = (), kind: str = "",
               proposed: bool = False, limit: int = 50) -> Found:
        """Records with one of ``record_ids`` (when given), one of ``tags`` (when given) and of ``kind`` (when given),
        newest first. Proposed lessons are held back unless ``proposed``, which lists only them, for a person to
        review."""
        records, unreadable = self.load()
        at = self.clock()
        wanted, tagged = set(record_ids), set(tags)
        current, stale, held = [], [], 0
        for record in reversed(records):
            if (wanted and record.id not in wanted) or (tagged and not tagged & set(record.tags)) or (
                    kind and record.kind != kind):
                continue
            if (record.status == "proposed") != proposed:
                held += record.status == "proposed"
                continue
            (stale if is_stale(record, at) else current).append((record, None))
        more = max(0, len(current) - limit) + max(0, len(stale) - limit)
        return Found(tuple(current[:limit]), tuple(stale[:limit]), held, more,
                     tuple(u for u in unreadable if not wanted or u.split(":", 1)[0] in wanted))

    def forget(self, *, reason: str, record_ids: tuple[str, ...] = (), tags: tuple[str, ...] = (),
               written_by: str = "", stale_only: bool = False) -> Receipt:
        """Remove every record the filter names (all of the given conditions), then read the store again to check each
        is gone. A record that cannot be read is removed only by its id. :class:`StoreError` ``G6_E_MEMORY_NO_FILTER``
        when no condition is given (it never removes everything), ``G6_E_MEMORY_NO_REASON`` for a blank reason, and
        ``G6_E_MEMORY_FORGET_INCOMPLETE`` when one is still there."""
        if not (record_ids or tags or written_by or stale_only):
            raise StoreError(NO_FILTER, "no record ids, tags, writer or stale_only were given; forget never removes "
                                        "everything")
        if not reason.strip():
            raise StoreError(NO_REASON, "no reason was given for forgetting")
        records, _ = self.load()
        at = self.clock()
        wanted, tagged = set(record_ids), set(tags)
        doomed: list[MemoryRecord | str] = []
        for record in records:
            if (wanted and record.id not in wanted) or (tagged and not tagged & set(record.tags)) or (
                    written_by and record.written_by != written_by) or (stale_only and not is_stale(record, at)):
                continue
            doomed.append(record)
        if wanted and not (tagged or written_by or stale_only):
            readable = {r.id for r in records}
            doomed += sorted(i for i in wanted if i not in readable and self._has(i))
        ids = [d.id if isinstance(d, MemoryRecord) else d for d in doomed]
        conn = self._db()
        try:
            with conn:
                conn.executemany("DELETE FROM records WHERE id = ?", [(i,) for i in ids])
            left = [i for i in ids if self._has(i)]
        except sqlite3.Error as exc:
            raise StoreError(STORE, f"the memory store at {self.path} could not be changed: {exc}") from None
        if left:
            raise StoreError(FORGET_INCOMPLETE, f"{len(left)} of {len(ids)} records are still in the store after "
                                                f"they were removed: {', '.join(left[:5])}")
        return Receipt(tuple(doomed), reason, now(), "each removed id was looked up in the store again and is no "
                                                     "longer there")

    def _has(self, record_id_: str) -> bool:
        return self._db().execute("SELECT 1 FROM records WHERE id = ?", (record_id_,)).fetchone() is not None

    def approve(self, record_id_: str, *, reviewer: str, reason: str) -> MemoryRecord:
        """Record an approval of the proposed lesson ``record_id_``. The reviewer's name is the approving caller's
        claim, kept as given and never checked: G6 cannot tell a person from a model that types a person's name. A name
        that is an agent's, a model's or a tool's (in any capitals, with any spacing), or the lesson's own writer's, is
        refused; any other is recorded, and the lesson is then served marked ``approval_is: "claim"``. The store
        applies nothing."""
        reviewer, reason = reviewer.strip(), reason.strip()
        if not reviewer or _plain(reviewer).startswith(_NOT_A_PERSON):
            raise StoreError(REVIEWER, "a lesson is approved by a person, named as the reviewer; an agent, a model or "
                                       "a tool cannot approve one")
        if not reason:
            raise StoreError(REVIEWER, "say why the lesson is approved: the reason is kept with the approval")
        records, _ = self.load()
        record = next((r for r in records if r.id == record_id_), None)
        if record is None:
            raise StoreError(NOT_FOUND, f"no readable record {record_id_} is kept")
        if record.status != "proposed":
            raise StoreError(NOT_A_PROPOSAL, f"record {record_id_} is {record.status}, not a proposed lesson")
        if _plain(reviewer) == _plain(record.written_by):
            raise StoreError(REVIEWER, "the lesson's own writer cannot approve it")
        approved = record.model_copy(update={"approval": Approval(reviewer=reviewer, approved_at=now(),
                                                                  reason=reason[:2000])})
        approved = MemoryRecord.model_validate(approved.model_dump(exclude={"trust"}))
        body = _body(approved)
        if len(body) > MAX_RECORD_BYTES:
            raise StoreError(INPUT, f"the approved lesson would take {len(body)} bytes as kept; a record takes at most "
                                    f"{MAX_RECORD_BYTES}: give a shorter reason")
        try:
            with self._db() as conn:
                conn.execute("UPDATE records SET body = ?, sha256 = ? WHERE id = ?", (body, _digest(body), record.id))
        except sqlite3.Error as exc:
            raise StoreError(STORE, f"the memory store at {self.path} could not be written: {exc}") from None
        return approved

    def status(self) -> dict[str, object]:
        """How many records the store keeps, of each kind and state, how many cannot be read, and how many bytes they
        take of the most the store holds."""
        records, unreadable = self.load()
        at = self.clock()
        try:
            size = self._db().execute("SELECT COALESCE(SUM(LENGTH(body)), 0) FROM records").fetchone()[0]
        except sqlite3.Error as exc:
            raise StoreError(STORE, f"the memory store at {self.path} could not be read: {exc}") from None
        return {"store": str(self.path), "records": len(records), "notes": sum(r.kind == "note" for r in records),
                "lessons_approved": sum(r.status == "approved" for r in records),
                "lessons_proposed": sum(r.status == "proposed" for r in records),
                "stale": sum(is_stale(r, at) for r in records), "unreadable": len(unreadable),
                "oldest": records[0].created_at if records else "", "newest": records[-1].created_at if records else "",
                "max_records": MAX_RECORDS, "max_text": MAX_TEXT, "max_passage_text": MAX_PASSAGE_TEXT,
                "store_bytes": size, "max_record_bytes": MAX_RECORD_BYTES, "max_store_bytes": MAX_STORE_BYTES}


__all__ = ["FORGET_INCOMPLETE", "FULL", "INPUT", "MAX_RECORDS", "MAX_STORE_BYTES", "NOT_A_PROPOSAL", "NOT_FOUND",
           "NO_FILTER", "NO_REASON", "REVIEWER", "STORE", "Found", "Receipt", "Store", "StoreError"]
