# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The owner-scoped store the workforce lives in: ventures (a named workspace an owner holds) and their append-only
evidence (vendored from the archive's ``venture.store``).

The ventures table is owner-scoped; evidence is keyed by venture id alone, so a caller holds an ``OwnerScope``
(``for_owner``), which resolves every venture id through its owner first. The archive's authority envelope, revenue,
inbox, monthly budget and work-loop tables belong to the venture operator loop and are not ported (audit 011).
The default location is ``$G6_HOME/workforce/venture.db`` (``g6.core.storage``).
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from g6.core.storage import open_db, state_path

_DDL = """
CREATE TABLE IF NOT EXISTS ventures (
    venture_id           TEXT PRIMARY KEY,
    owner                TEXT NOT NULL,
    name                 TEXT NOT NULL DEFAULT '',
    goal_id              TEXT NOT NULL DEFAULT '',
    stage                TEXT NOT NULL DEFAULT 'A',
    budget_aud_month     REAL NOT NULL DEFAULT 0.0,
    model_cap_usd_month  REAL NOT NULL DEFAULT 0.0,
    task_cap_usd         REAL NOT NULL DEFAULT 0.0,
    max_turns            INTEGER NOT NULL DEFAULT 20,
    runway_rule_json     TEXT NOT NULL DEFAULT '{}',
    status               TEXT NOT NULL DEFAULT 'active',
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_v_owner ON ventures(owner);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id          TEXT PRIMARY KEY,
    venture_id           TEXT NOT NULL,
    kind                 TEXT NOT NULL,
    ref                  TEXT NOT NULL DEFAULT '',
    detail_json          TEXT NOT NULL DEFAULT '{}',
    seq                  INTEGER NOT NULL,
    at                   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ev_venture ON evidence(venture_id, seq);

"""

#: Evidence is the venture's history, so it is written once and never rewritten: a retried write that
#: carries an idempotency key lands once (workforce OS plan T2), and no row is updated or deleted. Run
#: after the additive ``idem_key`` column exists.
_EVIDENCE_RULES = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_ev_idem ON evidence(venture_id, idem_key) WHERE idem_key <> '';
CREATE TRIGGER IF NOT EXISTS evidence_no_update BEFORE UPDATE ON evidence
BEGIN SELECT RAISE(ABORT, 'evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evidence_no_delete BEFORE DELETE ON evidence
BEGIN SELECT RAISE(ABORT, 'evidence is append-only'); END;
-- INSERT OR REPLACE deletes the old row without firing a delete trigger (recursive_triggers is off by
-- default), so a conflicting insert is refused before conflict resolution runs (Codex review of T2).
CREATE TRIGGER IF NOT EXISTS evidence_no_replace BEFORE INSERT ON evidence
WHEN EXISTS (SELECT 1 FROM evidence WHERE evidence_id = NEW.evidence_id)
  OR (NEW.idem_key <> '' AND EXISTS (SELECT 1 FROM evidence WHERE venture_id = NEW.venture_id
                                      AND idem_key = NEW.idem_key))
BEGIN SELECT RAISE(ABORT, 'evidence is append-only'); END;
"""


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def default_db_path() -> Path:
    """The one venture.db: ``$G6_HOME/workforce/venture.db``."""
    return state_path("workforce", "venture.db")


class VentureStore:
    def __init__(self, db_path: str | Path) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection = open_db(path.resolve(), check_same_thread=False)
        self._conn.executescript(_DDL)
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(evidence)")}
        if "idem_key" not in cols:  # workforce OS T2: a retried write is recorded once
            with self._conn:
                self._conn.execute("ALTER TABLE evidence ADD COLUMN idem_key TEXT NOT NULL DEFAULT ''")
        self._conn.executescript(_EVIDENCE_RULES)

    @classmethod
    def reader(cls, db_path: str | Path) -> VentureStore:
        """An existing store opened for a reader that must not write (E2E Build 7 review I1): the same connection,
        ``query_only`` set as soon as it opens (before any read), and no schema statement, so any write raises."""
        path = Path(db_path)
        if not path.is_file():
            raise FileNotFoundError(f"no venture db at {path}")
        found = cls.__new__(cls)
        found._conn = open_db(path.resolve(), check_same_thread=False)
        found._conn.execute("PRAGMA query_only=ON")
        return found

    @property
    def conn(self) -> sqlite3.Connection:
        """The shared connection: sibling tables (the approval queue) live in the same file."""
        return self._conn

    def for_owner(self, owner: str) -> OwnerScope:
        """This store as ``owner`` sees it: the only handle a customer-facing caller (MCP) holds."""
        return OwnerScope(self, owner)

    # -- ventures --------------------------------------------------------------
    def create_venture(self, owner: str, name: str, *, budget_aud_month: float,
                       model_cap_usd_month: float, task_cap_usd: float, max_turns: int,
                       stage: str = "A", goal_id: str = "", runway_rule: dict | None = None) -> dict:
        vid = _uid()
        now = _now()
        with self._conn:
            self._conn.execute(
                "INSERT INTO ventures (venture_id, owner, name, goal_id, stage, budget_aud_month,"
                " model_cap_usd_month, task_cap_usd, max_turns, runway_rule_json, status,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,'active',?,?)",
                (vid, owner, name, goal_id, stage, float(budget_aud_month),
                 float(model_cap_usd_month), float(task_cap_usd), int(max_turns),
                 json.dumps(runway_rule or {}), now, now),
            )
        return self.get_venture(owner, vid)

    def get_venture(self, owner: str, venture_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM ventures WHERE venture_id=? AND owner=?", (venture_id, owner)
        ).fetchone()
        return self._venture(row) if row else None

    def list_ventures(self, owner: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM ventures WHERE owner=? ORDER BY created_at", (owner,)
        ).fetchall()
        return [self._venture(r) for r in rows]

    def set_status(self, owner: str, venture_id: str, status: str) -> None:
        with self._conn:
            cur = self._conn.execute(
                "UPDATE ventures SET status=?, updated_at=? WHERE venture_id=? AND owner=?",
                (status, _now(), venture_id, owner),
            )
        if cur.rowcount == 0:
            raise LookupError(f"no venture {venture_id!r} for owner {owner!r}")

    @staticmethod
    def _venture(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["runway_rule"] = json.loads(d.pop("runway_rule_json") or "{}")
        return d

    # -- evidence (append only) ---------------------------------------------------
    def record_evidence(self, venture_id: str, kind: str, *, ref: str = "",
                        detail: dict | None = None, idem_key: str = "") -> str:
        """Append one row and return its id. With ``idem_key``, a second write with the same key for the
        same venture records nothing and returns the first row's id."""
        if idem_key:
            existing = self._idem_row(venture_id, idem_key)
            if existing:
                return existing
        try:
            with self._conn:
                return self.append_evidence(venture_id, kind, ref=ref, detail=detail, idem_key=idem_key)
        except sqlite3.IntegrityError:
            existing = self._idem_row(venture_id, idem_key) if idem_key else ""
            if not existing:
                raise
            return existing  # a concurrent writer recorded the same key first

    def append_evidence(self, venture_id: str, kind: str, *, ref: str = "", detail: dict | None = None,
                        idem_key: str = "") -> str:
        """Insert one row INSIDE THE CALLER'S TRANSACTION (no commit here), so a state change and its
        history commit together or not at all. A repeated ``idem_key`` raises ``sqlite3.IntegrityError``."""
        eid = _uid()
        seq = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM evidence WHERE venture_id=?", (venture_id,)
        ).fetchone()[0]
        self._conn.execute(
            "INSERT INTO evidence (evidence_id, venture_id, kind, ref, detail_json, seq, at, idem_key)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (eid, venture_id, kind, ref, json.dumps(detail or {}, default=str), seq, _now(), idem_key),
        )
        return eid

    def _idem_row(self, venture_id: str, idem_key: str) -> str:
        row = self._conn.execute(
            "SELECT evidence_id FROM evidence WHERE venture_id=? AND idem_key=?", (venture_id, idem_key)
        ).fetchone()
        return row[0] if row else ""

    def evidence(self, venture_id: str, *, kind: str | None = None, limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM evidence WHERE venture_id=?"
        args: list = [venture_id]
        if kind is not None:
            sql += " AND kind=?"
            args.append(kind)
        sql += " ORDER BY seq DESC LIMIT ?"
        args.append(int(limit))
        out = []
        for row in self._conn.execute(sql, args).fetchall():
            d = dict(row)
            d["detail"] = json.loads(d.pop("detail_json") or "{}")
            out.append(d)
        return out


#: One refusal for "no such venture" and "another owner's venture": it never says which, and never
#: echoes the id, so a caller cannot probe for ventures that exist.
NOT_FOUND = "no such venture for this owner"


class OwnerScope:
    """One owner's view of a VentureStore (Part 5 A1). Every venture id resolves through
    ``WHERE venture_id=? AND owner=?`` before anything is read or written, and an id the owner
    does not hold raises ``LookupError(NOT_FOUND)``."""

    def __init__(self, vstore: VentureStore, owner: str) -> None:
        if not owner:
            raise ValueError("an owner scope needs an owner")
        self._vstore = vstore
        self.owner = owner

    def resolve(self, venture_id: str) -> str:
        row = self._vstore.conn.execute(
            "SELECT venture_id FROM ventures WHERE venture_id=? AND owner=?", (str(venture_id), self.owner)
        ).fetchone()
        if row is None:
            raise LookupError(NOT_FOUND)
        return row["venture_id"]

    # -- ventures --------------------------------------------------------------
    def create_venture(self, name: str, **caps) -> dict:
        return self._vstore.create_venture(self.owner, name, **caps)

    def list_ventures(self) -> list[dict]:
        return self._vstore.list_ventures(self.owner)

    def venture(self, venture_id: str) -> dict:
        return self._vstore.get_venture(self.owner, self.resolve(venture_id))

    def set_status(self, venture_id: str, status: str) -> None:
        self._vstore.set_status(self.owner, self.resolve(venture_id), status)

    # -- per-venture evidence ---------------------------------------------------------
    def evidence(self, venture_id: str, *, kind: str | None = None, limit: int = 100) -> list[dict]:
        return self._vstore.evidence(self.resolve(venture_id), kind=kind, limit=limit)

    # -- per-venture writes (Part 5 A2): the same resolve-first rule; the MCP tools write only through these --
    def record_evidence(self, venture_id: str, kind: str, *, ref: str = "", detail: dict | None = None,
                        idem_key: str = "") -> str:
        return self._vstore.record_evidence(self.resolve(venture_id), kind, ref=ref, detail=detail,
                                            idem_key=idem_key)



def unscoped(source: VentureStore | OwnerScope) -> tuple[VentureStore, OwnerScope | None]:
    """(the store, the scope or None) for the per-venture accessors that take either."""
    if isinstance(source, OwnerScope):
        return source._vstore, source  # the scope's own module
    return source, None
