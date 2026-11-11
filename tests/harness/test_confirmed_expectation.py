# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""A person's confirmed answer, in the issuer's terms (G6 0.3 design, section 3.2, step 4; slice 2, item 5). No dialog
here: slice 3 asks the question.

- It is exactly one ``example.v1`` check (the existing template; no second one) with provenance ``person_confirmed``,
  bound to the digest of the question the person was shown and to the artefact's digest.
- Only a person confirms it: a non-answer, or an answer from anything else, leaves it unconfirmed.
- It is kept apart from any tentative model of intent, and a model-originated check never becomes normative.
- A later answer that contradicts it starts a superseding standard, never an overwrite, and the evidence issued under
  the old standard no longer counts under the new one.
- In the issuer's terms it is an independent check whose author is the person; like any other, it counts only with a
  negative control and a receipt on the bytes it is enforced on.
"""
from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from g6.core.ledger import ChainLedger
from g6.evidence import Artifact, CertificateRefused, Control, MissionFacts, Receipt, issue, record, verify
from g6.evidence import mapping as m
from g6.harness import patch_review, verification_templates
from g6.harness.confirmed_expectation import (
    NORMATIVE,
    NOT_CONFIRMING,
    PERSON_CONFIRMED,
    ContradictsStandard,
    ExpectationStandard,
    Question,
    Unconfirmed,
    confirm,
    normative,
)
from g6.harness.verification_templates import TemplateInstance

BASE = hashlib.sha256(b"the base commit's tree").hexdigest()
PATCH = b"the final patch"
PATCH_SHA = hashlib.sha256(PATCH).hexdigest()


def question(args=None, **over) -> Question:
    base = {"requirement_id": "R1", "function": "shop.prices.total", "args": args or [[2, 3]],
            "outcomes": {"A": {"returns": 5}, "B": {"raises": "ValueError"}}, "scope": "this one case",
            "cited": "the task: total adds the prices", "baseline_sha256": BASE}
    return Question(**{**base, **over})


def test_a_confirmed_answer_is_one_example_v1_check_with_the_person_s_provenance():
    got = confirm(question(), "A", answered_by="person:owner")
    one = got.instance
    assert one.template == "example.v1" and one.provenance == PERSON_CONFIRMED == got.provenance
    assert one.bindings == {"function": "shop.prices.total", "cases": [{"args": [[2, 3]], "returns": 5}]}
    assert set(verification_templates.TEMPLATES) == {"example.v1", "relation.v1", "access_rule.v1",
                                                     "differential.v1", "forbidden_dependency.v1"}   # no second one
    # it is an instance the existing template takes, as G6 builds it (a request cannot set provenance)
    assert verification_templates.template_instances([one], {"R1"}) == [one]
    with pytest.raises(ValueError, match="provenance"):
        verification_templates.template_instances([one.model_dump()], {"R1"})
    assert got.check_sha256 == verification_templates.instance_digest(one)


def test_it_is_bound_to_the_question_the_person_was_shown_and_to_the_artefact():
    shown = question()
    got = confirm(shown, "A", answered_by="person:owner")
    assert got.question_sha256 == shown.sha256 and got.artefact_sha256 == BASE
    assert got.options_sha256 == shown.options_sha256 and got.for_question(shown) is got
    for changed in (question(args=[[2, 2]]), question(scope="every list"), question(cited="another line"),
                    question(baseline_sha256="0" * 64), question(outcomes={"A": {"returns": 6}, "B": {"returns": 5}})):
        assert changed.sha256 != shown.sha256
        with pytest.raises(ValueError, match="question"):
            got.for_question(changed)
    assert confirm(shown, "B", answered_by="person:owner").check_sha256 != got.check_sha256


@pytest.mark.parametrize("answer", [*NOT_CONFIRMING, "C", ""])
def test_a_non_answer_leaves_the_expectation_unconfirmed(answer):
    with pytest.raises(Unconfirmed):
        confirm(question(), answer, answered_by="person:owner")


@pytest.mark.parametrize("who", ["agent", "model:claude", "person:", "", "g6"])
def test_only_a_person_confirms_no_agent_or_model_answers_on_their_behalf(who):
    with pytest.raises(Unconfirmed):
        confirm(question(), "A", answered_by=who)


def test_a_model_originated_check_never_becomes_normative():
    assert normative("model_hypothesis") is False and normative("inferred") is False
    assert NORMATIVE == patch_review.NORMATIVE and all(normative(p) for p in NORMATIVE)
    got = confirm(question(), "A", answered_by="person:owner")
    hypothesis = TemplateInstance(**{**got.instance.model_dump(), "provenance": "model_hypothesis"})
    with pytest.raises(ValueError, match="person"):
        replace(got, instance=hypothesis)
    with pytest.raises(ValueError, match="person"):
        replace(got, provenance="model_hypothesis")
    # patch_review gives a model's hypothesis no status, and its checks never run
    assert "model_hypothesis" not in patch_review.NORMATIVE


def test_the_standard_holds_confirmed_expectations_only_kept_apart_from_any_model_of_intent():
    got = confirm(question(), "A", answered_by="person:owner")
    standard = ExpectationStandard().with_expectation(got)
    assert standard.predicates == (got.expectation_id,)
    with pytest.raises(TypeError, match="tentative"):
        ExpectationStandard((got.instance,))
    with pytest.raises(TypeError, match="tentative"):
        ExpectationStandard(({"requirement": "R1", "provenance": "model_hypothesis"},))


def test_a_contradicting_answer_starts_a_superseding_standard_and_the_old_evidence_stops_counting():
    first = confirm(question(), "A", answered_by="person:owner")
    standard = ExpectationStandard().with_expectation(first)
    later = confirm(question(), "B", answered_by="person:owner")   # the same case, the other outcome
    assert standard.contradicted_by(later) == (first.expectation_id,)
    with pytest.raises(ContradictsStandard):
        standard.with_expectation(later)   # never an overwrite
    superseding = standard.superseding(later)
    assert superseding.supersedes == standard.sha256 and superseding.replaced == (first.expectation_id,)
    assert superseding.predicates == (later.expectation_id,) and standard.predicates == (first.expectation_id,)
    assert superseding.revision == standard.revision + 1
    # evidence for the old standard: verified under it, and refused or flagged under the new one
    control = Control("k1", first.check_sha256, first.expectation_id, "g6", 3, True)
    check = first.check(frozen_seq=0, controls=(control,))
    receipt = Receipt("r1", check.check_id, check.check_sha, PATCH_SHA, "pass", 4)
    ledger = ChainLedger()
    record(ledger, control, receipt)
    old = issue(standard.facts("1" * 64), Artifact(PATCH_SHA, "agent", 1), [check], [receipt], ledger=ledger)
    assert old.verified_predicates == (first.expectation_id,)
    assert "the certificate is under another standard" in verify(old.as_dict(), PATCH, standard_sha=superseding.sha256)
    stale = MissionFacts(mission_sha="1" * 64, mission_standard_sha=standard.sha256, standard_sha=superseding.sha256,
                         predicates=superseding.predicates, standard_predicates=superseding.predicates)
    with pytest.raises(CertificateRefused, match="standard"):
        issue(stale, Artifact(PATCH_SHA, "agent", 1), [check], [receipt], ledger=ledger)


def test_a_consistent_answer_adds_a_revision_and_two_answers_to_one_case_cannot_both_stand():
    first = confirm(question(), "A", answered_by="person:owner")
    other = confirm(question(args=[[1, 1]], outcomes={"A": {"returns": 2}, "B": {"returns": 3}}), "A",
                    answered_by="person:owner")
    standard = ExpectationStandard().with_expectation(first).with_expectation(other)
    assert standard.predicates == (first.expectation_id, other.expectation_id) and standard.revision == 3
    assert standard.with_expectation(first) is standard
    with pytest.raises(ValueError, match="nothing is contradicted"):
        standard.superseding(other)
    with pytest.raises(ContradictsStandard):
        ExpectationStandard((first, confirm(question(), "B", answered_by="person:owner")))


def test_in_the_issuer_s_terms_it_counts_only_with_a_control_and_a_receipt_on_the_patch():
    got = confirm(question(), "A", answered_by="person:owner")
    standard = ExpectationStandard().with_expectation(got)
    check = got.check(frozen_seq=0)
    assert (check.kind, check.author, check.check_sha, check.predicates) == (
        "independent_executable", "person:owner", got.check_sha256, (got.expectation_id,))
    row = m.MAPPING[PERSON_CONFIRMED]
    assert row.issuer_kind == "independent_executable" and row.source == "patch_review"
    rec = m.SourceRecord(kind=PERSON_CONFIRMED, record_id="r1", predicates=(got.expectation_id,), result="pass",
                         artefact_sha256=PATCH_SHA, producer="the patch's author", artefact_seq=1,
                         standard_sha256=standard.sha256, check_id=got.expectation_id, check_sha256=got.check_sha256,
                         author=got.answered_by, frozen_seq=0, environment_sha256="f" * 64, seq=3,
                         scope="the confirmed case, on the final patch")
    lone = m.issue_records(standard.facts("1" * 64), Artifact(PATCH_SHA, "the patch's author", 1), [rec])
    assert [c for c, _ in lone.refused[0][1]] == [m.NO_CONTROL] and lone.certificate.verified_predicates == ()
    controlled = replace(rec, controls=(Control("k1", got.check_sha256, got.expectation_id, "g6", 2, True),))
    held = m.issue_records(standard.facts("1" * 64), Artifact(PATCH_SHA, "the patch's author", 1), [controlled])
    assert held.certificate.verified_predicates == (got.expectation_id,)
