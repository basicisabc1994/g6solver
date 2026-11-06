# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""ECHA is an audit and analysis view over the issuer's records, not a second authority (G6 0.3 design, section 6).

- Its status never overrides the issuer's: the view copies each predicate's statement, also where ECHA's own claim rule,
  given the same evidence, would have said otherwise.
- Its feature flag is gone, and every entry point works without it.
- Its custody binding is the structure a promoted receipt is bound by; its own records are retired as a source of
  evidence: none of them is promoted.
- Its competence measure is retired for selection and kept as a research analysis, and says so.
"""
from __future__ import annotations

import hashlib
import importlib

import pytest

from g6.core.ledger import ChainLedger
from g6.evidence import Artifact, Check, Control, MissionFacts, Receipt, issue, record
from g6.evidence import mapping as m
from g6.research.echa import ClaimRecord, EvidenceRecord, Ledger, StandardRef, evaluate_claim
from g6.research.echa import example as kernel_example
from g6.research.echa.view import STATUS_FIELDS, certificate_view, source_record

DATA = b"the artefact"
A = hashlib.sha256(DATA).hexdigest()
CODE = "c" * 64
P = "p"


def _issued(author: str) -> tuple[dict, ChainLedger]:
    std = "5" * 64
    facts = MissionFacts(mission_sha="1" * 64, mission_standard_sha=std, standard_sha=std, predicates=(P,),
                         standard_predicates=(P,))
    control = Control("k1", CODE, P, "g6", 2, True)
    check = Check("c1", (P,), author, "independent_executable", CODE, 0, (control,))
    receipt = Receipt("r1", "c1", CODE, A, "pass", 3)
    ledger = ChainLedger()
    record(ledger, control, receipt)
    return issue(facts, Artifact(A, "agent", 1), [check], [receipt], ledger=ledger).as_dict(), ledger


def _echa_says(independent: bool = True) -> str:
    """ECHA's own claim rule on the same evidence: one independent executable check with a control that failed."""
    std = StandardRef.from_text("s", "p holds", "task", (P,))
    led = Ledger()
    led.add_standard(std)
    led.add_vault("v", "e" * 64, files=1, roots=1)
    base = {"predicate_id": P, "oracle_category": "INDEPENDENT_EXECUTABLE", "oracle_version": "o@1",
            "fixture_version": "e" * 64, "independent": independent, "independence_group": "g",
            "independence_rationale": "held apart", "scope": "the inputs", "known_limitations": ("some inputs",),
            "source_ref": "log", "standard_sha256": std.standard_sha256}
    led.add_evidence(EvidenceRecord(evidence_id="k", result="fail", purpose="negative_control", **base))
    led.add_evidence(EvidenceRecord(evidence_id="e", result="pass", negative_control_ref="k", **base))
    led.add_claim(ClaimRecord(claim_id="c", subject="the artefact", predicate_id=P, scope="the inputs",
                              breadth="covered_cases", estimand="oracle_scoped", population="one", sample_unit="one",
                              provenance="test", assumptions=("a",), known_limitations=("l",), versions={"s": "1"},
                              evidence_ids=("e",), standard_sha256=std.standard_sha256))
    return evaluate_claim(led, "c")["result"]


def test_the_view_copies_the_issuer_s_statement_and_never_overrides_it():
    body, ledger = _issued(author="agent")   # the artefact's producer wrote the check: the issuer does not count it
    assert _echa_says() == "supported"     # ECHA's own rule has no author, so on its records it would say supported
    seen = certificate_view(body, artifact_sha256=A, ledger=ledger)
    for got, statement in zip(seen["predicates"], body["predicates"], strict=True):
        assert got["status"] == {field: statement[field] for field in STATUS_FIELDS}
    assert seen["predicates"][0]["status"]["support"] is False and seen["state"] == body["state"]
    assert "self_authored" in seen["predicates"][0]["gap"]
    assert seen["authority"].startswith("g6.evidence.certificate")


def test_the_view_explains_what_the_issuer_counted_and_where_each_record_is():
    body, ledger = _issued(author="evaluator")
    seen = certificate_view(body, artifact_sha256=A, ledger=ledger)
    evidence = seen["predicates"][0]["evidence"][0]
    assert seen["predicates"][0]["status"]["supported_by"] == ["c1"] and evidence["role"] == "supports"
    assert evidence["receipt_in_ledger"] is True and evidence["controls"][0]["in_ledger"] is True
    offline = certificate_view(body, artifact_sha256=A)
    assert offline["predicates"][0]["evidence"][0]["receipt_in_ledger"] is None   # no ledger: unknown, never True
    forged = ChainLedger()
    record(forged, Receipt("r1", "c1", CODE, A, "fail", 3))
    assert certificate_view(body, artifact_sha256=A, ledger=forged)["predicates"][0]["evidence"][0][
        "receipt_in_ledger"] is False


def test_the_view_refuses_a_body_that_is_not_a_certificate():
    with pytest.raises(ValueError, match="does not read as an issuer certificate"):
        certificate_view({"artifact_sha": A}, artifact_sha256=A)
    body, _ = _issued(author="evaluator")
    with pytest.raises(ValueError, match="does not read as an issuer certificate"):
        certificate_view({**body, "predicates": [{"predicate": P}]}, artifact_sha256=A)


def test_the_flag_is_gone_and_every_entry_point_works_without_it(monkeypatch, tmp_path):
    monkeypatch.delenv("G6_ECHA_RESEARCH", raising=False)
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("g6.research.echa.flag")
    import g6.research.echa as echa

    assert not {"FLAG", "ResearchDisabled", "require_research", "research_enabled"} & set(echa.__all__)
    ledger = Ledger(tmp_path / "ledger.jsonl")
    claims = kernel_example.build(ledger)
    assert evaluate_claim(ledger, claims["supported"])["result"] == "supported"


def test_the_custody_binding_is_the_one_a_promoted_receipt_is_bound_by():
    custody = importlib.import_module("g6.research.echa.custody")
    assert custody.CheckBinding is m.CheckBinding
    binding = m.CheckBinding(request_id="r", task_id="t", predicate_id=P, artifact_sha256=A, standard_sha256="5" * 64,
                             fixture_sha256="e" * 64, evaluator_sha256=CODE)
    std = StandardRef.from_text("s", "p holds", "task", (P,))

    def rec(source_ref: str) -> EvidenceRecord:
        return EvidenceRecord(evidence_id="e", predicate_id=P, oracle_category="PARTIAL_FALSIFIER", oracle_version="o",
                              fixture_version="unsealed", independent=False, independence_group="",
                              independence_rationale="", result="pass", scope="s", known_limitations=(),
                              source_ref=source_ref, standard_sha256=std.standard_sha256)
    from g6.research.echa.records import canonical

    assert custody.binding_of(rec(canonical(binding.to_dict()))) == binding
    # read as strictly as a receipt: a repeated key is no binding (the last one would otherwise win), nor is free text
    doubled = canonical(binding.to_dict())[:-1] + ',"artifact_sha256":"' + "b" * 64 + '"}'
    assert custody.binding_of(rec(doubled)) is None and custody.binding_of(rec("log:1")) is None


def test_echa_s_own_records_are_retired_as_a_source_of_evidence_none_is_promoted(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    kernel_example.build(ledger)
    records = [EvidenceRecord.from_dict(e["body"]) for e in ledger.entries() if e["kind"] == "evidence"]
    assert records
    for rec in records:
        source = source_record(rec)
        found = [code for code, _ in m.problems(source, artefact_sha256=A, standard_sha256=rec.standard_sha256,
                                                predicates=(rec.predicate_id,))]
        assert found, rec.evidence_id
        assert m.NO_CONTROL in found or m.NOT_PROMOTED in found, (rec.evidence_id, found)


def test_competence_is_retired_for_selection_and_kept_as_a_research_analysis():
    competence = importlib.import_module("g6.research.echa.competence")
    doc = " ".join((competence.__doc__ or "").split()).lower()
    assert "retired for selection" in doc and "research analysis" in doc
