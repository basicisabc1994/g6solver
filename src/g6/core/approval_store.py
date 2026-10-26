# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Durable, tamper-evident backing store for :mod:`mvp.core.approval_authority`.

WHY THIS EXISTS — two defects, one cause
========================================

``ApprovalAuthority`` kept every record in ``self._records``: a plain dict, on a
process-global singleton. MEASURED against the shipping topology
(``docker-compose.prod.yml``), that is not a storage choice, it is two bugs:

1. **Approvals are invisible across processes.** ``web``, ``rest``, ``mcp`` and
   ``celery-worker`` (``--concurrency=2``) are separate containers and separate
   processes, each with its own singleton. An approval minted on the surface the
   customer used cannot be seen by the process that runs the gate. The customer
   approves, and the gate refuses — not sometimes, but whenever the two land in
   different processes, which under this topology is the normal case.
2. **Approvals do not survive a restart.** Every record is lost on deploy.

The second is fatal for the purpose. The founder's ruling (2026-08-09) is that
the account holder approves regulated work and that this "acts as a legal guard
to protect g6 in the case that we get sued". **A consent record that a routine
deploy erases is not evidence of anything.** So the store has to be durable, and
being durable is not enough — it has to be *tamper-evident*, or it proves only
that a row exists today, not that it is the row that was written then.

WHY AN EVENT LOG RATHER THAN A ROW PER APPROVAL
===============================================

An approval mutates: it gets consumed, it gets revoked. A table of mutable rows
cannot be hash-chained, because the whole point of the chain is that nothing may
be edited after the fact. So this stores an APPEND-ONLY log of events — issued,
consumed, revoked — and derives current state by replaying an id's events. No
row is ever updated or deleted, the chain covers consumption and revocation as
well as issuance, and the audit answers "what happened to this approval", not
merely "what does it look like now".

Volumes are tiny (a human approving regulated work), so replay-per-lookup is
cheap and needs no cache to be correct.

POSTURE
=======

CONTROL PLANE ONLY. This is authority state: it decides whether regulated work
may proceed. It must never be routed through the per-tenant worker seam
(``connection_factory`` server-tenant mode) — a tenant that can write its own
approvals can approve its own regulated work. Hence a plain ``sqlite3`` path
chosen by the control plane, which is the same rule the P2-B classification
applies to ``task_tracker`` and the billing ledger.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol

from g6.core.approval_authority import ApprovalRecord

logger = logging.getLogger(__name__)

#: Chain root. Domain-separated so a hash from another chain cannot be spliced in.
GENESIS_HASH = "0" * 64

#: Env var naming the control-plane approval database. Absent -> in-memory, which
#: is the historical behaviour and is what every direct ``ApprovalAuthority()``
#: construction still gets.
DB_PATH_ENV = "G6_APPROVAL_DB_PATH"

EVENT_ISSUED = "issued"
EVENT_CONSUMED = "consumed"
EVENT_REVOKED = "revoked"
EVENT_KINDS = (EVENT_ISSUED, EVENT_CONSUMED, EVENT_REVOKED)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS approval_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    approval_id  TEXT NOT NULL,
    event        TEXT NOT NULL,
    timestamp    REAL NOT NULL,
    payload_json TEXT NOT NULL,
    prev_hash    TEXT NOT NULL,
    entry_hash   TEXT NOT NULL
)
"""

_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_approval_events_id "
    "ON approval_events(approval_id)"
)

#: WHY AN ANCHOR EXISTS — a hash chain alone does NOT detect TRUNCATION.
#: Caught by this module's own test: delete the most recent events and the
#: remaining prefix still verifies perfectly, because every surviving link still
#: matches its predecessor. That is exactly the edit someone would make to a
#: legal record: drop the revocation, drop the last few approvals. So the tail
#: is pinned out-of-band — the count and the tail hash — and a log shorter than
#: its anchor is a broken chain.
_ANCHOR_SQL = """
CREATE TABLE IF NOT EXISTS approval_anchor (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    event_count INTEGER NOT NULL,
    tail_hash   TEXT NOT NULL,
    updated_at  REAL NOT NULL
)
"""

#: Fields of ``ApprovalRecord`` persisted verbatim on the ``issued`` event. Kept
#: explicit rather than derived from ``__dataclass_fields__`` so that ADDING a
#: field to the record is a decision about whether it belongs in the permanent
#: legal record, not something that happens silently.
#:
#: SCHEMA COMPATIBILITY, both directions, because this list defines the persisted
#: payload. An OLD row replayed by NEW code is fine: the missing keys take their
#: dataclass defaults. A NEW row replayed by OLD code raises ``TypeError`` on the
#: unexpected keyword, which ``_replay`` catches and turns into ``None`` — the
#: approval is REFUSED, not misread. That is the right direction to fail, but it
#: does mean a rollback past this change makes approvals written after it
#: unusable, which is a deploy-ordering fact and not a silent one.
_ISSUED_FIELDS = (
    "approval_id",
    "reviewer_id",
    "scope",
    "decision",
    "created_at",
    "expires_at",
    "run_id",
    "single_use",
    "note",
    "issuer_kind",
    "policy_version",
    # The account-holder acceptance evidence. In the permanent record on purpose:
    # the founder's ruling makes this a legal guard, and a consent record that
    # does not carry what the person was shown, and what they said back, is a
    # record of a button press.
    "disclosure",
    "acknowledgement",
)


def entry_hash(prev_hash: str, approval_id: str, event: str, payload_json: str) -> str:
    """Domain-separated chain hash over the whole event, not just its payload.

    ``approval_id`` and ``event`` are hashed too: without them an attacker who
    could edit those two columns could relabel a ``revoked`` event as belonging
    to a different approval and the chain would still verify.
    """
    h = hashlib.sha256()
    h.update(b"g6-approval-authority-v1\x00")
    h.update(prev_hash.encode("utf-8"))
    h.update(b"\x00")
    h.update(approval_id.encode("utf-8"))
    h.update(b"\x00")
    h.update(event.encode("utf-8"))
    h.update(b"\x00")
    h.update(payload_json.encode("utf-8"))
    return h.hexdigest()


#: How long to keep trying to switch a database into WAL before giving up and
#: running in whatever journal mode it is already in.
_WAL_SETUP_TIMEOUT = 5.0
_WAL_RETRY_INTERVAL = 0.05


def _enable_wal(conn: sqlite3.Connection) -> bool:
    """Switch to WAL, waiting for a peer if necessary. BEST EFFORT — never raises.

    TWO MEASURED FACTS SHAPE THIS, and the second one contradicts the fix that
    was proposed for it.

    1. Switching journal mode needs the database's exclusive lock, so on a cold
       start where several containers open the same file at once, whichever ones
       lose the race hit ``database is locked``.
    2. ``PRAGMA busy_timeout`` DOES NOT COVER THIS STATEMENT — SQLite does not
       run the busy handler for a journal-mode change. Measured on one
       connection with the timeout already at 3000ms::

           PRAGMA journal_mode=WAL      -> raised after 0.00s
           CREATE TABLE IF NOT EXISTS   -> raised after 3.34s

       An independent review diagnosed this as "busy_timeout was not in effect"
       and prescribed setting it BEFORE the WAL pragma. Both halves are wrong:
       the timeout was already in effect at 5000ms (``sqlite3.connect``
       defaults ``timeout=5.0``), and no ordering helps a statement that ignores
       the busy handler. The symptom they found is real; the mechanism is not
       what it looked like, and the prescribed fix changes nothing. Hence an
       explicit retry here.

    AND IT DEGRADES RATHER THAN FAILING. WAL is a concurrency optimisation here:
    it lets the gate process read while an issuing process writes. Rollback
    journal is slower under contention, not wrong — ``busy_timeout`` covers the
    correctness of ordinary statements either way. Refusing to construct the
    store because we could not change journal mode would take approval checking
    down over a performance setting, and a store that cannot be built is a gate
    that refuses regulated work.
    """
    deadline = time.monotonic() + _WAL_SETUP_TIMEOUT
    last: Exception | None = None
    while True:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            return True
        except sqlite3.OperationalError as exc:  # a peer holds the lock
            last = exc
            if time.monotonic() >= deadline:
                break
            time.sleep(_WAL_RETRY_INTERVAL)
    logger.warning(
        "could not switch the approval database to WAL (%s); "
        "continuing in the existing journal mode", last,
    )
    return False


class ApprovalStore(Protocol):
    """What :class:`~mvp.core.approval_authority.ApprovalAuthority` needs."""

    #: Do records survive a restart and are they visible to other processes?
    #: An ISSUING surface must refuse to mint into a store that says False —
    #: see ``ApprovalAuthority.store_is_durable``.
    is_durable: bool

    def put(self, record: ApprovalRecord) -> None: ...
    def get(self, approval_id: str) -> ApprovalRecord | None: ...
    def try_consume(self, approval_id: str) -> bool: ...
    def mark_consumed(self, approval_id: str, at: float) -> None: ...
    def mark_revoked(self, approval_id: str, at: float) -> None: ...
    def purge_expired(self, *, now: float) -> int: ...
    def clear(self) -> None: ...


class InMemoryApprovalStore:
    """The historical behaviour, unchanged, extracted behind the interface.

    Kept as the default so a bare ``ApprovalAuthority()`` — which is what the
    existing test suite constructs — behaves exactly as before.
    """

    #: A dict on a per-process singleton. Neither of the two things a durable
    #: store promises is true here, which is exactly why an issuing surface must
    #: be able to ask before minting.
    is_durable = False

    def __init__(self) -> None:
        self._records: dict[str, ApprovalRecord] = {}
        self._lock = threading.RLock()

    def put(self, record: ApprovalRecord) -> None:
        with self._lock:
            self._records[record.approval_id] = record

    def get(self, approval_id: str) -> ApprovalRecord | None:
        with self._lock:
            return self._records.get(approval_id)

    def try_consume(self, approval_id: str) -> bool:
        """Same atomic contract as the durable store, under the local lock."""
        if not isinstance(approval_id, str) or not approval_id:
            return False
        with self._lock:
            record = self._records.get(approval_id)
            if record is None or not record.is_live():
                return False
            self._records[approval_id] = replace(record, consumed_at=time.time())
            return True

    def mark_consumed(self, approval_id: str, at: float) -> None:
        with self._lock:
            record = self._records.get(approval_id)
            if record is not None:
                self._records[approval_id] = replace(record, consumed_at=at)

    def mark_revoked(self, approval_id: str, at: float) -> None:
        with self._lock:
            record = self._records.get(approval_id)
            if record is not None:
                self._records[approval_id] = replace(record, revoked_at=at)

    def purge_expired(self, *, now: float) -> int:
        with self._lock:
            dead = [
                aid
                for aid, r in self._records.items()
                if (r.expires_at is not None and now >= r.expires_at)
                or r.revoked_at is not None
                or (r.single_use and r.consumed_at is not None)
            ]
            for aid in dead:
                self._records.pop(aid, None)
        return len(dead)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


class SqliteApprovalStore:
    """Durable, cross-process, append-only, hash-chained.

    Every mutation is an APPEND. Nothing is ever updated or deleted, so
    :meth:`verify_chain` covers consumption and revocation, not just issuance.
    """

    def __init__(self, db_path: str) -> None:
        if not str(db_path or "").strip():
            raise ValueError("SqliteApprovalStore requires a db_path")
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._ensure_schema()

    @property
    def is_durable(self) -> bool:
        """MEASURED per instance, not asserted by class.

        A flat ``is_durable = True`` on this class would have been a lie in one
        reachable configuration: this class accepts ``":memory:"`` (tests use
        it), and ``configured_store()`` hands it ANY non-blank
        ``G6_APPROVAL_DB_PATH`` — including that one. An operator who set it
        would get a store that PASSED the issuing surface's durability guard and
        had neither property. Being SQLite-backed is not the same fact as being
        on disk, and only the second one is what the guard is asking about.

        WHAT THIS STILL DOES NOT PROVE: that the path outlives the container. A
        file inside the image's writable layer, or on tmpfs, satisfies this and
        is erased by the next deploy. Pointing the path at a mounted volume is
        an operator obligation, not something this can check.
        """
        return self.db_path != ":memory:"

    # -- plumbing --------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            if self.db_path != ":memory:":
                Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            # isolation_level=None -> autocommit, so THIS module controls
            # transactions explicitly rather than relying on the driver's
            # implicit BEGIN, whose behaviour has changed across Python
            # versions. HONEST SCOPE: an independent re-review measured that the
            # suite still passes with the driver default, so this is defensive
            # about driver behaviour and is NOT the thing that serializes
            # writers. `BEGIN IMMEDIATE` is.
            self._conn = sqlite3.connect(
                self.db_path, check_same_thread=False, isolation_level=None
            )
            self._conn.row_factory = sqlite3.Row
            # REDUNDANT, AND SAID SO ON PURPOSE. `sqlite3.connect` already
            # defaults `timeout=5.0`, which sets busy_timeout to exactly this —
            # measured: a bare connection reports `PRAGMA busy_timeout` = 5000.
            # So this line states the value we are relying on rather than
            # leaving it implied in a driver default, and removing it is an
            # EQUIVALENT MUTATION, not an untested gap. It is NOT what fixed the
            # racy cold start; see `_enable_wal` for what did.
            self._conn.execute("PRAGMA busy_timeout=5000")
            # WAL so the gate process can read while an issuing process writes.
            if self.db_path != ":memory:":
                _enable_wal(self._conn)
        return self._conn

    def _discard_conn(self, conn: sqlite3.Connection) -> None:
        """Drop a connection that is stuck mid-transaction.

        WHY THIS IS NECESSARY. A cached connection left inside an open
        transaction is not merely useless to THIS process — it holds the SQLite
        write lock, so every peer container's writes fail with "database is
        locked" too. One transient disk error in one container would otherwise
        disable approval issuance fleet-wide until someone restarted it.
        Closing releases the lock, and the next call reconnects.
        """
        try:
            conn.close()
        except Exception:
            logger.exception("closing a wedged approval connection failed")
        if self._conn is conn:
            self._conn = None

    @contextmanager
    def _write_txn(self) -> Iterator[sqlite3.Connection]:
        """Serialize a read-compute-write across PROCESSES, not just threads.

        WHY THIS EXISTS. `_append` reads the tail hash, computes the next link
        from it, and inserts. `self._lock` is a ``threading.RLock`` — it orders
        threads inside ONE interpreter and means nothing to another container.
        MEASURED before this fix: two OS processes each issuing 120 approvals
        into one database committed all 240 rows without error, and
        ``verify_chain()`` then reported ``prev_hash does not match the
        preceding entry`` — because both had read the same tail and forked the
        chain. Under the very topology this module cites as its motivation, the
        tamper-evident log cried tamper on entirely honest data, which is worse
        than no checker at all: a detector with false positives gets switched
        off, and then it is not there for the true positive.

        ``BEGIN IMMEDIATE`` takes the RESERVED lock at the START of the
        transaction rather than on first write, so a second writer blocks here
        instead of racing to the same tail.

        THE COMMIT IS INSIDE THE TRY, and that is a fix, not a style choice. It
        used to sit after the ``except``, so a COMMIT that raised — a disk error
        is enough — neither committed nor rolled back. The connection stayed in
        an open transaction and was cached in that state, so every later write
        in the process raised "cannot start a transaction within a transaction",
        and because the stuck transaction still held the write lock, peer
        containers got "database is locked". One transient I/O hiccup wedged
        approval issuance everywhere until a restart.
        """
        with self._lock:
            conn = self._get_conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                self._abandon_txn(conn)
                raise

    def _abandon_txn(self, conn: sqlite3.Connection) -> None:
        """End a failed transaction without masking why it failed.

        The ROLLBACK is best-effort and its own failure is swallowed
        DELIBERATELY: the caller is already unwinding a real exception, and a
        secondary "rollback also failed" replacing it would hide the disk error
        that actually caused the problem behind a symptom of it.

        If the connection is still in a transaction afterwards, it is wedged and
        holding the lock — discard it rather than cache it.
        """
        try:
            conn.execute("ROLLBACK")
            still_open = conn.in_transaction
        except Exception:
            # Either the rollback failed, or the connection is too broken to say
            # whether it worked. Both mean the same thing here: we cannot show
            # this connection is clean, so it does not get to stay in the cache.
            logger.exception("approval transaction rollback failed")
            still_open = True
        if still_open:
            self._discard_conn(conn)

    @contextmanager
    def _read_txn(self) -> Iterator[sqlite3.Connection]:
        """ONE consistent snapshot across several SELECTs.

        WHY THIS EXISTS. ``verify_chain`` reads the event rows and then the
        anchor. Done as two autocommit statements they see two different
        database states, so an HONEST append committed by another container
        between them leaves ``anchor.event_count > len(rows)`` — which the
        truncation branch reports as "events were removed". MEASURED: a single
        interleaved honest append produced exactly that verdict on an intact
        four-event chain.

        That is the same false-positive failure this module already argues is
        the worst outcome available — a detector that cries tamper on honest
        data gets switched off, and then it is not there for the true positive.
        The write path was fixed for it; the read path had it too.

        A deferred ``BEGIN`` is right here: in WAL mode the read snapshot is
        fixed at the first read and held for the transaction, which is exactly
        the property needed, and it takes no write lock so it cannot block an
        issuing container.
        """
        with self._lock:
            conn = self._get_conn()
            conn.execute("BEGIN")
            try:
                yield conn
            finally:
                try:
                    conn.execute("COMMIT")
                except Exception:
                    logger.exception("ending an approval read transaction failed")
                    self._abandon_txn(conn)

    def _ensure_schema(self) -> None:
        with self._lock:
            conn = self._get_conn()
            # Statement at a time: apsw has no executescript, and this module is
            # deliberately plain-sqlite3 control-plane, but the habit is cheap.
            conn.execute(_SCHEMA_SQL)
            conn.execute(_INDEX_SQL)
            conn.execute(_ANCHOR_SQL)
            conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _append_unlocked(
        self, conn: sqlite3.Connection, approval_id: str, event: str,
        payload: dict[str, Any],
    ) -> str:
        """Append one event. MUST be called inside :meth:`_write_txn`."""
        if event not in EVENT_KINDS:
            raise ValueError(f"unknown approval event: {event!r}")
        now = time.time()
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        tail = conn.execute(
            "SELECT entry_hash FROM approval_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        prev_hash = tail["entry_hash"] if tail is not None else GENESIS_HASH
        digest = entry_hash(prev_hash, approval_id, event, payload_json)
        conn.execute(
            "INSERT INTO approval_events "
            "(approval_id, event, timestamp, payload_json, prev_hash, entry_hash) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (approval_id, event, now, payload_json, prev_hash, digest),
        )
        # Same transaction as the insert: an anchor that can lag the log by even
        # one commit would report a false break after a crash.
        count = conn.execute("SELECT COUNT(*) FROM approval_events").fetchone()[0]
        conn.execute(
            "INSERT INTO approval_anchor (id, event_count, tail_hash, updated_at) "
            "VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "event_count = excluded.event_count, "
            "tail_hash = excluded.tail_hash, "
            "updated_at = excluded.updated_at",
            (int(count), digest, now),
        )
        return digest

    def _append(self, approval_id: str, event: str, payload: dict[str, Any]) -> str:
        with self._write_txn() as conn:
            return self._append_unlocked(conn, approval_id, event, payload)

    # -- the store interface ---------------------------------------------

    def put(self, record: ApprovalRecord) -> None:
        payload = {name: getattr(record, name) for name in _ISSUED_FIELDS}
        self._append(record.approval_id, EVENT_ISSUED, payload)

    def _replay(
        self, conn: sqlite3.Connection, approval_id: str
    ) -> ApprovalRecord | None:
        """Reconstruct current state from this id's events. FAIL CLOSED."""
        rows = conn.execute(
            "SELECT event, payload_json, timestamp FROM approval_events "
            "WHERE approval_id = ? ORDER BY id ASC",
            (approval_id,),
        ).fetchall()
        if not rows:
            return None

        record: ApprovalRecord | None = None
        for row in rows:
            event = row["event"]
            if event == EVENT_ISSUED:
                if record is not None:
                    # An id issued twice is a token collision or a tampered log.
                    # Either way it is not something to guess about.
                    return None
                try:
                    payload = json.loads(row["payload_json"])
                    record = ApprovalRecord(**payload)
                except (TypeError, ValueError):
                    # ValueError covers json.JSONDecodeError. A payload written
                    # by a different schema version, or corrupted: fail closed
                    # rather than reconstruct a partial approval, and never let
                    # it escape as an exception -- `check()` promises to refuse,
                    # not to raise.
                    return None
            elif record is None:
                # consumed/revoked before issued: a tampered or truncated log.
                return None
            elif event == EVENT_CONSUMED:
                record = replace(record, consumed_at=float(row["timestamp"]))
            elif event == EVENT_REVOKED:
                record = replace(record, revoked_at=float(row["timestamp"]))
        return record

    def get(self, approval_id: str) -> ApprovalRecord | None:
        """Replay this id's events into its current state.

        Returns None if it was never issued. An id with a ``consumed`` or
        ``revoked`` event still resolves — to a record carrying that timestamp,
        so ``is_live()`` refuses it. Forgetting it instead would let a consumed
        id be re-issued and read as fresh.
        """
        if not isinstance(approval_id, str) or not approval_id:
            return None
        with self._lock:
            return self._replay(self._get_conn(), approval_id)

    def try_consume(self, approval_id: str) -> bool:
        """Consume ONCE, atomically, across processes. True if we won.

        WHY THIS IS NOT `get()` THEN `mark_consumed()`. That is a TOCTOU, and it
        was measured to lose: two processes racing ``check(consume=True)`` over
        500 shared ids each produced ids with TWO ``consumed`` events and
        ``True`` returned to BOTH — a single-use approval authorizing twice.
        For an approval that releases regulated work, "single use" that is only
        single-use-per-process is not a limit at all.

        The liveness test and the append happen inside ONE ``BEGIN IMMEDIATE``
        transaction, so the second caller reads the state the first one wrote.
        """
        if not isinstance(approval_id, str) or not approval_id:
            return False
        with self._write_txn() as conn:
            record = self._replay(conn, approval_id)
            if record is None or not record.is_live():
                return False
            self._append_unlocked(
                conn, approval_id, EVENT_CONSUMED, {"at": time.time()}
            )
            return True

    def mark_consumed(self, approval_id: str, at: float) -> None:
        with self._write_txn() as conn:
            if self._replay(conn, approval_id) is None:
                return
            self._append_unlocked(conn, approval_id, EVENT_CONSUMED, {"at": at})

    def mark_revoked(self, approval_id: str, at: float) -> None:
        with self._write_txn() as conn:
            if self._replay(conn, approval_id) is None:
                return
            self._append_unlocked(conn, approval_id, EVENT_REVOKED, {"at": at})

    def purge_expired(self, *, now: float) -> int:
        """DELIBERATELY A NO-OP. Returns 0.

        The in-memory store purges to bound a dict that would otherwise grow
        forever. Here, purging would mean DELETING AUDIT ROWS — destroying the
        evidence that the store exists to preserve, and breaking
        :meth:`verify_chain` for every event after the gap.

        Nothing is lost by keeping them. A dead approval is already refused by
        ``is_live()``, and retaining it is what stops a consumed id from being
        re-issued and read as fresh. Retention policy on a legal record is a
        founder decision with an export step, not a background sweep.
        """
        return 0

    def clear(self) -> None:
        """Test/maintenance helper. NOT part of the audit contract.

        Deliberately truncates rather than pretending to be append-only about
        it: a caller asking to clear the authority is asking for a fresh chain.
        """
        with self._write_txn() as conn:
            conn.execute("DELETE FROM approval_events")
            # The anchor goes with it. Leaving it behind would make an
            # intentionally-emptied store look truncated, i.e. cry tamper on a
            # clean slate — and a chain checker that cries wolf gets ignored.
            conn.execute("DELETE FROM approval_anchor")

    # -- tamper evidence --------------------------------------------------

    def verify_chain(self) -> dict[str, Any]:
        """Recompute every link and check the tail against the anchor.

        Detects in-place edits, reordering, insertion, AND truncation. Returns
        ``{"ok": bool, "checked": int, "broken_at": int | None, "reason": str}``.

        WHAT THIS DOES NOT PROVE. Someone with write access to this database can
        rewrite the log AND the anchor together and the result verifies — the
        anchor raises the cost of tampering, it does not make it impossible.
        Non-repudiation against the operator needs the tail published somewhere
        we cannot retract (a signed periodic checkpoint, an offsite copy).
        ``job_framework.audit.publish_checkpoint`` is the existing machinery for
        that and is NOT wired here. Say "tamper-evident", never "tamper-proof".
        """
        with self._read_txn() as conn:
            rows = conn.execute(
                "SELECT id, approval_id, event, payload_json, prev_hash, entry_hash "
                "FROM approval_events ORDER BY id ASC"
            ).fetchall()
            anchor = conn.execute(
                "SELECT event_count, tail_hash FROM approval_anchor WHERE id = 1"
            ).fetchone()

        prev = GENESIS_HASH
        for index, row in enumerate(rows):
            if row["prev_hash"] != prev:
                return {
                    "ok": False,
                    "checked": index,
                    "broken_at": int(row["id"]),
                    "reason": "prev_hash does not match the preceding entry",
                }
            expected = entry_hash(
                prev, row["approval_id"], row["event"], row["payload_json"]
            )
            if expected != row["entry_hash"]:
                return {
                    "ok": False,
                    "checked": index,
                    "broken_at": int(row["id"]),
                    "reason": "entry_hash does not match the recorded event",
                }
            prev = row["entry_hash"]

        if anchor is None:
            if rows:
                # Events with no anchor: either the anchor row was deleted, or
                # this log predates anchoring. Neither is something to wave past.
                return {
                    "ok": False,
                    "checked": len(rows),
                    "broken_at": None,
                    "reason": "anchor missing for a non-empty log",
                }
        elif int(anchor["event_count"]) != len(rows) or anchor["tail_hash"] != prev:
            # THE TRUNCATION CASE. Every surviving link verified above; the log
            # is simply shorter than what was written.
            return {
                "ok": False,
                "checked": len(rows),
                "broken_at": None,
                "reason": (
                    f"log holds {len(rows)} events, anchor records "
                    f"{int(anchor['event_count'])} — events were removed"
                ),
            }
        return {"ok": True, "checked": len(rows), "broken_at": None, "reason": ""}

    def history(self, approval_id: str) -> list[dict[str, Any]]:
        """Every recorded event for one approval, oldest first."""
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT event, timestamp, payload_json FROM approval_events "
                "WHERE approval_id = ? ORDER BY id ASC",
                (approval_id,),
            ).fetchall()
        return [
            {
                "event": row["event"],
                "timestamp": row["timestamp"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]


def configured_store() -> ApprovalStore:
    """The store the process-wide authority should use.

    Durable when ``G6_APPROVAL_DB_PATH`` names a database, in-memory otherwise.

    THE PRECONDITION FOR THAT DEFAULT NO LONGER HOLDS. This used to say
    in-memory was acceptable "only because nothing in the tree mints approvals
    yet". Something does now — the account-holder approval surface ships — so
    leaving this unset is a live misconfiguration. It is not a SECURITY hole:
    an in-memory store reports ``is_durable = False`` and issuance refuses,
    so nothing forgeable is minted. It is an AVAILABILITY hole, and a total
    one: every regulated agent requires an approval, no approval can be
    issued, and no customer can unblock themselves. The env templates and
    compose files now set it; this default stays fail-closed for the case
    where someone runs the code with no environment at all.

    ``~`` and ``$VARS`` are expanded here. sqlite3 does NOT expand them, and
    the store only does ``Path(db_path).parent.mkdir(...)`` — so an operator
    following the sibling convention in ``.env.example``
    (``IMMUNE_DB_PATH=~/.g6/immune.db``) would silently get a directory
    literally named ``~`` in the process working directory, and an approval
    store that "works" in a place nobody looks and that no other container
    shares. Expanding is a no-op on the absolute container paths.
    """
    path = os.environ.get(DB_PATH_ENV, "").strip()
    if not path:
        return InMemoryApprovalStore()
    return SqliteApprovalStore(os.path.expanduser(os.path.expandvars(path)))


__all__ = [
    "DB_PATH_ENV",
    "EVENT_CONSUMED",
    "EVENT_ISSUED",
    "EVENT_KINDS",
    "EVENT_REVOKED",
    "GENESIS_HASH",
    "ApprovalStore",
    "InMemoryApprovalStore",
    "SqliteApprovalStore",
    "configured_store",
    "entry_hash",
]
