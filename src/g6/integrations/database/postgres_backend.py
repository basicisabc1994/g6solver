# SPDX-License-Identifier: Apache-2.0
"""PostgreSQL through pg8000, a pure-Python driver (BSD-3-Clause; it needs scramp, MIT-0, and python-dateutil,
Apache-2.0 or BSD), behind the ``db`` extra. psycopg2 and psycopg 3 are LGPL, so G6 does not use them.

The server is a program the person runs (:data:`POSTGRES`); G6 finds it by connecting to the address the operator's
``url`` names when an op runs, and a server that does not answer is refused with ``G6_E_OUTSIDE_PROGRAM_MISSING`` and
the install steps, never a stand-in. Every statement goes over the extended protocol, which takes one statement per
message. Reads open the session with ``default_transaction_read_only`` on and run in ``START TRANSACTION READ ONLY``,
through a cursor that fetches one row past the cap; changes run in ``START TRANSACTION READ WRITE`` and are rolled back
unless the op decides to commit. ``statement_timeout`` is the call's time limit. The server's own permissions still
apply: a role that may only read is the strongest guard.
"""
from __future__ import annotations

import ssl
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from g6.core.outside import OUTSIDE_PROGRAM_MISSING, OutsideProgram
from g6.core.resources import RESOURCE_LIMIT
from g6.integrations.database.backends import (
    BackendError,
    Decide,
    Limits,
    Observed,
    ReadOutcome,
    WriteOutcome,
    every_row,
    split_table,
)
from g6.integrations.database.config import ConnectionSettings
from g6.integrations.database.envelope import CONFIG_INVALID, CONNECT_FAILED, NOT_INSTALLED, STATEMENT_FAILED
from g6.integrations.database.sql import Statement

#: The server a postgresql connection reaches. G6 never installs, bundles or stands in for it.
POSTGRES = OutsideProgram(
    name="PostgreSQL",
    purpose="the database server a postgresql connection names; G6 connects to the address in the operator's url "
            "when an op runs",
    install="Install PostgreSQL from postgresql.org (or use a server you already run), start it, and declare it as a "
            "postgresql connection in G6_DATABASE_CONNECTIONS with the db extra installed: pip install 'g6[db] @ git+https://github.com/basicisabc1994/g6solver'.",
    licence="PostgreSQL Licence (permissive)", command="postgres")

_TLS = ("require", "verify-ca", "verify-full")
_TIMED_OUT, _READ_ONLY_STATE = "57014", "25006"
_NAMES = ("SELECT table_schema, table_name, table_type FROM information_schema.tables "
          "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') ORDER BY 1, 2")
_KINDS = {"BASE TABLE": "table", "VIEW": "view", "FOREIGN": "foreign table", "LOCAL TEMPORARY": "temporary table"}


def _said(exc: Exception) -> tuple[str, str]:
    """(SQLSTATE code, message) from a pg8000 error; a server error carries a dict of its fields."""
    first = exc.args[0] if exc.args else ""
    if isinstance(first, dict):
        return str(first.get("C", "")), str(first.get("M", "")) or "the server gave no message"
    return "", str(first) or type(exc).__name__


class PostgresBackend:
    """PostgreSQL through pg8000."""

    name = "postgresql"

    def __init__(self, connection: str, settings: ConnectionSettings) -> None:
        try:
            import pg8000.exceptions
            import pg8000.native
        except ImportError:
            raise BackendError(NOT_INSTALLED, "pg8000, the PostgreSQL driver, is not installed here. It comes with the "
                                              "db extra: pip install 'g6[db] @ git+https://github.com/basicisabc1994/g6solver'") from None
        self.pg, self.connection, self.settings = pg8000, connection, settings
        parts = urlsplit(settings.url)
        try:
            port = parts.port or 5432
        except ValueError:
            raise BackendError(CONFIG_INVALID, f"the {connection} connection's url has a port that is not a "
                                               "number") from None
        if not parts.hostname or not parts.username:
            raise BackendError(CONFIG_INVALID, f"the {connection} connection's url must name a user and a host")
        mode = (parse_qs(parts.query).get("sslmode") or [""])[0]
        self.login = {"user": unquote(parts.username), "host": parts.hostname, "port": port,
                      "database": unquote(parts.path.lstrip("/")) or None,
                      "password": unquote(parts.password) if parts.password else None,
                      "ssl_context": ssl.create_default_context() if mode in _TLS else None}

    @contextmanager
    def _open(self, *, read: bool, limits: Limits) -> Iterator[Any]:
        params = {"statement_timeout": str(max(1, int(limits.timeout_seconds * 1000)))}
        if read:
            params["default_transaction_read_only"] = "on"
        try:
            con = self.pg.native.Connection(**self.login, timeout=limits.timeout_seconds + 10, application_name="g6",
                                            startup_params=params)
        except self.pg.exceptions.DatabaseError as exc:
            raise BackendError(CONNECT_FAILED, f"the server refused the {self.connection} connection: "
                                               f"{_said(exc)[1]}") from None
        except (self.pg.exceptions.InterfaceError, OSError) as exc:
            raise BackendError(OUTSIDE_PROGRAM_MISSING, f"{POSTGRES.name} ({POSTGRES.purpose}) is not ready: nothing "
                                                        f"answered at the address the {self.connection} connection "
                                                        f"names ({type(exc).__name__}). G6 never installs it; install "
                                                        f"it yourself: {POSTGRES.install}") from None
        try:
            yield con
        finally:
            try:
                con.close()   # an open transaction is rolled back
            except Exception:  # noqa: BLE001 - a connection that is already gone needs no closing
                pass

    def _send(self, con: Any, statement: str, vals: Sequence[Any] = (), *, limits: Limits, index: int = 0) -> Any:
        try:
            return con.execute_unnamed(statement, vals=tuple(vals))
        except self.pg.exceptions.DatabaseError as exc:
            code, message = _said(exc)
            if code == _TIMED_OUT:
                raise BackendError(RESOURCE_LIMIT, f"the call ran past its time limit of {limits.timeout_seconds:g} "
                                                   "seconds and the server stopped it", statement=index) from None
            raise BackendError(STATEMENT_FAILED, f"PostgreSQL said: {message}", statement=index) from None
        except (self.pg.exceptions.InterfaceError, OSError) as exc:
            raise BackendError(STATEMENT_FAILED, f"the connection to PostgreSQL failed ({type(exc).__name__}); "
                                                 "nothing was committed", statement=index) from None

    def _rows(self, con: Any, statement: str, vals: Sequence[Any] = (), *, limits: Limits) -> list[tuple]:
        return [tuple(r) for r in self._send(con, statement, vals, limits=limits).rows or ()]

    def read(self, statement: str, params: Sequence[Any], limits: Limits) -> ReadOutcome:
        with self._open(read=True, limits=limits) as con:
            self._send(con, "START TRANSACTION READ ONLY", limits=limits)
            self._send(con, "DECLARE g6_rows NO SCROLL CURSOR FOR " + statement, params, limits=limits)
            fetched = self._send(con, f"FETCH FORWARD {limits.max_rows + 1} FROM g6_rows", limits=limits)
            rows = [tuple(r) for r in fetched.rows or ()]
            columns = [str(c.get("name", "")) for c in fetched.columns or ()]
            self._send(con, "ROLLBACK", limits=limits)
        return ReadOutcome(columns, rows[:limits.max_rows], len(rows) > limits.max_rows)

    def explain(self, statement: Statement, params: Sequence[Any], limits: Limits) -> ReadOutcome:
        with self._open(read=True, limits=limits) as con:
            self._send(con, "START TRANSACTION READ ONLY", limits=limits)
            planned = self._send(con, "EXPLAIN " + statement.text, params, limits=limits)
            rows = [tuple(r) for r in planned.rows or ()]
            columns = [str(c.get("name", "")) for c in planned.columns or ()]
            self._send(con, "ROLLBACK", limits=limits)
        return ReadOutcome(columns, rows[:limits.max_rows], len(rows) > limits.max_rows)

    def _names(self, con: Any, limits: Limits) -> list[str]:
        return [n if s == "public" else f"{s}.{n}" for s, n, _ in self._rows(con, _NAMES, limits=limits)]

    def write(self, statements: list[Statement], params: Sequence[Any], limits: Limits,
              decide: Decide) -> WriteOutcome:
        with self._open(read=False, limits=limits) as con:
            self._send(con, "START TRANSACTION READ WRITE", limits=limits)
            try:
                before = self._names(con, limits)
                observed = []
                for st in statements:
                    done = self._send(con, st.text, params if len(statements) == 1 else (), limits=limits,
                                      index=st.index)
                    changed = max(int(done.row_count or 0), 0)
                    every = False
                    if st.keyword in ("DELETE", "UPDATE") and st.table:
                        counted = self._send(con, f"SELECT count(*) FROM {st.table}", limits=limits, index=st.index)
                        every = every_row(changed, int(counted.rows[0][0]), st.keyword)
                    observed.append(Observed(st.index, st.keyword, st.kind, changed, every))
                after = self._names(con, limits)
            except BackendError:
                try:
                    con.execute_unnamed("ROLLBACK")
                except Exception:  # noqa: BLE001 - closing the connection rolls back too
                    pass
                raise
            outcome = WriteOutcome(observed, before, after, committed=False)
            keep = decide(outcome)
            self._send(con, "COMMIT" if keep else "ROLLBACK", limits=limits)
        return replace(outcome, committed=keep)

    def tables(self, limits: Limits) -> list[dict[str, str]]:
        with self._open(read=True, limits=limits) as con:
            rows = self._rows(con, _NAMES, limits=limits)
        return [{"schema": s, "name": n, "kind": _KINDS.get(k, k.lower())} for s, n, k in rows]

    def columns(self, table: str, limits: Limits) -> list[dict[str, Any]] | None:
        schema, name = split_table(table)
        if not name:
            return None
        with self._open(read=True, limits=limits) as con:
            found = self._rows(con, "SELECT DISTINCT table_schema FROM information_schema.columns WHERE table_name = $1 "
                                    "AND ($2 = '' OR table_schema = $2) AND table_schema NOT IN "
                                    "('pg_catalog', 'information_schema')", (name, schema), limits=limits)
            if not found:
                return None
            if len(found) > 1:
                raise BackendError(STATEMENT_FAILED, f"a table called {name} is in more than one schema "
                                                     f"({', '.join(sorted(r[0] for r in found))}); name it as "
                                                     "schema.table")
            where = found[0][0]
            keys = {r[0] for r in self._rows(
                con, "SELECT k.column_name FROM information_schema.table_constraints c JOIN "
                     "information_schema.key_column_usage k ON c.constraint_name = k.constraint_name AND "
                     "c.table_schema = k.table_schema WHERE c.constraint_type = 'PRIMARY KEY' AND c.table_schema = $1 "
                     "AND c.table_name = $2", (where, name), limits=limits)}
            rows = self._rows(con, "SELECT column_name, data_type, is_nullable, column_default FROM "
                                   "information_schema.columns WHERE table_schema = $1 AND table_name = $2 "
                                   "ORDER BY ordinal_position", (where, name), limits=limits)
        return [{"name": c, "type": t, "notnull": n == "NO", "default": d, "pk": c in keys} for c, t, n, d in rows]

    def schema(self, limits: Limits) -> tuple[list[dict[str, Any]], list[str]]:
        tables = self.tables(limits)
        return [{**t, "columns": self.columns(f"{t['schema']}.{t['name']}", limits) or []} for t in tables], []


__all__ = ["POSTGRES", "PostgresBackend"]
