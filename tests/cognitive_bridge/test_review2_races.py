# SPDX-License-Identifier: Apache-2.0
"""Review 2, findings 6, 10 and 11 (check_then_act_race): freeze, then check, then act on the frozen copy. The executor
and the orchestrator copy the plan whole before checking it and run that copy; an artifact's content is immutable
bytes from the moment it is made; and the review scope and the criterion checks read one frozen set of artifacts."""
from __future__ import annotations

from dataclasses import replace

import pytest
from returns.result import Success

from g6.cognitive_bridge import (
    ClaimIR, EvidenceArtifact, InProcessAssuranceEvaluator, MethodRouter, MixedMethodExecutor, ProfessionalAttestation,
    mixed_methods, orchestration,
)


def test_finding_6_pending_nested_parameters_cannot_change_during_freezing(monkeypatch):
    plan = MethodRouter().plan([
        ClaimIR("one", "first", ("formal",), {"formal": {"formula": "A", "premises": ["A"]}}),
        ClaimIR("two", "second", ("formal",), {"formal": {"formula": "B", "premises": ["B"]}}),
    ])
    real = mixed_methods.canonical_json
    switched = False

    def interleave(value):
        nonlocal switched
        data = real(value)
        if not switched:
            switched = True
            # Precisely after canonical equality was checked, before later steps are frozen.
            plan.claims[1].models["formal"]["premises"][:] = ["False"]
        return data

    monkeypatch.setattr(mixed_methods, "canonical_json", interleave)
    calls = []

    class Registry:
        def invoke(self, name, op, params=None, **kwargs):
            calls.append(params)
            return Success({"completion_state": "qualified-draft"})

    result = MixedMethodExecutor(Registry()).execute(plan, approved=True)
    assert not any(x["premises"] == ["False"] for x in calls), (
        f"post-check edit executed: {calls}; state={result['completion_state']}")


def test_the_orchestrator_runs_the_copy_it_checked_against_the_pins(executed_case, monkeypatch):
    c = executed_case
    model = c["compiled"].executable_plan.claims[0].models["formal"]
    real = orchestration.canonical_json
    calls, refused = [], []

    def interleave(value):
        data = real(value)
        calls.append(value)
        if len(calls) == 2:  # the pin check rendered the model and then the steps; the caller edits right after
            try:
                model["premises"][:] = ["False"]
            except TypeError:  # review 3: the compiled model is a snapshot, so the edit itself is refused
                refused.append(True)
        return data

    monkeypatch.setattr(orchestration, "canonical_json", interleave)
    run = c["orchestrator"].execute(c["compiled"], approved=True)
    assert refused or model["premises"] == ["False"]
    sent = [params for _, _, params in c["registry"].calls]
    assert sent and all(params["premises"] == ["A"] for params in sent), f"an edit after the pin check ran: {sent}"
    assert run["completion_state"] == "qualified-draft"


def test_finding_10_review_cannot_authenticate_different_bytes_than_the_checks(executed_case):
    c = executed_case
    standard = replace(c["standard"], risk=3)
    artifacts = dict(c["artifacts"])
    bad = replace(artifacts["c"], content=b"BAD", source_ref="store:unchecked")
    checked = []

    def checker(content):
        checked.append(content)
        if len(checked) == 3:  # after positive and negative controls, during the artifact check
            artifacts["c"] = bad
        return content == b"GOOD"

    verifiers = {"checker": replace(c["verifiers"]["checker"], check=checker)}
    evaluator = InProcessAssuranceEvaluator(evaluator_id="outside", reviewer_authenticator=lambda a: a == signed)
    common = dict(standard=standard, verifiers=verifiers, documentation=c["documentation"],
                  candidate_actor=c["candidate_actor"])
    # The professional authentically signed BAD, which must fail the pinned checker.
    scope = evaluator.review_scope(artifacts={"c": bad}, **common)
    signed = ProfessionalAttestation("professional", "reviewer", scope, "authentic-signature")
    dossier = evaluator.evaluate(artifacts=artifacts, attestation=signed, **common)
    assert checked == [b"GOOD", b"BAD", b"GOOD"]
    assert dossier.payload["completion_state"] == "blocked-escalated", (
        "authenticated review for BAD combined with passing checks of GOOD")


def test_finding_11_mutable_artifact_cannot_change_after_its_manifest_is_recorded(executed_case):
    c = executed_case
    content = bytearray(b"BAD")
    try:
        artifact = EvidenceArtifact("e", content, "report", "candidate", "store:buffer", "claim")
    except (TypeError, ValueError):
        return  # rejecting mutable evidence is safe
    checked = []

    def checker(value):
        checked.append(bytes(value))
        if len(checked) == 1:  # the positive control: after the evaluator has recorded BAD's manifest
            content[:] = b"GOOD"
        return value == b"GOOD"

    verifiers = {"checker": replace(c["verifiers"]["checker"], check=checker)}
    dossier = c["evaluator"].evaluate(
        standard=c["standard"], artifacts={"c": artifact}, verifiers=verifiers,
        documentation=c["documentation"], candidate_actor=c["candidate_actor"])
    assert dossier.payload["completion_state"] == "blocked-escalated", (
        f"mutable bytes changed after manifest: recorded length={dossier.payload['checks'][0]['artifact']['length']}, "
        f"checked={checked[-1]!r}")


def test_the_adaptation_gate_counts_the_pairs_it_admitted(passing_adaptation):
    # Found walking the class: the gate admitted each pair, then counted wins from the caller's list again.
    gate, change, pairs, controls = passing_adaptation
    losing = [replace(p, baseline_passed=True, adapted_passed=False) for p in pairs]
    shown = list(losing)

    def admit_control(control):
        shown[:] = list(pairs)  # every pair was admitted; now the caller's list holds pairs nobody admitted
        return control in controls

    gate.admit_pair, gate.admit_control = (lambda x: x in losing), admit_control
    out = gate.assess(change, shown, controls, detected_failures=change.mitigates)
    assert out["completion_state"] == "blocked-escalated" and out["wins"] == 0, (
        f"counted pairs that were never admitted: wins={out['wins']}")


@pytest.mark.parametrize("content", [bytearray(b"GOOD"), memoryview(b"GOOD"), b"GOOD"])
def test_an_artifacts_content_is_immutable_bytes_from_the_moment_it_is_made(content):
    artifact = EvidenceArtifact("e", content, "report", "candidate", "store:buffer", "claim")
    assert type(artifact.content) is bytes and artifact.content == b"GOOD"
    if isinstance(content, bytearray):
        content[:] = b"EVIL"
        assert artifact.content == b"GOOD" and artifact.manifest()["length"] == 4


@pytest.mark.parametrize("content", ["GOOD", 1234, None, ["G"]])
def test_content_that_is_not_bytes_is_refused_when_the_artifact_is_made(content):
    with pytest.raises((TypeError, ValueError)):
        EvidenceArtifact("e", content, "report", "candidate", "store:buffer", "claim")
