# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""RS-03-F03 — an action-gating gate token must be single-use (no replay).

``execute_backend_op`` validated the token, capsule-hash scope, global-safety
approval and audit availability — all fail-closed — but never removed the token
from ``_TOKENS`` after use, so one issued token authorised UNLIMITED executions
until its 10-minute expiry. The fix consumes the token on first *successful*
execution (matching ``PermitLedger.consume``); a replay then fails closed with
``G6_E_GATE_TOKEN_INVALID``. A *failed* attempt must NOT consume the token, so a
legitimate retry after fixing approval/audit still works.
"""
from __future__ import annotations

import os

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from g6.action_gating.token_store import ActionGatingStore
from g6.action_gating.native_ops import execute_backend_op, issue_gate_token

#: A folder the gate can read, standing for the backup the write is undone from (ACTION_GATING-01: without one a write's
#: reversibility is unknown and no token is authorised). Not this file: "token" in its name reads as a secret's path.
_ARGS = {"checkpoint": str(Path(__file__).resolve().parent)}
_CAP = {"action_type": "write_file", "target_backend": "local", "action_params": dict(_ARGS), **_ARGS}


def _db_path() -> str:
    root = Path(os.environ["G6_HOME"]) / "test_dbs"  # isolated by tests/conftest.py
    root.mkdir(parents=True, exist_ok=True)
    return str(root / f"{uuid4().hex}.db")


def _exec(tok, *, approved=True, audit=True):
    return execute_backend_op(
        "local", "write_file", dict(_ARGS), tok,
        global_safety_approved=approved, audit_available=audit,
    )


def test_gate_token_is_single_use():
    tok = issue_gate_token(_CAP).token
    r1 = _exec(tok)
    assert r1["completion_state"] == "qualified-draft"
    r2 = _exec(tok)
    assert r2["completion_state"] == "blocked-escalated"
    assert r2["error"]["code"] == "G6_E_GATE_TOKEN_INVALID"


def test_failed_attempt_does_not_consume_token():
    # An attempt rejected for a non-token reason (audit unavailable) must leave
    # the token usable, so the operator can retry once audit is back.
    tok = issue_gate_token(_CAP).token
    blocked = _exec(tok, audit=False)
    assert blocked["completion_state"] == "blocked-escalated"
    assert blocked["error"]["code"] == "G6_E_AUDIT_REQUIRED"
    # Token survives the failed attempt -> a valid execution still succeeds once.
    ok = _exec(tok)
    assert ok["completion_state"] == "qualified-draft"
    # ...and is now consumed.
    assert _exec(tok)["error"]["code"] == "G6_E_GATE_TOKEN_INVALID"


def test_expired_gate_token_fails_closed():
    store = ActionGatingStore(_db_path())
    tok = issue_gate_token(_CAP, store=store)
    expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    store._conn.execute(  # noqa: SLF001 - test-only expiry setup against durable ledger
        "UPDATE action_gate_policy_decisions SET expires_at=? WHERE gate_token=?",
        (expired, tok.token),
    )
    store._conn.commit()  # noqa: SLF001

    result = execute_backend_op(
        "local",
        "write_file",
        {},
        tok.token,
        global_safety_approved=True,
        audit_available=True,
        store=store,
    )

    assert result["completion_state"] == "blocked-escalated"
    assert result["reliability_label"] == "blocked-escalated"
    assert result["error"]["code"] == "G6_E_GATE_TOKEN_EXPIRED"
    assert result["warning_card"]
    assert result["evidence"]
