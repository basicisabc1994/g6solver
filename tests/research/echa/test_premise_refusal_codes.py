# SPDX-License-Identifier: Apache-2.0
"""Inventory stable custody refusals across every receipt role."""
from dataclasses import replace
from pathlib import Path
import runpy

import pytest

from g6.evidence.mapping import BINDING_PREDICATE, BINDING_STANDARD, NO_ENVIRONMENT
from g6.research.echa import EvidenceRecord, Ledger, StandardRef, trace_claim
from g6.research.echa.custody import about

S = runpy.run_path(str(Path(__file__).with_name("test_premise_subjects.py")))
H = S["H"]
ROLE_IDS = {"check": "e1", "check_control": "ctl", "calibration": "cal",
            "calibration_control": "cal-ctl", "scope_mapping": "scope", "scope_control": "scope-ctl"}


def refused_ledger(role, field):
    led = Ledger()
    hashes = {}
    for entry in S["receipts"]().entries():
        body = entry["body"]
        if entry["kind"] == "standard":
            led.add_standard(StandardRef.from_dict(body))
        elif entry["kind"] == "vault":
            led.add_vault(body["vault_id"], body["digest"], files=body["files"], roots=body["roots"])
        else:
            assert entry["kind"] == "evidence"
            original = EvidenceRecord.from_dict(body)
            rec = replace(original, **({field: "other" if field == "predicate_id" else "e" * 64}
                                       if original.evidence_id == ROLE_IDS[role] else {}))
            rec = replace(rec, applies_to_sha256=hashes.get(original.applies_to_sha256, original.applies_to_sha256))
            hashes[original.sha256] = rec.sha256
            led.add_evidence(rec)
    return led


@pytest.mark.parametrize("role", ROLE_IDS)
@pytest.mark.parametrize("field,code", [("fixture_version", NO_ENVIRONMENT),
                                        ("standard_sha256", BINDING_STANDARD), ("predicate_id", BINDING_PREDICATE)])
def test_every_receipt_role_keeps_its_custody_refusal_in_verdict_and_trace(role, field, code):
    assert set(ROLE_IDS) == set(S["ROLES"])
    led = refused_ledger(role, field)
    rec = EvidenceRecord.from_dict(led.get("evidence", ROLE_IDS[role]))
    assert code in {item for item, _ in about(rec)[1]}
    got = H["verdict"](led, subject="sha256:" + "b" * 64, estimand="true_correctness", target=0.5,
                       breadth="general", scope="all inputs")
    assert got["result"] == "inconclusive"
    assert got["reasons"].count(code) == 1
    traced = trace_claim(led, "c1")
    assert traced["verdict"] == got
    assert code in traced["verdict"]["reasons"]
    assert traced["ledger_ok"] is True
