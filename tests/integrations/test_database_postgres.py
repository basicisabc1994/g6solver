# SPDX-License-Identifier: Apache-2.0
"""The PostgreSQL backend, through pg8000 (BSD-3-Clause, behind the ``db`` extra), against a transport double: a fake
``pg8000`` that records every statement and answers like a server, so nothing reaches the network. What is checked is
what G6 sends and what it does with the answer: reads in a read-only transaction through a cursor that fetches one row
past the cap, every statement over the extended protocol (one statement per message), a change in one transaction,
rolled back unless the gate allows it, and a server that does not answer refused with install steps and no credential
in the reply."""
from __future__ import annotations

import json
import sys
import types

import pytest

from g6.core.outside import OUTSIDE_PROGRAM_MISSING
from g6.core.resources import RESOURCE_LIMIT
from g6.integrations.database import config, ops
from g6.integrations.database import envelope as E

SECRET = "hunter2-secret"
URL = f"postgresql://analyst:{SECRET}@db.internal.example:6543/sales"


class FakeServer:
    """What the fake pg8000 connects to: the statements it was sent, and how it answers."""

    def __init__(self) -> None:
        self.connects: list[dict] = []
        self.sent: list[tuple[str, tuple]] = []
        self.refuse_connect: Exception | None = None
        self.fail: dict[str, Exception] = {}   # a statement prefix -> the error the server raises for it
        self.counts: dict[str, int] = {"items": 3}
        self.rowcount = 1

    def answer(self, statement: str, vals: tuple) -> types.SimpleNamespace:
        self.sent.append((statement, vals))
        for prefix, error in self.fail.items():
            if statement.startswith(prefix):
                raise error
        if statement.startswith("FETCH"):
            n = int(statement.split()[2])
            rows = [[i, f"row {i}"] for i in range(1, 8)][:n]
            return types.SimpleNamespace(rows=rows, columns=[{"name": "id"}, {"name": "name"}], row_count=len(rows))
        if statement.startswith("SELECT count(*) FROM"):
            table = statement.rsplit(" ", 1)[1]
            return types.SimpleNamespace(rows=[[self.counts.get(table, 0)]], columns=[{"name": "count"}], row_count=1)
        if "information_schema.tables" in statement:
            return types.SimpleNamespace(rows=[["public", "items", "BASE TABLE"]],
                                         columns=[{"name": "s"}, {"name": "n"}, {"name": "k"}], row_count=1)
        if statement.startswith(("UPDATE", "DELETE", "INSERT")):
            return types.SimpleNamespace(rows=None, columns=None, row_count=self.rowcount)
        return types.SimpleNamespace(rows=None, columns=None, row_count=-1)


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> FakeServer:
    found = FakeServer()

    class DatabaseError(Exception):
        pass

    class InterfaceError(Exception):
        pass

    class Connection:
        def __init__(self, user: str, **kwargs: object) -> None:
            found.connects.append({"user": user, **kwargs})
            if found.refuse_connect is not None:
                raise found.refuse_connect

        def execute_unnamed(self, statement: str, vals: tuple = (), oids: tuple = (), stream: object = None):  # noqa: ANN201
            return found.answer(statement, tuple(vals))

        def run(self, sql: str, **params: object) -> None:
            raise AssertionError("G6 sends every statement over the extended protocol, never run()")

        def close(self) -> None:
            found.sent.append(("<close>", ()))

    package = types.ModuleType("pg8000")
    native = types.ModuleType("pg8000.native")
    exceptions = types.ModuleType("pg8000.exceptions")
    native.Connection = Connection
    exceptions.DatabaseError, exceptions.InterfaceError = DatabaseError, InterfaceError
    package.native, package.exceptions = native, exceptions
    for name, module in (("pg8000", package), ("pg8000.native", native), ("pg8000.exceptions", exceptions)):
        monkeypatch.setitem(sys.modules, name, module)
    found.DatabaseError, found.InterfaceError = DatabaseError, InterfaceError
    return found


def _conns(**extra: object) -> config.Connections:
    return config.from_mapping({"sales": {"backend": "postgresql", "url": URL, **extra}})


def _said(result) -> str:  # noqa: ANN001
    return json.dumps(result.model_dump(mode="json"))


def test_a_read_runs_in_a_read_only_transaction_through_a_capped_cursor(server: FakeServer) -> None:
    result = ops.query("SELECT id, name FROM items WHERE qty > $1", connections=_conns(), params=[1], max_rows=5)
    assert result.completion_state == "qualified-draft" and result.rows[0] == [1, "row 1"]
    assert len(result.rows) == 5 and result.truncated["rows"] is True
    sent = [s for s, _ in server.sent]
    assert sent[0] == "START TRANSACTION READ ONLY"
    assert sent[1] == "DECLARE g6_rows NO SCROLL CURSOR FOR SELECT id, name FROM items WHERE qty > $1"
    assert server.sent[1][1] == (1,) and sent[2] == "FETCH FORWARD 6 FROM g6_rows"
    assert sent[3] == "ROLLBACK" and sent[-1] == "<close>"
    [connect] = server.connects
    assert (connect["user"], connect["host"], connect["port"], connect["database"]) == (
        "analyst", "db.internal.example", 6543, "sales")
    assert connect["password"] == SECRET and connect["startup_params"]["default_transaction_read_only"] == "on"
    assert int(connect["startup_params"]["statement_timeout"]) > 0
    assert SECRET not in _said(result) and "db.internal.example" not in _said(result)


def test_a_change_is_one_transaction_rolled_back_unless_the_gate_allows_it(server: FakeServer) -> None:
    dry = ops.execute("UPDATE items SET qty = 0 WHERE id = 2", connections=_conns(reach="local"))
    assert dry.committed is False and dry.statements[0]["rows"] == 1 and dry.statements[0]["every_row"] is False
    sent = [s for s, _ in server.sent]
    assert sent[0] == "START TRANSACTION READ WRITE" and "UPDATE items SET qty = 0 WHERE id = 2" in sent
    assert "ROLLBACK" in sent and "COMMIT" not in sent
    assert "default_transaction_read_only" not in server.connects[0]["startup_params"]
    server.sent.clear()
    done = ops.execute("UPDATE items SET qty = 0 WHERE id = 2", connections=_conns(reach="local"), commit=True)
    assert done.committed is True and [s for s, _ in server.sent][-2] == "COMMIT"


def test_with_the_reach_undeclared_the_gate_holds_a_commit_to_a_server(server: FakeServer) -> None:
    held = ops.execute("UPDATE items SET qty = 0 WHERE id = 2", connections=_conns(), commit=True)
    assert (held.completion_state, held.code, held.committed) == ("blocked-escalated", E.GATE_HELD, False)
    assert held.gate["decision"] == "require_additional_information" and "reach" in held.reason
    assert "COMMIT" not in [s for s, _ in server.sent]


def test_a_failing_second_statement_rolls_the_first_back(server: FakeServer) -> None:
    server.fail["INSERT INTO nowhere"] = server.DatabaseError(
        {"S": "ERROR", "C": "42P01", "M": 'relation "nowhere" does not exist'})
    result = ops.execute("INSERT INTO items VALUES (4, 'fig', 1); INSERT INTO nowhere VALUES (1)",
                         connections=_conns(reach="local"), commit=True)
    assert (result.code, result.failed_statement, result.committed) == (E.STATEMENT_FAILED, 2, False)
    assert 'relation "nowhere" does not exist' in result.reason
    assert "COMMIT" not in [s for s, _ in server.sent] and "ROLLBACK" in [s for s, _ in server.sent]


def test_an_update_that_touches_every_row_is_destructive(server: FakeServer) -> None:
    server.rowcount = 3
    result = ops.execute("UPDATE items SET qty = 0 WHERE qty >= 0", connections=_conns(reach="local"), commit=True)
    assert (result.code, result.committed) == (E.DESTRUCTIVE_REFUSED, False)
    assert ("SELECT count(*) FROM items", ()) in server.sent and "COMMIT" not in [s for s, _ in server.sent]


@pytest.mark.parametrize(("statement", "code"), [
    ("WITH d AS (DELETE FROM items RETURNING 1) INSERT INTO log SELECT count(*) FROM d", E.DESTRUCTIVE_REFUSED),
    ("MERGE INTO items USING (SELECT 1 AS id) s ON items.id = s.id WHEN MATCHED THEN DELETE", E.DESTRUCTIVE_REFUSED),
    ("INSERT INTO items SELECT * FROM items ON CONFLICT (id) DO UPDATE SET qty = 0", E.DESTRUCTIVE_REFUSED),
    ("ALTER TABLE items ALTER COLUMN qty TYPE bigint USING 0", E.DESTRUCTIVE_REFUSED),
    ("INSERT INTO items VALUES (4, 'fig', 1) /* /* */ */", E.INPUT_INVALID),
    ("CREATE SCHEMA s GRANT USAGE ON SCHEMA s TO public", E.STATEMENT_REFUSED),
])
def test_a_change_postgresql_would_run_as_more_than_g6_read_is_refused_before_the_server(server: FakeServer,
                                                                                        statement: str,
                                                                                        code: str) -> None:
    # PostgreSQL runs the DELETE inside a WITH, a MERGE's DELETE, an upsert's UPDATE, a USING rewrite, a nested
    # comment as nested, and a GRANT given inside CREATE SCHEMA; each reached the server as an ordinary write
    result = ops.execute(statement, connections=_conns(reach="local"), commit=True)
    assert (result.completion_state, result.code, result.committed) == ("blocked-escalated", code, False), result.reason
    assert server.connects == [] and server.sent == []


def test_a_server_that_does_not_answer_is_refused_with_install_steps_and_no_address(server: FakeServer) -> None:
    server.refuse_connect = server.InterfaceError(
        f"Can't create a connection to host db.internal.example and port 6543 ({URL})")
    result = ops.query("SELECT 1", connections=_conns())
    assert (result.completion_state, result.code) == ("blocked-escalated", OUTSIDE_PROGRAM_MISSING)
    assert "PostgreSQL" in result.reason and "install" in result.reason.lower()
    assert SECRET not in _said(result) and "db.internal.example" not in _said(result) and URL not in _said(result)


def test_a_login_the_server_refuses_is_a_connection_failure_without_the_password(server: FakeServer) -> None:
    server.refuse_connect = server.DatabaseError(
        {"S": "FATAL", "C": "28P01", "M": f"password authentication failed for user analyst ({SECRET})"})
    result = ops.list_tables(connections=_conns())
    assert (result.completion_state, result.code) == ("blocked-escalated", E.CONNECT_FAILED)
    assert SECRET not in _said(result)


def test_a_statement_past_the_servers_time_limit_is_a_resource_limit(server: FakeServer) -> None:
    server.fail["DECLARE"] = server.DatabaseError(
        {"S": "ERROR", "C": "57014", "M": "canceling statement due to statement timeout"})
    result = ops.query("SELECT pg_sleep(100)", connections=_conns(timeout_seconds=1))
    assert (result.completion_state, result.code) == ("blocked-escalated", RESOURCE_LIMIT)
    assert server.connects[0]["startup_params"]["statement_timeout"] == "1000"


def test_a_tls_setting_in_the_url_asks_for_a_verified_connection(server: FakeServer) -> None:
    import ssl

    ops.query("SELECT 1", connections=config.from_mapping(
        {"sales": {"backend": "postgresql", "url": URL + "?sslmode=verify-full"}}))
    context = server.connects[0]["ssl_context"]
    assert isinstance(context, ssl.SSLContext) and context.verify_mode == ssl.CERT_REQUIRED
    server.connects.clear()
    ops.query("SELECT 1", connections=_conns())
    assert server.connects[0]["ssl_context"] is None


def test_a_list_of_tables_comes_from_the_information_schema(server: FakeServer) -> None:
    result = ops.list_tables(connections=_conns())
    assert result.tables == [{"schema": "public", "name": "items", "kind": "table"}]
