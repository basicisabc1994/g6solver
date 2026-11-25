# SPDX-License-Identifier: Apache-2.0
"""P1-P7 distinguish absent evidence, reversible estimates and observed misses."""
from __future__ import annotations

import pytest

from worked_examples import replay_rc as rc


def samples():
    registration = rc._parse_registration(rc.REGISTRATION.read_bytes())
    bugs = (*registration.neighbours, registration.no_lead_bug, registration.question_bug)
    cases, records = [], []
    for bug in bugs:
        for label in ("right", "wrong"):
            case = {"bug": bug, "digest": label, "label": label, "arguments": {},
                    "origins": [{"run": f"run/{bug}/{label}", "arguments": {}}]}
            cases.append(case)
            for mode in rc.MODES:
                completion = {"neighbourhood": {
                    "cases": [{"class": "symptom_persists"}]
                             if bug in registration.neighbours and label == "wrong" else [],
                    "question_candidates": ["Q"] if bug == registration.question_bug else []}}
                records.append({**case, "mode": mode, "origin_run": case["origins"][0]["run"],
                                "status": "finished", "seconds": 1,
                                "review": {"ok": True, "assessment": {
                                    "outcome": "insufficient_evidence", "completion": completion}}})
    return registration, cases, records


@pytest.mark.parametrize("number", range(1, 8))
@pytest.mark.parametrize("stage", ["zero", "partial", "full", "disabled", "failed"])
def test_all_predictions_require_their_evidence(number, stage):
    registration, cases, records = samples()
    if stage in {"zero", "disabled"}:
        records = []
    elif stage == "partial":
        records = records[:3]  # just one right patch, no wrong-patch evidence
    elif stage == "failed":
        records = [{**r, "status": "failed", "review": None} for r in records]
    modes = [] if stage == "disabled" else list(rc.MODES)
    prediction = rc.evaluate(records, cases, modes=modes, s0="after",
                             registration=registration)["predictions"][f"P{number}"]
    expected = True if stage == "full" and number != 1 else None
    assert prediction["prediction_met"] is expected
    assert prediction["status"] == ("disabled" if stage == "disabled" else
                                    "met" if expected else "incomplete")


@pytest.mark.parametrize("number", [1, 2, 3, 5, 6])
def test_irreversible_observed_miss_remains_visible_with_missing_records(number):
    registration, cases, records = samples()
    if number in {1, 3}:
        record = next(r for r in records if r["mode"] == "hook" and r["label"] == "right")
        record["review"]["assessment"]["outcome"] = (
            "test_integrity_compromised" if number == 1 else "defect_detected")
        selected = [record]
    elif number == 2:
        record = next(r for r in records if r["mode"] == "agent" and r["label"] == "wrong")
        record["review"]["assessment"]["outcome"] = "supported_within_scope"
        selected = [record]
    elif number == 5:
        selected = [r for r in records if r["mode"] == "neighbourhood" and r["label"] == "right"]
        for record in selected:
            record["review"]["assessment"]["completion"]["neighbourhood"]["cases"] = [{"class": "symptom_persists"}]
    else:
        selected = [next(r for r in records if r["mode"] == "neighbourhood"
                         and r["bug"] == registration.no_lead_bug)]
        selected[0]["review"]["assessment"]["completion"]["neighbourhood"]["cases"] = [{"class": "symptom_persists"}]
    prediction = rc.evaluate(selected, cases, modes=list(rc.MODES), s0="after",
                             registration=registration)["predictions"][f"P{number}"]
    assert prediction["prediction_met"] is False


def test_partial_time_estimate_and_missing_question_are_unknown():
    registration, cases, records = samples()
    selected = [next(r for r in records if r["mode"] == "neighbourhood"
                     and r["bug"] == registration.question_bug)]
    selected[0]["seconds"] = 1000
    selected[0]["review"]["assessment"]["completion"] = {}
    predictions = rc.evaluate(selected, cases, modes=list(rc.MODES),
                              registration=registration)["predictions"]
    assert predictions["P6"]["prediction_met"] is None
    assert predictions["P7"]["prediction_met"] is None
    assert predictions["P7"]["kill"] is False
