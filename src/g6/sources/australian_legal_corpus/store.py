# SPDX-License-Identifier: Apache-2.0
"""What australian_legal_corpus keeps on disk, all under its folder in G6's state root: the manifest of the corpus file
(fetched into the folder, or registered where the person keeps it), and the SQLite FTS5 index built from it.

:func:`check_file` hashes a file against the pin; :func:`read_manifest` and :func:`write_manifest` keep what was checked
(a manifest that cannot be read counts as none). :class:`Index` is the index: :meth:`Index.build` reads the file from
its recorded byte offset, cuts each document's text into consecutive passages (joined in order they give the text
back exactly) and indexes them, committing the offset with every batch, so a call stopped by its record or time cap,
by low disk or by a crash resumes where the last batch ended. A line over the size cap, a line that is not JSON, a
record without the fields the README lists, a value outside its pinned set and a repeated ``version_id`` are counted
and skipped, never fatal. :meth:`Index.search` passes the caller's words as quoted FTS5 strings (never FTS5 syntax),
filtered by jurisdiction and type inside the full-text match and ranked by BM25 on the text; :meth:`Index.get` gives
one document back, bounded.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from g6.core.storage import open_db
from g6.sources.australian_legal_corpus.corpus import JURISDICTIONS, SOURCES, TYPES, Pin

MANIFEST = "manifest.json"
INDEX = "index.sqlite3"
#: A JSON line longer than this is counted and skipped (the largest acts are a few megabytes of text).
MAX_LINE_BYTES = 32 * 1024 * 1024
#: About how long a passage is, in characters: it ends at a line break or a space near this length.
PASSAGE_CHARS = 1500
#: Records a build call reads by default and at most, and seconds it runs by default and at most.
DEFAULT_RECORDS, MAX_RECORDS = 20_000, 250_000
DEFAULT_SECONDS, MAX_SECONDS = 120.0, 900.0
#: A build stops (its progress kept) when the disk holding the index has less free space than this.
BUILD_FLOOR = 1024 ** 3
#: The longest a search runs before it is stopped as too broad.
SEARCH_SECONDS = 10.0
#: The most of one document ``get`` returns per call, in characters (``start`` reads on).
GET_CHARS = 100_000
MAX_TERMS = 32
#: How many SQLite steps pass between two checks of a search's time limit.
PROGRESS_TICKS = 1000
UNREADABLE = "G6_E_LEGAL_CORPUS_INDEX_UNREADABLE"
TOO_BROAD = "G6_E_LEGAL_CORPUS_SEARCH_TOO_BROAD"
_BATCH = 500
_FIELDS = ("version_id", "type", "jurisdiction", "source", "citation", "mime", "date", "url", "when_scraped")
_REQUIRED = ("version_id", "type", "jurisdiction", "source", "citation", "url", "when_scraped")
_SKIPS = ("too_long", "not_json", "bad_record", "unpinned_value", "filtered", "repeated")
_WORD = re.compile(r"\w+")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_of(path: Path, chunk: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while piece := f.read(chunk):
            digest.update(piece)
    return digest.hexdigest()


def check_file(path: Path, pin: Pin) -> bool:
    """Whether ``path`` is a regular file of exactly the pinned size and SHA-256 (the size is checked first)."""
    try:
        if not path.is_file() or path.stat().st_size != pin.size:
            return False
        return sha256_of(path) == pin.sha256
    except OSError:
        return False


def read_manifest(folder: Path) -> dict[str, Any] | None:
    """The manifest, or ``None`` when there is none or it cannot be read (a damaged one counts as none)."""
    try:
        data = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    need = {"dataset": str, "revision": str, "filename": str, "path": str, "size": int, "sha256": str,
            "how": str, "checked": str, "mtime_ns": int}
    if not isinstance(data, dict) or any(not isinstance(data.get(k), t) for k, t in need.items()):
        return None
    return data


def write_manifest(folder: Path, pin: Pin, path: Path, how: str) -> dict[str, Any]:
    """Record ``path`` as the checked corpus file (``how``: ``fetched`` or ``registered``), written in one step."""
    stat = path.stat()
    data = {"dataset": pin.dataset, "revision": pin.revision, "filename": pin.filename, "path": str(path),
            "size": stat.st_size, "sha256": pin.sha256, "how": how, "checked": _now(), "mtime_ns": stat.st_mtime_ns}
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / f".{MANIFEST}.tmp"
    temporary.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(temporary, folder / MANIFEST)
    return data


def manifest_problem(manifest: dict[str, Any] | None, pin: Pin) -> str:
    """Why the manifest's file cannot be read as the pinned corpus now, or ``""``: none on record, another revision,
    the file gone, or its size or modification time changed since its hash was checked (it is not hashed again)."""
    if manifest is None:
        return "no corpus file has been fetched or registered"
    if (manifest["dataset"], manifest["revision"], manifest["sha256"]) != (pin.dataset, pin.revision, pin.sha256):
        return (f"the file on record is {manifest['dataset']} at revision {manifest['revision']}, not the pinned "
                f"{pin.dataset} at {pin.revision}")
    try:
        stat = Path(manifest["path"]).stat()
    except OSError:
        return f"the corpus file on record ({manifest['path']}) is no longer there"
    if stat.st_size != pin.size or stat.st_mtime_ns != manifest["mtime_ns"]:
        return (f"the corpus file on record ({manifest['path']}) has changed since its hash was checked on "
                f"{manifest['checked']}")
    return ""


def passages(text: str, size: int = PASSAGE_CHARS) -> list[str]:
    """``text`` cut into consecutive pieces of about ``size`` characters, each ending at a line break or a space in
    its second half where there is one; joined in order they are ``text`` exactly."""
    out, start, n = [], 0, len(text)
    while start < n:
        end = min(n, start + size)
        if end < n:
            half = start + size // 2
            cut = text.rfind("\n", half, end)
            if cut < 0:
                cut = max(text.rfind(" ", half, end), text.rfind("\t", half, end))
            if cut >= 0:
                end = cut + 1
        out.append(text[start:end])
        start = end
    return out


def _token(value: str) -> str:
    """A jurisdiction or type as one FTS5 token (``new_south_wales`` -> ``newsouthwales``)."""
    return value.replace("_", "")


def words_of(query: str) -> list[str]:
    """The query's words, in order, each once, at most :data:`MAX_TERMS`."""
    return list(dict.fromkeys(w.lower() for w in _WORD.findall(query or "")))[:MAX_TERMS]


def match_expression(words: list[str], jurisdictions: tuple[str, ...], types: tuple[str, ...], every: bool) -> str:
    """The FTS5 query: each word a quoted string (so no word is FTS5 syntax), all of them (``every``) or any, within
    the text column, and the jurisdictions and types as quoted tokens of their own columns."""
    joined = (" AND " if every else " OR ").join(f'"{w}"' for w in words)
    places = " OR ".join(f'"{_token(j)}"' for j in jurisdictions)
    kinds = " OR ".join(f'"{_token(t)}"' for t in types)
    return f"text : ({joined}) AND jurisdiction : ({places}) AND type : ({kinds})"


def _record(line: bytes) -> tuple[str, dict[str, Any] | None]:
    """The record a line holds, or why it is skipped."""
    try:
        record = json.loads(line)
    except ValueError:
        return "not_json", None
    if not isinstance(record, dict) or not isinstance(record.get("text"), str):
        return "bad_record", None
    if not all(isinstance(record.get(k), str) and record[k].strip() for k in _REQUIRED):
        return "bad_record", None
    if not isinstance(record.get("date"), str | None) or not isinstance(record.get("mime", ""), str):
        return "bad_record", None
    if record["type"] not in TYPES or record["jurisdiction"] not in JURISDICTIONS or record["source"] not in SOURCES:
        return "unpinned_value", None
    return "", record


@dataclass
class Progress:
    """What one build call did (counts for this call) and where the index stands (``offset``, ``complete``)."""

    lines: int = 0
    indexed: int = 0
    passages: int = 0
    skipped: dict[str, int] = field(default_factory=lambda: dict.fromkeys(_SKIPS, 0))
    stopped: str = ""          # "records", "seconds" or "disk"; "" when the end of the file was reached
    offset: int = 0
    complete: bool = False


class IndexProblem(Exception):
    """The index cannot be used as asked: ``code`` names why."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def _read_only(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


class Index:
    """The SQLite FTS5 index under ``folder``."""

    def __init__(self, folder: Path) -> None:
        self.folder, self.path = folder, folder / INDEX

    def exists(self) -> bool:
        return self.path.is_file()

    def _open(self) -> sqlite3.Connection:
        conn = open_db(self.path)
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS documents (
                    version_id TEXT PRIMARY KEY, type TEXT NOT NULL, jurisdiction TEXT NOT NULL, source TEXT NOT NULL,
                    citation TEXT NOT NULL, mime TEXT NOT NULL, date TEXT, url TEXT NOT NULL, when_scraped TEXT NOT NULL,
                    chars INTEGER NOT NULL, passages INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS passages (
                    id INTEGER PRIMARY KEY, version_id TEXT NOT NULL, seq INTEGER NOT NULL, text TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS passages_by_document ON passages (version_id, seq);
            """)
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'passages_fts'").fetchone() is None:
                conn.execute("CREATE VIRTUAL TABLE passages_fts USING fts5(text, jurisdiction, type, content='', "
                             "tokenize='unicode61 remove_diacritics 2')")
                conn.execute("INSERT INTO passages_fts (passages_fts, rank) VALUES ('rank', 'bm25(1.0, 0.0, 0.0)')")
                conn.commit()
        except BaseException:
            with contextlib.suppress(sqlite3.Error, OSError):
                conn.close()
            raise
        return conn

    def meta(self) -> dict[str, Any]:
        """The index's record of itself (``{}`` with no index)."""
        if not self.exists():
            return {}
        try:
            with contextlib.closing(_read_only(self.path)) as conn:
                rows = conn.execute("SELECT key, value FROM meta").fetchall()
            return {row["key"]: json.loads(row["value"]) for row in rows}
        except (sqlite3.Error, ValueError) as exc:
            raise IndexProblem(UNREADABLE, f"the index cannot be read ({exc}); remove it and build again") from exc

    def build(self, manifest: dict[str, Any], *, types: tuple[str, ...], jurisdictions: tuple[str, ...],
              max_records: int, max_seconds: float, free: Callable[[Path], int],
              clock: Callable[[], float] = time.monotonic, max_line: int = MAX_LINE_BYTES) -> Progress:
        """Index up to ``max_records`` more records for up to ``max_seconds``, from the recorded offset, with the
        filters the index was started with (``types`` and ``jurisdictions`` start a new one)."""
        conn = self._open()
        try:
            meta = {row["key"]: json.loads(row["value"]) for row in conn.execute("SELECT key, value FROM meta")}
            if not meta:
                meta = {"revision": manifest["revision"], "sha256": manifest["sha256"], "types": list(types),
                        "jurisdictions": list(jurisdictions), "offset": 0, "lines": 0, "indexed": 0, "passages": 0,
                        "skipped": dict.fromkeys(_SKIPS, 0), "complete": False, "started": _now()}
            base = {k: meta[k] for k in ("lines", "indexed", "passages")} | {"skipped": dict(meta["skipped"])}
            progress = Progress(offset=meta["offset"], complete=meta["complete"])
            if meta["complete"]:
                return progress
            began, kinds, places = clock(), set(meta["types"]), set(meta["jurisdictions"])
            with open(manifest["path"], "rb") as f:
                f.seek(meta["offset"])
                while True:
                    if progress.lines >= max_records:
                        progress.stopped = "records"
                        break
                    if clock() - began >= max_seconds:
                        progress.stopped = "seconds"
                        break
                    if progress.lines % _BATCH == 0 and free(self.folder) < BUILD_FLOOR:
                        progress.stopped = "disk"
                        break
                    line = f.readline(max_line + 1)
                    if not line:
                        progress.complete = True
                        break
                    progress.lines += 1
                    if len(line) > max_line and not line.endswith(b"\n"):
                        while (rest := f.readline(1024 * 1024)) and not rest.endswith(b"\n"):
                            pass
                        progress.skipped["too_long"] += 1
                    else:
                        self._take(conn, line, kinds, places, progress)
                    if progress.lines % _BATCH == 0:
                        self._commit(conn, meta, base, progress, f.tell())
                self._commit(conn, meta, base, progress, f.tell())
            return progress
        finally:
            conn.close()

    def _take(self, conn: sqlite3.Connection, line: bytes, kinds: set[str], places: set[str],
              progress: Progress) -> None:
        why, record = _record(line)
        if record is None:
            progress.skipped[why] += 1
            return
        if record["type"] not in kinds or record["jurisdiction"] not in places:
            progress.skipped["filtered"] += 1
            return
        pieces = passages(record["text"])
        row = [record.get(k) if k in ("date", "mime") else record[k] for k in _FIELDS]
        row[_FIELDS.index("mime")] = row[_FIELDS.index("mime")] or ""
        done = conn.execute("INSERT OR IGNORE INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (*row, len(record["text"]), len(pieces)))
        if done.rowcount == 0:
            progress.skipped["repeated"] += 1
            return
        for seq, piece in enumerate(pieces):
            rowid = conn.execute("INSERT INTO passages (version_id, seq, text) VALUES (?, ?, ?)",
                                 (record["version_id"], seq, piece)).lastrowid
            conn.execute("INSERT INTO passages_fts (rowid, text, jurisdiction, type) VALUES (?, ?, ?, ?)",
                         (rowid, piece, _token(record["jurisdiction"]), _token(record["type"])))
        progress.indexed += 1
        progress.passages += len(pieces)

    @staticmethod
    def _commit(conn: sqlite3.Connection, meta: dict[str, Any], base: dict[str, Any], progress: Progress,
                offset: int) -> None:
        """Commit the batch and the offset after it together, so a crash resumes after the last batch."""
        meta.update({k: base[k] + getattr(progress, k) for k in ("lines", "indexed", "passages")})
        meta["skipped"] = {k: base["skipped"].get(k, 0) + v for k, v in progress.skipped.items()}
        meta["offset"], meta["complete"], meta["updated"] = offset, progress.complete, _now()
        progress.offset = offset
        conn.executemany("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                         [(k, json.dumps(v)) for k, v in meta.items()])
        conn.commit()

    def search(self, words: list[str], *, jurisdictions: tuple[str, ...], types: tuple[str, ...], k: int,
               every: bool, seconds: float = SEARCH_SECONDS,
               clock: Callable[[], float] = time.monotonic) -> list[dict[str, Any]]:
        """The ``k`` best passages for ``words`` (BM25 on the text), each with its document's fields."""
        expression = match_expression(words, jurisdictions, types, every)
        with contextlib.closing(_read_only(self.path)) as conn:
            ends = clock() + seconds
            conn.set_progress_handler(lambda: 1 if clock() > ends else 0, PROGRESS_TICKS)
            try:
                ranked = conn.execute("SELECT rowid, rank FROM passages_fts WHERE passages_fts MATCH ? "
                                      "ORDER BY rank LIMIT ?", (expression, k)).fetchall()
            except sqlite3.OperationalError as exc:
                if "interrupt" in str(exc).lower():
                    raise IndexProblem(TOO_BROAD, f"the search did not finish within {seconds:g} seconds: add "
                                       "words, or narrow the jurisdiction or the types") from exc
                raise IndexProblem(UNREADABLE, f"the index could not be searched ({exc}); remove it and build "
                                   "again") from exc
            finally:
                conn.set_progress_handler(None, 0)
            hits = []
            for rank, row in enumerate(ranked, 1):
                found = conn.execute("SELECT p.text AS passage, p.seq AS passage_number, d.* FROM passages p JOIN "
                                     "documents d ON d.version_id = p.version_id WHERE p.id = ?",
                                     (row["rowid"],)).fetchone()
                if found is not None:
                    hits.append({"rank": rank, "bm25": round(float(row["rank"]), 6), **dict(found)})
            return hits

    def get(self, version_id: str, start: int = 0, limit: int = GET_CHARS) -> dict[str, Any] | None:
        """One document's fields and at most ``limit`` characters of its text from ``start``, or ``None``."""
        with contextlib.closing(_read_only(self.path)) as conn:
            doc = conn.execute("SELECT * FROM documents WHERE version_id = ?", (version_id,)).fetchone()
            if doc is None:
                return None
            text, at, kept = [], 0, 0
            for (piece,) in conn.execute("SELECT text FROM passages WHERE version_id = ? ORDER BY seq",
                                         (version_id,)):
                if at + len(piece) > start:
                    part = piece[max(0, start - at):]
                    text.append(part)
                    kept += len(part)
                at += len(piece)
                if kept >= limit:
                    break
            body = "".join(text)[:limit]
        return {**dict(doc), "text": body, "start": start, "returned_chars": len(body),
                "truncated": start + len(body) < doc["chars"]}

    def remove(self) -> list[str]:
        """Delete the index and SQLite's files beside it; the paths deleted."""
        gone = []
        for suffix in ("", "-wal", "-shm", "-journal"):
            target = self.path.with_name(self.path.name + suffix)
            with contextlib.suppress(FileNotFoundError):
                target.unlink()
                gone.append(str(target))
        return gone


__all__ = ["BUILD_FLOOR", "DEFAULT_RECORDS", "DEFAULT_SECONDS", "GET_CHARS", "INDEX", "MANIFEST", "MAX_LINE_BYTES",
           "MAX_RECORDS", "MAX_SECONDS", "MAX_TERMS", "PASSAGE_CHARS", "PROGRESS_TICKS", "SEARCH_SECONDS", "TOO_BROAD", "UNREADABLE",
           "Index", "IndexProblem", "Progress", "check_file", "manifest_problem", "match_expression", "passages",
           "read_manifest", "sha256_of", "words_of", "write_manifest"]
