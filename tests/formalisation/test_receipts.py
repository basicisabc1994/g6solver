# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""A formal result reaches the issuer only with a resolvable acceptance, a shown correspondence and a control."""
from __future__ import annotations

import dataclasses
import hashlib

import pytest

z3 = pytest.importorskip("z3")

from g6.evidence import Artifact, MissionFacts  # noqa: E402
from g6.evidence import mapping as m  # noqa: E402
from g6.harness.formalisation import receipts  # noqa: E402
from g6.harness.formalisation.adapters import from_expectation  # noqa: E402
from g6.harness.formalisation.checks import CheckOutcome, assess  # noqa: E402
from g6.harness.formalisation.code_check import check_code  # noqa: E402
from g6.harness.formalisation.receipts import FormalEvidence, problems, seeded_control, source_record  # noqa: E402
from g6.harness.confirmed_expectation import ExpectationStandard  # noqa: E402
from tests.formalisation._cases import confirmed, relation  # noqa: E402
from tests.formalisation.test_code_check import RIGHT, WRONG, running  # noqa: E402

FIRST = confirmed([3], {"A": {"returns": 4}, "B": {"returns": 5}})
STANDARD = ExpectationStandard((FIRST,))
CASES = (from_expectation(FIRST),)
STATEMENT = relation("(= result (+ x 1))")
ASSESSED = assess(STATEMENT, STANDARD, CASES, comparison=CheckOutcome("comparison", "passed", ()))
PATCH = hashlib.sha256(RIGHT.encode()).hexdigest()
ENV = "f" * 64
P = FIRST.expectation_id


def store(*, without=()):
    """A content-addressed store holding every part the acceptance binds, and the acceptance itself."""
    held = receipts.held_parts(STATEMENT, ASSESSED.rendering, ASSESSED.scope, STANDARD)
    accepted = receipts.acceptance_record(scope_sha256=ASSESSED.scope.sha256, assessment_sha256=ASSESSED.sha256,
                                          accepted_by="person:owner")
    held[hashlib.sha256(accepted).hexdigest()] = accepted
    for sha in without:
        held.pop(sha)
    return held, hashlib.sha256(accepted).hexdigest()


def evidence(source=RIGHT, *, run=None, **over) -> FormalEvidence:
    _, accepted = store()
    code = check_code(STATEMENT, source, "f", run=run or running(source))
    sha = receipts.check_sha256(STATEMENT, ASSESSED.scope)
    control = seeded_control(STATEMENT, CASES[0], source=source, fn_name="f", run=run or running(source),
                             check_sha=sha, predicate=P, seq=2)
    return FormalEvidence(**{
        "acceptance_sha256": accepted, "statement": STATEMENT, "scope": ASSESSED.scope,
        "assessment_sha256": ASSESSED.sha256, "code": code, "controls": (control,), "author": "model:openai",
        "engine": "z3", "engine_version": code.query.query.engine_version if code.query else "",
        "environment_sha256": ENV, "artefact_sha256": PATCH, "producer": "model:anthropic", "artefact_seq": 1,
        "frozen_seq": 0, "seq": 3, "standard_sha256": STANDARD.sha256, "predicates": (P,), **over})


def facts(standard=STANDARD) -> MissionFacts:
    return MissionFacts(mission_sha="1" * 64, mission_standard_sha=standard.sha256, standard_sha=standard.sha256,
                        predicates=standard.predicates, standard_predicates=standard.predicates)


ARTEFACT = Artifact(PATCH, "model:anthropic", 1)


def test_accepted_statement_with_correspondence_and_control_is_promoted_as_formal_checked():
    held, _ = store()
    found = evidence()
    assert problems(found, resolve=held.get) == []
    rec = source_record(found, resolve=held.get)
    assert rec.kind == "formal_accepted" and rec.result == "pass" and rec.model_sha256 == STATEMENT.sha256
    check, receipt = m.promote(rec, facts=facts(), artifact=ARTEFACT)
    assert check.kind == "formal_checked" and check.author == "model:openai" and check.certificate == rec.certificate
    assert check.controls[0].killed is True and receipt.artifact_sha == PATCH
    issued = m.issue_records(facts(), ARTEFACT, [rec])
    assert issued.certificate.verified_predicates == (P,) and issued.certificate.formal[0][0] == P


def test_model_only_result_is_a_lead():
    held, _ = store()
    found = evidence(run=None)
    found = dataclasses.replace(found, code=check_code(STATEMENT, RIGHT, "f", run=None))
    assert found.code.query.decided and found.code.result == "not_established"
    rec = source_record(found, resolve=held.get)
    assert rec.kind == "formal_model_result" and rec.result == "unknown"
    with pytest.raises(m.PromotionRefused) as refused:
        m.promote(rec, facts=facts(), artifact=ARTEFACT)
    assert [code for code, _ in refused.value.codes] == [m.NOT_PROMOTED]
    assert (m.NO_CORRESPONDENCE, ) == tuple(code for code, _ in problems(found, resolve=held.get))


@pytest.mark.parametrize("part", ["statement", "definitions", "assumptions", "rendering", "acceptance", "standard"])
def test_unresolvable_digest_refused(part):
    _, accepted = store()
    sha = {"statement": STATEMENT.sha256, "definitions": STATEMENT.definitions_sha256,
           "assumptions": STATEMENT.assumptions_sha256, "rendering": ASSESSED.rendering.sha256,
           "acceptance": accepted, "standard": STANDARD.sha256}[part]
    held, _ = store(without=(sha,))
    found = problems(evidence(), resolve=held.get)
    assert found and all(code == m.NO_ACCEPTANCE for code, _ in found)
    assert any(why.startswith(f"{part}:") for _, why in found)
    assert source_record(evidence(), resolve=held.get).kind == "formal_model_result"
    # a store that answers with other bytes resolves nothing either
    other = {**store()[0], sha: b"other bytes"}
    assert any(why.startswith(f"{part}:") for _, why in problems(evidence(), resolve=other.get))


def test_unregistered_adapter_refused():
    held, _ = store()
    scope = dataclasses.replace(ASSESSED.scope, adapter="smt.return.int_bool.v0")
    found = problems(evidence(scope=scope), resolve=held.get)
    assert (m.NO_ACCEPTANCE, "adapter: 'smt.return.int_bool.v0' is not the statement's registered adapter") in found


def test_no_acceptance_yet_is_a_lead():
    held, _ = store()
    found = evidence(acceptance_sha256="")
    assert any(why.startswith("acceptance: no person's acceptance") for _, why in problems(found, resolve=held.get))
    assert source_record(found, resolve=held.get).kind == "formal_model_result"


def test_an_acceptance_of_another_scope_or_by_an_agent_does_not_count():
    held, _ = store()
    for record in (receipts.acceptance_record(scope_sha256="0" * 64, assessment_sha256=ASSESSED.sha256,
                                              accepted_by="person:owner"),
                   receipts.acceptance_record(scope_sha256=ASSESSED.scope.sha256, assessment_sha256="0" * 64,
                                              accepted_by="person:owner"),
                   receipts.acceptance_record(scope_sha256=ASSESSED.scope.sha256, assessment_sha256=ASSESSED.sha256,
                                              accepted_by="agent:claude"), b"not json"):
        sha = hashlib.sha256(record).hexdigest()
        found = problems(evidence(acceptance_sha256=sha), resolve={**held, sha: record}.get)
        assert any(why.startswith("acceptance:") for _, why in found)


def test_true_model_bad_code_correspondence():
    """The relation holds of the function as read; the function that runs is another one (it reads a global)."""
    held, _ = store()
    module = "OFFSET = 2\ndef f(x):\n    return x + 1\ndef g(x):\n    return x + OFFSET\nf = g\n"
    found = evidence(RIGHT, run=running(module))
    assert found.code.query.reading == "entailed_under_assumptions" and found.code.replayed == "not_reproduced"
    assert [code for code, _ in problems(found, resolve=held.get)] == [m.NO_CORRESPONDENCE]
    assert source_record(found, resolve=held.get).kind == "formal_model_result"


def test_reproduced_counterexample_is_a_refutation_the_issuer_reads():
    held, _ = store()
    found = evidence(WRONG, artefact_sha256=hashlib.sha256(WRONG.encode()).hexdigest())
    rec = source_record(found, resolve=held.get)
    assert rec.kind == "formal_accepted" and rec.result == "fail"
    issued = m.issue_records(facts(), Artifact(rec.artefact_sha256, "model:anthropic", 1), [rec])
    assert issued.certificate.verified_predicates == () and issued.certificate.predicates[0].support is False


def test_no_negative_control_no_promotion():
    held, _ = store()
    found = evidence(controls=())
    assert (m.NO_CONTROL, f"{P}: negative control unavailable; enforcement not established") in problems(
        found, resolve=held.get)
    rec = source_record(found, resolve=held.get)
    assert rec.kind == "formal_model_result" and "negative control unavailable; enforcement not established" in rec.scope
    only_raise = from_expectation(confirmed([3], {"A": {"returns": 4}, "B": {"raises": "ValueError"}}))
    sha = receipts.check_sha256(STATEMENT, ASSESSED.scope)
    assert seeded_control(STATEMENT, only_raise, source=RIGHT, fn_name="f", run=running(RIGHT), check_sha=sha,
                          predicate=P, seq=2) is None
    assert seeded_control(STATEMENT, CASES[0], source="def f(x):\n    return abs(x)\n", fn_name="f", run=None,
                          check_sha=sha, predicate=P, seq=2) is None


def test_control_must_be_rejected_by_this_check():
    held, _ = store()
    sha = receipts.check_sha256(STATEMENT, ASSESSED.scope)
    real = seeded_control(STATEMENT, CASES[0], source=RIGHT, fn_name="f", run=running(RIGHT), check_sha=sha,
                          predicate=P, seq=2)
    assert real.killed is True and real.check_sha == sha and real.generated_by == "g6"
    for other in (dataclasses.replace(real, check_sha="c" * 64), dataclasses.replace(real, predicate="another"),
                  dataclasses.replace(real, killed=False)):
        assert m.NO_CONTROL in [code for code, _ in problems(evidence(controls=(other,)), resolve=held.get)]
    # a statement too loose to exclude the rejected outcome does not reject the seeded function
    loose = relation("(>= result x)")
    assert seeded_control(loose, CASES[0], source=RIGHT, fn_name="f", run=running(RIGHT), check_sha=sha,
                          predicate=P, seq=2).killed is False


def test_person_confirmation_does_not_change_author():
    held, _ = store()
    rec = source_record(evidence(author="model:X"), resolve=held.get)
    assert rec.kind == "formal_accepted" and rec.author == "model:X"
    for author in ("person:owner", "", "model:", "owner"):
        found = evidence(author=author)
        assert m.NO_INDEPENDENCE in [code for code, _ in problems(found, resolve=held.get)]
        assert source_record(found, resolve=held.get).kind == "formal_model_result"


def test_statement_drafted_by_the_producer_family_is_a_partial_falsifier():
    held, _ = store()
    rec = source_record(evidence(author="model:anthropic"), resolve=held.get)
    check, _ = m.promote(rec, facts=facts(), artifact=ARTEFACT)
    assert check.kind == "partial_falsifier"


def test_stale_standard_refused():
    held, _ = store()
    newer = STANDARD.superseding(confirmed([3], {"A": {"returns": 4}, "B": {"returns": 5}}, chosen="B"))
    rec = source_record(evidence(), resolve=held.get)
    with pytest.raises(m.PromotionRefused) as refused:
        m.promote(rec, facts=facts(newer), artifact=ARTEFACT)
    assert m.OTHER_STANDARD in [code for code, _ in refused.value.codes]
    relabelled = evidence(standard_sha256=newer.sha256)   # evidence relabelled to the new standard has no acceptance
    assert any(why.startswith("standard:") for _, why in problems(relabelled, resolve=held.get))


def test_caller_chosen_ledger_positions_are_refused_by_the_mapping():
    held, _ = store()
    rec = source_record(evidence(artefact_seq=0), resolve=held.get)
    with pytest.raises(m.PromotionRefused) as refused:
        m.promote(rec, facts=facts(), artifact=ARTEFACT)
    assert m.OTHER_ORDER in [code for code, _ in refused.value.codes]
    retargeted = source_record(evidence(), resolve=held.get)
    with pytest.raises(m.PromotionRefused) as refused:
        m.promote(retargeted, facts=facts(), artifact=Artifact("b" * 64, "model:anthropic", 1))
    assert m.OTHER_ARTEFACT in [code for code, _ in refused.value.codes]


def test_confirmed_examples_survive_unconfirmed_rule():
    """With the formalisation unconfirmed, the standard's example.v1 checks are what they were."""
    unconfirmed = assess(relation("(>= result x)"), STANDARD, CASES)
    assert unconfirmed.status == "unconfirmed"
    check = FIRST.check(frozen_seq=0)
    assert check.kind == "independent_executable" and check.author == "person:owner"
    assert FIRST.provenance == "person_confirmed" and FIRST.instance.template == "example.v1"
    assert STANDARD.predicates == (FIRST.expectation_id,) and STANDARD.sha256 == ExpectationStandard((FIRST,)).sha256


def test_standard_record_is_what_the_standard_digest_is_taken_over():
    assert hashlib.sha256(receipts.standard_record(STANDARD)).hexdigest() == STANDARD.sha256


def test_kinds_are_the_two_rows():
    assert set(receipts.KINDS) == {kind for kind, row in m.MAPPING.items() if row.source == "formalisation"}