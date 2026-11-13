# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Each receipt keeps its own role in ECHA's view (G6 0.3, slice 2, repair 1; review 1's finding 4).

The view copies the issuer's statement and explains each receipt. A receipt's role comes from its own result, read
against what the issuer counted for its check: a pass of a check in support supports, a failure of a check that refutes
refutes, a failure or a result that is none of a check kept as a caveat is a caveat, and anything else is not counted.
A check's other receipts never lend it theirs, so one check with a passing and a failing receipt shows one of each.
"""
from __future__ import annotations

import pytest

from g6.core.ledger import ChainLedger
from g6.evidence import Artifact, Check, Control, MissionFacts, Receipt, issue, record
from g6.research.echa.view import certificate_view

A, STANDARD, CODE = "a" * 64, "5" * 64, "c" * 64


def view(*receipts: Receipt, positive_seq: int | None = 1) -> tuple[dict, dict]:
    control = Control("control", CODE, "R1", "g6", 2, True)
    check = Check("check", ("R1",), "reviewer", "independent_executable", CODE, 0, (control,),
                  positive_seq=positive_seq)
    ledger = ChainLedger()
    record(ledger, control, *receipts)
    facts = MissionFacts("1" * 64, STANDARD, STANDARD, ("R1",), ("R1",))
    certificate = issue(facts, Artifact(A, "agent", 1), [check], list(receipts), ledger=ledger)
    seen = certificate_view(certificate.as_dict(), artifact_sha256=A, ledger=ledger)
    return {e["receipt_id"]: e["role"] for e in seen["predicates"][0]["evidence"]}, certificate.predicates[0].as_dict()


PASSED = Receipt("pass-run", "check", CODE, A, "pass", 3)
FAILED = Receipt("fail-run", "check", CODE, A, "fail", 4)
UNKNOWN = Receipt("unknown-run", "check", CODE, A, "unknown", 4)


def test_finding_4_the_view_does_not_call_a_refuting_receipt_support():
    roles, statement = view(PASSED, FAILED)
    assert statement["refuted_by"] == ["check"] and statement["supported_by"] == ["check"]
    assert roles["fail-run"] == "refutes", "a refuting receipt is displayed as supports"
    assert roles["pass-run"] == "supports"


@pytest.mark.parametrize(("receipts", "positive_seq", "expected"), [
    ((PASSED,), 1, {"pass-run": "supports"}),
    ((FAILED,), 1, {"fail-run": "refutes"}),
    ((FAILED,), None, {"fail-run": "caveat"}),          # never shown to pass known-good code: a caveat
    ((UNKNOWN,), 1, {"unknown-run": "caveat"}),
    ((PASSED, UNKNOWN), 1, {"pass-run": "supports", "unknown-run": "caveat"}),
    ((PASSED, FAILED), None, {"pass-run": "supports", "fail-run": "caveat"}),
], ids=["pass-alone", "fail-alone", "fail-without-positive", "unknown-alone", "pass-and-unknown",
        "pass-and-caveat"])
def test_each_receipt_s_role_comes_from_its_own_result(receipts, positive_seq, expected):
    roles, _ = view(*receipts, positive_seq=positive_seq)
    assert roles == expected


def test_a_pass_the_issuer_did_not_count_is_not_counted_whatever_its_check_s_other_receipts_did():
    early = Receipt("early-pass", "check", CODE, A, "pass", 1)   # taken before its control: not support
    roles, statement = view(early, FAILED)
    assert statement["supported_by"] == [] and statement["refuted_by"] == ["check"]
    assert roles == {"early-pass": "not counted", "fail-run": "refutes"}


def test_a_receipt_whose_result_is_not_one_the_issuer_knows_is_not_counted():
    roles, _ = view(PASSED)
    control = Control("control", CODE, "R1", "g6", 2, True)
    check = Check("check", ("R1",), "reviewer", "independent_executable", CODE, 0, (control,), positive_seq=1)
    ledger = ChainLedger()
    record(ledger, control, PASSED)
    facts = MissionFacts("1" * 64, STANDARD, STANDARD, ("R1",), ("R1",))
    body = issue(facts, Artifact(A, "agent", 1), [check], [PASSED], ledger=ledger).as_dict()
    body["receipts"][0]["result"] = "maybe"   # malformed: a result the issuer has no word for
    seen = certificate_view(body, artifact_sha256=A, ledger=ledger)
    assert seen["predicates"][0]["evidence"][0]["role"] == "not counted" and roles == {"pass-run": "supports"}
