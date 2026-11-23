# SPDX-License-Identifier: Apache-2.0
"""Formal authority requires G6's checker result for the exact request."""
from dataclasses import replace

import pytest

from g6.evidence import mapping as m
from g6.evidence import Control
from tests.evidence.test_execution_custody import FACTS, SUBJECT, held, issue, record


def test_a_digest_alone_supplies_no_formal_authority():
    rec = record(kind="formal_checked", certificate="d" * 64, model_sha256="e" * 64)
    got = issue(rec)
    assert not got.promoted
    assert got.certificate.formal == ()
    assert got.certificate.state != "verified"
    with pytest.raises(m.PromotionRefused):
        m.promote(rec, facts=FACTS, artifact=SUBJECT)


def checked():
    from g6.evidence.checker_runs import check_propositional

    return check_propositional("p | ~p", definitions=(), axioms=(), subject=SUBJECT.sha256,
                               standard=FACTS.standard_sha, predicates=("p",), check_id="check")


def formal():
    request, receipt = checked()
    rec = replace(held(check_sha256=request.check_sha256, environment_sha256=request.environment_sha256,
                        controls=(Control("control", request.check_sha256, "p", "g6", 2, True),)),
                   kind="formal_checked", formal_request=request, certificate=receipt,
                   model_sha256=request.sha256)
    assert m.problems(rec, facts=FACTS, artifact=SUBJECT) == []
    return rec


def test_a_matching_receipt_from_g6s_checker_run_retains_formal_authority():
    got = issue(formal())
    assert got.certificate.formal
    assert got.certificate.state == "verified"


@pytest.mark.parametrize("field,value", [
    ("statement", "p"), ("subject", "b" * 64), ("checker", "other-checker"),
    ("version", "other-version"), ("definitions", ("q",)), ("axioms", ("q",)),
    ("encoding", "other-encoding"), ("environment_sha256", "f" * 64),
    ("check_id", "other-check"), ("check_sha256", "f" * 64), ("standard", "f" * 64),
    ("predicates", ("other",)),
])
def test_a_receipt_for_another_request_cannot_be_borrowed(field, value):
    rec = formal()
    request = replace(rec.formal_request, **{field: value})
    got = issue(replace(rec, formal_request=request, model_sha256=request.sha256))
    assert not got.promoted and not got.certificate.formal
    assert got.certificate.state != "verified"


def test_missing_formal_custody_does_not_lend_authority_to_an_executable_result():
    executable = held()
    formal_rec = replace(executable, kind="formal_checked", certificate="d" * 64, model_sha256="e" * 64)
    assert not issue(formal_rec).promoted
    got = issue(executable)
    assert got.promoted[0][2] == "independent_executable"
    assert got.certificate.formal == ()


def test_a_checker_failure_cannot_be_relabelled_as_a_formal_pass():
    from g6.evidence.checker_runs import check_propositional

    request, receipt = check_propositional("p & ~p", definitions=(), axioms=(), subject=SUBJECT.sha256,
        standard=FACTS.standard_sha, predicates=("p",), check_id="check")
    rec = replace(held(check_sha256=request.check_sha256, environment_sha256=request.environment_sha256,
                       controls=(Control("control", request.check_sha256, "p", "g6", 2, True),)),
                  kind="formal_checked", formal_request=request, certificate=receipt, model_sha256=request.sha256)
    assert m.problems(rec, facts=FACTS, artifact=SUBJECT) == []
    got = issue(rec)
    assert not got.promoted and not got.certificate.formal
    assert m.NO_CHECKER_RUN in {code for _, codes in got.refused for code, _ in codes}
