# SPDX-License-Identifier: Apache-2.0
"""DuckDB, the analytical backend for large datasets (the ``db`` extra; DuckDB is MIT and needs nothing else).

Every connection is opened with extension autoloading and autoinstalling off, then, before any of the caller's SQL:
the operator's ``data_dir`` (if any) becomes DuckDB's only allowed directory, external access is switched off (no file
outside that directory, no network, no extension), and the configuration is locked, so SQL cannot switch any of it
back. A file database is opened read-only for reads. DuckDB's own parser must agree with G6's classifier: one statement,
of a type the op runs (``SELECT`` for reads; ``INSERT``, ``UPDATE``, ``DELETE``, ``CREATE``, ``ALTER``, ``DROP`` and
``MERGE_INTO`` for changes). A timer interrupts a call at its time limit.
"""
from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

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
from g6.integrations.database.envelope import (
    CONNECT_FAILED,
    FILE_REFUSED,
    NOT_INSTALLED,
    STATEMENT_FAILED,
    STATEMENT_REFUSED,
)
from g6.integrations.database.sql import CHANGE, Statement

_READ_TYPES = frozenset({"SELECT"})
_CHANGE_TYPES = frozenset({"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "MERGE_INTO"})
_NAMES = ("SELECT schema_name, table_name, 'table' FROM duckdb_tables() WHERE NOT internal UNION ALL "
          "SELECT schema_name, view_name, 'view' FROM duckdb_views() WHERE NOT internal ORDER BY 1, 2")


class DuckDBBackend:
    """DuckDB through its Python package, with external access off."""

    name = "duckdb"

    def __init__(self, connection: str, settings: ConnectionSettings) -> None:
        try:
            import duckdb
        except ImportError:
            raise BackendError(NOT_INSTALLED, "DuckDB is not installed here. It comes with the db extra: "
                                              "pip install 'g6[db]'") from None
        self.duckdb, self.connection, self.settings = duckdb, connection, settings
        self.path = settings.location()
        self.data_dir = settings.data_directory()

    @contextmanager
    def _open(self, *, read: bool, limits: Limits) -> Iterator[Any]:
        duckdb = self.duckdb
        settings = {"autoload_known_extensions": False, "autoinstall_known_extensions": False}
        try:
            if self.path is None:
                con = duckdb.connect(":memory:", config=settings)
            else:
                if not self.path.exists():
                    if not self.path.parent.exists():
                        raise BackendError(CONNECT_FAILED, f"the folder for the {self.connection} database file "
                                                           "does not exist")
                    duckdb.connect(str(self.path), config=settings).close()
                con = duckdb.connect(str(self.path), read_only=read, config=settings)
            if self.data_dir is not None:
                con.execute("SET allowed_directories = ?", [[str(self.data_dir) + os.sep]])
            con.execute("SET enable_external_access = false")
            con.execute("SET lock_configuration = true")
        except duckdb.Error as exc:
            raise BackendError(CONNECT_FAILED, f"DuckDB could not open the {self.connection} database: {exc}") from None
        timer = threading.Timer(limits.timeout_seconds, con.interrupt)
        timer.daemon = True
        timer.start()
        try:
            yield con
        finally:
            timer.cancel()
            con.close()   # an open transaction is rolled back

    def _failed(self, exc: Exception, limits: Limits, statement: int = 0) -> BackendError:
        duckdb = self.duckdb
        if isinstance(exc, BackendError):
            return BackendError(exc.code, exc.message, statement=statement or exc.statement)
        if isinstance(exc, duckdb.InterruptException):
            return BackendError(RESOURCE_LIMIT, f"the call ran past its time limit of {limits.timeout_seconds:g} "
                                                "seconds and was stopped", statement=statement)
        if isinstance(exc, duckdb.PermissionException):
            return BackendError(FILE_REFUSED, f"DuckDB refused to reach a file, the network or an extension: {exc}",
                                statement=statement)
        return BackendError(STATEMENT_FAILED, f"DuckDB said: {exc}", statement=statement)

    def _check(self, con: Any, text: str, allowed: frozenset[str]) -> None:
        """DuckDB's own parser must find one statement of an allowed type."""
        found = con.extract_statements(text)
        if len(found) != 1:
            raise BackendError(STATEMENT_REFUSED, f"DuckDB reads this as {len(found)} statements; G6 runs one at a "
                                                  "time")
        kind = found[0].type.name
        if kind not in allowed:
            raise BackendError(STATEMENT_REFUSED, f"DuckDB reads this as a {kind} statement, which G6 does not run "
                                                  "here")

    def _guarded(self, run: Callable[[], Any], limits: Limits, statement: int = 0) -> Any:
        try:
            return run()
        except (self.duckdb.Error, BackendError) as exc:
            raise self._failed(exc, limits, statement) from None

    def read(self, statement: str, params: Sequence[Any], limits: Limits) -> ReadOutcome:
        with self._open(read=True, limits=limits) as con:
            def run() -> ReadOutcome:
                self._check(con, statement, _READ_TYPES)
                cursor = con.execute(statement, list(params) or None)
                columns = [d[0] for d in cursor.description or ()]
                rows = cursor.fetchmany(limits.max_rows + 1) if cursor.description else []
                return ReadOutcome(columns, [tuple(r) for r in rows[:limits.max_rows]], len(rows) > limits.max_rows)
            return self._guarded(run, limits)

    def explain(self, statement: Statement, params: Sequence[Any], limits: Limits) -> ReadOutcome:
        # DuckDB plans a change only on a connection that may write; EXPLAIN runs nothing, and the transaction is rolled
        # back on close anyway.
        changes = statement.keyword in CHANGE and not self.settings.read_only
        with self._open(read=not changes, limits=limits) as con:
            def run() -> ReadOutcome:
                self._check(con, statement.text, _READ_TYPES | (_CHANGE_TYPES if changes else frozenset()))
                if changes:
                    con.execute("BEGIN TRANSACTION")
                cursor = con.execute("EXPLAIN " + statement.text, list(params) or None)
                rows = cursor.fetchmany(limits.max_rows + 1)
                return ReadOutcome([d[0] for d in cursor.description or ()], [tuple(r) for r in rows[:limits.max_rows]],
                                   len(rows) > limits.max_rows)
            return self._guarded(run, limits)

    def _names(self, con: Any) -> list[str]:
        return [n if s == "main" else f"{s}.{n}" for s, n, _ in con.execute(_NAMES).fetchall()]

    def write(self, statements: list[Statement], params: Sequence[Any], limits: Limits,
              decide: Decide) -> WriteOutcome:
        with self._open(read=False, limits=limits) as con:
            self._guarded(lambda: con.execute("BEGIN TRANSACTION"), limits)
            before = self._guarded(lambda: self._names(con), limits)
            observed = [self._run(con, st, params if len(statements) == 1 else (), limits) for st in statements]
            after = self._guarded(lambda: self._names(con), limits)
            outcome = WriteOutcome(observed, before, after, committed=False)
            keep = decide(outcome)
            self._guarded(lambda: con.execute("COMMIT" if keep else "ROLLBACK"), limits)
            return replace(outcome, committed=keep)

    def _run(self, con: Any, st: Statement, params: Sequence[Any], limits: Limits) -> Observed:
        def run() -> Observed:
            self._check(con, st.text, _CHANGE_TYPES)
            cursor = con.execute(st.text, list(params) or None)
            changed = 0
            if st.keyword in CHANGE and cursor.description:
                rows = cursor.fetchall()
                counted = len(cursor.description) == 1 and cursor.description[0][0] == "Count" and len(rows) == 1
                changed = int(rows[0][0]) if counted else len(rows)
            every = False
            if st.keyword in ("DELETE", "UPDATE") and st.table:
                remaining = con.execute(f"SELECT count(*) FROM {st.table}").fetchone()[0]
                every = every_row(changed, int(remaining), st.keyword)
            return Observed(st.index, st.keyword, st.kind, changed, every)
        return self._guarded(run, limits, st.index)

    def tables(self, limits: Limits) -> list[dict[str, str]]:
        with self._open(read=True, limits=limits) as con:
            rows = self._guarded(lambda: con.execute(_NAMES).fetchall(), limits)
        return [{"schema": s, "name": n, "kind": k} for s, n, k in rows]

    def columns(self, table: str, limits: Limits) -> list[dict[str, Any]] | None:
        schema, name = split_table(table)
        if not name:
            return None
        with self._open(read=True, limits=limits) as con:
            def run() -> list[dict[str, Any]] | None:
                found = con.execute("SELECT DISTINCT schema_name FROM duckdb_columns() WHERE NOT internal AND "
                                    "table_name = ? AND (? = '' OR schema_name = ?)", [name, schema, schema]).fetchall()
                if not found:
                    return None
                if len(found) > 1:
                    raise BackendError(STATEMENT_FAILED, f"a table called {name} is in more than one schema "
                                                         f"({', '.join(sorted(r[0] for r in found))}); name it as "
                                                         "schema.table")
                where = found[0][0]
                keys = {c for (cols,) in con.execute(
                    "SELECT constraint_column_names FROM duckdb_constraints() WHERE constraint_type = 'PRIMARY KEY' "
                    "AND schema_name = ? AND table_name = ?", [where, name]).fetchall() for c in cols}
                return [{"name": c, "type": t, "notnull": not nullable, "default": d, "pk": c in keys}
                        for c, t, nullable, d in con.execute(
                            "SELECT column_name, data_type, is_nullable, column_default FROM duckdb_columns() "
                            "WHERE schema_name = ? AND table_name = ? ORDER BY column_index", [where, name]).fetchall()]
            return self._guarded(run, limits)

    def schema(self, limits: Limits) -> tuple[list[dict[str, Any]], list[str]]:
        tables = self.tables(limits)
        described = [{**t, "columns": self.columns(f"{t['schema']}.{t['name']}", limits) or []} for t in tables]
        with self._open(read=True, limits=limits) as con:
            ddl = self._guarded(lambda: [r[0] for r in con.execute(
                "SELECT sql FROM duckdb_tables() WHERE NOT internal AND sql IS NOT NULL UNION ALL "
                "SELECT sql FROM duckdb_views() WHERE NOT internal AND sql IS NOT NULL UNION ALL "
                "SELECT sql FROM duckdb_indexes() WHERE sql IS NOT NULL").fetchall()], limits)
        return described, ddl


__all__ = ["DuckDBBackend"]
