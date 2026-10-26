# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""SQLite-backed durable gate-token ledger: policy decisions + audit records, single-use tokens."""
from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from typing import Any

from g6.core.storage import open_db

# Relative to $G6_HOME (see g6.core.storage); the archive defaulted to the working directory.
_DEFAULT_DB = "action_gating/action_gating.db"


class ActionGatingStore:
    """Durable gate-token ledger."""

    def __init__(self, db_path: str | None = None) -> None:
        db_path = db_path or os.getenv("ACTION_GATING_STORE_PATH") or _DEFAULT_DB
        self._db_path = db_path
        self._conn = open_db(db_path, check_same_thread=False)
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS action_gate_policy_decisions (
                gate_token          TEXT PRIMARY KEY,
                action_capsule_hash TEXT NOT NULL,
                action_capsule_json TEXT NOT NULL,
                decision            TEXT NOT NULL,
                completion_state    TEXT NOT NULL,
                authorized          INTEGER NOT NULL DEFAULT 0,
                reason_codes_json   TEXT NOT NULL DEFAULT '[]',
                audit_id            TEXT NOT NULL,
                created_at          TEXT NOT NULL,
                expires_at          TEXT NOT NULL,
                consumed_at         TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_action_gate_policy_hash
                ON action_gate_policy_decisions(action_capsule_hash);

            CREATE TABLE IF NOT EXISTS action_gate_audit_records (
                audit_id            TEXT PRIMARY KEY,
                gate_token          TEXT NOT NULL,
                action_capsule_hash TEXT NOT NULL,
                audit_record_json   TEXT NOT NULL,
                created_at          TEXT NOT NULL,
                FOREIGN KEY(gate_token)
                    REFERENCES action_gate_policy_decisions(gate_token)
                    ON DELETE CASCADE
            );
            """
        )
        self._conn.commit()

    @staticmethod
    def _now_utc() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)

    def persist_gate_authorization(
        self,
        *,
        gate_token: str,
        action_capsule_hash: str,
        action_capsule: dict,
        decision: dict,
        audit_record: dict,
        expires_at: str,
    ) -> None:
        completion_state = str(decision.get("completion_state") or "")
        decision_name = str(decision.get("decision") or "")
        # Authorised = the policy said allow and nothing escalated (AG-007: not "verified").
        authorized = int(decision_name == "allow" and completion_state != "blocked-escalated")
        audit_id = str(decision.get("audit_id") or audit_record.get("audit_id") or "")
        if not audit_id:
            raise RuntimeError("durable audit id missing")
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO action_gate_policy_decisions (
                    gate_token, action_capsule_hash, action_capsule_json, decision,
                    completion_state, authorized, reason_codes_json, audit_id,
                    created_at, expires_at, consumed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    gate_token,
                    action_capsule_hash,
                    self._json(action_capsule),
                    decision_name,
                    completion_state,
                    authorized,
                    self._json(decision.get("reason_codes") or []),
                    audit_id,
                    self._now_utc(),
                    expires_at,
                ),
            )
            self._conn.execute(
                """
                INSERT INTO action_gate_audit_records (
                    audit_id, gate_token, action_capsule_hash, audit_record_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    audit_id,
                    gate_token,
                    action_capsule_hash,
                    self._json(audit_record),
                    self._now_utc(),
                ),
            )

    def load_gate_authorization(self, gate_token: str) -> dict | None:
        row = self._conn.execute(
            """
            SELECT p.gate_token, p.action_capsule_hash, p.decision,
                   p.completion_state, p.authorized, p.reason_codes_json,
                   p.audit_id, p.expires_at, p.consumed_at, a.audit_record_json
            FROM action_gate_policy_decisions p
            LEFT JOIN action_gate_audit_records a ON a.gate_token = p.gate_token
            WHERE p.gate_token=?
            """,
            (gate_token,),
        ).fetchone()
        if row is None:
            return None
        return {
            "gate_token": row[0],
            "action_capsule_hash": row[1],
            "decision": row[2],
            "completion_state": row[3],
            "authorized": bool(row[4]),
            "reason_codes": json.loads(row[5] or "[]"),
            "audit_id": row[6],
            "expires_at": row[7],
            "consumed_at": row[8],
            "audit_record": json.loads(row[9]) if row[9] else None,
        }

    def consume_gate_token(self, gate_token: str) -> bool:
        with self._conn:
            cur = self._conn.execute(
                """
                UPDATE action_gate_policy_decisions
                SET consumed_at=?
                WHERE gate_token=? AND consumed_at IS NULL
                """,
                (self._now_utc(), gate_token),
        )
        return cur.rowcount == 1


__all__ = ["ActionGatingStore"]
