# SPDX-License-Identifier: Apache-2.0
"""Verification search's first rule: with search off and no intent, every decided field of every reply is what the patch
review gave before verification search existed.

The cases are the six development cases of the engineering trial (``validation/patch_review/run.py``), called exactly
as the trial calls them; none is evaluation data. The golden file was written from the unchanged code (commit
``cd52d48``) in confined runs (Linux bubblewrap). It is never rewritten to make this test pass: a difference is a change
of behaviour, to be explained, not absorbed."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

GOLDEN = Path(__file__).with_name("data") / "patch_review_off_path.json"
TRIAL = Path(__file__).resolve().parents[2] / "validation" / "patch_review" / "run.py"


def _trial():
    spec = importlib.util.spec_from_file_location("patch_review_trial_off_path", TRIAL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def decided(reply: dict) -> dict:
    """What a reply decides: the outcome, each requirement's id and status, each reason's stage and code, merge
    authorisation, the reliability label and its code, and how many unknowns it lists. Wording, timings, digests and
    receipts are not decided fields."""
    assessment = reply.get("assessment") or {}
    return {"ok": reply.get("ok"), "completion_state": reply.get("completion_state"),
            "warning_code": (reply.get("warning_card") or {}).get("code"),
            "outcome": assessment.get("outcome"),
            "requirements": [[found["id"], found["status"]] for found in assessment.get("requirements", [])],
            "reasons": [[found["stage"], found["code"]] for found in assessment.get("reasons", [])],
            "merge": (reply.get("merge") or {}).get("status"),
            "unknowns": len(assessment.get("unknowns", []))}


def replies(**extra) -> dict[str, dict]:
    """Each case's decided fields, from the trial's own calls (its ``run``), with ``extra`` added to every call."""
    trial = _trial()
    real, got = trial.review_patch, []

    def recorded(*args, **kwargs):
        reply = real(*args, **kwargs, **extra)
        got.append(reply)
        return reply

    trial.review_patch = recorded
    rows = trial.run()["cases"]
    return {row["case"]: decided(reply) for row, reply in zip(rows, got, strict=True)}


def _golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("extra", [{}, {"intent": None}, {"proposed_checks": None}, {"proposed_checks": []},
                                   {"template_instances": None}, {"template_instances": []}, {"search": None}],
                         ids=["as-the-trial-calls", "intent-none", "proposed-checks-none", "proposed-checks-empty",
                              "template-instances-none", "template-instances-empty", "search-none"])
def test_with_search_off_and_no_intent_every_decided_field_is_unchanged(bwrap_backend, extra):
    assert replies(**extra) == _golden()


def test_the_golden_file_records_the_trials_expected_outcomes():
    """A survival control: the golden file is not the record of a broken run (every case refused, say), so agreeing
    with it means something."""
    golden = _golden()
    assert {name: found["outcome"] for name, found in golden.items()} == {
        case[0]: case[-1] for case in _trial().CASES}
    assert golden["correct"]["requirements"] == [["R1", "passed_on_stated_checks"], ["R2", "passed_on_stated_checks"]]
    assert golden["stale-artifact"]["completion_state"] == "blocked-escalated"
    assert all(found["completion_state"] == "qualified-draft" for name, found in golden.items()
               if name != "stale-artifact")
