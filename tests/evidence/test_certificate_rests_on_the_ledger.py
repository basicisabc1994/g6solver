# SPDX-License-Identifier: Apache-2.0
"""A ``verified`` certificate rests on records G6 wrote to its own ledger, not on fields a caller typed (EVIDENCE-01).

A control's ``generated_by="g6"`` and ``killed=True`` and a receipt's ``"pass"`` are only words in a dataclass. The
issuer now resolves every control and receipt a support would rest on against a ``g6.core.ledger.ChainLedger`` by
kind, id and body hash; one it cannot resolve there keeps the predicate unsupported, is named in the gaps, and holds
the certificate at ``qualified-draft``.
"""
from __future__ import annotations

import copy
import hashlib
from dataclasses import replace

from g6.core.ledger import ChainLedger
from g6.evidence import Artifact, Check, Control, MissionFacts, Receipt, digest_of, issue, verify

PATCH = b"def double(x):\n    return x + x\n"
ART = Artifact(hashlib.sha256(PATCH).hexdigest(), producer="coding-agent", seq=10)
CODE = hashlib.sha256(b"def test_double(): assert double(2) == 4").hexdigest()
CONTROL = Control("mutant-1", CODE, "double_is_correct", generated_by="g6", seq=3, killed=True)
CHECK = Check("held-out-test", ("double_is_correct",), author="reviewer", kind="independent_executable",
              check_sha=CODE, frozen_seq=2, controls=(CONTROL,))
RECEIPT = Receipt("r1", "held-out-test", CODE, ART.sha256, "pass", seq=11)
FACTS = MissionFacts(mission_sha="mission-1", mission_standard_sha="standard-1", standard_sha="standard-1",
                     predicates=("double_is_correct",), standard_predicates=("double_is_correct",))


def _written(*records) -> ChainLedger:
    from g6.evidence import record

    ledger = ChainLedger()
    record(ledger, *records)
    return ledger


def test_hand_written_records_that_claim_g6_are_a_qualified_draft_naming_them() -> None:
    # Fails if certificate._unresolved is skipped in issue (the hand-written control and receipt support again).
    cert = issue(FACTS, ART, [CHECK], [RECEIPT])
    assert cert.state == "qualified-draft"
    assert cert.verified_predicates == ()
    (subject, why), = cert.gaps
    assert subject == "predicate:double_is_correct"
    assert "receipt:r1" in why and "control:mutant-1" in why, why
    assert cert.as_dict()["unresolved"] == [["control", "mutant-1"], ["receipt", "r1"]]


def test_a_ledger_that_does_not_hold_the_records_resolves_nothing() -> None:
    # Fails if certificate._resolver treats a ledger as proof by its presence rather than by its entries.
    assert issue(FACTS, ART, [CHECK], [RECEIPT], ledger=ChainLedger()).state == "qualified-draft"


def test_records_g6_wrote_to_the_ledger_stay_as_today() -> None:
    # Fails if certificate.record writes a body _resolver does not hash the same way (nothing would ever resolve).
    cert = issue(FACTS, ART, [CHECK], [RECEIPT], ledger=_written(CONTROL, RECEIPT))
    assert cert.state == "verified" and cert.gaps == () and cert.as_dict()["unresolved"] == []


def test_a_record_is_resolved_by_its_hash_not_its_id() -> None:
    # Fails if certificate._resolver compares ids only: the ledger saw this control survive, the caller says killed.
    ledger = _written(replace(CONTROL, killed=False), RECEIPT)
    cert = issue(FACTS, ART, [CHECK], [RECEIPT], ledger=ledger)
    assert cert.state == "qualified-draft"
    assert "control:mutant-1" in cert.gaps[0][1] and "receipt:r1" not in cert.gaps[0][1]


def test_verify_offline_keeps_the_recorded_resolution_and_the_ledger_catches_a_rewrite() -> None:
    # Fails if verify ignores its ledger argument: the consistent rewrite below passes offline by design.
    ledger = _written(CONTROL, RECEIPT)
    good = issue(FACTS, ART, [CHECK], [RECEIPT], ledger=ledger).as_dict()
    assert verify(good, PATCH) == [] and verify(good, PATCH, ledger=ledger) == []

    draft = issue(FACTS, ART, [CHECK], [RECEIPT]).as_dict()
    assert verify(draft, PATCH) == []
    fake = copy.deepcopy(draft)
    fake["predicates"][0].update(support=True, supported_by=["held-out-test"], caveats=[])
    fake.update(gaps=[], unresolved=[], rerun=[["held-out-test", CODE, "independent_executable"]], state="verified")
    fake["digest"] = digest_of(fake)
    assert verify(fake, PATCH) == []  # offline, verify checks consistency only (the skill's blind spot)
    problems = verify(fake, PATCH, ledger=ChainLedger())
    assert any("issuing rules" in p for p in problems), problems
