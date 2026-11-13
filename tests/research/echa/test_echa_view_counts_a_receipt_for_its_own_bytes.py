# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""ECHA's view gives a receipt a role only for its own bytes (G6 0.3, slice 2's follow-up; review 4's finding 3; class
``guard_covers_less_than_it_guards``, met in repairs 1 and 2).

The view gave a receipt its role from its result and its check's place in the issuer's statement, so a passing receipt
taken on other bytes was shown as supporting the subject because another receipt of its check did. Every gate of the
issuer's rule was open to the same loan: a receipt with other code, one before the artefact or its control, one the
ledger does not hold. Now a receipt counts for the subject only when it is bound to the subject's bytes, read through
ECHA's accessor (``custody.receipt_about``), and then it plays exactly the role the issuer gives that receipt alone. The
inventory lists, from ``g6.evidence.certificate``, every gate of the issuer's rule for one receipt, and plants a receipt
at each beside one the issuer counts.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

import g6
from g6.core.ledger import ChainLedger
from g6.evidence import Artifact, Check, Control, MissionFacts, Receipt, issue, record, verify
from g6.evidence import mapping as m
from g6.research.echa import custody
from g6.research.echa.view import WHY, certificate_view

# --- review 4's finding 3, brought in as this change's own ------------------------------------------------------------


def test_finding_3_a_receipt_for_other_bytes_is_not_shown_as_supporting_the_view_subject():
    patch_a, patch_b = b"patch A", b"patch B"
    a, b = (hashlib.sha256(patch).hexdigest() for patch in (patch_a, patch_b))
    standard, code = "5" * 64, "c" * 64
    facts = MissionFacts("1" * 64, standard, standard, ("R1",), ("R1",))
    artifact = Artifact(a, "patch author", 1)
    control = Control("control", code, "R1", "g6", 2, True)
    check = Check("check", ("R1",), "reviewer", "independent_executable", code, 0, (control,))
    other = Receipt("on-other-bytes", "check", code, b, "pass", 3)
    own = Receipt("on-subject-bytes", "check", code, a, "pass", 4)
    ledger = ChainLedger()
    record(ledger, control, other, own)

    # The issuer explicitly refuses to count the other receipt for this artefact.
    alone = issue(facts, artifact, [check], [other], ledger=ledger)
    assert alone.verified_predicates == ()
    both = issue(facts, artifact, [check], [other, own], ledger=ledger)
    assert both.verified_predicates == ("R1",)
    assert verify(both.as_dict(), patch_a, ledger=ledger) == []
    view = certificate_view(both.as_dict(), artifact_sha256=a, ledger=ledger)
    assert view["predicates"][0]["status"] == {
        key: value for key, value in both.predicates[0].as_dict().items() if key != "predicate"}
    roles = {e["receipt_id"]: e["role"] for e in view["predicates"][0]["evidence"]}
    assert roles[own.receipt_id] == "supports"
    assert roles[other.receipt_id] == "not counted", (
        "the view credits a passing receipt for other bytes because a different receipt of that check supports A",
        roles,
    )


# --- one check, receipts on the subject's bytes and on other bytes, each passing and failing -------------------------

A, B, STANDARD, CODE, OTHER_CODE = "a" * 64, "b" * 64, "5" * 64, "c" * 64, "d" * 64
FACTS = MissionFacts("1" * 64, STANDARD, STANDARD, ("R1",), ("R1",))
SUBJECT = Artifact(A, "agent", 1)
CONTROL = Control("control", CODE, "R1", "g6", 2, True)


def check(**over) -> Check:
    return Check(**{"check_id": "check", "predicates": ("R1",), "author": "reviewer", "kind": "independent_executable",
                    "check_sha": CODE, "frozen_seq": 0, "controls": (CONTROL,), "positive_seq": 1, **over})


def seen(*receipts: Receipt, checks=None, held=None, provenance=None, offline=False) -> tuple[dict, dict]:
    """(each receipt's (role, gate) in the view, the issuer's statement); ``held``: the receipts written to the
    ledger (all, by default); ``provenance``: entries to write beside receipts, by receipt id."""
    ledger = ChainLedger()
    checks = checks or [check()]
    record(ledger, *{c.control_id: c for one in checks for c in one.controls}.values(),
           *(held if held is not None else receipts))
    for rid, body in (provenance or {}).items():
        ledger.append(m.PROVENANCE_KIND, rid, body)
    body = issue(FACTS, SUBJECT, checks, list(receipts), ledger=ledger).as_dict()
    view = certificate_view(body, artifact_sha256=A, ledger=None if offline else ledger)
    return ({e["receipt_id"]: (e["role"], e["gate"]) for e in view["predicates"][0]["evidence"]},
            body["predicates"][0])


@pytest.mark.parametrize(("result", "on", "role"), [
    ("pass", A, "supports"), ("fail", A, "refutes"), ("pass", B, "not counted"), ("fail", B, "not counted"),
], ids=["own-bytes-pass", "own-bytes-fail", "other-bytes-pass", "other-bytes-fail"])
def test_a_receipt_supports_or_refutes_only_for_its_own_bytes(result, on, role):
    counted = Receipt("counted", "check", CODE, A, "pass", 3)
    planted = Receipt("planted", "check", CODE, on, result, 4)
    roles, statement = seen(counted, planted)
    assert roles["counted"] == ("supports", "")
    assert roles["planted"][0] == role
    if on == B:
        assert roles["planted"][1] == "receipt_for_other_bytes" and statement["refuted_by"] == []


def test_the_four_receipts_of_one_check_together_keep_their_own_roles():
    receipts = [Receipt(f"{result}-on-{name}", "check", CODE, on, result, seq)
                for seq, (result, name, on) in enumerate(
                    [("pass", "a", A), ("fail", "a", A), ("pass", "b", B), ("fail", "b", B)], start=3)]
    roles, statement = seen(*receipts)
    assert statement["supported_by"] == ["check"] and statement["refuted_by"] == ["check"]
    assert {rid: role for rid, (role, _) in roles.items()} == {
        "pass-on-a": "supports", "fail-on-a": "refutes", "pass-on-b": "not counted", "fail-on-b": "not counted"}


# --- the inventory: every gate of the issuer's rule for one receipt, from the code, and a receipt planted at each -----

CERTIFICATE = Path(g6.__file__).resolve().parent / "evidence" / "certificate.py"
#: Each test the issuer makes of one receipt, with the gate the view names when a receipt does not meet it ("" for a
#: test that only chooses the branch).
GATES = {
    "r.artifact_sha != artifact.sha256": "receipt_for_other_bytes",
    "r.check_sha != c.check_sha": "check_changed_after_freeze",
    "r.seq < artifact.seq": "receipt_before_artifact",
    "r.result == 'pass'": "",
    "reasons": "not_grounded",
    "not _backing(c, p, r)": "control_after_receipt",
    "(missing := _unresolved(c, p, r, resolves))": "not_in_ledger",
    "r.result == 'fail'": "",
    "_backing(c, p, r) and c.positive_seq is not None and (c.positive_seq < r.seq)": "",   # refutes, or a caveat
}


def gates() -> set[str]:
    tree = ast.parse(CERTIFICATE.read_text(encoding="utf-8"))
    issuing = next(f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef) and f.name == "_issue")
    loop = next(node for node in ast.walk(issuing) if isinstance(node, ast.For) and ast.unparse(node.target) == "r")
    return {ast.unparse(node.test) for node in ast.walk(loop) if isinstance(node, ast.If)}


def test_every_gate_of_the_issuer_s_rule_for_one_receipt_is_listed_and_every_listed_one_is_still_there():
    found = gates()
    assert sorted(found - set(GATES)) == [], "a gate of the issuer the view does not name: plant a receipt at it"
    assert sorted(set(GATES) - found) == [], "listed gates the issuer no longer has: remove them"
    named = {gate for gate in GATES.values() if gate}
    assert named | {"not_a_receipt"} == set(m.RECEIPT_GATES) and set(m.RECEIPT_GATES) <= set(WHY)


COUNTED = Receipt("counted", "check", CODE, A, "pass", 3)
#: A receipt planted at each gate, beside one the issuer counts, as (receipts, other checks, receipts held in the
#: ledger, the planted receipt's role and gate).
PLANTED = {
    "receipt_for_other_bytes": ([COUNTED, Receipt("planted", "check", CODE, B, "pass", 4)], None, None),
    "check_changed_after_freeze": ([COUNTED, Receipt("planted", "check", OTHER_CODE, A, "pass", 4)], None, None),
    "receipt_before_artifact": ([COUNTED, Receipt("planted", "check", CODE, A, "pass", 0)], None, None),
    "not_grounded": ([COUNTED, Receipt("planted", "own", CODE, A, "pass", 4)],
                     [check(), check(check_id="own", author="agent")], None),
    "control_after_receipt": ([COUNTED, Receipt("planted", "check", CODE, A, "pass", 1)], None, None),
    "not_in_ledger": ([COUNTED, Receipt("planted", "check", CODE, A, "pass", 4)], None, [COUNTED]),
}


@pytest.mark.parametrize("gate", sorted(PLANTED))
def test_a_receipt_planted_at_each_gate_is_not_counted_beside_one_the_issuer_counts(gate):
    receipts, checks, held = PLANTED[gate]
    roles, statement = seen(*receipts, checks=checks, held=held)
    assert statement["supported_by"] == ["check"] and roles["counted"] == ("supports", "")   # positive control
    assert roles["planted"] == ("not counted", gate), roles


def test_a_failure_shown_to_fail_and_to_pass_known_good_code_refutes_and_any_other_is_a_caveat():
    failed = Receipt("failed", "check", CODE, A, "fail", 4)
    assert seen(COUNTED, failed)[0]["failed"] == ("refutes", "")
    assert seen(COUNTED, failed, checks=[check(positive_seq=None)])[0]["failed"] == ("caveat", "")
    assert seen(COUNTED, Receipt("unknown", "check", CODE, A, "unknown", 4))[0]["unknown"] == ("caveat", "")


# --- the view's own gate: the bytes a receipt is bound to, through ECHA's accessor ------------------------------------

BINDING = m.CheckBinding(request_id="req", task_id="task", predicate_id="R1", artifact_sha256=A,
                         standard_sha256=STANDARD, fixture_sha256="f" * 64, evaluator_sha256=CODE)


@pytest.mark.parametrize(("binding", "gate"), [
    (BINDING.to_dict(), ""), ({**BINDING.to_dict(), "artifact_sha256": B}, "binding_artefact"),
    ({"not": "a binding"}, "binding_unreadable"), ("text", "binding_unreadable"),
], ids=["bound-to-the-subject", "bound-to-other-bytes", "unreadable", "not-a-mapping"])
def test_a_receipt_counts_only_when_its_custody_binding_names_the_subject_s_bytes(binding, gate):
    planted = Receipt("planted", "check", CODE, A, "pass", 4)
    roles, _ = seen(COUNTED, planted, provenance={"planted": {"binding": binding}})
    assert roles["planted"] == (("supports", "") if not gate else ("not counted", gate))
    parts, refused = custody.receipt_about({"artifact_sha": A}, {"binding": binding})
    assert bool(refused) is bool(gate)


def test_a_receipt_with_no_binding_is_about_its_own_bytes():
    parts, refused = custody.receipt_about({"artifact_sha": B}, None)
    assert parts.artefact == B and refused == ()
    assert custody.receipt_about({"artifact_sha": B}, {"source_kind": "stated"})[0].artefact == B


# --- offline, and a body the issuer could not have written ------------------------------------------------------------


def test_offline_the_roles_follow_the_certificate_s_own_account_of_its_ledger():
    early, late = Receipt("early", "check", CODE, A, "pass", 1), Receipt("late", "check", CODE, A, "pass", 4)
    online, _ = seen(early, late)
    offline, _ = seen(early, late, offline=True)
    assert online == offline == {"early": ("not counted", "control_after_receipt"), "late": ("supports", "")}
    held, _ = seen(COUNTED, late, held=[COUNTED], offline=True)   # its unresolved list names the late receipt
    assert held == {"counted": ("supports", ""), "late": ("not counted", "not_in_ledger")}


def test_a_receipt_the_issuer_cannot_read_is_not_counted():
    ledger = ChainLedger()
    record(ledger, CONTROL, COUNTED)
    body = issue(FACTS, SUBJECT, [check()], [COUNTED], ledger=ledger).as_dict()
    body["receipts"].append({**body["receipts"][0], "receipt_id": "odd", "result": "maybe"})
    body["receipts"].append({**body["receipts"][0], "receipt_id": "extra", "field": 1})
    roles = {e["receipt_id"]: (e["role"], e["gate"]) for e in
             certificate_view(body, artifact_sha256=A, ledger=ledger)["predicates"][0]["evidence"]}
    assert roles == {"counted": ("supports", ""), "odd": ("not counted", "not_a_receipt"),
                     "extra": ("not counted", "not_a_receipt")}


def test_the_view_still_copies_the_issuer_s_status_unchanged():
    roles, statement = seen(COUNTED, Receipt("planted", "check", CODE, B, "pass", 4))
    ledger = ChainLedger()
    record(ledger, CONTROL, COUNTED)
    body = issue(FACTS, SUBJECT, [check()], [COUNTED], ledger=ledger).as_dict()
    view = certificate_view(body, artifact_sha256=A, ledger=ledger)
    assert view["predicates"][0]["status"] == {k: v for k, v in body["predicates"][0].items() if k != "predicate"}
    assert all(e["why"] == WHY.get(e["gate"], "") for e in view["predicates"][0]["evidence"])
