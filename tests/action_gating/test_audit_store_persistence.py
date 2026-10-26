# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Durable + tamper-evident action-gating audit trail (INV-AUDIT-DURABLE).

Two-sided oracle for the SQLite-backed ActionGateAuditStore and its wiring into
ActionGateAuditLogger. Closes the in-memory-only gap the ISO/IEC 42001 mapping
flags (Clause 9.2: a persisted, tamper-evident, retained audit log) and is
partial evidence toward PA-15 (inspectable audit trail).

POSITIVE: a record written through the logger survives a fresh store on the same
path (process-restart simulation), is queryable by audit_id / action_id / recency,
preserves the decision + reason codes + human-approval/confirmation fields, and an
intact log verifies its hash chain.
NEGATIVE: tampering a persisted row breaks verify_chain(); a durable-write failure
re-raises so the fail-closed audit path still blocks the action; and a store on a
different path does not see another store's records.
"""
from __future__ import annotations

import json
import sqlite3

from g6.action_gating.audit import ActionGateAuditLogger
from g6.action_gating.audit_store import ActionGateAuditStore
from g6.action_gating.decision_engine import evaluate_action
from g6.action_gating.normalizer import normalize_action_capsule

_PAYMENT = {
    "action_type": "payment",
    "target_backend": "payments",
    "target_resource": "invoice-1",
    "external_side_effect": True,
    "irreversible_or_costly_to_reverse": True,
}
_DELETE = {
    "action_type": "account_deletion",
    "target_backend": "billing",
    "target_resource": "account-9",
    "irreversible_or_costly_to_reverse": True,
}


def _record_through_logger(store, payload):
    """Drive a real evaluate_action so a genuine record is built + persisted."""
    logger = ActionGateAuditLogger(available=True, store=store)
    capsule = normalize_action_capsule(payload)
    result = evaluate_action(capsule, audit_logger=logger)
    return result, logger.records[-1]


# ── POSITIVE: durability across "restart" ──────────────────────────────────────


def test_records_survive_a_fresh_store_on_the_same_path(tmp_path):
    db = str(tmp_path / "audit.db")
    store = ActionGateAuditStore(db)
    _, record = _record_through_logger(store, _PAYMENT)
    assert store.count() == 1
    store.close()

    # Fresh instance == process restart: the record must still be there.
    reopened = ActionGateAuditStore(db)
    assert reopened.count() == 1
    got = reopened.get_by_audit_id(record.audit_id)
    assert got is not None
    # Inspectability: the persisted record distinguishes the gate decision and the
    # human-approval / confirmation context, round-tripped intact.
    assert got.action_id == record.action_id
    assert got.decision == record.decision
    assert got.reason_codes == record.reason_codes
    assert got.confirmation_required == record.confirmation_required
    assert got.gate_token_issued == record.gate_token_issued
    reopened.close()


def test_queries_by_action_and_recency(tmp_path):
    store = ActionGateAuditStore(str(tmp_path / "audit.db"))
    _, r1 = _record_through_logger(store, _PAYMENT)
    _, r2 = _record_through_logger(store, _DELETE)
    assert store.count() == 2

    by_action = store.get_by_action_id(r2.action_id)
    assert [r.action_id for r in by_action] == [r2.action_id]

    recent = store.list_recent(limit=10)
    assert {r.audit_id for r in recent} == {r1.audit_id, r2.audit_id}
    store.close()


# ── POSITIVE/NEGATIVE: tamper-evidence ─────────────────────────────────────────


def test_intact_chain_verifies_and_tampering_is_detected(tmp_path):
    db = str(tmp_path / "audit.db")
    store = ActionGateAuditStore(db)
    for payload in (_PAYMENT, _DELETE, _PAYMENT):
        _record_through_logger(store, payload)
    assert store.count() == 3
    assert store.verify_chain() is True
    store.close()

    # Tamper with a persisted row's content directly (an attacker editing the DB
    # to hide that an action was blocked) WITHOUT recomputing the chain hashes.
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id, record_json FROM audit_records ORDER BY id ASC LIMIT 1"
    ).fetchone()
    obj = json.loads(row["record_json"])
    obj["decision"] = "approve" if obj["decision"] != "approve" else "block"
    conn.execute(
        "UPDATE audit_records SET record_json = ? WHERE id = ?",
        (json.dumps(obj), row["id"]),
    )
    conn.commit()
    conn.close()

    reopened = ActionGateAuditStore(db)
    assert reopened.verify_chain() is False
    reopened.close()


# ── NEGATIVE: fail-closed preserved with a durable store ───────────────────────


class _RaisingStore:
    """Stub durable store whose append always fails (e.g. disk full / DB locked)."""

    def append(self, record):  # noqa: ANN001, D401
        raise RuntimeError("simulated durable-store failure")


def test_durable_write_failure_blocks_the_action_fail_closed():
    logger = ActionGateAuditLogger(available=True, store=_RaisingStore())
    result = evaluate_action(normalize_action_capsule(_PAYMENT), audit_logger=logger)
    assert result.gate_decision.decision == "block"
    assert result.gate_decision.completion_state == "blocked-escalated"
    assert "G6_E_AUDIT_WRITE_FAILED" in result.gate_decision.reason_codes


# ── NEGATIVE: store isolation / unknown lookups ────────────────────────────────


def test_distinct_stores_are_isolated_and_unknown_lookups_are_empty(tmp_path):
    store_a = ActionGateAuditStore(str(tmp_path / "a.db"))
    _, rec = _record_through_logger(store_a, _PAYMENT)

    store_b = ActionGateAuditStore(str(tmp_path / "b.db"))
    assert store_b.count() == 0
    assert store_b.get_by_audit_id(rec.audit_id) is None
    assert store_a.get_by_audit_id("no-such-audit-id") is None
    assert store_a.get_by_action_id("no-such-action") == []
    store_a.close()
    store_b.close()
