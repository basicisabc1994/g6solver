# SPDX-License-Identifier: Apache-2.0
"""The operator's durable state: owner-scoped ventures, append-only evidence and a monthly spend ledger.

One SQLite file, ``$G6_HOME/operator.db`` by default (``g6.core.storage.open_db``). A caller acting for an owner holds
an :class:`OwnerScope`: every venture id resolves through ``WHERE venture_id=? AND owner=?`` first, and an id the owner
does not hold gets the same refusal as one that does not exist.

Evidence, the ledger and the queue's decisions are append-only: database triggers refuse UPDATE, DELETE and an
``INSERT OR REPLACE`` over an existing row. A reservation is checked against the caps and written in one
``BEGIN IMMEDIATE`` transaction, so two tickers on the same file cannot both pass the cap, and the ledger is keyed by
month (the archive kept a lifetime ledger beside a month counter it charged after the run: audit 011, VEN-005).
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from g6.core.storage import open_db
from g6.operator import gates

NOT_FOUND = "no such venture for this owner"
STATUSES = frozenset({"active", "paused", "stopped"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ventures (venture_id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
    caps_json TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence (seq INTEGER PRIMARY KEY AUTOINCREMENT, venture_id TEXT NOT NULL,
    kind TEXT NOT NULL, ref TEXT NOT NULL, detail_json TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ledger (seq INTEGER PRIMARY KEY AUTOINCREMENT, venture_id TEXT NOT NULL,
    month TEXT NOT NULL, account TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('reserve', 'release', 'charge')),
    amount REAL NOT NULL CHECK (amount >= 0), ref TEXT NOT NULL, at TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS ledger_once BEFORE INSERT ON ledger WHEN NEW.kind <> 'charge'
    AND EXISTS (SELECT 1 FROM ledger WHERE ref = NEW.ref AND kind = NEW.kind)
BEGIN SELECT RAISE(ABORT, 'a reservation is made once and released once'); END;
CREATE TABLE IF NOT EXISTS week_books (owner TEXT NOT NULL, provider TEXT NOT NULL, week TEXT NOT NULL, book TEXT NOT NULL,
    PRIMARY KEY (owner, provider, week));
CREATE TABLE IF NOT EXISTS week_grids (owner TEXT NOT NULL, provider TEXT NOT NULL, reset TEXT NOT NULL,
    PRIMARY KEY (owner, provider));
CREATE TABLE IF NOT EXISTS reading_baselines (owner TEXT NOT NULL, provider TEXT NOT NULL, book TEXT NOT NULL,
    read_at TEXT NOT NULL, charged REAL NOT NULL, PRIMARY KEY (owner, provider, book, read_at));
"""


def append_only(table: str) -> str:
    """Triggers that refuse UPDATE, DELETE and a REPLACE over an existing ``seq`` on ``table``."""
    return f"""
CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table}
BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;
CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table}
BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;
CREATE TRIGGER IF NOT EXISTS {table}_no_replace BEFORE INSERT ON {table}
WHEN EXISTS (SELECT 1 FROM {table} WHERE seq = NEW.seq)
BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class BudgetRefused(RuntimeError):
    """The spend may not start: the venture is stopped, its budget is unknown, or the cap would be exceeded."""


class OperatorStore:
    """``path`` defaults to ``$G6_HOME/operator.db``; ``":memory:"`` keeps nothing past the process."""

    def __init__(self, path: str | os.PathLike[str] = "operator.db") -> None:
        self.conn = open_db(path)
        self.conn.executescript(_SCHEMA + append_only("evidence") + append_only("ledger"))

    def for_owner(self, owner: str) -> OwnerScope:
        return OwnerScope(self, owner)

    def close(self) -> None:
        self.conn.close()


class OwnerScope:
    """One owner's view of the store: the only handle the queue and the tick hold."""

    def __init__(self, store: OperatorStore, owner: str) -> None:
        if not str(owner or "").strip():
            raise ValueError("an owner scope needs an owner")
        self.store, self.owner, self.conn = store, owner, store.conn

    def resolve(self, venture_id: str) -> str:
        row = self.conn.execute("SELECT venture_id FROM ventures WHERE venture_id=? AND owner=?",
                                (str(venture_id), self.owner)).fetchone()
        if row is None:
            raise LookupError(NOT_FOUND)
        return row["venture_id"]

    # -- ventures -------------------------------------------------------------------------
    def create_venture(self, name: str, *, caps: dict[str, float]) -> dict[str, Any]:
        """``caps`` gives each account in ``gates.ACCOUNTS`` a finite, non-negative monthly budget (0 = none may be
        spent). A missing or invalid cap is refused here, and would stop the venture if it reached the stop check."""
        with self.conn:
            venture_id = self._insert_venture(name, caps)
        return self.venture(venture_id)

    def _insert_venture(self, name: str, caps: dict[str, float]) -> str:
        """Inside the caller's transaction: the venture's row and its history, and its id."""
        bad = [a for a in gates.ACCOUNTS if gates.cap_of(caps, a) is None]
        if bad:
            raise ValueError(f"caps must give a finite, non-negative monthly budget for {bad}")
        venture_id = uuid.uuid4().hex[:12]
        clean = {a: gates.cap_of(caps, a) for a in gates.ACCOUNTS}
        self.conn.execute("INSERT INTO ventures VALUES (?, ?, ?, ?, 'active', ?)",
                          (venture_id, self.owner, name, json.dumps(clean), _now()))
        self._append_evidence(venture_id, "created", "", {"name": name, "caps": clean})
        return venture_id

    def has_venture(self, name: str) -> bool:
        return self._named(name) is not None

    def _named(self, name: str) -> str | None:
        row = self.conn.execute("SELECT venture_id FROM ventures WHERE owner=? AND name=?", (self.owner, name)).fetchone()
        return row["venture_id"] if row is not None else None

    def _book_of(self, provider: str, week: str) -> str | None:
        row = self.conn.execute("SELECT book FROM week_books WHERE owner=? AND provider=? AND week=?",
                                (self.owner, provider, week)).fetchone()
        return row["book"] if row is not None else None

    def week_grid(self, provider: str) -> str | None:
        """A moment one of the provider's weeks ended, as it last reported, or ``None`` before it has reported one."""
        row = self.conn.execute("SELECT reset FROM week_grids WHERE owner=? AND provider=?", (self.owner, provider)).fetchone()
        return row["reset"] if row is not None else None

    def reading_baseline(self, provider: str, book: str, read_at: str, venture_id: str, account: str) -> float:
        """What the ledger had charged under ``book`` when the account reading taken at ``read_at`` first reached a meter: the
        first word stands, so every meter, in any process, measures what was charged since the reading from the same point.
        The charge is summed in the transaction that keeps it, so a settlement on another connection cannot fall between."""
        vid = self.resolve(venture_id)
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            charged = self.conn.execute("SELECT COALESCE(SUM(amount), 0) AS charged FROM ledger WHERE venture_id=? AND month=?"
                                        " AND account=? AND kind='charge'", (vid, book, account)).fetchone()["charged"]
            self.conn.execute("INSERT OR IGNORE INTO reading_baselines VALUES (?, ?, ?, ?, ?)",
                              (self.owner, provider, book, read_at, float(charged)))
            row = self.conn.execute("SELECT charged FROM reading_baselines WHERE owner=? AND provider=? AND book=? AND read_at=?",
                                    (self.owner, provider, book, read_at)).fetchone()
            self.conn.commit()
            return float(row["charged"])
        except BaseException:
            self.conn.rollback()
            raise

    def week_ledger(self, provider: str, week: str, *, caps: dict[str, float], moved_from: str = "",
                    grid: str = "") -> tuple[str, str]:
        """The venture a provider's ``week`` is charged under and its book, the month its rows carry, decided in one
        transaction so that what one process decides every other reads, and never decided twice: a week booked under its own
        start has a venture of that name, and one booked under another has a row here, the first word standing.

        A week goes on its own book, unless its end moved while ``moved_from`` (the week it was counted in until then) was
        open: then it takes that week's book, so what was charged there still counts, through any number of moves. Not when a
        venture of its own already exists, as a process has charged it under its own book; nor when the book last went to a
        week other than the one this moved from, as what is charged there is that week's, which this one did not make. ``grid``: a moment one of the provider's weeks ends, kept
        (the latest standing) in this same transaction, so a move is never kept without the book it gave its week."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            if grid:
                self.conn.execute("INSERT OR REPLACE INTO week_grids VALUES (?, ?, ?)", (self.owner, provider, grid))
            book, own = self._book_of(provider, week), self._own(provider, week)
            if book is None and own is None and moved_from and moved_from != week:
                source = self._book_of(provider, moved_from) or self._own_book(provider, moved_from)
                last = self.conn.execute("SELECT week FROM week_books WHERE owner=? AND provider=? AND book=? ORDER BY rowid DESC "
                                         "LIMIT 1", (self.owner, provider, source)).fetchone()
                if last is None or last["week"] == moved_from:     # the book is where this week moved from, however often
                    # it moved: a week the book went to last is the one moving on; any other holding it is another week
                    self.conn.execute("INSERT INTO week_books VALUES (?, ?, ?, ?)", (self.owner, provider, week, source))
                    book = source
            if book is None:
                found, book = own if own is not None else (None, week)
            else:
                found = self._named(f"{provider}@{book}")
            found = found or self._insert_venture(f"{provider}@{book}", caps)
            self.conn.commit()
            return found, book
        except BaseException:
            self.conn.rollback()
            raise

    def _own(self, provider: str, week: str) -> tuple[str, str] | None:
        """Inside the caller's transaction: the venture named for ``week``'s own start and the book it carries, under the UTC
        name or, from a folder enrolled before weeks were named in UTC, under the same instant in another zone (its rows carry
        that rendering, so it stays the book); else ``None``. A week's own venture is never a row of ``week_books``, so a
        week that moves on from it can be followed by another move."""
        found = self._named(f"{provider}@{week}")
        return (found, week) if found is not None else self._same_moment(provider, week)

    def _own_book(self, provider: str, week: str) -> str:
        own = self._own(provider, week)
        return own[1] if own is not None else week

    def _same_moment(self, provider: str, book: str) -> tuple[str, str] | None:
        """Inside the caller's transaction: a venture of ``provider`` named for the moment ``book`` names, rendered in another
        zone, as a folder enrolled before weeks were named in UTC holds one, and that rendering; else ``None``."""
        try:
            moment = datetime.fromisoformat(book)
        except ValueError:
            return None
        if moment.tzinfo is None:
            return None
        prefix = f"{provider}@"
        rows = self.conn.execute("SELECT venture_id, name FROM ventures WHERE owner=? AND substr(name, 1, ?) = ? ORDER BY rowid",
                                 (self.owner, len(prefix), prefix)).fetchall()
        for row in rows:
            rendering = row["name"][len(prefix):]
            try:
                other = datetime.fromisoformat(rendering)
            except ValueError:
                continue
            if other.tzinfo is not None and other == moment:
                return row["venture_id"], rendering
        return None

    def venture_named(self, name: str, *, caps: dict[str, float]) -> str:
        """The id of this owner's venture of this name, made once however many ask at the same moment."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute("SELECT venture_id FROM ventures WHERE owner=? AND name=?",
                                    (self.owner, name)).fetchone()
            found = row["venture_id"] if row is not None else self.create_venture(name, caps=caps)["venture_id"]
            self.conn.commit()
            return found
        except BaseException:
            self.conn.rollback()
            raise

    def venture(self, venture_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM ventures WHERE venture_id=? AND owner=?",
                                (str(venture_id), self.owner)).fetchone()
        if row is None:
            raise LookupError(NOT_FOUND)
        out = dict(row)
        out["caps"] = json.loads(out.pop("caps_json"))
        return out

    def list_ventures(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT venture_id FROM ventures WHERE owner=? ORDER BY created_at", (self.owner,))
        return [self.venture(r["venture_id"]) for r in rows.fetchall()]

    def set_status(self, venture_id: str, status: str, *, by: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {sorted(STATUSES)}")
        vid = self.resolve(venture_id)
        with self.conn:
            self.conn.execute("UPDATE ventures SET status=? WHERE venture_id=?", (status, vid))
            self._append_evidence(vid, "status", by, {"status": status})

    # -- evidence (append-only) -----------------------------------------------------------
    def record(self, venture_id: str, kind: str, *, ref: str = "", detail: dict[str, Any] | None = None) -> int:
        vid = self.resolve(venture_id)
        with self.conn:
            return self._append_evidence(vid, kind, ref, detail)

    def evidence(self, venture_id: str, *, kind: str | None = None, ref: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM evidence WHERE venture_id=?", [self.resolve(venture_id)]
        for column, value in (("kind", kind), ("ref", ref)):
            if value is not None:
                sql += f" AND {column}=?"
                args.append(value)
        rows = self.conn.execute(sql + " ORDER BY seq", args).fetchall()
        return [{**{k: r[k] for k in ("seq", "kind", "ref", "at")}, "detail": json.loads(r["detail_json"])}
                for r in rows]

    def _append_evidence(self, vid: str, kind: str, ref: str, detail: dict[str, Any] | None) -> int:
        """Inside the caller's transaction: a state change and its history commit together or not at all."""
        cur = self.conn.execute("INSERT INTO evidence (venture_id, kind, ref, detail_json, at) VALUES (?, ?, ?, ?, ?)",
                                (vid, kind, ref, json.dumps(detail or {}, default=str), _now()))
        return int(cur.lastrowid)

    # -- the monthly ledger ---------------------------------------------------------------
    def committed(self, venture_id: str, month: str) -> dict[str, float]:
        """This month's charges plus open reservations, per account."""
        return self._committed(self.resolve(venture_id), month)

    def _committed(self, vid: str, month: str) -> dict[str, float]:
        rows = self.conn.execute("SELECT account, SUM(CASE kind WHEN 'release' THEN -amount ELSE amount END) AS used"
                                 " FROM ledger WHERE venture_id=? AND month=? GROUP BY account", (vid, month))
        return {r["account"]: float(r["used"] or 0.0) for r in rows.fetchall()}

    def outstanding(self, venture_id: str, month: str) -> dict[str, float]:
        """What is reserved and not yet settled, per account: the part of ``committed`` nothing has charged."""
        return self._outstanding(self.resolve(venture_id), month)

    def _outstanding(self, vid: str, month: str) -> dict[str, float]:
        rows = self.conn.execute("SELECT account, SUM(CASE kind WHEN 'reserve' THEN amount WHEN 'release' THEN -amount"
                                 " ELSE 0 END) AS pending FROM ledger WHERE venture_id=? AND month=? GROUP BY account",
                                 (vid, month))
        return {r["account"]: float(r["pending"] or 0.0) for r in rows.fetchall()}

    def _append_ledger(self, vid: str, month: str, account: str, kind: str, amount: float, ref: str) -> None:
        self.conn.execute("INSERT INTO ledger (venture_id, month, account, kind, amount, ref, at)"
                          " VALUES (?, ?, ?, ?, ?, ?, ?)", (vid, month, account, kind, float(amount), ref, _now()))

    def reserve(self, venture_id: str, *, month: str, account: str, amount: float,
                admit: Callable[[float, float], str] | None = None) -> str:
        """Reserve ``amount`` before a spend starts; the stop check and the write are one transaction. ``admit`` is a
        further check inside it, given this account's committed and outstanding amounts as they stand under the lock,
        and returns why not, or nothing."""
        vid = self.resolve(venture_id)
        value = gates.amount_of(amount)
        if value is None:
            raise BudgetRefused(f"a reservation must be a finite, positive amount, got {amount!r}")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            why = "" if admit is None else admit(self._committed(vid, month).get(account, 0.0),
                                                 self._outstanding(vid, month).get(account, 0.0))
            why = why or gates.spend_refusal(self.venture(vid), self._committed(vid, month), account, value)
            if why:
                raise BudgetRefused(why)
            reservation = uuid.uuid4().hex
            self._append_ledger(vid, month, account, "reserve", value, reservation)
            self.conn.commit()
            return reservation
        except BaseException:
            self.conn.rollback()
            raise

    def settle(self, venture_id: str, reservation: str, *, reported: float | None = None,
               metered: float | None = None) -> float:
        """Release ``reservation`` and charge it: the full reservation, or ``reported`` when that is higher. What a
        run reports can raise its charge, never lower it, so an executor that reports 0 still spends its reservation
        (the archive recorded local and customer-client runs as 0, so their cap never tripped: VEN-004).

        ``metered`` is different: the host's own count of what the run used, taken where the run cannot touch it. When
        given it is the charge, up or down, and ``reported`` is not read."""
        if metered is not None and (isinstance(metered, bool) or not isinstance(metered, int | float)
                                    or not math.isfinite(metered) or metered < 0):
            raise ValueError(f"a metered amount must be finite and not negative, got {metered!r}")
        vid = self.resolve(venture_id)
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute("SELECT month, account, amount FROM ledger WHERE venture_id=? AND ref=?"
                                    " AND kind='reserve'", (vid, reservation)).fetchone()
            if row is None:
                raise LookupError("no such reservation for this venture")
            charged = float(row["amount"])
            reported_value = gates.amount_of(reported)
            if metered is not None:
                charged = float(metered)
            elif reported_value is not None and reported_value > charged:
                charged = reported_value
            self._append_ledger(vid, row["month"], row["account"], "release", row["amount"], reservation)
            self._append_ledger(vid, row["month"], row["account"], "charge", charged, reservation)
            self.conn.commit()
            return charged
        except sqlite3.IntegrityError as exc:
            self.conn.rollback()
            raise ValueError("this reservation is already settled") from exc
        except BaseException:
            self.conn.rollback()
            raise
