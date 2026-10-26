# SPDX-License-Identifier: Apache-2.0
"""``verify`` decides a certificate again from the inputs it records, by ``issue``'s rules, and names every field
that differs: a certificate rewritten consistently (state, statements, gaps, re-run list and digest changed together)
no longer passes. Offline; nothing is run."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from g6.core.ledger import ChainLedger
from g6.evidence import Artifact, Check, Control, MissionFacts, Receipt, digest_of, issue, record, verify

PATCH = b"def double(x):\n    return x + x\n"
ART = Artifact(hashlib.sha256(PATCH).hexdigest(), producer="coding-agent", seq=10)
CODE = hashlib.sha256(b"def test_double(): assert double(2) == 4").hexdigest()
CONTROL = Control("mutant-1", CODE, "double_is_correct", generated_by="g6", seq=3, killed=True)
GIVEN = {"mission_sha": "mission-1", "predicates": ["double_is_correct"], "gates": ["approve"],
         "standard_sha": "standard-1", "standard_predicates": ["double_is_correct"]}


def _facts() -> MissionFacts:
    return MissionFacts(mission_sha="mission-1", mission_standard_sha="standard-1", standard_sha="standard-1",
                        predicates=("double_is_correct",), standard_predicates=("double_is_correct",),
                        gates=("approve",))


def _check(author: str) -> Check:
    return Check("test-double", ("double_is_correct",), author=author, kind="independent_executable", check_sha=CODE,
                 frozen_seq=2, controls=(CONTROL,))


def _body(author: str = "coding-agent", decisions: tuple[str, ...] = ("approve",)) -> dict:
    # The records G6's runner observed go to its ledger (EVIDENCE-01); without it nothing here could support.
    receipt = Receipt("r1", "test-double", CODE, ART.sha256, "pass", 11)
    ledger = ChainLedger()
    record(ledger, CONTROL, receipt)
    cert = issue(_facts(), ART, [_check(author)], [receipt], decisions=decisions, ledger=ledger)
    return cert.as_dict()


def _forged(body: dict, edit) -> dict:
    out = copy.deepcopy(body)
    edit(out)
    out["digest"] = digest_of(out)   # unkeyed: a forger can always recompute it
    return out


def _as_verified(b: dict) -> None:
    """The consistent forgery: support set, caveats and gaps cleared, re-run list set, state verified."""
    b["predicates"][0].update(support=True, supported_by=["test-double"], caveats=[])
    b["gaps"] = []
    b["rerun"] = [["test-double", CODE, "independent_executable"]]
    b["state"] = "verified"


def test_a_self_authored_draft_rewritten_consistently_as_verified_fails_verify() -> None:
    body = _body()
    assert body["state"] == "qualified-draft" and verify(body, PATCH, **GIVEN) == []
    fake = _forged(body, _as_verified)
    for problems in (verify(fake, PATCH), verify(fake, PATCH, **GIVEN)):
        assert any("issuing rules" in p and "state" in p for p in problems), problems


def test_an_undecided_gate_cleared_consistently_fails_verify_offline() -> None:
    body = _body(author="reviewer", decisions=())
    assert body["state"] == "qualified-draft" and [g[0] for g in body["gaps"]] == ["gate:approve"]
    fake = _forged(body, lambda b: b.update(gaps=[], state="verified"))
    problems = verify(fake, PATCH)
    assert any("issuing rules" in p for p in problems), problems


def test_a_rewritten_receipt_result_fails_verify() -> None:
    body = _body(author="reviewer")
    assert body["state"] == "verified"
    fake = _forged(body, lambda b: b["receipts"][0].update(result="unknown"))
    assert any("issuing rules" in p for p in verify(fake, PATCH))


def test_a_certificate_that_records_no_checks_cannot_be_decided_again() -> None:
    def to_v1(b: dict) -> None:
        for key in ("checks", "gates", "obligations", "artifact_seq"):
            b.pop(key)
        b["schema"] = "csir_certificate.v1"

    old = _forged(_body(author="reviewer"), to_v1)
    assert any("cannot be decided again" in p for p in verify(old, PATCH))


@pytest.mark.parametrize("author", ["coding-agent", "reviewer"])
@pytest.mark.parametrize("decisions", [(), ("approve",)])
def test_survival_an_issued_certificate_verifies_also_after_a_json_round_trip(author, decisions) -> None:
    body = _body(author, decisions)
    assert verify(body, PATCH, **GIVEN) == []
    assert verify(json.loads(json.dumps(body)), PATCH, **GIVEN) == []
