# SPDX-License-Identifier: Apache-2.0
"""The Context Engine's per-project registry (design 12.6): source registrations, source-to-passage membership,
generations and tombstones, in one SQLite file per project.

``_``-prefixed, so it never registers as a component. The file is
``$G6_HOME/knowledge/projects/<project>/registry.db``; every change is one ``BEGIN IMMEDIATE`` transaction, so two
server processes on one project see one order of events.

* A :class:`Registration` is one folder a person accepted in the client's dialog. Its id is random and stable for its
  life. A revoked registration never becomes active again.
* A :class:`Member` is one file of one registration in one generation, with the ids and offsets of its passages.
* A :class:`Generation` is staged, filled, and published in one transaction only when every required backend's
  receipt says it read back what was written. Readers use the published generation's number; a staged generation
  left behind by a crash is marked abandoned by the next stage.
* A :class:`Tombstone` is written, and the registration's deny epoch raised, before anything is deleted:
  :meth:`ProjectStore.use` then denies the registration's passages (``revoked``) or one file's passages in the
  generations that held the old bytes (``changed``) although an index may still hold the rows.

:meth:`ProjectStore.use` is the check every return path calls, with the clock: it answers which of some passage ids
may be used now, and why each other one may not.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from g6.core.storage import state_path
from g6._context_engine._envelope import BackendState, Denied
from g6._context_engine._scope import Access, Binding, ScopeDenied, bound
from g6.knowledge._types import instant, now, sha256_hex

SOURCE_UNKNOWN = "G6_E_KNOWLEDGE_SOURCE"
PREVIEW_USED = "G6_E_KNOWLEDGE_PREVIEW_USED"
NOT_PUBLISHED = "G6_E_KNOWLEDGE_NOT_PUBLISHED"
STATE = "G6_E_KNOWLEDGE_STATE"
SUPERSEDED = "G6_E_KNOWLEDGE_SUPERSEDED"
#: Published generations whose members and index file are kept: the current one and the one a reader may still pin.
KEPT_GENERATIONS = 2
_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS registrations (id TEXT PRIMARY KEY, root TEXT NOT NULL, label TEXT NOT NULL, "
    "preview_sha256 TEXT NOT NULL UNIQUE, attribution_sha256 TEXT NOT NULL, created_at TEXT NOT NULL, "
    "expires_at TEXT NOT NULL, retention TEXT NOT NULL, authorisation_revision INTEGER NOT NULL, "
    "deny_epoch INTEGER NOT NULL, state TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS generations (number INTEGER PRIMARY KEY, state TEXT NOT NULL, backends TEXT NOT NULL, "
    "published_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS members (generation INTEGER NOT NULL, registration_id TEXT NOT NULL, uri TEXT NOT NULL, "
    "relative TEXT NOT NULL, file_sha256 TEXT NOT NULL, size INTEGER NOT NULL, document_sha256 TEXT NOT NULL, "
    "media_type TEXT NOT NULL, converter TEXT NOT NULL, chunker TEXT NOT NULL, unread TEXT NOT NULL, "
    "PRIMARY KEY (generation, uri))",
    "CREATE TABLE IF NOT EXISTS member_passages (generation INTEGER NOT NULL, passage_id TEXT NOT NULL, "
    "registration_id TEXT NOT NULL, uri TEXT NOT NULL, span_start INTEGER NOT NULL, span_end INTEGER NOT NULL, "
    "position INTEGER NOT NULL, PRIMARY KEY (generation, passage_id))",
    "CREATE TABLE IF NOT EXISTS tombstones (n INTEGER PRIMARY KEY AUTOINCREMENT, registration_id TEXT NOT NULL, "
    "epoch INTEGER NOT NULL, at TEXT NOT NULL, reason TEXT NOT NULL, uri TEXT NOT NULL, generation INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS receipts (generation INTEGER NOT NULL, backend TEXT NOT NULL, item TEXT NOT NULL, "
    "action TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL, PRIMARY KEY (generation, backend, item, action))",
    "CREATE TABLE IF NOT EXISTS dependents (kind TEXT NOT NULL, ident TEXT NOT NULL, registration_id TEXT NOT NULL, "
    "passage_ids TEXT NOT NULL, uris TEXT NOT NULL, generation INTEGER NOT NULL, watermark INTEGER NOT NULL, "
    "PRIMARY KEY (kind, ident, registration_id))",
    "CREATE TABLE IF NOT EXISTS snapshots (id TEXT PRIMARY KEY, body TEXT NOT NULL, created_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS snapshot_sources (id TEXT NOT NULL, registration_id TEXT NOT NULL, "
    "PRIMARY KEY (id, registration_id))",
    "CREATE TABLE IF NOT EXISTS retained (sha256 TEXT NOT NULL, registration_id TEXT NOT NULL, "
    "PRIMARY KEY (sha256, registration_id))",
    "CREATE TABLE IF NOT EXISTS rereads (n INTEGER PRIMARY KEY AUTOINCREMENT, registration_id TEXT NOT NULL, "
    "generation INTEGER NOT NULL, body TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS normative_grants (id TEXT PRIMARY KEY, registration_id TEXT NOT NULL, body TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, body TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS pending_removals (path TEXT PRIMARY KEY)",
    "CREATE TABLE IF NOT EXISTS nodes (generation INTEGER NOT NULL, body TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS edges (generation INTEGER NOT NULL, id TEXT NOT NULL, subject TEXT NOT NULL, "
    "object TEXT NOT NULL, relation TEXT NOT NULL, body TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS proposed_edges (id TEXT PRIMARY KEY, registration_id TEXT NOT NULL, body TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS accepted_backends (registration_id TEXT NOT NULL, backend TEXT NOT NULL, "
    "PRIMARY KEY (registration_id, backend))",
)
_HEX64 = "0123456789abcdef"


class Refused(Exception):
    """Something the Context Engine will not do, with its stable code and a reason that holds no source content."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"[{code}] {reason}")
        self.code, self.reason = code, reason


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Registration(_Record):
    id: str
    root: str
    label: str
    preview_sha256: str
    attribution_sha256: str
    created_at: str
    expires_at: str = ""
    retention: Literal["none", "audit"] = "none"
    authorisation_revision: int = 1
    deny_epoch: int = 0
    state: Literal["active", "revoked", "deleted"] = "active"
    #: The optional backends the preview the person accepted lists. Nothing else is ever written to for this source.
    backends: tuple[str, ...] = ()


class Member(_Record):
    registration_id: str
    generation: int
    uri: str
    relative: str
    file_sha256: str
    size: int
    document_sha256: str
    passage_ids: tuple[str, ...] = ()
    spans: tuple[tuple[int, int], ...] = ()
    media_type: str = "text/plain"
    converter: str = ""
    chunker: str = ""
    unread: tuple[str, ...] = ()


class Generation(_Record):
    number: int
    state: Literal["staging", "published", "abandoned"]
    backends: dict[str, dict[str, Any]]
    published_at: str = ""


class Tombstone(_Record):
    registration_id: str
    epoch: int
    at: str
    reason: Literal["revoked", "changed", "deleted"]
    uri: str = ""
    generation: int = 0


class Receipt(_Record):
    """One item's progress in one backend of one staged generation."""

    generation: int
    backend: str
    item: str
    action: Literal["add", "remove"]
    state: Literal["pending", "acknowledged", "read_back", "failed"]
    attempts: int = 1


class Dependent(_Record):
    """A stored consumer of some passages of one registration: it must ask :meth:`ProjectStore.invalidated` before
    every use of what it stored."""

    kind: Literal["cache", "pack", "snapshot", "graph_edge", "model_edge", "skill_trial", "skill_admission",
                  "normative_grant"]
    ident: str
    registration_id: str
    passage_ids: tuple[str, ...] = ()
    generation: int = 0


def may_use(registration: Any, at: datetime) -> str:
    """The one answer to "may this registration be used at this time": ``""`` when it may, otherwise why not
    (``unknown``, ``revoked`` for a revoked or deleted one, ``expired``). Every consumer asks this, with the time:
    no caller reads the state alone. ``registration`` is a :class:`Registration`, a registry row or ``None``."""
    if registration is None:
        return "unknown"
    state = registration["state"] if not isinstance(registration, Registration) else registration.state
    expires_at = registration["expires_at"] if not isinstance(registration, Registration) else registration.expires_at
    if state != "active":
        return "revoked"
    if expires_at:
        expires = instant(expires_at)
        if expires is None or expires <= at:
            return "expired"
    return ""


def is_kept(registration: Any) -> bool:
    """Whether a registration's passages stay in the next generation's index: until it is revoked or deleted. This
    is about what the index holds, never about use: use is :func:`may_use`, with the time."""
    state = registration["state"] if not isinstance(registration, Registration) else registration.state
    return state not in ("revoked", "deleted")


def new_registration_id() -> str:
    return "src-" + secrets.token_hex(8)


def source_uri(registration_id: str, relative: str) -> str:
    """The address a file of a registration is cited by: no absolute path, so it names nothing about the machine."""
    return f"g6-knowledge://{registration_id}/{relative}"


class ProjectStore:
    """The registry of one project; see the module docstring."""

    def __init__(self, binding: Binding) -> None:
        self.binding = bound(binding)
        self.folder = state_path("knowledge", "projects", binding.project)
        self.path = self.folder / "registry.db"

    # -- the file -----------------------------------------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _reading(self) -> Iterator[sqlite3.Connection | None]:
        if not self.path.exists():
            yield None
            return
        conn = self._connect()
        try:
            yield conn
        except sqlite3.Error as exc:
            raise Refused(STATE, f"the project's registry could not be read ({type(exc).__name__})") from None
        finally:
            conn.close()

    @contextmanager
    def _writing(self) -> Iterator[sqlite3.Connection]:
        self.folder.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            for statement in _SCHEMA:
                conn.execute(statement)
            yield conn
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise Refused(STATE, f"the project's registry could not be written ({type(exc).__name__})") from None
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    @staticmethod
    def _has(conn: sqlite3.Connection | None, table: str = "registrations") -> bool:
        return conn is not None and conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None

    def index_path(self, backend: str, generation: int) -> Path:
        """Where the index of ``backend`` for ``generation`` lives; only this store names such a file."""
        names = {"sqlite": "lexical"}
        if backend not in names:
            raise Refused(STATE, f"no local index file is kept for the {backend} backend")
        return self.folder / f"{names[backend]}.g{int(generation)}.db"

    # -- registrations ------------------------------------------------------------------------------------------------

    def registrations(self) -> list[Registration]:
        with self._reading() as conn:
            if not self._has(conn):
                return []
            accepted: dict[str, list[str]] = {}
            if self._has(conn, "accepted_backends"):
                for row in conn.execute("SELECT registration_id, backend FROM accepted_backends ORDER BY backend"):
                    accepted.setdefault(row["registration_id"], []).append(row["backend"])
            return [Registration(**dict(row), backends=tuple(accepted.get(row["id"], ())))
                    for row in conn.execute("SELECT * FROM registrations ORDER BY created_at, id")]

    def registration(self, registration_id: str) -> Registration | None:
        return next((r for r in self.registrations() if r.id == registration_id), None)

    def register(self, preview: Any, attribution: str) -> Registration:
        """A new active registration for an accepted preview. A preview is committed once: its digest is refused a
        second time, so a revoked registration cannot come back through the preview that made it."""
        made = Registration(id=new_registration_id(), root=preview.root, label=preview.label,
                            preview_sha256=preview.sha256, attribution_sha256=sha256_hex(attribution),
                            created_at=now(), expires_at=preview.expires_at, retention=preview.retention,
                            backends=tuple(sorted(b for b in preview.backends if b != "sqlite")))
        with self._writing() as conn:
            if conn.execute("SELECT 1 FROM registrations WHERE preview_sha256 = ?", (made.preview_sha256,)).fetchone():
                raise Refused(PREVIEW_USED, "this preview was already committed; a new preview and a new dialog are "
                                            "needed to add the folder again")
            conn.execute("INSERT INTO registrations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(
                made.model_dump()[k] for k in ("id", "root", "label", "preview_sha256", "attribution_sha256",
                                               "created_at", "expires_at", "retention", "authorisation_revision",
                                               "deny_epoch", "state")))
            conn.executemany("INSERT INTO accepted_backends VALUES (?, ?)", [(made.id, b) for b in made.backends])
        return made

    def _tombstone(self, conn: sqlite3.Connection, registration_id: str, reason: str, uri: str,
                   state: str | None) -> Tombstone:
        row = conn.execute("SELECT * FROM registrations WHERE id = ?", (registration_id,)).fetchone()
        if row is None:
            raise Refused(SOURCE_UNKNOWN, "no such source registration in this project")
        epoch = row["deny_epoch"] + 1
        generation = conn.execute("SELECT COALESCE(MAX(number), 0) FROM generations WHERE state = 'published'").fetchone()[0]
        made = Tombstone(registration_id=registration_id, epoch=epoch, at=now(), reason=reason, uri=uri,
                         generation=generation)
        conn.execute("UPDATE registrations SET deny_epoch = ?, authorisation_revision = authorisation_revision + ?, "
                     "state = COALESCE(?, state) WHERE id = ?", (epoch, 1 if state else 0, state, registration_id))
        conn.execute("INSERT INTO tombstones (registration_id, epoch, at, reason, uri, generation) VALUES (?, ?, ?, ?, ?, ?)",
                     (made.registration_id, made.epoch, made.at, made.reason, made.uri, made.generation))
        return made

    def revoke(self, registration_id: str) -> Tombstone:
        """Raise the deny epoch and write the tombstone, in one transaction, before anything is deleted."""
        with self._writing() as conn:
            row = conn.execute("SELECT state FROM registrations WHERE id = ?", (registration_id,)).fetchone()
            if row is not None and not is_kept(row):
                last = conn.execute("SELECT * FROM tombstones WHERE registration_id = ? AND uri = '' ORDER BY n DESC "
                                    "LIMIT 1", (registration_id,)).fetchone()
                if last is not None:
                    return Tombstone(**{k: last[k] for k in ("registration_id", "epoch", "at", "reason", "uri",
                                                             "generation")})
            return self._tombstone(conn, registration_id, "revoked", "", "revoked")

    def changed(self, registration_id: str, uri: str) -> Tombstone:
        """Deny one file's passages in every generation published so far: its bytes were observed to differ."""
        with self._writing() as conn:
            return self._tombstone(conn, registration_id, "changed", uri, None)

    def tombstones(self) -> list[Tombstone]:
        with self._reading() as conn:
            if not self._has(conn, "tombstones"):
                return []
            return [Tombstone(**{k: row[k] for k in ("registration_id", "epoch", "at", "reason", "uri", "generation")})
                    for row in conn.execute("SELECT * FROM tombstones ORDER BY n")]

    # -- generations --------------------------------------------------------------------------------------------------

    @staticmethod
    def _generation(row: sqlite3.Row) -> Generation:
        return Generation(number=row["number"], state=row["state"], backends=json.loads(row["backends"]),
                          published_at=row["published_at"])

    def stage(self, backends: Mapping[str, bool]) -> Generation:
        """A new staging generation; one left staging by a crash is marked abandoned first."""
        stale: list[int] = []
        with self._writing() as conn:
            stale = [r["number"] for r in conn.execute("SELECT number FROM generations WHERE state = 'staging'")]
            for number in stale:
                conn.execute("UPDATE generations SET state = 'abandoned' WHERE number = ?", (number,))
                conn.execute("DELETE FROM members WHERE generation = ?", (number,))
                conn.execute("DELETE FROM member_passages WHERE generation = ?", (number,))
            number = conn.execute("SELECT COALESCE(MAX(number), 0) + 1 FROM generations").fetchone()[0]
            made = Generation(number=number, state="staging",
                              backends={name: {"required": bool(required), "state": "unavailable", "receipt": {}}
                                        for name, required in backends.items()})
            conn.execute("INSERT INTO generations VALUES (?, ?, ?, ?)",
                         (made.number, made.state, json.dumps(made.backends, sort_keys=True), ""))
        for number in stale:
            self._drop_index_files(number)
        self.retry_removals()
        return made

    def abandon(self, generation: int) -> None:
        """Give up a staging generation now: its membership and its index file go (the file through the checked
        removal, so one that cannot be removed yet is retried later)."""
        with self._writing() as conn:
            conn.execute("UPDATE generations SET state = 'abandoned' WHERE number = ? AND state = 'staging'", (generation,))
            conn.execute("DELETE FROM members WHERE generation = ?", (generation,))
            conn.execute("DELETE FROM member_passages WHERE generation = ?", (generation,))
        self._drop_index_files(generation)

    def record(self, generation: int, members: Sequence[Member]) -> None:
        """The membership of a staging generation."""
        with self._writing() as conn:
            row = conn.execute("SELECT state FROM generations WHERE number = ?", (generation,)).fetchone()
            if row is None or row["state"] != "staging":
                raise Refused(STATE, "membership is recorded only in a staging generation")
            for m in members:
                if len(m.passage_ids) != len(m.spans):
                    raise Refused(STATE, "a member's passage ids and spans must correspond")
                conn.execute("INSERT INTO members VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (generation, m.registration_id, m.uri, m.relative, m.file_sha256, m.size,
                              m.document_sha256, m.media_type, m.converter, m.chunker, json.dumps(list(m.unread))))
                conn.executemany("INSERT INTO member_passages VALUES (?, ?, ?, ?, ?, ?, ?)",
                                 [(generation, pid, m.registration_id, m.uri, start, end, n)
                                  for n, (pid, (start, end)) in enumerate(zip(m.passage_ids, m.spans, strict=True))])

    def publish(self, generation: int, receipts: Mapping[str, dict], *, after: int | None = None) -> Generation:
        """Make ``generation`` the one readers use, in one transaction; :class:`Refused` (and nothing changed)
        unless every required backend's receipt says it read back exactly the passages the membership lists.
        ``after`` is the published generation the new one was prepared from (0: none): when another publication
        happened since it was read, this one is refused in the same transaction, so nothing is published over a
        generation its membership was not built from. Members of a registration that is no longer kept are refused
        too."""
        with self._writing() as conn:
            row = conn.execute("SELECT * FROM generations WHERE number = ?", (generation,)).fetchone()
            if row is None or row["state"] != "staging":
                raise Refused(NOT_PUBLISHED, "only a staging generation can be published")
            newest = conn.execute("SELECT COALESCE(MAX(number), 0) FROM generations WHERE state = 'published'").fetchone()[0]
            if after is not None and newest != after:
                raise Refused(SUPERSEDED, "another publication happened while this one was prepared; nothing "
                                          "was published over it. Run the operation again.")
            gone = conn.execute(
                "SELECT count(*) FROM members m JOIN registrations r ON r.id = m.registration_id "
                "WHERE m.generation = ? AND r.state = 'deleted'", (generation,)).fetchone()[0]
            if gone:
                raise Refused(NOT_PUBLISHED, "the generation lists files of a source that was deleted while it was "
                                             "prepared; nothing was published. Run the operation again.")
            listed = conn.execute("SELECT count(*) FROM member_passages WHERE generation = ?", (generation,)).fetchone()[0]
            backends = json.loads(row["backends"])
            for name, entry in backends.items():
                receipt = dict(receipts.get(name) or {})
                complete = receipt.get("written") == listed and receipt.get("read_back") == listed
                if entry["required"] and not complete:
                    raise Refused(NOT_PUBLISHED, f"the {name} backend did not read back every passage of the "
                                                 "generation, so it is not published")
                state: BackendState = "answered" if complete else ("partial" if receipt else "unavailable")
                entry.update(state=state, receipt=receipt)
            published_at = now()
            conn.execute("UPDATE generations SET state = 'published', backends = ?, published_at = ? WHERE number = ?",
                         (json.dumps(backends, sort_keys=True), published_at, generation))
            old = [r["number"] for r in conn.execute(
                "SELECT number FROM generations WHERE state = 'published' ORDER BY number DESC LIMIT -1 OFFSET ?",
                (KEPT_GENERATIONS,))]
            for number in old:
                conn.execute("DELETE FROM members WHERE generation = ?", (number,))
                conn.execute("DELETE FROM member_passages WHERE generation = ?", (number,))
        for number in old:
            self._drop_index_files(number)
        self.retry_removals()
        return Generation(number=generation, state="published", backends=backends, published_at=published_at)

    def _drop_index_files(self, generation: int) -> bool:
        """Remove a generation's index file and what SQLite keeps beside it; whether all of it is gone."""
        return all([self.remove_file(Path(f"{self.index_path('sqlite', generation)}{suffix}"))
                    for suffix in ("", "-journal", "-wal", "-shm")])

    # -- removal: checked, and retried until it is done ------------------------------------------------------------

    def _pending_key(self, path: Path) -> str:
        try:
            return "store:" + path.resolve().relative_to(self.folder.resolve()).as_posix()
        except ValueError:
            return "file:" + str(path)

    def _pending_path(self, key: str) -> Path:
        return self.folder / key[6:] if key.startswith("store:") else Path(key[5:])

    def pend(self, paths: Sequence[Path]) -> None:
        """Record files that must be removed, durably, before any attempt to remove them."""
        if paths:
            with self._writing() as conn:
                conn.executemany("INSERT OR IGNORE INTO pending_removals VALUES (?)",
                                 [(self._pending_key(p),) for p in paths])

    def remove_file(self, path: Path) -> bool:
        """Remove one file that holds a source's bytes or names. True when it is gone (or was never there). A file
        that cannot be removed now (another program has it open) is recorded in the pending-removals list and every
        later deletion, stage and publish tries again; nothing reports it removed until it is."""
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            self.pend([path])
            return False
        key = self._pending_key(path)
        with self._reading() as conn:
            listed = self._has(conn, "pending_removals") and conn.execute(
                "SELECT 1 FROM pending_removals WHERE path = ?", (key,)).fetchone() is not None
        if listed:
            with self._writing() as conn:
                conn.execute("DELETE FROM pending_removals WHERE path = ?", (key,))
        return True

    def pending_removals(self) -> list[str]:
        with self._reading() as conn:
            if not self._has(conn, "pending_removals"):
                return []
            return [row["path"] for row in conn.execute("SELECT path FROM pending_removals ORDER BY path")]

    def retry_removals(self) -> list[str]:
        """Try every pending removal again; what is still pending afterwards."""
        for key in self.pending_removals():
            if key == "vacuum:registry":
                self._vacuum()
            else:
                self.remove_file(self._pending_path(key))
        return self.pending_removals()

    def _vacuum(self) -> bool:
        """Rewrite the registry file so that no freed page holds a deleted row's bytes; pending when it fails."""
        conn = self._connect()
        try:
            conn.execute("VACUUM")
            done = True
        except sqlite3.Error:
            done = False
        finally:
            conn.close()
        with self._writing() as conn:
            if done:
                conn.execute("DELETE FROM pending_removals WHERE path = 'vacuum:registry'")
            else:
                conn.execute("INSERT OR IGNORE INTO pending_removals VALUES ('vacuum:registry')")
        return done

    def published(self) -> Generation | None:
        with self._reading() as conn:
            if not self._has(conn, "generations"):
                return None
            row = conn.execute("SELECT * FROM generations WHERE state = 'published' ORDER BY number DESC LIMIT 1").fetchone()
            return self._generation(row) if row is not None else None

    def generations(self) -> list[Generation]:
        with self._reading() as conn:
            if not self._has(conn, "generations"):
                return []
            return [self._generation(row) for row in conn.execute("SELECT * FROM generations ORDER BY number")]

    def members(self, generation: int, registration_id: str | None = None) -> list[Member]:
        with self._reading() as conn:
            if not self._has(conn, "members"):
                return []
            return self._members(conn, generation, registration_id=registration_id)

    @staticmethod
    def _members(conn: sqlite3.Connection, generation: int, *, registration_id: str | None = None,
                 uris: Sequence[str] | None = None) -> list[Member]:
        query, args = "SELECT * FROM members WHERE generation = ?", [generation]
        if registration_id is not None:
            query, args = query + " AND registration_id = ?", [*args, registration_id]
        rows = [r for r in conn.execute(query + " ORDER BY uri", args) if uris is None or r["uri"] in set(uris)]
        out = []
        for r in rows:
            cut = conn.execute("SELECT passage_id, span_start, span_end FROM member_passages WHERE generation = ? "
                               "AND uri = ? ORDER BY position", (generation, r["uri"])).fetchall()
            out.append(Member(registration_id=r["registration_id"], generation=generation, uri=r["uri"],
                              relative=r["relative"], file_sha256=r["file_sha256"], size=r["size"],
                              document_sha256=r["document_sha256"], media_type=r["media_type"],
                              converter=r["converter"], chunker=r["chunker"], unread=tuple(json.loads(r["unread"])),
                              passage_ids=tuple(c["passage_id"] for c in cut),
                              spans=tuple((c["span_start"], c["span_end"]) for c in cut)))
        return out

    # -- receipts, rereads, retained bytes and snapshots -------------------------------------------------------------

    def receipt(self, receipt: Receipt) -> None:
        with self._writing() as conn:
            conn.execute("INSERT INTO receipts VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (generation, backend, item, action) "
                         "DO UPDATE SET state = excluded.state, attempts = receipts.attempts + 1",
                         (receipt.generation, receipt.backend, receipt.item, receipt.action, receipt.state,
                          receipt.attempts))

    def receipts_to(self, generation: int, state: str, backend: str = "sqlite") -> None:
        """Move every receipt of ``generation`` in ``backend`` to ``state``."""
        with self._writing() as conn:
            conn.execute("UPDATE receipts SET state = ? WHERE generation = ? AND backend = ?", (state, generation, backend))

    def receipts(self, generation: int) -> list[Receipt]:
        with self._reading() as conn:
            if not self._has(conn, "receipts"):
                return []
            return [Receipt(**dict(row)) for row in conn.execute(
                "SELECT * FROM receipts WHERE generation = ? ORDER BY backend, action, item", (generation,))]

    def reread(self, receipt: Any, generation: int) -> None:
        """Keep the receipt of one re-read: the source version it saw, and when."""
        with self._writing() as conn:
            conn.execute("INSERT INTO rereads (registration_id, generation, body) VALUES (?, ?, ?)",
                         (receipt.registration_id, generation, receipt.model_dump_json()))

    def rereads(self, registration_id: str) -> list[Any]:
        from g6._context_engine._refresh import Reread

        with self._reading() as conn:
            if not self._has(conn, "rereads"):
                return []
            return [Reread.model_validate_json(row["body"]) for row in conn.execute(
                "SELECT body FROM rereads WHERE registration_id = ? ORDER BY n", (registration_id,))]

    def retain(self, data: bytes, registration_id: str = "") -> str:
        """Keep ``data`` as an audit copy, by its hash, in the project's own folder; no index reads that folder."""
        digest = sha256_hex(data)
        folder = self.folder / "retained"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / digest
        if not path.exists():
            path.write_bytes(data)
        with self._writing() as conn:
            conn.execute("INSERT OR IGNORE INTO retained VALUES (?, ?)", (digest, registration_id))
        return digest

    def unretain(self, kept: Sequence[tuple[str, str]]) -> None:
        """Take back audit copies written by a step that did not complete: each ``(digest, registration id)`` row,
        and through the checked removal each file no row names any more."""
        files = []
        with self._writing() as conn:
            for digest, registration_id in kept:
                conn.execute("DELETE FROM retained WHERE sha256 = ? AND registration_id = ?", (digest, registration_id))
                if conn.execute("SELECT 1 FROM retained WHERE sha256 = ?", (digest,)).fetchone() is None:
                    files.append(self.folder / "retained" / digest)
            conn.executemany("INSERT OR IGNORE INTO pending_removals VALUES (?)", [(self._pending_key(p),) for p in files])
        for path in files:
            self.remove_file(path)

    def has_retained(self, digest: str, registration_id: str) -> bool:
        with self._reading() as conn:
            return self._has(conn, "retained") and conn.execute(
                "SELECT 1 FROM retained WHERE sha256 = ? AND registration_id = ?", (digest, registration_id)).fetchone() is not None

    def retained(self, digest: str) -> bytes | None:
        """The audit copy kept under ``digest``, checked against it; ``None`` when none is kept."""
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in _HEX64 for c in digest):
            return None
        try:
            data = (self.folder / "retained" / digest).read_bytes()
        except OSError:
            return None
        return data if sha256_hex(data) == digest else None

    def keep_snapshot(self, ident: str, body: str, registration_ids: Sequence[str] = ()) -> None:
        with self._writing() as conn:
            conn.execute("INSERT OR IGNORE INTO snapshots VALUES (?, ?, ?)", (ident, body, now()))
            conn.executemany("INSERT OR IGNORE INTO snapshot_sources VALUES (?, ?)",
                             [(ident, r) for r in registration_ids])

    def snapshot(self, ident: str) -> str | None:
        with self._reading() as conn:
            if not self._has(conn, "snapshots"):
                return None
            row = conn.execute("SELECT body FROM snapshots WHERE id = ?", (ident,)).fetchone()
            return row["body"] if row is not None else None

    def put_graph(self, generation: int, nodes: Sequence[str], edges: Sequence[tuple[str, str, str, str, str]]) -> None:
        """Replace the graph kept for ``generation``: derived records, rebuilt from the files at any time."""
        with self._writing() as conn:
            conn.execute("DELETE FROM nodes WHERE generation = ?", (generation,))
            conn.execute("DELETE FROM edges WHERE generation = ?", (generation,))
            conn.executemany("INSERT INTO nodes VALUES (?, ?)", [(generation, body) for body in nodes])
            conn.executemany("INSERT INTO edges VALUES (?, ?, ?, ?, ?, ?)", [(generation, *row) for row in edges])

    def graph(self, generation: int) -> tuple[list[str] | None, list[str]]:
        """The nodes and edges kept for ``generation``; ``(None, [])`` when no graph was built for it."""
        with self._reading() as conn:
            if not self._has(conn, "nodes"):
                return None, []
            nodes = [row["body"] for row in conn.execute("SELECT body FROM nodes WHERE generation = ?", (generation,))]
            edges = [row["body"] for row in conn.execute("SELECT body FROM edges WHERE generation = ? ORDER BY id",
                                                         (generation,))]
            return (nodes or None), edges

    def propose_edge(self, ident: str, body: str, registration_id: str) -> None:
        """Keep a model-proposed relation (``g6._context_engine._relations``): a lead, never evidence. Its writer registers
        it as a stored consumer, and :meth:`erase` removes it with its registration."""
        with self._writing() as conn:
            conn.execute("INSERT OR IGNORE INTO proposed_edges VALUES (?, ?, ?)", (ident, registration_id, body))

    def proposed_edges(self, registrations: Sequence[str]) -> list[str]:
        """The proposed relations of ``registrations`` and of no other: the caller's scope is decided first."""
        wanted = sorted(set(registrations))
        with self._reading() as conn:
            if not wanted or not self._has(conn, "proposed_edges"):
                return []
            marks = ",".join("?" * len(wanted))
            return [row["body"] for row in conn.execute(
                f"SELECT body FROM proposed_edges WHERE registration_id IN ({marks}) ORDER BY id", wanted)]

    def put_job(self, ident: str, body: str) -> None:
        with self._writing() as conn:
            conn.execute("INSERT OR REPLACE INTO jobs VALUES (?, ?)", (ident, body))

    def job(self, ident: str) -> str | None:
        with self._reading() as conn:
            if not self._has(conn, "jobs"):
                return None
            row = conn.execute("SELECT body FROM jobs WHERE id = ?", (ident,)).fetchone()
            return row["body"] if row is not None else None

    def jobs(self) -> list[str]:
        with self._reading() as conn:
            if not self._has(conn, "jobs"):
                return []
            return [row["body"] for row in conn.execute("SELECT body FROM jobs ORDER BY rowid")]

    def backend_state(self, generation: int, backend: str, state: str, receipt: Mapping[str, Any], *,
                      registration_id: str) -> None:
        """Record what an optional backend holds of one registration in a published generation. The backend's own
        state is ``answered`` when every registration recorded for it is, ``partial`` when some are, and otherwise
        the state given. A required backend's record is never changed here: only publication writes it."""
        with self._writing() as conn:
            row = conn.execute("SELECT backends FROM generations WHERE number = ? AND state = 'published'",
                               (generation,)).fetchone()
            if row is None:
                raise Refused(STATE, "an optional backend is recorded only for a published generation")
            backends = json.loads(row["backends"])
            entry = backends.get(backend) or {"required": False, "state": "unavailable", "receipt": {},
                                              "registrations": {}}
            if entry.get("required"):
                raise Refused(STATE, "a required backend's record is written by publication only")
            held = dict(entry.get("registrations") or {})
            held[registration_id] = {"state": state, "receipt": dict(receipt)}
            states = {one["state"] for one in held.values()}
            entry.update(registrations=held, receipt=dict(receipt),
                         state="answered" if states == {"answered"} else "partial" if "answered" in states else state)
            backends[backend] = entry
            conn.execute("UPDATE generations SET backends = ? WHERE number = ?",
                         (json.dumps(backends, sort_keys=True), generation))

    def grant_normative(self, ident: str, body: str, registration_id: str = "") -> None:
        """Keep a normative grant's record (``g6._context_engine._support``): made from a person's confirmed rule answer."""
        with self._writing() as conn:
            conn.execute("INSERT OR IGNORE INTO normative_grants VALUES (?, ?, ?)", (ident, registration_id, body))

    def normative_grants(self) -> list[str]:
        with self._reading() as conn:
            if not self._has(conn, "normative_grants"):
                return []
            return [row["body"] for row in conn.execute("SELECT body FROM normative_grants ORDER BY rowid")]

    # -- reverse dependencies -----------------------------------------------------------------------------------------

    def depend(self, dependent: Dependent) -> None:
        """Record a stored consumer. The watermark is the newest tombstone at this moment: any later tombstone on the
        registration, or on a file holding one of the consumer's passages, invalidates it."""
        with self._writing() as conn:
            uris = sorted({row["uri"] for pid in dependent.passage_ids for row in conn.execute(
                "SELECT uri FROM member_passages WHERE passage_id = ? AND registration_id = ?",
                (pid, dependent.registration_id))})
            watermark = conn.execute("SELECT COALESCE(MAX(n), 0) FROM tombstones").fetchone()[0]
            conn.execute("INSERT OR REPLACE INTO dependents VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (dependent.kind, dependent.ident, dependent.registration_id,
                          json.dumps(list(dependent.passage_ids)), json.dumps(uris), dependent.generation, watermark))

    def invalidated(self, kind: str, ident: str, *, at: datetime | None = None) -> list[Denied]:
        """Why the stored consumer ``(kind, ident)`` may no longer be used at ``at``: ``[]`` when it still may. The
        time is always checked: a caller that gives none is answered for this moment, never without the expiry check
        (G6's own callers all give it; an inventory test holds them to that). A
        consumer that was never recorded is ``unknown``; one whose registration may not be used at this time
        (:func:`may_use`: revoked, deleted or expired), or one of whose files was observed changed or removed since
        it was recorded, is denied: a refresh publishes new passages and does not revive a consumer of the old ones."""
        with self._reading() as conn:
            rows = conn.execute("SELECT * FROM dependents WHERE kind = ? AND ident = ?", (kind, ident)).fetchall() \
                if self._has(conn, "dependents") else []
            if not rows:
                return [Denied(passage_id="", registration_id="", why="unknown")]
            found: dict[str, Denied] = {}
            at = at if at is not None else datetime.now(UTC)
            for row in rows:
                registration = conn.execute("SELECT * FROM registrations WHERE id = ?", (row["registration_id"],)).fetchone()
                passages = json.loads(row["passage_ids"])
                why = may_use(registration, at)
                why = "revoked" if why == "unknown" else why
                if not why:
                    uris = set(json.loads(row["uris"]))
                    later = conn.execute("SELECT uri FROM tombstones WHERE registration_id = ? AND n > ?",
                                         (row["registration_id"], row["watermark"])).fetchall()
                    if any(t["uri"] == "" or t["uri"] in uris for t in later):
                        why = "changed"
                if why:
                    found.setdefault(why, Denied(passage_id=passages[0] if passages else "",
                                                 registration_id=row["registration_id"], why=why))
            return list(found.values())

    def usable(self, at: datetime, *, sources: Any = None) -> list[Registration]:
        """The registrations that may be used at ``at`` (:func:`may_use`), inside the caller's narrowing."""
        return [r for r in self.registrations() if may_use(r, at) == "" and (sources is None or r.id in sources)]

    # -- deletion -----------------------------------------------------------------------------------------------------

    def delete(self, registration_id: str, *, complete: bool) -> dict[str, Any]:
        """Mark a registration deleted: a tombstone first, as for a revoke, so it is denied from this moment. Nothing
        that lists its passages is erased here: the index is rebuilt without it first, and :meth:`erase` runs after
        that publication, so a deletion interrupted at any point can be run again and completes."""
        with self._writing() as conn:
            row = conn.execute("SELECT * FROM registrations WHERE id = ?", (registration_id,)).fetchone()
            if row is None:
                raise Refused(SOURCE_UNKNOWN, "no such source registration in this project")
            tombstone = self._tombstone(conn, registration_id, "deleted", "", "deleted")
        return {"source": registration_id, "state": "deleted", "deny_epoch": tombstone.epoch,
                "audit_copies": "kept" if not complete else "pending", "preview_sha256": row["preview_sha256"],
                "root": row["root"]}

    def forget_retained(self, registration_id: str) -> bool:
        """Drop a registration's audit copies: the rows that name them and, through the checked removal, each file no
        other registration retains. The files are listed as pending in the transaction that drops the rows."""
        with self._writing() as conn:
            files = []
            for kept in conn.execute("SELECT sha256 FROM retained WHERE registration_id = ?", (registration_id,)).fetchall():
                conn.execute("DELETE FROM retained WHERE sha256 = ? AND registration_id = ?", (kept["sha256"], registration_id))
                if conn.execute("SELECT 1 FROM retained WHERE sha256 = ?", (kept["sha256"],)).fetchone() is None:
                    files.append(self.folder / "retained" / kept["sha256"])
            conn.executemany("INSERT OR IGNORE INTO pending_removals VALUES (?)", [(self._pending_key(p),) for p in files])
        return all([self.remove_file(path) for path in files])

    def erase(self, registration_id: str) -> None:
        """After the index was published without a deleted registration: erase every record that holds its content
        (membership in every generation, receipts, snapshots, audit copies, grants, its changed-file tombstones, its
        root and its label) and rewrite the registry file. Only its id, its state and its deletion tombstones stay."""
        with self._writing() as conn:
            row = conn.execute("SELECT state FROM registrations WHERE id = ?", (registration_id,)).fetchone()
            if row is None or row["state"] != "deleted":
                raise Refused(STATE, "only a deleted registration is erased")
            ids = [r["passage_id"] for r in conn.execute(
                "SELECT DISTINCT passage_id FROM member_passages WHERE registration_id = ?", (registration_id,))]
            for start in range(0, len(ids), 500):
                marks = ",".join("?" * len(ids[start:start + 500]))
                conn.execute(f"DELETE FROM receipts WHERE item IN ({marks})", ids[start:start + 500])
            for table in ("members", "member_passages", "rereads", "normative_grants", "proposed_edges"):
                conn.execute(f"DELETE FROM {table} WHERE registration_id = ?", (registration_id,))
            conn.execute("DELETE FROM nodes")   # the graph is derived: it is rebuilt without the source
            conn.execute("DELETE FROM edges")
            conn.execute("DELETE FROM tombstones WHERE registration_id = ? AND uri != ''", (registration_id,))
            # a saved context holds view text of every source in it: each pack that holds this source goes whole
            packs = [self.folder / "packs" / f"{r['ident']}.json" for r in conn.execute(
                "SELECT DISTINCT ident FROM dependents WHERE kind = 'pack' AND registration_id = ?", (registration_id,))]
            # every holder is looked in, not only the recorded ones: a saved context whose record was never written,
            # or was written for another of its sources, still holds this source's text
            folder = self.folder / "packs"
            for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
                try:
                    holds = any(item.get("source") == registration_id
                                for item in json.loads(path.read_text(encoding="utf-8")).get("items", []))
                except (OSError, ValueError, AttributeError):
                    holds = True   # a saved context that cannot be read cannot be shown to hold no text of it
                if holds and path not in packs:
                    packs.append(path)
            conn.executemany("INSERT OR IGNORE INTO pending_removals VALUES (?)", [(self._pending_key(p),) for p in packs])
            if self._has(conn, "jobs"):   # a job's record keeps its counts; the names of files it could not read go
                for row in conn.execute("SELECT id, body FROM jobs").fetchall():
                    body = json.loads(row["body"])
                    if body.get("registration_id") == registration_id and body.get("unread"):
                        conn.execute("UPDATE jobs SET body = ? WHERE id = ?", (json.dumps({**body, "unread": []}), row["id"]))
            conn.execute("UPDATE dependents SET passage_ids = '[]', uris = '[]' WHERE registration_id = ?",
                         (registration_id,))
            for found in conn.execute("SELECT id FROM snapshot_sources WHERE registration_id = ?",
                                      (registration_id,)).fetchall():
                conn.execute("DELETE FROM snapshots WHERE id = ?", (found["id"],))
                conn.execute("DELETE FROM snapshot_sources WHERE id = ?", (found["id"],))
            conn.execute("UPDATE registrations SET root = '', label = '' WHERE id = ?", (registration_id,))
            conn.execute("INSERT OR IGNORE INTO pending_removals VALUES ('vacuum:registry')")
        for path in packs:
            self.remove_file(path)
        self.forget_retained(registration_id)
        self._vacuum()
    # -- the check at every use ---------------------------------------------------------------------------------------

    def use(self, access: Access, passage_ids: Sequence[str], *, at: datetime,
            generation: int | None = None) -> tuple[list[Member], list[Denied]]:
        """Which of ``passage_ids`` may be used now under ``access``: the members holding the ones that may, and a
        :class:`Denied` for each that may not (``not_published``, ``unknown``, ``revoked``, ``expired``, ``changed``).
        ``generation`` is the published generation a reader pinned; it defaults to the current one. A passage of a
        registration outside the caller's narrowing is ``unknown``: narrowing never widens and never reveals."""
        if not isinstance(access, Access) or bound(access.binding).project != self.binding.project:
            raise ScopeDenied("the access is not bound to this project")
        wanted = list(dict.fromkeys(passage_ids))
        with self._reading() as conn:
            if not self._has(conn, "generations"):
                return [], [Denied(passage_id=p, registration_id="", why="not_published") for p in wanted]
            if generation is None:
                current = conn.execute("SELECT MAX(number) FROM generations WHERE state = 'published'").fetchone()[0]
            else:
                current = conn.execute("SELECT number FROM generations WHERE state = 'published' AND number = ?",
                                       (generation,)).fetchone()
                current = current[0] if current is not None else None
            if current is None:
                return [], [Denied(passage_id=p, registration_id="", why="not_published") for p in wanted]
            registrations = {r["id"]: r for r in conn.execute("SELECT * FROM registrations")}
            changed: dict[tuple[str, str], int] = {}
            for t in conn.execute("SELECT registration_id, uri, MAX(generation) AS g FROM tombstones WHERE reason = "
                                  "'changed' GROUP BY registration_id, uri"):
                changed[t["registration_id"], t["uri"]] = t["g"]
            allowed: list[str] = []
            denied: list[Denied] = []
            for pid in wanted:
                row = conn.execute("SELECT registration_id, uri FROM member_passages WHERE generation = ? AND "
                                   "passage_id = ?", (current, pid)).fetchone()
                registration = registrations.get(row["registration_id"]) if row is not None else None
                if registration is None or (access.sources is not None and registration["id"] not in access.sources):
                    denied.append(Denied(passage_id=pid, registration_id="", why="unknown"))
                    continue
                why = may_use(registration, at)
                if not why and changed.get((registration["id"], row["uri"]), -1) >= current:
                    why = "changed"
                if why:
                    denied.append(Denied(passage_id=pid, registration_id=registration["id"], why=why))
                else:
                    allowed.append(row["uri"])
            members = self._members(conn, current, uris=list(dict.fromkeys(allowed))) if allowed else []
        return members, denied


__all__ = ["KEPT_GENERATIONS", "NOT_PUBLISHED", "PREVIEW_USED", "SOURCE_UNKNOWN", "STATE", "Denied", "Dependent", "Generation",
           "Member", "ProjectStore", "Receipt", "Refused", "Registration", "Tombstone", "is_kept", "may_use",
           "new_registration_id", "source_uri"]
