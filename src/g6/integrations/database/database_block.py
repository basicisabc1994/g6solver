# SPDX-License-Identifier: Apache-2.0
"""DatabaseBlock: the registry and MCP door (``g6_invoke``) to g6.integrations.database.

Each op calls one function of :mod:`g6.integrations.database.ops` with the connections the person who runs the server
declared (read on every call, never taken from the input) and returns its labelled result unchanged, with
``component`` and ``op``. A refusal is a ``blocked-escalated`` result, never a raised error. The typed input refuses
unknown ops and unknown fields (``G6_E_INPUT_VALIDATION``): there is no field for a path, a URL, an approval or a
setting, so a caller cannot supply one.

``execute`` changes data, so the package lists it in ``__g6_sensitive_ops__``: the registry refuses it over MCP unless
the person who runs the server lists ``database.execute`` (or ``database``) in ``G6_MCP_SENSITIVE``. Even then it is a
dry run unless ``commit`` is true, and a commit happens only when the action gate allows it.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from g6.core.ai_block import AIBlock
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.integrations.database import ops

#: Changes data: from Python, and over MCP only when the server's G6_MCP_SENSITIVE lists it.
SENSITIVE_OPS: tuple[str, ...] = ("execute",)
_OPS: dict[str, str] = {
    "query": "query(sql, connection, params, max_rows): one read on a connection that cannot write; at most max_rows "
             "rows, long cells cut, and every cut said",
    "explain": "explain(sql, connection, params): the database's plan for one query or change; it runs nothing",
    "execute": "execute(sql, connection, params, commit): a script of changes in one transaction, rolled back (a dry "
               "run) unless commit is true and the action gate allows it; destructive statements need the server's "
               "setting (sensitive: it changes data; from Python, or over MCP when the server's G6_MCP_SENSITIVE "
               "lists it)",
    "list_tables": "list_tables(connection): the tables and views the database lists",
    "describe_table": "describe_table(table, connection): one table's columns, types, nullability, defaults and keys",
    "get_schema": "get_schema(connection): every table with its columns, and the engine's own definitions where it "
                  "keeps them",
}
_NEEDS = {"query": "sql", "explain": "sql", "execute": "sql", "describe_table": "table"}

Param = (Annotated[str, Field(max_length=1_000_000)] | bool | int | Annotated[float, Field(allow_inf_nan=False)]
         | None)


class DatabaseInput(BaseModel):
    """One op's parameters. Fields an op does not use are ignored by it; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["query", "explain", "execute", "list_tables", "describe_table", "get_schema", "ops", "help"] = "help"
    connection: str = Field("", max_length=200)
    sql: str = Field("", max_length=1_000_000)
    params: list[Param] | None = Field(None, max_length=1000)
    table: str = Field("", max_length=300)
    max_rows: int | None = Field(None, ge=1, le=100_000)
    commit: bool = False
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)

    @model_validator(mode="after")
    def _has_what_the_op_needs(self) -> DatabaseInput:
        needed = _NEEDS.get(self.op)
        if needed and not getattr(self, needed).strip():
            raise ValueError(f"{self.op} needs {needed}")
        return self


def _ids(d: DatabaseInput) -> dict[str, str]:
    return {"connection": d.connection, "request_id": d.request_id, "run_id": d.run_id}


_RUN: dict[str, Callable[[DatabaseInput], Any]] = {
    "query": lambda d: ops.query(d.sql, params=d.params, max_rows=d.max_rows, **_ids(d)),
    "explain": lambda d: ops.explain(d.sql, params=d.params, **_ids(d)),
    "execute": lambda d: ops.execute(d.sql, params=d.params, commit=d.commit, **_ids(d)),
    "list_tables": lambda d: ops.list_tables(**_ids(d)),
    "describe_table": lambda d: ops.describe_table(d.table, **_ids(d)),
    "get_schema": lambda d: ops.get_schema(**_ids(d)),
}


@component_maturity(
    "beta",
    reason="reads on connections that cannot write and changes dry-run first, both on databases only the server's "
           "operator declares; SQLite tested here, DuckDB and PostgreSQL with the db extra; qualified-draft at best",
)
class DatabaseBlock(AIBlock[DatabaseInput, dict, dict]):
    """Query and change a database the server's operator declared, and say what each answer rests on. Never
    ``verified``."""

    def __init__(self, name: str = "database", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: DatabaseInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        result = _RUN[data.op](data)
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


__all__ = ["SENSITIVE_OPS", "DatabaseBlock", "DatabaseInput"]
