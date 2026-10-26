# SPDX-License-Identifier: Apache-2.0
"""The stable codes of g6.integrations.database and the one labelled result every op returns.

An answer is ``qualified-draft``, and its warning card says what it rests on: :data:`RESULT` for rows the database
returned, :data:`PLAN` for the database's plan of a statement it did not run, :data:`DRY_RUN` for a change it made and
rolled back, :data:`COMMITTED` for a change it reported committed. A refusal is ``blocked-escalated`` with its own code.
Nothing is ``verified``: :class:`DatabaseResult` has no such state, because what a database returned is data, not a
check that the data or the change is right.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from g6.core.outside import OUTSIDE_PROGRAM_MISSING
from g6.core.reliability_envelope import WarningCard
from g6.core.resources import RESOURCE_LIMIT

RESULT = "G6_E_DB_RESULT"
PLAN = "G6_E_DB_PLAN"
DRY_RUN = "G6_E_DB_DRY_RUN"
COMMITTED = "G6_E_DB_COMMITTED"

INPUT_INVALID = "G6_E_DB_INPUT_INVALID"
UNKNOWN_CONNECTION = "G6_E_DB_UNKNOWN_CONNECTION"
LOCATION_IN_INPUT = "G6_E_DB_LOCATION_IN_INPUT"
CONFIG_INVALID = "G6_E_DB_CONFIG_INVALID"
STATEMENT_REFUSED = "G6_E_DB_STATEMENT_REFUSED"
DESTRUCTIVE_REFUSED = "G6_E_DB_DESTRUCTIVE_REFUSED"
READ_ONLY_CONNECTION = "G6_E_DB_READ_ONLY_CONNECTION"
STATEMENT_FAILED = "G6_E_DB_STATEMENT_FAILED"
FILE_REFUSED = "G6_E_DB_FILE_REFUSED"
UNKNOWN_TABLE = "G6_E_DB_UNKNOWN_TABLE"
GATE_HELD = "G6_E_DB_GATE_HELD"
NOT_INSTALLED = "G6_E_DB_NOT_INSTALLED"
CONNECT_FAILED = "G6_E_DB_CONNECT_FAILED"

_ACTIONS = {
    INPUT_INVALID: "Correct the input as the reason says and ask again.",
    UNKNOWN_CONNECTION: "Use one of the connection names listed; only the person who runs the server can add one.",
    LOCATION_IN_INPUT: "Name a connection the person who runs the server has declared; a path or URL is never taken.",
    CONFIG_INVALID: "Ask the person who runs the server to correct the database configuration.",
    STATEMENT_REFUSED: "Rewrite the SQL without the refused statement; reads go to query, changes to execute.",
    DESTRUCTIVE_REFUSED: "Narrow the change with a WHERE clause, or ask the person who runs the server to allow "
                         "destructive statements on this connection.",
    READ_ONLY_CONNECTION: "This connection is declared read-only; ask the person who runs the server.",
    STATEMENT_FAILED: "Read the database's message, correct the statement and try again; nothing was changed.",
    FILE_REFUSED: "Read files only from the data directory the person who runs the server declared.",
    UNKNOWN_TABLE: "Use a name from list_tables.",
    GATE_HELD: "Nothing was committed. Show the person the dry run and the gate's message; they decide.",
    NOT_INSTALLED: "Install the db extra: pip install 'g6[db]'.",
    CONNECT_FAILED: "Ask the person who runs the server to check the connection's settings and the database's login.",
    OUTSIDE_PROGRAM_MISSING: "Ask the person who runs the server to start the database server, or correct its "
                             "address in the configuration.",
    RESOURCE_LIMIT: "Make the query cheaper (a WHERE clause, a LIMIT, an index), or ask for a higher time limit.",
}

State = Literal["qualified-draft", "blocked-escalated"]


class DatabaseResult(BaseModel):
    """One op's labelled outcome. Op-specific fields sit beside the envelope's."""

    model_config = ConfigDict(extra="allow", frozen=True)

    operation: str
    code: str = ""
    reason: str = ""
    completion_state: State
    warning_card: dict[str, Any]
    evidence: list[dict[str, Any]]
    request_id: str = ""
    run_id: str = ""


def answer(operation: str, card: str, message: str, action: str, *, evidence: dict[str, Any], request_id: str = "",
           run_id: str = "", **fields: Any) -> DatabaseResult:
    """A ``qualified-draft`` answer whose card (``card``) says what it rests on."""
    warning = WarningCard(code=card, message=message, severity="amber", suggested_action=action).model_dump()
    return DatabaseResult.model_validate({**fields, "operation": operation, "completion_state": "qualified-draft",
                                          "warning_card": warning, "evidence": [evidence], "request_id": request_id,
                                          "run_id": run_id})


def refuse(operation: str, code: str, reason: str, *, evidence: dict[str, Any] | None = None, request_id: str = "",
           run_id: str = "", **fields: Any) -> DatabaseResult:
    """A ``blocked-escalated`` refusal with its stable code and the reason in words."""
    warning = WarningCard(code=code, message=reason, severity="blocked", suggested_action=_ACTIONS[code]).model_dump()
    return DatabaseResult.model_validate({**fields, "operation": operation, "code": code, "reason": reason,
                                          "completion_state": "blocked-escalated", "warning_card": warning,
                                          "evidence": [{**(evidence or {}), "code": code, "reason": reason}],
                                          "request_id": request_id, "run_id": run_id})


__all__ = ["COMMITTED", "CONFIG_INVALID", "CONNECT_FAILED", "DESTRUCTIVE_REFUSED", "DRY_RUN", "FILE_REFUSED",
           "GATE_HELD", "INPUT_INVALID", "LOCATION_IN_INPUT", "NOT_INSTALLED", "OUTSIDE_PROGRAM_MISSING", "PLAN",
           "READ_ONLY_CONNECTION", "RESOURCE_LIMIT", "RESULT", "STATEMENT_FAILED", "STATEMENT_REFUSED",
           "UNKNOWN_CONNECTION", "UNKNOWN_TABLE", "DatabaseResult", "answer", "refuse"]
