"""Review 3: what the evaluation and the exit code say when there is no evidence.

Saved records and temporary folders only: no container, no model, no real archive, no confined run.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "validation"))
from worked_examples import replay_rc as rc  # noqa: E402

COMMIT = "a" * 40


def hook_record(label, outcome):
    case = {"bug": "black-5634", "digest": label, "label": label, "arguments": None}
    record = {**case, "mode": "hook", "status": "finished", "seconds": 1,
              "review": {"ok": True, "assessment": {"outcome": outcome, "completion": {}}}}
    return case, record


@pytest.mark.parametrize("number", ["P4", "P6", "P7"])
def test_prediction_with_no_record_is_not_reported_as_missed(number):
    # A default live run is modes agent and hook: mode 3 never runs and has no record at all.
    registration = rc._parse_registration(rc.REGISTRATION.read_bytes())
    cases, records = zip(hook_record("right", "insufficient_evidence"), hook_record("wrong", "insufficient_evidence"))
    prediction = rc.evaluate(list(records), list(cases), modes=["hook"], registration=registration)["predictions"][number]
    assert prediction["status"] == "disabled"
    assert prediction["prediction_met"] is None, prediction


def test_absent_source_root_is_not_a_successful_plan(tmp_path, capsys):
    code = rc.main(["--source", str(tmp_path / "no-such-archive"), "--registered-commit", COMMIT, "--dry"])
    printed = capsys.readouterr()
    assert code == 1, f"exit 0 with plan: {printed.out[:200]!r}"
