# SPDX-License-Identifier: Apache-2.0

"""Durable, tamper-evident SQLite store for the action-gating audit trail.

The in-memory ``ActionGateAuditLogger`` loses every audit record on process
restart. An auditor (and ISO/IEC 42001 Clause 9.2) expects a *persisted,
tamper-evident, retained* audit log. This module provides that durable backing
store, matching the house persistence idiom of ``token_budget.ledger`` (SQLite
under ``$G6_HOME``, WAL mode, per-instance lock, commit-on-write) and the
hash-chaining idiom of the marketplace transparency log (each entry's hash
chains the prior entry's hash so an in-place edit or a reordering is detectable).

HONEST SCOPE: this is durability + retention + append-only tamper-EVIDENCE
(detection), not tamper-PROOFNESS (an attacker with write access to the DB file
can rewrite the whole chain). It is the persistence backing for the action-gating
audit trail; it does not, by itself, satisfy the broader live-substantive PA-15
criterion (an inspectable trail distinguishing model/tool/formal/human-approval
across every surface) — it is partial evidence toward it.

Unlike ``InstructorStore``, this store deliberately does NOT silently fall back to
``:memory:`` when the filesystem path is unusable: an audit trail that silently
stops persisting would defeat its purpose, so a connection failure propagates and
the caller's fail-closed audit path blocks the action. ``:memory:`` is supported
only when requested explicitly (tests).
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
from pathlib import Path

from g6.action_gating.schema import ActionGateAuditRecord
from g6.core.storage import state_path

# Under $G6_HOME (see g6.core.storage); the archive ignored G6_HOME and always used ~/.g6.
_DEFAULT_DB_NAME = "action_gating_audit.db"

# Genesis predecessor hash for the first entry in an empty chain.
_GENESIS_HASH = "0" * 64

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS audit_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    decision TEXT NOT NULL,
    confirmation_required INTEGER NOT NULL DEFAULT 0,
    gate_token_issued INTEGER NOT NULL DEFAULT 0,
    record_json TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_anchor (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    chain_length INTEGER NOT NULL,
    head_hash TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_audit_id ON audit_records(audit_id);
CREATE INDEX IF NOT EXISTS idx_audit_action_id ON audit_records(action_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_records(timestamp);
"""


def _entry_hash(prev_hash: str, record_json: str) -> str:
    """Domain-separated chain hash of (prev_hash, record_json)."""
    h = hashlib.sha256()
    h.update(b"g6-action-gating-audit-v1\x00")
    h.update(prev_hash.encode("utf-8"))
    h.update(b"\x00")
    h.update(record_json.encode("utf-8"))
    return h.hexdigest()


class ActionGateAuditStore:
    """Durable, append-only, hash-chained store for ``ActionGateAuditRecord``.

    Thread-safe via a per-instance lock. WAL mode allows concurrent readers.
    """

    _default_instance: ActionGateAuditStore | None = None
    _default_lock = threading.Lock()

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or str(state_path(_DEFAULT_DB_NAME))
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._ensure_schema()

    @classmethod
    def default(cls) -> ActionGateAuditStore:
        if cls._default_instance is not None:
            return cls._default_instance
        with cls._default_lock:
            if cls._default_instance is None:
                cls._default_instance = cls()
        return cls._default_instance

    @classmethod
    def reset_default(cls) -> None:
        with cls._default_lock:
            if cls._default_instance is not None:
                cls._default_instance.close()
            cls._default_instance = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            if self.db_path != ":memory:":
                Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def _ensure_schema(self) -> None:
        with self._lock:
            conn = self._get_conn()
            had_anchor = conn.execute("SELECT 1 FROM sqlite_master WHERE name='audit_anchor'").fetchone()
            conn.executescript(_SCHEMA_SQL)
            # Never silently bless a pre-existing unanchored log or repair a deleted anchor.
            if not had_anchor and conn.execute("SELECT COUNT(*) FROM audit_records").fetchone()[0] == 0:
                conn.execute("INSERT INTO audit_anchor VALUES (1, 0, ?)", (_GENESIS_HASH,))
            conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ------------------------------------------------------------------
    # Recording (append-only, hash-chained)
    # ------------------------------------------------------------------

    def append(self, record: ActionGateAuditRecord) -> str:
        """Persist one audit record; return its chain entry-hash.

        Append-only: the new entry chains the prior tail's ``entry_hash`` so any
        later in-place edit or reordering breaks :meth:`verify_chain`.
        """
        record_json = record.model_dump_json()
        with self._lock:
            conn = self._get_conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                report = self._verify_unlocked(conn)
                if not report["ok"]:
                    raise RuntimeError("[G6_E_AUDIT_CHAIN_BROKEN] " + report["reason"])
                tail = conn.execute(
                    "SELECT entry_hash FROM audit_records ORDER BY id DESC LIMIT 1"
                ).fetchone()
                prev_hash = tail["entry_hash"] if tail is not None else _GENESIS_HASH
                entry_hash = _entry_hash(prev_hash, record_json)
                conn.execute(
                    "INSERT INTO audit_records "
                    "(audit_id, action_id, timestamp, decision, confirmation_required, "
                    "gate_token_issued, record_json, prev_hash, entry_hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.audit_id,
                        record.action_id,
                        record.timestamp,
                        record.decision,
                        1 if record.confirmation_required else 0,
                        1 if record.gate_token_issued else 0,
                        record_json,
                        prev_hash,
                        entry_hash,
                    ),
                )
                conn.execute("UPDATE audit_anchor SET chain_length=chain_length+1, head_hash=? WHERE id=1", (entry_hash,))
                conn.commit()

            except Exception:
                conn.rollback()
                raise
            return entry_hash

    # ------------------------------------------------------------------
    # Queries (inspectability)
    # ------------------------------------------------------------------

    def count(self) -> int:
        with self._lock:
            conn = self._get_conn()
            row = conn.execute("SELECT COUNT(*) AS n FROM audit_records").fetchone()
            return int(row["n"])

    def get_by_audit_id(self, audit_id: str) -> ActionGateAuditRecord | None:
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT record_json FROM audit_records WHERE audit_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (audit_id,),
            ).fetchone()
        if row is None:
            return None
        return ActionGateAuditRecord.model_validate_json(row["record_json"])

    def get_by_action_id(self, action_id: str) -> list[ActionGateAuditRecord]:
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT record_json FROM audit_records WHERE action_id = ? ORDER BY id ASC",
                (action_id,),
            ).fetchall()
        return [ActionGateAuditRecord.model_validate_json(r["record_json"]) for r in rows]

    def list_recent(self, limit: int = 100) -> list[ActionGateAuditRecord]:
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT record_json FROM audit_records ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [ActionGateAuditRecord.model_validate_json(r["record_json"]) for r in rows]

    # ------------------------------------------------------------------
    # Tamper-evidence
    # ------------------------------------------------------------------

    def _verify_unlocked(self, conn: sqlite3.Connection, anchor: dict | None = None) -> dict:
        rows = conn.execute("SELECT * FROM audit_records ORDER BY id ASC").fetchall()
        local = conn.execute("SELECT chain_length, head_hash FROM audit_anchor WHERE id=1").fetchone()
        head = rows[-1]["entry_hash"] if rows else _GENESIS_HASH

        def result(ok: bool, reason: str, row: int | None = None) -> dict:
            return {"ok": ok, "reason": reason, "first_broken_row": row, "chain_length": len(rows),
                    "head_hash": head, "completion_state": "qualified-draft" if ok else "blocked-escalated",
                    "code": "G6_E_AUDIT_CHAIN_INTACT" if ok else "G6_E_AUDIT_CHAIN_BROKEN"}

        prev = _GENESIS_HASH
        for index, row in enumerate(rows, 1):
            if row["prev_hash"] != prev:
                return result(False, "previous_hash_mismatch", index)
            expected = _entry_hash(prev, row["record_json"])
            if row["entry_hash"] != expected:
                return result(False, "entry_hash_mismatch", index)
            try:
                record = ActionGateAuditRecord.model_validate_json(row["record_json"])
                for name in ("audit_id", "action_id", "timestamp", "decision", "confirmation_required", "gate_token_issued"):
                    if getattr(record, name) != row[name]:
                        return result(False, "index_fields_mismatch", index)
            except (ValueError, TypeError):
                return result(False, "record_invalid", index)
            prev = expected
        if local is None:
            return result(False, "anchor_missing")
        for reference in (dict(local),) + ((anchor,) if anchor is not None else ()):
            if (not isinstance(reference, dict) or set(reference) != {"chain_length", "head_hash"}
                    or type(reference["chain_length"]) is not int or reference["chain_length"] < 0
                    or not isinstance(reference["head_hash"], str) or len(reference["head_hash"]) != 64):
                return result(False, "anchor_invalid")
            if reference["chain_length"] != len(rows):
                return result(False, "chain_length_mismatch", min(reference["chain_length"], len(rows)) + 1)
            if reference["head_hash"] != head:
                # A head alone cannot locate the original edit in a fully rewritten chain.
                return result(False, "head_hash_mismatch")
        return result(True, "chain_intact")

    def verify_chain_report(self, *, anchor: dict | None = None) -> dict:
        """One-based first broken position, or None when an anchor cannot locate the edit.

        An external anchor is an exact snapshot: legitimate later appends also differ.
        Keep it elsewhere. An internal anchor can be rewritten by anyone who can write this DB.
        Legacy logs without anchors refuse; no migration silently blesses their current tail.
        """
        with self._lock:
            try:
                return self._verify_unlocked(self._get_conn(), anchor)
            except Exception:
                return {"ok": False, "reason": "store_unavailable", "first_broken_row": None,
                        "completion_state": "blocked-escalated", "code": "G6_E_AUDIT_CHAIN_BROKEN"}

    def verify_chain(self, *, anchor: dict | None = None) -> bool:
        """Compatibility boolean; inspect verify_chain_report for the first broken row."""
        return self.verify_chain_report(anchor=anchor)["ok"]

    def export_anchor(self) -> dict:
        """Length and head of an intact chain, for the person to retain separately."""
        report = self.verify_chain_report()
        if not report["ok"]:
            raise RuntimeError("[G6_E_AUDIT_CHAIN_BROKEN] " + report["reason"])
        return {"chain_length": report["chain_length"], "head_hash": report["head_hash"]}
