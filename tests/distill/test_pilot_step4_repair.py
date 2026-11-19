"""Admission units, retry costs and declared probe limits remain independent."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.distill import acceptance_gate as production
from tests.distill import test_pilot_gate as pilot_tests
from tests.distill.test_pilot_gate import decide

pilot = pilot_tests.pilot
rig = pilot_tests.rig


@pytest.mark.parametrize("regression,code", [
    ("positive_abstain", "PILOT_UNIT_REGRESSION"), ("negative", "PILOT_ADMISSION_FP"),
    ("negative_abstain", "PILOT_UNIT_REGRESSION"),
])
def test_compensating_gain_cannot_hide_unit_regression(pilot, rig, monkeypatch, regression, code):
    frozen = rig["frozen"]
    rows = list(frozen.replacement.rows)
    rows[1] = rows[1].model_copy(update={"teacher_verdict": "abstain"})
    rig["frozen"] = replace(frozen, replacement=frozen.replacement.model_copy(update={"rows": rows}))
    original = production._measure

    def measure(candidate, group, entries):
        values, costs = original(candidate, group, entries)
        if group == frozen.acceptance:
            index = 5 if regression.startswith("negative") else 0
            values[index] = "concern" if regression == "negative" else "abstain"
        return values, costs

    monkeypatch.setattr(production, "_measure", measure)
    result = decide(pilot, rig)
    assert result.measurements["accuracy_delta"] == 0
    assert result.pilot_verdict == "refuse"
    assert result.code == code


def test_new_alarm_refused_even_on_already_incorrect_unit(pilot, rig, monkeypatch):
    frozen = rig["frozen"]
    rows = list(frozen.replacement.rows)
    rows[5] = rows[5].model_copy(update={"teacher_verdict": "abstain"})
    rig["frozen"] = replace(frozen, replacement=frozen.replacement.model_copy(update={"rows": rows}))
    original = production._measure

    def measure(candidate, group, entries):
        values, costs = original(candidate, group, entries)
        if group == frozen.acceptance:
            values[5] = "concern"
        return values, costs

    monkeypatch.setattr(production, "_measure", measure)
    assert decide(pilot, rig).code == "PILOT_ADMISSION_FP"


def test_nonheadline_unit_still_cannot_regress(pilot, rig, monkeypatch):
    frozen = rig["frozen"]
    first = replace(frozen.acceptance[0], oracle_result=frozen.acceptance[0].oracle_result | {"wrong_witnesses": []})
    rows = list(frozen.replacement.rows)
    rows[1] = rows[1].model_copy(update={"teacher_verdict": "abstain"})
    rig["frozen"] = replace(frozen, acceptance=(first, *frozen.acceptance[1:]),
        replacement=frozen.replacement.model_copy(update={"rows": rows}))
    original = production._measure

    def measure(candidate, group, entries):
        values, costs = original(candidate, group, entries)
        if group == rig["frozen"].acceptance:
            values[0] = "abstain"
        return values, costs

    monkeypatch.setattr(production, "_measure", measure)
    assert decide(pilot, rig).code == "PILOT_UNIT_REGRESSION"


def clocked(pilot, monkeypatch):
    now = [0.]
    monkeypatch.setattr(pilot, "time", SimpleNamespace(monotonic=lambda: now[0], time=lambda: now[0]))
    monkeypatch.setattr(pilot, "_production_decision", lambda c, f:
        production.AcceptanceDecision("inconclusive", ("synthetic",), c.candidate_id))
    return now


@pytest.mark.parametrize("interrupted", [False, True])
def test_retry_charges_refused_or_interrupted_validation(pilot, rig, monkeypatch, interrupted):
    now = clocked(pilot, monkeypatch)
    original = production._measure
    charged = [False]

    def measure(*args):
        if not charged[0]:
            charged[0] = True
            now[0] += 10000
            if interrupted:
                raise KeyboardInterrupt
        return original(*args)

    monkeypatch.setattr(production, "_measure", measure)
    if interrupted:
        with pytest.raises(KeyboardInterrupt):
            decide(pilot, rig)
    else:
        first = decide(pilot, rig)
        assert first.code == "PILOT_COST"
        assert first.measurements["cost"]["point"] > 0
    result = decide(pilot, rig)
    assert result.code == "PILOT_COST"
    assert result.measurements["cost"]["fixed_difference"] >= 10011
    history = rig["root"] / "pilot-validation" / f'{rig["candidate"].candidate_id}.jsonl'
    rows = [json.loads(line) for line in history.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["validation_total_s"] >= 10000


@pytest.mark.parametrize("when", ["start", "finish"])
def test_cost_write_failure_refuses_before_publication(pilot, rig, monkeypatch, when):
    original = Path.open
    writes = [0]

    def open_path(path, mode="r", *args, **kwargs):
        if "pilot-validation" in path.parts and path.suffix == ".jsonl" and "a" in mode:
            writes[0] += 1
            if when == "start" or writes[0] == 3:
                raise OSError("synthetic cost append failure")
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_path)
    result = decide(pilot, rig)
    assert result.code == "PILOT_COST_RECORD"
    assert not (rig["root"] / "pilot-gate" / f'{rig["candidate"].candidate_id}.json').exists()


