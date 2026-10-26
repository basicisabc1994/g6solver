# SPDX-License-Identifier: Apache-2.0
"""The DuckDB backend, for large datasets, behind the ``db`` extra: external access is off, so the only files SQL can
read are inside the data directory the operator declares, nothing reaches the network, no extension loads, and SQL
cannot switch any of that back on. Skipped where DuckDB is not installed; ``test_database_surface`` covers that case."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

duckdb = pytest.importorskip("duckdb")

from g6.core.resources import RESOURCE_LIMIT  # noqa: E402
from g6.integrations.database import backends, config, ops, sql  # noqa: E402
from g6.integrations.database import envelope as E  # noqa: E402

ITEMS = [(1, "apple", 3), (2, "pear", 0), (3, "plum", 7)]
LIMITS = backends.Limits(max_rows=100, max_cell_chars=1000, max_result_chars=100_000, timeout_seconds=10)


@pytest.fixture
def lake(tmp_path: Path) -> SimpleNamespace:
    data = tmp_path / "data"
    data.mkdir()
    (data / "sales.csv").write_text("region,amount\nnorth,10\nsouth,32\n", encoding="utf-8")
    (tmp_path / "private.csv").write_text("secret\n42\n", encoding="utf-8")
    db = tmp_path / "lake.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL, qty INTEGER)")
    con.execute("INSERT INTO items VALUES (1, 'apple', 3), (2, 'pear', 0), (3, 'plum', 7)")
    con.close()

    def conns(**extra: object) -> config.Connections:
        return config.from_mapping({"lake": {"backend": "duckdb", "path": str(db), "data_dir": str(data), **extra}})

    def rows() -> list[tuple]:
        con = duckdb.connect(str(db), read_only=True)
        try:
            return con.execute("SELECT id, name, qty FROM items ORDER BY id").fetchall()
        finally:
            con.close()

    return SimpleNamespace(tmp=tmp_path, data=data, db=db, conns=conns, rows=rows)


def _backend(lake: SimpleNamespace, **extra: object):  # noqa: ANN202
    return backends.create("lake", lake.conns(**extra).settings["lake"])


def test_a_file_inside_the_data_directory_can_be_read(lake) -> None:
    csv = (lake.data / "sales.csv").as_posix()
    result = ops.query(f"SELECT region, amount FROM read_csv('{csv}') ORDER BY region", connections=lake.conns())
    assert result.completion_state == "qualified-draft" and result.rows == [["north", 10], ["south", 32]]


@pytest.mark.parametrize("where", ["{tmp}/private.csv", "{data}/../private.csv", "https://example.com/sales.csv"])
def test_a_file_outside_the_data_directory_or_on_the_network_is_refused(lake, where: str) -> None:
    target = where.format(tmp=lake.tmp.as_posix(), data=lake.data.as_posix())
    for statement in (f"SELECT * FROM read_csv('{target}')", f"FROM '{target}'", f"SELECT * FROM read_text('{target}')"):
        result = ops.query(statement, connections=lake.conns())
        assert (result.completion_state, result.code) == ("blocked-escalated", E.FILE_REFUSED), statement
        assert "42" not in json.dumps(result.model_dump(mode="json"))


def test_with_no_data_directory_no_file_can_be_read(lake) -> None:
    conns = config.from_mapping({"lake": {"backend": "duckdb", "path": str(lake.db)}})
    csv = (lake.data / "sales.csv").as_posix()
    assert ops.query(f"SELECT * FROM read_csv('{csv}')", connections=conns).code == E.FILE_REFUSED


@pytest.mark.parametrize("statement", ["INSTALL httpfs", "LOAD httpfs", "SET enable_external_access = true",
                                       "COPY items TO '{data}/out.csv'", "ATTACH '{tmp}/other.duckdb' AS other",
                                       "EXPORT DATABASE '{data}/dump'", "PRAGMA enable_external_access = true"])
def test_duckdbs_own_parser_refuses_what_the_classifier_would(lake, statement: str) -> None:
    # DuckDB rewrites a PRAGMA that only reads (PRAGMA version) into a SELECT, which is harmless; one that changes a
    # setting it reads as SET, and refuses.
    text = statement.format(data=lake.data.as_posix(), tmp=lake.tmp.as_posix())
    assert sql.parse(text)[0].kind == "refused"
    with pytest.raises(backends.BackendError) as on_read:
        _backend(lake).read(text, (), LIMITS)
    disguised = sql.Statement(index=1, text=text, keyword="INSERT", kind="write")
    with pytest.raises(backends.BackendError) as on_write:
        _backend(lake, allow_destructive=True).write([disguised], (), LIMITS, lambda observed: True)
    assert on_read.value.code in (E.STATEMENT_REFUSED, E.FILE_REFUSED)
    assert on_write.value.code in (E.STATEMENT_REFUSED, E.FILE_REFUSED)
    assert not (lake.data / "out.csv").exists() and not (lake.data / "dump").exists()
    assert not (lake.tmp / "other.duckdb").exists()


def test_a_write_sent_to_query_is_refused_and_the_read_connection_cannot_write(lake) -> None:
    assert ops.query("DELETE FROM items WHERE id = 1", connections=lake.conns()).code == E.STATEMENT_REFUSED
    with pytest.raises(backends.BackendError):
        _backend(lake).read("DELETE FROM items WHERE id = 1", (), LIMITS)
    assert lake.rows() == ITEMS


def test_a_failing_second_statement_leaves_the_database_unchanged(lake) -> None:
    script = "INSERT INTO items VALUES (4, 'fig', 1); INSERT INTO nowhere VALUES (1)"
    result = ops.execute(script, connections=lake.conns(), commit=True)
    assert (result.code, result.failed_statement, result.committed) == (E.STATEMENT_FAILED, 2, False)
    assert lake.rows() == ITEMS


def test_execute_dry_runs_then_commits_through_the_gate(lake) -> None:
    dry = ops.execute("UPDATE items SET qty = 9 WHERE id = 2", connections=lake.conns())
    assert (dry.committed, dry.statements[0]["rows"], dry.statements[0]["every_row"]) == (False, 1, False)
    assert lake.rows() == ITEMS
    done = ops.execute("UPDATE items SET qty = 9 WHERE id = 2", connections=lake.conns(), commit=True)
    assert done.committed is True and lake.rows()[1] == (2, "pear", 9)


def test_a_where_clause_that_matches_every_row_is_destructive(lake) -> None:
    result = ops.execute("DELETE FROM items WHERE id > 0", connections=lake.conns(), commit=True)
    assert (result.code, result.committed) == (E.DESTRUCTIVE_REFUSED, False) and lake.rows() == ITEMS


def _columns(lake: SimpleNamespace) -> list[str]:
    con = duckdb.connect(str(lake.db), read_only=True)
    try:
        return [r[0] for r in con.execute("DESCRIBE items").fetchall()]
    finally:
        con.close()


@pytest.mark.parametrize("statement", [
    "MERGE INTO items USING (SELECT 1 AS id) s ON items.id = s.id WHEN MATCHED THEN DELETE",
    "INSERT INTO items SELECT * FROM items ON CONFLICT (id) DO UPDATE SET qty = 0",
    "INSERT OR REPLACE INTO items VALUES (2, 'x', 0)",
    "ALTER TABLE items ALTER qty TYPE INTEGER USING 0",
    "WITH insert AS (SELECT 1) DELETE FROM items",
])
def test_a_merge_an_upsert_or_a_retyped_column_is_destructive_on_duckdb(lake, statement: str) -> None:
    # each reached the gate as an ordinary write and was committed with allow_destructive off
    result = ops.execute(statement, connections=lake.conns(), commit=True)
    assert (result.completion_state, result.code, result.committed) == (
        "blocked-escalated", E.DESTRUCTIVE_REFUSED, False), result.reason
    assert lake.rows() == ITEMS


def test_a_comment_inside_a_comment_is_refused_before_duckdb_reads_it(lake) -> None:
    # DuckDB nests /* */, G6's reader does not, so G6 refuses the comment rather than guess where it ends
    result = ops.execute("INSERT INTO items VALUES (4, 'fig', 1) /* /* */ */", connections=lake.conns(), commit=True)
    assert (result.completion_state, result.code, result.committed) == ("blocked-escalated", E.INPUT_INVALID, False)
    assert lake.rows() == ITEMS and _columns(lake) == ["id", "name", "qty"]


def test_list_describe_schema_and_explain(lake) -> None:
    assert ops.list_tables(connections=lake.conns()).tables == [{"schema": "main", "name": "items", "kind": "table"}]
    columns = ops.describe_table("items", connections=lake.conns()).columns
    assert [c["name"] for c in columns] == ["id", "name", "qty"] and columns[1]["notnull"] is True
    schema = ops.get_schema(connections=lake.conns())
    assert [t["name"] for t in schema.tables] == ["items"] and "CREATE TABLE items" in schema.ddl[0]
    assert ops.describe_table("nowhere", connections=lake.conns()).code == E.UNKNOWN_TABLE
    plan = ops.explain("SELECT * FROM items WHERE id = 2", connections=lake.conns())
    assert plan.completion_state == "qualified-draft" and plan.rows
    change = ops.explain("DELETE FROM items WHERE id = 2", connections=lake.conns())
    assert change.completion_state == "qualified-draft" and lake.rows() == ITEMS


def test_an_in_memory_database_queries_the_data_directory(lake) -> None:
    conns = config.from_mapping({"files": {"backend": "duckdb", "path": ":memory:", "data_dir": str(lake.data)}})
    csv = (lake.data / "sales.csv").as_posix()
    result = ops.query(f"SELECT sum(amount) AS total FROM '{csv}'", connections=conns)
    assert result.rows == [[42]]


def test_a_query_past_its_time_limit_is_interrupted(lake) -> None:
    slow = "SELECT count(*) FROM range(100000000) a, range(100000) b WHERE a.range + b.range = -1"
    result = ops.query(slow, connections=lake.conns(timeout_seconds=0.5))
    assert (result.completion_state, result.code) == ("blocked-escalated", RESOURCE_LIMIT)
