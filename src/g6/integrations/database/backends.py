# SPDX-License-Identifier: Apache-2.0
"""The backends that reach a database, registered by name, and SQLite, the one that needs nothing installed.

A backend is made per call from a connection's settings (:func:`create`) and does the engine's part of each op:

* ``read(statement, params, limits)``: one statement on a connection that cannot write, at most ``max_rows + 1`` rows
  fetched, so the op can say whether more exist;
* ``explain(statement, params, limits)``: the engine's plan of one statement, which it does not run;
* ``write(statements, params, limits, decide)``: every statement in one transaction, counting the rows each changes and,
  for a DELETE or UPDATE, whether it changed every row of its table; then ``decide`` sees what happened and the
  transaction is committed only if it returns true, otherwise rolled back (a failure rolls back too);
* ``tables(limits)``, ``columns(table, limits)`` (``None`` for a table that is not there) and ``schema(limits)``.

Each refuses with :class:`BackendError`, carrying a stable code. The SQLite backend opens its read connection with
``mode=ro`` and ``PRAGMA query_only``, and installs an authorizer: reads may only select, read and call functions
other than ``load_extension``; writes may also insert, update, delete, create and alter, and drop only where the
operator allows destructive statements; ATTACH, DETACH, PRAGMA and transaction control are never authorized
(DATABASE-02, DATABASE-04). A progress handler stops a statement at the time limit (``g6.core.resources``).

Register another backend with :func:`register_backend`; DuckDB and PostgreSQL are registered here and import their
drivers only when made, so without the ``db`` extra they refuse with ``G6_E_DB_NOT_INSTALLED``.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from g6.core.resources import RESOURCE_LIMIT, ResourceBounds, ResourceUsage, check
from g6.core.storage import g6_home
from g6.integrations.database.config import ConnectionSettings
from g6.integrations.database.envelope import CONFIG_INVALID, CONNECT_FAILED, STATEMENT_FAILED
from g6.integrations.database.sql import Statement


@dataclass(frozen=True)
class Limits:
    """The ceilings on one call: rows, characters per cell and in all, and seconds."""

    max_rows: int
    max_cell_chars: int
    max_result_chars: int
    timeout_seconds: float

    def bounds(self) -> ResourceBounds:
        return ResourceBounds(max_execution_seconds=self.timeout_seconds)


@dataclass(frozen=True)
class ReadOutcome:
    columns: list[str]
    rows: list[tuple]
    more: bool   # the engine had more rows than max_rows


@dataclass(frozen=True)
class Observed:
    """What one statement of a change did inside the transaction."""

    index: int
    keyword: str
    kind: str
    rows: int
    every_row: bool   # a DELETE or UPDATE that changed every row of the table it names


@dataclass(frozen=True)
class WriteOutcome:
    observed: list[Observed]
    tables_before: list[str]
    tables_after: list[str]
    committed: bool


class BackendError(Exception):
    """A backend's refusal: a stable code, the reason in words, and the statement it concerns (0 for none)."""

    def __init__(self, code: str, message: str, *, statement: int = 0) -> None:
        super().__init__(message)
        self.code, self.message, self.statement = code, message, statement


Decide = Callable[[WriteOutcome], bool]


class Backend(Protocol):
    name: str

    def read(self, statement: str, params: Sequence[Any], limits: Limits) -> ReadOutcome: ...

    def explain(self, statement: Statement, params: Sequence[Any], limits: Limits) -> ReadOutcome: ...

    def write(self, statements: list[Statement], params: Sequence[Any], limits: Limits,
              decide: Decide) -> WriteOutcome: ...

    def tables(self, limits: Limits) -> list[dict[str, str]]: ...

    def columns(self, table: str, limits: Limits) -> list[dict[str, Any]] | None: ...

    def schema(self, limits: Limits) -> tuple[list[dict[str, Any]], list[str]]: ...


Factory = Callable[[str, ConnectionSettings], Backend]


def every_row(observed_rows: int, remaining: int, keyword: str) -> bool:
    """Whether a DELETE or UPDATE that changed ``observed_rows`` changed every row: a DELETE that empties a table it
    changed, or an UPDATE that changed at least as many rows as the table holds."""
    if observed_rows <= 0:
        return False
    return remaining == 0 if keyword == "DELETE" else observed_rows >= remaining


def split_table(table: str) -> tuple[str, str]:
    """``schema.name`` or ``name`` as (schema, name), with any double quotes removed; ("", "") if it is neither."""
    parts = table.split(".")
    if len(parts) > 2 or not all(p.strip() for p in parts):
        return "", ""
    clean = [p.strip().strip('"') for p in parts]
    return (clean[0], clean[1]) if len(clean) == 2 else ("", clean[0])


# -- SQLite ----------------------------------------------------------------------------------------------------------

_READS = frozenset({sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE})
_CHANGES = frozenset({sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                      sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_CREATE_VIEW,
                      sqlite3.SQLITE_CREATE_TEMP_TABLE, sqlite3.SQLITE_CREATE_TEMP_INDEX,
                      sqlite3.SQLITE_CREATE_TEMP_VIEW, sqlite3.SQLITE_ALTER_TABLE})
_DROPS = frozenset({sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_INDEX, sqlite3.SQLITE_DROP_VIEW,
                    sqlite3.SQLITE_DROP_TEMP_TABLE, sqlite3.SQLITE_DROP_TEMP_INDEX, sqlite3.SQLITE_DROP_TEMP_VIEW})
#: Functions that load code or reach files; refused wherever they are called.
_FUNCTIONS_REFUSED = frozenset({"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"})
_NAMES = ("SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' "
          "ORDER BY name")


def _authorizer(allowed: frozenset[int]) -> Callable[..., int]:
    def authorize(action: int, arg1: str | None, arg2: str | None, database: str | None, trigger: str | None) -> int:
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_DENY if (arg2 or "").lower() in _FUNCTIONS_REFUSED else sqlite3.SQLITE_OK
        return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY
    return authorize


class SQLiteBackend:
    """SQLite through the standard library's ``sqlite3``."""

    name = "sqlite"

    def __init__(self, connection: str, settings: ConnectionSettings) -> None:
        self.connection, self.settings = connection, settings
        self.path = settings.location()

    def _ensure(self) -> None:
        """Make the database file if it is not there yet (its folder too, under the G6 home only)."""
        if self.path is None or self.path.exists():
            return
        parent = self.path.parent
        if not parent.exists():
            home = g6_home().resolve()
            if home != parent.resolve() and home not in parent.resolve().parents:
                raise BackendError(CONNECT_FAILED, f"the folder for the {self.connection} database file does not "
                                                   "exist; G6 makes folders only under its own home")
            parent.mkdir(parents=True, exist_ok=True)
        sqlite3.connect(self.path).close()

    def _open(self, *, read: bool, limits: Limits, allowed: frozenset[int]) -> sqlite3.Connection:
        try:
            self._ensure()
            if self.path is None:
                con = sqlite3.connect(":memory:", isolation_level=None)
            elif read:
                con = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None)
            else:
                con = sqlite3.connect(self.path, isolation_level=None)
            con.execute("PRAGMA query_only = ON" if read else "PRAGMA foreign_keys = ON")
        except (sqlite3.Error, OSError) as exc:
            raise BackendError(CONNECT_FAILED, f"SQLite could not open the {self.connection} database: {exc}") from None
        usage, bounds = ResourceUsage(), limits.bounds()
        con.set_progress_handler(lambda: 1 if check(bounds, usage) else 0, 1000)
        con.set_authorizer(_authorizer(allowed))
        return con

    @staticmethod
    def _failed(exc: Exception, limits: Limits, statement: int = 0) -> BackendError:
        if isinstance(exc, sqlite3.OperationalError) and str(exc) == "interrupted":
            return BackendError(RESOURCE_LIMIT, f"the call ran past its time limit of {limits.timeout_seconds:g} "
                                                "seconds and was stopped", statement=statement)
        return BackendError(STATEMENT_FAILED, f"SQLite said: {exc}", statement=statement)

    def _select(self, statement: str, params: Sequence[Any], limits: Limits, allowed: frozenset[int]) -> ReadOutcome:
        con = self._open(read=True, limits=limits, allowed=allowed)
        try:
            cursor = con.execute(statement, tuple(params))
            columns = [d[0] for d in cursor.description or ()]
            rows = cursor.fetchmany(limits.max_rows + 1) if cursor.description else []
            return ReadOutcome(columns, [tuple(r) for r in rows[:limits.max_rows]], len(rows) > limits.max_rows)
        except (sqlite3.Error, sqlite3.Warning) as exc:
            raise self._failed(exc, limits) from None
        finally:
            con.close()

    def read(self, statement: str, params: Sequence[Any], limits: Limits) -> ReadOutcome:
        return self._select(statement, params, limits, _READS)

    def explain(self, statement: Statement, params: Sequence[Any], limits: Limits) -> ReadOutcome:
        # Planning a change needs its actions authorized; EXPLAIN QUERY PLAN runs none of them, on a connection that
        # cannot write anyway.
        return self._select("EXPLAIN QUERY PLAN " + statement.text, params, limits, _READS | _CHANGES | _DROPS)

    def write(self, statements: list[Statement], params: Sequence[Any], limits: Limits,
              decide: Decide) -> WriteOutcome:
        allowed = _READS | _CHANGES | (_DROPS if self.settings.allow_destructive else frozenset())
        con = self._open(read=False, limits=limits, allowed=allowed)
        try:
            con.set_authorizer(None)
            con.execute("BEGIN IMMEDIATE")
            before = [r[0] for r in con.execute(_NAMES)]
            con.set_authorizer(_authorizer(allowed))
            observed = [self._run(con, st, params if len(statements) == 1 else (), limits) for st in statements]
            after = [r[0] for r in con.execute(_NAMES)]
            con.set_authorizer(None)
            outcome = WriteOutcome(observed, before, after, committed=False)
            keep = decide(outcome)
            con.execute("COMMIT" if keep else "ROLLBACK")
            return replace(outcome, committed=keep)
        except (sqlite3.Error, sqlite3.Warning) as exc:
            raise self._failed(exc, limits) from None
        finally:
            con.close()   # an open transaction is rolled back

    def _run(self, con: sqlite3.Connection, st: Statement, params: Sequence[Any], limits: Limits) -> Observed:
        try:
            changed = max(con.execute(st.text, tuple(params)).rowcount, 0)
            every = False
            if st.keyword in ("DELETE", "UPDATE") and st.table:
                remaining = con.execute(f"SELECT count(*) FROM {st.table}").fetchone()[0]
                every = every_row(changed, remaining, st.keyword)
        except (sqlite3.Error, sqlite3.Warning) as exc:
            raise self._failed(exc, limits, st.index) from None
        return Observed(st.index, st.keyword, st.kind, changed, every)

    def tables(self, limits: Limits) -> list[dict[str, str]]:
        con = self._open(read=True, limits=limits, allowed=_READS)
        try:
            con.set_authorizer(None)
            return [{"schema": "main", "name": n, "kind": k} for n, k in con.execute(
                _NAMES.replace("SELECT name FROM", "SELECT name, type FROM"))]
        except sqlite3.Error as exc:
            raise self._failed(exc, limits) from None
        finally:
            con.close()

    def columns(self, table: str, limits: Limits) -> list[dict[str, Any]] | None:
        schema, name = split_table(table)
        if schema not in ("", "main") or not name or name.lower().startswith("sqlite_"):
            return None
        con = self._open(read=True, limits=limits, allowed=_READS)
        try:
            con.set_authorizer(None)
            if not con.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
                               (name,)).fetchone():
                return None
            return [{"name": r[0], "type": r[1], "notnull": bool(r[2]), "default": r[3], "pk": bool(r[4])}
                    for r in con.execute('SELECT name, type, "notnull", dflt_value, pk FROM pragma_table_info(?)',
                                         (name,))]
        except sqlite3.Error as exc:
            raise self._failed(exc, limits) from None
        finally:
            con.close()

    def schema(self, limits: Limits) -> tuple[list[dict[str, Any]], list[str]]:
        tables = self.tables(limits)
        described = [{**t, "columns": self.columns(t["name"], limits) or []} for t in tables]
        con = self._open(read=True, limits=limits, allowed=_READS)
        try:
            con.set_authorizer(None)
            ddl = [r[0] for r in con.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' "
                "ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'view' THEN 1 WHEN 'index' THEN 2 ELSE 3 END, name")]
        except sqlite3.Error as exc:
            raise self._failed(exc, limits) from None
        finally:
            con.close()
        return described, ddl


# -- the registry ----------------------------------------------------------------------------------------------------

def _duckdb(connection: str, settings: ConnectionSettings) -> Backend:
    from g6.integrations.database.duckdb_backend import DuckDBBackend

    return DuckDBBackend(connection, settings)


def _postgres(connection: str, settings: ConnectionSettings) -> Backend:
    from g6.integrations.database.postgres_backend import PostgresBackend

    return PostgresBackend(connection, settings)


#: Each backend's name and how to make it. Add one with :func:`register_backend`.
BACKENDS: dict[str, Factory] = {"sqlite": SQLiteBackend, "duckdb": _duckdb, "postgresql": _postgres}


def register_backend(name: str, factory: Factory) -> None:
    """Make ``factory(connection_name, settings)`` the backend for connections whose ``backend`` is ``name``."""
    if not name or not isinstance(name, str):
        raise ValueError("a backend needs a name")
    BACKENDS[name] = factory


def create(connection: str, settings: ConnectionSettings) -> Backend:
    """The backend for one connection. Raises :class:`BackendError` (an unknown backend, a driver not installed)."""
    factory = BACKENDS.get(settings.backend)
    if factory is None:
        raise BackendError(CONFIG_INVALID, f"the {connection} connection names a backend G6 does not have; known: "
                                           f"{', '.join(sorted(BACKENDS))}")
    return factory(connection, settings)


__all__ = ["BACKENDS", "Backend", "BackendError", "Decide", "Factory", "Limits", "Observed", "ReadOutcome",
           "SQLiteBackend", "WriteOutcome", "create", "every_row", "register_backend", "split_table"]
