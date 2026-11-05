# SPDX-License-Identifier: Apache-2.0
"""The operator's durable state: owner-scoped ventures, append-only evidence and a monthly spend ledger.

One SQLite file, ``$G6_HOME/operator.db`` by default (``g6.core.storage.open_db``). A caller acting for an owner holds
an :class:`OwnerScope`: every venture id resolves through ``WHERE venture_id=? AND owner=?`` first, and an id the owner
does not hold gets the same refusal as one that does not exist.

Evidence, the ledger and the queue's decisions are append-only: database triggers refuse UPDATE, DELETE and an
``INSERT OR REPLACE`` over an existing row. A reservation is checked against the caps and written in one
``BEGIN IMMEDIATE`` transaction, so two tickers on the same file cannot both pass the cap, and the ledger is keyed by
month (the archive kept a lifetime ledger beside a month counter it charged after the run: audit 011, VEN-005).

A provider's weeks (``g6.self_maintenance.pacing``) are kept as spans. Each reservation made on them carries, beside its
ledger row and in the same transaction, its start, the start of the provider week it was made under and that week's end,
as instants in UTC. Its settlement is dated by the store's own clock as it is written, never before the reservation
began. It is filed under the week it was made in, for good: nothing moves between weeks when a provider says its weeks
fall elsewhere, and a reading counts every reservation whose span, from its start to its settlement or, while open, on
without end, overlaps the week it reads (``week_held``).

Every instant that enters this module goes through ``utc`` first, so two renderings of one instant are one instant: two
times in one zone compare by their wall clocks, which repeat in the hour a clock goes back.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
CREATE INDEX IF NOT EXISTS ledger_by_ref ON ledger (ref, kind);
CREATE TRIGGER IF NOT EXISTS ledger_once BEFORE INSERT ON ledger WHEN NEW.kind <> 'charge'
    AND EXISTS (SELECT 1 FROM ledger WHERE ref = NEW.ref AND kind = NEW.kind)
BEGIN SELECT RAISE(ABORT, 'a reservation is made once and released once'); END;
CREATE TABLE IF NOT EXISTS week_grids (owner TEXT NOT NULL, provider TEXT NOT NULL, reset TEXT NOT NULL,
    PRIMARY KEY (owner, provider));
CREATE TABLE IF NOT EXISTS week_spans (ref TEXT PRIMARY KEY, owner TEXT NOT NULL, provider TEXT NOT NULL,
    week_us INTEGER NOT NULL, start_us INTEGER NOT NULL, end_us INTEGER NOT NULL,
    CHECK (typeof(week_us) = 'integer' AND typeof(start_us) = 'integer' AND typeof(end_us) = 'integer'
           AND start_us < end_us));
CREATE INDEX IF NOT EXISTS week_spans_by_start ON week_spans (owner, provider, start_us);
CREATE TABLE IF NOT EXISTS week_settled (ref TEXT PRIMARY KEY, at_us INTEGER NOT NULL CHECK (typeof(at_us) = 'integer'));
CREATE TRIGGER IF NOT EXISTS week_settled_after_start BEFORE INSERT ON week_settled
    WHEN NEW.at_us < (SELECT start_us FROM week_spans WHERE ref = NEW.ref)
BEGIN SELECT RAISE(ABORT, 'a settlement cannot end before its reservation began'); END;
"""
#: What the book code before week spans kept, read once into spans (``OwnerScope.adopt_books``) and never written again.
_OLD_MOVES = ("week_books", "book_holders")
_MICROSECOND = timedelta(microseconds=1)
_WEEK = timedelta(days=7)


def utc(moment: Any) -> Any:
    """The one way an instant enters the loop's budget (this module, ``pacing`` and ``usage``): ``moment`` as the same
    instant in UTC, so every comparison and every stored value is of instants, never of wall clocks. ``None`` stays
    ``None``; a time with no zone is a ``ValueError``, as a naive time reads differently on another machine."""
    if moment is None:
        return None
    if not isinstance(moment, datetime):
        raise TypeError(f"{moment!r} is not a moment")
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("a moment needs its zone: a naive time reads differently on another machine")
    return moment.astimezone(UTC)


_EPOCH = utc(datetime(1970, 1, 1, tzinfo=UTC))


def append_only(table: str, key: str = "seq") -> str:
    """Triggers that refuse UPDATE, DELETE and a REPLACE over an existing ``key`` on ``table``."""
    return f"""
CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table}
BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;
CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table}
BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;
CREATE TRIGGER IF NOT EXISTS {table}_no_replace BEFORE INSERT ON {table}
WHEN EXISTS (SELECT 1 FROM {table} WHERE {key} = NEW.{key})
BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;
"""


def _now() -> str:
    return utc(datetime.now(UTC)).isoformat(timespec="seconds")


def _wall_clock() -> datetime:
    return utc(datetime.now(UTC))


def _us(moment: datetime) -> int:
    """A moment as whole microseconds since 1970 in UTC: one instant has one number, in whatever zone it was written."""
    moment = utc(moment)
    return (moment - _EPOCH) // _MICROSECOND


def _moment(us: int) -> datetime:
    return _EPOCH + us * _MICROSECOND


def _instant(text: Any) -> datetime:
    """A moment the store wrote as text, which must carry its zone; a ``ValueError`` when it does not read as one."""
    if not isinstance(text, str):
        raise ValueError(f"{text!r} is not a moment")
    return utc(datetime.fromisoformat(text))


def _week_name(week: datetime) -> str:
    """The month a provider week's ledger rows carry, and its venture's name after the provider: its start in UTC."""
    week = utc(week)
    return _moment(_us(week)).isoformat()


@dataclass(frozen=True)
class Held:
    """One reservation on a provider's weeks as it stands: what it reserved; what it was charged, once settled; and when it
    was settled. ``settled`` is ``None`` while it is open, and for a charge the book code made with no time of its own."""
    amount: float
    charged: float | None = None
    settled: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "settled", utc(self.settled))


class BudgetRefused(RuntimeError):
    """The spend may not start: the venture is stopped, its budget is unknown, or the cap would be exceeded."""


class OperatorStore:
    """``path`` defaults to ``$G6_HOME/operator.db``; ``":memory:"`` keeps nothing past the process. ``clock``: the store's
    own clock, which dates a settlement as it is written; the wall clock unless the owner of the store runs on another,
    as a loop whose ticks run on a clock of their own does."""

    def __init__(self, path: str | os.PathLike[str] = "operator.db", *,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.conn = open_db(path)
        self.clock = clock or _wall_clock
        self._depth = 0                       # how deep this connection is inside ``OwnerScope.immediate``
        self.conn.executescript(_SCHEMA + append_only("evidence") + append_only("ledger")
                                + append_only("week_spans", "ref") + append_only("week_settled", "ref"))

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

    # -- a provider's weeks ---------------------------------------------------------------
    def week_grid(self, provider: str) -> datetime | None:
        """A moment one of the provider's weeks ended, as it last reported, or ``None`` before it has reported one. A row that
        does not read as a moment with its zone is a ``ValueError``: where its weeks fall is then not known."""
        row = self.conn.execute("SELECT reset FROM week_grids WHERE owner=? AND provider=?", (self.owner, provider)).fetchone()
        return _instant(row["reset"]) if row is not None else None

    @contextmanager
    def immediate(self) -> Iterator[None]:
        """One ``BEGIN IMMEDIATE`` transaction: what is read inside it is what a write inside it is checked against, as no
        other connection can write until it ends. A use inside one already open joins it, so a store method that writes
        on its own can also be one step of a larger check."""
        if self.store._depth:
            self.store._depth += 1
            try:
                yield
            finally:
                self.store._depth -= 1
            return
        self.conn.execute("BEGIN IMMEDIATE")
        self.store._depth = 1
        try:
            yield
        except BaseException:
            self.store._depth = 0
            self.conn.rollback()
            raise
        self.store._depth = 0
        self.conn.commit()

    def keep_grid(self, provider: str, reset: datetime) -> None:
        """Where the provider's weeks fall, as it now says: a moment one of them ends. The latest word stands. It moves the
        weeks a reading looks at, and no reservation."""
        reset = utc(reset)
        with self.immediate():
            self.conn.execute("INSERT OR REPLACE INTO week_grids VALUES (?, ?, ?)", (self.owner, provider, _week_name(reset)))

    def week_venture(self, provider: str, week: datetime) -> str:
        """The venture the reservations made in the provider week that began at ``week`` are filed under, or ``""`` before
        one is made."""
        week = utc(week)
        return self._named(f"{provider}@{_week_name(week)}") or ""

    def week_held(self, provider: str, account: str, start: datetime, end: datetime) -> list[Held]:
        """Every reservation of ``provider`` on ``account`` whose span overlaps [``start``, ``end``), as it stands, in one
        read, so a settlement on another connection is all in it or not at all.

        A reservation's span runs from its start to when it was settled. While it is open it runs on without end, as a
        request it sent may still come back and be charged, so it counts in every week from its start until it is settled;
        so does a charge the book code made with no time of its own."""
        start, end = utc(start), utc(end)
        rows = self.conn.execute(
            "SELECT t.at_us AS settled,"
            " SUM(CASE l.kind WHEN 'reserve' THEN l.amount ELSE 0 END) AS amount,"
            " COUNT(CASE l.kind WHEN 'reserve' THEN 1 END) AS reserves,"
            " SUM(CASE l.kind WHEN 'charge' THEN l.amount END) AS charged"
            " FROM week_spans s JOIN ledger l ON l.ref = s.ref"
            " JOIN ventures v ON v.venture_id = l.venture_id AND v.owner = s.owner"
            " LEFT JOIN week_settled t ON t.ref = s.ref"
            " WHERE s.owner = ? AND s.provider = ? AND l.account = ? AND s.start_us < ? AND (t.at_us IS NULL OR t.at_us >= ?)"
            " GROUP BY s.ref", (self.owner, provider, account, _us(end), _us(start))).fetchall()
        return [Held(float(row["amount"]), None if row["charged"] is None else float(row["charged"]),
                     None if row["settled"] is None else _moment(row["settled"])) for row in rows if row["reserves"]]

    def reserve_span(self, provider: str, *, account: str, amount: float, caps: dict[str, float],
                     decide: Callable[[], tuple[str, datetime, datetime, datetime]]) -> tuple[str, str]:
        """Reserve ``amount`` on ``account`` for ``provider``, and return the venture it is filed under and the reservation.
        One ``BEGIN IMMEDIATE`` transaction (``immediate``), opened before anything is read: ``decide()`` reads inside it
        everything admission rests on and gives why not, or nothing, with the start of the provider week, the reservation's
        own start and the week's end; then the week's venture, made once and named for the week's start in UTC, with
        ``caps``; the operator's own stop check on it; and the ledger's row with its span. Nothing it was checked against can
        change before it is written. A refusal is ``BudgetRefused``, raised once the transaction has ended with no
        reservation in it: what ``decide`` itself kept, such as where the provider's weeks fall, stays kept."""
        value = gates.amount_of(amount)
        if value is None:
            raise BudgetRefused(f"a reservation must be a finite, positive amount, got {amount!r}")
        with self.immediate():
            why, week, start, end = decide()
            if not why:
                week, start, end = utc(week), utc(start), utc(end)
                span = _us(week), _us(start), _us(end)
                if not span[0] <= span[1] < span[2]:
                    raise ValueError("a reservation starts in the week it is made under and ends after it starts")
                month = _week_name(week)
                vid = self._named(f"{provider}@{month}") or self._insert_venture(f"{provider}@{month}", caps)
                why = gates.spend_refusal(self.venture(vid), self._committed(vid, month), account, value) or ""
            if not why:
                reservation = uuid.uuid4().hex
                self._append_ledger(vid, month, account, "reserve", value, reservation)
                self.conn.execute("INSERT INTO week_spans VALUES (?, ?, ?, ?, ?, ?)", (reservation, self.owner, provider, *span))
        if why:
            raise BudgetRefused(why)
        return vid, reservation

    def settle_open(self, provider: str, account: str) -> float:
        """Settle in full, on the store's clock, every reservation of ``provider`` still open, and give what was charged. Only
        an owner that knows none of its spends is running may: the loop does, under its folder's tick lock, where an open
        reservation is a tick that died before it settled. Until then it counts in every week from its start."""
        with self.immediate():
            rows = self.conn.execute(
                "SELECT l.venture_id, l.ref FROM week_spans s JOIN ledger l ON l.ref = s.ref AND l.kind = 'reserve'"
                " JOIN ventures v ON v.venture_id = l.venture_id AND v.owner = s.owner"
                " WHERE s.owner = ? AND s.provider = ? AND l.account = ?"
                " AND NOT EXISTS (SELECT 1 FROM ledger x WHERE x.ref = s.ref AND x.kind = 'release') ORDER BY l.seq",
                (self.owner, provider, account)).fetchall()
            return sum(self._settle(row["venture_id"], row["ref"], None, None, None) for row in rows)

    def adopt_books(self, providers: Sequence[str]) -> dict[str, str]:
        """Read into spans, once, every reservation of ``providers`` that the book code before spans kept: under a week's
        venture named ``<provider>@<its start>``, in UTC or, before weeks were named in UTC, in the settings' zone, whose book
        later weeks could take over (``week_books``, and ``book_holders`` for where it went last). Nothing says when within
        those weeks one was made, so its span runs from the first of them to the end of the last: it counts in every week it
        may have been spent in, and is never forgotten, which would be an overspend. A charge is dated a second after the
        ledger wrote it, as the ledger keeps whole seconds, when that is not before the span's start; otherwise it has no
        time, and goes on top of every reading. One transaction, so a stop part way leaves the old rows as they were, read
        again on the next open. It returns why a provider's old rows cannot be read: that is a refusal to reserve until a
        person looks, never a fresh book."""
        unread: dict[str, str] = {}
        if not any(self._old_reservations(providers).values()):     # looked at again under the lock, before anything is read
            return unread
        with self.immediate():
            for provider, mine in self._old_reservations(providers).items():
                try:
                    spans = self._old_spans(provider, mine) if mine else []
                except ValueError as exc:
                    unread[provider] = (f"{len(mine)} reservation(s) of {provider} kept before week spans cannot all be read "
                                        f"({str(exc)[:160]}), so nothing more is reserved on it until a person looks at its "
                                        "rows in operator.db")
                    continue
                for ref, span in spans:
                    self.conn.execute("INSERT INTO week_spans VALUES (?, ?, ?, ?, ?, ?)", (ref, self.owner, provider, *span))
                    self._date_old_charge(ref, span[1])
        return unread

    def _old_reservations(self, providers: Sequence[str]) -> dict[str, list[sqlite3.Row]]:
        """Each provider's reservations with no span: those the book code before spans made, under its weeks' ventures."""
        rows = self.conn.execute(
            "SELECT l.ref, l.month, v.name FROM ledger l JOIN ventures v ON v.venture_id = l.venture_id WHERE v.owner = ?"
            " AND l.kind = 'reserve' AND NOT EXISTS (SELECT 1 FROM week_spans s WHERE s.ref = l.ref) ORDER BY l.seq",
            (self.owner,)).fetchall()
        return {provider: [row for row in rows if str(row["name"]).startswith(f"{provider}@")] for provider in providers}

    def _old_spans(self, provider: str, rows: Sequence[sqlite3.Row]) -> list[tuple[str, tuple[int, int, int]]]:
        """Inside the caller's transaction: the span of each old reservation of ``provider``, as ``(week, start, end)``. A
        ``ValueError`` when one of them, or a row that may have moved its book, does not read as a moment with its zone."""
        tables = {row["name"] for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        moved: dict[datetime, set[datetime]] = {}           # a book's own week, and every week that may have used it
        for table in (name for name in _OLD_MOVES if name in tables):
            for row in self.conn.execute(f"SELECT book, week FROM {table} WHERE owner=? AND provider=?", (self.owner, provider)):
                moved.setdefault(_instant(row["book"]), set()).add(_instant(row["week"]))
        spans = []
        for row in rows:
            book = _instant(row["month"])
            weeks = {book, *moved.get(book, ())}
            spans.append((row["ref"], (_us(book), _us(min(weeks)), _us(max(weeks) + _WEEK))))
        return spans

    def _date_old_charge(self, ref: str, start: int) -> None:
        row = self.conn.execute("SELECT at FROM ledger WHERE ref=? AND kind='charge' ORDER BY seq DESC LIMIT 1", (ref,)).fetchone()
        try:
            settled = _us(_instant(row["at"]) + timedelta(seconds=1)) if row is not None else None
        except ValueError:
            settled = None
        if settled is not None and settled >= start:
            self.conn.execute("INSERT INTO week_settled VALUES (?, ?)", (ref, settled))

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
               metered: float | None = None, at: datetime | None = None) -> float:
        """Release ``reservation`` and charge it: the full reservation, or ``reported`` when that is higher. What a
        run reports can raise its charge, never lower it, so an executor that reports 0 still spends its reservation
        (the archive recorded local and customer-client runs as 0, so their cap never tripped: VEN-004).

        ``metered`` is different: the host's own count of what the run used, taken where the run cannot touch it. When
        given it is the charge, up or down, and ``reported`` is not read.

        A reservation on a provider's weeks is dated as it settles, by the store's own clock, and never before it began:
        that one value ends its span (``week_held``) and dates its charge against a reading, for every reader alike.
        ``at`` is a time a caller gives instead, for recovery and tests; one before the reservation began is refused."""
        at = utc(at)
        if metered is not None and (isinstance(metered, bool) or not isinstance(metered, int | float)
                                    or not math.isfinite(metered) or metered < 0):
            raise ValueError(f"a metered amount must be finite and not negative, got {metered!r}")
        vid = self.resolve(venture_id)
        try:
            with self.immediate():
                return self._settle(vid, reservation, reported, metered, at)
        except sqlite3.IntegrityError as exc:
            raise ValueError("this reservation is already settled") from exc

    def _settle(self, vid: str, reservation: str, reported: float | None, metered: float | None,
                at: datetime | None) -> float:
        """Inside the caller's transaction: ``settle``, of a venture already resolved."""
        at = utc(at)
        row = self.conn.execute("SELECT month, account, amount FROM ledger WHERE venture_id=? AND ref=?"
                                " AND kind='reserve'", (vid, reservation)).fetchone()
        if row is None:
            raise LookupError("no such reservation for this venture")
        span = self.conn.execute("SELECT start_us FROM week_spans WHERE ref=?", (reservation,)).fetchone()
        if span is not None and at is not None and _us(at) < span["start_us"]:
            raise ValueError(f"a settlement cannot end at {at.isoformat()}, before its reservation began at "
                             f"{_moment(span['start_us']).isoformat()}")
        charged = float(row["amount"])
        reported_value = gates.amount_of(reported)
        if metered is not None:
            charged = float(metered)
        elif reported_value is not None and reported_value > charged:
            charged = reported_value
        self._append_ledger(vid, row["month"], row["account"], "release", row["amount"], reservation)
        self._append_ledger(vid, row["month"], row["account"], "charge", charged, reservation)
        if span is not None:
            ended = _us(at) if at is not None else max(_us(self.store.clock()), span["start_us"])
            self.conn.execute("INSERT INTO week_settled VALUES (?, ?)", (reservation, ended))
        return charged
