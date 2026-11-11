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

The store's own times never go backwards, whatever its clock does. It records the time of every reading of a provider's
account its meter is given (``keep_reading``), before it writes any stamp. A settlement is stamped after the newest of
those and no earlier than the newest stamp (``_stamp``), and a reservation is refused while the clock reads earlier than
either (``reserve_span``): the week an admission is checked in is never one the store has already left.

Every instant that enters this module goes through ``utc`` first, so two renderings of one instant are one instant: two
times in one zone compare by their wall clocks, which repeat in the hour a clock goes back.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
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
    read_at TEXT NOT NULL DEFAULT '', PRIMARY KEY (owner, provider));
CREATE TABLE IF NOT EXISTS week_spans (ref TEXT PRIMARY KEY, owner TEXT NOT NULL, provider TEXT NOT NULL,
    week_us INTEGER NOT NULL, start_us INTEGER NOT NULL, end_us INTEGER NOT NULL,
    CHECK (typeof(week_us) = 'integer' AND typeof(start_us) = 'integer' AND typeof(end_us) = 'integer'
           AND start_us < end_us));
CREATE INDEX IF NOT EXISTS week_spans_by_start ON week_spans (owner, provider, start_us);
CREATE TABLE IF NOT EXISTS week_settled (ref TEXT PRIMARY KEY, at_us INTEGER NOT NULL CHECK (typeof(at_us) = 'integer'));
CREATE TABLE IF NOT EXISTS week_ticks (ref TEXT PRIMARY KEY, tick TEXT NOT NULL CHECK (length(tick) > 0));
CREATE TABLE IF NOT EXISTS week_readings (owner TEXT NOT NULL, provider TEXT NOT NULL,
    read_us INTEGER NOT NULL CHECK (typeof(read_us) = 'integer'), PRIMARY KEY (owner, provider));
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
    ``None``; a time with no zone is a ``ValueError``, as a naive time reads differently on another machine. Only a plain
    ``datetime`` is a moment, and its zone is asked its offset once: what comes back is a plain ``datetime`` in UTC, with
    nothing of the caller's left on it, so no class of a caller's can answer a comparison or a sum its own way. Anything
    else is a ``TypeError``, and a zone that does not say its offset a ``ValueError``."""
    if moment is None:
        return None
    if type(moment) is not datetime:
        raise TypeError(f"a moment is a datetime, not a {gates.kind_of(moment)}")
    if moment.tzinfo is UTC:
        return moment
    try:
        offset = moment.utcoffset()
    except Exception:  # noqa: BLE001 - a zone that cannot say its offset gives no moment
        raise ValueError("a moment's zone does not say its offset from UTC") from None
    if offset is None:
        raise ValueError("a moment needs its zone: a naive time reads differently on another machine")
    if type(offset) is not timedelta:
        raise ValueError(f"a moment's zone gave a {gates.kind_of(offset)} for its offset, not a timedelta")
    try:
        return (moment.replace(tzinfo=None) - offset).replace(tzinfo=UTC)
    except OverflowError:
        raise ValueError("a moment out of the range a datetime holds") from None


_EPOCH = utc(datetime(1970, 1, 1, tzinfo=UTC))


def utc_clock(clock: Callable[[], datetime]) -> Callable[[], datetime]:
    """``clock`` as one that gives UTC, where a clock is taken in: every clock the loop's budget reads, its own or one it is
    given, goes through ``utc`` as it is defined, so no reading of it is a local instant to compare."""
    if getattr(clock, "gives_utc", False):
        return clock

    def read() -> datetime:
        return utc(clock())

    read.gives_utc, read.__wrapped__ = True, clock
    return read


def stored_amount(value: Any) -> float:
    """The one way an amount is read back from the ledger: a finite number of zero or more. Anything else, a text, bytes,
    an infinity, is a ``ValueError``: an unreadable row, which refuses, and never zero."""
    number = gates.exact_number(value)
    if number is None or number < 0:
        raise ValueError(f"an amount in the ledger, a {gates.kind_of(value)}, does not read as a finite number of zero or more")
    return number


#: What SQLite stores as it is given. A value of any other class is bound by what its own ``__conform__`` answers, each time
#: it is bound.
_BOUND = (str, int, float, bytes, type(None))


def _name(value: Any, what: str) -> str:
    """A name as it enters the store (a venture's name, a provider, an account, a month, a reservation): ``value`` as a
    plain ``str``, converted once where it enters, which is all that is used after it. ``gates.exact_text`` is the one
    conversion, for every method that takes a name: a method whose refusal is its own (no such venture, a reservation
    refused, an owner or a status that is none) calls it and refuses its own way. A text of a caller's own class is read at
    the text it holds; anything else is a ``ValueError`` here, which names its type and never renders it."""
    text = gates.exact_text(value)
    if text is None:
        raise ValueError(f"{what} is named by text, not a {gates.kind_of(value)}")
    return text


def _plain(*values: Any) -> tuple[Any, ...]:
    """What a statement of the store is given to bind, and nothing else is: values of the types SQLite stores as they are.
    A value of any other class, a caller's own kind of text among them, is bound by what its ``__conform__`` answers, which
    can differ between the statement that checks and the statement that writes; so none reaches a statement, and a
    ``TypeError`` names its type."""
    for value in values:
        if type(value) not in _BOUND:
            raise TypeError(f"a statement binds plain values, not a {gates.kind_of(value)}")
    return values


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


@utc_clock
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
    as a loop whose ticks run on a clock of their own does. ``poisoned``: why nothing more is written on this connection, or
    ``""``. It is set when a transaction that failed could not be shown to have rolled back (``OwnerScope.immediate``): what
    it wrote may still be pending on the connection, where the next commit of anything would keep it. From then every
    transaction and script on this store is refused; closing it drops what is pending, and a store opened again starts
    clean."""

    def __init__(self, path: str | os.PathLike[str] = "operator.db", *,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.conn = open_db(path)
        self.clock = utc_clock(clock or _wall_clock)
        self._writing = threading.RLock()     # every write and commit on this connection, one at a time (``immediate``)
        self._held = threading.local()        # how deep the thread that holds it is inside it
        self.poisoned = ""
        self.script(_SCHEMA + append_only("evidence") + append_only("ledger") + append_only("week_spans", "ref")
                    + append_only("week_settled", "ref") + append_only("week_ticks", "ref"))
        with self._writing:                   # a grid kept before it carried the time of the reading it came from
            if "read_at" not in {row["name"] for row in self.conn.execute("PRAGMA table_info(week_grids)")}:
                self.conn.execute("ALTER TABLE week_grids ADD COLUMN read_at TEXT NOT NULL DEFAULT ''")
                self.conn.commit()

    def script(self, sql: str) -> None:
        """Run a script that commits on its own (``executescript`` does), holding the store's lock, so it can never commit
        another thread's transaction half way. It is refused inside a transaction, whether this thread's own or one the
        connection itself says is still open, and on a poisoned store, so its commit never keeps what a transaction left."""
        with self._writing:
            if self.poisoned:
                raise sqlite3.OperationalError(f"nothing more is written on this connection: {self.poisoned}")
            if self.in_transaction() or self.conn.in_transaction:
                raise RuntimeError("a script commits on its own, so it never runs inside a transaction")
            self.conn.executescript(sql)

    def in_transaction(self) -> bool:
        """Whether the calling thread is inside ``OwnerScope.immediate`` on this store."""
        return getattr(self._held, "depth", 0) > 0

    def for_owner(self, owner: str) -> OwnerScope:
        return OwnerScope(self, owner)

    def close(self) -> None:
        self.conn.close()


class OwnerScope:
    """One owner's view of the store: the only handle the queue and the tick hold."""

    def __init__(self, store: OperatorStore, owner: str) -> None:
        name = gates.exact_text(owner)                # converted once: every statement binds this plain text
        if name is None or not name.strip():
            raise ValueError("an owner scope needs an owner")
        self.store, self.owner, self.conn = store, name, store.conn
        #: Set by a meter on this scope: called with a provider and a moment before the store reads what it has recorded
        #: of the provider's times (``_kept_times``), so the reading the meter can be given now is recorded first.
        self.ask_reader: Callable[[str, datetime], Any] | None = None

    def resolve(self, venture_id: str) -> str:
        """The venture's id as the store holds it, which is all that is used after this. An id that is not text names no
        venture, and gets the same refusal as one this owner does not hold."""
        name = gates.exact_text(venture_id)
        row = None if name is None else self.conn.execute("SELECT venture_id FROM ventures WHERE venture_id=? AND owner=?",
                                                          _plain(name, self.owner)).fetchone()
        if row is None:
            raise LookupError(NOT_FOUND)
        return row["venture_id"]

    # -- ventures -------------------------------------------------------------------------
    def create_venture(self, name: str, *, caps: dict[str, float]) -> dict[str, Any]:
        """``caps`` gives each account in ``gates.ACCOUNTS`` a finite, non-negative monthly budget (0 = none may be
        spent). A missing or invalid cap is refused here, and would stop the venture if it reached the stop check."""
        name = _name(name, "a venture")
        with self.immediate():
            venture_id = self._insert_venture(name, caps)
        return self.venture(venture_id)

    def _insert_venture(self, name: str, caps: dict[str, float]) -> str:
        """Inside the caller's transaction: the venture's row and its history, and its id."""
        clean = {a: gates.cap_of(caps, a) for a in gates.ACCOUNTS}       # each cap read once: the one that is checked is kept
        bad = [a for a, cap in clean.items() if cap is None]
        if bad:
            raise ValueError(f"caps must give a finite, non-negative monthly budget for {bad}")
        venture_id = uuid.uuid4().hex[:12]
        self.conn.execute("INSERT INTO ventures VALUES (?, ?, ?, ?, 'active', ?)",
                          _plain(venture_id, self.owner, name, json.dumps(clean), _now()))
        self._append_evidence(venture_id, "created", "", {"name": name, "caps": clean})
        return venture_id

    def has_venture(self, name: str) -> bool:
        return self._named(_name(name, "a venture")) is not None

    def _named(self, name: str) -> str | None:
        row = self.conn.execute("SELECT venture_id FROM ventures WHERE owner=? AND name=?",
                                _plain(self.owner, name)).fetchone()
        return row["venture_id"] if row is not None else None

    # -- a provider's weeks ---------------------------------------------------------------
    def week_grid(self, provider: str) -> datetime | None:
        """A moment one of the provider's weeks ended, as it last reported, or ``None`` before it has reported one. A row that
        does not read as a moment with its zone is a ``ValueError``: where its weeks fall is then not known."""
        return self.kept_grid(provider)[0]

    def kept_grid(self, provider: str) -> tuple[datetime | None, datetime | None]:
        """The kept grid (``week_grid``) and when the reading it came from was taken, ``None`` for one with no such time."""
        provider = _name(provider, "a provider")
        row = self.conn.execute("SELECT reset, read_at FROM week_grids WHERE owner=? AND provider=?",
                                _plain(self.owner, provider)).fetchone()
        if row is None:
            return None, None
        return _instant(row["reset"]), _instant(row["read_at"]) if row["read_at"] else None

    @contextmanager
    def immediate(self) -> Iterator[None]:
        """One ``BEGIN IMMEDIATE`` transaction: what is read inside it is what a write inside it is checked against, as no
        other connection can write until it ends. It belongs to the thread that opened it: another thread on this same
        connection waits here until it ends, then opens its own and reads what the first one wrote. A use inside one the
        same thread already holds is a savepoint within it, so a store method that writes on its own can also be one step
        of a larger check: when that step fails, its own part rolls back, whatever the larger check then does, and the rest
        stands.

        Whatever fails, and however (an error or an interrupt), nothing a failed transaction or step wrote is kept. The
        outer transaction's begin, its work and its commit are one guarded step: when any of them fails it is rolled back
        and the connection is asked whether it did (``_abandon``); a rollback that cannot be shown to have happened poisons
        the store, which then refuses every transaction and script. A step whose savepoint fails, or whose undo fails
        (``_undo``), marks the transaction it is in: a savepoint that failed may still be there, under the name the next
        step at its depth takes, where a later undo would roll back to it. A marked transaction is rolled back whole when
        it ends, and every step in it from then on ends in that error, so none is told it was kept."""
        store = self.store
        with store._writing:
            if store.poisoned:
                raise sqlite3.OperationalError(f"nothing more is written on this connection: {store.poisoned}")
            depth = getattr(store._held, "depth", 0)
            store._held.depth = depth + 1
            try:
                if depth:
                    point = f"g6_step_{depth}"
                    try:
                        self.conn.execute(f"SAVEPOINT {point}")
                    except BaseException as exc:   # it may have been made all the same: the transaction must not commit
                        store._held.broken = store._held.broken or f"a step could not be begun ({gates.kind_of(exc)})"
                        raise
                    try:
                        yield
                        self.conn.execute(f"RELEASE {point}")
                    except BaseException:          # the step, or its own release, failed: nothing of it is left
                        self._undo(point)
                        raise
                    if store._held.broken:
                        raise sqlite3.OperationalError(f"nothing of this transaction is kept: {store._held.broken}")
                else:
                    store._held.broken = ""
                    try:
                        self.conn.execute("BEGIN IMMEDIATE")
                        yield
                        if store._held.broken:
                            raise sqlite3.OperationalError(f"nothing of this transaction is kept: {store._held.broken}")
                        self.conn.commit()
                        if self.conn.in_transaction is not False:
                            raise sqlite3.OperationalError("the commit left its transaction open, so nothing of it is kept")
                    except BaseException:          # the begin, the work or the commit failed: nothing of it is left
                        self._abandon()
                        raise
            finally:
                store._held.depth = depth

    def _undo(self, point: str) -> None:
        """Roll a failed step back to its savepoint and release it. When that itself fails, for any reason, an interrupt
        among them, the step's writes may still be there, so the transaction it is in is marked: it can no longer commit,
        every step in it ends in that error, and it is rolled back whole when it ends. An interrupt goes on once that is
        marked."""
        try:
            self.conn.execute(f"ROLLBACK TO {point}")
            self.conn.execute(f"RELEASE {point}")
        except BaseException as exc:  # whatever stopped the undo, the transaction must not commit
            self.store._held.broken = f"a failed step could not be rolled back ({gates.kind_of(exc)})"
            if not isinstance(exc, Exception):
                raise

    def _abandon(self) -> None:
        """Roll back the transaction this thread holds, after it failed at its begin, in its work or at its commit, and ask
        the connection whether it did. When the rollback fails, for any reason, or the connection still says a transaction
        is open, what was written may still be pending on it, where the next commit of anything would keep it: the store is
        poisoned (``OperatorStore.poisoned``), and nothing more is written on it. An interrupt goes on once that is marked."""
        try:
            self.conn.rollback()
            pending = self.conn.in_transaction
        except BaseException as exc:  # an undo whose outcome is not known: nothing more is admitted on this connection
            self.store.poisoned = f"a failed transaction could not be rolled back ({gates.kind_of(exc)})"
            if not isinstance(exc, Exception):
                raise
            return
        if pending is not False:
            self.store.poisoned = "a failed transaction was rolled back and the connection still holds one"

    def keep_grid(self, provider: str, reset: datetime, read_at: datetime | None = None) -> tuple[datetime, datetime | None]:
        """Where the provider's weeks fall, as a reading taken at ``read_at`` says: a moment one of them ends. A grid moves
        only forward: it is replaced only by a reading newer than the one it came from (or when it came from one with no
        time), decided inside the transaction that writes it, so an older reading leaves a newer grid as it is. It moves the
        weeks a reading looks at, and no reservation. It gives the grid that stands after it, and its reading's time. A kept
        row that does not read says nothing of where the weeks fall, so the reading's word replaces it."""
        reset, read_at = utc(reset), utc(read_at)
        provider = _name(provider, "a provider")          # converted once: the grid it is checked against is the one written
        with self.immediate():
            try:
                kept, kept_at = self.kept_grid(provider)
            except ValueError:
                kept, kept_at = None, None
            if kept is not None and kept_at is not None and (read_at is None or read_at <= kept_at):
                return kept, kept_at
            self.conn.execute("INSERT OR REPLACE INTO week_grids VALUES (?, ?, ?, ?)", _plain(
                self.owner, provider, _week_name(reset), _week_name(read_at) if read_at is not None else ""))
            return reset, read_at

    def week_venture(self, provider: str, week: datetime) -> str:
        """The venture the reservations made in the provider week that began at ``week`` are filed under, or ``""`` before
        one is made."""
        week = utc(week)
        name = gates.exact_text(provider)
        return self._named(f"{name}@{_week_name(week)}") or "" if name is not None else ""

    def week_held(self, provider: str, account: str, start: datetime, end: datetime) -> list[Held]:
        """Every reservation of ``provider`` on ``account`` whose span overlaps [``start``, ``end``), as it stands, in one
        read, so a settlement on another connection is all in it or not at all.

        A reservation's span runs from its start to when it was settled. While it is open it has no end, as a request it
        sent may still come back and be charged, so it counts in every week until it is settled, whatever its start: a
        clock set back over a week's end must not read the week before as empty of it. So does a charge the book code made
        with no time of its own. Every amount is read as one (``stored_amount``): one that does not read is a
        ``ValueError``, never zero. Amounts are summed exactly (``math.fsum``)."""
        start, end = utc(start), utc(end)
        provider, account = _name(provider, "a provider"), _name(account, "an account")
        rows = self.conn.execute(
            "SELECT s.ref AS ref, t.at_us AS settled, l.kind AS kind, l.amount AS amount"
            " FROM week_spans s JOIN ledger l ON l.ref = s.ref"
            " JOIN ventures v ON v.venture_id = l.venture_id AND v.owner = s.owner"
            " LEFT JOIN week_settled t ON t.ref = s.ref"
            " WHERE s.owner = ? AND s.provider = ? AND l.account = ? AND (t.at_us IS NULL OR (s.start_us < ? AND t.at_us >= ?))"
            " ORDER BY l.seq", _plain(self.owner, provider, account, _us(end), _us(start))).fetchall()
        found: dict[str, dict[str, Any]] = {}
        for row in rows:
            held = found.setdefault(row["ref"], {"settled": row["settled"], "reserve": [], "charge": []})
            if row["kind"] in ("reserve", "charge"):
                held[row["kind"]].append(stored_amount(row["amount"]))
        return [Held(math.fsum(held["reserve"]), math.fsum(held["charge"]) if held["charge"] else None,
                     None if held["settled"] is None else _moment(held["settled"]))
                for held in found.values() if held["reserve"]]

    def reserve_span(self, provider: str, *, account: str, amount: float, caps: dict[str, float],
                     decide: Callable[[], tuple[str, datetime, datetime, datetime]], tick: str = "") -> tuple[str, str]:
        """Reserve ``amount`` on ``account`` for ``provider``, and return the venture it is filed under and the reservation.
        One ``BEGIN IMMEDIATE`` transaction (``immediate``), opened before anything is read: ``decide()`` reads inside it
        everything admission rests on and gives why not, or nothing, with the start of the provider week, the reservation's
        own start and the week's end; then the week's venture, made once and named for the week's start in UTC, with
        ``caps``; the operator's own stop check on it; and the ledger's row with its span. Nothing it was checked against can
        change before it is written. A refusal is ``BudgetRefused``, raised once the transaction has ended with no
        reservation in it: what ``decide`` itself kept, such as where the provider's weeks fall, stays kept. ``tick``: the
        tick that makes it, recorded with it, or ``""`` for none (``settle_open``). Every value given is converted once, as it
        enters, to a plain one (``gates.exact_number``, ``gates.exact_text``), and only that is used. A reservation is
        refused while the clock is behind the book: when the start it is given and the store's own clock both read earlier
        than a time the store has already recorded for the provider (``_kept_times``: a settlement's stamp, a reading). The
        clock has then been set back, and the week the reservation names may be one the store has left. One microsecond
        before is not before: the store itself stamps a settlement that far after a reading of the same instant. The
        store's clock is read only when the start is behind; when it is not behind too, the store and its caller are on
        two clocks, and the caller's time is taken as it always was."""
        value = gates.amount_of(amount)
        if value is None:
            raise BudgetRefused(f"a reservation must be a finite, positive amount, not a {gates.kind_of(amount)}")
        provider, account, tick = gates.exact_text(provider), gates.exact_text(account), gates.exact_text(tick)
        if provider is None or account is None or tick is None:
            raise BudgetRefused("a reservation's provider, account and tick are text")
        with self.immediate():
            said, week, start, end = decide()
            why = gates.exact_text(said)
            if why is None:
                why = f"admission gave a {gates.kind_of(said)}, not why or nothing"
            if not why:
                week, start, end = utc(week), utc(start), utc(end)
                span = _us(week), _us(start), _us(end)
                if not span[0] <= span[1] < span[2]:
                    raise ValueError("a reservation starts in the week it is made under and ends after it starts")
                stamps, readings = self._kept_times(self.owner, provider, span[1])
                recorded = max((*stamps, *readings), default=None)
                if recorded is not None and span[1] + 1 < recorded and _us(self.store.clock()) + 1 < recorded:
                    why = (f"the clock reads earlier than a time already recorded for {provider} (a settlement or a reading "
                           "of its account), so nothing is reserved on it until the clock has caught up")
            if not why:
                month = _week_name(week)
                vid = self._named(f"{provider}@{month}") or self._insert_venture(f"{provider}@{month}", caps)
                why = self._refusal(vid, month, account, value)
            if not why:
                reservation = uuid.uuid4().hex
                self._append_ledger(vid, month, account, "reserve", value, reservation)
                self.conn.execute("INSERT INTO week_spans VALUES (?, ?, ?, ?, ?, ?)",
                                  _plain(reservation, self.owner, provider, *span))
                if tick:
                    self.conn.execute("INSERT INTO week_ticks VALUES (?, ?)", _plain(reservation, tick))
        if why:
            raise BudgetRefused(why)
        return vid, reservation

    def settle_open(self, provider: str, account: str, *, made_by_a_tick: bool = False) -> float:
        """Settle in full, on the store's clock, every reservation of ``provider`` still open, and give what was charged.
        Only an owner that knows none of them can still be spending may. ``made_by_a_tick``: only those a tick made, as the
        loop recovers under its folder's tick lock: one tick holds it at a time, so an open reservation a tick made is one a
        tick that is gone left. One made outside a tick is never recovered: it stays open, and counts, until its maker
        settles it. All or none: every amount is read (``stored_amount``) before any is settled, and the whole is one
        transaction, or one savepoint of the caller's, so one that does not read is a ``ValueError`` with nothing settled.
        Anything but ``False`` itself for ``made_by_a_tick`` settles only those a tick made, which is the fewer."""
        provider, account = _name(provider, "a provider"), _name(account, "an account")
        with self.immediate():
            rows = self.conn.execute(
                "SELECT l.venture_id, l.ref, l.amount FROM week_spans s JOIN ledger l ON l.ref = s.ref AND l.kind = 'reserve'"
                " JOIN ventures v ON v.venture_id = l.venture_id AND v.owner = s.owner"
                " WHERE s.owner = ? AND s.provider = ? AND l.account = ?"
                " AND NOT EXISTS (SELECT 1 FROM ledger x WHERE x.ref = s.ref AND x.kind = 'release')"
                " AND (? = 0 OR EXISTS (SELECT 1 FROM week_ticks k WHERE k.ref = s.ref)) ORDER BY l.seq",
                _plain(self.owner, provider, account, 0 if made_by_a_tick is False else 1)).fetchall()
            for row in rows:
                stored_amount(row["amount"])
            return math.fsum([self._settle(row["venture_id"], row["ref"], None, None) for row in rows])

    def adopt_books(self, providers: Sequence[str]) -> dict[str, str]:
        """Read into spans, once, every reservation of ``providers`` that the book code before spans kept: under a week's
        venture named ``<provider>@<its start>``, in UTC or, before weeks were named in UTC, in the settings' zone, whose book
        later weeks could take over (``week_books``, and ``book_holders`` for where it went last). Nothing says when within
        those weeks one was made, so its span runs from the first of them to the end of the last: it counts in every week it
        may have been spent in, and is never forgotten, which would be an overspend. A charge is dated a second after the
        ledger wrote it, as the ledger keeps whole seconds, and never before what the store has already recorded for the
        provider (``_stamp``), when that is not before the span's start; otherwise it has no time, and goes on top of every
        reading. One transaction, so a stop part way leaves the old rows as they were, read
        again on the next open. It returns why a provider's old rows cannot be read: that is a refusal to reserve until a
        person looks, never a fresh book."""
        providers = [name for name in map(gates.exact_text, providers) if name is not None]
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
                    self.conn.execute("INSERT INTO week_spans VALUES (?, ?, ?, ?, ?, ?)",
                                      _plain(ref, self.owner, provider, *span))
                    self._date_old_charge(ref, provider, span[1])
        return unread

    def _old_reservations(self, providers: Sequence[str]) -> dict[str, list[sqlite3.Row]]:
        """Each provider's reservations with no span: those the book code before spans made, under its weeks' ventures."""
        rows = self.conn.execute(
            "SELECT l.ref, l.month, l.amount, v.name FROM ledger l JOIN ventures v ON v.venture_id = l.venture_id WHERE v.owner = ?"
            " AND l.kind = 'reserve' AND NOT EXISTS (SELECT 1 FROM week_spans s WHERE s.ref = l.ref) ORDER BY l.seq",
            _plain(self.owner)).fetchall()
        return {provider: [row for row in rows if str(row["name"]).startswith(f"{provider}@")] for provider in providers}

    def _old_spans(self, provider: str, rows: Sequence[sqlite3.Row]) -> list[tuple[str, tuple[int, int, int]]]:
        """Inside the caller's transaction: the span of each old reservation of ``provider``, as ``(week, start, end)``. A
        ``ValueError`` when one of them, or a row that may have moved its book, does not read as a moment with its zone, or
        when its amount does not read as one (``stored_amount``)."""
        tables = {row["name"] for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        moved: dict[datetime, set[datetime]] = {}           # a book's own week, and every week that may have used it
        for table in (name for name in _OLD_MOVES if name in tables):
            for row in self.conn.execute(f"SELECT book, week FROM {table} WHERE owner=? AND provider=?",
                                         _plain(self.owner, provider)):
                moved.setdefault(_instant(row["book"]), set()).add(_instant(row["week"]))
        spans = []
        for row in rows:
            stored_amount(row["amount"])
            book = _instant(row["month"])
            weeks = {book, *moved.get(book, ())}
            spans.append((row["ref"], (_us(book), _us(min(weeks)), _us(max(weeks) + _WEEK))))
        return spans

    def _date_old_charge(self, ref: str, provider: str, start: int) -> None:
        """Inside the caller's transaction: date the charge of a reservation found with no span, from the ledger's own time
        for it, through the one rule for a stamp (``_stamp``). The ledger's time alone is not enough: the operator's own
        ``reserve`` still writes such a reservation, and it may be charged after the store kept a reading."""
        row = self.conn.execute("SELECT at FROM ledger WHERE ref=? AND kind='charge' ORDER BY seq DESC LIMIT 1",
                                _plain(ref)).fetchone()
        try:
            settled = _us(_instant(row["at"]) + timedelta(seconds=1)) if row is not None else None
        except ValueError:
            settled = None
        if settled is not None and settled >= start:
            self._stamp(ref, self.owner, provider, settled, start)

    def venture_named(self, name: str, *, caps: dict[str, float]) -> str:
        """The id of this owner's venture of this name, made once however many ask at the same moment."""
        name = _name(name, "a venture")
        with self.immediate():
            return self._named(name) or self._insert_venture(name, caps)

    def venture(self, venture_id: str) -> dict[str, Any]:
        name = gates.exact_text(venture_id)          # as ``resolve``: an id that is not text names no venture
        row = None if name is None else self.conn.execute("SELECT * FROM ventures WHERE venture_id=? AND owner=?",
                                                          _plain(name, self.owner)).fetchone()
        if row is None:
            raise LookupError(NOT_FOUND)
        out = dict(row)
        out["caps"] = json.loads(out.pop("caps_json"))
        return out

    def list_ventures(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT venture_id FROM ventures WHERE owner=? ORDER BY created_at", _plain(self.owner))
        return [self.venture(r["venture_id"]) for r in rows.fetchall()]

    def set_status(self, venture_id: str, status: str, *, by: str) -> None:
        status = gates.exact_text(status)            # converted once: the status that is checked is the one written
        if status not in STATUSES:
            raise ValueError(f"status must be one of {sorted(STATUSES)}")
        vid = self.resolve(venture_id)
        with self.immediate():
            self.conn.execute("UPDATE ventures SET status=? WHERE venture_id=?", _plain(status, vid))
            self._append_evidence(vid, "status", by, {"status": status})

    # -- evidence (append-only) -----------------------------------------------------------
    def record(self, venture_id: str, kind: str, *, ref: str = "", detail: dict[str, Any] | None = None) -> int:
        vid = self.resolve(venture_id)
        with self.immediate():
            return self._append_evidence(vid, kind, ref, detail)

    def evidence(self, venture_id: str, *, kind: str | None = None, ref: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM evidence WHERE venture_id=?", [self.resolve(venture_id)]
        for column, value in (("kind", kind), ("ref", ref)):
            if value is not None:
                sql += f" AND {column}=?"
                args.append(value)
        rows = self.conn.execute(sql + " ORDER BY seq", _plain(*args)).fetchall()
        return [{**{k: r[k] for k in ("seq", "kind", "ref", "at")}, "detail": json.loads(r["detail_json"])}
                for r in rows]

    def _append_evidence(self, vid: str, kind: str, ref: str, detail: dict[str, Any] | None) -> int:
        """Inside the caller's transaction: a state change and its history commit together or not at all."""
        cur = self.conn.execute("INSERT INTO evidence (venture_id, kind, ref, detail_json, at) VALUES (?, ?, ?, ?, ?)",
                                _plain(vid, kind, ref, json.dumps(detail or {}, default=str), _now()))
        return int(cur.lastrowid)

    # -- the monthly ledger ---------------------------------------------------------------
    def committed(self, venture_id: str, month: str) -> dict[str, float]:
        """This month's charges plus open reservations, per account."""
        return self._committed(self.resolve(venture_id), _name(month, "a month"))

    def _committed(self, vid: str, month: str) -> dict[str, float]:
        return self._month(vid, month, {"reserve": 1, "release": -1, "charge": 1})

    def outstanding(self, venture_id: str, month: str) -> dict[str, float]:
        """What is reserved and not yet settled, per account: the part of ``committed`` nothing has charged."""
        return self._outstanding(self.resolve(venture_id), _name(month, "a month"))

    def _outstanding(self, vid: str, month: str) -> dict[str, float]:
        return self._month(vid, month, {"reserve": 1, "release": -1, "charge": 0})

    def _month(self, vid: str, month: str, signs: dict[str, int]) -> dict[str, float]:
        """A month's rows summed per account by ``signs``, each amount read as one (``stored_amount``): a row that does not
        read is a ``ValueError``, never zero. The sum is exact (``math.fsum``): a running sum of floats stops moving once it
        is large, and every small amount after that would be admitted against a cap already met."""
        terms: dict[str, list[float]] = {}
        for row in self.conn.execute("SELECT account, kind, amount FROM ledger WHERE venture_id=? AND month=? ORDER BY seq",
                                     _plain(vid, month)):
            terms.setdefault(row["account"], []).append(signs[row["kind"]] * stored_amount(row["amount"]))
        return {account: math.fsum(amounts) for account, amounts in terms.items()}

    def _refusal(self, vid: str, month: str, account: str, value: float) -> str:
        """Inside the caller's transaction: why the operator's stop check refuses ``value`` this month, or ``""``. A month
        whose ledger cannot be read refuses: an amount that does not read never counts as zero."""
        try:
            committed = self._committed(vid, month)
        except ValueError as exc:
            return f"this month's ledger cannot be read ({exc}), so nothing more is reserved on it"
        return gates.spend_refusal(self.venture(vid), committed, account, value) or ""

    def _append_ledger(self, vid: str, month: str, account: str, kind: str, amount: float, ref: str) -> None:
        """Inside the caller's transaction: one row of the ledger. Its amount is written by the rule it is read back by
        (``stored_amount``), so nothing is turned into a number on its way in: one that is not a finite number of zero or
        more is a ``ValueError``."""
        self.conn.execute("INSERT INTO ledger (venture_id, month, account, kind, amount, ref, at)"
                          " VALUES (?, ?, ?, ?, ?, ?, ?)", _plain(vid, month, account, kind, stored_amount(amount), ref, _now()))

    def reserve(self, venture_id: str, *, month: str, account: str, amount: float,
                admit: Callable[[float, float], str] | None = None) -> str:
        """Reserve ``amount`` before a spend starts; the stop check and the write are one transaction. ``admit`` is a
        further check inside it, given this account's committed and outstanding amounts as they stand under the lock,
        and returns why not, or nothing. The month and the account are converted once, as they enter, to plain text: the
        account that is checked against its cap is the account the ledger's row is written on."""
        vid = self.resolve(venture_id)
        value = gates.amount_of(amount)
        if value is None:
            raise BudgetRefused(f"a reservation must be a finite, positive amount, not a {gates.kind_of(amount)}")
        month, account = gates.exact_text(month), gates.exact_text(account)
        if month is None or account is None:
            raise BudgetRefused("a reservation's month and account are text")
        with self.immediate():
            try:
                said = "" if admit is None else admit(self._committed(vid, month).get(account, 0.0),
                                                      self._outstanding(vid, month).get(account, 0.0))
                why = gates.exact_text(said)              # converted once: its own truth is never asked
                if why is None:
                    why = f"the further check gave a {gates.kind_of(said)}, not why or nothing"
            except ValueError as exc:
                why = f"this month's ledger cannot be read ({exc}), so nothing more is reserved on it"
            why = why or self._refusal(vid, month, account, value)
            if why:
                raise BudgetRefused(why)
            reservation = uuid.uuid4().hex
            self._append_ledger(vid, month, account, "reserve", value, reservation)
            return reservation

    def settle(self, venture_id: str, reservation: str, *, reported: float | None = None,
               metered: float | None = None) -> float:
        """Release ``reservation`` and charge it: the full reservation, or ``reported`` when that is higher. What a
        run reports can raise its charge, never lower it, so an executor that reports 0 still spends its reservation
        (the archive recorded local and customer-client runs as 0, so their cap never tripped: VEN-004).

        ``metered`` is different: the host's own count of what the run used, taken where the run cannot touch it. When
        given it is the charge, up or down, and ``reported`` is not read.

        A reservation on a provider's weeks is dated as it settles, by the store's own clock alone, and never before it
        began: that one value ends its span (``week_held``) and dates its charge against a reading, for every reader
        alike. No caller gives that time. The reservation is converted once, as it enters, to plain text: every statement
        of the settlement names that one reservation."""
        reservation = _name(reservation, "a reservation")
        if metered is not None:
            given, metered = metered, gates.exact_number(metered)          # converted once; only this is used
            if metered is None or metered < 0:
                raise ValueError(f"a metered amount must be finite and not negative, not a {gates.kind_of(given)}")
        reported = gates.amount_of(reported)                                  # converted once; one that does not read is none
        vid = self.resolve(venture_id)
        try:
            with self.immediate():
                return self._settle(vid, reservation, reported, metered)
        except sqlite3.IntegrityError as exc:
            raise ValueError("this reservation is already settled") from exc

    def _settle(self, vid: str, reservation: str, reported: float | None, metered: float | None) -> float:
        """Inside the caller's transaction: ``settle``, of a venture already resolved."""
        row = self.conn.execute("SELECT month, account, amount FROM ledger WHERE venture_id=? AND ref=?"
                                " AND kind='reserve'", _plain(vid, reservation)).fetchone()
        if row is None:
            raise LookupError("no such reservation for this venture")
        span = self.conn.execute("SELECT owner, provider, start_us FROM week_spans WHERE ref=?", _plain(reservation)).fetchone()
        reserved = stored_amount(row["amount"])
        charged = reserved
        reported_value = gates.amount_of(reported)
        if metered is not None:
            charged = float(metered)
        elif reported_value is not None and reported_value > charged:
            charged = reported_value
        self._append_ledger(vid, row["month"], row["account"], "release", reserved, reservation)
        self._append_ledger(vid, row["month"], row["account"], "charge", charged, reservation)
        if span is not None:
            self._stamp(reservation, span["owner"], span["provider"], _us(self.store.clock()), span["start_us"])
        return charged

    def _stamp(self, ref: str, owner: str, provider: str, at: int, start: int) -> None:
        """Inside the caller's transaction: the one statement that stamps a settlement, for one settled now (``_settle``) and
        for a charge found with no stamp (``_date_old_charge``) alike. It is stamped at ``at``, and no earlier than its
        reservation's start or anything the store has already recorded for the provider (``_kept_times``): no earlier than
        the newest stamp already written, and a microsecond after the newest reading, as a settlement written after a
        reading was given cannot be in that reading's figure. One rule, so no charge written after a reading reached the
        meter is dated inside that reading, whichever way it is settled."""
        stamps, readings = self._kept_times(owner, provider, at)
        self.conn.execute("INSERT INTO week_settled VALUES (?, ?)",
                          _plain(ref, max(at, start, *stamps, *(read + 1 for read in readings))))

    def keep_reading(self, provider: str, read_at: datetime | None) -> None:
        """Record that a reading of the provider's account taken at ``read_at`` has been given to the meter: the newest such
        time, which only moves forward. Every reading is recorded, whether or not it says when the provider's week ends, and
        before any stamp is written after it (``_kept_times``), so no charge settled after a reading was taken is dated
        inside it. ``None`` is no time, and records nothing."""
        read_at = utc(read_at)
        provider = _name(provider, "a provider")
        if read_at is None:
            return
        at = _us(read_at)
        kept = self._read_time(self.owner, provider)
        if kept is not None and kept >= at:
            return
        with self.immediate():
            kept = self._read_time(self.owner, provider)       # looked at again under the lock, before it is written
            if kept is None or kept < at:
                self.conn.execute("INSERT OR REPLACE INTO week_readings VALUES (?, ?, ?)", _plain(self.owner, provider, at))

    def _read_time(self, owner: str, provider: str) -> int | None:
        """The newest time of a reading recorded for the provider (``keep_reading``), or ``None``."""
        row = self.conn.execute("SELECT read_us FROM week_readings WHERE owner = ? AND provider = ?",
                                _plain(owner, provider)).fetchone()
        return row["read_us"] if row is not None and type(row["read_us"]) is int else None

    def _kept_times(self, owner: str, provider: str, at: int) -> tuple[list[int], list[int]]:
        """Inside the caller's transaction: the times the store has already recorded for the provider, which are the
        floors of a settlement's stamp (``_stamp``) and of a reservation's start (``reserve_span``) alike, so its times
        never go backwards whatever its clock does: the newest settlement stamp, and the times of the readings it was
        given (the newest recorded, with or without a reset, and the one the kept grid came from). Whoever reads the
        account is asked first (``ask_reader``, at the moment ``at``), so a reading the meter can be given now is recorded
        before these are read. A kept time that does not read sets no floor: such a grid is refused and replaced by the
        next reading (``keep_grid``)."""
        if self.ask_reader is not None and owner == self.owner:
            self.ask_reader(provider, _moment(at))
        stamps, readings = [], []
        read = self._read_time(owner, provider)
        if read is not None:
            readings.append(read)
        newest = self.conn.execute("SELECT MAX(t.at_us) FROM week_settled t JOIN week_spans s ON s.ref = t.ref"
                                   " WHERE s.owner = ? AND s.provider = ?", _plain(owner, provider)).fetchone()[0]
        if newest is not None:
            stamps.append(int(newest))
        row = self.conn.execute("SELECT read_at FROM week_grids WHERE owner = ? AND provider = ?",
                                _plain(owner, provider)).fetchone()
        try:
            if row is not None and row["read_at"]:
                readings.append(_us(_instant(row["read_at"])))
        except ValueError:
            pass
        return stamps, readings