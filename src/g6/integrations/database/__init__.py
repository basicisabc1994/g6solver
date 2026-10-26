# SPDX-License-Identifier: Apache-2.0
"""g6.integrations.database — query and change a database the server's operator declares, reads on a connection that cannot write, changes dry-run first.

An extension in the integrations family. The owner, 2026-10-20: "an incredibly important component", for storing and
manipulating data, reached over MCP, for big data and complex datasets.

Capability label: ``implemented``.

    from g6.integrations.database import config, ops
    shop = config.from_mapping({"shop": {"backend": "sqlite", "path": "/data/shop.sqlite"}})  # the operator's
    ops.query("SELECT name FROM items WHERE qty > ?", params=[1], connections=shop).rows
    dry = ops.execute("DELETE FROM items WHERE qty = 0", connections=shop)    # rolled back: dry.statements says
    ops.execute("DELETE FROM items WHERE qty = 0", connections=shop, commit=True)  # what changed; the gate decides

What it is:

* six ops (:mod:`~g6.integrations.database.ops`): ``query`` (one read, on a connection that cannot write, with a row
  cap, a cell cap, a size cap and a time limit, every cut said), ``explain`` (the engine's plan; nothing runs),
  ``execute`` (a script of changes in one transaction, a dry run unless ``commit`` is true and ``g6.action_gating``
  allows it), ``list_tables``, ``describe_table`` and ``get_schema``;
* which database is reached is the operator's choice (:mod:`~g6.integrations.database.config`): connections are
  declared in ``G6_DATABASE_CONNECTIONS`` or a file under the G6 home, an agent only names one, and with nothing
  declared there is one scratch SQLite database under the G6 home;
* a classifier (:mod:`~g6.integrations.database.sql`) that splits a script outside quotes and comments and allows
  only the statements it knows, then each engine's own guard: SQLite's read-only connection and authorizer
  (no ATTACH, PRAGMA or load_extension), DuckDB with external access off outside the operator's data directory, and
  PostgreSQL in a read-only transaction;
* destructive statements (DROP, TRUNCATE, ALTER ... DROP, a DELETE or UPDATE with no WHERE, or one the dry run finds
  changing every row) need the operator's ``allow_destructive``; a caller cannot supply it;
* backends registered by name (:mod:`~g6.integrations.database.backends`): SQLite from the standard library, and,
  with the ``db`` extra, DuckDB for large datasets and PostgreSQL through pg8000.

The best state is ``qualified-draft``: what a database returned is data, and nothing here checks that it is right.
``DatabaseBlock`` makes the ops callable through the registry; ``execute`` is sensitive.

Not ported from the archive's ``database``, and why:

* the Redis, MongoDB and Neo4j adapters, ERD rendering, DDD entities, sessions, migrations, pool metrics and the
  scaffold: out of the port plan's small core;
* the model-assisted schema design, which ran the claude command line by subprocess (DATABASE-05);
* ``check_normalization``, which reported every table compliant and in fifth normal form (DATABASE-01);
* psycopg2, which is LGPL (DATABASE-08), and the liveness probe labelled verified (DATABASE-06).
"""
from g6.integrations.database import backends, config, ops, sql
from g6.integrations.database.backends import BACKENDS, register_backend
from g6.integrations.database.config import ConnectionSettings, Connections
from g6.integrations.database.database_block import SENSITIVE_OPS, DatabaseBlock, DatabaseInput
from g6.integrations.database.envelope import DatabaseResult
from g6.integrations.database.postgres_backend import POSTGRES

__g6_component__ = True
__g6_block__ = "DatabaseBlock"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # it changes data
__g6_requires__: tuple[str, ...] = ("action_gating",)
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("unsafe_db_access",)
__g6_introduces__: tuple[str, ...] = ("prompt_injection",)
__g6_outside__ = (POSTGRES,)
__g6_extras__: dict[str, tuple[str, ...]] = {"db": ("duckdb", "pg8000")}

__all__ = ["BACKENDS", "ConnectionSettings", "Connections", "DatabaseBlock", "DatabaseInput", "DatabaseResult",
           "backends", "config", "ops", "register_backend", "sql"]
