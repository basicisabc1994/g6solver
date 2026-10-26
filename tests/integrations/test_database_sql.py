# SPDX-License-Identifier: Apache-2.0
"""The statement splitter and classifier that run before any SQL reaches a database (g6.integrations.database.sql).

A semicolon inside a string, a quoted name or a comment never ends a statement (DATABASE-03: the archive split on
``;``), and every statement's kind comes from its own leading keyword, after a WITH clause, from an allowlist: anything
G6 does not know is refused, never run."""
from __future__ import annotations

import pytest

from g6.integrations.database import sql


def _kinds(script: str) -> list[tuple[str, str]]:
    return [(s.keyword, s.kind) for s in sql.parse(script)]


@pytest.mark.parametrize(("script", "texts"), [
    ("SELECT 1; SELECT 2", ["SELECT 1", "SELECT 2"]),
    ("INSERT INTO t VALUES ('a;b'); SELECT 2;", ["INSERT INTO t VALUES ('a;b')", "SELECT 2"]),
    ('SELECT "odd;name" FROM t', ['SELECT "odd;name" FROM t']),
    ("SELECT 1 -- a comment; DROP TABLE t\n", ["SELECT 1"]),
    ("SELECT 1 /* ; */ ; SELECT 2", ["SELECT 1", "SELECT 2"]),
    ("SELECT $body$ ; $body$", ["SELECT $body$ ; $body$"]),
    ("SELECT E'it\\'s; still a string'", ["SELECT E'it\\'s; still a string'"]),
    ("SELECT 'it''s; one'", ["SELECT 'it''s; one'"]),
    (";;  SELECT 1 ;; ", ["SELECT 1"]),
])
def test_a_semicolon_ends_a_statement_only_outside_quotes_and_comments(script: str, texts: list[str]) -> None:
    assert [s.text for s in sql.parse(script)] == texts


@pytest.mark.parametrize("script", ["SELECT 'never closed", "SELECT /* never closed", 'SELECT "never closed',
                                    "SELECT $x$ never closed", "SELECT (1", "SELECT 1)"])
def test_a_script_that_cannot_be_read_is_refused_whole(script: str) -> None:
    with pytest.raises(sql.SQLSyntaxError):
        sql.parse(script)


@pytest.mark.parametrize(("script", "keyword", "kind"), [
    ("SELECT * FROM t", "SELECT", "read"),
    ("select * from t", "SELECT", "read"),
    ("VALUES (1), (2)", "VALUES", "read"),
    ("WITH x AS (SELECT 1) SELECT * FROM x", "SELECT", "read"),
    ("WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT n FROM r", "SELECT", "read"),
    ("FROM 'data.parquet'", "FROM", "read"),
    ("SUMMARIZE SELECT 1", "SUMMARIZE", "read"),
    ("INSERT INTO t VALUES (1)", "INSERT", "write"),
    ("REPLACE INTO t VALUES (1)", "REPLACE", "destructive"),
    ("UPDATE t SET a = 1 WHERE id = 2", "UPDATE", "write"),
    ("DELETE FROM t WHERE id = 2", "DELETE", "write"),
    ("WITH old AS (SELECT id FROM t) DELETE FROM t WHERE id IN (SELECT id FROM old)", "DELETE", "write"),
    ("CREATE TABLE t (a INTEGER)", "CREATE", "write"),
    ("CREATE TEMP TABLE IF NOT EXISTS t (a INTEGER)", "CREATE", "write"),
    ("CREATE UNIQUE INDEX i ON t (a)", "CREATE", "write"),
    ("CREATE OR REPLACE VIEW v AS SELECT 1", "CREATE", "write"),
    ("ALTER TABLE t ADD COLUMN b TEXT", "ALTER", "write"),
    ("DELETE FROM t", "DELETE", "destructive"),
    ("delete from t returning id", "DELETE", "destructive"),
    ("UPDATE t SET a = 1", "UPDATE", "destructive"),
    ("UPDATE t SET a = (SELECT b FROM u WHERE u.id = t.id)", "UPDATE", "destructive"),  # its WHERE is the subquery's
    ("WITH x AS (SELECT 1 WHERE 1 = 1) DELETE FROM t", "DELETE", "destructive"),
    ("DROP TABLE t", "DROP", "destructive"),
    ("DROP VIEW IF EXISTS v", "DROP", "destructive"),
    ("TRUNCATE t", "TRUNCATE", "destructive"),
    ("ALTER TABLE t DROP COLUMN b", "ALTER", "destructive"),
    ("CREATE OR REPLACE TABLE t AS SELECT 1", "CREATE", "destructive"),
    ("ATTACH DATABASE 'x.db' AS x", "ATTACH", "refused"),
    ("DETACH x", "DETACH", "refused"),
    ("PRAGMA writable_schema = ON", "PRAGMA", "refused"),
    ("COPY t TO 'out.csv'", "COPY", "refused"),
    ("INSTALL httpfs", "INSTALL", "refused"),
    ("LOAD httpfs", "LOAD", "refused"),
    ("BEGIN", "BEGIN", "refused"),
    ("COMMIT", "COMMIT", "refused"),
    ("SET enable_external_access = true", "SET", "refused"),
    ("GRANT ALL ON t TO public", "GRANT", "refused"),
    ("VACUUM INTO 'copy.db'", "VACUUM", "refused"),
    ("EXPLAIN ANALYZE DELETE FROM t", "EXPLAIN", "refused"),
    ("CREATE TRIGGER g AFTER INSERT ON t BEGIN SELECT 1", "CREATE", "refused"),
    ("CREATE EXTENSION dblink", "CREATE", "refused"),
    ("DROP DATABASE shop", "DROP", "refused"),
    ("ALTER SYSTEM SET x = 1", "ALTER", "refused"),
    ("DO $$ BEGIN DELETE FROM t; END $$", "DO", "refused"),
    ("WITH x AS (SELECT 1)", "WITH", "refused"),
    ("(SELECT 1)", "(", "refused"),
])
def test_each_statement_is_classified_by_its_own_leading_keyword(script: str, keyword: str, kind: str) -> None:
    [statement] = sql.parse(script)
    assert (statement.keyword, statement.kind) == (keyword, kind), statement.why
    if kind in ("destructive", "refused"):
        assert statement.why


# -- the classifier fails closed: a change that can remove or overwrite rows is destructive however it is written -----

@pytest.mark.parametrize(("script", "keyword"), [
    ("WITH d AS (DELETE FROM items RETURNING 1) INSERT INTO log SELECT count(*) FROM d", "INSERT"),
    ("WITH d AS (UPDATE items SET qty = 0 RETURNING id) SELECT * FROM d", "SELECT"),
    ("WITH a AS (SELECT 1), b AS MATERIALIZED (INSERT INTO log VALUES (1) RETURNING 1) SELECT 1", "SELECT"),
    ("WITH insert AS (SELECT 1) DELETE FROM items", "DELETE"),   # a CTE named like a keyword is not the statement
    ("MERGE INTO items USING (SELECT 1 AS id) s ON items.id = s.id WHEN MATCHED THEN DELETE", "MERGE"),
    ("MERGE INTO items USING src ON items.id = src.id WHEN NOT MATCHED THEN INSERT VALUES (src.id)", "MERGE"),
    ("INSERT INTO items SELECT * FROM items ON CONFLICT (id) DO UPDATE SET qty = 0", "INSERT"),
    ("insert into items values (1, 'a', 0) on conflict do update set qty = 0", "INSERT"),
    ("REPLACE INTO items VALUES (1, 'a', 0)", "REPLACE"),
    ("INSERT OR REPLACE INTO items VALUES (1, 'a', 0)", "INSERT"),
    ("UPDATE OR REPLACE items SET id = 1 WHERE id = 2", "UPDATE"),
    ("ALTER TABLE items ALTER COLUMN qty TYPE BIGINT USING 0", "ALTER"),
    ("ALTER TABLE items ALTER qty SET DATA TYPE BIGINT", "ALTER"),
    ("UPDATE [items] SET qty = 0 WHERE 1", "UPDATE"),             # a target G6 cannot read
    ("DELETE FROM [items] WHERE id = 1", "DELETE"),
])
def test_a_change_that_can_remove_or_overwrite_rows_is_destructive_however_it_is_written(script: str,
                                                                                       keyword: str) -> None:
    [statement] = sql.parse(script)
    assert (statement.keyword, statement.kind) == (keyword, "destructive"), statement.why
    assert statement.why


@pytest.mark.parametrize("script", [
    "INSERT INTO items VALUES (1, 'a', 0) ON CONFLICT DO NOTHING",
    "INSERT OR IGNORE INTO items VALUES (1, 'a', 0)",
    "INSERT INTO items SELECT id, replace(name, 'a', 'b'), qty FROM old",
    "ALTER TABLE items ADD COLUMN type TEXT",
    "WITH x AS (SELECT replace(name, 'a', 'b') AS n FROM items) INSERT INTO log SELECT n FROM x",
])
def test_the_same_words_in_an_ordinary_change_keep_it_an_ordinary_change(script: str) -> None:
    assert sql.parse(script)[0].kind == "write"


@pytest.mark.parametrize("script", ["SELECT 1 /* a /* b */ c */", "INSERT INTO t VALUES (1) /* /* */ */",
                                    "/* one /* two */ SELECT 1"])
def test_a_comment_inside_a_comment_is_refused_whole(script: str) -> None:
    # PostgreSQL and DuckDB read /* */ inside /* */ as nested; SQLite and G6's reader stop at the first */, so G6
    # cannot be sure which text the engine runs
    with pytest.raises(sql.SQLSyntaxError, match="inside"):
        sql.parse(script)
    assert [s.text for s in sql.parse("SELECT 1 /* a */ /* b */ ; SELECT /* ** / */ 2")] == [
        "SELECT 1", "SELECT /* ** / */ 2"]


@pytest.mark.parametrize(("script", "kind"), [
    ("CREATE SCHEMA s GRANT USAGE ON SCHEMA s TO public", "refused"),
    ("CREATE SCHEMA s AUTHORIZATION joe GRANT ALL ON TABLES TO public", "refused"),
    ("CREATE SCHEMA s CREATE TABLE t (a INTEGER)", "refused"),
    ("CREATE SCHEMA s CREATE TRIGGER g AFTER INSERT ON t EXECUTE FUNCTION f()", "refused"),
    ("CREATE SCHEMA s", "write"),
    ("CREATE SCHEMA IF NOT EXISTS s", "write"),
    ("CREATE SCHEMA s AUTHORIZATION joe", "write"),
    ("CREATE SCHEMA AUTHORIZATION joe", "write"),
    ('CREATE SCHEMA IF NOT EXISTS "My schema" AUTHORIZATION "joe"', "write"),
    ("CREATE SCHEMA lake.s", "write"),
])
def test_create_schema_takes_a_name_and_an_owner_and_nothing_after_them(script: str, kind: str) -> None:
    [statement] = sql.parse(script)
    assert (statement.keyword, statement.kind) == ("CREATE", kind), statement.why


def test_a_keyword_inside_a_string_a_quoted_name_or_a_comment_is_not_a_keyword() -> None:
    assert _kinds("SELECT 'DELETE FROM t' AS a") == [("SELECT", "read")]
    assert _kinds('SELECT "drop" FROM t') == [("SELECT", "read")]
    assert _kinds("/* DROP TABLE t */ SELECT 1") == [("SELECT", "read")]
    assert _kinds("UPDATE t SET note = 'WHERE' ") == [("UPDATE", "destructive")]  # the WHERE is inside a string
    assert _kinds("UPDATE t SET [where] = 1") == [("UPDATE", "destructive")]      # and here it is a bracketed name


def test_the_target_table_of_a_delete_or_update_is_named_as_written() -> None:
    assert sql.parse("DELETE FROM main.items WHERE id = 1")[0].table == "main.items"
    assert sql.parse('UPDATE OR IGNORE "my items" SET a = 1 WHERE b = 2')[0].table == '"my items"'
    assert sql.parse("DELETE FROM ONLY items WHERE id = 1")[0].table == "items"
    assert sql.parse("INSERT INTO items VALUES (1)")[0].table == ""


def test_a_whole_script_keeps_its_order() -> None:
    script = "CREATE TABLE a (x INTEGER); INSERT INTO a VALUES (1); DELETE FROM a; ATTACH 'b.db' AS b"
    assert _kinds(script) == [("CREATE", "write"), ("INSERT", "write"), ("DELETE", "destructive"),
                              ("ATTACH", "refused")]
    assert [s.index for s in sql.parse(script)] == [1, 2, 3, 4]
