# SPDX-License-Identifier: Apache-2.0
"""Host-store custody at ingestion; fixture writes stand for completed host runs."""
from dataclasses import replace
import uuid

import pytest

from g6.evidence import Artifact, Control, MissionFacts
from g6.evidence import mapping as m

A, OTHER, CODE, ENV, STANDARD = (c * 64 for c in "abce5")
FACTS = MissionFacts("1" * 64, STANDARD, STANDARD, ("p",), ("p",))
SUBJECT = Artifact(A, "author", 1)


def record(**over):
    return m.SourceRecord(**{
        "kind": "independent_executable", "record_id": "run", "predicates": ("p",), "result": "pass",
        "artefact_sha256": A, "producer": "author", "artefact_seq": 1, "standard_sha256": STANDARD,
        "check_id": "check", "check_sha256": CODE, "author": "reviewer", "frozen_seq": 0,
        "environment_sha256": ENV, "seq": 4, "scope": "one case",
        "controls": (Control("control", CODE, "p", "g6", 2, True),), **over})


def observed(check="check", sha=CODE, artifact=A, result="pass", environment=ENV):
    from g6.evidence.custody import RunReference
    from g6.workforce import receipt_store

    ident = receipt_store._record({
        "schema": "g6.observed_run/1", "execution_id": uuid.uuid4().hex,
        "source_before": artifact, "source_after": artifact,
        "environment_sha256": environment, "checks": {check: {"sha256": sha, "result": result}}})
    return RunReference(ident, artifact)


def held(**over):
    rec = record(**over)
    common = {"check": rec.check_id, "sha": rec.check_sha256, "environment": rec.environment_sha256}
    control_run = observed(artifact=OTHER, result="fail", **common)
    positive = observed(artifact=OTHER, **common) if rec.positive_seq else None
    execution = observed(result=rec.result, **common)
    return replace(rec, execution=execution, control_runs=(("control", control_run),), positive=positive)


def issue(*records):
    return m.issue_records(FACTS, SUBJECT, records)


def test_a_positive_declaration_without_an_observed_execution_cannot_support():
    got = issue(record())
    assert not got.certificate.predicates[0].support
    assert got.refused


def test_an_unobserved_failure_is_a_caveat_beside_observed_support():
    support = held()
    failure = record(record_id="failure", check_id="failing", result="fail", positive_seq=2)
    got = issue(support, failure)
    statement = got.certificate.predicates[0]
    assert statement.support and not statement.refute and statement.caveats
    assert got.certificate.state != "verified"


def test_a_real_observed_pass_and_fail_pair_remains_conflicted():
    got = issue(held(), held(record_id="failure", check_id="failing", result="fail", positive_seq=2,
                             controls=(Control("control", CODE, "p", "g6", 2, True),)))
    statement = got.certificate.predicates[0]
    assert statement.support and statement.refute
    assert got.certificate.state == "blocked-escalated"


def test_an_authors_observed_reproduction_can_refute():
    got = issue(held(result="fail", author="author", positive_seq=2))
    assert got.certificate.predicates[0].refute
    assert got.promoted[0][2] == "partial_falsifier"


@pytest.mark.parametrize("part", ["execution", "control", "positive"])
@pytest.mark.parametrize("wrong", ["missing", "artifact", "check", "code", "result", "environment"])
def test_each_polarity_and_reference_resolves_its_exact_run(part, wrong):
    rec = held(result="fail", positive_seq=2)
    expected = "fail" if part in ("execution", "control") else "pass"
    ref = observed(check="foreign" if wrong == "check" else "check",
                   sha="f" * 64 if wrong == "code" else CODE,
                   artifact=A if part == "execution" else OTHER,
                   result=("pass" if expected == "fail" else "fail") if wrong == "result" else expected,
                   environment="f" * 64 if wrong == "environment" else ENV)
    if wrong in ("missing", "artifact"):
        ref = replace(ref, receipt_id="0" * 64) if wrong == "missing" else replace(ref, artifact_sha256="f" * 64)
    if part == "execution":
        rec = replace(rec, execution=ref)
    elif part == "control":
        rec = replace(rec, control_runs=(("control", ref),))
    else:
        rec = replace(rec, positive=ref)
    got = issue(held(record_id="support", check_id="support"), rec)
    statement = got.certificate.predicates[0]
    assert statement.support and not statement.refute and statement.caveats
    assert got.certificate.state != "verified"


def test_a_caller_cannot_forge_only_the_positive_reference():
    rec = held(result="fail", positive_seq=2)
    got = issue(replace(rec, positive=None))
    assert not got.certificate.predicates[0].refute
    assert got.certificate.predicates[0].caveats


def test_an_unobserved_control_cannot_ground_a_genuine_execution():
    got = issue(replace(held(), control_runs=()))
    assert not got.certificate.predicates[0].support


def test_a_reference_observed_after_the_failure_cannot_ground_it():
    rec = held(result="fail", positive_seq=2)
    got = issue(replace(rec, positive=observed(artifact=OTHER)))
    assert not got.certificate.predicates[0].refute
    assert got.certificate.predicates[0].caveats


def test_an_unreadable_host_store_withholds_support(tmp_path, monkeypatch):
    from g6.workforce import receipt_store

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    rec = held()
    receipt_store.store_path().write_text("broken\n", encoding="utf-8")
    assert not issue(rec).certificate.predicates[0].support


def test_an_existing_test_receipt_without_per_check_observations_is_unknown():
    from g6.evidence.custody import RunReference
    from g6.workforce import receipt_store

    rec = held()
    ident = receipt_store._record({"schema": receipt_store.SCHEMA, "execution_id": uuid.uuid4().hex,
                                  "source_before": A, "source_after": A, "result": {"verdict": "passed"}})
    got = issue(replace(rec, execution=RunReference(ident, A)))
    assert not got.certificate.predicates[0].support


@pytest.mark.parametrize("field,value", [
    ("execution", {"receipt_id": "f" * 64}), ("control_runs", []),
    ("positive", {"receipt_id": "f" * 64}), ("formal_request", {"statement": "p"}),
])
def test_untyped_custody_metadata_never_becomes_authority(field, value):
    rec = replace(held(result="fail", positive_seq=2), **{field: value})
    got = issue(rec)
    assert not got.promoted and not got.certificate.predicates[0].refute
    assert got.certificate.predicates[0].caveats


def test_an_untyped_control_outcome_is_not_a_killed_control():
    rec = held(result="fail", positive_seq=2,
               controls=(Control("control", CODE, "p", "g6", 2, "False"),))
    got = issue(rec)
    assert not got.promoted and not got.certificate.predicates[0].refute
    assert got.certificate.predicates[0].caveats
