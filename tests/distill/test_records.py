# SPDX-License-Identifier: Apache-2.0
"""Data contracts through the public construction and persistence interfaces."""
import pytest
from pydantic import ValidationError

from g6.core.ledger import digest
from g6.distill import records as r

D = digest("fixture")
PREDICTION = dict(prediction_id="p1", measure="defects", before=2.0, expected=1.0)
INTERVAL = dict(point=1.0, lo95=0.8, hi95=1.0, method="wilson")


def bodies():
    failure = dict(kind="missed_defect", family="test_weakening", source="review", observed_by="independent",
                   case_ref=D, split="training", evidence=[dict(kind="receipt", digest=D, receipt="local")],
                   finding_class="boundary_case", met_before=[], g6_commit="abc", observed_at="2026-11-18",
                   provenance={"source": "fixture"})
    f = r.FailureRecord(**failure)
    concept = dict(statement="Lost assertions weaken tests", family="test_weakening", from_failures=[f.record_id],
                   observable_features=["assertions"], falsifier="Equivalent assertions moved to a helper",
                   prediction=PREDICTION, proposer=dict(provider="fixture", model="none", prompt_sha256=D),
                   origin="model_proposed", worth=0.5, status="proposed")
    c = r.Concept(**concept)
    contract = dict(family="test_weakening", languages=["python"], frameworks=["pytest"], applies_when=[],
                    assumptions=["static only"], inputs=["assertions"], probes=[],
                    verdict_kinds=["concern", "abstain", "no_concern"],
                    cost_bound=dict(wall_s=1.0, probe_runs=0, model_calls=0), escape_family="helper changes",
                    withdrawal_conditions=["regression"])
    mc = r.MethodContract(**contract)
    candidate = dict(concept_id=c.concept_id, form="predicate", body="def solve(facts):\n return {'verdict': 'abstain'}",
                     contract=contract, lineage=[], synthesised_by=dict(kind="template", exchange_sha256=None),
                     training_case_ids=[f.case_ref])
    v = r.VerifierCandidate(**candidate)
    evidence = dict(acceptance_report_sha256=D, policy_sha256=D, split_sha256=D, reuse_index=0,
                    n_pos=30, n_neg=10, tp=30, fp=0, tn=10, fn=0, abstain=0, precision=INTERVAL,
                    recall=INTERVAL, false_rejection=dict(point=0.0, lo95=0.0, hi95=0.1, method="wilson"),
                    cost=dict(median_s=0.1, p95_s=0.2))
    entry = dict(candidate_id=v.candidate_id, contract_sha256=mc.content_id, concept_id=c.concept_id,
                 family="test_weakening", version=1, parents=[], status="admitted", authority="A2", evidence=evidence,
                 regression_suite=[dict(case_ref=D, verdict="concern")],
                 field=dict(fired=20, confirmed=20, refuted=0, unlabelled=0, precision=INTERVAL),
                 decided_by=dict(gate_version="fixture", person=None), certificate_sha256=D,
                 created_at="2026-11-18", changed_at="2026-11-18", reason="fixture only")
    extension = dict(kind="method", requested_by="person", gap_record=f.record_id, summary="fixture",
                     artefact_ref=D, evidence_ref=D, certificate_ref=D, status="open", scope="test_weakening")
    mode = dict(mode_code="boundary_case", first_seen="2026-11-18", occurrences=1, met_before=False,
                removing_check=None, recurrences_after_removal=0)
    rect = dict(failure_mode="boundary_case", instance_or_class="class",
                change=dict(kind="library", branch=None, commit=None, pr=None), prediction=PREDICTION,
                gate_decision_sha256=D, class_removed=False, outcome="open", rollback_ref=None)
    return [(r.FailureRecord, failure), (r.Concept, concept), (r.MethodContract, contract),
            (r.VerifierCandidate, candidate), (r.LibraryEntry, entry), (r.CapabilityExtension, extension),
            (r.FailureMode, mode), (r.Rectification, rect)]


@pytest.mark.parametrize("index", range(8))
def test_round_trip_digest_and_unknown_fields(index):
    cls, body = bodies()[index]
    value = cls(**body)
    assert value.content_id == digest(body)
    assert cls(**dict(reversed(list(body.items())))).content_id == value.content_id
    assert cls.model_validate_json(value.model_dump_json()) == value
    assert len(value.content_id) == 64
    with pytest.raises(ValidationError):
        cls(**body, surprise=True)


def test_split_and_nested_unknown_fields_and_forged_identity():
    cls, body = bodies()[0]
    with pytest.raises(ValidationError):
        cls(**(body | {"split": "held_out"}))
    with pytest.raises(ValidationError):
        cls(**(body | {"evidence": [dict(kind="x", digest=D, receipt="r", hidden="secret")]}))
    with pytest.raises(ValidationError):
        cls(**body, record_id=D)
    assert cls(**(body | {"family": "other"})).record_id != cls(**body).record_id


def test_contract_and_candidate_refuse_unsafe_shapes():
    contract = bodies()[2][1]
    with pytest.raises(ValidationError):
        r.MethodContract(**(contract | {"cost_bound": dict(wall_s=1, probe_runs=0, model_calls=1)}))
    with pytest.raises(ValidationError):
        r.MethodContract(**(contract | {"inputs": ["unknown"]}))
    candidate = bodies()[3][1]
    for change in ({"body": "x" * 8193}, {"form": "reuse"}, {"form": "unknown"}):
        with pytest.raises(ValidationError):
            r.VerifierCandidate(**(candidate | change))


def test_domain_and_prediction_adapters_preserve_independent_observation():
    from g6.metaharness.domain import FailureRecord as DomainFailure
    from g6.metaharness.lessons import Prediction as DomainPrediction
    failure = r.FailureRecord(**bodies()[0][1])
    adapted = failure.as_domain(DomainFailure)
    assert adapted.case_id == failure.case_ref
    assert adapted.cause == failure.kind
    assert adapted.independently_observed is True
    assert adapted.note == failure.family
    assert r.Prediction(**PREDICTION).as_domain(DomainPrediction) == DomainPrediction(**PREDICTION)


def test_mode_codes_and_authority_shape_are_validated():
    with pytest.raises(ValidationError):
        r.FailureMode(**(bodies()[6][1] | {"mode_code": "invented_code"}))
    with pytest.raises(ValidationError):
        r.LibraryEntry(**(bodies()[4][1] | {"status": "withdrawn"}))


def test_cascade_can_record_demoted_composition_at_a0():
    entry = r.LibraryEntry(**(bodies()[4][1] | {"status": "demoted", "authority": "A0"}))
    assert entry.status == "demoted" and entry.authority == "A0"
