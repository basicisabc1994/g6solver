# SPDX-License-Identifier: Apache-2.0
"""Optional session persistence; all reasoning still uses the existing checked ops."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from g6.core.storage import open_db, state_path
from g6.datalog.envelope import INPUT_TOO_LARGE, Refusal, finish
from g6.datalog.language import MAX_FACTS
from g6.datalog.ops import check_program, evaluate, explain, query
from g6.datalog.schema import DatalogResult


class _Document(BaseModel):
    model_config = ConfigDict(extra="forbid")
    program: str
    facts: dict[str, list[list[Any]]] = Field(default_factory=dict)


def _load(conn, session: str) -> _Document | None:
    row = conn.execute("SELECT document FROM sessions WHERE session=?", (session,)).fetchone()
    return _Document.model_validate_json(row[0]) if row else None


def session_op(op: str, session: str, *, program: str = "", facts=None, goal: str = "", relations=None,
               engine: str = "auto", time_limit: float = 10.0, dry_run: bool = True,
               request_id: str = "", run_id: str = "") -> DatalogResult:
    """Assert facts or reset asserted facts; read ops use the same saved program.

    A new session needs program text. Rules cannot be replaced mid-session. Reset
    retains program text (including its original facts). Dry runs never create a
    database. Writes hold one SQLite transaction across read, check and read-back.
    A write is a local sensitive op (declared, dry-run by default, refused over MCP
    unless allowed), not an outward action, so it does not go through the action gate.
    """
    conn = None
    ids = {"request_id": request_id, "run_id": run_id}
    try:
        if not isinstance(session, str) or not session.strip() or len(session) > 200:
            raise Refusal("G6_E_DATALOG_SESSION_INPUT", "a session needs a non-empty name of at most 200 characters")
        path = state_path("datalog", "sessions.db")
        mutates = op in ("assert_facts", "reset")
        if mutates and not dry_run:
            conn = open_db(path)
            conn.execute("CREATE TABLE IF NOT EXISTS sessions (session TEXT PRIMARY KEY, document TEXT NOT NULL)")
            conn.execute("BEGIN IMMEDIATE")
            saved = _load(conn, session)
        elif path.exists():
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as reader:
                saved = _load(reader, session)
        else:
            saved = None
        if saved is None:
            if op != "assert_facts" or not program.strip():
                raise Refusal("G6_E_DATALOG_SESSION_MISSING", "no session; create it with assert_facts and program text")
            saved = _Document(program=program)
        if program and program != saved.program:
            raise Refusal("G6_E_DATALOG_SESSION_INPUT", "a session's program is fixed; use a new session name")
        merged = {k: list(v) for k, v in saved.facts.items()}
        if not mutates and facts:
            raise Refusal("G6_E_DATALOG_SESSION_INPUT", "persist facts with assert_facts before querying a session")
        if op == "reset":
            if facts:
                raise Refusal("G6_E_DATALOG_SESSION_INPUT", "reset takes no facts")
            merged = {}
        elif op == "assert_facts":
            # Cap the rows before any comparison; the program check caps them again after the merge.
            if sum(len(r) for r in merged.values()) + sum(len(r) for r in (facts or {}).values()) > MAX_FACTS:
                raise Refusal(INPUT_TOO_LARGE, f"a session holds at most {MAX_FACTS} asserted facts")
            for name, rows in (facts or {}).items():
                current = merged.setdefault(name, [])
                seen = {tuple(row) for row in current}
                for row in rows:
                    key = tuple(row)
                    if key not in seen:
                        seen.add(key)
                        current.append(row)
        common = {"facts": merged, "engine": engine, "time_limit": time_limit, **ids}
        if op == "query":
            result = query(saved.program, goal, **common)
        elif op == "explain":
            result = explain(saved.program, goal, **common)
        elif op == "check":
            result = check_program(saved.program, facts=merged, **ids)
        else:
            result = evaluate(saved.program, relations=relations or None, **common)
        if result.completion_state == "blocked-escalated":
            return result.model_copy(update={"operation": op})
        written = False
        if mutates:
            document = _Document(program=saved.program, facts=merged)
            if dry_run:
                written = False
            else:
                encoded = document.model_dump_json()
                conn.execute("INSERT INTO sessions(session, document) VALUES (?, ?) ON CONFLICT(session) DO UPDATE SET document=excluded.document",
                             (session, encoded))
                if _load(conn, session) != document:
                    raise Refusal("G6_E_DATALOG_SESSION_READBACK", "stored document differs from the checked proposal")
                conn.commit()
                written = True
        return result.model_copy(update={"operation": op, "report": {**result.report, "session": session,
                                         "dry_run": dry_run if mutates else False, "written": written}})
    except Refusal as exc:
        return finish(op, code=exc.code, reason=exc.reason, **ids)
    except Exception as exc:  # noqa: BLE001 -- dependency failure refuses the session operation
        return finish(op, code="G6_E_DATALOG_SESSION_FAILED", reason=f"{type(exc).__name__}: {exc}"[:500], **ids)
    finally:
        if conn is not None:
            conn.close()  # rolls back any uncommitted change
