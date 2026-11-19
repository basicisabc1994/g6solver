# SPDX-License-Identifier: Apache-2.0
"""Compatibility and receipt-bound target contracts for distillation cases."""
import pytest
from pydantic import ValidationError

from g6.core.ledger import digest
from g6.distill import records as r
from g6.distill.held_out.oracle import OracleResult
from g6.distill.synthesis import SynthesisCase

from .test_records import D, bodies


OLD_IDS = [
    "db4a06e49f96c3cb4dbf2801fff663ee99ec6130a0d6dbbc566b665f60aea378",
    "4ac055a9cde178d9dc2822116f95c57f0ca6628dc0ff8fdc5da2ba341e5aa49b",
    "926e2ccba290d5fd6f95804be5ab88700f600c49a72db31c786b6e1dabbed949",
    "039de3587a3084dc8db11bb64ad750fa3ff6aa67c9fcc05627a4c235956273b0",
    "cbcd79f9cef8b249b5f317319f4f47eef2dd58657e34ed1828db714a3ed81888",
    "ad8f4ba99035fab80407545af31980f292c502838c4dbde1a95b73800e71adc5",
    "408ae1f41f84c8899be7266cded2a43508a10c5c17cd3a72e01f1a0fd53a90cd",
    "02fb985746712a7190a24a8594e0116c55f720f3dc993090d9ee52438d2d596c",
]


@pytest.mark.parametrize("index", range(8))
def test_old_record_ids_survive_round_trip(index):
    cls, body = bodies()[index]
    value = cls(**body)
    assert value.content_id == OLD_IDS[index]
    assert cls.model_validate_json(value.model_dump_json()).content_id == OLD_IDS[index]


def probe_bodies(name):
    contract = bodies()[2][1] | {
        "probes": [dict(name=name, runs=1, wall_s=1.0)],
        "cost_bound": dict(wall_s=1.0, probe_runs=1, model_calls=0),
    }
    candidate = bodies()[3][1] | {
        "form": "template_instance", "body": dict(template=name, bindings={}), "contract": contract,
    }
    return contract, candidate


def test_old_v1_probe_ids_survive():
    contract, candidate = probe_bodies("kill_set.v1")
    assert r.MethodContract(**contract).content_id == "7eabed20d3bce0b795fd1ad3b924ad5fc3cf80215ce88608eedcd27b160e96ab"
    assert r.VerifierCandidate(**candidate).content_id == "d38e5c419ff9f919c5e21f5e4fee7c2586b70ae25a441071fd22acea5130618f"


@pytest.mark.parametrize("name", ["kill_set.v2", "discriminates_at_base.v2"])
def test_v2_probes_can_be_carried_by_candidates(name):
    contract, candidate = probe_bodies(name)
    value = r.VerifierCandidate(**candidate)
    assert value.contract.probes[0].name == name
    assert value.body.template == name
    assert name in r.PROBE_NAMES
    assert r.MethodContract(**contract).content_id == value.contract.content_id
    assert r.VerifierCandidate.model_validate_json(value.model_dump_json()) == value


def replacement_fields():
    return dict(replacement="R", replacement_scope=[dict(feature="assertions", op="gt", value=0)],
                teacher=D, noninferiority=digest("noninferiority receipt"))


def test_replacement_metadata_is_hashed_and_round_trips():
    cls, body = bodies()[4]
    legacy = cls(**body)
    assert legacy.replacement == "none"
    assert not (set(replacement_fields()) & legacy.model_dump().keys())
    entry = cls(**body, **replacement_fields())
    assert entry.content_id != OLD_IDS[4]
    assert entry.model_dump(mode="json")["replacement"] == "R"
    assert cls.model_validate_json(entry.model_dump_json()) == entry
    for name in ("replacement_scope", "teacher", "noninferiority"):
        fields = replacement_fields()
        fields.pop(name)
        with pytest.raises(ValidationError):
            cls(**body, **fields)


def test_replacement_cannot_claim_authority_at_a0():
    cls, body = bodies()[4]
    with pytest.raises(ValidationError):
        cls(**(body | dict(status="withdrawn", authority="A0")), **replacement_fields())


def bound_body(label="positive"):
    result = OracleResult(case_id=D, label=label, reason="fixture", policy_sha256=D)
    failure = r.FailureRecord(**(bodies()[0][1] | {
        "evidence": [dict(kind="oracle_label", digest=digest(result.model_dump(mode="json")), receipt="oracle/1")],
    }))
    return dict(failure=failure, oracle_result=result, registered_policy_sha256=D,
                before={"test_case.py": "assert 1"}, after={"test_case.py": "assert True"})


def assert_unbound(body):
    with pytest.raises(ValidationError) as caught:
        r.OracleBoundCase(**body)
    assert any(error["type"] == "G6_E_SYNTH_UNBOUND_LABEL" for error in caught.value.errors())


@pytest.mark.parametrize("label,teacher,expected", [
    ("positive", "no_concern", "concern"), ("negative", "concern", "no_concern"),
])
def test_teacher_wrong_case_retains_oracle_target(label, teacher, expected):
    bound = r.OracleBoundCase(**bound_body(label))
    assert bound.expected == expected and bound.expected != teacher
    training = SynthesisCase(failure=bound.failure, before=bound.before, after=bound.after, expected=bound.expected)
    assert training.expected == expected
    assert r.OracleBoundCase.model_validate_json(bound.model_dump_json()) == bound


@pytest.mark.parametrize("label,conflict", [("positive", "no_concern"), ("negative", "concern")])
def test_conflicting_caller_label_refused(label, conflict):
    assert_unbound(bound_body(label) | {"expected": conflict})


@pytest.mark.parametrize("missing", ["result", "evidence", "both"])
def test_missing_receipt_refused(missing):
    body = bound_body()
    if missing in {"result", "both"}:
        body.pop("oracle_result")
    if missing in {"evidence", "both"}:
        body["failure"] = r.FailureRecord(**bodies()[0][1])
    assert_unbound(body)


@pytest.mark.parametrize("change", ["case", "policy", "digest", "excluded", "duplicate", "empty_receipt"])
def test_receipt_binding_refuses_mismatch_and_ambiguity(change):
    body = bound_body("excluded" if change == "excluded" else "positive")
    if change in {"case", "policy"}:
        raw = body["oracle_result"].model_dump(mode="json")
        raw["case_id" if change == "case" else "policy_sha256"] = digest("other")
        body["oracle_result"] = OracleResult(**raw)
        failure = body["failure"].model_dump(mode="json", exclude={"record_id"})
        failure["evidence"][0]["digest"] = digest(raw)
        body["failure"] = r.FailureRecord(**failure)
    else:
        raw = body["failure"].model_dump(mode="json", exclude={"record_id"})
        if change == "digest":
            raw["evidence"][0]["digest"] = digest("other")
        elif change == "duplicate":
            raw["evidence"] *= 2
        elif change == "empty_receipt":
            raw["evidence"][0]["receipt"] = " "
        body["failure"] = r.FailureRecord(**raw)
    assert_unbound(body)
