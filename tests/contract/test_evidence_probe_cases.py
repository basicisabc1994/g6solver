# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The two probe cases of G6 0.3's evidence slice (design section 5, the Evidence row; bar: all refused).

1. A formal check with no negative control is refused through every path that can carry it to a status: the issuer,
   ECHA's claim rule and its view over the issuer's records, the mapping table's promotion, and the patch review's
   adapter.
2. Evidence bound to one artefact cannot be retargeted to another, a patch that never ran: the claim is bound to its
   subject's bytes, and a different subject is refused, through the same paths.

Each refusal has a positive control beside it (the same path counts the evidence when nothing is wrong), so a path that
refuses everything cannot pass.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from g6.core.ledger import ChainLedger
from g6.evidence import Artifact, Check, Control, MissionFacts, Receipt, issue, record, verify

PATCH_A = b"def total(xs):\n    return sum(xs)\n"
PATCH_B = b"def total(xs):\n    return 0\n"          # a patch that never ran
A = hashlib.sha256(PATCH_A).hexdigest()
B = hashlib.sha256(PATCH_B).hexdigest()
STANDARD = "5" * 64
CODE = "c" * 64
CERT = "d" * 64
MODEL = "e" * 64
ENV = "f" * 64
P = "total_sums"


def facts() -> MissionFacts:
    return MissionFacts(mission_sha="1" * 64, mission_standard_sha=STANDARD, standard_sha=STANDARD, predicates=(P,),
                        standard_predicates=(P,))


def control(**over) -> Control:
    return Control(**{"control_id": "k1", "check_sha": CODE, "predicate": P, "generated_by": "g6", "seq": 2,
                      "killed": True, **over})


def check(kind="independent_executable", controls=(), **over) -> Check:
    return Check(**{"check_id": "c1", "predicates": (P,), "author": "evaluator", "kind": kind, "check_sha": CODE,
                    "frozen_seq": 0, "controls": tuple(controls), **over})


def receipt(on: str = A) -> Receipt:
    return Receipt("r1", "c1", CODE, on, "pass", 3)


def issued(chk: Check, rec: Receipt, artefact: str = A):
    ledger = ChainLedger()
    record(ledger, *chk.controls, rec)
    return issue(facts(), Artifact(artefact, "agent", 1), [chk], [rec], ledger=ledger)


# --- 1. a formal check with no negative control ------------------------------------------------------------------------


def test_the_issuer_refuses_a_formal_check_with_no_negative_control_and_counts_one_with_its_control():
    lone = issued(check("formal_checked", certificate=CERT), receipt())
    assert lone.state != "verified" and lone.verified_predicates == () and lone.formal == ()
    assert "no_negative_control" in dict(lone.gaps)[f"predicate:{P}"]
    controlled = issued(check("formal_checked", [control()], certificate=CERT), receipt())   # positive control
    assert controlled.verified_predicates == (P,) and controlled.formal == ((P, "c1", CERT),)


def _echa_ledger():
    from g6.research.echa import EvidenceRecord, Ledger, StandardRef

    std = StandardRef.from_text("std", "total sums its inputs", "task", (P,))
    led = Ledger()
    led.add_standard(std)
    led.add_vault("v", CERT, files=1, roots=1)

    def ev(evidence_id, **over):
        base = {"evidence_id": evidence_id, "predicate_id": P, "oracle_category": "FORMAL_CHECK",
                "oracle_version": "solver@1", "fixture_version": CERT, "independent": True,
                "independence_group": "solver", "independence_rationale": "a checker apart from the producer",
                "result": "pass", "scope": "lemma L", "known_limitations": ("relative to the formalisation",),
                "source_ref": f"log:{evidence_id}", "standard_sha256": std.standard_sha256,
                "negative_control_ref": "", "purpose": "check"}
        return EvidenceRecord(**{**base, **over})
    return led, std, ev


def _claim(std, claim_id, evidence_ids, subject="patch p1", breadth="covered_cases"):
    from g6.research.echa import ClaimRecord

    return ClaimRecord(claim_id=claim_id, subject=subject, predicate_id=P, scope="lemma L", breadth=breadth,
                       estimand="oracle_scoped", population="one task", sample_unit="one task", provenance="test",
                       assumptions=("the formalisation is the requirement",), known_limitations=("one lemma",),
                       versions={"standard": "std"}, evidence_ids=tuple(evidence_ids),
                       standard_sha256=std.standard_sha256)


def test_echa_refuses_a_formal_check_with_no_negative_control_and_counts_one_with_its_control():
    from g6.research.echa import evaluate_claim

    led, std, ev = _echa_ledger()
    led.add_evidence(ev("f-lone"))
    led.add_claim(_claim(std, "c-lone", ["f-lone"], breadth="general"))
    lone = evaluate_claim(led, "c-lone")
    assert lone["result"] != "supported" and lone["active_evidence"] == [] and lone["structural"] == ["f-lone"]
    led.add_evidence(ev("f-ctl", purpose="negative_control", result="fail", scope="a seeded defect the check rejects"))
    led.add_evidence(ev("f-held", negative_control_ref="f-ctl"))
    led.add_claim(_claim(std, "c-held", ["f-held"], breadth="general"))
    held = evaluate_claim(led, "c-held")   # positive control: the same check with its control is active
    assert held["result"] == "supported" and held["active_evidence"] == ["f-held"]


def _formal_record(controls=(), **over):
    from g6.evidence.mapping import SourceRecord

    base = {"kind": "formal_checked", "record_id": "r1", "predicates": (P,), "result": "pass", "artefact_sha256": A,
            "producer": "agent", "artefact_seq": 1, "standard_sha256": STANDARD, "check_id": "c1",
            "check_sha256": CODE, "author": "evaluator", "frozen_seq": 0, "environment_sha256": ENV, "seq": 3,
            "scope": "lemma L on these bytes", "model_sha256": MODEL, "certificate": CERT,
            "controls": tuple(controls)}
    return SourceRecord(**{**base, **over})


def test_the_mapping_refuses_to_promote_a_formal_check_with_no_negative_control():
    from g6.evidence.mapping import NO_CONTROL, PromotionRefused, issue_records, promote

    with pytest.raises(PromotionRefused) as refused:
        promote(_formal_record(), facts=facts(), artifact=Artifact(A, "agent", 1))
    assert [code for code, _ in refused.value.codes] == [NO_CONTROL]
    with pytest.raises(PromotionRefused) as echa:   # ECHA's category maps to the same row rule
        promote(_formal_record(kind="FORMAL_CHECK"), facts=facts(), artifact=Artifact(A, "agent", 1))
    assert NO_CONTROL in [code for code, _ in echa.value.codes]
    out = issue_records(facts(), Artifact(A, "agent", 1), [_formal_record()])
    assert out.promoted == () and out.certificate.verified_predicates == ()
    held = issue_records(facts(), Artifact(A, "agent", 1), [_formal_record([control()])])   # positive control
    assert held.refused == () and held.certificate.verified_predicates == (P,)


def test_echa_s_view_of_the_issuer_shows_the_issuer_s_refusal_of_a_formal_check_with_no_control():
    from g6.evidence.mapping import issue_records
    from g6.research.echa.view import certificate_view

    lone = issue_records(facts(), Artifact(A, "agent", 1), [_formal_record()])
    seen = certificate_view(lone.certificate.as_dict(), artifact_sha256=A, ledger=lone.ledger)
    assert seen["predicates"][0]["status"]["support"] is False
    held = issue_records(facts(), Artifact(A, "agent", 1), [_formal_record([control()])])
    assert certificate_view(held.certificate.as_dict(), artifact_sha256=A)["predicates"][0]["status"]["support"] is True


def test_the_patch_review_adapter_never_promotes_a_model_result_or_a_compiled_check():
    from g6.harness.requirement_evidence import records_for

    req = {"id": P, "text": "total sums", "kind": "requirement", "provenance": "stated",
           "checks": [{"id": "state_invariant-1", "provenance": "proposed", "verdict": "passed"}]}
    _facts, _artefact, records = records_for([req], [], artifact=A, base_commit="0" * 40, check_sha={},
                                             written=lambda _check: False, environment={"python": "3"})
    from g6.evidence.mapping import NOT_PROMOTED, problems

    assert [problems(r, facts=_facts, artifact=_artefact)[0][0]
            for r in records] == [NOT_PROMOTED]


# --- 2. evidence bound to one artefact, retargeted to another --------------------------------------------------------


def test_the_issuer_refuses_a_receipt_taken_on_other_bytes_and_a_certificate_read_for_other_bytes():
    on_a = issued(check(controls=[control()]), receipt(on=A))
    assert on_a.verified_predicates == (P,)   # positive control
    retargeted = issued(check(controls=[control()]), receipt(on=A), artefact=B)
    assert retargeted.verified_predicates == () and "receipt_for_other_bytes" in dict(retargeted.gaps)[f"predicate:{P}"]
    assert "the artifact bytes are not the ones the certificate was issued for" in verify(on_a.as_dict(), PATCH_B)


def _bound(evidence_id, artefact, std, ev, **over):
    from g6.evidence.mapping import CheckBinding
    from g6.research.echa.records import canonical

    binding = CheckBinding(request_id="r-1", task_id="t-1", predicate_id=P, artifact_sha256=artefact,
                           standard_sha256=std.standard_sha256, fixture_sha256=CERT, evaluator_sha256=CODE)
    return ev(evidence_id, oracle_category="INDEPENDENT_EXECUTABLE", oracle_version="probe@1",
              source_ref=canonical(binding.to_dict()), **over)


def test_echa_refuses_evidence_bound_to_one_artefact_for_a_claim_about_another():
    from g6.research.echa import evaluate_claim

    led, std, ev = _echa_ledger()
    led.add_evidence(_bound("k", A, std, ev, purpose="negative_control", result="fail", scope="a seeded defect"))
    led.add_evidence(_bound("e-a", A, std, ev, negative_control_ref="k"))
    led.add_claim(_claim(std, "c-a", ["e-a"], subject=f"sha256:{A}"))
    assert evaluate_claim(led, "c-a")["result"] == "supported"   # positive control: its own subject
    led.add_claim(_claim(std, "c-b", ["e-a"], subject=f"sha256:{B}"))
    to_b = evaluate_claim(led, "c-b")
    assert to_b["result"] != "supported" and "evidence_for_another_subject" in to_b["reasons"]
    led.add_claim(_claim(std, "c-text", ["e-a"], subject="patch p2, which never ran"))
    to_text = evaluate_claim(led, "c-text")
    assert to_text["result"] != "supported" and "evidence_for_another_subject" in to_text["reasons"]
    led.add_evidence(ev("e-free", oracle_category="INDEPENDENT_EXECUTABLE", oracle_version="probe@1",
                        negative_control_ref="", scope="unbound"))
    led.add_claim(_claim(std, "c-b2", ["e-free"], subject=f"sha256:{B}"))
    unbound = evaluate_claim(led, "c-b2")
    assert unbound["result"] != "supported" and "evidence_not_bound_to_subject" in unbound["reasons"]


def test_the_mapping_refuses_to_promote_a_record_taken_on_other_bytes():
    from g6.evidence.mapping import OTHER_ARTEFACT, PromotionRefused, issue_records, promote

    rec = _formal_record([control()], kind="independent_executable", model_sha256="", certificate="")
    promote(rec, facts=facts(), artifact=Artifact(A, "agent", 1))   # positive control
    with pytest.raises(PromotionRefused) as refused:
        promote(rec, facts=facts(), artifact=Artifact(B, "agent", 1))
    assert [code for code, _ in refused.value.codes] == [OTHER_ARTEFACT]
    out = issue_records(facts(), Artifact(B, "agent", 1), [rec])
    assert out.promoted == () and out.certificate.verified_predicates == ()


def test_echa_s_view_never_shows_a_certificate_for_other_bytes_as_the_subject_s():
    from g6.evidence.mapping import issue_records
    from g6.research.echa.view import certificate_view

    rec = _formal_record([control()], kind="independent_executable", model_sha256="", certificate="")
    on_a = issue_records(facts(), Artifact(A, "agent", 1), [rec])
    assert certificate_view(on_a.certificate.as_dict(), artifact_sha256=A)["artifact_sha256"] == A
    with pytest.raises(ValueError, match="other bytes"):
        certificate_view(on_a.certificate.as_dict(), artifact_sha256=B)


def test_the_patch_review_adapter_binds_every_record_to_the_review_s_own_artefact():
    from g6.evidence.mapping import OTHER_ARTEFACT, problems
    from g6.harness.requirement_evidence import records_for

    req = {"id": P, "text": "total sums", "kind": "requirement", "provenance": "stated",
           "checks": [{"id": "tests/test_total.py::test_sum", "provenance": "stated", "verdict": "passed"}]}
    runs = [{"tier": 2, "check": "stated checks at the base commit",
             "outcomes": {"tests/test_total.py::test_sum": "failed"}}]
    got_facts, artefact, records = records_for([req], runs, artifact=A, base_commit="0" * 40,
                                               check_sha={"tests/test_total.py::test_sum": CODE},
                                               written=lambda _check: False, environment={"python": "3"})
    assert artefact.sha256 == A and [r.artefact_sha256 for r in records] == [A]
    assert problems(records[0], facts=got_facts, artifact=artefact) == []
    assert [code for code, _ in problems(records[0], facts=got_facts,
                                         artifact=Artifact(B, artefact.producer, artefact.seq))] == [OTHER_ARTEFACT]


def test_a_confirmed_expectation_is_bound_to_the_question_it_answers():
    from g6.harness.confirmed_expectation import Question, confirm

    question = Question(requirement_id=P, function="shop.prices.total", args=[[2, 3]],
                        outcomes={"A": {"returns": 5}, "B": {"returns": 6}}, scope="this one case",
                        cited="the task says total adds the prices", baseline_sha256=A)
    other = Question(requirement_id=P, function="shop.prices.total", args=[[2, 2]],
                     outcomes={"A": {"returns": 4}, "B": {"returns": 5}}, scope="this one case",
                     cited="the task says total adds the prices", baseline_sha256=A)
    got = confirm(question, "A", answered_by="person:owner")
    assert got.question_sha256 == question.sha256 and got.artefact_sha256 == A
    assert got.question_sha256 != other.sha256
    with pytest.raises(ValueError, match="question"):
        got.for_question(other)   # an answer to one question never stands for another
    assert json.loads(json.dumps(got.to_dict()))["provenance"] == "person_confirmed"
