# SPDX-License-Identifier: Apache-2.0
"""The coding-agent gate's door counts an evidence item as G6-observed only when it cites a receipt id that G6's
store holds for the artifact under review (plan Step 2, amendment A6). The receipts here are written straight to the
store (``receipt_store._record``) in place of a confined run; tests/workforce/test_receipt_store.py writes them with
real runs. Whatever JSON the caller sends (``observed_by``, a digest, a receipt's body) stays caller-declared."""
from __future__ import annotations

import json
import uuid

import pytest

from g6.core.registry import get_registry
from g6.mcp import invoke_reply
from g6.workforce import receipt_store

ARTIFACT = "a1" * 32
TASK = {"task": "Fix add()", "answer": "Fixed add(); all tests pass.",
        "task_contract": "add(2, 3) == 5 and the existing tests stay green"}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """A G6_HOME of this test's own: the store holds only the receipts this test writes."""
    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6-home"))


def _receipt(verdict: str = "passed", source_before: str = ARTIFACT) -> str:
    passed = 2 if verdict == "passed" else 1
    return receipt_store._record({
        "schema": "g6.test_receipt/1", "execution_id": uuid.uuid4().hex, "started_at": "2026-10-28T10:00:00.000+00:00",
        "finished_at": "2026-10-28T10:00:01.000+00:00", "g6_version": "0.1.1", "source_before": source_before,
        "source_after": source_before, "base_commit": "0" * 40,
        "executable": {"path": "/usr/bin/python3", "version": "3.13.5", "sha256": "b" * 64},
        "tests": {"requested": ["tests/test_calc.py"], "collected": ["tests/test_calc.py::test_add",
                                                                     "tests/test_calc.py::test_sub"],
                  "deselected": 0, "omitted": []},
        "command": "pytest 1 test id(s) in place", "isolation": {"backend": "bubblewrap", "why": "", "network": False},
        "result": {"verdict": verdict, "passed": passed, "failed": 2 - passed, "not_passed": 0, "total": 2,
                   "failed_ids": [] if verdict == "passed" else ["tests/test_calc.py::test_add"], "completed": True}})


def _gate(evidence: list[dict], op: str = "verify_answer", **params) -> dict:
    return get_registry().invoke("coding_agent_gate", op, {**TASK, "evidence": evidence, **params}).unwrap()


def test_a_stored_receipt_for_the_artifact_under_review_is_g6_observed():
    # Removing the door's receipt check, or its artifact binding, fails here.
    ident = _receipt()
    seen = _gate([{"kind": "test", "ref": "pytest -q", "exit_code": 0, "receipt": ident}], artifact_digest=ARTIFACT)
    assert (seen["verdict"], seen["evidence_basis"]) == ("pass", "g6-observed"), seen
    [counted] = seen["evidence_counted"]
    assert (counted["kind"], counted["ref"], counted["exit_code"], counted["observed_by"]) == ("test", ident, 0, "g6")
    assert seen["receipts"] == [{"receipt": ident, "receipt_status": "verified"}]
    assert any(item["source"] == "g6" for item in seen["evidence"])
    # the same run declared by the caller: a readiness claim on it needs the answer to say it is unverified
    declared = _gate([{"kind": "test", "ref": "pytest -q", "exit_code": 0}], artifact_digest=ARTIFACT)
    assert (declared["verdict"], declared["evidence_basis"]) == ("revise", "caller-declared")
    assert "overclaim_on_declared_run" in {f["code"] for f in declared["findings"]}
    assert declared["receipts"] == [{"receipt": "", "receipt_status": "not_supplied"}]


def test_the_receipt_decides_the_item_and_the_callers_own_fields_are_ignored():
    ident = _receipt("failed")
    seen = _gate([{"kind": "log", "ref": "all green", "output": "2 passed", "exit_code": 0, "receipt": ident}],
                 artifact_digest=ARTIFACT)
    [counted] = seen["evidence_counted"]
    assert (counted["kind"], counted["ref"], counted["exit_code"], counted["observed_by"]) == ("test", ident, 1, "g6")
    assert seen["verdict"] == "revise" and "test_failed" in {f["code"] for f in seen["findings"]}


@pytest.mark.parametrize(("cited", "artifact", "status"), [
    ("forged", ARTIFACT, "unknown"),          # an id the store does not hold
    ("real", "b2" * 32, "artifact_mismatch"),  # a real receipt, for another tree
    ("real", "", "artifact_mismatch"),        # a real receipt, and no artifact named
])
def test_a_receipt_the_store_does_not_hold_for_this_artifact_stays_caller_declared(cited, artifact, status):
    ident = _receipt() if cited == "real" else "f" * 64
    seen = _gate([{"kind": "test", "ref": "pytest -q", "exit_code": 0, "receipt": ident}], artifact_digest=artifact)
    assert seen["evidence_basis"] == "caller-declared" and seen["verdict"] == "revise"
    [counted] = seen["evidence_counted"]
    assert (counted["ref"], counted["observed_by"]) == ("pytest -q", "caller")
    assert seen["receipts"] == [{"receipt": ident, "receipt_status": status}]


def test_preflight_reads_receipts_the_same_way():
    ident = _receipt()
    seen = _gate([{"kind": "test", "ref": "pytest -q", "exit_code": 0, "receipt": ident}], op="preflight",
                 artifact_digest=ARTIFACT, proposed_action="merge the fix")
    assert seen["evidence_basis"] == "g6-observed"
    assert seen["receipts"] == [{"receipt": ident, "receipt_status": "verified"}]


@pytest.mark.parametrize("item", [
    {"kind": "test", "ref": "pytest", "exit_code": 0, "observed_by": "g6"},
    {"kind": "test", "ref": "pytest", "exit_code": 0, "digest": ARTIFACT},
    {"kind": "test", "ref": "pytest", "exit_code": 0, "source_before": ARTIFACT},
    {"kind": "test", "ref": "pytest", "exit_code": 0, "receipt": {"schema": "g6.test_receipt/1",
                                                                  "source_before": ARTIFACT}},
    {"kind": "test", "ref": "pytest", "exit_code": 0, "receipt_status": "verified"},
])
def test_a_caller_cannot_send_g6_provenance_or_a_receipts_body(item):
    reply = invoke_reply("coding_agent_gate", "verify_answer",
                         json.dumps({**TASK, "artifact_digest": ARTIFACT, "evidence": [item]}))
    assert not reply["ok"] and reply["completion_state"] == "blocked-escalated"


def test_a_receipts_body_sent_as_its_id_is_not_a_receipt():
    body = {"schema": "g6.test_receipt/1", "source_before": ARTIFACT, "result": {"verdict": "passed"}}
    for ident in (json.dumps(body), receipt_store.receipt_id(body)):
        seen = _gate([{"kind": "test", "ref": "pytest -q", "exit_code": 0, "receipt": ident}],
                     artifact_digest=ARTIFACT)
        assert seen["evidence_basis"] == "caller-declared"
        assert seen["receipts"] == [{"receipt": ident, "receipt_status": "unknown"}]
