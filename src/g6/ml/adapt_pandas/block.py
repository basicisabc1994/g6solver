# SPDX-License-Identifier: Apache-2.0
"""AdaptPandasBlock, the registry and MCP door (``g6_invoke``) to adapt_pandas: read and describe a table you give.

Ops: ``load`` and ``profile``, plus ``ops`` and ``help``. Each takes the table as one of ``csv`` (text with a header
line), ``json_text`` (an array of objects) or ``rows``, reads it by one stated rule (``table.READING_RULE``) and says
what it found. ``load`` returns the rows as read, ready for adapt_sklearn, with the line accounting and each column's
kind; ``profile`` describes every column and the table. Nothing is read from a file, nothing is converted silently,
and only the tokens you name count as missing. Results are ``qualified-draft``: a description of the values given,
which no one checked against the world.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.ml._types import INPUT_INVALID, MAX_ROWS, OP_FAILED, PROFILED, Cell, MLError, answer, column_kind, refuse
from g6.ml.adapt_pandas import table as tables
from g6.ml.adapt_pandas.table import MAX_TEXT_CHARS, READING_RULE

_OPS = {
    "load": "load(csv | json_text | rows, delimiter, missing): the rows as read by one stated rule, each column's kind, "
            "rows in and out, and the warnings a profile would give",
    "profile": "profile(csv | json_text | rows, delimiter, missing, key): each column's kind, missing count, distinct "
               "values, number summary or common texts and notes; repeated rows and repeated key values",
}


class TableInput(BaseModel):
    """One table, given one way, and how to read it. Fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["load", "profile", "ops", "help"] = "help"
    csv: str = Field("", max_length=MAX_TEXT_CHARS)
    json_text: str = Field("", max_length=MAX_TEXT_CHARS)
    rows: list[dict[str, Cell]] = Field(default_factory=list, max_length=MAX_ROWS)
    delimiter: Literal[",", ";", "\t", "|"] = ","
    missing: list[str] = Field(default_factory=lambda: [""], max_length=50)
    key: list[str] = Field(default_factory=list, max_length=10)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _ids(d: TableInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


def _table(d: TableInput) -> tables.Table:
    given = [name for name, value in (("csv", d.csv), ("json_text", d.json_text), ("rows", d.rows)) if value]
    if len(given) != 1:
        raise MLError(INPUT_INVALID, "give the table one way: csv, json_text or rows"
                                     + (f" (got {', '.join(given)})" if given else " (none was given)"))
    if d.csv:
        return tables.read_csv(d.csv, d.delimiter, d.missing)
    if d.json_text:
        return tables.read_json(d.json_text)
    return tables.read_rows([dict(r) for r in d.rows])


def _reading(table: tables.Table) -> dict[str, Any]:
    body = json.dumps({"columns": table.columns, "rows": table.rows}, sort_keys=True, default=str)
    return {"kind": "ml_table_reading", "source": table.source, "rule": READING_RULE,
            "missing_tokens": table.missing_tokens if table.source == "csv" else "none: values keep their own types",
            "accounting": table.accounting, "rows_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()}


def _said(table: tables.Table, warnings: list[str]) -> str:
    acc = table.accounting
    lines = (f" ({acc['lines_read']} lines, {acc['blank_lines_skipped']} blank skipped)" if table.source == "csv"
             else "")
    head = f"{len(table.rows)} rows and {len(table.columns)} columns read from {table.source}{lines}"
    if not warnings:
        return head + "; no column mixes kinds, no row repeats and no value looks like a missing marker"
    more = f"; and {len(warnings) - 3} more warning(s)" if len(warnings) > 3 else ""
    return head + "; " + "; ".join(warnings[:3]) + more


def _run(d: TableInput) -> Any:
    ids = _ids(d)
    try:
        table = _table(d)
        found = tables.profile(table, d.key)
    except MLError as exc:
        return refuse(d.op, exc.code, exc.reason, evidence=exc.evidence, **ids)
    evidence = [_reading(table)]
    message = _said(table, found["warnings"])
    if d.op == "load":
        columns = [{"name": c, "kind": column_kind([r[c] for r in table.rows]),
                    "present": next(x["present"] for x in found["columns"] if x["name"] == c),
                    "missing": next(x["missing"] for x in found["columns"] if x["name"] == c)}
                   for c in table.columns]
        return answer("load", message, card=PROFILED, evidence=evidence, rows=table.rows, columns=columns,
                      accounting=table.accounting, warnings=found["warnings"], **ids)
    return answer("profile", message, card=PROFILED, evidence=evidence, profile=found, accounting=table.accounting,
                  **ids)


@block_contract(
    purpose="read a table the caller gives (CSV or JSON text, or rows) by one stated rule and describe it: column "
            "kinds, missing values, numbers mixed with text, values that look like missing markers, repeated rows "
            "and repeated keys",
    mitigates_failure_modes=("data_handling_error",),
    verification_method="one stated reading rule applied to every value; counts recomputed from the values read; "
                        "no value converted or dropped without being reported",
)
@component_maturity(
    "beta",
    reason="load and profile by G6's own reader over the standard library; nothing read from a file; qualified-draft "
           "at best: whether the values are true is not judged",
)
class AdaptPandasBlock(AIBlock[TableInput, dict, dict]):
    """Read and describe a table. Never ``verified``."""

    def __init__(self, name: str = "adapt_pandas", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: TableInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": []})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values()),
                              "reading_rule": READING_RULE})
        try:
            result = _run(data)
        except Exception as exc:  # noqa: BLE001 - an error is a refusal with its reason, never a crash or a table
            result = refuse(data.op, OP_FAILED, f"{data.op} failed: {type(exc).__name__}: {exc}"[:500], **_ids(data))
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


__all__ = ["AdaptPandasBlock", "TableInput"]
