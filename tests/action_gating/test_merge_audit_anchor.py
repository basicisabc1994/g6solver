# SPDX-License-Identifier: Apache-2.0
"""Append-only anchors through the existing action audit store."""
import sqlite3

import pytest

from g6.action_gating import evaluate_action
from g6.action_gating.audit import ActionGateAuditLogger
from g6.action_gating.audit_store import ActionGateAuditStore, _entry_hash


def populated(tmp_path):
    store = ActionGateAuditStore(str(tmp_path / "audit.db"))
    logger = ActionGateAuditLogger(store=store)
    for _ in range(3):
        evaluate_action({"action_type": "send_email"}, audit_logger=logger)
    return store, logger.records[-1]


def test_d1_deleted_tail_reports_first_missing_row(tmp_path):
    # Removal oracle: _verify_unlocked's chain_length comparison against the stored anchor.
    store, _ = populated(tmp_path)
    anchor = store.export_anchor()
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("DELETE FROM audit_records WHERE id=3")
    report = store.verify_chain_report()
    assert not store.verify_chain() and report["first_broken_row"] == 3
    assert report["reason"] == "chain_length_mismatch"
    assert not store.verify_chain(anchor=anchor)
    store.close()


def test_inserted_consistently_hashed_tail_is_detected(tmp_path):
    # Removal oracle: _verify_unlocked's length check also rejects a longer chain.
    store, record = populated(tmp_path)
    anchor = store.export_anchor()
    raw = record.model_dump_json()
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("INSERT INTO audit_records (audit_id,action_id,timestamp,decision,confirmation_required,gate_token_issued,record_json,prev_hash,entry_hash) "
                     "VALUES (?,?,?,?,?,?,?,?,?)", (record.audit_id, record.action_id, record.timestamp,
                     record.decision, record.confirmation_required, record.gate_token_issued, raw, anchor["head_hash"], _entry_hash(anchor["head_hash"], raw)))
    assert store.verify_chain_report()["first_broken_row"] == 4
    assert store.verify_chain_report()["reason"] == "chain_length_mismatch"
    assert not store.verify_chain()
    store.close()


def test_csf_audit_01_append_never_launders_an_earlier_edit(tmp_path):
    # Removal oracle: append's pre-write _verify_unlocked refusal.
    store, record = populated(tmp_path)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE audit_records SET record_json='{}' WHERE id=2")
    with pytest.raises(RuntimeError, match="G6_E_AUDIT_CHAIN_BROKEN"):
        store.append(record)
    assert store.count() == 3 and store.verify_chain_report()["first_broken_row"] == 2
    store.close()


def test_external_anchor_survives_rewrite_of_internal_anchor(tmp_path):
    # Removal oracle: _verify_unlocked's external anchor head comparison.
    store, _ = populated(tmp_path)
    anchor = store.export_anchor()
    with sqlite3.connect(store.db_path) as conn:
        rows = conn.execute("SELECT id,record_json FROM audit_records ORDER BY id").fetchall()
        previous = "0" * 64
        for ident, raw in rows:
            # Whitespace alters the bytes without invalidating record structure.
            raw += " "
            head = _entry_hash(previous, raw)
            conn.execute("UPDATE audit_records SET record_json=?,prev_hash=?,entry_hash=? WHERE id=?",
                         (raw, previous, head, ident))
            previous = head
        conn.execute("UPDATE audit_anchor SET head_hash=?", (previous,))
    assert store.verify_chain() is True
    report = store.verify_chain_report(anchor=anchor)
    assert report["ok"] is False and report["reason"] == "head_hash_mismatch"
    assert report["completion_state"] == "blocked-escalated"
    store.close()


def test_anchor_export_roundtrip_and_storage_failure(tmp_path, monkeypatch):
    # Removal oracle: export_anchor's integrity check before returning an anchor.
    store, _ = populated(tmp_path)
    anchor = store.export_anchor()
    assert store.verify_chain(anchor=anchor) is True
    assert store.verify_chain_report()["completion_state"] == "qualified-draft"
    def unavailable():
        raise sqlite3.OperationalError("database unavailable")
    monkeypatch.setattr(store, "_get_conn", unavailable)
    assert store.verify_chain_report()["reason"] == "store_unavailable"
    with pytest.raises(RuntimeError, match="G6_E_AUDIT_CHAIN_BROKEN"):
        store.export_anchor()
    store.close()


def test_missing_anchor_is_not_recreated_on_reopen(tmp_path):
    # Removal oracle: _ensure_schema initialises only a newly created empty store.
    store, _ = populated(tmp_path)
    path = store.db_path
    store.close()
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM audit_anchor")
    reopened = ActionGateAuditStore(path)
    assert reopened.verify_chain_report()["reason"] == "anchor_missing"
    assert reopened.verify_chain() is False
    reopened.close()
